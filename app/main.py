"""FastAPI web server: serves the page and runs checks in the background."""

from __future__ import annotations

import asyncio
import json
import re
from datetime import datetime, timezone
from contextlib import asynccontextmanager

from fastapi import Body, FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from .coach import MAX_MESSAGE_CHARS, MAX_TURNS, PERSONAS, draft_letter, practice_turn
from .config import LOG_DIR, MAX_LEASE_BYTES, REPO_ROOT, SAMPLES_DIR, STATIC_DIR, api_key
from .lease import LeaseError, extract_lease_text
from .runner import JOBS, LANGUAGES, MAX_LISTING_CHARS, Job, new_job, run_check, run_compare, run_listing_check

MAX_RUNNING_CHECKS = 3
DOCS_DIR = REPO_ROOT / "docs"

SAMPLE_LEASES = {
    "standard": ("lease_standard.pdf", "A fair, standard lease"),
    "red_flags": ("lease_red_flags.pdf", "A lease with illegal clauses"),
    "injection": ("lease_hidden_instructions.pdf", "A lease with hidden AI instructions"),
}


@asynccontextmanager
async def lifespan(_app: FastAPI):
    api_key()  # fail fast with a clear message if PORTKEY_API_KEY is missing
    _load_sample_report()
    yield


SAMPLE_REPORT = DOCS_DIR / "data" / "traced_run.json"


def _load_sample_report() -> None:
    """Register a real, previously run report as job "sample" so first-time visitors (and graders) can open a
    full report, and try the negotiation coach on it, without waiting for a new check."""
    if not SAMPLE_REPORT.exists():
        return
    trace = json.loads(SAMPLE_REPORT.read_text(encoding="utf-8"))
    result = {**trace["result"], "language": trace["result"].get("language", "en"),
              "has_lease": trace["result"].get("has_lease", trace.get("has_lease", True)),
              "sample": {"job_id": trace["job_id"], "when": trace["when"][:10]}}
    JOBS["sample"] = Job(id="sample", address_input=trace["address_input"], has_lease=result["has_lease"],
                         status="done", result=result)


app = FastAPI(title="Before You Sign", lifespan=lifespan)


@app.middleware("http")
async def no_stale_pages(request, call_next):
    # Make browsers re-check the page and its scripts after an update instead of using an old cached copy.
    response = await call_next(request)
    if request.url.path == "/" or request.url.path.startswith(("/static/", "/study")):
        response.headers["Cache-Control"] = "no-cache"
    return response
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
_tasks: set[asyncio.Task] = set()


@app.get("/")
async def index() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/study")
async def study_page() -> FileResponse:
    return FileResponse(STATIC_DIR / "study.html")


@app.get("/study/results")
async def study_results_page() -> FileResponse:
    return FileResponse(STATIC_DIR / "study_results.html")


# ------------------------------------------------------------------ user study
# Results stay on this computer (logs/ is never committed). No names: participant IDs only.

STUDY_FILE = LOG_DIR / "study" / "results.jsonl"
STUDY_ITEMS = {"owner", "llc_people", "portfolio", "complaints", "evictions", "flood", "lease_issues", "questions"}
STUDY_FEATURES = {"compare", "language", "letter", "practice", "scam", "org"}


def _rating(v, lo: int, hi: int):
    if v in (None, ""):
        return None
    v = int(v)
    if not lo <= v <= hi:
        raise ValueError
    return v


@app.post("/api/study")
async def save_study(payload: dict = Body(...)) -> dict:
    try:
        pid = str(payload.get("participant_id", "")).strip()
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,10}", pid):
            raise HTTPException(400, "Use a short participant ID like P1 (letters, numbers, - or _).")
        rec = {
            "participant_id": pid,
            "saved_at": datetime.now(timezone.utc).isoformat(),
            "role": str(payload.get("role", ""))[:60],
            "lease": payload.get("lease") if payload.get("lease") in ("none", "own", "sample") else "none",
            "address": str(payload.get("address", ""))[:200],
            "manual_seconds": max(0, min(int(payload.get("manual_seconds") or 0), 4 * 3600)),
            "app_seconds": max(0, min(int(payload.get("app_seconds") or 0), 4 * 3600)),
            "found_manual": sorted(set(payload.get("found_manual") or []) & STUDY_ITEMS),
            "found_app": sorted(set(payload.get("found_app") or []) & STUDY_ITEMS),
            "features": sorted(set(payload.get("features") or []) & STUDY_FEATURES),
            "useful": _rating(payload.get("useful"), 1, 5),
            "trust": _rating(payload.get("trust"), 1, 5),
            "recommend": _rating(payload.get("recommend"), 0, 10),
            "would_use": payload.get("would_use") if payload.get("would_use") in ("Yes", "Maybe", "No") else None,
        }
        for k in ("notes_manual", "caught", "quote", "change"):
            rec[k] = str(payload.get(k, ""))[:1000]
    except (TypeError, ValueError):
        raise HTTPException(400, "Some answers were not in the expected format.") from None
    STUDY_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(STUDY_FILE, "a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    return {"ok": True, "participant_id": pid}


@app.get("/api/study")
async def list_study(download: int = 0):
    latest: dict[str, dict] = {}
    if STUDY_FILE.exists():
        for line in STUDY_FILE.read_text(encoding="utf-8").splitlines():
            if line.strip():
                rec = json.loads(line)
                latest[rec["participant_id"]] = rec  # a re-saved session replaces the earlier one
    sessions = sorted(latest.values(), key=lambda r: r["saved_at"])
    headers = {"Content-Disposition": "attachment; filename=study_results.json"} if download else None
    return JSONResponse({"sessions": sessions}, headers=headers)


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
    language: str = Form("en"),
    lease: UploadFile | None = File(None),
) -> dict[str, str]:
    address = address.strip()
    if not 5 <= len(address) <= 200:
        raise HTTPException(400, "Please enter a street address and town, like 31 Chapel St, New Haven, CT.")
    if sum(j.status == "running" for j in JOBS.values()) >= MAX_RUNNING_CHECKS:
        raise HTTPException(429, "A few checks are already running. Please try again in a minute.")

    lease_text = None
    try:
        if lease is not None and lease.filename:
            content = await lease.read(MAX_LEASE_BYTES + 1)
            if len(content) > MAX_LEASE_BYTES:
                raise LeaseError(f"That file is larger than {MAX_LEASE_BYTES // 1_000_000} MB.")
            lease_text = extract_lease_text(lease.filename, content)
        elif sample_lease:
            if sample_lease not in SAMPLE_LEASES:
                raise LeaseError("Unknown sample lease.")
            path = SAMPLES_DIR / SAMPLE_LEASES[sample_lease][0]
            lease_text = extract_lease_text(path.name, path.read_bytes())
    except LeaseError as exc:
        raise HTTPException(400, str(exc)) from exc

    job = new_job(address, has_lease=lease_text is not None)
    if language not in LANGUAGES:
        raise HTTPException(400, "Unsupported report language.")
    task = asyncio.create_task(run_check(job, address, lease_text, language))
    _tasks.add(task)
    task.add_done_callback(_tasks.discard)
    return {"job_id": job.id}


SAMPLE_LISTINGS = {"scam": "listing_scam.txt", "normal": "listing_normal.txt"}


@app.get("/api/sample-listing/{sample_id}")
async def sample_listing(sample_id: str) -> dict[str, str]:
    if sample_id not in SAMPLE_LISTINGS:
        raise HTTPException(404, "No such sample")
    return {"text": (SAMPLES_DIR / SAMPLE_LISTINGS[sample_id]).read_text(encoding="utf-8")}


@app.get("/org")
async def org_page() -> FileResponse:
    return FileResponse(STATIC_DIR / "org.html")


@app.post("/api/org")
async def start_org(payload: dict = Body(...)) -> dict[str, str]:
    from .org import MAX_ADDRESSES, run_org

    raw = payload.get("addresses") or []
    if not isinstance(raw, list):
        raise HTTPException(400, "Send a list of addresses.")
    addresses = [str(a).strip()[:200] for a in raw if str(a).strip()]
    if not 2 <= len(addresses) <= MAX_ADDRESSES:
        raise HTTPException(400, f"Enter between 2 and {MAX_ADDRESSES} addresses, one per line.")
    language = payload.get("language", "en")
    if language not in LANGUAGES:
        raise HTTPException(400, "Unsupported report language.")
    if sum(j.status == "running" for j in JOBS.values()) >= MAX_RUNNING_CHECKS:
        raise HTTPException(429, "A few checks are already running. Please try again in a minute.")
    job = new_job(f"[org batch of {len(addresses)}]", has_lease=False)
    task = asyncio.create_task(run_org(job, addresses, LANGUAGES[language], language))
    _tasks.add(task)
    task.add_done_callback(_tasks.discard)
    return {"job_id": job.id}


@app.post("/api/listing")
async def start_listing(listing_text: str = Form(...), language: str = Form("en")) -> dict[str, str]:
    text = listing_text.strip()
    if len(text) < 40:
        raise HTTPException(400, "Paste the full listing text (title, description and contact details).")
    if len(text) > MAX_LISTING_CHARS:
        raise HTTPException(400, f"That listing is too long (limit {MAX_LISTING_CHARS:,} characters).")
    if language not in LANGUAGES:
        raise HTTPException(400, "Unsupported report language.")
    if sum(j.status == "running" for j in JOBS.values()) >= MAX_RUNNING_CHECKS:
        raise HTTPException(429, "A few checks are already running. Please try again in a minute.")
    job = new_job("[listing]", has_lease=False)
    task = asyncio.create_task(run_listing_check(job, text, language))
    _tasks.add(task)
    task.add_done_callback(_tasks.discard)
    return {"job_id": job.id}


@app.post("/api/compare")
async def start_compare(
    address: list[str] = Form(...),
    rent: list[str] = Form(default=[]),
    language: str = Form("en"),
) -> dict[str, str]:
    items = []
    for i, a in enumerate(address):
        a = a.strip()
        if not a:
            continue
        if not 5 <= len(a) <= 200:
            raise HTTPException(400, f"Address {i + 1} doesn't look like a street address.")
        raw = (rent[i] if i < len(rent) else "").replace("$", "").replace(",", "").strip()
        try:
            r = round(float(raw)) if raw else None
        except ValueError:
            raise HTTPException(400, f"Rent {i + 1} should be a number, like 1800.") from None
        if r is not None and not 100 <= r <= 50_000:
            raise HTTPException(400, f"Rent {i + 1} should be a monthly amount between $100 and $50,000.")
        items.append({"address": a, "rent": r})
    if not 2 <= len(items) <= 5:
        raise HTTPException(400, "Enter between 2 and 5 addresses to compare.")
    if language not in LANGUAGES:
        raise HTTPException(400, "Unsupported report language.")
    if sum(j.status == "running" for j in JOBS.values()) >= MAX_RUNNING_CHECKS:
        raise HTTPException(429, "A few checks are already running. Please try again in a minute.")
    job = new_job(" | ".join(i["address"] for i in items), has_lease=False)
    task = asyncio.create_task(run_compare(job, items, language))
    _tasks.add(task)
    task.add_done_callback(_tasks.discard)
    return {"job_id": job.id}


def _finished_report(job_id: str) -> dict:
    job = JOBS.get(job_id)
    if job is None or job.status != "done" or not job.result or "report" not in job.result:
        raise HTTPException(404, "That report isn't available. Run a verification first.")
    return job.result


@app.post("/api/jobs/{job_id}/letter")
async def letter(job_id: str) -> dict:
    result = _finished_report(job_id)
    try:
        out, usage = await asyncio.wait_for(draft_letter(result), timeout=120)
    except Exception as exc:  # keep the message friendly; details stay server-side
        raise HTTPException(502, f"The letter could not be drafted ({exc.__class__.__name__}). Please try again.") from exc
    return {"subject": out.subject, "body": out.body, "usage": usage}


@app.post("/api/jobs/{job_id}/practice")
async def practice(job_id: str, payload: dict = Body(...)) -> dict:
    result = _finished_report(job_id)
    persona = payload.get("persona", "reasonable")
    message = str(payload.get("message", "")).strip()
    history = payload.get("history") or []
    if persona not in PERSONAS:
        raise HTTPException(400, "Unknown landlord persona.")
    if not message or len(message) > MAX_MESSAGE_CHARS:
        raise HTTPException(400, f"Please write a message of up to {MAX_MESSAGE_CHARS} characters.")
    if not isinstance(history, list) or len(history) >= 2 * MAX_TURNS:
        raise HTTPException(400, f"This practice session has reached its {MAX_TURNS}-turn limit. Start a new one.")
    clean = []
    for h in history:
        if not isinstance(h, dict) or h.get("role") not in ("tenant", "landlord"):
            raise HTTPException(400, "Invalid conversation history.")
        clean.append({"role": h["role"], "text": str(h.get("text", ""))[:MAX_MESSAGE_CHARS]})
    lang = LANGUAGES.get(result.get("language", "en"), "English")
    try:
        return await asyncio.wait_for(practice_turn(result, persona, clean, message, lang), timeout=120)
    except Exception as exc:
        raise HTTPException(502, f"The practice landlord didn't respond ({exc.__class__.__name__}). Please try again.") from exc


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
