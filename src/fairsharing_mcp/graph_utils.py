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


def build_label_overrides(record: dict, record_id: int | str) -> dict[tuple[str, str], str]:
    """Build exact (source, target) → relationship label pairs from a record's associations.

    The graph payload only carries edge *colors*, and color is not a faithful proxy for
    the relationship label — "brown" covers both shares_data_with and part_of, and
    part_of / shares_code_with are not reachable from color at all (see
    constants.AMBIGUOUS_EDGE_COLORS and COLOR_UNREACHABLE_LABELS). Where a caller has
    already fetched `recordAssociations`, those labels are authoritative and should win.

    Args:
        record: A record dict containing recordAssociations / reverseRecordAssociations.
        record_id: The record the associations belong to (the edge endpoint they share).

    Returns:
        Mapping of (source_key, target_key) → label, suitable for parse_graph(overrides=).
    """
    src = str(record_id)
    overrides: dict[tuple[str, str], str] = {}

    for assoc in record.get("recordAssociations") or []:
        linked = assoc.get("linkedRecord") or {}
        target = linked.get("id")
        label = assoc.get("recordAssocLabel")
        if target is not None and label:
            overrides[(src, str(target))] = label

    for assoc in record.get("reverseRecordAssociations") or []:
        linked = assoc.get("fairsharingRecord") or {}
        source = linked.get("id")
        label = assoc.get("recordAssocLabel")
        if source is not None and label:
            overrides[(str(source), src)] = label

    return overrides


def parse_graph(
    graph_data, label_overrides: dict[tuple[str, str], str] | None = None
) -> ParsedGraph:
    """Parse raw FAIRsharing graph JSON into indexed structures.

    Handles both JSON string and dict input. Builds undirected adjacency,
    directed out/in adjacency, and maps edge colors to relationship types.

    Args:
        graph_data: Raw graph data (JSON string or dict) from GET_GRAPH_QUERY.
        label_overrides: Optional exact (source, target) → label mapping that takes
            precedence over color inference. Build it with build_label_overrides().

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
        # Authoritative label wins; color is only a fallback for graph-only edges.
        rel = (label_overrides or {}).get((s, t))
        if rel is None:
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


async def fetch_and_parse_graph(
    record_id: int, authoritative_labels: bool = False
) -> ParsedGraph | None:
    """Fetch graph data for a record and parse it into a ParsedGraph.

    Args:
        record_id: The FAIRsharing record ID.
        authoritative_labels: If True, make one extra API call to fetch the record's
            `recordAssociations` and overlay their exact labels onto the seed record's
            incident edges, bypassing lossy color inference for those edges. Costs one
            additional request; edges elsewhere in the neighbourhood still use color.

    Returns:
        A ParsedGraph, or None if no graph data is available.
    """
    from fairsharing_mcp import app
    from fairsharing_mcp.queries import GET_GRAPH_QUERY, GET_RECORD_WITH_ASSOCIATIONS_QUERY

    client = app.get_client()
    data = await client.query(GET_GRAPH_QUERY, {"id": record_id})
    graph = data.get("fairsharingGraph", {}).get("data")

    if not graph:
        return None

    overrides: dict[tuple[str, str], str] | None = None
    if authoritative_labels:
        try:
            rec_data = await client.query(GET_RECORD_WITH_ASSOCIATIONS_QUERY, {"id": record_id})
            record = rec_data.get("fairsharingRecord")
            if record:
                overrides = build_label_overrides(record, record_id)
        except FAIRsharingError:
            # Non-fatal: fall back to color inference rather than failing the whole call.
            logger.warning(
                "Could not fetch authoritative labels for record %s; using color inference",
                record_id,
            )

    try:
        return parse_graph(graph, label_overrides=overrides)
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
