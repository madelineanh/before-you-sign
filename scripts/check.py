"""Run one check from the command line (no browser) and print the report and token usage.

    python scripts/check.py "31 Chapel St, New Haven, CT" --lease samples/lease_red_flags.pdf
"""

import argparse
import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.lease import extract_lease_text  # noqa: E402
from app.runner import new_job, run_check  # noqa: E402


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("address")
    ap.add_argument("--lease", help="path to a lease PDF or .txt")
    ap.add_argument("--events", action="store_true", help="print every agent step")
    args = ap.parse_args()

    lease_text = None
    if args.lease:
        p = Path(args.lease)
        lease_text = extract_lease_text(p.name, p.read_bytes())
    job = new_job(args.address, has_lease=lease_text is not None)
    await run_check(job, args.address, lease_text)

    if args.events:
        for e in job.events:
            print(json.dumps(e, default=str)[:400])
    print("\nSTATUS:", job.status, job.error or "")
    if job.result and "report" in job.result:
        print(json.dumps(job.result["report"], indent=2))
    if job.result:
        print("\nSAFETY EVENTS:", json.dumps(job.result.get("safety_events"), indent=2))
        print("\nUSAGE:", json.dumps(job.result.get("usage"), indent=2))
    print(f"\nTrace saved to logs/traces/{job.id}.json")


if __name__ == "__main__":
    asyncio.run(main())
