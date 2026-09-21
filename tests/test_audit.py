"""Algorithm-level and API-level tests.

Forced-edge results are cross-checked against an independent brute-force
enumeration of every perfect matching.
"""

from __future__ import annotations

from itertools import permutations

import pytest
from fastapi.testclient import TestClient

from app import matching
from app.main import app

client = TestClient(app)


def brute_force_perfect_matchings(
    reviewers: list[str],
    manuscripts: list[str],
    pairs: list[tuple[str, str]],
) -> set[tuple[tuple[str, str], ...]]:
    allowed = set(pairs)
    result: set[tuple[tuple[str, str], ...]] = set()
    for perm in permutations(sorted(manuscripts)):
        candidate = tuple(sorted(zip(sorted(reviewers), perm)))
        if all(pair in allowed for pair in candidate):
            result.add(candidate)
    return result


def assert_audit_matches_bruteforce(
    reviewers, manuscripts, pairs
) -> dict:
    body = {
        "reviewers": reviewers,
        "manuscripts": manuscripts,
        "allowed_pairs": [
            {"reviewer_id": r, "manuscript_id": m} for r, m in pairs
        ],
    }
    resp = client.post("/audit", json=body)
    assert resp.status_code == 200, resp.text
    data = resp.json()

    all_pms = brute_force_perfect_matchings(reviewers, manuscripts, pairs)
    assert all_pms, "test case must admit a perfect matching"

    canonical = tuple(
        sorted(
            (a["reviewer_id"], a["manuscript_id"]) for a in data["assignment"]
        )
    )
    # Canonical assignment must be the lexicographically smallest PM.
    assert canonical == min(all_pms)

    edge_to_item = {
        (a["reviewer_id"], a["manuscript_id"]): a for a in data["assignment"]
    }
    for edge in canonical:
        appears_in = {pm for pm in all_pms if edge in pm}
        item = edge_to_item[edge]
        assert item["forced"] == (appears_in == all_pms)
        if not item["forced"]:
            alt = tuple(
                (p["reviewer_id"], p["manuscript_id"])
                for p in item["alternative"]
            )
            assert alt in all_pms
            assert edge not in alt
        else:
            assert item["alternative"] is None
    return data


def test_unique_matching_all_forced():
    data = assert_audit_matches_bruteforce(
        ["r1", "r2"], ["m1", "m2"],
        [("r1", "m1"), ("r1", "m2"), ("r2", "m2")],
    )
    assert {a["reviewer_id"]: a["forced"] for a in data["assignment"]} == {
        "r1": True,
        "r2": True,
    }


def test_complete_graph_nothing_forced():
    data = assert_audit_matches_bruteforce(
        ["r1", "r2"], ["m1", "m2"],
        [
            ("r1", "m1"), ("r1", "m2"),
            ("r2", "m1"), ("r2", "m2"),
        ],
    )
    assignment = {a["reviewer_id"]: a for a in data["assignment"]}
    assert assignment["r1"]["manuscript_id"] == "m1"
    assert assignment["r2"]["manuscript_id"] == "m2"
    assert all(not a["forced"] for a in data["assignment"])
    # Alternative for r1->m1 must reassign r1 to m2 and be lex-min of the rest.
    alt_r1 = {p["reviewer_id"]: p["manuscript_id"]
              for p in assignment["r1"]["alternative"]}
    assert alt_r1 == {"r1": "m2", "r2": "m1"}


def test_mixed_forced_structure():
    # r1 can only take m1 (forced); r2/r3 are interchangeable over m2/m3.
    pairs = [
        ("r1", "m1"),
        ("r2", "m2"), ("r2", "m3"),
        ("r3", "m2"), ("r3", "m3"),
    ]
    data = assert_audit_matches_bruteforce(
        ["r1", "r2", "r3"], ["m1", "m2", "m3"], pairs
    )
    assignment = {a["reviewer_id"]: a for a in data["assignment"]}
    assert assignment["r1"]["manuscript_id"] == "m1"
    assert assignment["r1"]["forced"] is True
    assert assignment["r2"]["forced"] is False
    assert assignment["r3"]["forced"] is False
    alt_r2 = {p["reviewer_id"]: p["manuscript_id"]
              for p in assignment["r2"]["alternative"]}
    assert alt_r2["r2"] == "m3"
    assert alt_r2["r3"] == "m2"


def test_lex_min_sorted_by_reviewer_not_input_order():
    # Request lists in non-sorted order; response must still be canonical.
    data = assert_audit_matches_bruteforce(
        ["bob", "alice"], ["mB", "mA"],
        [
            ("bob", "mA"), ("bob", "mB"),
            ("alice", "mA"), ("alice", "mB"),
        ],
    )
    ids = [a["reviewer_id"] for a in data["assignment"]]
    assert ids == ["alice", "bob"]
    assert data["assignment"][0]["manuscript_id"] == "mA"


def test_no_perfect_matching_409():
    body = {
        "reviewers": ["r1", "r2"],
        "manuscripts": ["m1", "m2"],
        "allowed_pairs": [
            {"reviewer_id": "r1", "manuscript_id": "m1"},
            {"reviewer_id": "r2", "manuscript_id": "m1"},
        ],
    }
    resp = client.post("/audit", json=body)
    assert resp.status_code == 409
    data = resp.json()
    assert data["perfect_match"] is False
    matched = {(p["reviewer_id"], p["manuscript_id"])
               for p in data["maximum_matching"]}
    assert matched == {("r1", "m1")}
    assert data["unmatched_reviewer_ids"] == ["r2"]
    assert data["unmatched_manuscript_ids"] == ["m2"]
    # Must not carry a partial-success 200 style payload.
    assert "assignment" not in data


def test_409_max_matching_lex_min():
    body = {
        "reviewers": ["r1", "r2", "r3"],
        "manuscripts": ["m1", "m2", "m3"],
        "allowed_pairs": [
            {"reviewer_id": "r1", "manuscript_id": "m1"},
            {"reviewer_id": "r2", "manuscript_id": "m1"},
            {"reviewer_id": "r2", "manuscript_id": "m2"},
            {"reviewer_id": "r3", "manuscript_id": "m1"},
        ],
    }
    resp = client.post("/audit", json=body)
    assert resp.status_code == 409
    data = resp.json()
    max_match = [(p["reviewer_id"], p["manuscript_id"])
                 for p in data["maximum_matching"]]
    assert max_match == [("r1", "m1"), ("r2", "m2")]
    assert data["unmatched_reviewer_ids"] == ["r3"]
    assert data["unmatched_manuscript_ids"] == ["m3"]


def test_duplicate_reviewer_id_422():
    body = {
        "reviewers": ["r1", "r1"],
        "manuscripts": ["m1", "m2"],
        "allowed_pairs": [
            {"reviewer_id": "r1", "manuscript_id": "m1"},
        ],
    }
    resp = client.post("/audit", json=body)
    assert resp.status_code == 422
    locs = [tuple(e["loc"]) for e in resp.json()["detail"]]
    assert ("reviewers", 1) in locs


def test_duplicate_pair_and_unknown_refs_422():
    body = {
        "reviewers": ["r1"],
        "manuscripts": ["m1"],
        "allowed_pairs": [
            {"reviewer_id": "r1", "manuscript_id": "m1"},
            {"reviewer_id": "r1", "manuscript_id": "m1"},
            {"reviewer_id": "ghost", "manuscript_id": "m1"},
            {"reviewer_id": "r1", "manuscript_id": "ghost"},
        ],
    }
    resp = client.post("/audit", json=body)
    assert resp.status_code == 422
    errors = resp.json()["detail"]
    locs = {tuple(e["loc"]): e["type"] for e in errors}
    assert locs[("allowed_pairs", 1)] == "duplicate_pair"
    assert locs[("allowed_pairs", 2, "reviewer_id")] == "unknown_reference"
    assert locs[("allowed_pairs", 3, "manuscript_id")] == "unknown_reference"


def test_count_mismatch_422():
    body = {
        "reviewers": ["r1", "r2"],
        "manuscripts": ["m1"],
        "allowed_pairs": [
            {"reviewer_id": "r1", "manuscript_id": "m1"},
        ],
    }
    resp = client.post("/audit", json=body)
    assert resp.status_code == 422
    assert any(e["type"] == "count_mismatch" for e in resp.json()["detail"])


def test_empty_inputs_422():
    resp = client.post(
        "/audit",
        json={"reviewers": [], "manuscripts": [], "allowed_pairs": []},
    )
    assert resp.status_code == 422


def test_deterministic_repeated_calls():
    body = {
        "reviewers": ["r1", "r2", "r3", "r4"],
        "manuscripts": ["m1", "m2", "m3", "m4"],
        "allowed_pairs": [
            {"reviewer_id": r, "manuscript_id": m}
            for r in ["r1", "r2", "r3", "r4"]
            for m in ["m1", "m2", "m3", "m4"]
        ],
    }
    responses = [client.post("/audit", json=body).json() for _ in range(5)]
    assert all(r == responses[0] for r in responses)
    assignment = {a["reviewer_id"]: a["manuscript_id"]
                  for a in responses[0]["assignment"]}
    assert assignment == {"r1": "m1", "r2": "m2", "r3": "m3", "r4": "m4"}


def test_shared_id_namespaces_do_not_collide():
    # Same string used as both a reviewer ID and a manuscript ID.
    data = assert_audit_matches_bruteforce(
        ["x", "y"], ["x", "y"],
        [("x", "x"), ("x", "y"), ("y", "x"), ("y", "y")],
    )
    assignment = {a["reviewer_id"]: a["manuscript_id"]
                  for a in data["assignment"]}
    assert assignment == {"x": "x", "y": "y"}


def test_health():
    assert client.get("/health").json() == {"status": "ok"}


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))
