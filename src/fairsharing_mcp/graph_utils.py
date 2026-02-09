"""Shared graph data structures and parsing for FAIRsharing knowledge graph analysis."""

import asyncio
import json
import logging
from dataclasses import dataclass, field

from fairsharing_mcp.client import FAIRsharingError
from fairsharing_mcp.constants import EDGE_COLOR_TO_RELATIONSHIP, RELATIONSHIP_WEIGHTS

logger = logging.getLogger(__name__)


class GraphParseError(Exception):
    """Raised when graph JSON cannot be parsed (invalid or malformed data)."""


@dataclass
class NodeInfo:
    """A node in the FAIRsharing knowledge graph."""

    key: str
    label: str
    registry: str
    record_type: str
    status: str


@dataclass
class ParsedGraph:
    """Parsed and indexed representation of a FAIRsharing knowledge graph."""

    nodes: dict[str, NodeInfo] = field(default_factory=dict)
    edges: list[tuple[str, str, str]] = field(default_factory=list)
    adj: dict[str, set[str]] = field(default_factory=dict)
    out_adj: dict[str, list[tuple[str, str]]] = field(default_factory=dict)
    in_adj: dict[str, list[tuple[str, str]]] = field(default_factory=dict)
    name: str = "Unknown"


def parse_graph(graph_data) -> ParsedGraph:
    """Parse raw FAIRsharing graph JSON into indexed structures.

    Handles both JSON string and dict input. Builds undirected adjacency,
    directed out/in adjacency, and maps edge colors to relationship types.

    Args:
        graph_data: Raw graph data (JSON string or dict) from GET_GRAPH_QUERY.

    Returns:
        A ParsedGraph with all adjacency structures populated.

    Raises:
        GraphParseError: If graph_data is a string that is not valid JSON.
    """
    if isinstance(graph_data, str):
        try:
            graph_data = json.loads(graph_data)
        except json.JSONDecodeError as e:
            raise GraphParseError(f"Invalid graph data: {e}") from e

    raw_nodes = graph_data.get("nodes", [])
    raw_edges = graph_data.get("edges", [])
    graph_name = graph_data.get("name", "Unknown")

    nodes: dict[str, NodeInfo] = {}
    for n in raw_nodes:
        attrs = n.get("attributes", {})
        key = n["key"]
        nodes[key] = NodeInfo(
            key=key,
            label=attrs.get("label", key),
            registry=attrs.get("registry", "unknown"),
            record_type=attrs.get("record_type", "unknown"),
            status=attrs.get("status", "unknown"),
        )

    adj: dict[str, set[str]] = {}
    out_adj: dict[str, list[tuple[str, str]]] = {}
    in_adj: dict[str, list[tuple[str, str]]] = {}
    edges: list[tuple[str, str, str]] = []

    for e in raw_edges:
        s, t = e["source"], e["target"]
        color = e.get("attributes", {}).get("color", "grey")
        rel = EDGE_COLOR_TO_RELATIONSHIP.get(color, "related_to")
        edges.append((s, t, rel))

        adj.setdefault(s, set()).add(t)
        adj.setdefault(t, set()).add(s)
        out_adj.setdefault(s, []).append((t, rel))
        in_adj.setdefault(t, []).append((s, rel))

    return ParsedGraph(
        nodes=nodes,
        edges=edges,
        adj=adj,
        out_adj=out_adj,
        in_adj=in_adj,
        name=graph_name,
    )


def edge_weight(rel_type: str) -> float:
    """Get the semantic distance weight for a relationship type.

    Lower values indicate stronger/more meaningful relationships.
    """
    return RELATIONSHIP_WEIGHTS.get(rel_type, 5.0)


async def fetch_and_parse_graph(record_id: int) -> ParsedGraph | None:
    """Fetch graph data for a record and parse it into a ParsedGraph.

    Args:
        record_id: The FAIRsharing record ID.

    Returns:
        A ParsedGraph, or None if no graph data is available.
    """
    from fairsharing_mcp import app
    from fairsharing_mcp.queries import GET_GRAPH_QUERY

    client = app.get_client()
    data = await client.query(GET_GRAPH_QUERY, {"id": record_id})
    graph = data.get("fairsharingGraph", {}).get("data")

    if not graph:
        return None

    try:
        return parse_graph(graph)
    except GraphParseError as e:
        raise FAIRsharingError(f"Invalid graph data: {e}") from e


def merge_graphs(graph_a: ParsedGraph, graph_b: ParsedGraph) -> ParsedGraph:
    """Merge two ParsedGraph instances, deduplicating nodes and edges.

    Nodes present in both graphs are kept once (graph_a's NodeInfo preferred).
    Edges are deduplicated by (source, target, relationship) tuple.

    Args:
        graph_a: First graph (its node metadata takes priority for duplicates).
        graph_b: Second graph.

    Returns:
        A new ParsedGraph containing the union of both graphs.
    """
    # Merge nodes — graph_a takes priority for duplicates
    nodes: dict[str, NodeInfo] = {}
    nodes.update(graph_b.nodes)
    nodes.update(graph_a.nodes)

    # Merge edges — deduplicate by (source, target, rel) tuple
    edge_set: set[tuple[str, str, str]] = set()
    edges: list[tuple[str, str, str]] = []
    for edge_list in (graph_a.edges, graph_b.edges):
        for s, t, rel in edge_list:
            key = (s, t, rel)
            if key not in edge_set:
                edge_set.add(key)
                edges.append((s, t, rel))

    # Rebuild adjacency structures from merged edges
    adj: dict[str, set[str]] = {}
    out_adj: dict[str, list[tuple[str, str]]] = {}
    in_adj: dict[str, list[tuple[str, str]]] = {}

    for s, t, rel in edges:
        adj.setdefault(s, set()).add(t)
        adj.setdefault(t, set()).add(s)
        out_adj.setdefault(s, []).append((t, rel))
        in_adj.setdefault(t, []).append((s, rel))

    return ParsedGraph(
        nodes=nodes,
        edges=edges,
        adj=adj,
        out_adj=out_adj,
        in_adj=in_adj,
        name=f"{graph_a.name} + {graph_b.name}",
    )


def merge_multiple_graphs(graphs: list[ParsedGraph]) -> ParsedGraph | None:
    """Merge multiple ParsedGraph instances into one.

    Reduces the list by repeatedly merging pairs (graph_a, graph_b).
    Order: first graph's node metadata takes priority for duplicates.

    Args:
        graphs: List of ParsedGraph instances (may be empty).

    Returns:
        A single merged ParsedGraph, or None if graphs is empty.
    """
    if not graphs:
        return None
    if len(graphs) == 1:
        return graphs[0]
    merged = graphs[0]
    for g in graphs[1:]:
        merged = merge_graphs(merged, g)
    return merged


async def run_in_thread(fn, *args, **kwargs):
    """Run a synchronous function in a thread pool to avoid blocking the event loop.

    All graph algorithm functions (Dijkstra, PageRank, label propagation, etc.)
    are pure functions operating on in-memory structures, making them safe to
    execute in a separate thread.
    """
    return await asyncio.to_thread(fn, *args, **kwargs)
