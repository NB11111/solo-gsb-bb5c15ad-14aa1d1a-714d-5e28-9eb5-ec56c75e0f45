"""Anonymous review assignment identifiability audit API."""

from __future__ import annotations

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from app import matching
from app.schemas import (
    AllowedPair,
    AssignmentPair,
    AuditRequest,
    AuditResponse,
    ConflictResponse,
)

app = FastAPI(
    title="Anonymous Review Assignment Identifiability Audit API",
    version="1.0.0",
)


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(
    request: Request, exc: RequestValidationError
) -> JSONResponse:
    # Preserve FastAPI's standard 422 envelope: {"detail": [...]}
    return JSONResponse(status_code=422, content={"detail": exc.errors()})


def _err(error_type: str, loc: list[str | int], msg: str, value: object) -> dict:
    return {"type": error_type, "loc": loc, "msg": msg, "input": value}


def semantic_errors(req: AuditRequest) -> list[dict]:
    """Cross-field checks. Each error carries a precise JSON-path `loc`."""
    errors: list[dict] = []

    def duplicates(values: list[str], field: str) -> None:
        seen: set[str] = set()
        for i, value in enumerate(values):
            if value in seen:
                errors.append(
                    _err(
                        "duplicate_id",
                        [field, i],
                        f"Duplicate {field[:-1]} ID: {value!r}",
                        value,
                    )
                )
            seen.add(value)

    duplicates(req.reviewers, "reviewers")
    duplicates(req.manuscripts, "manuscripts")

    if len(req.reviewers) != len(req.manuscripts):
        errors.append(
            _err(
                "count_mismatch",
                ["body"],
                "reviewers and manuscripts must contain an equal number of IDs",
                {
                    "reviewers": len(req.reviewers),
                    "manuscripts": len(req.manuscripts),
                },
            )
        )

    reviewer_set = set(req.reviewers)
    manuscript_set = set(req.manuscripts)
    seen_pairs: set[tuple[str, str]] = set()
    for i, pair in enumerate(req.allowed_pairs):
        if pair.reviewer_id not in reviewer_set:
            errors.append(
                _err(
                    "unknown_reference",
                    ["allowed_pairs", i, "reviewer_id"],
                    f"Unknown reviewer ID: {pair.reviewer_id!r}",
                    pair.reviewer_id,
                )
            )
        if pair.manuscript_id not in manuscript_set:
            errors.append(
                _err(
                    "unknown_reference",
                    ["allowed_pairs", i, "manuscript_id"],
                    f"Unknown manuscript ID: {pair.manuscript_id!r}",
                    pair.manuscript_id,
                )
            )
        key = (pair.reviewer_id, pair.manuscript_id)
        if key in seen_pairs:
            errors.append(
                _err(
                    "duplicate_pair",
                    ["allowed_pairs", i],
                    f"Duplicate allowed pair: {pair.reviewer_id!r} -> "
                    f"{pair.manuscript_id!r}",
                    pair.model_dump(),
                )
            )
        seen_pairs.add(key)

    return errors


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post(
    "/audit",
    response_model=AuditResponse,
    responses={409: {"model": ConflictResponse}},
)
def audit(req: AuditRequest):
    errors = semantic_errors(req)
    if errors:
        raise RequestValidationError(errors)

    pair_tuples = [(p.reviewer_id, p.manuscript_id) for p in req.allowed_pairs]
    graph = matching.build_graph(req.reviewers, req.manuscripts, pair_tuples)

    canonical = matching.lex_min_matching(graph)

    if len(canonical) != len(req.reviewers):
        matched_reviewers = {r for r, _ in canonical}
        matched_manuscripts = {m for _, m in canonical}
        body = ConflictResponse(
            detail="No perfect matching exists for the given allowed pairs.",
            maximum_matching=[
                AllowedPair(reviewer_id=r, manuscript_id=m)
                for r, m in canonical
            ],
            unmatched_reviewer_ids=sorted(
                r for r in req.reviewers if r not in matched_reviewers
            ),
            unmatched_manuscript_ids=sorted(
                m for m in req.manuscripts if m not in matched_manuscripts
            ),
        )
        return JSONResponse(status_code=409, content=body.model_dump())

    forced = matching.forced_edges(graph, canonical)
    assignment: list[AssignmentPair] = []
    for reviewer_id, manuscript_id in canonical:
        is_forced = (reviewer_id, manuscript_id) in forced
        if is_forced:
            assignment.append(
                AssignmentPair(
                    reviewer_id=reviewer_id,
                    manuscript_id=manuscript_id,
                    forced=True,
                    alternative=None,
                )
            )
        else:
            alternative = matching.perfect_matching_without(
                graph, reviewer_id, manuscript_id
            )
            # SCC criterion guarantees a PM without this edge.
            assert alternative is not None
            assignment.append(
                AssignmentPair(
                    reviewer_id=reviewer_id,
                    manuscript_id=manuscript_id,
                    forced=False,
                    alternative=[
                        AllowedPair(reviewer_id=r, manuscript_id=m)
                        for r, m in alternative
                    ],
                )
            )

    return AuditResponse(assignment=assignment)
