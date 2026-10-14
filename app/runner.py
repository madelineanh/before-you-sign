"""Runs one renter check from start to finish and logs it."""

from __future__ import annotations

import asyncio
import hashlib
import json
import re
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from pydantic_ai.exceptions import UsageLimitExceeded

from . import sources
from .agents import LEAD, CheckState, lead_agent, risk_points, run_agent, run_comparison
from .config import LOG_DIR, MODEL_NAME, PRICES, SAFETY_FILTERS_ON, cost_usd
from .safety import filter_untrusted

CHECK_TIMEOUT_SECONDS = 420


@dataclass
class Job:
    id: str
    address_input: str
    has_lease: bool
    status: str = "running"  # running | done | error
    events: list[dict[str, Any]] = field(default_factory=list)
    result: dict[str, Any] | None = None
    error: str | None = None
    started: float = field(default_factory=time.time)

    def emit(self, kind: str, **data: Any) -> None:
        self.events.append({"t": round(time.time() - self.started, 2), "kind": kind, **data})


JOBS: dict[str, Job] = {}


def new_job(address: str, has_lease: bool) -> Job:
    job = Job(id=uuid.uuid4().hex[:10], address_input=address, has_lease=has_lease)
    JOBS[job.id] = job
    return job


def _usage_table(state: CheckState) -> dict[str, Any]:
    rows = []
    for agent, u in state.usage_by_agent.items():
        rows.append({"agent": agent, **u, "cost_usd": round(cost_usd(u), 6)})
    totals = state.totals()
    return {
        "model": MODEL_NAME,
        "prices_per_million_usd": PRICES,
        "by_agent": rows,
        "totals": {**totals, "cost_usd": round(cost_usd(totals), 6)},
    }


def _loggable(evidence_id: str, data: Any) -> Any:
    """The full lease is never written to disk; log only its size and a fingerprint."""
    if evidence_id in ("LEASE", "LISTING"):
        return {"withheld": "text is not saved", "characters": len(data),
                "sha256": hashlib.sha256(data.encode("utf-8")).hexdigest()[:16]}
    return data


def _write_logs(job: Job, state: CheckState | None) -> None:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    (LOG_DIR / "traces").mkdir(exist_ok=True)
    trace = {
        "job_id": job.id,
        "when": datetime.now(timezone.utc).isoformat(),
        "address_input": job.address_input,
        "has_lease": job.has_lease,
        "status": job.status,
        "error": job.error,
        "safety_filters_on": SAFETY_FILTERS_ON,
        "events": job.events,
        "result": job.result,
        "evidence": {k: {"source": e.source, "url": e.url, "data": _loggable(k, e.data)}
                     for k, e in state.ledger.items.items()} if state else {},
    }
    (LOG_DIR / "traces" / f"{job.id}.json").write_text(json.dumps(trace, indent=2, default=str), encoding="utf-8")
    summary = {
        "job_id": job.id, "when": trace["when"], "address": job.address_input, "has_lease": job.has_lease,
        "status": job.status, "seconds": round(time.time() - job.started, 1),
        "usage": job.result.get("usage") if job.result else (_usage_table(state) if state else None),
        "safety_events": len(state.safety_events) if state else 0,
    }
    with open(LOG_DIR / "runs.jsonl", "a", encoding="utf-8") as f:
        f.write(json.dumps(summary, default=str) + "\n")


LANGUAGES = {"en": "English", "es": "Spanish (Español)", "pt": "Portuguese (Português)",
             "zh": "Simplified Chinese (简体中文)", "ko": "Korean (한국어)", "fr": "French (Français)"}


async def run_check(job: Job, address: str, lease_text: str | None, language: str = "en") -> None:
    state: CheckState | None = None
    try:
        job.emit("status", message="Finding the address")
        geo = await sources.geocode(normalize_address(address))
        if geo is None:
            raise UserFacingError("We couldn't find that address. Try the street number, street name, and town, "
                                  "like 31 Chapel St, New Haven, CT.")
        if geo["state"] != "CT" or not geo.get("town"):
            job.emit("safety", type="scope_block", agent="Intake",
                     detail=f"Address resolved to {geo['matched_address']}, outside Connecticut. No AI calls were made.")
            raise UserFacingError(
                f"That address matched {geo['matched_address']}. Before You Sign covers Connecticut addresses only."
            )
        town_note = f" (town of {geo['town']})" if geo["town"].upper() != geo["city"].upper() else ""
        job.emit("status", message=f"Matched {geo['matched_address']}{town_note}")

        state = CheckState(job_id=job.id, address=geo, emit=job.emit)

        if lease_text:
            filtered = filter_untrusted(lease_text)
            state.lease_text = filtered.text
            state.ledger.add("Uploaded lease (after safety filter)", filtered.text, id="LEASE")
            if filtered.flagged:
                state.safety("injection_filtered", "Intake",
                             f"Removed {len(filtered.flagged)} passage(s) in the lease that were addressed to an AI system.",
                             removed=filtered.flagged)
            if filtered.invisible_chars_removed:
                state.safety("invisible_text", "Intake",
                             f"Stripped {filtered.invisible_chars_removed} invisible character(s) from the lease.")

        prompt = (
            f"Renter's address: {geo['matched_address']}\n"
            f"Lease uploaded: {'yes' if state.lease_text else 'no'}\n"
            f"REPORT LANGUAGE: {LANGUAGES.get(language, 'English')}\n"
            "Please run the full check and write the renter's report."
        )
        report = await asyncio.wait_for(run_agent(lead_agent, LEAD, prompt, state, request_limit=8),
                                        timeout=CHECK_TIMEOUT_SECONDS)

        used_ids = {i for f in report.findings for i in f.evidence_ids}
        job.result = {
            "address": geo,
            "report": report.model_dump(),
            "registry_links": sources.REGISTRY_LINKS,
            "language": language if language in LANGUAGES else "en",
            "has_lease": bool(state.lease_text),
            "evidence": [e for e in state.ledger.public() if e["id"] in used_ids],
            "gaps_from_tools": state.gaps,
            "safety_events": state.safety_events,
            "usage": _usage_table(state),
            "seconds": round(time.time() - job.started, 1),
        }
        job.status = "done"
        job.emit("status", message="Report ready")
    except UserFacingError as exc:
        job.status, job.error = "error", str(exc)
    except UsageLimitExceeded as exc:
        job.status, job.error = "error", f"This check hit its spending limit and was stopped safely ({exc})."
        job.emit("safety", type="budget_stop", agent="Harness", detail=str(exc))
    except asyncio.TimeoutError:
        job.status, job.error = "error", f"This check took longer than {CHECK_TIMEOUT_SECONDS // 60} minutes and was stopped."
    except Exception as exc:  # show a friendly message, keep the details in the log
        job.status, job.error = "error", f"Something went wrong ({exc.__class__.__name__}: {str(exc)[:200]})."
    finally:
        if job.status != "done" and state is not None and job.result is None:
            job.result = {"usage": _usage_table(state), "safety_events": state.safety_events}
        job.emit("finished", status=job.status, error=job.error)
        _write_logs(job, state)


class UserFacingError(Exception):
    pass


MAX_LISTING_CHARS = 20_000


async def run_listing_check(job: Job, listing_text: str, language: str = "en") -> None:
    """Scam check for a pasted listing. The listing text is filtered, used for this check only, and never logged."""
    from .listing import FTC_REPORT_URL, ListingState, listing_agent

    state: ListingState | None = None
    try:
        filtered = filter_untrusted(listing_text)
        state = ListingState(job_id=job.id, address={"matched_address": ""}, emit=job.emit, listing_text=filtered.text)
        state.ledger.add("Pasted listing (after safety filter)", filtered.text, id="LISTING")
        if filtered.flagged:
            state.safety("injection_filtered", "Intake",
                         f"Removed {len(filtered.flagged)} passage(s) in the listing that were addressed to an AI system.")
        prompt = f"REPORT LANGUAGE: {LANGUAGES.get(language, 'English')}\nPlease check this rental listing."
        report = await asyncio.wait_for(run_agent(listing_agent, "Listing Analyst", prompt, state, request_limit=8),
                                        timeout=CHECK_TIMEOUT_SECONDS)
        used = {i for f in report.findings for i in f.evidence_ids}
        job.result = {
            "mode": "listing",
            "language": language if language in LANGUAGES else "en",
            "report": report.model_dump(),
            "address": state.address if state.address.get("matched_address") else None,
            "ftc_report_url": FTC_REPORT_URL,
            "evidence": [e for e in state.ledger.public() if e["id"] in used],
            "safety_events": state.safety_events,
            "usage": _usage_table(state),
            "seconds": round(time.time() - job.started, 1),
        }
        job.status = "done"
        job.emit("status", message="Listing check ready")
    except UsageLimitExceeded as exc:
        job.status, job.error = "error", f"This check hit its spending limit and was stopped safely ({exc})."
    except asyncio.TimeoutError:
        job.status, job.error = "error", "This check took too long and was stopped."
    except Exception as exc:
        job.status, job.error = "error", f"Something went wrong ({exc.__class__.__name__}: {str(exc)[:200]})."
    finally:
        job.emit("finished", status=job.status, error=job.error)
        _write_logs(job, state)


COMPARE_PARALLEL = 3


async def run_compare(job: Job, items: list[dict[str, Any]], language: str = "en") -> None:
    """Check each shortlisted address (in parallel), then have the Comparison Advisor rank them."""
    labels = "ABCDE"
    apartments: list[dict[str, Any]] = []
    sem = asyncio.Semaphore(COMPARE_PARALLEL)

    async def one(label: str, item: dict[str, Any]) -> None:
        async with sem:
            sub = new_job(item["address"], has_lease=False)
            job.emit("sub_started", label=label, address=item["address"], job_id=sub.id)
            await run_check(sub, item["address"], None, language)
        res = sub.result or {}
        report = res.get("report")
        apt = {"label": label, "address": item["address"], "rent": item.get("rent"), "job_id": sub.id,
               "status": sub.status, "error": sub.error, "report": report,
               "matched_address": (res.get("address") or {}).get("matched_address"),
               "lat": (res.get("address") or {}).get("lat"), "lng": (res.get("address") or {}).get("lng"),
               "usage": (res.get("usage") or {}).get("totals")}
        apartments.append(apt)
        job.emit("sub_done", label=label, address=item["address"], job_id=sub.id, status=sub.status,
                 verdict=(report or {}).get("verdict"), error=sub.error)

    try:
        job.emit("status", message=f"Checking {len(items)} apartments")
        await asyncio.wait_for(asyncio.gather(*(one(labels[i], it) for i, it in enumerate(items))),
                               timeout=CHECK_TIMEOUT_SECONDS + 120)
        apartments.sort(key=lambda a: a["label"])
        done = [a for a in apartments if a["status"] == "done" and a["report"]]
        if len(done) < 2:
            raise UserFacingError("At least two of the addresses need to be checked successfully to compare them.")
        job.emit("agent_start", agent="Comparison Advisor")
        job.emit("status", message="Comparing the apartments")
        comparison, cstate = await asyncio.wait_for(
            run_comparison(done, LANGUAGES.get(language, "English")), timeout=240)
        job.emit("agent_done", agent="Comparison Advisor")
        for e in cstate.safety_events:
            job.emit("safety", **e)
        keys = ("requests", "tool_calls", "input_tokens", "cache_read_tokens", "cache_write_tokens", "output_tokens")
        totals = {k: sum((a["usage"] or {}).get(k, 0) for a in done) + cstate.usage.get(k, 0) for k in keys}
        job.result = {
            "mode": "compare",
            "language": language if language in LANGUAGES else "en",
            "apartments": [{**{k: v for k, v in a.items() if k != "usage"},
                            "risk_points": risk_points(a["report"]) if a["report"] else None} for a in apartments],
            "scoring": "10 points for a Serious concerns verdict, 3 for Proceed with caution, +3 per red finding, "
                       "+1 per yellow finding; lower is better; ties go to the lower rent.",
            "comparison": comparison.model_dump(),
            "safety_events": cstate.safety_events,
            "usage": {"model": MODEL_NAME, "prices_per_million_usd": PRICES,
                      "by_agent": [{"agent": f"Apartment {a['label']} (all agents)", **(a["usage"] or {}),
                                    "cost_usd": round(cost_usd(a["usage"] or {}), 6)} for a in done]
                      + [{"agent": "Comparison Advisor", **cstate.usage, "cost_usd": round(cost_usd(cstate.usage), 6)}],
                      "totals": {**totals, "cost_usd": round(cost_usd(totals), 6)}},
            "seconds": round(time.time() - job.started, 1),
        }
        job.status = "done"
        job.emit("status", message="Comparison ready")
    except UserFacingError as exc:
        job.status, job.error = "error", str(exc)
    except asyncio.TimeoutError:
        job.status, job.error = "error", "The comparison took too long and was stopped."
    except Exception as exc:
        job.status, job.error = "error", f"Something went wrong ({exc.__class__.__name__}: {str(exc)[:200]})."
    finally:
        job.emit("finished", status=job.status, error=job.error)
        _write_logs(job, None)


def normalize_address(address: str) -> str:
    """Add ", CT" (and default to New Haven if no town was typed)."""
    address = " ".join(address.split())
    upper = address.upper()
    if re.search(r"(,|HAVEN)\s*CT\b|CONNECTICUT", upper):
        return address
    if "NEW HAVEN" in upper:
        return f"{address}, CT"
    return f"{address}, New Haven, CT"
