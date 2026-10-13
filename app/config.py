"""Settings, API key loading, model setup, and token prices.

The .env file is read from this repo's folder first, then from the folder above it
(the teaching staff keep their .env one level above the cloned repo).
"""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path

from dotenv import load_dotenv

os.environ.setdefault("PYDANTIC_AI_NO_BANNER", "1")

REPO_ROOT = Path(__file__).resolve().parents[1]

# Repo folder first, then the folder above. load_dotenv never overrides a value
# that is already set, so the repo's own .env wins if both exist.
for env_file in (REPO_ROOT / ".env", REPO_ROOT.parent / ".env"):
    if env_file.exists():
        load_dotenv(env_file, override=False)

MODEL_NAME = "gpt-6-luna"
PORTKEY_BASE_URL = "https://api.portkey.ai/v1"

# USD per 1M tokens for gpt-6-luna (course pricing table, Lecture 11 pricing.py,
# OpenAI standard tier, checked Oct 2026).
PRICES = {"input": 0.10, "cached_input": 0.01, "cache_write": 0.125, "output": 0.50}

LOG_DIR = REPO_ROOT / "logs"
CACHE_DIR = REPO_ROOT / ".cache"
DATA_DIR = REPO_ROOT / "data"
SAMPLES_DIR = REPO_ROOT / "samples"
STATIC_DIR = REPO_ROOT / "static"

# Guardrails on spend and runaway loops (shared across all agents in one check).
MAX_REQUESTS_PER_CHECK = 30
MAX_TOOL_CALLS_PER_CHECK = 40
MAX_TOKENS_PER_CHECK = 250_000
MAX_LEASE_CHARS = 60_000  # roughly 20 pages of lease text
MAX_LEASE_BYTES = 5_000_000

# Set SAFETY_FILTERS=off only to demonstrate what happens without the defenses.
SAFETY_FILTERS_ON = os.getenv("SAFETY_FILTERS", "on").lower() != "off"


def api_key() -> str:
    key = os.getenv("PORTKEY_API_KEY", "").strip()
    if not key:
        raise RuntimeError(
            "PORTKEY_API_KEY is missing. Put it in a .env file in this folder or the folder above it."
        )
    return key


@lru_cache(maxsize=1)
def model():
    from openai import AsyncOpenAI
    from pydantic_ai.models.openai import OpenAIResponsesModel
    from pydantic_ai.providers.openai import OpenAIProvider

    client = AsyncOpenAI(
        api_key=api_key(),
        base_url=PORTKEY_BASE_URL,
        default_headers={"x-portkey-provider": "openai"},
        timeout=120,
        max_retries=2,
    )
    return OpenAIResponsesModel(MODEL_NAME, provider=OpenAIProvider(openai_client=client))


def cost_usd(usage: dict) -> float:
    """Dollar cost of one usage dict. input_tokens already include cached and cache-write tokens."""
    cached = usage.get("cache_read_tokens", 0) or 0
    written = usage.get("cache_write_tokens", 0) or 0
    fresh = max((usage.get("input_tokens", 0) or 0) - cached - written, 0)
    out = usage.get("output_tokens", 0) or 0
    return (
        fresh * PRICES["input"]
        + cached * PRICES["cached_input"]
        + written * PRICES["cache_write"]
        + out * PRICES["output"]
    ) / 1_000_000
