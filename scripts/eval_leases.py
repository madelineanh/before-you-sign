"""Accuracy check: run the full app on each sample lease and compare with an answer key.

    python scripts/eval_leases.py

Each sample lease has known problems (planted on purpose). This counts how many the
Lease Reviewer caught as "red" and whether it raised false alarms on the fair lease.
Results are saved to logs/eval_leases.json. Costs about $0.01.
"""

import asyncio
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
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


async def evaluate(name: str) -> dict:
    p = ROOT / "samples" / name
    job = new_job(ADDRESS, has_lease=True)
    await run_check(job, ADDRESS, extract_lease_text(name, p.read_bytes()))
    report = (job.result or {}).get("report", {})
    red_lease = [f for f in report.get("findings", []) if f["category"] == "lease" and f["severity"] == "red"]
    cited = set().union(*[set(f["evidence_ids"]) for f in red_lease]) if red_lease else set()
    key = ANSWER_KEY[name]
    # An issue counts as caught when some red lease finding cites one of its rules.
    caught = {issue: bool(rules & cited) for issue, rules in key.items()}
    return {
        "lease": name, "job_id": job.id, "status": job.status, "verdict": report.get("verdict"),
        "planted_issues": len(key), "caught": sum(caught.values()), "missed": [k for k, v in caught.items() if not v],
        "red_lease_findings": [f["title"] for f in red_lease],
        "false_alarm_red_findings_on_fair_lease": [f["title"] for f in red_lease] if not key else None,
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
