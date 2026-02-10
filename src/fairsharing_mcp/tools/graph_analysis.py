"""FAIRsharing MCP tools — Advanced graph analysis algorithms.

Weighted path finding, PageRank, community detection, bipartite projection,
multi-path analysis, betweenness centrality, and strongly connected components.
"""

import asyncio
import heapq
import json
import logging
import random
from collections import Counter, deque
from typing import Annotated

from mcp.types import ToolAnnotations
from pydantic import Field

from fairsharing_mcp import app
from fairsharing_mcp.client import FAIRsharingError
from fairsharing_mcp.constants import RELATIONSHIP_INFLUENCE_WEIGHTS
from fairsharing_mcp.formatters import escape_md_table
from fairsharing_mcp.graph_utils import (
    NodeInfo,
    ParsedGraph,
    edge_weight,
    fetch_and_parse_graph,
    merge_graphs,
    merge_multiple_graphs,
    run_in_thread,
)

logger = logging.getLogger(__name__)

# Scope caveat appended to graph analysis output headers to prevent
# users from interpreting local-neighborhood metrics as platform-wide.
_SCOPE_CAVEAT = (
    "_Scope: This analysis covers **only the local neighborhood graph** of "
    "the seed record (1 API call). Metrics like PageRank and betweenness "
    "reflect local structure, not platform-wide importance._"
)


# ── Helpers ──────────────────────────────────────────────────────────────


def _dijkstra(
    graph: ParsedGraph,
    source: str,
    target: str | None = None,
    max_cost: float = float("inf"),
    excluded_edges: set[tuple[str, str]] | None = None,
) -> tuple[dict[str, float], dict[str, str | None], dict[str, str]]:
    """Dijkstra's shortest path on the undirected graph with semantic weights.

    Args:
        graph: Parsed graph.
        source: Starting node key.
        target: Optional target node key (stops early when reached).
        max_cost: Maximum path cost to explore.
        excluded_edges: Set of (source, target) edge pairs to skip.

    Returns:
        (dist, prev, prev_rel): distance dict, predecessor dict, predecessor relationship dict.
    """
    dist: dict[str, float] = {source: 0.0}
    prev: dict[str, str | None] = {source: None}
    prev_rel: dict[str, str] = {}
    heap: list[tuple[float, str]] = [(0.0, source)]
    visited: set[str] = set()
    excluded = excluded_edges or set()

    while heap:
        d, u = heapq.heappop(heap)
        if u in visited:
            continue
        visited.add(u)

        if u == target:
            break

        if d > max_cost:
            break

        # Check outgoing directed edges (both directions for undirected)
        for neighbor, rel in graph.out_adj.get(u, []):
            if (u, neighbor) in excluded:
                continue
            w = edge_weight(rel)
            nd = d + w
            if nd < dist.get(neighbor, float("inf")):
                dist[neighbor] = nd
                prev[neighbor] = u
                prev_rel[neighbor] = rel
                heapq.heappush(heap, (nd, neighbor))

        for neighbor, rel in graph.in_adj.get(u, []):
            if (neighbor, u) in excluded:
                continue
            w = edge_weight(rel)
            nd = d + w
            if nd < dist.get(neighbor, float("inf")):
                dist[neighbor] = nd
                prev[neighbor] = u
                prev_rel[neighbor] = rel
                heapq.heappush(heap, (nd, neighbor))

    return dist, prev, prev_rel


def _reconstruct_path(
    prev: dict[str, str | None], prev_rel: dict[str, str], target: str
) -> list[tuple[str, str]]:
    """Reconstruct path from Dijkstra results.

    Returns:
        List of (node_key, relationship_to_next) tuples. Last entry has rel="".
    """
    if target not in prev:
        return []

    path: list[str] = []
    rels: list[str] = []
    current: str | None = target
    while current is not None:
        path.append(current)
        if prev.get(current) is not None:
            rels.append(prev_rel.get(current, "?"))
        current = prev.get(current)

    path.reverse()
    rels.reverse()
    rels.append("")  # No relationship after the last node

    return list(zip(path, rels))


# ── Tool 1: Weighted Semantic Path Finding ───────────────────────────────


@app.mcp.tool(
    name="fairsharing_find_semantic_path",
    annotations=ToolAnnotations(
        readOnlyHint=True,
        idempotentHint=True,
        openWorldHint=True,
    ),
)
async def find_semantic_path(
    record_id_1: Annotated[int, Field(ge=1, description="First FAIRsharing record ID")],
    record_id_2: Annotated[int, Field(ge=1, description="Second FAIRsharing record ID")],
    prefer: Annotated[
        str,
        Field(
            default="strongest",
            pattern="^(strongest|shortest)$",
            description="Path preference",
        ),
    ] = "strongest",
    output_format: Annotated[
        str,
        Field(
            default="markdown",
            pattern="^(markdown|json)$",
            description="Output format: 'markdown' or 'json'",
        ),
    ] = "markdown",
) -> str:
    """Find the most semantically meaningful path between two records.

    Single-record neighborhood only: uses record_id_1's local knowledge graph
    (1 API call). Record_id_2 must be in that graph; if not, use
    find_cross_graph_path to merge both records' graphs and search for a path
    (2 API calls).
    If this tool reports "not in the local knowledge graph," use
    find_cross_graph_path instead.

    Recommended workflow:
    1. suggest_graph_starting_points — find records with large, rich graphs.
    2. find_semantic_path — try single-graph path (1 API call).
    3. find_cross_graph_path — fallback if records aren't in the same neighborhood.

    Uses Dijkstra's algorithm with edge weights based on relationship type.
    An 'implements' edge (weight 1.0) is treated as a much stronger connection
    than 'related_to' (weight 4.0).

    See also: find_record_connections for unweighted BFS path finding.

    Args:
        record_id_1: First record ID.
        record_id_2: Second record ID.
        prefer: "strongest" for lowest semantic distance (default), or
                "shortest" for fewest hops with weight as tiebreaker.

    Returns:
        Path visualization with edge types, weights, and total semantic distance.
    """
    try:
        graph = await fetch_and_parse_graph(record_id_1)
        if not graph:
            return f"No graph data available for record ID {record_id_1}."

        key1, key2 = str(record_id_1), str(record_id_2)
        name1 = graph.nodes[key1].label if key1 in graph.nodes else f"Record {record_id_1}"
        name2 = graph.nodes[key2].label if key2 in graph.nodes else f"Record {record_id_2}"

        lines = [f"# Semantic Path: {name1} <-> {name2}", ""]

        if key2 not in graph.nodes:
            lines.append(
                f"Record {record_id_2} is **not in the local knowledge graph** "
                f"of record {record_id_1}."
            )
            lines.append("")
            lines.append(
                "**Tip:** Use `find_cross_graph_path` to search across both records' "
                "graphs by merging their neighborhoods (2 API calls)."
            )
            return "\n".join(lines)

        if prefer == "shortest":
            # BFS for hop count, use weight as tiebreaker
            bfs_dist, bfs_prev, bfs_rel = await run_in_thread(_bfs_weighted, graph, key1, key2)
            path = _reconstruct_path(bfs_prev, bfs_rel, key2)
        else:
            dist, prev, prev_rel = await run_in_thread(_dijkstra, graph, key1, target=key2)
            path = _reconstruct_path(prev, prev_rel, key2)

        if not path:
            lines.append("**No path found** between these records.")
            return "\n".join(lines)

        # Compute total weight
        total_weight = sum(edge_weight(rel) for _, rel in path if rel)
        hop_count = len(path) - 1

        if output_format == "json":
            path_json = []
            for node_key, rel in path:
                node = graph.nodes.get(node_key)
                entry: dict = {
                    "id": node_key,
                    "name": node.label if node else node_key,
                    "registry": node.registry if node else None,
                }
                if rel:
                    entry["relationship"] = rel
                    entry["weight"] = edge_weight(rel)
                path_json.append(entry)
            return json.dumps(
                {
                    "record_id": record_id_1,
                    "source_id": record_id_1,
                    "target_id": record_id_2,
                    "path": path_json,
                    "total_weight": round(total_weight, 2),
                    "hops": hop_count,
                },
                indent=2,
            )

        lines.append(f"**Hops:** {hop_count} | **Total semantic distance:** {total_weight:.1f}")
        lines.append(
            f"**Mode:** {'Fewest hops' if prefer == 'shortest' else 'Strongest semantic connection'}"
        )
        lines.append("")

        # Path visualization
        lines.append("### Path")
        for i, (node_key, rel) in enumerate(path):
            node = graph.nodes.get(node_key)
            label = node.label if node else node_key
            registry = f" ({node.registry})" if node else ""
            lines.append(f"  {'  ' * i}**{label}**{registry} [ID: {node_key}]")
            if rel:
                w = edge_weight(rel)
                lines.append(f"  {'  ' * i}  |-- [{rel}, w={w}] -->")

        # Also compute the alternative to show comparison
        if prefer == "strongest":
            bfs_dist, bfs_prev, bfs_rel = await run_in_thread(_bfs_weighted, graph, key1, key2)
            bfs_path = _reconstruct_path(bfs_prev, bfs_rel, key2)
            if bfs_path and len(bfs_path) != len(path):
                bfs_hops = len(bfs_path) - 1
                bfs_weight = sum(edge_weight(r) for _, r in bfs_path if r)
                lines.append("")
                lines.append(
                    f"_BFS shortest path would be {bfs_hops} hop(s) "
                    f"(semantic distance {bfs_weight:.1f})._"
                )

        return "\n".join(lines)

    except FAIRsharingError as e:
        return f"Error finding semantic path: {e}"


def _bfs_weighted(
    graph: ParsedGraph, source: str, target: str
) -> tuple[dict[str, int], dict[str, str | None], dict[str, str]]:
    """BFS finding shortest hop-count path, recording relationship types."""
    dist: dict[str, int] = {source: 0}
    prev: dict[str, str | None] = {source: None}
    prev_rel: dict[str, str] = {}
    queue: deque[str] = deque([source])

    while queue:
        u = queue.popleft()
        if u == target:
            break
        d = dist[u]
        if d >= 6:
            continue

        neighbors: set[str] = set()
        for neighbor, rel in graph.out_adj.get(u, []):
            if neighbor not in dist:
                neighbors.add(neighbor)
                dist[neighbor] = d + 1
                prev[neighbor] = u
                prev_rel[neighbor] = rel
        for neighbor, rel in graph.in_adj.get(u, []):
            if neighbor not in dist:
                neighbors.add(neighbor)
                dist[neighbor] = d + 1
                prev[neighbor] = u
                prev_rel[neighbor] = rel

        queue.extend(sorted(neighbors))

    return dist, prev, prev_rel


def _compute_pagerank_scores(
    node_keys: list[str],
    out_adj: dict[str, list[tuple[str, str]]],
    damping: float = 0.85,
    iterations: int = 20,
) -> dict[str, float]:
    """Compute weighted PageRank scores using power iteration.

    Pure function suitable for execution in a thread pool.

    Args:
        node_keys: List of all node keys.
        out_adj: Outgoing adjacency dict mapping node -> [(neighbor, rel_type)].
        damping: Damping factor (default: 0.85).
        iterations: Number of power iterations (default: 20).

    Returns:
        Dict mapping node key -> PageRank score.
    """
    n = len(node_keys)
    if n == 0:
        return {}

    pr: dict[str, float] = {k: 1.0 / n for k in node_keys}

    # Precompute weighted out-degree sums for normalization
    out_weight_sum: dict[str, float] = {}
    for k in node_keys:
        total = sum(RELATIONSHIP_INFLUENCE_WEIGHTS.get(rel, 0.2) for _, rel in out_adj.get(k, []))
        out_weight_sum[k] = total if total > 0 else 1.0

    # Power iteration
    for _ in range(iterations):
        new_pr: dict[str, float] = {k: (1.0 - damping) / n for k in node_keys}
        for u in node_keys:
            for v, rel in out_adj.get(u, []):
                if v in new_pr:
                    w = RELATIONSHIP_INFLUENCE_WEIGHTS.get(rel, 0.2)
                    new_pr[v] += damping * pr[u] * w / out_weight_sum[u]
        pr = new_pr

    return pr


# ── Tool 2: Weighted PageRank ────────────────────────────────────────────


@app.mcp.tool(
    name="fairsharing_compute_pagerank",
    annotations=ToolAnnotations(
        readOnlyHint=True,
        idempotentHint=True,
        openWorldHint=True,
    ),
)
async def compute_pagerank(
    record_id: Annotated[int, Field(ge=1, description="FAIRsharing record ID")],
    top_n: Annotated[
        int, Field(default=25, ge=1, le=100, description="Number of top results")
    ] = 25,
    damping: Annotated[
        float, Field(default=0.85, gt=0, lt=1, description="PageRank damping factor")
    ] = 0.85,
    iterations: Annotated[
        int, Field(default=20, ge=1, le=100, description="Number of iterations")
    ] = 20,
    output_format: Annotated[
        str,
        Field(
            default="markdown",
            pattern="^(markdown|json)$",
            description="Output format: 'markdown' or 'json'",
        ),
    ] = "markdown",
) -> str:
    """Compute weighted PageRank to identify truly influential nodes in the graph.

    NOTE: Analyzes a single record's local knowledge graph (1 API call). Use
    suggest_graph_starting_points to find records with the largest graphs.

    Unlike simple degree counting, PageRank accounts for the quality of
    connections. A node linked by many 'implements' edges ranks higher than
    one with the same degree via 'related_to' edges.

    See also: find_graph_hubs for simple degree-based hub ranking.

    Args:
        record_id: Record ID whose graph to analyze.
        top_n: Number of top-ranked nodes to return (default: 25, max: 50).
        damping: PageRank damping factor (default: 0.85).
        iterations: Power iteration count (default: 20, max: 50).

    Returns:
        Ranked table of nodes by PageRank score with comparison to degree ranking.
    """
    top_n = min(max(1, top_n), 50)
    iterations = min(max(1, iterations), 50)

    try:
        graph = await fetch_and_parse_graph(record_id)
        if not graph:
            return f"No graph data available for record ID {record_id}."

        n = len(graph.nodes)
        if n == 0:
            return "Graph has no nodes."

        node_keys = list(graph.nodes.keys())

        pr = await run_in_thread(
            _compute_pagerank_scores, node_keys, graph.out_adj, damping, iterations
        )

        # Compute degree for comparison
        degree: dict[str, int] = {}
        for k in node_keys:
            degree[k] = len(graph.out_adj.get(k, [])) + len(graph.in_adj.get(k, []))

        # Rank by PageRank
        pr_ranked = sorted(pr.items(), key=lambda x: x[1], reverse=True)
        degree_ranked = sorted(degree.items(), key=lambda x: x[1], reverse=True)

        # Build degree rank lookup
        degree_rank = {k: i + 1 for i, (k, _) in enumerate(degree_ranked)}

        if output_format == "json":
            rankings = []
            for key, score in pr_ranked[:top_n]:
                node = graph.nodes.get(key)
                rankings.append(
                    {
                        "id": key,
                        "name": node.label if node else key,
                        "registry": node.registry if node else None,
                        "score": round(score, 6),
                    }
                )
            return json.dumps(
                {
                    "record_id": record_id,
                    "nodes": n,
                    "edges": len(graph.edges),
                    "rankings": rankings,
                },
                indent=2,
            )

        lines = [
            f"# PageRank Analysis: {graph.name}",
            f"**Network:** {n:,} nodes, {len(graph.edges):,} edges",
            f"**Parameters:** damping={damping}, iterations={iterations}",
            "",
            _SCOPE_CAVEAT,
            "",
            f"## Top {min(top_n, n)} Nodes by PageRank",
            "",
            "| PR Rank | Name | ID | Registry | PageRank | Degree | Deg Rank |",
            "|---------|------|----|----------|----------|--------|----------|",
        ]

        for rank, (key, score) in enumerate(pr_ranked[:top_n], 1):
            node = graph.nodes.get(key)
            label = escape_md_table(node.label) if node else key
            reg = node.registry if node else "?"
            deg = degree.get(key, 0)
            dr = degree_rank.get(key, "?")
            lines.append(f"| {rank} | {label} | {key} | {reg} | {score:.4f} | {deg} | {dr} |")

        # Score spread interpretation
        lines.append("")
        lines.append("## Score Interpretation")
        all_scores = [s for _, s in pr_ranked]
        top_score = all_scores[0]
        median_score = all_scores[n // 2] if n > 0 else top_score
        bottom_score = all_scores[-1] if all_scores else top_score
        spread = top_score / median_score if median_score > 0 else 1.0

        if spread > 5.0:
            interpretation = (
                "Strong hierarchy — top nodes are significantly more influential than typical nodes"
            )
        elif spread > 2.0:
            interpretation = "Moderate hierarchy — some nodes are notably more influential"
        else:
            interpretation = (
                "Flat distribution — influence is spread fairly evenly, "
                "likely a dense or uniformly connected graph"
            )

        lines.append(
            f"**Score spread:** top={top_score:.4f}, median={median_score:.4f}, "
            f"bottom={bottom_score:.4f} (top/median ratio: {spread:.1f}x)"
        )
        lines.append(f"**Interpretation:** {interpretation}")

        # Top node edge type breakdown
        if pr_ranked:
            top_key = pr_ranked[0][0]
            top_node = graph.nodes.get(top_key)
            top_label = top_node.label if top_node else top_key
            in_rels = Counter(rel for _, rel in graph.in_adj.get(top_key, []))
            out_rels = Counter(rel for _, rel in graph.out_adj.get(top_key, []))
            all_rels = in_rels + out_rels
            if all_rels:
                rel_parts = [f"{count}x {rel}" for rel, count in all_rels.most_common()]
                lines.append(f"**Why #{top_label} ranks #1:** {', '.join(rel_parts)} connections")

        # Highlight divergences
        lines.append("")
        lines.append("## Notable Divergences (PageRank vs Degree)")
        divergences = []
        for rank, (key, score) in enumerate(pr_ranked[:top_n], 1):
            dr = degree_rank.get(key, rank)
            diff = dr - rank
            if abs(diff) >= 3:
                node = graph.nodes.get(key)
                label = node.label if node else key
                if diff > 0:
                    divergences.append(
                        f"- **{label}**: PR rank #{rank} but degree rank #{dr} "
                        f"— influential through high-quality connections"
                    )
                else:
                    divergences.append(
                        f"- **{label}**: PR rank #{rank} but degree rank #{dr} "
                        f"— many connections but lower-quality edges"
                    )

        if divergences:
            lines.extend(divergences[:10])
        else:
            lines.append("_PageRank and degree rankings largely agree for this graph._")

        return "\n".join(lines)

    except FAIRsharingError as e:
        return f"Error computing PageRank: {e}"


# ── Tool 3: Community Detection ──────────────────────────────────────────


@app.mcp.tool(
    name="fairsharing_detect_communities",
    annotations=ToolAnnotations(
        readOnlyHint=True,
        idempotentHint=True,
        openWorldHint=True,
    ),
)
async def detect_communities(
    record_id: Annotated[int, Field(ge=1, description="FAIRsharing record ID")],
    max_iterations: Annotated[
        int, Field(default=20, ge=1, le=100, description="Maximum iterations")
    ] = 20,
    min_community_size: Annotated[
        int, Field(default=3, ge=1, le=50, description="Minimum community size")
    ] = 3,
    seed: Annotated[
        int | None, Field(default=42, description="Random seed for reproducibility")
    ] = 42,
    output_format: Annotated[
        str,
        Field(
            default="markdown",
            pattern="^(markdown|json)$",
            description="Output format: 'markdown' or 'json'",
        ),
    ] = "markdown",
) -> str:
    """Detect natural communities/clusters in the knowledge graph using label propagation.

    NOTE: Analyzes a single record's local knowledge graph (1 API call). Use
    suggest_graph_starting_points to find records with the largest graphs.

    Identifies groups of standards, databases, and policies that form coherent
    ecosystems (e.g., a 'proteomics cluster' or 'genomics cluster').
    Uses a default seed (42) for reproducible results; pass seed=None for non-deterministic runs.

    Args:
        record_id: Record ID whose graph to analyze.
        max_iterations: Maximum label propagation iterations (default: 20, max: 50).
        min_community_size: Minimum nodes to report a community (default: 3).
        seed: Random seed for reproducible results (default: 42). Pass None for non-deterministic.

    Returns:
        Communities with member lists, registry distribution, and theme labels.
    """
    max_iterations = min(max(1, max_iterations), 50)
    min_community_size = max(1, min_community_size)

    try:
        graph = await fetch_and_parse_graph(record_id)
        if not graph:
            return f"No graph data available for record ID {record_id}."

        node_keys = list(graph.nodes.keys())
        n = len(node_keys)
        if n == 0:
            return "Graph has no nodes."

        labels = await run_in_thread(_label_propagation, graph, max_iterations, seed=seed)

        # Group nodes by label
        communities: dict[str, list[str]] = {}
        for node, label in labels.items():
            communities.setdefault(label, []).append(node)

        # Filter by minimum size and sort by size descending
        significant = sorted(
            [
                (label, members)
                for label, members in communities.items()
                if len(members) >= min_community_size
            ],
            key=lambda x: len(x[1]),
            reverse=True,
        )

        # Compute modularity and edge density
        modularity = await run_in_thread(_compute_modularity, graph, communities)
        max_edges = n * (n - 1) / 2
        edge_density = len(graph.edges) / max_edges if max_edges > 0 else 0.0

        # Detect collapse: single community with >80% of nodes
        largest_frac = len(significant[0][1]) / n if significant else 0.0
        is_collapsed = len(significant) == 1 and largest_frac > 0.8

        if output_format == "json":
            comms_json = []
            for i, (label, members) in enumerate(significant, 1):
                member_list = []
                for m in members:
                    node = graph.nodes.get(m)
                    member_list.append(
                        {
                            "id": m,
                            "name": node.label if node else m,
                            "registry": node.registry if node else None,
                        }
                    )
                comms_json.append(
                    {
                        "id": i,
                        "members": member_list,
                        "size": len(members),
                    }
                )
            return json.dumps(
                {
                    "record_id": record_id,
                    "communities": comms_json,
                    "modularity": round(modularity, 4),
                },
                indent=2,
            )

        lines = [
            f"# Community Detection: {graph.name}",
            f"**Network:** {n:,} nodes, {len(graph.edges):,} edges "
            f"| **Edge density:** {edge_density:.2%}",
            f"**Communities found:** {len(significant)} (min size {min_community_size}) "
            f"| **Modularity Q:** {modularity:.3f}",
            "",
            _SCOPE_CAVEAT,
            "",
        ]

        if modularity < 0.1:
            lines.append(
                "_Modularity Q < 0.1 — weak community structure. "
                "The graph may be too densely interconnected for label propagation "
                "to separate meaningful clusters._"
            )
            lines.append("")

        if is_collapsed:
            pct = largest_frac * 100
            lines.append(
                f"**Community collapse detected:** Label propagation converged to a "
                f"single community containing {len(significant[0][1])} of {n} nodes "
                f"({pct:.0f}%). This typically indicates a densely interconnected graph "
                f"without clear modular boundaries. A resolution-based algorithm "
                f"(e.g., Louvain) might reveal finer-grained structure."
            )
            lines.append("")

        if not significant:
            lines.append("_No communities of sufficient size found._")
            singleton_count = sum(1 for m in communities.values() if len(m) < min_community_size)
            if singleton_count:
                lines.append(f"_({singleton_count} groups below minimum size threshold)_")
            return "\n".join(lines)

        for i, (label, members) in enumerate(significant, 1):
            # Determine registry distribution for theme hint
            reg_dist = Counter(graph.nodes[m].registry for m in members if m in graph.nodes)
            dominant_reg = reg_dist.most_common(1)[0][0] if reg_dist else "mixed"
            theme = f"Primarily {dominant_reg}" if reg_dist else "Mixed"

            lines.append(f"## Community {i} ({len(members)} nodes) — {theme}")
            lines.append("")

            # Registry breakdown
            for reg, count in reg_dist.most_common():
                lines.append(f"- **{reg}:** {count}")
            lines.append("")

            # Member table
            lines.append("| Name | ID | Registry | Type | Status |")
            lines.append("|------|----|----------|------|--------|")
            for m in sorted(
                members, key=lambda k: graph.nodes.get(k, NodeInfo(k, k, "", "", "")).label
            ):
                node = graph.nodes.get(m)
                if node:
                    lines.append(
                        f"| {escape_md_table(node.label)} | {node.key} | {node.registry} | "
                        f"{node.record_type} | {node.status} |"
                    )
            lines.append("")

        # Small groups summary
        small_count = sum(1 for m in communities.values() if len(m) < min_community_size)
        if small_count:
            small_nodes = sum(len(m) for m in communities.values() if len(m) < min_community_size)
            lines.append(
                f"_Plus {small_count} small groups ({small_nodes} nodes) below threshold._"
            )

        return "\n".join(lines)

    except FAIRsharingError as e:
        return f"Error detecting communities: {e}"


# ── Tool 4: Bipartite Projection for Similarity ─────────────────────────


@app.mcp.tool(
    name="fairsharing_find_similar_records",
    annotations=ToolAnnotations(
        readOnlyHint=True,
        idempotentHint=True,
        openWorldHint=True,
    ),
)
async def find_similar_records(
    record_id: Annotated[int, Field(ge=1, description="FAIRsharing record ID")],
    projection_side: Annotated[
        str,
        Field(
            default="auto",
            pattern="^(auto|databases|standards|policies)$",
            description="Projection side for bipartite analysis",
        ),
    ] = "auto",
    top_n: Annotated[
        int, Field(default=15, ge=1, le=100, description="Number of top results")
    ] = 15,
    output_format: Annotated[
        str,
        Field(
            default="markdown",
            pattern="^(markdown|json)$",
            description="Output format: 'markdown' or 'json'",
        ),
    ] = "markdown",
) -> str:
    """Find similar records using bipartite projection of the standard-database network.

    NOTE: Analyzes a single record's local knowledge graph (1 API call). Use
    suggest_graph_starting_points to find records with the largest graphs.

    Two databases that implement many of the same standards are functionally
    similar, even without a direct edge. This projects the bipartite
    standard↔database graph and computes Jaccard similarity.

    See also: suggest_related_resources for peer-based collaborative filtering.

    Args:
        record_id: Record ID to find similar records for.
        projection_side: "database" (find similar databases), "standard" (find similar
                         standards), or "auto" (infer from the record's registry).
        top_n: Number of most-similar records to return (default: 15, max: 30).

    Returns:
        Ranked similarity table with shared connection details.
    """
    top_n = min(max(1, top_n), 30)

    try:
        graph = await fetch_and_parse_graph(record_id)
        if not graph:
            return f"No graph data available for record ID {record_id}."

        key = str(record_id)
        if key not in graph.nodes:
            return f"Record {record_id} not found in graph."

        node = graph.nodes[key]

        # Determine projection side
        if projection_side == "auto":
            projection_side = node.registry.lower()
            if projection_side not in ("database", "standard"):
                return (
                    f"Record {record_id} is a {node.registry}. Bipartite projection "
                    f"works with databases and standards. Specify projection_side explicitly."
                )

        # Extract implements edges (bipartite standard↔database subgraph)
        # An implements edge goes from standard to database (source=std, target=db)
        # based on color "pink"
        db_to_standards: dict[str, set[str]] = {}
        std_to_databases: dict[str, set[str]] = {}

        for s, t, rel in graph.edges:
            if rel != "implements":
                continue
            s_node = graph.nodes.get(s)
            t_node = graph.nodes.get(t)
            if not s_node or not t_node:
                continue

            # Determine which is standard and which is database
            if s_node.registry == "standard" and t_node.registry == "database":
                std_key, db_key = s, t
            elif s_node.registry == "database" and t_node.registry == "standard":
                std_key, db_key = t, s
            else:
                continue

            db_to_standards.setdefault(db_key, set()).add(std_key)
            std_to_databases.setdefault(std_key, set()).add(db_key)

        name = node.label

        if projection_side == "database":
            my_connections = db_to_standards.get(key, set())
            peer_connections = db_to_standards
            connection_label = "standards"
        else:
            my_connections = std_to_databases.get(key, set())
            peer_connections = std_to_databases
            connection_label = "databases"

        if not my_connections:
            return (
                f"# Similar Records: {name}\n\n"
                f"No 'implements' connections found for this {projection_side}. "
                f"Cannot compute similarity."
            )

        # Compute Jaccard similarity with all peers
        similarities: list[tuple[str, int, float, set[str]]] = []
        for peer_key, peer_conns in peer_connections.items():
            if peer_key == key:
                continue
            shared = my_connections & peer_conns
            if not shared:
                continue
            union = my_connections | peer_conns
            jaccard = len(shared) / len(union) if union else 0
            similarities.append((peer_key, len(shared), jaccard, shared))

        similarities.sort(key=lambda x: (-x[2], -x[1]))

        if output_format == "json":
            similar_json = []
            for peer_key, shared_count, jaccard, _ in similarities[:top_n]:
                peer = graph.nodes.get(peer_key)
                similar_json.append(
                    {
                        "id": peer_key,
                        "name": peer.label if peer else peer_key,
                        "registry": peer.registry if peer else None,
                        "similarity": round(jaccard, 4),
                    }
                )
            return json.dumps(
                {
                    "record_id": record_id,
                    "similar": similar_json,
                },
                indent=2,
            )

        lines = [
            f"# Similar Records: {name}",
            f"**Type:** {node.registry} | **Projection via:** shared {connection_label}",
            f"**{name}'s {connection_label}:** {len(my_connections)}",
            "",
        ]

        if not similarities:
            lines.append(f"_No similar {projection_side}s found (no shared {connection_label})._")
            return "\n".join(lines)

        lines.append(f"## Top {min(top_n, len(similarities))} Similar {projection_side.title()}s")
        lines.append("")
        lines.append(f"| Rank | Name | ID | Shared {connection_label.title()} | Jaccard |")
        lines.append("|------|------|----|" + "-" * (len(connection_label) + 10) + "|---------|")

        for rank, (peer_key, shared_count, jaccard, _) in enumerate(similarities[:top_n], 1):
            peer = graph.nodes.get(peer_key)
            peer_label = escape_md_table(peer.label) if peer else peer_key
            lines.append(f"| {rank} | {peer_label} | {peer_key} | {shared_count} | {jaccard:.2f} |")

        # Show shared connection details for top 3
        lines.append("")
        lines.append("### Shared Connection Details")
        for peer_key, shared_count, jaccard, shared in similarities[:3]:
            peer = graph.nodes.get(peer_key)
            peer_label = peer.label if peer else peer_key
            shared_names = sorted(graph.nodes[s].label for s in shared if s in graph.nodes)
            lines.append(f"\n**{name} & {peer_label}** (Jaccard {jaccard:.2f}):")
            for sn in shared_names[:10]:
                lines.append(f"  - {sn}")
            if len(shared_names) > 10:
                lines.append(f"  _(...and {len(shared_names) - 10} more)_")

        return "\n".join(lines)

    except FAIRsharingError as e:
        return f"Error finding similar records: {e}"


# ── Tool 5: Multi-Path Analysis (Yen's K-Shortest) ──────────────────────


@app.mcp.tool(
    name="fairsharing_find_multiple_paths",
    annotations=ToolAnnotations(
        readOnlyHint=True,
        idempotentHint=True,
        openWorldHint=True,
    ),
)
async def find_multiple_paths(
    record_id_1: Annotated[int, Field(ge=1, description="First FAIRsharing record ID")],
    record_id_2: Annotated[int, Field(ge=1, description="Second FAIRsharing record ID")],
    k: Annotated[int, Field(default=3, ge=1, le=10, description="Number of shortest paths")] = 3,
    max_path_length: Annotated[
        int, Field(default=8, ge=2, le=20, description="Maximum path length")
    ] = 8,
    output_format: Annotated[
        str,
        Field(
            default="markdown",
            pattern="^(markdown|json)$",
            description="Output format: 'markdown' or 'json'",
        ),
    ] = "markdown",
) -> str:
    """Find K diverse paths between two records, each telling a different story.

    IMPORTANT: This operates on record_id_1's LOCAL knowledge graph only (1 API
    call). Record_id_2 must be in the same graph neighborhood. If not found,
    use find_cross_graph_path instead (2 API calls).

    Uses Yen's K-shortest paths algorithm with semantic weights. Each path
    represents a different type of relationship chain between the records.

    See also: find_semantic_path for the single strongest path.

    Args:
        record_id_1: First record ID.
        record_id_2: Second record ID.
        k: Number of alternative paths to find (default: 3, max: 5).
        max_path_length: Maximum nodes in a path (default: 8).

    Returns:
        Multiple paths with edge types, weights, and relationship narratives.
    """
    k = min(max(1, k), 5)
    max_path_length = min(max(3, max_path_length), 10)

    try:
        graph = await fetch_and_parse_graph(record_id_1)
        if not graph:
            return f"No graph data available for record ID {record_id_1}."

        key1, key2 = str(record_id_1), str(record_id_2)
        name1 = graph.nodes[key1].label if key1 in graph.nodes else f"Record {record_id_1}"
        name2 = graph.nodes[key2].label if key2 in graph.nodes else f"Record {record_id_2}"

        if key2 not in graph.nodes:
            return (
                f"# Multiple Paths: {name1} <-> {name2}\n\n"
                f"Record {record_id_2} is **not in the local knowledge graph** "
                f"of record {record_id_1}.\n\n"
                f"**Tip:** Use `find_cross_graph_path` to search across both records' "
                f"graphs by merging their neighborhoods (2 API calls)."
            )

        # Yen's K-shortest paths
        all_paths: list[tuple[float, list[tuple[str, str]]]] = []

        # First path: standard Dijkstra
        dist, prev, prev_rel = await run_in_thread(_dijkstra, graph, key1, target=key2)
        first_path = _reconstruct_path(prev, prev_rel, key2)

        if not first_path:
            return (
                f"# Multiple Paths: {name1} <-> {name2}\n\n**No path found** between these records."
            )

        first_cost = dist.get(key2, float("inf"))
        all_paths.append((first_cost, first_path))

        # Find k-1 more paths using Yen's algorithm
        candidates: list[tuple[float, list[tuple[str, str]]]] = []

        for ki in range(1, k):
            if ki - 1 >= len(all_paths):
                break

            prev_path = all_paths[ki - 1][1]

            for spur_idx in range(len(prev_path) - 1):
                spur_node = prev_path[spur_idx][0]
                root_path = prev_path[: spur_idx + 1]
                root_cost = sum(edge_weight(rel) for _, rel in root_path[:-1] if rel)

                # Exclude edges used by existing paths at this spur point
                excluded: set[tuple[str, str]] = set()
                for _, existing_path in all_paths:
                    if len(existing_path) > spur_idx:
                        prefix_match = all(
                            existing_path[j][0] == root_path[j][0]
                            for j in range(min(spur_idx + 1, len(existing_path)))
                        )
                        if prefix_match and spur_idx + 1 < len(existing_path):
                            excluded.add(
                                (existing_path[spur_idx][0], existing_path[spur_idx + 1][0])
                            )
                            excluded.add(
                                (existing_path[spur_idx + 1][0], existing_path[spur_idx][0])
                            )

                # Exclude root path nodes (except spur) to guarantee simple paths
                root_nodes = {root_path[j][0] for j in range(spur_idx)}
                for rn in root_nodes:
                    for neighbor, _ in graph.out_adj.get(rn, []):
                        excluded.add((rn, neighbor))
                        excluded.add((neighbor, rn))
                    for neighbor, _ in graph.in_adj.get(rn, []):
                        excluded.add((neighbor, rn))
                        excluded.add((rn, neighbor))

                spur_dist, spur_prev, spur_rel = await run_in_thread(
                    _dijkstra, graph, spur_node, target=key2, excluded_edges=excluded
                )

                if key2 not in spur_prev:
                    continue

                spur_path = _reconstruct_path(spur_prev, spur_rel, key2)
                if not spur_path or len(root_path) + len(spur_path) - 1 > max_path_length:
                    continue

                # Combine root + spur (skip duplicate spur node)
                full_path = list(root_path[:-1]) + spur_path
                full_cost = root_cost + spur_dist.get(key2, float("inf"))

                # Check for duplicates
                full_keys = tuple(n for n, _ in full_path)
                is_dup = any(
                    tuple(n for n, _ in ep) == full_keys for _, ep in all_paths + candidates
                )
                if not is_dup:
                    candidates.append((full_cost, full_path))

            if not candidates:
                break

            # Pick the best candidate
            candidates.sort(key=lambda x: x[0])
            all_paths.append(candidates.pop(0))

        if output_format == "json":
            paths_json = []
            for cost, path in all_paths:
                path_nodes = []
                for node_key, rel in path:
                    node = graph.nodes.get(node_key)
                    entry: dict = {
                        "id": node_key,
                        "name": node.label if node else node_key,
                        "registry": node.registry if node else None,
                    }
                    if rel:
                        entry["relationship"] = rel
                        entry["weight"] = edge_weight(rel)
                    path_nodes.append(entry)
                paths_json.append(
                    {
                        "nodes": path_nodes,
                        "total_weight": round(cost, 2),
                        "hops": len(path) - 1,
                    }
                )
            return json.dumps(
                {
                    "record_id": record_id_1,
                    "source_id": record_id_1,
                    "target_id": record_id_2,
                    "paths": paths_json,
                },
                indent=2,
            )

        # Format output
        lines = [
            f"# Multiple Paths: {name1} <-> {name2}",
            f"**Paths found:** {len(all_paths)}",
            "",
        ]

        for i, (cost, path) in enumerate(all_paths, 1):
            hops = len(path) - 1
            rels_in_path = [rel for _, rel in path if rel]

            lines.append(f"## Path {i} (weight: {cost:.1f}, {hops} hops)")
            lines.append("")

            # Compact visualization
            parts = []
            for node_key, rel in path:
                node = graph.nodes.get(node_key)
                label = node.label if node else node_key
                parts.append(label)
                if rel:
                    parts.append(f"--[{rel}]-->")

            lines.append(" ".join(parts))
            lines.append("")

            # Narrative
            if rels_in_path:
                rel_counts = Counter(rels_in_path)
                story_parts = [f"{count}x {rel}" for rel, count in rel_counts.most_common()]
                lines.append(f"_Relationships: {', '.join(story_parts)}_")
                lines.append("")

        return "\n".join(lines)

    except FAIRsharingError as e:
        return f"Error finding multiple paths: {e}"


# ── Tool 6: Cross-Graph Path Finding ──────────────────────────────────


@app.mcp.tool(
    name="fairsharing_find_cross_graph_path",
    annotations=ToolAnnotations(
        readOnlyHint=True,
        idempotentHint=True,
        openWorldHint=True,
    ),
)
async def find_cross_graph_path(
    record_id_1: Annotated[int, Field(ge=1, description="First FAIRsharing record ID")],
    record_id_2: Annotated[int, Field(ge=1, description="Second FAIRsharing record ID")],
    prefer: Annotated[
        str,
        Field(
            default="strongest",
            pattern="^(strongest|shortest)$",
            description="Path preference",
        ),
    ] = "strongest",
    output_format: Annotated[
        str,
        Field(
            default="markdown",
            pattern="^(markdown|json)$",
            description="Output format: 'markdown' or 'json'",
        ),
    ] = "markdown",
) -> str:
    """Find a path between two records by merging their local knowledge graphs.

    When two records are not in the same local graph neighborhood, this tool
    fetches BOTH records' graphs, identifies overlapping nodes (bridge points),
    and runs path-finding on the merged graph. Costs 2 API calls instead of 1.

    If the records share no overlapping nodes, no path can be found — the
    records are in completely separate parts of the FAIRsharing knowledge graph.

    Use find_semantic_path first (1 API call). Only use this tool when that
    returns "not in the local knowledge graph."

    Args:
        record_id_1: First record ID.
        record_id_2: Second record ID.
        prefer: "strongest" for lowest semantic distance (default), or
                "shortest" for fewest hops with weight as tiebreaker.

    Returns:
        Path visualization with bridge nodes highlighted, or explanation of why
        no path exists (disjoint graphs, no overlapping nodes).
    """
    try:
        graph_1 = await fetch_and_parse_graph(record_id_1)
        if not graph_1:
            return f"No graph data available for record ID {record_id_1}."

        key1, key2 = str(record_id_1), str(record_id_2)
        name1 = graph_1.nodes[key1].label if key1 in graph_1.nodes else f"Record {record_id_1}"

        # If record_2 is already in graph_1, no need for a second fetch
        if key2 in graph_1.nodes:
            name2 = graph_1.nodes[key2].label
            merged = graph_1
            overlap = set()
            graph_2 = None
        else:
            graph_2 = await fetch_and_parse_graph(record_id_2)
            if not graph_2:
                return f"No graph data available for record ID {record_id_2}."

            name2 = graph_2.nodes[key2].label if key2 in graph_2.nodes else f"Record {record_id_2}"
            overlap = set(graph_1.nodes.keys()) & set(graph_2.nodes.keys())

            if not overlap:
                return (
                    f"# Cross-Graph Path: {name1} <-> {name2}\n\n"
                    f"**No overlapping nodes** between the two graphs.\n\n"
                    f"- Graph 1 ({name1}): {len(graph_1.nodes):,} nodes, "
                    f"{len(graph_1.edges):,} edges\n"
                    f"- Graph 2 ({name2}): {len(graph_2.nodes):,} nodes, "
                    f"{len(graph_2.edges):,} edges\n\n"
                    f"These records are in completely separate parts of the "
                    f"FAIRsharing knowledge graph."
                )

            merged = merge_graphs(graph_1, graph_2)

        # Run path-finding on merged graph
        if prefer == "shortest":
            bfs_dist, bfs_prev, bfs_rel = await run_in_thread(_bfs_weighted, merged, key1, key2)
            path = _reconstruct_path(bfs_prev, bfs_rel, key2)
        else:
            dist, prev, prev_rel = await run_in_thread(_dijkstra, merged, key1, target=key2)
            path = _reconstruct_path(prev, prev_rel, key2)

        if not path:
            return (
                f"# Cross-Graph Path: {name1} <-> {name2}\n\n"
                f"**No path found** even after merging both graphs."
            )

        total_weight = sum(edge_weight(rel) for _, rel in path if rel)
        hop_count = len(path) - 1

        if output_format == "json":
            path_json = []
            for node_key, rel in path:
                node = merged.nodes.get(node_key)
                entry: dict = {
                    "id": node_key,
                    "name": node.label if node else node_key,
                    "registry": node.registry if node else None,
                }
                if rel:
                    entry["relationship"] = rel
                    entry["weight"] = edge_weight(rel)
                path_json.append(entry)
            return json.dumps(
                {
                    "record_id_a": record_id_1,
                    "record_id_b": record_id_2,
                    "path": path_json,
                    "total_weight": round(total_weight, 2),
                    "hops": hop_count,
                },
                indent=2,
            )

        lines = [
            f"# Cross-Graph Path: {name1} <-> {name2}",
            f"**Hops:** {hop_count} | **Total semantic distance:** {total_weight:.1f}",
            f"**Mode:** {'Fewest hops' if prefer == 'shortest' else 'Strongest semantic connection'}",
        ]

        if graph_2:
            lines.append(
                f"**Graphs merged:** {len(graph_1.nodes):,} + {len(graph_2.nodes):,} nodes "
                f"| Overlap: {len(overlap):,} nodes"
            )

        lines.extend(["", "### Path"])

        for i, (node_key, rel) in enumerate(path):
            node = merged.nodes.get(node_key)
            label = node.label if node else node_key
            registry = f" ({node.registry})" if node else ""
            bridge_marker = " **[BRIDGE]**" if node_key in overlap else ""
            lines.append(f"  {'  ' * i}**{label}**{registry} [ID: {node_key}]{bridge_marker}")
            if rel:
                w = edge_weight(rel)
                lines.append(f"  {'  ' * i}  |-- [{rel}, w={w}] -->")

        if overlap:
            bridge_on_path = [k for k, _ in path if k in overlap]
            if bridge_on_path:
                lines.append("")
                lines.append(
                    f"_Path crosses {len(bridge_on_path)} bridge node(s) "
                    f"shared between both graphs._"
                )

        return "\n".join(lines)

    except FAIRsharingError as e:
        return f"Error finding cross-graph path: {e}"


@app.mcp.tool(
    name="fairsharing_find_path_across_graphs",
    annotations=ToolAnnotations(
        readOnlyHint=True,
        idempotentHint=True,
        openWorldHint=True,
    ),
)
async def find_path_across_graphs(
    record_ids: Annotated[
        list[int], Field(min_length=2, description="Record IDs whose graphs to merge")
    ],
    source_id: Annotated[int, Field(ge=1, description="First FAIRsharing record ID")],
    target_id: Annotated[int, Field(ge=1, description="Second FAIRsharing record ID")],
    prefer: Annotated[
        str,
        Field(
            default="strongest",
            pattern="^(strongest|shortest)$",
            description="Path preference",
        ),
    ] = "strongest",
    output_format: Annotated[
        str,
        Field(
            default="markdown",
            pattern="^(markdown|json)$",
            description="Output format: 'markdown' or 'json'",
        ),
    ] = "markdown",
) -> str:
    """Find a path between two records by merging multiple neighborhood graphs.

    Use when source and target may lie in different local graphs. Provide a list
    of record IDs whose graphs to fetch and merge (must include source_id and
    target_id). All graphs are fetched in parallel, then merged; pathfinding
    runs on the combined graph. Costs N API calls (one per record in record_ids).

    For exactly two records, prefer find_cross_graph_path (same result, 2 calls).
    Use this when you have 3+ records and want to search across all their
    neighborhoods (e.g. intermediate "bridge" records).

    Args:
        record_ids: List of record IDs whose graphs to merge (min 2; include
            source_id and target_id).
        source_id: Start node (record ID).
        target_id: End node (record ID).
        prefer: "strongest" (default) or "shortest".

    Returns:
        Path visualization or explanation if no path or missing graph data.
    """
    if len(record_ids) < 2:
        return "Provide at least 2 record IDs to merge graphs."
    if source_id not in record_ids or target_id not in record_ids:
        return "record_ids must include both source_id and target_id."

    try:
        graphs = await asyncio.gather(*[fetch_and_parse_graph(rid) for rid in record_ids])
        failed = [record_ids[i] for i, g in enumerate(graphs) if g is None]
        if failed:
            return (
                f"No graph data for record ID(s): {failed}. "
                "Remove them from record_ids or choose other records."
            )

        merged = merge_multiple_graphs(list(graphs))
        if not merged:
            return "No graphs to merge."

        key1, key2 = str(source_id), str(target_id)
        if key1 not in merged.nodes:
            return f"Source record {source_id} not found in merged graph."
        if key2 not in merged.nodes:
            return f"Target record {target_id} not found in merged graph."

        name1 = merged.nodes[key1].label
        name2 = merged.nodes[key2].label

        if prefer == "shortest":
            bfs_dist, bfs_prev, bfs_rel = await run_in_thread(_bfs_weighted, merged, key1, key2)
            path = _reconstruct_path(bfs_prev, bfs_rel, key2)
        else:
            dist, prev, prev_rel = await run_in_thread(_dijkstra, merged, key1, target=key2)
            path = _reconstruct_path(prev, prev_rel, key2)

        if not path:
            return (
                f"# Path Across Graphs: {name1} <-> {name2}\n\n"
                "**No path found** in the merged graph."
            )

        total_weight = sum(edge_weight(rel) for _, rel in path if rel)
        hop_count = len(path) - 1

        if output_format == "json":
            path_json = []
            for node_key, rel in path:
                node = merged.nodes.get(node_key)
                entry: dict = {
                    "id": node_key,
                    "name": node.label if node else node_key,
                    "registry": node.registry if node else None,
                }
                if rel:
                    entry["relationship"] = rel
                    entry["weight"] = edge_weight(rel)
                path_json.append(entry)
            return json.dumps(
                {
                    "record_ids": record_ids,
                    "path": path_json,
                    "total_weight": round(total_weight, 2),
                    "hops": hop_count,
                },
                indent=2,
            )

        lines = [
            f"# Path Across Graphs: {name1} <-> {name2}",
            f"**Merged:** {len(record_ids)} graphs, {len(merged.nodes):,} nodes, "
            f"{len(merged.edges):,} edges",
            f"**Hops:** {hop_count} | **Semantic distance:** {total_weight:.1f}",
            "",
            "### Path",
        ]
        for i, (node_key, rel) in enumerate(path):
            node = merged.nodes.get(node_key)
            label = node.label if node else node_key
            registry = f" ({node.registry})" if node else ""
            lines.append(f"  {'  ' * i}**{label}**{registry} [ID: {node_key}]")
            if rel:
                w = edge_weight(rel)
                lines.append(f"  {'  ' * i}  |-- [{rel}, w={w}] -->")

        return "\n".join(lines)

    except FAIRsharingError as e:
        return f"Error finding path across graphs: {e}"


# ── Betweenness Centrality Helper ──────────────────────────────────────


def _label_propagation(
    graph: ParsedGraph, max_iterations: int = 20, seed: int | None = 42
) -> dict[str, str]:
    """Run weighted label propagation and return node->label mapping.

    Each node starts with its own label. Iteratively, each node adopts the
    label with the highest weighted vote from its neighbors, using
    RELATIONSHIP_INFLUENCE_WEIGHTS for edge weighting.

    Args:
        graph: Parsed graph.
        max_iterations: Maximum iterations (default: 20).
        seed: Random seed for reproducible results (default: 42). Pass None for non-deterministic.

    Returns:
        Dict mapping node key -> community label (a node key).
    """
    rng = random.Random(seed) if seed is not None else random
    node_keys = list(graph.nodes.keys())
    labels: dict[str, str] = {k: k for k in node_keys}

    for _ in range(max_iterations):
        changed = False
        order = node_keys.copy()
        rng.shuffle(order)

        for node in order:
            neighbor_weights: dict[str, float] = {}
            for neighbor in graph.adj.get(node, set()):
                nl = labels[neighbor]
                w = 0.3  # default
                for nb, rel in graph.out_adj.get(node, []):
                    if nb == neighbor:
                        w = max(w, RELATIONSHIP_INFLUENCE_WEIGHTS.get(rel, 0.2))
                for nb, rel in graph.in_adj.get(node, []):
                    if nb == neighbor:
                        w = max(w, RELATIONSHIP_INFLUENCE_WEIGHTS.get(rel, 0.2))
                neighbor_weights[nl] = neighbor_weights.get(nl, 0) + w

            if not neighbor_weights:
                continue

            max_weight = max(neighbor_weights.values())
            candidates = [lbl for lbl, w in neighbor_weights.items() if w == max_weight]
            best = rng.choice(candidates)

            if labels[node] != best:
                labels[node] = best
                changed = True

        if not changed:
            break

    return labels


def _compute_modularity(graph: ParsedGraph, communities: dict[str, list[str]]) -> float:
    """Compute Newman-Girvan modularity Q for a community partition.

    Q = sum_c [ (edges_within_c / total_edges) - (degree_sum_c / (2 * total_edges))^2 ]

    Values range from -0.5 to ~1.0. Q near 0 means no better than random.
    Q > 0.3 typically indicates meaningful community structure.

    Args:
        graph: Parsed graph.
        communities: Dict of community_label -> list of member node keys.

    Returns:
        Modularity Q score.
    """
    m = len(graph.edges)
    if m == 0:
        return 0.0

    # Degree of each node (undirected)
    degree: dict[str, int] = {}
    for k in graph.nodes:
        degree[k] = len(graph.adj.get(k, set()))

    # Build node -> community mapping
    node_to_comm: dict[str, str] = {}
    for comm_label, members in communities.items():
        for node in members:
            node_to_comm[node] = comm_label

    q = 0.0
    for comm_label, members in communities.items():
        member_set = set(members)
        # Count edges within this community (using undirected adj)
        edges_within = 0
        for node in members:
            for neighbor in graph.adj.get(node, set()):
                if neighbor in member_set:
                    edges_within += 1
        edges_within //= 2  # Each edge counted twice in undirected

        # Sum of degrees in this community
        deg_sum = sum(degree.get(n, 0) for n in members)

        q += (edges_within / m) - (deg_sum / (2 * m)) ** 2

    return q


def _compute_betweenness(graph: ParsedGraph, sample_size: int = 100) -> dict[str, float]:
    """Compute normalized betweenness centrality scores for all nodes.

    Uses Brandes' algorithm (undirected) with optional source sampling.

    Args:
        graph: Parsed graph to analyze.
        sample_size: Max source nodes to sample (for large graphs).

    Returns:
        Dict mapping node key -> betweenness centrality score (normalized).
    """
    node_keys = list(graph.nodes.keys())
    n = len(node_keys)

    betweenness: dict[str, float] = {k: 0.0 for k in node_keys}

    sources = node_keys if n <= sample_size else random.sample(node_keys, sample_size)

    for s in sources:
        # BFS from s
        stack: list[str] = []
        predecessors: dict[str, list[str]] = {k: [] for k in node_keys}
        sigma: dict[str, int] = {k: 0 for k in node_keys}
        sigma[s] = 1
        d: dict[str, int] = {k: -1 for k in node_keys}
        d[s] = 0
        queue: deque[str] = deque([s])

        while queue:
            v = queue.popleft()
            stack.append(v)
            for w in graph.adj.get(v, set()):
                if d[w] < 0:
                    d[w] = d[v] + 1
                    queue.append(w)
                if d[w] == d[v] + 1:
                    sigma[w] += sigma[v]
                    predecessors[w].append(v)

        # Backtrack
        delta: dict[str, float] = {k: 0.0 for k in node_keys}
        while stack:
            w = stack.pop()
            for v in predecessors[w]:
                delta[v] += (sigma[v] / sigma[w]) * (1.0 + delta[w])
            if w != s:
                betweenness[w] += delta[w]

    # Normalize
    if n > 2:
        norm = 2.0 / ((n - 1) * (n - 2))
        if len(sources) < n:
            norm *= n / len(sources)
        for k in node_keys:
            betweenness[k] *= norm

    return betweenness


# ── Tool 6: Betweenness Centrality ──────────────────────────────────────


@app.mcp.tool(
    name="fairsharing_compute_betweenness_centrality",
    annotations=ToolAnnotations(
        readOnlyHint=True,
        idempotentHint=True,
        openWorldHint=True,
    ),
)
async def compute_betweenness_centrality(
    record_id: Annotated[int, Field(ge=1, description="FAIRsharing record ID")],
    top_n: Annotated[
        int, Field(default=25, ge=1, le=100, description="Number of top results")
    ] = 25,
    sample_size: Annotated[
        int, Field(default=100, ge=1, le=200, description="Number of source nodes to sample")
    ] = 100,
    output_format: Annotated[
        str,
        Field(
            default="markdown",
            pattern="^(markdown|json)$",
            description="Output format: 'markdown' or 'json'",
        ),
    ] = "markdown",
) -> str:
    """Compute betweenness centrality to identify critical bridge nodes.

    NOTE: Analyzes a single record's local knowledge graph (1 API call). Use
    suggest_graph_starting_points to find records with the largest graphs.

    Bridge nodes are bottlenecks that connect otherwise disconnected parts
    of the graph. Their removal would fragment the network. This complements
    analyze_deprecation_impact by showing which nodes are structurally critical.

    Uses Brandes' algorithm with optional source sampling for performance.

    See also: analyze_path_criticality to annotate a specific path with BC scores.

    Args:
        record_id: Record ID whose graph to analyze.
        top_n: Number of top bridge nodes to return (default: 25, max: 50).
        sample_size: Number of source nodes to sample (default: 100, max: 200).

    Returns:
        Ranked bridge nodes with betweenness scores and bridge analysis.
    """
    top_n = min(max(1, top_n), 50)
    sample_size = min(max(1, sample_size), 200)

    try:
        graph = await fetch_and_parse_graph(record_id)
        if not graph:
            return f"No graph data available for record ID {record_id}."

        node_keys = list(graph.nodes.keys())
        n = len(node_keys)
        if n < 3:
            return f"Graph has only {n} node(s) — too small for centrality analysis."

        betweenness = await run_in_thread(_compute_betweenness, graph, sample_size)

        # Degree for comparison
        degree = {k: len(graph.adj.get(k, set())) for k in node_keys}
        degree_ranked = sorted(degree.items(), key=lambda x: x[1], reverse=True)
        degree_rank = {k: i + 1 for i, (k, _) in enumerate(degree_ranked)}

        bc_ranked = sorted(betweenness.items(), key=lambda x: x[1], reverse=True)

        if output_format == "json":
            rankings = []
            for key, bc in bc_ranked[:top_n]:
                node = graph.nodes.get(key)
                rankings.append(
                    {
                        "id": key,
                        "name": node.label if node else key,
                        "score": round(bc, 6),
                    }
                )
            return json.dumps(
                {
                    "record_id": record_id,
                    "rankings": rankings,
                },
                indent=2,
            )

        sampled = n > sample_size
        lines = [
            f"# Betweenness Centrality: {graph.name}",
            f"**Network:** {n:,} nodes, {len(graph.edges):,} edges",
            "",
            _SCOPE_CAVEAT,
        ]
        if sampled:
            lines.append(f"**Sampled sources:** {sample_size}/{n}")
        lines.extend(["", f"## Top {min(top_n, n)} Bridge Nodes", ""])
        lines.append("| Rank | Name | ID | Registry | Betweenness | Degree | Deg Rank |")
        lines.append("|------|------|----|----------|-------------|--------|----------|")

        for rank, (key, bc) in enumerate(bc_ranked[:top_n], 1):
            node = graph.nodes.get(key)
            label = escape_md_table(node.label) if node else key
            reg = node.registry if node else "?"
            deg = degree.get(key, 0)
            dr = degree_rank.get(key, "?")
            lines.append(f"| {rank} | {label} | {key} | {reg} | {bc:.4f} | {deg} | {dr} |")

        # Score distribution
        all_bc = [bc for _, bc in bc_ranked]
        bc_max = all_bc[0] if all_bc else 0.0
        bc_mean = sum(all_bc) / len(all_bc) if all_bc else 0.0
        bc_median = all_bc[n // 2] if n > 0 else 0.0
        spread_ratio = bc_max / bc_mean if bc_mean > 0 else 0.0

        lines.extend(["", "## Score Distribution"])
        lines.append(
            f"**Max:** {bc_max:.4f} | **Mean:** {bc_mean:.4f} | "
            f"**Median:** {bc_median:.4f} | **Max/Mean ratio:** {spread_ratio:.1f}x"
        )

        # Bridge analysis with continuous classification
        lines.extend(["", "## Bridge Analysis"])

        # Percentile thresholds
        critical_threshold = max(5, n // 20)  # top 5% or at least 5
        moderate_threshold = n // 4  # top 25%

        bridge_entries = []
        for rank, (key, bc) in enumerate(bc_ranked[:top_n], 1):
            dr = degree_rank.get(key, rank)
            deg = degree.get(key, 0)
            node = graph.nodes.get(key)
            label = node.label if node else key

            # Bridge importance based on BC percentile
            if rank <= critical_threshold:
                importance = "critical bridge"
            elif rank <= moderate_threshold:
                importance = "moderate bridge"
            else:
                importance = "minor"

            # Structural role: compare BC rank vs degree rank
            diff = dr - rank
            if diff >= 5:
                role = "true bottleneck (high BC despite low degree)"
            elif diff <= -5:
                role = "hub, not bottleneck (high degree but BC is proportional)"
            else:
                role = "balanced (BC and degree are proportional)"

            if importance != "minor" or abs(diff) >= 5:
                bridge_entries.append(
                    f"- **{label}** (ID: {key}): BC={bc:.4f} (rank #{rank}), "
                    f"degree={deg} (rank #{dr}) — **{importance}**, {role}"
                )

        if bridge_entries:
            lines.extend(bridge_entries[:10])
        else:
            lines.append("_No notable bridges — betweenness and degree rankings are consistent._")

        return "\n".join(lines)

    except FAIRsharingError as e:
        return f"Error computing betweenness centrality: {e}"


# ── Tool 7: Path Criticality (Combined Path + BC) ─────────────────────


@app.mcp.tool(
    name="fairsharing_analyze_path_criticality",
    annotations=ToolAnnotations(
        readOnlyHint=True,
        idempotentHint=True,
        openWorldHint=True,
    ),
)
async def analyze_path_criticality(
    record_id_1: Annotated[int, Field(ge=1, description="First FAIRsharing record ID")],
    record_id_2: Annotated[int, Field(ge=1, description="Second FAIRsharing record ID")],
    prefer: Annotated[
        str,
        Field(
            default="strongest",
            pattern="^(strongest|shortest)$",
            description="Path preference",
        ),
    ] = "strongest",
    sample_size: Annotated[
        int, Field(default=100, ge=1, le=200, description="Number of source nodes to sample")
    ] = 100,
    output_format: Annotated[
        str,
        Field(
            default="markdown",
            pattern="^(markdown|json)$",
            description="Output format: 'markdown' or 'json'",
        ),
    ] = "markdown",
) -> str:
    """Find the path between two records AND annotate each node with its
    betweenness centrality score (bridge importance).

    Combines find_semantic_path + compute_betweenness_centrality into a single
    call using one shared graph fetch (1 API call). Each path node is annotated
    with its BC score and network-wide rank, making it easy to identify which
    intermediate nodes are critical bridges.

    IMPORTANT: This operates on record_id_1's LOCAL knowledge graph only. If
    record_id_2 is not found, use find_cross_graph_path instead.

    Args:
        record_id_1: First record ID.
        record_id_2: Second record ID.
        prefer: "strongest" for lowest semantic distance, "shortest" for fewest hops.
        sample_size: BC source sampling size (default: 100, max: 200).

    Returns:
        Path with each node annotated by BC rank and score, plus a summary of
        the most critical bridge nodes on the path.
    """
    sample_size = min(max(1, sample_size), 200)

    try:
        graph = await fetch_and_parse_graph(record_id_1)
        if not graph:
            return f"No graph data available for record ID {record_id_1}."

        key1, key2 = str(record_id_1), str(record_id_2)
        name1 = graph.nodes[key1].label if key1 in graph.nodes else f"Record {record_id_1}"
        name2 = graph.nodes[key2].label if key2 in graph.nodes else f"Record {record_id_2}"

        if key2 not in graph.nodes:
            return (
                f"# Path Criticality: {name1} <-> {name2}\n\n"
                f"Record {record_id_2} is **not in the local knowledge graph** "
                f"of record {record_id_1}.\n\n"
                f"**Tip:** Use `find_cross_graph_path` to search across both records' "
                f"graphs by merging their neighborhoods (2 API calls)."
            )

        n = len(graph.nodes)

        # Step 1: Find path
        if prefer == "shortest":
            bfs_dist, bfs_prev, bfs_rel = await run_in_thread(_bfs_weighted, graph, key1, key2)
            path = _reconstruct_path(bfs_prev, bfs_rel, key2)
        else:
            dist, prev, prev_rel = await run_in_thread(_dijkstra, graph, key1, target=key2)
            path = _reconstruct_path(prev, prev_rel, key2)

        if not path:
            return (
                f"# Path Criticality: {name1} <-> {name2}\n\n"
                f"**No path found** between these records."
            )

        # Step 2: Compute betweenness centrality on the same graph
        betweenness = await run_in_thread(_compute_betweenness, graph, sample_size)
        bc_ranked = sorted(betweenness.items(), key=lambda x: x[1], reverse=True)
        bc_rank_lookup = {k: i + 1 for i, (k, _) in enumerate(bc_ranked)}

        # Step 3: Build annotated path table
        total_weight = sum(edge_weight(rel) for _, rel in path if rel)
        hop_count = len(path) - 1

        if output_format == "json":
            path_json = []
            for node_key, rel in path:
                node = graph.nodes.get(node_key)
                entry: dict = {
                    "id": node_key,
                    "name": node.label if node else node_key,
                    "registry": node.registry if node else None,
                    "betweenness_score": round(betweenness.get(node_key, 0.0), 6),
                    "betweenness_rank": bc_rank_lookup.get(node_key, n),
                }
                if rel:
                    entry["relationship"] = rel
                    entry["weight"] = edge_weight(rel)
                path_json.append(entry)
            critical_nodes = []
            intermediaries = path_json[1:-1] if len(path_json) > 2 else []
            for node_entry in sorted(
                intermediaries, key=lambda x: x["betweenness_score"], reverse=True
            ):
                critical_nodes.append(
                    {
                        "id": node_entry["id"],
                        "name": node_entry["name"],
                        "betweenness_score": node_entry["betweenness_score"],
                        "betweenness_rank": node_entry["betweenness_rank"],
                    }
                )
            return json.dumps(
                {
                    "record_id": record_id_1,
                    "paths": path_json,
                    "critical_nodes": critical_nodes,
                    "total_weight": round(total_weight, 2),
                    "hops": hop_count,
                },
                indent=2,
            )

        lines = [
            f"# Path Criticality: {name1} <-> {name2}",
            f"**Hops:** {hop_count} | **Semantic distance:** {total_weight:.1f} "
            f"| **Network:** {n:,} nodes",
            "",
            "## Annotated Path",
            "",
            "| Step | Node | ID | Registry | Relationship | Weight | BC Score | BC Rank |",
            "|------|------|----|----------|--------------|--------|----------|---------|",
        ]

        for step, (node_key, rel) in enumerate(path, 1):
            node = graph.nodes.get(node_key)
            label = escape_md_table(node.label) if node else node_key
            reg = node.registry if node else "?"
            bc = betweenness.get(node_key, 0.0)
            rank = bc_rank_lookup.get(node_key, "?")
            rel_display = rel if rel else "—"
            w_display = f"{edge_weight(rel):.1f}" if rel else "—"
            lines.append(
                f"| {step} | {label} | {node_key} | {reg} "
                f"| {rel_display} | {w_display} | {bc:.4f} | #{rank} |"
            )

        # Step 4: Highlight critical bridges on the path
        lines.extend(["", "## Critical Bridges on Path"])

        path_nodes = [
            (node_key, betweenness.get(node_key, 0.0), bc_rank_lookup.get(node_key, n))
            for node_key, _ in path
        ]
        # Exclude endpoints — focus on intermediary bridge nodes
        intermediaries = path_nodes[1:-1] if len(path_nodes) > 2 else []

        if intermediaries:
            intermediaries.sort(key=lambda x: x[1], reverse=True)
            for node_key, bc, rank in intermediaries:
                node = graph.nodes.get(node_key)
                label = node.label if node else node_key
                severity = (
                    "critical"
                    if rank <= max(5, n // 10)
                    else "moderate"
                    if rank <= n // 4
                    else "low"
                )
                lines.append(
                    f"- **{label}** (ID: {node_key}): BC rank #{rank} (score {bc:.4f}) "
                    f"— {severity} bridge importance"
                )
        else:
            lines.append("_Direct connection — no intermediate bridge nodes._")

        return "\n".join(lines)

    except FAIRsharingError as e:
        return f"Error analyzing path criticality: {e}"


# ── Tool 8: Strongly Connected Components (Tarjan's) ────────────────────


@app.mcp.tool(
    name="fairsharing_find_dependency_clusters",
    annotations=ToolAnnotations(
        readOnlyHint=True,
        idempotentHint=True,
        openWorldHint=True,
    ),
)
async def find_dependency_clusters(
    record_id: Annotated[int, Field(ge=1, description="FAIRsharing record ID")],
    min_component_size: Annotated[
        int, Field(default=2, ge=1, le=50, description="Minimum community size")
    ] = 2,
    output_format: Annotated[
        str,
        Field(
            default="markdown",
            pattern="^(markdown|json)$",
            description="Output format: 'markdown' or 'json'",
        ),
    ] = "markdown",
) -> str:
    """Find strongly connected components (mutual dependency clusters) in the graph.

    NOTE: Analyzes a single record's local knowledge graph (1 API call). Use
    suggest_graph_starting_points to find records with the largest graphs.

    An SCC is a group of records where every node can reach every other node
    via directed edges. This reveals tightly coupled dependency groups that
    go beyond simple cycle detection.

    Extends detect_circular_dependencies with full SCC analysis in a single
    API call (vs per-hop queries).

    Args:
        record_id: Record ID whose graph to analyze.
        min_component_size: Minimum SCC size to report (default: 2).

    Returns:
        Dependency clusters with member lists and internal edge details.
    """
    min_component_size = max(1, min_component_size)

    try:
        graph = await fetch_and_parse_graph(record_id)
        if not graph:
            return f"No graph data available for record ID {record_id}."

        node_keys = list(graph.nodes.keys())
        n = len(node_keys)

        # Tarjan's SCC (iterative to avoid Python recursion limits)
        sccs = await run_in_thread(_tarjan_scc, graph, node_keys)

        # Filter by minimum size
        significant = sorted(
            [scc for scc in sccs if len(scc) >= min_component_size],
            key=len,
            reverse=True,
        )

        if output_format == "json":
            clusters_json = []
            for i, scc in enumerate(significant, 1):
                members = []
                for m in scc:
                    node = graph.nodes.get(m)
                    members.append(
                        {
                            "id": m,
                            "name": node.label if node else m,
                            "registry": node.registry if node else None,
                        }
                    )
                clusters_json.append(
                    {
                        "id": i,
                        "members": members,
                        "size": len(scc),
                    }
                )
            return json.dumps(
                {
                    "record_id": record_id,
                    "clusters": clusters_json,
                },
                indent=2,
            )

        lines = [
            f"# Strongly Connected Components: {graph.name}",
            f"**Network:** {n:,} nodes, {len(graph.edges):,} edges (directed)",
            f"**Non-trivial SCCs:** {len(significant)}",
            "",
            _SCOPE_CAVEAT,
            "",
        ]

        if not significant:
            lines.append("_No mutual dependency clusters found (all nodes are acyclic)._")
            return "\n".join(lines)

        total_in_sccs = sum(len(scc) for scc in significant)

        for i, scc in enumerate(significant, 1):
            scc_set = set(scc)
            lines.append(f"## Cluster {i} ({len(scc)} nodes)")
            lines.append("")
            lines.append("| Name | ID | Registry | Type | Status |")
            lines.append("|------|----|----------|------|--------|")

            for m in sorted(
                scc, key=lambda k: graph.nodes.get(k, NodeInfo(k, k, "", "", "")).label
            ):
                node = graph.nodes.get(m)
                if node:
                    lines.append(
                        f"| {escape_md_table(node.label)} | {node.key} | {node.registry} | "
                        f"{node.record_type} | {node.status} |"
                    )

            # Internal edges
            internal_edges = [
                (s, t, rel) for s, t, rel in graph.edges if s in scc_set and t in scc_set
            ]
            if internal_edges:
                lines.append("")
                lines.append("**Internal relationships:**")
                for s, t, rel in internal_edges[:20]:
                    s_label = graph.nodes[s].label if s in graph.nodes else s
                    t_label = graph.nodes[t].label if t in graph.nodes else t
                    lines.append(f"- {s_label} --[{rel}]--> {t_label}")
                if len(internal_edges) > 20:
                    lines.append(f"_(...and {len(internal_edges) - 20} more)_")

            lines.append("")

        lines.append("## Summary")
        lines.append(
            f"- **{len(significant)}** dependency clusters with **{total_in_sccs}** total nodes"
        )
        lines.append(f"- Largest cluster: {len(significant[0])} nodes")
        lines.append(
            "- Nodes in these clusters are mutually dependent — changes to any member may affect all others"
        )

        return "\n".join(lines)

    except FAIRsharingError as e:
        return f"Error finding dependency clusters: {e}"


def _tarjan_scc(graph: ParsedGraph, node_keys: list[str]) -> list[list[str]]:
    """Compute strongly connected components using iterative Tarjan's algorithm.

    Returns:
        List of SCCs, where each SCC is a list of node keys.
    """
    sccs: list[list[str]] = []
    _tarjan_iterative(graph, node_keys, sccs)
    return sccs


def _tarjan_iterative(
    graph: ParsedGraph,
    node_keys: list[str],
    sccs: list[list[str]],
) -> None:
    """Iterative Tarjan's SCC to avoid recursion limit issues."""
    index_counter = 0
    stack: list[str] = []
    on_stack: set[str] = set()
    index: dict[str, int] = {}
    lowlink: dict[str, int] = {}

    for root in node_keys:
        if root in index:
            continue

        # DFS using an explicit call stack
        # Each frame: (node, neighbor_iterator, is_returning)
        call_stack: list[tuple[str, int]] = []
        index[root] = index_counter
        lowlink[root] = index_counter
        index_counter += 1
        stack.append(root)
        on_stack.add(root)

        call_stack.append((root, 0))

        while call_stack:
            v, ni = call_stack[-1]
            neighbors = graph.out_adj.get(v, [])

            if ni < len(neighbors):
                call_stack[-1] = (v, ni + 1)
                w, _ = neighbors[ni]

                if w not in index:
                    index[w] = index_counter
                    lowlink[w] = index_counter
                    index_counter += 1
                    stack.append(w)
                    on_stack.add(w)
                    call_stack.append((w, 0))
                elif w in on_stack:
                    lowlink[v] = min(lowlink[v], index[w])
            else:
                # Done with v's neighbors
                if lowlink[v] == index[v]:
                    scc: list[str] = []
                    while True:
                        w = stack.pop()
                        on_stack.discard(w)
                        scc.append(w)
                        if w == v:
                            break
                    sccs.append(scc)

                call_stack.pop()
                if call_stack:
                    parent, _ = call_stack[-1]
                    lowlink[parent] = min(lowlink[parent], lowlink[v])


# ── Tool 10: Comprehensive Graph Analysis ─────────────────────────────


@app.mcp.tool(
    name="fairsharing_analyze_graph_comprehensive",
    annotations=ToolAnnotations(
        readOnlyHint=True,
        idempotentHint=True,
        openWorldHint=True,
    ),
)
async def analyze_graph_comprehensive(
    record_id: Annotated[int, Field(ge=1, description="FAIRsharing record ID")],
    top_n: Annotated[
        int, Field(default=15, ge=1, le=100, description="Number of top results")
    ] = 15,
    damping: Annotated[
        float, Field(default=0.85, gt=0, lt=1, description="PageRank damping factor")
    ] = 0.85,
    min_community_size: Annotated[
        int, Field(default=3, ge=1, le=50, description="Minimum community size")
    ] = 3,
    seed: Annotated[
        int | None, Field(default=42, description="Random seed for reproducibility")
    ] = 42,
    summary_mode: Annotated[
        bool, Field(default=False, description="If True, output is condensed for large graphs")
    ] = False,
    additional_seed_ids: Annotated[
        list[int] | None,
        Field(default=None, max_length=20, description="Additional seed record IDs"),
    ] = None,
    output_format: Annotated[
        str,
        Field(
            default="markdown",
            pattern="^(markdown|json)$",
            description="Output format: 'markdown' or 'json'",
        ),
    ] = "markdown",
) -> str:
    """Run PageRank, community detection, and betweenness centrality in one call.

    This combined analysis tool fetches the graph once (1 API call) and runs
    all three algorithms, then cross-references the results:

    - Which communities do the top PageRank nodes belong to?
    - Are high-betweenness bridges connecting different communities?
    - Is there meaningful community structure, or did detection collapse?

    Use this instead of calling compute_pagerank, detect_communities, and
    compute_betweenness_centrality separately when you need a holistic picture.
    For very large graphs (e.g. 10k+ nodes), the combined analysis may be slow.

    NOTE: By default, analyzes a single record's local knowledge graph (1 API call).
    Pass additional_seed_ids to merge multiple records' graphs for broader coverage.

    Args:
        record_id: Primary record ID whose graph to analyze.
        top_n: Number of top nodes to show in cross-reference (default: 15, max: 30).
        damping: PageRank damping factor (default: 0.85).
        min_community_size: Minimum community size to label (default: 3).
        seed: Random seed for community detection (default: 42). Pass None for non-deterministic.
        summary_mode: If True, output is condensed for large graphs.
        additional_seed_ids: Optional list of extra record IDs to merge into the analysis.
            Each ID costs 1 API call. Max 10 additional seeds.

    Returns:
        Cross-referenced analysis combining PageRank influence, community
        membership, and bridge importance for each top node, plus quality
        indicators (modularity Q, score spread, edge density).
    """
    top_n = min(max(1, top_n), 30)

    try:
        graph = await fetch_and_parse_graph(record_id)
        if not graph:
            return f"No graph data available for record ID {record_id}."

        # Merge additional seed graphs if requested
        seed_count = 1
        if additional_seed_ids:
            extra_ids = additional_seed_ids[:10]  # Cap at 10 extras
            extra_graphs = await asyncio.gather(*[fetch_and_parse_graph(sid) for sid in extra_ids])
            valid_extras = [g for g in extra_graphs if g is not None]
            if valid_extras:
                all_graphs = [graph] + valid_extras
                graph = merge_multiple_graphs(all_graphs) or graph
                seed_count = 1 + len(valid_extras)

        node_keys = list(graph.nodes.keys())
        n = len(node_keys)
        if n < 3:
            return f"Graph has only {n} node(s) — too small for comprehensive analysis."

        use_summary = summary_mode or (n > 500)
        display_top_n = min(top_n, 8) if use_summary else top_n

        # ── PageRank ──
        pr = await run_in_thread(_compute_pagerank_scores, node_keys, graph.out_adj, damping, 20)
        pr_ranked = sorted(pr.items(), key=lambda x: x[1], reverse=True)

        # ── Community Detection ──
        labels = await run_in_thread(_label_propagation, graph, seed=seed)
        communities: dict[str, list[str]] = {}
        for node_key, label in labels.items():
            communities.setdefault(label, []).append(node_key)
        significant = sorted(
            [(lbl, m) for lbl, m in communities.items() if len(m) >= min_community_size],
            key=lambda x: len(x[1]),
            reverse=True,
        )
        # Assign community numbers
        comm_number: dict[str, int] = {}
        for i, (lbl, members) in enumerate(significant, 1):
            for m in members:
                comm_number[m] = i
        modularity = await run_in_thread(_compute_modularity, graph, communities)

        # ── Betweenness Centrality ──
        betweenness = await run_in_thread(_compute_betweenness, graph)
        bc_ranked = sorted(betweenness.items(), key=lambda x: x[1], reverse=True)
        bc_rank_lookup = {k: i + 1 for i, (k, _) in enumerate(bc_ranked)}

        # ── Quality indicators ──
        max_edges = n * (n - 1) / 2
        edge_density = len(graph.edges) / max_edges if max_edges > 0 else 0.0
        all_pr = [s for _, s in pr_ranked]
        pr_top = all_pr[0]
        pr_median = all_pr[n // 2] if n > 0 else pr_top
        pr_spread = pr_top / pr_median if pr_median > 0 else 1.0
        largest_comm_frac = len(significant[0][1]) / n if significant else 0.0
        is_collapsed = len(significant) <= 1 and largest_comm_frac > 0.8

        if output_format == "json":
            pagerank_json = []
            for key, score in pr_ranked[:top_n]:
                node = graph.nodes.get(key)
                pagerank_json.append(
                    {
                        "id": key,
                        "name": node.label if node else key,
                        "registry": node.registry if node else None,
                        "score": round(score, 6),
                    }
                )
            comms_json = []
            for i, (lbl, members) in enumerate(significant, 1):
                member_list = []
                for m in members:
                    node = graph.nodes.get(m)
                    member_list.append(
                        {
                            "id": m,
                            "name": node.label if node else m,
                            "registry": node.registry if node else None,
                        }
                    )
                comms_json.append(
                    {
                        "id": i,
                        "members": member_list,
                        "size": len(members),
                    }
                )
            bc_json = []
            for key, bc in bc_ranked[:top_n]:
                node = graph.nodes.get(key)
                bc_json.append(
                    {
                        "id": key,
                        "name": node.label if node else key,
                        "score": round(bc, 6),
                    }
                )
            return json.dumps(
                {
                    "record_id": record_id,
                    "nodes": n,
                    "edges": len(graph.edges),
                    "pagerank": pagerank_json,
                    "communities": comms_json,
                    "betweenness": bc_json,
                    "modularity": round(modularity, 4),
                },
                indent=2,
            )

        # ── Build output ──
        lines = [
            f"# Comprehensive Graph Analysis: {graph.name}",
            f"**Network:** {n:,} nodes, {len(graph.edges):,} edges "
            f"| **Edge density:** {edge_density:.2%}",
        ]
        if seed_count > 1:
            lines.append(f"**Seeds merged:** {seed_count} record graphs")
            lines.append("")
            lines.append(
                "_Scope: This analysis covers **merged local neighborhoods** from "
                f"{seed_count} seed records. Metrics reflect the merged subgraph, "
                "not the full platform graph._"
            )
        else:
            lines.append("")
            lines.append(_SCOPE_CAVEAT)
        lines.append("")
        if use_summary:
            lines.append("_(Summary mode — condensed output.)_")
            lines.append("")
        lines.extend(
            [
                "## Quality Indicators",
                f"- **Modularity Q:** {modularity:.3f}"
                + (
                    " (weak — near random)"
                    if modularity < 0.1
                    else " (moderate)"
                    if modularity < 0.3
                    else " (strong)"
                ),
                f"- **PageRank spread:** {pr_spread:.1f}x (top/median)"
                + (
                    " — flat distribution"
                    if pr_spread < 2.0
                    else " — moderate hierarchy"
                    if pr_spread < 5.0
                    else " — strong hierarchy"
                ),
                f"- **Communities:** {len(significant)}"
                + (" (collapsed to single community)" if is_collapsed else ""),
                "",
            ]
        )

        if is_collapsed:
            lines.append(
                "**Community collapse detected:** The graph is too densely interconnected "
                "for label propagation to find distinct clusters. All analysis below uses "
                "a single community."
            )
            lines.append("")

        # ── Cross-reference table ──
        critical_threshold = max(5, n // 20)

        lines.append(f"## Cross-Reference: Top {min(display_top_n, n)} Nodes")
        lines.append("")
        lines.append(
            "| PR Rank | Name | ID | Registry | PageRank | Community | "
            "BC Rank | BC Score | Bridge? |"
        )
        lines.append(
            "|---------|------|----|----------|----------|-----------|"
            "---------|----------|---------|"
        )

        for rank, (key, score) in enumerate(pr_ranked[:display_top_n], 1):
            node = graph.nodes.get(key)
            label = escape_md_table(node.label) if node else key
            reg = node.registry if node else "?"
            comm = comm_number.get(key, 0)
            comm_str = f"C{comm}" if comm > 0 else "small"
            bc = betweenness.get(key, 0.0)
            bc_r = bc_rank_lookup.get(key, n)
            bridge = "Yes" if bc_r <= critical_threshold else "No"
            lines.append(
                f"| {rank} | {label} | {key} | {reg} | {score:.4f} "
                f"| {comm_str} | #{bc_r} | {bc:.4f} | {bridge} |"
            )

        # ── Narrative synthesis ──
        lines.extend(["", "## Synthesis"])

        # Which communities hold the top PageRank nodes?
        top_comms = Counter(comm_number.get(k, 0) for k, _ in pr_ranked[: min(10, n)])
        if len(top_comms) == 1:
            lines.append(
                "- **Influence concentration:** All top PageRank nodes belong to a "
                "single community, suggesting a centralized ecosystem."
            )
        else:
            comm_parts = [f"C{c} ({cnt})" for c, cnt in top_comms.most_common()]
            lines.append(
                f"- **Influence distribution:** Top PageRank nodes span "
                f"{len(top_comms)} communities: {', '.join(comm_parts)}."
            )

        # Are bridges connecting different communities?
        bridge_nodes = [(k, bc) for k, bc in bc_ranked[:critical_threshold]]
        cross_comm_bridges = []
        for bk, bbc in bridge_nodes:
            neighbor_comms = set()
            for nb in graph.adj.get(bk, set()):
                nc = comm_number.get(nb, 0)
                if nc > 0:
                    neighbor_comms.add(nc)
            if len(neighbor_comms) > 1:
                node = graph.nodes.get(bk)
                cross_comm_bridges.append(node.label if node else bk)

        if cross_comm_bridges:
            lines.append(
                f"- **Cross-community bridges:** {', '.join(cross_comm_bridges[:5])} "
                f"connect different communities — removing them would fragment the graph."
            )
        elif bridge_nodes:
            lines.append(
                "- **Bridges are intra-community:** Top bridge nodes connect parts "
                "within the same community rather than across communities."
            )

        # Community summaries
        if significant and not is_collapsed:
            lines.extend(["", "## Community Summaries"])
            max_comm_summaries = 3 if use_summary else 5
            for i, (lbl, members) in enumerate(significant[:max_comm_summaries], 1):
                reg_dist = Counter(graph.nodes[m].registry for m in members if m in graph.nodes)
                dominant = reg_dist.most_common(1)[0][0] if reg_dist else "mixed"
                # Top PR node in this community
                comm_pr = [(k, pr.get(k, 0)) for k in members]
                comm_pr.sort(key=lambda x: x[1], reverse=True)
                top_node = graph.nodes.get(comm_pr[0][0])
                top_label = top_node.label if top_node else comm_pr[0][0]
                lines.append(
                    f"- **Community {i}** ({len(members)} nodes, primarily {dominant}): "
                    f"led by {top_label}"
                )

        return "\n".join(lines)

    except FAIRsharingError as e:
        return f"Error in comprehensive analysis: {e}"


# ── Tool 11: Expanded Graph Exploration ─────────────────────────────────


@app.mcp.tool(
    name="fairsharing_explore_expanded_graph",
    annotations=ToolAnnotations(
        readOnlyHint=True,
        idempotentHint=True,
        openWorldHint=True,
    ),
)
async def explore_expanded_graph(
    record_id: Annotated[int, Field(ge=1, description="FAIRsharing record ID")],
    depth: Annotated[int, Field(default=2, ge=1, le=3, description="Expansion depth")] = 2,
    max_seeds: Annotated[
        int, Field(default=10, ge=1, le=20, description="Maximum seed records")
    ] = 10,
    top_n: Annotated[
        int, Field(default=15, ge=1, le=100, description="Number of top results")
    ] = 15,
    damping: Annotated[
        float, Field(default=0.85, gt=0, lt=1, description="PageRank damping factor")
    ] = 0.85,
    seed: Annotated[
        int | None, Field(default=42, description="Random seed for reproducibility")
    ] = 42,
    output_format: Annotated[
        str,
        Field(
            default="markdown",
            pattern="^(markdown|json)$",
            description="Output format: 'markdown' or 'json'",
        ),
    ] = "markdown",
) -> str:
    """Iteratively expand a record's neighborhood graph for broader analysis.

    Starting from one record, fetches its graph, identifies the most connected
    neighbors, fetches THEIR graphs, merges all, and runs comprehensive analysis
    (PageRank, communities, betweenness) on the merged supergraph.

    Depth 1 = single graph (equivalent to analyze_graph_comprehensive).
    Depth 2 = adds the most-connected neighbors' graphs.
    Depth 3 = one more expansion round.

    API cost: 1 + up to (max_seeds - 1) additional calls.

    Args:
        record_id: Starting record ID.
        depth: Expansion depth (1-3, default: 2).
        max_seeds: Maximum total API calls for graph fetching (2-20, default: 10).
        top_n: Top nodes to show in analysis (default: 15, max: 30).
        damping: PageRank damping factor (default: 0.85).
        seed: Random seed for community detection (default: 42).

    Returns:
        Comprehensive analysis of the expanded graph with provenance notes.
    """
    depth = min(max(1, depth), 3)
    max_seeds = min(max(2, max_seeds), 20)
    top_n = min(max(1, top_n), 30)

    try:
        # Fetch the initial seed graph
        root_graph = await fetch_and_parse_graph(record_id)
        if not root_graph:
            return f"No graph data available for record ID {record_id}."

        all_graphs = [root_graph]
        fetched_ids: set[str] = {str(record_id)}
        total_fetches = 1

        # Iterative expansion
        current_graph = root_graph
        for _ in range(depth - 1):
            if total_fetches >= max_seeds:
                break

            # Find top-degree neighbors not yet fetched
            degree_list = []
            for nk in current_graph.nodes:
                if nk not in fetched_ids:
                    deg = len(current_graph.adj.get(nk, set()))
                    degree_list.append((nk, deg))
            degree_list.sort(key=lambda x: x[1], reverse=True)

            # Fetch top neighbors' graphs
            budget = max_seeds - total_fetches
            expansion_ids = [nk for nk, _ in degree_list[:budget]]
            if not expansion_ids:
                break

            expansion_graphs = await asyncio.gather(
                *[fetch_and_parse_graph(int(nk)) for nk in expansion_ids],
                return_exceptions=True,
            )

            new_graphs = []
            for nk, result in zip(expansion_ids, expansion_graphs):
                fetched_ids.add(nk)
                total_fetches += 1
                if isinstance(result, Exception) or result is None:
                    continue
                new_graphs.append(result)

            if new_graphs:
                all_graphs.extend(new_graphs)

            # Merge for next iteration
            merged = merge_multiple_graphs(all_graphs)
            if merged:
                current_graph = merged

        # Final merge
        final_graph = merge_multiple_graphs(all_graphs)
        if not final_graph or len(final_graph.nodes) < 3:
            return (
                f"Expanded graph from record {record_id} has too few nodes "
                f"({len(final_graph.nodes) if final_graph else 0}) for analysis."
            )

        n = len(final_graph.nodes)
        node_keys = list(final_graph.nodes.keys())

        # Run comprehensive analysis on merged graph
        pr = await run_in_thread(
            _compute_pagerank_scores, node_keys, final_graph.out_adj, damping, 20
        )
        pr_ranked = sorted(pr.items(), key=lambda x: x[1], reverse=True)

        labels = await run_in_thread(_label_propagation, final_graph, seed=seed)
        communities: dict[str, list[str]] = {}
        for nk, lbl in labels.items():
            communities.setdefault(lbl, []).append(nk)
        significant_comms = sorted(
            [(lbl, m) for lbl, m in communities.items() if len(m) >= 3],
            key=lambda x: len(x[1]),
            reverse=True,
        )

        betweenness = await run_in_thread(_compute_betweenness, final_graph)
        bc_ranked = sorted(betweenness.items(), key=lambda x: x[1], reverse=True)

        if output_format == "json":
            pagerank_json = []
            for key, score in pr_ranked[:top_n]:
                node = final_graph.nodes.get(key)
                pagerank_json.append(
                    {
                        "id": key,
                        "name": node.label if node else key,
                        "registry": node.registry if node else None,
                        "score": round(score, 6),
                    }
                )
            comms_json = []
            for i, (lbl, members) in enumerate(significant_comms, 1):
                comms_json.append(
                    {
                        "id": i,
                        "size": len(members),
                        "dominant_registry": Counter(
                            final_graph.nodes[m].registry for m in members if m in final_graph.nodes
                        ).most_common(1)[0][0]
                        if members
                        else "mixed",
                    }
                )
            bc_json = []
            for key, bc_score in bc_ranked[:top_n]:
                node = final_graph.nodes.get(key)
                bc_json.append(
                    {
                        "id": key,
                        "name": node.label if node else key,
                        "score": round(bc_score, 6),
                    }
                )
            return json.dumps(
                {
                    "record_id": record_id,
                    "depth": depth,
                    "graphs_merged": total_fetches,
                    "nodes": n,
                    "edges": len(final_graph.edges),
                    "pagerank": pagerank_json,
                    "communities": comms_json,
                    "betweenness": bc_json,
                },
                indent=2,
            )

        # Build output
        lines = [
            f"# Expanded Graph Analysis (depth={depth})",
            f"**Seed:** Record {record_id} | **Graphs merged:** {total_fetches}",
            f"**Network:** {n:,} nodes, {len(final_graph.edges):,} edges",
            f"**Communities:** {len(significant_comms)}",
            "",
            f"_Scope: Merged {total_fetches} local neighborhood graphs via iterative "
            f"expansion from seed record {record_id} (depth={depth}). Metrics reflect "
            "the merged subgraph, not the full platform graph._",
            "",
            "## Top Nodes by PageRank",
            "",
            "| Rank | Name | ID | Registry | PageRank |",
            "|------|------|----|----------|----------|",
        ]

        for rank, (key, score) in enumerate(pr_ranked[:top_n], 1):
            node = final_graph.nodes.get(key)
            label = escape_md_table(node.label) if node else key
            reg = node.registry if node else "?"
            lines.append(f"| {rank} | {label} | {key} | {reg} | {score:.4f} |")

        if significant_comms:
            lines.extend(["", "## Communities"])
            for i, (lbl, members) in enumerate(significant_comms[:5], 1):
                reg_dist = Counter(
                    final_graph.nodes[m].registry for m in members if m in final_graph.nodes
                )
                dominant = reg_dist.most_common(1)[0][0] if reg_dist else "mixed"
                lines.append(f"- **Community {i}**: {len(members)} nodes, primarily {dominant}")

        lines.extend(["", "## Top Bridge Nodes (Betweenness)"])
        for rank, (key, bc_score) in enumerate(bc_ranked[:5], 1):
            node = final_graph.nodes.get(key)
            label = escape_md_table(node.label) if node else key
            lines.append(f"{rank}. **{label}** (BC: {bc_score:.4f})")

        return "\n".join(lines)

    except FAIRsharingError as e:
        return f"Error in expanded graph analysis: {e}"


# ── Tool 12: Topic Graph ────────────────────────────────────────────────


@app.mcp.tool(
    name="fairsharing_build_topic_graph",
    annotations=ToolAnnotations(
        readOnlyHint=True,
        idempotentHint=True,
        openWorldHint=True,
    ),
)
async def build_topic_graph(
    subject: Annotated[str, Field(min_length=1, description="Subject to build topic graph for")],
    registry: Annotated[
        str | None,
        Field(default=None, description="Registry filter (Standard, Database, Policy)"),
    ] = None,
    max_seeds: Annotated[
        int, Field(default=5, ge=1, le=20, description="Maximum seed records")
    ] = 5,
    top_n: Annotated[
        int, Field(default=15, ge=1, le=100, description="Number of top results")
    ] = 15,
    seed: Annotated[
        int | None, Field(default=42, description="Random seed for reproducibility")
    ] = 42,
    output_format: Annotated[
        str,
        Field(
            default="markdown",
            pattern="^(markdown|json)$",
            description="Output format: 'markdown' or 'json'",
        ),
    ] = "markdown",
) -> str:
    """Build a topic-level graph by searching for records and merging their neighborhoods.

    Searches for top records matching a subject, fetches each one's graph,
    merges all, and returns combined analysis. Useful for understanding the
    landscape of a topic across standards, databases, and policies.

    API cost: 1 search + up to max_seeds graph fetches.

    Args:
        subject: Subject to search (e.g., "Genomics", "Proteomics").
        registry: Optional registry filter ("Standard", "Database", "Policy").
        max_seeds: Maximum records to fetch graphs for (2-10, default: 5).
        top_n: Top nodes to show in analysis (default: 15, max: 30).
        seed: Random seed for community detection (default: 42).

    Returns:
        Topic-level graph analysis with community structure and key nodes.
    """
    from fairsharing_mcp.queries import SEARCH_RECORDS_COMPACT_QUERY

    max_seeds = min(max(2, max_seeds), 10)
    top_n = min(max(1, top_n), 30)

    try:
        client = app.get_client()

        # Search for records matching the subject
        variables: dict = {"searchQuery": subject, "page": 1, "perPage": max_seeds}
        if registry:
            variables["registry"] = registry.capitalize()

        data = await client.query(SEARCH_RECORDS_COMPACT_QUERY, variables)
        search_result = data.get("searchFairsharingRecords", {})
        records = search_result.get("records", [])

        if not records:
            return f"No records found for subject '{subject}'."

        # Fetch graphs for top records
        record_ids = [int(r["id"]) for r in records[:max_seeds]]
        graphs = await asyncio.gather(
            *[fetch_and_parse_graph(rid) for rid in record_ids],
            return_exceptions=True,
        )

        valid_graphs = []
        seed_info = []
        for rid, g in zip(record_ids, graphs):
            if isinstance(g, Exception) or g is None:
                continue
            valid_graphs.append(g)
            seed_info.append((rid, len(g.nodes), len(g.edges)))

        if not valid_graphs:
            return f"No graph data available for records matching '{subject}'."

        merged = merge_multiple_graphs(valid_graphs)
        if not merged or len(merged.nodes) < 3:
            return f"Merged graph for '{subject}' has too few nodes for analysis."

        n = len(merged.nodes)
        node_keys = list(merged.nodes.keys())

        # Run analysis
        pr = await run_in_thread(_compute_pagerank_scores, node_keys, merged.out_adj, 0.85, 20)
        pr_ranked = sorted(pr.items(), key=lambda x: x[1], reverse=True)

        labels = await run_in_thread(_label_propagation, merged, seed=seed)
        communities: dict[str, list[str]] = {}
        for nk, lbl in labels.items():
            communities.setdefault(lbl, []).append(nk)
        significant_comms = sorted(
            [(lbl, m) for lbl, m in communities.items() if len(m) >= 3],
            key=lambda x: len(x[1]),
            reverse=True,
        )

        if output_format == "json":
            pagerank_json = []
            for key, score in pr_ranked[:top_n]:
                node = merged.nodes.get(key)
                pagerank_json.append(
                    {
                        "id": key,
                        "name": node.label if node else key,
                        "registry": node.registry if node else None,
                        "score": round(score, 6),
                    }
                )
            comms_json = []
            for i, (lbl, members) in enumerate(significant_comms, 1):
                comms_json.append(
                    {
                        "id": i,
                        "size": len(members),
                        "dominant_registry": Counter(
                            merged.nodes[m].registry for m in members if m in merged.nodes
                        ).most_common(1)[0][0]
                        if members
                        else "mixed",
                    }
                )
            return json.dumps(
                {
                    "subject": subject,
                    "seeds": [rid for rid, _, _ in seed_info],
                    "nodes": n,
                    "edges": len(merged.edges),
                    "pagerank": pagerank_json,
                    "communities": comms_json,
                },
                indent=2,
            )

        # Build output
        reg_filter = f" ({registry})" if registry else ""
        lines = [
            f"# Topic Graph: {subject}{reg_filter}",
            f"**Seeds:** {len(valid_graphs)} records | "
            f"**Network:** {n:,} nodes, {len(merged.edges):,} edges",
            f"**Communities:** {len(significant_comms)}",
            "",
            f"_Scope: Merged {len(valid_graphs)} local neighborhood graphs from "
            f'top records matching "{subject}". Metrics reflect the merged subgraph, '
            "not the full platform graph._",
            "",
            "## Seed Records",
        ]
        for rid, nn, ne in seed_info:
            lines.append(f"- Record {rid}: {nn} nodes, {ne} edges")

        lines.extend(
            [
                "",
                "## Top Nodes by PageRank",
                "",
                "| Rank | Name | ID | Registry | PageRank |",
                "|------|------|----|----------|----------|",
            ]
        )

        for rank, (key, score) in enumerate(pr_ranked[:top_n], 1):
            node = merged.nodes.get(key)
            label = escape_md_table(node.label) if node else key
            reg = node.registry if node else "?"
            lines.append(f"| {rank} | {label} | {key} | {reg} | {score:.4f} |")

        if significant_comms:
            lines.extend(["", "## Communities"])
            for i, (lbl, members) in enumerate(significant_comms[:5], 1):
                reg_dist = Counter(merged.nodes[m].registry for m in members if m in merged.nodes)
                dominant = reg_dist.most_common(1)[0][0] if reg_dist else "mixed"
                lines.append(f"- **Community {i}**: {len(members)} nodes, primarily {dominant}")

        return "\n".join(lines)

    except FAIRsharingError as e:
        return f"Error building topic graph: {e}"
