"""Measure tokens, cost and time for each feature beyond the single-property check (fresh model calls).

    python scripts/feature_costs.py

Runs: a 3-apartment shortlist comparison, both sample listings through the scam check, the negotiation
letter plus three practice turns on the saved sample report, and the 12-address organization sample batch.
Saves logs/feature_costs.json. Costs about $0.02.
"""

import asyncio
import json
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
os.environ.setdefault("FRESH_RESPONSES", "1")  # measure real model calls, not gateway replays
sys.path.insert(0, str(ROOT))

from app.coach import draft_letter, practice_turn  # noqa: E402
from app.config import LOG_DIR, cost_usd  # noqa: E402
from app.org import run_org  # noqa: E402
from app.runner import new_job, run_compare, run_listing_check  # noqa: E402

SHORTLIST = [{"address": "224 Wooster St, New Haven, CT", "rent": 1850},
             {"address": "31 Chapel St, New Haven, CT", "rent": 1500},
             {"address": "114 Prospect St, East Hartford, CT", "rent": 1400}]
ORG_BATCH = ["224 Wooster St, New Haven, CT", "43 William St, New Haven, CT", "37 William St, New Haven, CT",
             "32 Clinton Ave, New Haven, CT", "30 Pardee St, New Haven, CT", "75 Lenox St, New Haven, CT",
             "151 Howard Ave, New Haven, CT", "786 George St, New Haven, CT", "157 Howard Ave, New Haven, CT",
             "31 Chapel St, New Haven, CT", "100 Howe St, New Haven, CT", "114 Prospect St, East Hartford, CT"]
PRACTICE = [
    "Hi, can you make the lease better?",
    "The deposit is $5,400, three months of rent. Conn. Gen. Stat. § 47a-21(b) caps it at two months, so please lower it to $3,600 in writing.",
    "Please also remove the $150 application fee and $250 move-in fee; § 47a-4d doesn't allow them.",
]


def row(name: str, usage: dict, seconds: float, **extra) -> dict:
    keys = ("requests", "input_tokens", "cache_read_tokens", "output_tokens")
    return {"feature": name, **{k: usage.get(k, 0) for k in keys}, "cost_usd": round(cost_usd(usage), 6),
            "seconds": round(seconds, 1), **extra}


async def main() -> None:
    out = []

    job = new_job("[compare]", has_lease=False)
    await run_compare(job, SHORTLIST, "en")
    r = job.result or {}
    out.append(row("Shortlist comparison (3 apartments)", r.get("usage", {}).get("totals", {}), r.get("seconds", 0),
                   status=job.status, ranking=[x["label"] for x in r.get("comparison", {}).get("ranking", [])]))

    for name in ("listing_scam.txt", "listing_normal.txt"):
        job = new_job("[listing]", has_lease=False)
        await run_listing_check(job, (ROOT / "samples" / name).read_text(), "en")
        r = job.result or {}
        out.append(row(f"Listing scam check ({name})", r.get("usage", {}).get("totals", {}), r.get("seconds", 0),
                       status=job.status, risk=r.get("report", {}).get("risk")))

    sample = json.loads((ROOT / "docs" / "data" / "traced_run.json").read_text())["result"]
    t = time.time()
    _, usage = await draft_letter(sample)
    out.append(row("Negotiation letter", usage, time.time() - t))
    history, total, won = [], {}, []
    t = time.time()
    for msg in PRACTICE:
        turn = await practice_turn(sample, "tough", history, msg, "English")
        history += [{"role": "tenant", "text": msg}, {"role": "landlord", "text": turn["reply"]}]
        total = {k: total.get(k, 0) + v for k, v in turn["usage"].items()}
        won += turn["conceded"]
    out.append(row("Practice negotiation (3 turns, tough landlord)", total, time.time() - t, issues_won=sorted(set(won))))

    job = new_job("[org]", has_lease=False)
    await run_org(job, ORG_BATCH, "English", "en")
    r = job.result or {}
    out.append(row("Organization batch (12 addresses)", r.get("usage", {}), r.get("seconds", 0), status=job.status,
                   listings_with_flags=r.get("rollup", {}).get("listings_with_flags"),
                   distinct_owners=r.get("rollup", {}).get("distinct_owners")))

    LOG_DIR.mkdir(exist_ok=True)
    (LOG_DIR / "feature_costs.json").write_text(json.dumps(out, indent=2), encoding="utf-8")
    for o in out:
        print(json.dumps(o))
    print("Saved to logs/feature_costs.json")


if __name__ == "__main__":
    asyncio.run(main())
