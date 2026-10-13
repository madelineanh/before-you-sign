"""FastAPI web server: serves the page and runs checks in the background."""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from .config import MAX_LEASE_BYTES, SAMPLES_DIR, STATIC_DIR, api_key
from .lease import LeaseError, extract_lease_text
from .runner import JOBS, new_job, run_check

MAX_RUNNING_CHECKS = 3

SAMPLE_LEASES = {
    "standard": ("lease_standard.pdf", "A fair, standard lease"),
    "red_flags": ("lease_red_flags.pdf", "A lease with illegal clauses"),
    "injection": ("lease_hidden_instructions.pdf", "A lease with hidden AI instructions"),
}


@asynccontextmanager
async def lifespan(_app: FastAPI):
    api_key()  # fail fast with a clear message if PORTKEY_API_KEY is missing
    yield


app = FastAPI(title="Before You Sign", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
_tasks: set[asyncio.Task] = set()


@app.get("/")
async def index() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/api/samples")
async def samples() -> list[dict[str, str]]:
    return [{"id": k, "file": f, "label": label} for k, (f, label) in SAMPLE_LEASES.items()]


@app.get("/samples/{sample_id}")
async def sample_file(sample_id: str) -> FileResponse:
    if sample_id not in SAMPLE_LEASES:
        raise HTTPException(404, "No such sample")
    return FileResponse(SAMPLES_DIR / SAMPLE_LEASES[sample_id][0], media_type="application/pdf")


@app.post("/api/check")
async def start_check(
    address: str = Form(...),
    sample_lease: str = Form(""),
    lease: UploadFile | None = File(None),
) -> dict[str, str]:
    address = address.strip()
    if not 5 <= len(address) <= 200:
        raise HTTPException(400, "Please enter a street address, like 31 Chapel St, New Haven, CT.")
    if sum(j.status == "running" for j in JOBS.values()) >= MAX_RUNNING_CHECKS:
        raise HTTPException(429, "A few checks are already running. Please try again in a minute.")

    lease_text = None
    try:
        if lease is not None and lease.filename:
            content = await lease.read(MAX_LEASE_BYTES + 1)
            if len(content) > MAX_LEASE_BYTES:
                raise LeaseError("That file is larger than 5 MB.")
            lease_text = extract_lease_text(lease.filename, content)
        elif sample_lease:
            if sample_lease not in SAMPLE_LEASES:
                raise LeaseError("Unknown sample lease.")
            path = SAMPLES_DIR / SAMPLE_LEASES[sample_lease][0]
            lease_text = extract_lease_text(path.name, path.read_bytes())
    except LeaseError as exc:
        raise HTTPException(400, str(exc)) from exc

    job = new_job(address, has_lease=lease_text is not None)
    task = asyncio.create_task(run_check(job, address, lease_text))
    _tasks.add(task)
    task.add_done_callback(_tasks.discard)
    return {"job_id": job.id}


@app.get("/api/jobs/{job_id}")
async def job_status(job_id: str, since: int = 0) -> dict:
    job = JOBS.get(job_id)
    if job is None:
        raise HTTPException(404, "Unknown check")
    return {
        "status": job.status,
        "error": job.error,
        "events": job.events[since:],
        "next": len(job.events),
        "result": job.result if job.status != "running" else None,
    }
