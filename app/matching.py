"""Bipartite matching primitives for the review-assignment identifiability audit.

The graph has two partitions: reviewers and manuscripts. NetworkX keeps a single
node namespace, so reviewer/manuscript IDs are namespaced internally with a
prefix; callers always see the original string IDs.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence

import networkx as nx

R_PREFIX = "reviewer:"
M_PREFIX = "manuscript:"


def r_node(reviewer_id: str) -> str:
    return f"{R_PREFIX}{reviewer_id}"


def m_node(manuscript_id: str) -> str:
    return f"{M_PREFIX}{manuscript_id}"


def build_graph(
    reviewers: Sequence[str],
    manuscripts: Sequence[str],
    pairs: Iterable[tuple[str, str]],
) -> nx.Graph:
    """Build the bipartite graph of allowed reviewer-manuscript assignments."""
    graph = nx.Graph()
    graph.add_nodes_from((r_node(r) for r in reviewers), bipartite=0)
    graph.add_nodes_from((m_node(m) for m in manuscripts), bipartite=1)
    graph.add_edges_from((r_node(r), m_node(m)) for r, m in pairs)
    return graph


def reviewer_nodes(graph: nx.Graph) -> set[str]:
    return {n for n, d in graph.nodes(data=True) if d.get("bipartite") == 0}


def extract_pairs(matching: Mapping[str, str]) -> list[tuple[str, str]]:
    """Convert a NetworkX node->node matching into (reviewer_id, ms_id) pairs."""
    return [
        (u[len(R_PREFIX) :], v[len(M_PREFIX) :])
        for u, v in matching.items()
        if u.startswith(R_PREFIX)
    ]


def matching_size(graph: nx.Graph) -> int:
    """Size of a maximum matching; deterministic regardless of set order."""
    reviewers = reviewer_nodes(graph)
    return (
        len(nx.bipartite.hopcroft_karp_matching(graph, top_nodes=reviewers))
        // 2
    )


def lex_min_matching(graph: nx.Graph) -> list[tuple[str, str]]:
    """Return the deterministically canonical maximum matching.

    Reviewers are processed in lexicographic order. For each reviewer, the
    lexicographically smallest available manuscript is committed iff a maximum
    matching of the same cardinality is still attainable on the residual graph
    (fixed edges plus a maximum residual matching must still reach the global
    maximum size). Hence the result is the maximum matching that is
    lexicographically smallest when pairs are listed sorted by reviewer ID.
    """
    reviewer_ids = sorted(n[len(R_PREFIX) :] for n in reviewer_nodes(graph))
    manuscript_ids = {
        n[len(M_PREFIX) :] for n in graph.nodes if n.startswith(M_PREFIX)
    }
    edges = {
        (u[len(R_PREFIX) :], v[len(M_PREFIX) :])
        for u, v in graph.edges
        if u.startswith(R_PREFIX)
    }
    neighbors: dict[str, list[str]] = {r: [] for r in reviewer_ids}
    for r, m in edges:
        neighbors[r].append(m)
    for adj in neighbors.values():
        adj.sort()

    target = matching_size(graph)
    committed: set[str] = set()
    used_manuscripts: set[str] = set()
    chosen: list[tuple[str, str]] = []

    def feasible(reviewer: str, manuscript: str) -> bool:
        blocked_r = committed | {reviewer}
        blocked_m = used_manuscripts | {manuscript}
        residual = nx.Graph()
        residual.add_nodes_from(
            (r_node(x) for x in reviewer_ids if x not in blocked_r),
            bipartite=0,
        )
        residual.add_nodes_from(
            (m_node(x) for x in manuscript_ids if x not in blocked_m),
            bipartite=1,
        )
        residual.add_edges_from(
            (r_node(a), m_node(b))
            for a, b in edges
            if a not in blocked_r and b not in blocked_m
        )
        # Fixed edges (past + candidate) plus best residual must hit target.
        return len(chosen) + 1 + matching_size(residual) >= target

    for r in reviewer_ids:
        if len(chosen) >= target:
            break
        for m in neighbors[r]:
            if m in used_manuscripts:
                continue
            if feasible(r, m):
                chosen.append((r, m))
                used_manuscripts.add(m)
                committed.add(r)
                break

    return chosen


def has_perfect_matching(graph: nx.Graph) -> bool:
    return matching_size(graph) == len(reviewer_nodes(graph))


def _oriented_sccs(
    graph: nx.Graph, matching_pairs: Sequence[tuple[str, str]]
) -> list[set[str]]:
    """Strongly connected components of the graph oriented around a PM.

    Matched edges point reviewer -> manuscript; the other allowed edges point
    manuscript -> reviewer. An edge of the PM lies on an alternating cycle
    (i.e. is not forced) iff its reviewer and manuscript share an SCC.
    """
    matched = {(r_node(r), m_node(m)) for r, m in matching_pairs}
    digraph = nx.DiGraph()
    digraph.add_nodes_from(graph.nodes)
    for u, v in graph.edges:
        if (u, v) in matched or (v, u) in matched:
            digraph.add_edge(u, v)  # matched: reviewer -> manuscript
        else:
            digraph.add_edge(v, u)  # unmatched: manuscript -> reviewer
    return list(nx.strongly_connected_components(digraph))


def forced_edges(
    graph: nx.Graph, matching_pairs: Sequence[tuple[str, str]]
) -> set[tuple[str, str]]:
    """Edges that belong to every perfect matching, given one perfect matching."""
    scc_of: dict[str, int] = {}
    for i, component in enumerate(_oriented_sccs(graph, matching_pairs)):
        for node in component:
            scc_of[node] = i
    forced: set[tuple[str, str]] = set()
    for r, m in matching_pairs:
        if scc_of[r_node(r)] != scc_of[m_node(m)]:
            forced.add((r, m))
    return forced


def perfect_matching_without(
    graph: nx.Graph, reviewer_id: str, manuscript_id: str
) -> list[tuple[str, str]] | None:
    """Lexicographically smallest perfect matching avoiding one edge, or None."""
    reduced = graph.copy()
    reduced.remove_edge(r_node(reviewer_id), m_node(manuscript_id))
    if not has_perfect_matching(reduced):
        return None
    return lex_min_matching(reduced)
