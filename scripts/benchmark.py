"""Measure tokens, cost, speed and lease accuracy over a fixed set of checks.

    python scripts/benchmark.py                          # uses REASONING_EFFORT=low (the default)
    REASONING_EFFORT=medium python scripts/benchmark.py  # compare a higher effort setting

Runs 5 addresses without a lease and the 3 sample leases (answer key in scripts/eval_leases.py).
Saves every run and a summary to logs/benchmark_<effort>.json. Costs about $0.03 per run of the script.
"""

import asyncio
import json
import os
import statistics
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
os.environ.setdefault("FRESH_RESPONSES", "1")  # measure real model calls, not gateway replays
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

from app.config import LOG_DIR, PRICES, REASONING_EFFORT  # noqa: E402
from app.lease import extract_lease_text  # noqa: E402
from app.runner import new_job, run_check  # noqa: E402
from eval_leases import ADDRESS as LEASE_ADDRESS, ANSWER_KEY, score  # noqa: E402

ADDRESSES = [
    "31 Chapel St, New Haven, CT",
    "100 Howe St, New Haven, CT",
    "224 Wooster St, New Haven, CT",
    "43 William St, New Haven, CT",
    "75 Lenox St, New Haven, CT",
]
PARALLEL = 3


async def one(address: str, lease_name: str | None, sem: asyncio.Semaphore) -> dict:
    async with sem:
        text = None
        if lease_name:
            p = ROOT / "samples" / lease_name
            text = extract_lease_text(p.name, p.read_bytes())
        job = new_job(address, has_lease=text is not None)
        await run_check(job, address, text)
    res = job.result or {}
    t = res.get("usage", {}).get("totals", {})
    row = {
        "job_id": job.id, "address": address, "lease": lease_name, "status": job.status, "error": job.error,
        "seconds": res.get("seconds"), "requests": t.get("requests"), "tool_calls": t.get("tool_calls"),
        "input_tokens": t.get("input_tokens"), "cached_input_tokens": t.get("cache_read_tokens"),
        "cache_write_tokens": t.get("cache_write_tokens"), "output_tokens": t.get("output_tokens"),
        "cost_usd": t.get("cost_usd"),
        "findings": len(res.get("report", {}).get("findings", [])),
        "safety_events": len(res.get("safety_events", [])),
    }
    if lease_name and job.status == "done":
        row.update(score(res["report"]["findings"], lease_name))
    return row


def summarize(rows: list[dict]) -> dict:
    ok = [r for r in rows if r["status"] == "done"]
    if not ok:
        return {"runs": 0}
    out = {"runs": len(ok), "failed": len(rows) - len(ok)}
    for k in ("seconds", "input_tokens", "cached_input_tokens", "output_tokens", "cost_usd", "requests", "tool_calls"):
        vals = [r[k] for r in ok if r.get(k) is not None]
        out[f"mean_{k}"] = round(statistics.mean(vals), 6 if k == "cost_usd" else 1)
        out[f"median_{k}"] = round(statistics.median(vals), 6 if k == "cost_usd" else 1)
    # What the same tokens would have cost with no prompt caching (all input at the full input price).
    no_cache = statistics.mean(
        (r["input_tokens"] * PRICES["input"] + r["output_tokens"] * PRICES["output"]) / 1_000_000 for r in ok)
    out["mean_cost_usd_if_no_caching"] = round(no_cache, 6)
    if any("planted_issues" in r for r in ok):
        for k in ("planted_issues", "flagged_red", "flagged_red_with_statute", "false_alarm_reds_on_fair_lease"):
            out[k] = sum(r.get(k, 0) for r in ok)
    return out


async def main() -> None:
    sem = asyncio.Semaphore(PARALLEL)
    tasks = [one(a, None, sem) for a in ADDRESSES] + [one(LEASE_ADDRESS, n, sem) for n in ANSWER_KEY]
    rows = await asyncio.gather(*tasks)
    result = {
        "reasoning_effort": REASONING_EFFORT,
        "address_only": summarize([r for r in rows if not r["lease"]]),
        "with_lease": summarize([r for r in rows if r["lease"]]),
        "runs": rows,
    }
    LOG_DIR.mkdir(exist_ok=True)
    path = LOG_DIR / f"benchmark_{REASONING_EFFORT}.json"
    path.write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(json.dumps({k: v for k, v in result.items() if k != "runs"}, indent=2))
    print(f"Saved to {path.relative_to(ROOT)}")


if __name__ == "__main__":
    asyncio.run(main())
