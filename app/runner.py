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
from .agents import LEAD, CheckState, lead_agent, run_agent
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
    if evidence_id == "LEASE":
        return {"withheld": "lease text is not saved", "characters": len(data),
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


async def run_check(job: Job, address: str, lease_text: str | None) -> None:
    state: CheckState | None = None
    try:
        job.emit("status", message="Finding the address")
        geo = await sources.geocode(normalize_address(address))
        if geo is None:
            raise UserFacingError("We couldn't find that address. Try the street number, street name, and 'New Haven, CT'.")
        if geo["city"].upper() != "NEW HAVEN" or geo["state"] != "CT":
            job.emit("safety", type="scope_block", agent="Intake",
                     detail=f"Address resolved to {geo['matched_address']}, outside New Haven. No AI calls were made.")
            raise UserFacingError(
                f"That address matched {geo['matched_address']}. Before You Sign currently covers New Haven, CT only."
            )
        job.emit("status", message=f"Matched {geo['matched_address']}")

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
            "Please run the full check and write the renter's report."
        )
        report = await asyncio.wait_for(run_agent(lead_agent, LEAD, prompt, state, request_limit=8),
                                        timeout=CHECK_TIMEOUT_SECONDS)

        used_ids = {i for f in report.findings for i in f.evidence_ids}
        job.result = {
            "address": geo,
            "report": report.model_dump(),
            "registry_links": sources.REGISTRY_LINKS,
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


def normalize_address(address: str) -> str:
    """Add ", New Haven, CT" when the renter typed only a street address."""
    address = " ".join(address.split())
    upper = address.upper()
    if re.search(r"(,|HAVEN)\s*CT\b|CONNECTICUT", upper):
        return address
    if "NEW HAVEN" in upper:
        return f"{address}, CT"
    return f"{address}, New Haven, CT"
