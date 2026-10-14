"""Organization mode: screen many listings at once and roll the results up by landlord.

Built for a housing nonprofit, a university housing office or a tenant union. Every address is
screened with public data in code (no AI), so screening 25 addresses costs about the same as one
AI summary. A single Portfolio Analyst agent then writes the executive summary, and its numbers
are checked against the screening data the same way as every other report.
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel, Field
from pydantic_ai import Agent, ModelRetry, RunContext, UsageLimits

from . import sources
from .config import REASONING_EFFORT, cost_usd, model
from .runner import Job, normalize_address
from .safety import _numbers

MAX_ADDRESSES = 25
PARALLEL = 4

FLAG_TEXT = {
    "inactive_company": "Owner company is not active in the CT registry",
    "out_of_state_owner": "Owner's mailing address is outside Connecticut",
    "complaints_at_address": "311 reports at this address",
    "high_eviction_tract": "Area eviction filings in the top quarter of the county",
    "flood_zone": "FEMA high-risk flood zone",
    "no_record": "No assessor record found",
}


async def screen_address(raw: str) -> dict[str, Any]:
    """Public-data screening of one address. No AI calls."""
    out: dict[str, Any] = {"input": raw, "flags": [], "status": "done"}
    try:
        geo = await sources.geocode(normalize_address(raw))
        if geo is None or geo["state"] != "CT":
            return {**out, "status": "not_found", "error": "Not matched to a Connecticut address"}
        parcels, ev, flood, n311 = await asyncio.gather(
            sources.find_parcels_for(geo),
            sources.tract_evictions(geo["tract_geoid"]) if geo.get("tract_geoid") else asyncio.sleep(0, {}),
            sources.flood_zone(geo["lat"], geo["lng"]),
            sources.nearby_311(geo["lat"], geo["lng"], geo["street_number"], geo["street_name"]),
        )
        out.update(address=geo["matched_address"], town=geo["town"], lat=geo["lat"], lng=geo["lng"])
        out["flood_zone"] = flood.get("zone")
        if (flood.get("zone") or "").startswith(("A", "V")):
            out["flags"].append("flood_zone")
        out["reports_at_address"] = len(n311.get("reports_at_this_address", []))
        if out["reports_at_address"]:
            out["flags"].append("complaints_at_address")
        if ev and ev.get("found"):
            out["eviction_percentile"] = ev["percent_of_county_tracts_with_fewer_filings"]
            if ev["percent_of_county_tracts_with_fewer_filings"] >= 75:
                out["flags"].append("high_eviction_tract")
        if not parcels:
            out["flags"].append("no_record")
            out["owner"] = None
            return out
        p = parcels[0]
        owner = p.get("owner") or ""
        out.update(owner=owner, property_use=p.get("property_use"), owner_mailing=p.get("owner_mailing_address"))
        mailing_state = (p.get("owner_mailing_address") or "").rsplit(",", 1)[-1].strip().upper()
        if mailing_state and mailing_state not in ("CT", "CONNECTICUT"):
            out["flags"].append("out_of_state_owner")
        portfolio = await sources.owner_portfolio(owner)
        out["owner_ct_parcels"] = portfolio["connecticut_parcels"]
        if sources.looks_like_entity(owner):
            matches = await sources.search_business(owner)
            exact = [m for m in matches if m["exact_match"]]
            if exact:
                out["company_status"] = exact[0]["status"]
                out["principals"] = [x["name"] for x in await sources.business_principals(exact[0]["business_id"])][:5]
                if (exact[0]["status"] or "").lower() != "active":
                    out["flags"].append("inactive_company")
        return out
    except sources.SourceError as exc:
        return {**out, "status": "error", "error": str(exc)}


def rollup(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Group screened listings by owner of record."""
    owners: dict[str, dict[str, Any]] = {}
    for r in rows:
        if r.get("status") != "done" or not r.get("owner"):
            continue
        o = owners.setdefault(r["owner"], {"owner": r["owner"], "listings": 0, "flagged_listings": 0,
                                           "flags": {}, "owner_ct_parcels": r.get("owner_ct_parcels"),
                                           "company_status": r.get("company_status"), "principals": r.get("principals")})
        o["listings"] += 1
        if r["flags"]:
            o["flagged_listings"] += 1
        for f in r["flags"]:
            o["flags"][f] = o["flags"].get(f, 0) + 1
    table = sorted(owners.values(), key=lambda o: (-o["flagged_listings"], -o["listings"], o["owner"]))
    screened = [r for r in rows if r.get("status") == "done"]
    flag_counts: dict[str, int] = {}
    for r in screened:
        for f in r["flags"]:
            flag_counts[f] = flag_counts.get(f, 0) + 1
    return {
        "addresses_submitted": len(rows),
        "addresses_screened": len(screened),
        "listings_with_flags": sum(1 for r in screened if r["flags"]),
        "distinct_owners": len(owners),
        "flag_counts": flag_counts,
        "owners": table,
    }


class OrgSummary(BaseModel):
    headline: str
    summary: str = Field(description="A short paragraph for the organization's staff")
    priorities: list[str] = Field(description="3 to 5 concrete follow-ups (which owners or listings to look at first, and why)")


@dataclass
class OrgState:
    context_text: str


ORG_PROMPT = """You are the Portfolio Analyst for "Before You Sign", writing for staff at a housing organization (a nonprofit,
a university housing office or a tenant union) that screened a batch of Connecticut rental listings with public records.
You receive the screening rollup: counts of warning flags and a table of owners with how many of the listings they hold.

Write a short executive summary and 3-5 prioritized follow-ups (which owners or listings to review first, and why).
Rules: use ONLY the numbers in the input, copied exactly. Describe patterns in the data; do not accuse any owner of
wrongdoing; flags are prompts for review, not findings of fact. Write in the REPORT LANGUAGE named in the request."""

org_agent = Agent(output_type=OrgSummary, deps_type=OrgState, instructions=ORG_PROMPT, name="portfolio_analyst",
                  model_settings={"openai_prompt_cache_key": "before-you-sign-org", "openai_prompt_cache_retention": "24h",
                                  "openai_reasoning_effort": REASONING_EFFORT, "max_tokens": 6000}, retries=2)


@org_agent.output_validator
def _org_valid(ctx: RunContext[OrgState], out: OrgSummary) -> OrgSummary:
    supported = _numbers(ctx.deps.context_text) | {"311"}
    text = " ".join([out.headline, out.summary, *out.priorities])
    bad = sorted(n for n in _numbers(text) if len(n.replace(".", "")) >= 2 and n not in supported)
    if bad:
        raise ModelRetry(f"These numbers are not in the screening data: {bad}. Use only numbers from the input.")
    return out


async def run_org(job: Job, addresses: list[str], language_name: str, language_code: str) -> None:
    import time

    from .runner import _write_logs

    sem = asyncio.Semaphore(PARALLEL)
    rows: list[dict[str, Any]] = [None] * len(addresses)  # type: ignore[list-item]

    async def one(i: int, a: str) -> None:
        async with sem:
            rows[i] = await screen_address(a)
        job.emit("screened", index=i, address=a, status=rows[i]["status"], flags=rows[i]["flags"])

    try:
        job.emit("status", message=f"Screening {len(addresses)} addresses")
        await asyncio.wait_for(asyncio.gather(*(one(i, a) for i, a in enumerate(addresses))), timeout=420)
        roll = rollup(rows)
        job.emit("status", message="Writing the summary")
        compact = {**roll, "owners": [{k: o[k] for k in ("owner", "listings", "flagged_listings", "flags",
                                                            "owner_ct_parcels", "company_status")} for o in roll["owners"][:15]],
                   "flag_meanings": FLAG_TEXT}
        context = json.dumps(compact, ensure_ascii=False)
        run = await asyncio.wait_for(org_agent.run(f"REPORT LANGUAGE: {language_name}\nScreening rollup:\n{context}",
                                                   deps=OrgState(context), model=model(),
                                                   usage_limits=UsageLimits(request_limit=4, total_tokens_limit=80_000)),
                                     timeout=180)
        u = run.usage
        usage = {"requests": u.requests, "input_tokens": u.input_tokens or 0, "cache_read_tokens": u.cache_read_tokens or 0,
                 "cache_write_tokens": u.cache_write_tokens or 0, "output_tokens": u.output_tokens or 0}
        job.result = {"mode": "org", "language": language_code, "rows": rows, "rollup": roll, "flag_text": FLAG_TEXT,
                      "summary": run.output.model_dump(), "usage": {**usage, "cost_usd": round(cost_usd(usage), 6)},
                      "seconds": round(time.time() - job.started, 1)}
        job.status = "done"
    except asyncio.TimeoutError:
        job.status, job.error = "error", "The screening took too long and was stopped."
    except Exception as exc:
        job.status, job.error = "error", f"Something went wrong ({exc.__class__.__name__}: {str(exc)[:200]})."
    finally:
        job.emit("finished", status=job.status, error=job.error)
        _write_logs(job, None)
