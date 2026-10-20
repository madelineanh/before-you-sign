"""Build report.html and slides.html from the templates, the measured data, and the screenshots.

    python docs/build.py

Every number in the two documents comes from docs/data/*.json (copied from logs/ after real runs), and
every screenshot in docs/img/ is embedded, so both files open anywhere by double-click.
"""

from __future__ import annotations

import base64
import html
import json
import re
import statistics
from pathlib import Path

DOCS = Path(__file__).resolve().parent
ROOT = DOCS.parent
DATA = DOCS / "data"
IMG = DOCS / "img"

DISPLAY = {"Lead Advisor": "Lead Advisor", "Landlord Investigator": "Landlord Investigator",
           "Neighborhood Scout": "Neighborhood Scout", "Lease Reviewer": "Lease Reviewer", "Intake": "Intake (code)"}
TOOL_WORDS = {
    "ask_landlord_investigator": "assigns the Landlord Investigator",
    "ask_neighborhood_scout": "assigns the Neighborhood Scout",
    "ask_lease_reviewer": "assigns the Lease Reviewer",
    "get_property_records": "pulls the town assessor records",
    "get_owner_portfolio": "counts the owner's Connecticut parcels",
    "search_business_registry": "searches the CT business registry",
    "get_company_principals": "looks up the LLC's registered principals",
    "get_311_reports": "pulls 311 reports within ~150 m",
    "get_eviction_filings": "pulls tract eviction filings",
    "get_flood_zone": "queries the FEMA flood map",
    "read_lease": "reads the (filtered) lease",
    "check_security_deposit": "runs the deposit calculator",
    "check_late_fee": "runs the late-fee calculator",
    "check_upfront_fees": "runs the up-front fee check",
}

# Usage assumptions for the cost projections (stated in the report).
RENTER_MONTH = {"address_checks": 4, "lease_checks": 2, "comparisons": 1, "listing_checks": 2,
                "letters": 1, "practice_sessions": 1}
ORG_RENTERS_PER_MONTH = 1000  # e.g. a housing office or nonprofit serving 1,000 renters a month
ORG_LISTINGS_PER_MONTH = 500  # listings it screens in batches of 12


def load(name: str):
    return json.loads((DATA / name).read_text(encoding="utf-8"))


def img(name: str) -> str:
    return "data:image/jpeg;base64," + base64.b64encode((IMG / f"{name}.jpg").read_bytes()).decode()


def usd(x: float, digits: int = 4) -> str:
    return f"${x:,.{digits}f}"


def num(x: float) -> str:
    return f"{x:,.0f}"


def feature(rows: list[dict], prefix: str) -> dict:
    return next(r for r in rows if r["feature"].startswith(prefix))


def metrics() -> dict[str, str]:
    low, med = load("benchmark_low.json"), load("benchmark_medium.json")
    long_run, safety, traced, feats = load("long_lease_run.json"), load("safety_tests.json"), load("traced_run.json"), load("feature_costs.json")
    a, l = low["address_only"], low["with_lease"]
    ma, ml = med["address_only"], med["with_lease"]
    cmp_ = feature(feats, "Shortlist")
    scam = feature(feats, "Listing scam check (listing_scam")
    normal = feature(feats, "Listing scam check (listing_normal")
    letter = feature(feats, "Negotiation letter")
    practice = feature(feats, "Practice")
    org = feature(feats, "Organization")
    listing_cost = (scam["cost_usd"] + normal["cost_usd"]) / 2

    per_renter = (RENTER_MONTH["address_checks"] * a["mean_cost_usd"] + RENTER_MONTH["lease_checks"] * l["mean_cost_usd"]
                  + RENTER_MONTH["comparisons"] * cmp_["cost_usd"] + RENTER_MONTH["listing_checks"] * listing_cost
                  + RENTER_MONTH["letters"] * letter["cost_usd"] + RENTER_MONTH["practice_sessions"] * practice["cost_usd"])
    org_screen = ORG_LISTINGS_PER_MONTH / 12 * org["cost_usd"]
    org_total = ORG_RENTERS_PER_MONTH * per_renter + org_screen
    lt = long_run["usage"]["totals"]
    tu = traced["result"]["usage"]["totals"]
    all_secs = sorted(r["seconds"] for r in low["runs"] + med["runs"])

    m = {
        "a_in": num(a["median_input_tokens"]), "a_cached": num(a["median_cached_input_tokens"]),
        "a_out": num(a["median_output_tokens"]), "a_cost": usd(a["median_cost_usd"]), "a_mean_cost": usd(a["mean_cost_usd"]),
        "a_secs": f"{a['median_seconds']:.0f}", "a_req": f"{a['median_requests']:.0f}",
        "l_in": num(l["median_input_tokens"]), "l_cached": num(l["median_cached_input_tokens"]),
        "l_out": num(l["median_output_tokens"]), "l_cost": usd(l["median_cost_usd"]), "l_mean_cost": usd(l["mean_cost_usd"]),
        "l_secs": f"{l['median_seconds']:.0f}", "l_req": f"{l['median_requests']:.0f}", "l_nocache": usd(l["mean_cost_usd_if_no_caching"]),
        "l_cache_pct": f"{100 * l['median_cached_input_tokens'] / l['median_input_tokens']:.0f}%",
        "l_cache_saving": f"{100 * (1 - l['mean_cost_usd'] / l['mean_cost_usd_if_no_caching']):.0f}%",
        "a_cache_saving": f"{100 * (1 - a['mean_cost_usd'] / a['mean_cost_usd_if_no_caching']):.0f}%",
        "ml_secs": f"{ml['median_seconds']:.0f}",
        "med_lease_premium": f"{100 * (ml['mean_cost_usd'] / l['mean_cost_usd'] - 1):.0f}%",
        "med_addr_premium": f"{100 * (ma['mean_cost_usd'] / a['mean_cost_usd'] - 1):.0f}%",
        "low_flagged": f"{l['flagged_red']} of {l['planted_issues']}", "low_cited": f"{l['flagged_red_with_statute']} of {l['planted_issues']}",
        "med_cited": f"{ml['flagged_red_with_statute']} of {ml['planted_issues']}", "false_alarms": str(l["false_alarm_reds_on_fair_lease"]),
        "n_bench": str(len(all_secs)), "n_slow": str(sum(s > 60 for s in all_secs)), "median_all_secs": f"{statistics.median(all_secs):.0f}",
        "long_pages": str(long_run["pages"]), "long_req": str(lt["requests"]), "long_in": num(lt["input_tokens"]),
        "long_cached": num(lt["cache_read_tokens"]), "long_out": num(lt["output_tokens"]), "long_cost": usd(lt["cost_usd"]),
        "long_secs": f"{long_run['seconds']:.0f}",
        "cmp_cost": usd(cmp_["cost_usd"]), "cmp_secs": f"{cmp_['seconds']:.0f}", "cmp_in": num(cmp_["input_tokens"]), "cmp_out": num(cmp_["output_tokens"]),
        "scam_cost": usd(scam["cost_usd"]), "scam_secs": f"{scam['seconds']:.0f}", "scam_risk": str(scam.get("risk")), "normal_risk": str(normal.get("risk")),
        "listing_cost": usd(listing_cost), "listing_in": num((scam["input_tokens"] + normal["input_tokens"]) / 2),
        "listing_out": num((scam["output_tokens"] + normal["output_tokens"]) / 2),
        "letter_cost": usd(letter["cost_usd"]), "letter_secs": f"{letter['seconds']:.0f}", "letter_in": num(letter["input_tokens"]), "letter_out": num(letter["output_tokens"]),
        "practice_cost": usd(practice["cost_usd"]), "practice_secs": f"{practice['seconds']:.0f}", "practice_in": num(practice["input_tokens"]),
        "practice_out": num(practice["output_tokens"]), "practice_won": str(len(practice.get("issues_won", []))),
        "org_cost": usd(org["cost_usd"]), "org_secs": f"{org['seconds']:.0f}", "org_in": num(org["input_tokens"]), "org_out": num(org["output_tokens"]),
        "org_flagged": str(org.get("listings_with_flags")), "org_owners": str(org.get("distinct_owners")),
        "per_renter": usd(per_renter, 3), "per_100": usd(per_renter * 100, 2), "per_10k": usd(per_renter * 10_000, 0),
        "org_renters": f"{ORG_RENTERS_PER_MONTH:,}", "org_listings": f"{ORG_LISTINGS_PER_MONTH:,}",
        "org_screen": usd(org_screen, 3), "org_total": usd(org_total, 2),
        "t_job": traced["job_id"].upper(), "t_secs": f"{traced['result']['seconds']:.0f}", "t_in": num(tu["input_tokens"]),
        "t_cached": num(tu["cache_read_tokens"]), "t_out": num(tu["output_tokens"]), "t_cost": usd(tu["cost_usd"]),
        "t_req": str(tu["requests"]), "t_tools": str(tu["tool_calls"]),
        "t_findings": str(len(traced["result"]["report"]["findings"])),
        "t_red": str(sum(f["severity"] == "red" for f in traced["result"]["report"]["findings"])),
        "safety_pass": f"{sum(r['passed'] for r in safety)} of {len(safety)}",
        "renter_assumption": "4 address checks, 2 lease checks, 1 shortlist comparison, 2 listing checks, 1 landlord letter and 1 practice session",
    }
    m["usage_rows"] = usage_rows(traced)
    m["trace_rows"] = trace_rows(traced)
    m["safety_rows"] = "\n".join(f"<tr><td>{'PASS' if r['passed'] else 'FAIL'}</td><td>{html.escape(r['test'])}</td></tr>" for r in safety)
    m["effort_rows"] = effort_rows(low, med)
    return m


def usage_rows(traced: dict) -> str:
    rows = [f"<tr><td>{html.escape(DISPLAY.get(a['agent'], a['agent']))}</td><td>{a['requests']}</td><td>{num(a['input_tokens'])}</td>"
            f"<td>{num(a['cache_read_tokens'])}</td><td>{num(a['output_tokens'])}</td><td>{usd(a['cost_usd'])}</td></tr>"
            for a in traced["result"]["usage"]["by_agent"]]
    t = traced["result"]["usage"]["totals"]
    rows.append(f"<tr class='total'><td>Total</td><td>{t['requests']}</td><td>{num(t['input_tokens'])}</td>"
                f"<td>{num(t['cache_read_tokens'])}</td><td>{num(t['output_tokens'])}</td><td>{usd(t['cost_usd'])}</td></tr>")
    return "\n".join(rows)


def trace_rows(traced: dict) -> str:
    rows = []
    for e in traced["events"]:
        who = html.escape(DISPLAY.get(e.get("agent", ""), e.get("agent", "")))
        if e["kind"] == "model_response":
            u = e.get("usage", {})
            toks = f"{num(u.get('input', 0))} / {num(u.get('cached', 0))} / {num(u.get('output', 0))}"
            calls = e.get("tool_calls") or []
            if calls:
                action = "; ".join(html.escape(TOOL_WORDS.get(c["tool"], c["tool"]) + (
                    f": {c['args'].get('company_name') or c['args'].get('owner_name')}"
                    if (c["args"].get("company_name") or c["args"].get("owner_name")) else "")) for c in calls)
            else:
                action = "returns its structured, fact-checked findings"
            rows.append(f"<tr><td>{e['t']:.1f}s</td><td>{who}</td><td>{action}</td><td>{toks}</td></tr>")
        elif e["kind"] == "safety":
            rows.append(f"<tr class='safety'><td>{e['t']:.1f}s</td><td>{who}</td><td>Safeguard: {html.escape(e['detail'])}</td><td></td></tr>")
        elif e["kind"] == "status" and e["message"].startswith("Matched"):
            rows.append(f"<tr><td>{e['t']:.1f}s</td><td>Intake (code)</td><td>Census geocoder: {html.escape(e['message'])}; "
                        f"Connecticut scope check passes</td><td>no AI</td></tr>")
    return "\n".join(rows)


def effort_rows(low: dict, med: dict) -> str:
    out = []
    for name, d in (("Low (chosen)", low), ("Medium", med)):
        a, l = d["address_only"], d["with_lease"]
        out.append(f"<tr><td>{name}</td><td>{usd(a['mean_cost_usd'])}</td><td>{usd(l['mean_cost_usd'])}</td>"
                   f"<td>{num(l['median_output_tokens'])}</td><td>{l['median_seconds']:.0f}s</td>"
                   f"<td>{l['flagged_red']} / {l['planted_issues']}</td><td>{l['flagged_red_with_statute']} / {l['planted_issues']}</td>"
                   f"<td>{l['false_alarm_reds_on_fair_lease']}</td></tr>")
    return "\n".join(out)


def render(template: str, m: dict[str, str]) -> str:
    out = re.sub(r"\{\{img:([\w-]+)\}\}", lambda x: img(x.group(1)), template)
    missing = set()

    def sub(x: re.Match) -> str:
        if x.group(1) not in m:
            missing.add(x.group(1))
            return x.group(0)
        return m[x.group(1)]

    out = re.sub(r"\{\{(\w+)\}\}", sub, out)
    if missing:
        raise SystemExit(f"Missing values for: {sorted(missing)}")
    return out


def main() -> None:
    m = metrics()
    for name in ("report", "slides"):
        tpl = (DOCS / f"{name}_template.html").read_text(encoding="utf-8")
        out = ROOT / f"{name}.html"
        out.write_text(render(tpl, m), encoding="utf-8")
        print(f"wrote {out.name} ({out.stat().st_size / 1_000_000:.1f} MB)")


if __name__ == "__main__":
    main()
