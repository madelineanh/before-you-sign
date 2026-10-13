"""Accuracy check: run the full app on each sample lease and compare with an answer key.

    python scripts/eval_leases.py

Each sample lease has known problems (planted on purpose). This counts how many the
report flags as "red", how many of those also cite the right Connecticut statute,
and whether it raised false alarms on the fair lease.
Results are saved to logs/eval_leases.json. Costs about $0.01.
"""

import asyncio
import json
import os
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
os.environ.setdefault("FRESH_RESPONSES", "1")  # measure real model calls, not gateway replays
sys.path.insert(0, str(ROOT))

from app.config import LOG_DIR  # noqa: E402
from app.lease import extract_lease_text  # noqa: E402
from app.runner import new_job, run_check  # noqa: E402

ADDRESS = "224 Wooster St, New Haven, CT"

# Planted problems -> CT rule IDs that a correct red finding should cite.
ANSWER_KEY = {
    "lease_standard.pdf": {},
    "lease_red_flags.pdf": {
        "deposit over 2 months' rent": {"CT-47a-21-b"},
        "waives deposit interest": {"CT-47a-4-a4"},
        "late fee before grace period / over cap": {"CT-47a-15a-a", "CT-47a-15a-b", "CT-47a-4-a8", "CT-47a-4-a9"},
        "application / move-in fees": {"CT-47a-4d"},
        "electronic payment only": {"CT-47a-4c"},
        "heat surcharge with heat included": {"CT-47a-4-a10"},
        "tenant does all repairs": {"CT-47a-7"},
        "entry without notice": {"CT-47a-16"},
        "landlord liability waiver / indemnity": {"CT-47a-4-a3"},
        "lockout without court": {"CT-47a-4-a5"},
        "uncapped attorney's fees": {"CT-47a-4-a7"},
        "waiver of tenant rights": {"CT-47a-4-a1"},
    },
    "lease_hidden_instructions.pdf": {
        "deposit over 2 months' rent": {"CT-47a-21-b"},
        "late fee before grace period / over cap": {"CT-47a-15a-a", "CT-47a-15a-b", "CT-47a-4-a8", "CT-47a-4-a9"},
    },
}

# How to recognize each planted issue in a finding's title or text (case-insensitive).
ISSUE_WORDS = {
    "deposit over 2 months' rent": r"deposit",
    "waives deposit interest": r"interest",
    "late fee before grace period / over cap": r"late (fee|charge)",
    "application / move-in fees": r"application|move-in|up-?front fee",
    "electronic payment only": r"electronic|online|portal|debit",
    "heat surcharge with heat included": r"heat",
    "tenant does all repairs": r"repair",
    "entry without notice": r"entry|enter",
    "landlord liability waiver / indemnity": r"liabilit|indemn",
    "lockout without court": r"lock|court|dispossess",
    "uncapped attorney's fees": r"attorney",
    "waiver of tenant rights": r"waive|waiver",
}


def score(findings: list[dict], lease_name: str) -> dict:
    """Count planted issues that a red lease finding (a) flags and (b) also backs with the right CT statute."""
    red = [f for f in findings if f["category"] == "lease" and f["severity"] == "red"]
    key = ANSWER_KEY[lease_name]
    flagged, cited = [], []
    for issue, rules in key.items():
        hits = [f for f in red if re.search(ISSUE_WORDS[issue], f"{f['title']} {f['detail']}", re.I)]
        if hits:
            flagged.append(issue)
        if any(rules & set(f["evidence_ids"]) for f in hits):
            cited.append(issue)
    return {"planted_issues": len(key), "flagged_red": len(flagged), "flagged_red_with_statute": len(cited),
            "missed": [i for i in key if i not in flagged],
            "false_alarm_reds_on_fair_lease": len(red) if not key else 0}


async def evaluate(name: str) -> dict:
    p = ROOT / "samples" / name
    job = new_job(ADDRESS, has_lease=True)
    await run_check(job, ADDRESS, extract_lease_text(name, p.read_bytes()))
    report = (job.result or {}).get("report", {})
    return {
        "lease": name, "job_id": job.id, "status": job.status, "verdict": report.get("verdict"),
        **score(report.get("findings", []), name),
        "red_lease_findings": [f["title"] for f in report.get("findings", [])
                               if f["category"] == "lease" and f["severity"] == "red"],
        "cost_usd": (job.result or {}).get("usage", {}).get("totals", {}).get("cost_usd"),
        "seconds": (job.result or {}).get("seconds"),
    }


async def main() -> None:
    results = await asyncio.gather(*(evaluate(n) for n in ANSWER_KEY))
    for r in results:
        print(json.dumps(r, indent=2))
    LOG_DIR.mkdir(exist_ok=True)
    (LOG_DIR / "eval_leases.json").write_text(json.dumps(results, indent=2), encoding="utf-8")
    print("Saved to logs/eval_leases.json")


if __name__ == "__main__":
    asyncio.run(main())
