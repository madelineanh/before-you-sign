"""Safety defenses that run in code, outside the model, so a prompt cannot switch them off.

1. Prompt-injection filter: removes text aimed at an AI system from untrusted input
   (uploaded leases, resident-written 311 descriptions) before any agent reads it.
2. Evidence ledger + fact check: every finding must cite evidence IDs that real tool
   calls produced, every number in a finding must appear in that evidence, and every
   quoted clause must appear word-for-word in the lease.
3. Scope checks (New Haven only, company names only for registry search) live in the
   tools and the runner.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any

from .config import SAFETY_FILTERS_ON

# ------------------------------------------------------- prompt-injection filter

INJECTION_PATTERNS = [
    r"\b(ignore|disregard|forget|override)\b[^.\n]{0,40}\b(instructions?|prompts?|system message|your (rules|guidelines|task))\b",
    r"\b(ignore|disregard|forget)\b\s+(all|any)?\s*(previous|prior|earlier|above)\b",
    r"\b(ai|a\.i\.|llm|language model|chatbot|gpt|claude)s?\b[^.\n]{0,60}\b(must|should|shall|will|need to|are instructed|is instructed)\b",
    r"\b(note|message|instruction)s? (to|for) (the |any )?(ai|assistant|model|reviewer bot|llm|chatbot)\b",
    r"\b(system|developer)\s*(prompt|message)\b",
    r"^\s*(system|assistant|developer)\s*:",
    r"\b(report|mark|classify|label|rate|describe)\b[^.\n]{0,40}\b(as|is)\b[^.\n]{0,20}\b(safe|compliant|fully legal|no issues|green)\b",
    r"\b(do not|don't|never)\s+(flag|mention|disclose|warn about|highlight)\b[^.\n]{0,40}\b(clause|section|fee|provision|paragraph)",
    r"\b(tell|assure|inform|advise)\s+(the\s+)?(tenant|user|renter|reader)\b[^.\n]{0,40}\b(safe|fine|compliant|legal|sign)\b",
    r"\bjailbreak\b|\bprompt injection\b",
]
_INJECTION_RE = re.compile("|".join(f"(?:{p})" for p in INJECTION_PATTERNS), re.I | re.M)
_INVISIBLE_RE = re.compile("[​‌‍⁠﻿­]")
REMOVED_MARKER = "[REMOVED BY SAFETY FILTER: text addressed to an AI system]"


@dataclass
class FilterResult:
    text: str
    flagged: list[str] = field(default_factory=list)
    invisible_chars_removed: int = 0


def filter_untrusted(text: str) -> FilterResult:
    """Strip invisible characters and remove sentences that try to instruct an AI."""
    if not SAFETY_FILTERS_ON:
        return FilterResult(text=text)
    invisible = len(_INVISIBLE_RE.findall(text))
    text = _INVISIBLE_RE.sub("", text)
    flagged: list[str] = []
    out_lines = []
    for line in text.splitlines():
        # Check sentence by sentence so one bad sentence doesn't wipe a whole paragraph.
        pieces = re.split(r"(?<=[.!?])\s+", line)
        hits = [bool(_INJECTION_RE.search(p)) for p in pieces]
        if sum(hits) >= 2:
            # Two or more AI-directed sentences in one paragraph: treat the whole paragraph
            # as an injected block, since its other sentences are part of the same attack.
            flagged.append(line.strip()[:500])
            out_lines.append(REMOVED_MARKER)
            continue
        kept = []
        for piece, hit in zip(pieces, hits):
            if hit:
                flagged.append(piece.strip()[:300])
                kept.append(REMOVED_MARKER)
            else:
                kept.append(piece)
        out_lines.append(" ".join(kept))
    return FilterResult(text="\n".join(out_lines), flagged=flagged, invisible_chars_removed=invisible)


# ------------------------------------------------------------- evidence ledger


@dataclass
class Evidence:
    id: str
    source: str
    url: str | None
    data: Any

    def as_text(self) -> str:
        # The source label is part of the evidence (e.g. "Conn. Gen. Stat. § 47a-4(a)(10)"), so a finding
        # may repeat the citation's numbers.
        body = self.data if isinstance(self.data, str) else json.dumps(self.data, default=str)
        return f"{self.source} {body}"


class Ledger:
    """Everything the tools actually returned during one check, keyed by evidence ID."""

    def __init__(self) -> None:
        self.items: dict[str, Evidence] = {}
        self._n = 0

    def add(self, source: str, data: Any, url: str | None = None, id: str | None = None) -> str:
        if id is None:
            self._n += 1
            id = f"E{self._n}"
        self.items[id] = Evidence(id=id, source=source, url=url, data=data)
        return id

    def get(self, id: str) -> Evidence | None:
        return self.items.get(id)

    def public(self) -> list[dict[str, Any]]:
        return [{"id": e.id, "source": e.source, "url": e.url} for e in self.items.values()]


# ------------------------------------------------------------------ fact check

_NUMBER_RE = re.compile(r"\d[\d,]*(?:\.\d+)?")
_QUOTE_RE = re.compile(r"[\"“]([^\"”]{12,})[\"”]")


def _numbers(text: str) -> set[str]:
    out = set()
    for raw in _NUMBER_RE.findall(text):
        n = raw.replace(",", "").rstrip(".")
        if "." in n:
            n = n.rstrip("0").rstrip(".")
        out.add(n)
    return out


def _squash(text: str) -> str:
    return re.sub(r"\s+", " ", text.replace("’", "'").replace("‘", "'")).strip().lower()


# Numbers that are names, not facts ("311" is New Haven's service-request line).
ALLOWED_NUMBERS = {"311"}


def check_finding(finding: Any, ledger: Ledger, extra_context: str = "") -> list[str]:
    """Return a list of problems with one finding (empty list = passes)."""
    problems: list[str] = []
    ids = list(getattr(finding, "evidence_ids", []) or [])
    if not ids:
        return [f"'{finding.title}' cites no evidence IDs."]
    missing = [i for i in ids if ledger.get(i) is None]
    if missing:
        problems.append(f"'{finding.title}' cites evidence IDs that do not exist: {missing}.")
    cited = [ledger.get(i) for i in ids if ledger.get(i) is not None]
    # The address being checked is known context, so its numbers may always be mentioned.
    haystack = " ".join(e.as_text() for e in cited) + " " + extra_context

    claim = f"{finding.title} {finding.detail}"
    supported = _numbers(haystack) | ALLOWED_NUMBERS
    # Single digits ("2 months", "3 units") are too common to check reliably.
    unsupported = sorted(n for n in _numbers(claim) if len(n.replace(".", "")) >= 2 and n not in supported)
    if unsupported:
        problems.append(
            f"'{finding.title}' uses numbers not found in its cited evidence {ids}: {unsupported}. "
            "Use only numbers that appear in the cited tool results, or cite the right evidence."
        )

    squashed_haystack = _squash(haystack)
    for quote in _QUOTE_RE.findall(claim):
        # A quote may end with the writer's own punctuation ("... Chapter 830.").
        if _squash(quote).rstrip(".,;:!? ") not in squashed_haystack:
            problems.append(
                f"'{finding.title}' quotes text that does not appear word-for-word in its cited evidence: "
                f"\"{quote[:80]}\". Quote exactly or paraphrase without quotation marks."
            )
    return problems
