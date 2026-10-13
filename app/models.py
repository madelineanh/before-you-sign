"""Structured outputs the agents must return."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

Category = Literal["landlord", "building", "neighborhood", "lease"]
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
