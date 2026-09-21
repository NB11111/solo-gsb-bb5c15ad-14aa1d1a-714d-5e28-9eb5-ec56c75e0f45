"""Pydantic request/response schemas for the identifiability audit API."""

from __future__ import annotations

from pydantic import BaseModel, Field


class AllowedPair(BaseModel):
    reviewer_id: str = Field(..., min_length=1)
    manuscript_id: str = Field(..., min_length=1)


class AuditRequest(BaseModel):
    reviewers: list[str] = Field(..., min_length=1)
    manuscripts: list[str] = Field(..., min_length=1)
    allowed_pairs: list[AllowedPair] = Field(..., min_length=1)


class AssignmentPair(BaseModel):
    reviewer_id: str
    manuscript_id: str
    forced: bool
    alternative: list[AllowedPair] | None = None


class AuditResponse(BaseModel):
    perfect_match: bool = True
    assignment: list[AssignmentPair]


class ConflictResponse(BaseModel):
    perfect_match: bool = False
    detail: str
    maximum_matching: list[AllowedPair]
    unmatched_reviewer_ids: list[str]
    unmatched_manuscript_ids: list[str]
