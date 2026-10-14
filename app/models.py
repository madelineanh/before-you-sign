"""Structured outputs the agents must return."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

Category = Literal["landlord", "building", "neighborhood", "lease", "listing"]
Severity = Literal["red", "yellow", "green"]


class Finding(BaseModel):
    category: Category
    severity: Severity = Field(description="red = serious concern, yellow = ask about it, green = reassuring")
    title: str = Field(description="Short headline, under 12 words")
    detail: str = Field(description="One to three plain-English sentences a renter can act on")
    evidence_ids: list[str] = Field(description="Evidence IDs (e.g. E3, CT-47a-21-b) from tool results that support this finding")


class SpecialistReport(BaseModel):
    summary: str = Field(description="Two to four sentence summary of what you found")
    findings: list[Finding]
    questions_for_landlord: list[str] = Field(default_factory=list)
    gaps: list[str] = Field(default_factory=list, description="What you could not check, and why")


class FinalReport(BaseModel):
    verdict: Literal["Looks good", "Proceed with caution", "Serious concerns"]
    headline: str = Field(description="One sentence bottom line for the renter")
    summary: str = Field(description="A short paragraph tying the findings together")
    findings: list[Finding]
    questions_for_landlord: list[str]
    gaps: list[str] = Field(default_factory=list)


class RankedApartment(BaseModel):
    label: str = Field(description="The apartment's letter label, e.g. A")
    rank: int = Field(description="1 = best choice")
    summary: str = Field(description="One sentence on why it ranks here")
    strengths: list[str] = Field(default_factory=list)
    concerns: list[str] = Field(default_factory=list)


class Comparison(BaseModel):
    ranking: list[RankedApartment]
    recommendation: str = Field(description="Two to four sentences: which to pursue first and what to confirm")
    tradeoffs: str = Field(description="Two to four sentences on the key tradeoffs between the top options")


class ListingReport(BaseModel):
    risk: Literal["Low", "Elevated", "High"] = Field(description="Scam risk of this listing")
    headline: str = Field(description="One sentence bottom line")
    summary: str = Field(description="A short paragraph explaining the risk")
    findings: list[Finding] = Field(description="Red flags (red/yellow) and verified facts (green), each citing evidence")
    next_steps: list[str] = Field(description="3 to 6 concrete steps before sending any money")
    extracted_address: str | None = Field(default=None, description="The property address stated in the listing, if any")
    extracted_rent_usd: float | None = None
