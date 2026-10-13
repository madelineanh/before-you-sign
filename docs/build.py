"""Build report.html and slides.html from the templates, the measured data, and the screenshots.

    python docs/build.py

Every number in the two documents comes from docs/data/*.json (copied from logs/ after real runs),
and every screenshot from docs/img/ is embedded in the HTML, so both files open anywhere by double-click.
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

DISPLAY = {"Lead Advisor": "Lead Advisor", "Landlord Investigator": "Ownership Review",
           "Neighborhood Scout": "Property & Area Records", "Lease Reviewer": "Lease Compliance",
           "Intake": "Intake"}
TOOL_WORDS = {
    "ask_landlord_investigator": "assigns the ownership review",
    "ask_neighborhood_scout": "assigns the property & area review",
    "ask_lease_reviewer": "assigns the lease compliance review",
    "get_property_records": "pulls New Haven assessor records",
    "get_owner_portfolio": "counts the owner's New Haven parcels",
    "search_business_registry": "searches the CT business registry",
    "get_company_principals": "looks up the LLC's registered principals",
    "get_311_reports": "pulls 311 reports within ~150 m",
    "get_eviction_filings": "pulls tract eviction filings",
    "get_flood_zone": "queries the FEMA flood map",
    "read_lease": "reads the uploaded lease",
    "check_security_deposit": "runs the deposit calculator",
    "check_late_fee": "runs the late-fee calculator",
    "check_upfront_fees": "runs the up-front fee check",
}

# Usage assumptions for the cost projection (stated in the report).
CHECKS_PER_USER_MONTH = 6       # addresses checked during one month of apartment hunting
LEASE_CHECKS_PER_USER_MONTH = 2  # finalists whose lease is also reviewed


def load(name: str):
    return json.loads((DATA / name).read_text(encoding="utf-8"))


def img(name: str) -> str:
    data = base64.b64encode((IMG / f"{name}.jpg").read_bytes()).decode()
    return f"data:image/jpeg;base64,{data}"


def usd(x: float, digits: int = 4) -> str:
    return f"${x:,.{digits}f}"


def num(x: float) -> str:
    return f"{x:,.0f}"


def metrics() -> dict[str, str]:
    low, med = load("benchmark_low.json"), load("benchmark_medium.json")
    long_run = load("long_lease_run.json")
    safety = load("safety_tests.json")
    traced = load("traced_run.json")
    a, l = low["address_only"], low["with_lease"]
    ma, ml = med["address_only"], med["with_lease"]

    per_user = (CHECKS_PER_USER_MONTH - LEASE_CHECKS_PER_USER_MONTH) * a["mean_cost_usd"] \
        + LEASE_CHECKS_PER_USER_MONTH * l["mean_cost_usd"]
    heavy_user = CHECKS_PER_USER_MONTH * long_run["usage"]["totals"]["cost_usd"]
    lt = long_run["usage"]["totals"]
    tu = traced["result"]["usage"]["totals"]
    lease_secs = [r["seconds"] for r in low["runs"] if r["lease"]]
    all_secs = sorted(r["seconds"] for r in low["runs"] + med["runs"])

    m = {
        "a_in": num(a["median_input_tokens"]), "a_cached": num(a["median_cached_input_tokens"]),
        "a_out": num(a["median_output_tokens"]), "a_cost": usd(a["median_cost_usd"]),
        "a_mean_cost": usd(a["mean_cost_usd"]), "a_secs": f"{a['median_seconds']:.0f}",
        "a_req": f"{a['median_requests']:.0f}", "a_nocache": usd(a["mean_cost_usd_if_no_caching"]),
        "a_cache_pct": f"{100 * a['median_cached_input_tokens'] / a['median_input_tokens']:.0f}%",
        "l_in": num(l["median_input_tokens"]), "l_cached": num(l["median_cached_input_tokens"]),
        "l_out": num(l["median_output_tokens"]), "l_cost": usd(l["median_cost_usd"]),
        "l_mean_cost": usd(l["mean_cost_usd"]), "l_secs": f"{l['median_seconds']:.0f}",
        "l_req": f"{l['median_requests']:.0f}", "l_nocache": usd(l["mean_cost_usd_if_no_caching"]),
        "l_cache_pct": f"{100 * l['median_cached_input_tokens'] / l['median_input_tokens']:.0f}%",
        "l_cache_saving": f"{100 * (1 - l['mean_cost_usd'] / l['mean_cost_usd_if_no_caching']):.0f}%",
        "a_cache_saving": f"{100 * (1 - a['mean_cost_usd'] / a['mean_cost_usd_if_no_caching']):.0f}%",
        "ma_mean_cost": usd(ma["mean_cost_usd"]), "ml_mean_cost": usd(ml["mean_cost_usd"]),
        "ml_secs": f"{ml['median_seconds']:.0f}", "ma_secs": f"{ma['median_seconds']:.0f}",
        "ml_out": num(ml["median_output_tokens"]),
        "med_lease_premium": f"{100 * (ml['mean_cost_usd'] / l['mean_cost_usd'] - 1):.0f}%",
        "med_addr_premium": f"{100 * (ma['mean_cost_usd'] / a['mean_cost_usd'] - 1):.0f}%",
        "low_flagged": f"{l['flagged_red']} of {l['planted_issues']}",
        "low_cited": f"{l['flagged_red_with_statute']} of {l['planted_issues']}",
        "med_flagged": f"{ml['flagged_red']} of {ml['planted_issues']}",
        "med_cited": f"{ml['flagged_red_with_statute']} of {ml['planted_issues']}",
        "false_alarms": str(l["false_alarm_reds_on_fair_lease"]),
        "planted": str(l["planted_issues"]),
        "n_bench": str(len(low["runs"]) + len(med["runs"])),
        "n_slow": str(sum(s > 60 for s in all_secs)),
        "median_all_secs": f"{statistics.median(all_secs):.0f}",
        "long_pages": str(long_run["pages"]), "long_req": str(lt["requests"]), "long_in": num(lt["input_tokens"]),
        "long_cached": num(lt["cache_read_tokens"]), "long_out": num(lt["output_tokens"]),
        "long_cost": usd(lt["cost_usd"]), "long_secs": f"{long_run['seconds']:.0f}",
        "per_user": usd(per_user, 3), "per_100": usd(per_user * 100, 2), "per_10k": usd(per_user * 10_000, 0),
        "heavy_user": usd(heavy_user, 3), "heavy_100": usd(heavy_user * 100, 2),
        "heavy_10k": usd(heavy_user * 10_000, 0),
        "checks_month": str(CHECKS_PER_USER_MONTH), "lease_checks_month": str(LEASE_CHECKS_PER_USER_MONTH),
        "t_job": traced["job_id"].upper(), "t_secs": f"{traced['result']['seconds']:.0f}",
        "t_in": num(tu["input_tokens"]), "t_cached": num(tu["cache_read_tokens"]), "t_out": num(tu["output_tokens"]),
        "t_cost": usd(tu["cost_usd"]), "t_req": str(tu["requests"]), "t_tools": str(tu["tool_calls"]),
        "t_findings": str(len(traced["result"]["report"]["findings"])),
        "t_red": str(sum(f["severity"] == "red" for f in traced["result"]["report"]["findings"])),
        "safety_pass": f"{sum(r['passed'] for r in safety)} of {len(safety)}",
        "lease_secs_max": f"{max(lease_secs):.0f}",
    }
    m["usage_rows"] = usage_rows(traced)
    m["trace_rows"] = trace_rows(traced)
    m["safety_rows"] = safety_rows(safety)
    m["effort_rows"] = effort_rows(low, med)
    return m


def usage_rows(traced: dict) -> str:
    rows = []
    for a in traced["result"]["usage"]["by_agent"]:
        rows.append(f"<tr><td>{html.escape(DISPLAY.get(a['agent'], a['agent']))}</td><td>{a['requests']}</td>"
                    f"<td>{num(a['input_tokens'])}</td><td>{num(a['cache_read_tokens'])}</td>"
                    f"<td>{num(a['output_tokens'])}</td><td>{usd(a['cost_usd'])}</td></tr>")
    t = traced["result"]["usage"]["totals"]
    rows.append(f"<tr class='total'><td>Total</td><td>{t['requests']}</td><td>{num(t['input_tokens'])}</td>"
                f"<td>{num(t['cache_read_tokens'])}</td><td>{num(t['output_tokens'])}</td><td>{usd(t['cost_usd'])}</td></tr>")
    return "\n".join(rows)


def trace_rows(traced: dict) -> str:
    rows = []
    for e in traced["events"]:
        who = html.escape(DISPLAY.get(e.get("agent", ""), e.get("agent", "")))
        if e["kind"] == "model_response":
            calls = e.get("tool_calls") or []
            u = e.get("usage", {})
            toks = f"{num(u.get('input', 0))} / {num(u.get('cached', 0))} / {num(u.get('output', 0))}"
            if calls:
                parts = []
                for c in calls:
                    arg = c["args"].get("company_name") or c["args"].get("owner_name") or ""
                    words = TOOL_WORDS.get(c["tool"], c["tool"])
                    parts.append(html.escape(words + (f": {arg}" if arg else "")))
                action = "; ".join(parts)
            else:
                action = "returns its structured findings"
            rows.append(f"<tr><td>{e['t']:.1f}s</td><td>{who}</td><td>{action}</td><td>{toks}</td></tr>")
        elif e["kind"] == "safety":
            rows.append(f"<tr class='safety'><td>{e['t']:.1f}s</td><td>{who}</td>"
                        f"<td>Safeguard: {html.escape(e['detail'])}</td><td></td></tr>")
        elif e["kind"] == "status" and e["message"].startswith("Matched"):
            rows.append(f"<tr><td>{e['t']:.1f}s</td><td>Intake (code)</td><td>Census geocoder: "
                        f"{html.escape(e['message'])}; New Haven scope check passes</td><td>no AI</td></tr>")
    return "\n".join(rows)


def safety_rows(results: list[dict]) -> str:
    return "\n".join(
        f"<tr><td>{'PASS' if r['passed'] else 'FAIL'}</td><td>{html.escape(r['test'])}</td></tr>" for r in results)


def effort_rows(low: dict, med: dict) -> str:
    rows = []
    for name, d in (("Low (chosen)", low), ("Medium", med)):
        a, l = d["address_only"], d["with_lease"]
        rows.append(
            f"<tr><td>{name}</td><td>{usd(a['mean_cost_usd'])}</td><td>{usd(l['mean_cost_usd'])}</td>"
            f"<td>{num(l['median_output_tokens'])}</td><td>{l['median_seconds']:.0f}s</td>"
            f"<td>{l['flagged_red']} / {l['planted_issues']}</td><td>{l['flagged_red_with_statute']} / {l['planted_issues']}</td>"
            f"<td>{l['false_alarm_reds_on_fair_lease']}</td></tr>")
    return "\n".join(rows)


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
