"""Reading an uploaded lease (PDF or plain text) and the Connecticut law reference."""

from __future__ import annotations

import io
import json
from functools import lru_cache

from pypdf import PdfReader

from .config import DATA_DIR, MAX_LEASE_CHARS


class LeaseError(ValueError):
    pass


def extract_lease_text(filename: str, content: bytes) -> str:
    name = (filename or "").lower()
    if name.endswith(".pdf") or content[:5] == b"%PDF-":
        try:
            reader = PdfReader(io.BytesIO(content))
            text = "\n".join((page.extract_text() or "") for page in reader.pages)
        except Exception as exc:  # pypdf raises many exception types for bad files
            raise LeaseError(f"Could not read that PDF ({exc.__class__.__name__}).") from exc
    elif name.endswith((".txt", ".md")):
        text = content.decode("utf-8", errors="replace")
    else:
        raise LeaseError("Please upload the lease as a PDF or .txt file.")
    text = text.strip()
    if len(text) < 200:
        raise LeaseError(
            "That file has almost no readable text. If it is a scanned image, please upload a text-based PDF."
        )
    if len(text) > MAX_LEASE_CHARS:
        raise LeaseError(
            f"That lease is too long to review (about {len(text) // 3000} pages; the limit is about "
            f"{MAX_LEASE_CHARS // 3000} pages, or {MAX_LEASE_CHARS:,} characters)."
        )
    return text


@lru_cache(maxsize=1)
def ct_law() -> dict:
    return json.loads((DATA_DIR / "ct_tenant_law.json").read_text(encoding="utf-8"))


def ct_law_for_prompt() -> str:
    law = ct_law()
    lines = [law["source_note"], ""]
    for r in law["rules"]:
        lines.append(f"[{r['id']}] {r['citation']} - {r['topic']}: {r['rule']}")
    return "\n".join(lines)
