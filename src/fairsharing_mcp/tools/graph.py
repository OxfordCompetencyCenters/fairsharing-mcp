"""FAIRsharing MCP tools — Graph and relationship analysis."""

import json
import logging
from collections import Counter, deque
from typing import Annotated

from mcp.types import ToolAnnotations
from pydantic import Field

from fairsharing_mcp import app, config
from fairsharing_mcp.client import FAIRsharingError
from fairsharing_mcp.constants import EDGE_COLOR_TO_RELATIONSHIP
from fairsharing_mcp.formatters import build_fairsharing_url
from fairsharing_mcp.queries import (
    GET_GRAPH_QUERY,
    GET_RECORD_WITH_ASSOCIATIONS_QUERY,
    GET_RELATIONSHIP_LABELS_QUERY,
)

logger = logging.getLogger(__name__)


@app.mcp.tool(
    name="fairsharing_analyze_record_ecosystem",
    annotations=ToolAnnotations(
        readOnlyHint=True,
        idempotentHint=True,
        openWorldHint=True,
    ),
)
async def analyze_record_ecosystem(
    record_id: Annotated[int, Field(ge=1, description="FAIRsharing record ID")],
    output_format: Annotated[
        str,
        Field(
            default="markdown",
            pattern="^(markdown|json)$",
            description="Output format: 'markdown' or 'json'",
        ),
    ] = "markdown",
) -> str:
    """Analyze the full ecosystem around a record: what implements it, recommends it, extends it, etc.

    For standards: shows which databases implement it, which policies recommend it,
    which collections include it. For databases: shows which standards it uses,
    which policies reference it. Relationships are grouped by type and registry.

    Args:
        record_id: The FAIRsharing record ID
        output_format: "markdown" (default) or "json" for structured output

    Returns:
        Detailed ecosystem analysis with relationships grouped by type and registry
    """
    client = app.get_client()

    try:
        data = await client.query(GET_RECORD_WITH_ASSOCIATIONS_QUERY, {"id": record_id})
        record = data.get("fairsharingRecord")

        if not record:
            return f"No record found with ID {record_id}."

        name = record.get("name", "Unknown")
        abbrev = record.get("abbreviation", "")
        registry = record.get("registry", "Unknown")
        rec_type = record.get("type", "Unknown")

        # --- Outgoing relationships ---
        outgoing = record.get("recordAssociations", [])
        # --- Incoming relationships ---
        incoming = record.get("reverseRecordAssociations", [])

        if output_format == "json":
            # Outgoing and incoming are grouped SEPARATELY. They used to share one
            # by_relationship dict, so a label present in both directions produced a
            # merged list and any caller counting entries got a wrong answer.
            group_cap = config.get_display_limit("ecosystem_group")

            def _entry(linked: dict, direction: str) -> dict:
                return {
                    "id": linked.get("id"),
                    "name": linked.get("name", "Unknown"),
                    "abbreviation": linked.get("abbreviation", ""),
                    "registry": linked.get("registry", "Unknown"),
                    "status": linked.get("status", ""),
                    "fairsharing_url": build_fairsharing_url(linked.get("doi")),
                    "direction": direction,
                }

            def _grouped(assocs: list, key: str, direction: str) -> dict:
                """Group by relationship label, capping the record list but never the count."""
                buckets: dict[str, list] = {}
                registries: dict[str, int] = {}
                for a in assocs:
                    label = a.get("recordAssocLabel", "related_to")
                    linked = a.get(key, {})
                    buckets.setdefault(label, []).append(_entry(linked, direction))
                    reg = linked.get("registry", "Unknown")
                    registries[reg] = registries.get(reg, 0) + 1
                return {
                    "total": len(assocs),
                    "label_counts": {k: len(v) for k, v in buckets.items()},
                    "registry_counts": registries,
                    "by_relationship": {
                        k: {
                            "count": len(v),
                            "truncated": bool(group_cap) and len(v) > group_cap,
                            "records": v[:group_cap] if group_cap else v,
                        }
                        for k, v in buckets.items()
                    },
                }

            out_grouped = _grouped(outgoing, "linkedRecord", "outgoing")
            in_grouped = _grouped(incoming, "fairsharingRecord", "incoming")
            any_truncated = any(
                g["truncated"]
                for side in (out_grouped, in_grouped)
                for g in side["by_relationship"].values()
            )

            return json.dumps(
                {
                    "record_id": record_id,
                    "name": name,
                    "abbreviation": abbrev,
                    "registry": registry,
                    "type": rec_type,
                    "status": record.get("status", "N/A"),
                    "total_relationships": len(outgoing) + len(incoming),
                    "total_outgoing": len(outgoing),
                    "total_incoming": len(incoming),
                    "outgoing": out_grouped,
                    "incoming": in_grouped,
                    "records_truncated": any_truncated,
                    "note": (
                        f"Record lists capped at {group_cap} per relationship label; "
                        "`count` and `label_counts` are always exact. Call "
                        "fairsharing_list_associations for the complete, paginated set."
                        if any_truncated
                        else "Complete: no record list was capped."
                    ),
                },
                indent=2,
            )

        # Per (label, registry) group cap. Was hardcoded at 15, which no environment
        # variable could reach; now configurable via FAIRSHARING_DISPLAY_MAX_ECOSYSTEM_GROUP
        # (0 = show all).
        md_group_cap = config.get_display_limit("ecosystem_group")

        lines = [
            f"# Ecosystem Analysis: {name}" + (f" ({abbrev})" if abbrev else ""),
            f"**Registry:** {registry} | **Type:** {rec_type} | **Status:** {record.get('status', 'N/A')}",
            "",
        ]

        # Collect data for both JSON and markdown
        subjects = [s.get("label", "") for s in record.get("subjects", []) if s.get("label")]
        domains_list = [d.get("label", "") for d in record.get("domains", []) if d.get("label")]
        taxonomies = [t.get("label", "") for t in record.get("taxonomies", []) if t.get("label")]
        orgs = [o.get("name", "") for o in record.get("organisations", []) if o.get("name")]

        if subjects:
            lines.append(f"**Subjects:** {', '.join(subjects)}")
        if domains_list:
            lines.append(f"**Domains:** {', '.join(domains_list)}")
        if taxonomies:
            lines.append(f"**Taxonomies:** {', '.join(taxonomies[:10])}")
            if len(taxonomies) > 10:
                lines[-1] += f" _(+{len(taxonomies) - 10} more)_"
        if orgs:
            lines.append(f"**Organisations:** {', '.join(orgs[:5])}")
        lines.append("")

        if outgoing:
            lines.append(f"## Outgoing Relationships ({len(outgoing)} total)")
            lines.append("")

            # Group by relationship label, then by registry
            by_label: dict[str, dict[str, list]] = {}
            for a in outgoing:
                label = a.get("recordAssocLabel", "related_to")
                lr = a.get("linkedRecord", {})
                reg = lr.get("registry", "Unknown")
                by_label.setdefault(label, {}).setdefault(reg, []).append(lr)

            for label in sorted(by_label):
                label_total = sum(len(v) for v in by_label[label].values())
                lines.append(f"### {label} ({label_total})")
                for reg in sorted(by_label[label]):
                    items = by_label[label][reg]
                    lines.append(f"**{reg}** ({len(items)}):")
                    for item in items[:md_group_cap] if md_group_cap else items:
                        item_name = item.get("name", "Unknown")
                        item_abbrev = item.get("abbreviation", "")
                        item_status = item.get("status", "")
                        entry = f"  - {item_name}"
                        if item_abbrev:
                            entry += f" ({item_abbrev})"
                        entry += f" [ID: {item.get('id', '?')}]"
                        if item_status and item_status != "ready":
                            entry += f" _{item_status}_"
                        lines.append(entry)
                    if md_group_cap and len(items) > md_group_cap:
                        lines.append(
                            f"  _Showing {md_group_cap} of {len(items)}. Call "
                            "`fairsharing_list_associations` for the complete list._"
                        )
                lines.append("")

        if incoming:
            lines.append(f"## Incoming Relationships ({len(incoming)} total)")
            lines.append("_These records point TO this record:_")
            lines.append("")

            by_label_in: dict[str, dict[str, list]] = {}
            for a in incoming:
                label = a.get("recordAssocLabel", "related_to")
                lr = a.get("fairsharingRecord", {})
                reg = lr.get("registry", "Unknown")
                by_label_in.setdefault(label, {}).setdefault(reg, []).append(lr)

            for label in sorted(by_label_in):
                label_total = sum(len(v) for v in by_label_in[label].values())
                lines.append(f"### {label} ({label_total})")
                for reg in sorted(by_label_in[label]):
                    items = by_label_in[label][reg]
                    lines.append(f"**{reg}** ({len(items)}):")
                    for item in items[:md_group_cap] if md_group_cap else items:
                        item_name = item.get("name", "Unknown")
                        item_abbrev = item.get("abbreviation", "")
                        item_status = item.get("status", "")
                        entry = f"  - {item_name}"
                        if item_abbrev:
                            entry += f" ({item_abbrev})"
                        entry += f" [ID: {item.get('id', '?')}]"
                        if item_status and item_status != "ready":
                            entry += f" _{item_status}_"
                        lines.append(entry)
                    if md_group_cap and len(items) > md_group_cap:
                        lines.append(
                            f"  _Showing {md_group_cap} of {len(items)}. Call "
                            "`fairsharing_list_associations` for the complete list._"
                        )
                lines.append("")

        # --- Summary statistics ---
        total_out = len(outgoing)
        total_in = len(incoming)
        lines.append("## Summary")
        lines.append(f"- **Total outgoing relationships:** {total_out}")
        lines.append(f"- **Total incoming relationships:** {total_in}")
        lines.append(f"- **Total connections:** {total_out + total_in}")

        if outgoing:
            out_labels = Counter(a.get("recordAssocLabel", "?") for a in outgoing)
            lines.append(
                f"- **Outgoing breakdown:** {', '.join(f'{label}={c}' for label, c in out_labels.most_common())}"
            )
        if incoming:
            in_labels = Counter(a.get("recordAssocLabel", "?") for a in incoming)
            lines.append(
                f"- **Incoming breakdown:** {', '.join(f'{label}={c}' for label, c in in_labels.most_common())}"
            )

        return "\n".join(lines)

    except FAIRsharingError as e:
        return f"Error analyzing ecosystem: {e}"


@app.mcp.tool(
    name="fairsharing_find_record_connections",
    annotations=ToolAnnotations(
        readOnlyHint=True,
        idempotentHint=True,
        openWorldHint=True,
    ),
)
async def find_record_connections(
    record_id_1: Annotated[int, Field(ge=1, description="First FAIRsharing record ID")],
    record_id_2: Annotated[int, Field(ge=1, description="Second FAIRsharing record ID")],
    output_format: Annotated[
        str,
        Field(
            default="markdown",
            pattern="^(markdown|json)$",
            description="Output format: 'markdown' or 'json'",
        ),
    ] = "markdown",
) -> str:
    """Find how two records are connected in the knowledge graph.

    IMPORTANT: This searches within record_id_1's LOCAL graph neighborhood
    only (1 API call). If record_id_2 is not found, use find_cross_graph_path
    to merge both records' neighborhoods and search for a bridging path.

    Checks for direct connections and finds shortest paths between two records
    using the graph data. Shows the relationship chain connecting them.

    Args:
        record_id_1: First record ID
        record_id_2: Second record ID
        output_format: "markdown" (default) or "json" for structured output

    Returns:
        Connection analysis showing direct and indirect paths between the records
    """
    client = app.get_client()

    try:
        # Fetch graph from record 1's perspective
        data = await client.query(GET_GRAPH_QUERY, {"id": record_id_1})
        graph = data.get("fairsharingGraph", {}).get("data")

        if not graph:
            return f"No graph data available for record ID {record_id_1}."

        if isinstance(graph, str):
            graph = json.loads(graph)

        nodes = graph.get("nodes", [])
        edges = graph.get("edges", [])

        # Build node lookup
        node_map = {}
        for n in nodes:
            attrs = n.get("attributes", {})
            node_map[n["key"]] = attrs.get("label", n["key"])

        key1 = str(record_id_1)
        key2 = str(record_id_2)

        name1 = node_map.get(key1, f"Record {record_id_1}")
        name2 = node_map.get(key2, f"Record {record_id_2}")

        lines = [
            f"# Connection Analysis: {name1} <-> {name2}",
            "",
        ]

        # Check if record 2 exists in record 1's graph
        if key2 not in node_map:
            if output_format == "json":
                return json.dumps(
                    {
                        "record_id": record_id_1,
                        "target_id": record_id_2,
                        "connected": False,
                        "paths": [],
                        "shared_neighbors": [],
                        "note": f"Record {record_id_2} is not in the local knowledge graph of record {record_id_1}. Use find_cross_graph_path to search across both neighborhoods.",
                    },
                    indent=2,
                )
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

        # Build adjacency list (undirected for path finding)
        adj: dict[str, set[str]] = {}
        edge_lookup: dict[str, str] = {}  # (source,target) -> color
        for e in edges:
            s, t = e["source"], e["target"]
            adj.setdefault(s, set()).add(t)
            adj.setdefault(t, set()).add(s)
            edge_lookup[f"{s}->{t}"] = e.get("attributes", {}).get("color", "?")
            edge_lookup[f"{t}->{s}"] = e.get("attributes", {}).get("color", "?")

        # BFS for shortest path
        visited = {key1}
        queue: deque[list[str]] = deque([[key1]])
        found_path: list[str] | None = None

        while queue:
            path = queue.popleft()
            current = path[-1]

            if current == key2:
                found_path = path
                break

            if len(path) > 6:  # Limit path length
                continue

            for neighbor in adj.get(current, []):
                if neighbor not in visited:
                    visited.add(neighbor)
                    queue.append(path + [neighbor])

        # Single source of truth — this used to be a divergent hardcoded copy.
        color_meaning = EDGE_COLOR_TO_RELATIONSHIP

        # Shared neighbors
        neighbors_1 = adj.get(key1, set())
        neighbors_2 = adj.get(key2, set())
        shared = neighbors_1 & neighbors_2

        if output_format == "json":
            paths_json = []
            if found_path:
                path_steps = []
                for i, node_key in enumerate(found_path):
                    step = {"id": node_key, "name": node_map.get(node_key, node_key)}
                    if i < len(found_path) - 1:
                        next_key = found_path[i + 1]
                        color = edge_lookup.get(f"{node_key}->{next_key}", "?")
                        step["relationship"] = color_meaning.get(color, color)
                    path_steps.append(step)
                paths_json.append(path_steps)
            shared_json = [
                {"id": n, "name": node_map.get(n, n)}
                for n in sorted(shared, key=lambda x: node_map.get(x, x))
            ]
            return json.dumps(
                {
                    "record_id": record_id_1,
                    "target_id": record_id_2,
                    "connected": found_path is not None,
                    "path_length": len(found_path) - 1 if found_path else None,
                    "paths": paths_json,
                    "shared_neighbors": shared_json,
                },
                indent=2,
            )

        if found_path:
            lines.append(f"**Path found!** Length: {len(found_path) - 1} hop(s)")
            lines.append("")

            lines.append("### Path")
            for i, node_key in enumerate(found_path):
                label = node_map.get(node_key, node_key)
                lines.append(f"  {'  ' * i}**{label}** (ID: {node_key})")
                if i < len(found_path) - 1:
                    next_key = found_path[i + 1]
                    color = edge_lookup.get(f"{node_key}->{next_key}", "?")
                    rel = color_meaning.get(color, color)
                    lines.append(f"  {'  ' * i}  |-- [{rel}] -->")
            lines.append("")

            # Also show all direct connections between the two
            direct_1_to_2 = any(e["source"] == key1 and e["target"] == key2 for e in edges)
            direct_2_to_1 = any(e["source"] == key2 and e["target"] == key1 for e in edges)

            if direct_1_to_2 or direct_2_to_1:
                lines.append("### Direct Connection Exists")
                if direct_1_to_2:
                    color = edge_lookup.get(f"{key1}->{key2}", "?")
                    lines.append(f"- {name1} --[{color_meaning.get(color, color)}]--> {name2}")
                if direct_2_to_1:
                    color = edge_lookup.get(f"{key2}->{key1}", "?")
                    lines.append(f"- {name2} --[{color_meaning.get(color, color)}]--> {name1}")
        else:
            lines.append("**No path found** within 6 hops.")
            lines.append("The records exist in the same graph but are not closely connected.")

        if shared:
            lines.append(f"\n### Shared Neighbors ({len(shared)})")
            for n in sorted(shared, key=lambda x: node_map.get(x, x))[:20]:
                lines.append(f"- {node_map.get(n, n)} (ID: {n})")
            if len(shared) > 20:
                lines.append(f"_(...and {len(shared) - 20} more)_")

        return "\n".join(lines)

    except FAIRsharingError as e:
        return f"Error finding connections: {e}"


@app.mcp.tool(
    name="fairsharing_find_graph_hubs",
    annotations=ToolAnnotations(
        readOnlyHint=True,
        idempotentHint=True,
        openWorldHint=True,
    ),
)
async def find_graph_hubs(
    record_id: Annotated[int, Field(ge=1, description="FAIRsharing record ID")],
    top_n: Annotated[
        int, Field(default=25, ge=1, le=100, description="Number of top results")
    ] = 25,
    output_format: Annotated[
        str,
        Field(
            default="markdown",
            pattern="^(markdown|json)$",
            description="Output format: 'markdown' or 'json'",
        ),
    ] = "markdown",
) -> str:
    """Find the most connected hub nodes in a record's knowledge graph.

    NOTE: Analyzes a single record's local knowledge graph (1 API call). Use
    suggest_graph_starting_points to find records with the largest graphs.

    Hub nodes are the most influential standards, databases, or policies in the
    network. This helps identify key resources in an ecosystem.

    Args:
        record_id: Record ID whose graph to analyze
        top_n: Number of top hubs to return (default: 25, max: 50)
        output_format: "markdown" (default) or "json" for structured output

    Returns:
        Ranked list of hub nodes with connection counts and registry info
    """
    client = app.get_client()
    top_n = min(max(1, top_n), 50)

    try:
        data = await client.query(GET_GRAPH_QUERY, {"id": record_id})
        graph = data.get("fairsharingGraph", {}).get("data")

        if not graph:
            return f"No graph data available for record ID {record_id}."

        if isinstance(graph, str):
            graph = json.loads(graph)

        nodes = graph.get("nodes", [])
        edges = graph.get("edges", [])

        # Build node info
        node_map = {}
        for n in nodes:
            attrs = n.get("attributes", {})
            node_map[n["key"]] = {
                "label": attrs.get("label", n["key"]),
                "registry": attrs.get("registry", "unknown"),
                "record_type": attrs.get("record_type", "unknown"),
                "status": attrs.get("status", "unknown"),
            }

        # Count degrees
        in_degree = Counter()
        out_degree = Counter()
        for e in edges:
            out_degree[e["source"]] += 1
            in_degree[e["target"]] += 1

        total_degree = Counter()
        for key in node_map:
            total_degree[key] = in_degree.get(key, 0) + out_degree.get(key, 0)

        graph_name = graph.get("name", f"Record {record_id}")

        if output_format == "json":
            hubs = []
            for key, degree in total_degree.most_common(top_n):
                info = node_map.get(key, {})
                hubs.append(
                    {
                        "id": key,
                        "name": info.get("label", key),
                        "registry": info.get("registry", "?"),
                        "record_type": info.get("record_type", "?"),
                        "in_degree": in_degree.get(key, 0),
                        "out_degree": out_degree.get(key, 0),
                        "degree": degree,
                    }
                )
            return json.dumps(
                {
                    "record_id": record_id,
                    "graph_name": graph_name,
                    "total_nodes": len(nodes),
                    "total_edges": len(edges),
                    "hubs": hubs,
                },
                indent=2,
            )

        lines = [
            f"# Hub Analysis: {graph_name}",
            f"**Network:** {len(nodes):,} nodes, {len(edges):,} edges",
            "",
            f"## Top {top_n} Hub Nodes (by total connections)",
            "",
            "| Rank | Name | ID | Registry | Type | In | Out | Total |",
            "|------|------|----|----------|------|----|----|-------|",
        ]

        for rank, (key, degree) in enumerate(total_degree.most_common(top_n), 1):
            info = node_map.get(key, {})
            label = info.get("label", key)
            reg = info.get("registry", "?")
            rtype = info.get("record_type", "?")
            ind = in_degree.get(key, 0)
            outd = out_degree.get(key, 0)
            lines.append(
                f"| {rank} | {label} | {key} | {reg} | {rtype} | {ind} | {outd} | {degree} |"
            )

        lines.append("")

        # Hub distribution by registry
        lines.append("## Hub Distribution by Registry (top 50)")
        top_50 = [k for k, _ in total_degree.most_common(50)]
        hub_registries = Counter(node_map.get(k, {}).get("registry", "?") for k in top_50)
        for reg, count in hub_registries.most_common():
            lines.append(f"- **{reg.title()}:** {count}")

        return "\n".join(lines)

    except FAIRsharingError as e:
        return f"Error analyzing hubs: {e}"


@app.mcp.tool(
    name="fairsharing_get_relationship_types",
    annotations=ToolAnnotations(
        readOnlyHint=True,
        idempotentHint=True,
        openWorldHint=True,
    ),
)
async def get_relationship_types(
    output_format: Annotated[
        str,
        Field(
            default="markdown",
            pattern="^(markdown|json)$",
            description="Output format: 'markdown' or 'json'",
        ),
    ] = "markdown",
) -> str:
    """Get all possible relationship types between FAIRsharing records.

    Returns the vocabulary of relationship labels used to connect records,
    such as "implements", "recommends", "related_to", "extends", etc.

    Args:
        output_format: "markdown" (default) or "json" for structured output

    Returns:
        List of all relationship labels with their meanings
    """
    client = app.get_client()

    try:
        data = await client.query(GET_RELATIONSHIP_LABELS_QUERY, cache=True)
        label_objs = data.get("recordAssociationLabels", [])

        if not label_objs:
            return "No relationship labels found."

        labels = [obj.get("name", "") for obj in label_objs if obj.get("name")]

        # Known descriptions
        descriptions = {
            "related_to": "General relationship between records",
            "implements": "A database implements/uses a standard",
            "recommends": "A policy recommends a standard or database",
            "collects": "A collection includes a record",
            "extends": "A standard/database extends another",
            "accepts": "A database/policy accepts a format/standard",
            "outputs": "A database outputs data in a standard format",
            "profiles": "A policy profiles/specifies requirements for records",
            "shares_data_with": "A database shares data with another database",
            "shares_code_with": "A database shares code with another database",
            "deprecates": "A record deprecates/replaces another",
            "part_of": "A record is part of another record",
            "measures_principle": "A metric measures a FAIR principle",
            "has_associated_metric": "A record has an associated metric",
        }

        if output_format == "json":
            relationships = []
            for label in sorted(labels):
                relationships.append(
                    {
                        "name": label,
                        "description": descriptions.get(label, ""),
                    }
                )
            return json.dumps(
                {
                    "relationships": relationships,
                },
                indent=2,
            )

        lines = [
            "## FAIRsharing Relationship Types",
            "",
            "These labels describe how records are connected in the knowledge graph:",
            "",
        ]

        for label in sorted(labels):
            desc = descriptions.get(label, "")
            lines.append(f"- **{label}**" + (f": {desc}" if desc else ""))

        lines.append("")
        lines.append("### Typical Cross-Registry Patterns")
        lines.append("- Standard --[implements]--> Database: A database uses this standard")
        lines.append("- Policy --[recommends]--> Standard: A policy recommends this standard")
        lines.append(
            "- Collection --[collects]--> Standard/Database: A collection includes this record"
        )
        lines.append("- Database --[shares_data_with]--> Database: Databases that exchange data")
        lines.append("- Standard --[extends]--> Standard: A standard builds on another")

        return "\n".join(lines)

    except FAIRsharingError as e:
        return f"Error fetching relationship types: {e}"


@app.mcp.tool(
    name="fairsharing_get_collection_contents",
    annotations=ToolAnnotations(
        readOnlyHint=True,
        idempotentHint=True,
        openWorldHint=True,
    ),
)
async def get_collection_contents(
    record_id: Annotated[int, Field(ge=1, description="FAIRsharing record ID")],
    output_format: Annotated[
        str,
        Field(
            default="markdown",
            pattern="^(markdown|json)$",
            description="Output format: 'markdown' or 'json'",
        ),
    ] = "markdown",
) -> str:
    """Get all records that belong to a specific FAIRsharing collection.

    Collections in FAIRsharing group related standards, databases, and policies.
    This tool retrieves all member records via the "collects" relationship.

    Args:
        record_id: The collection record ID
        output_format: "markdown" (default) or "json" for structured output

    Returns:
        List of records in the collection, grouped by registry and type
    """
    client = app.get_client()

    try:
        data = await client.query(GET_RECORD_WITH_ASSOCIATIONS_QUERY, {"id": record_id})
        record = data.get("fairsharingRecord")

        if not record:
            return f"No record found with ID {record_id}."

        name = record.get("name", "Unknown")
        abbrev = record.get("abbreviation", "")
        registry = record.get("registry", "Unknown")

        if registry.lower() != "collection":
            return f"Record {record_id} ({name}) is a {registry}, not a Collection. Use this tool with a collection record."

        # Collection contents are in outgoing "collects" associations
        outgoing = record.get("recordAssociations", [])
        collected: dict[str, dict[str, list]] = {}  # registry -> type -> [records]
        other_assocs: dict[str, list] = {}

        for a in outgoing:
            lr = a.get("linkedRecord", {})
            label = a.get("recordAssocLabel", "")
            if label == "collects":
                reg = lr.get("registry", "Unknown")
                rtype = lr.get("type", "unknown")
                collected.setdefault(reg, {}).setdefault(rtype, []).append(lr)
            else:
                other_assocs.setdefault(label, []).append(lr)

        total_collected = sum(
            len(items) for reg_types in collected.values() for items in reg_types.values()
        )

        if output_format == "json":
            records_list = []
            for reg_types in collected.values():
                for items in reg_types.values():
                    for item in items:
                        records_list.append(
                            {
                                "id": item.get("id"),
                                "name": item.get("name", "Unknown"),
                                "abbreviation": item.get("abbreviation", ""),
                                "registry": item.get("registry", "Unknown"),
                                "type": item.get("type", "unknown"),
                                "status": item.get("status", ""),
                            }
                        )
            return json.dumps(
                {
                    "collection_id": record_id,
                    "collection_name": name,
                    "abbreviation": abbrev,
                    "status": record.get("status", "N/A"),
                    "total_records": total_collected,
                    "records": records_list,
                },
                indent=2,
            )

        lines = [
            f"# Collection Contents: {name}" + (f" ({abbrev})" if abbrev else ""),
            f"**Status:** {record.get('status', 'N/A')}",
            "",
        ]

        if total_collected == 0:
            lines.append("_No records found in this collection._")
            # Check incoming as well
            incoming = record.get("reverseRecordAssociations", [])
            collected_in: list = []
            for a in incoming:
                lr = a.get("fairsharingRecord", {})
                label = a.get("recordAssocLabel", "")
                if label == "collects":
                    collected_in.append(lr)

            if collected_in:
                lines.append("")
                lines.append(
                    f"_Note: {len(collected_in)} records point TO this collection (incoming collects)._"
                )
        else:
            lines.append(f"**Total records:** {total_collected}")
            lines.append("")

            reg_plural = {
                "Standard": "Standards",
                "Database": "Databases",
                "Policy": "Policies",
                "Collection": "Collections",
            }
            for reg in ["Standard", "Database", "Policy", "Collection"]:
                if reg in collected:
                    reg_types = collected[reg]
                    reg_total = sum(len(v) for v in reg_types.values())
                    lines.append(f"## {reg_plural.get(reg, reg)} ({reg_total})")

                    for rtype in sorted(reg_types):
                        items = reg_types[rtype]
                        if len(reg_types) > 1:
                            lines.append(f"### {rtype} ({len(items)})")

                        for item in sorted(items, key=lambda x: x.get("name", "")):
                            iname = item.get("name", "Unknown")
                            iabbrev = item.get("abbreviation", "")
                            istatus = item.get("status", "")
                            iid = item.get("id", "")
                            entry = f"- **{iname}**"
                            if iabbrev:
                                entry += f" ({iabbrev})"
                            entry += f" (ID: {iid})"
                            if istatus and istatus != "ready":
                                entry += f" _{istatus}_"
                            lines.append(entry)
                    lines.append("")

            # Unknown registries
            for reg in sorted(collected):
                if reg not in ["Standard", "Database", "Policy", "Collection"]:
                    reg_types = collected[reg]
                    reg_total = sum(len(v) for v in reg_types.values())
                    lines.append(f"## {reg} ({reg_total})")
                    for rtype in sorted(reg_types):
                        items = reg_types[rtype]
                        for item in sorted(items, key=lambda x: x.get("name", "")):
                            iname = item.get("name", "Unknown")
                            iid = item.get("id", "")
                            lines.append(f"- {iname} (ID: {iid})")
                    lines.append("")

        # Show other relationships if any
        if other_assocs:
            lines.append("## Other Relationships")
            for label in sorted(other_assocs):
                items = other_assocs[label]
                lines.append(f"### {label} ({len(items)})")
                for item in items[:10]:
                    lines.append(
                        f"- {item.get('name', 'Unknown')} ({item.get('registry', '?')}, ID: {item.get('id', '?')})"
                    )
                if len(items) > 10:
                    lines.append(f"_(...and {len(items) - 10} more)_")
            lines.append("")

        return "\n".join(lines)

    except FAIRsharingError as e:
        return f"Error fetching collection contents: {e}"


@app.mcp.tool(
    name="fairsharing_trace_influence_chain",
    annotations=ToolAnnotations(
        readOnlyHint=True,
        idempotentHint=True,
        openWorldHint=True,
    ),
)
async def trace_influence_chain(
    record_id: Annotated[int, Field(ge=1, description="FAIRsharing record ID")],
    direction: Annotated[
        str,
        Field(
            default="downstream",
            pattern="^(upstream|downstream|both)$",
            description="Traversal direction",
        ),
    ] = "downstream",
    depth: Annotated[int, Field(default=2, ge=1, le=3, description="Traversal depth")] = 2,
    output_format: Annotated[
        str,
        Field(
            default="markdown",
            pattern="^(markdown|json)$",
            description="Output format: 'markdown' or 'json'",
        ),
    ] = "markdown",
) -> str:
    """Trace multi-hop influence chains for any record.

    Visualizes the ecosystem around a record by tracing relationships recursively.

    Args:
        record_id: Starting record ID.
        direction: "downstream" (who uses/recommends this?) or "upstream" (what does this use/recommend?).
        depth: Traversal depth (default: 2, max: 3).
        output_format: "markdown" (default) or "json" for structured output

    Returns:
        Text-based tree visualization of the influence chain.
    """
    client = app.get_client()
    depth = min(max(1, depth), 3)  # Enforce 1-3 depth

    try:
        root_data = await client.query(GET_RECORD_WITH_ASSOCIATIONS_QUERY, {"id": record_id})
        root = root_data.get("fairsharingRecord")
        if not root:
            return f"Record {record_id} not found."

        visited = set()
        visited.add(str(record_id))

        lines = [
            f"# Influence Trace ({direction.title()}, Depth {depth})",
            f"**Root:** {root.get('name')} ({root.get('registry')}) [ID: {record_id}]",
            "",
        ]

        # Collect chain data for JSON output
        chain_data: list[dict] = []

        async def fetch_neighbors(rid):
            try:
                d = await client.query(GET_RECORD_WITH_ASSOCIATIONS_QUERY, {"id": rid})
                return d.get("fairsharingRecord", {})
            except Exception as e:
                logger.warning(f"Error fetching neighbor {rid}: {e}")
                return None

        # Recursive trace function
        async def trace(current_id, current_name, current_depth, prefix=""):
            if current_depth >= depth:
                return

            rec = await fetch_neighbors(current_id)
            if not rec:
                return

            # Determine neighbors based on direction
            neighbors = []
            if direction == "downstream":
                # Reverse associations: Incoming links (e.g., A recommends This)
                for a in rec.get("reverseRecordAssociations", []):
                    neighbor = a.get("fairsharingRecord", {})
                    rel = a.get("recordAssocLabel", "related")
                    neighbors.append({"rec": neighbor, "rel": rel, "rel_display": f"<--[{rel}]--"})
            else:
                # Forward associations: Outgoing links (e.g., This recommends A)
                for a in rec.get("recordAssociations", []):
                    neighbor = a.get("linkedRecord", {})
                    rel = a.get("recordAssocLabel", "related")
                    neighbors.append({"rec": neighbor, "rel": rel, "rel_display": f"--[{rel}]-->"})

            # Process neighbors
            valid_neighbors = [n for n in neighbors if n["rec"].get("id")]

            # Limit branching factor to avoid huge output
            max_branch = 10

            for i, n in enumerate(valid_neighbors[:max_branch]):
                n_rec = n["rec"]
                n_id = n_rec.get("id")
                n_name = n_rec.get("name")
                n_reg = n_rec.get("registry")
                relation = n["rel_display"]

                chain_data.append(
                    {
                        "source_id": current_id,
                        "source_name": current_name,
                        "target_id": n_id,
                        "target_name": n_name,
                        "target_registry": n_reg,
                        "relationship": n["rel"],
                        "depth": current_depth + 1,
                    }
                )

                is_last = i == len(valid_neighbors[:max_branch]) - 1
                connector = "\u2514\u2500\u2500 " if is_last else "\u251c\u2500\u2500 "

                lines.append(f"{prefix}{connector}{relation} {n_name} ({n_reg}) [ID: {n_id}]")

                # Recurse
                new_prefix = prefix + ("    " if is_last else "\u2502   ")
                if (
                    str(n_id) not in visited
                ):  # Avoid cycles in tree print (DFS path check ideally, but set is safer)
                    visited.add(str(n_id))
                    await trace(n_id, n_name, current_depth + 1, new_prefix)
                    visited.remove(
                        str(n_id)
                    )  # Allow valid multipath, just not loops in current stack

            if len(valid_neighbors) > max_branch:
                connector = "\u2514\u2500\u2500 "  # approximate
                lines.append(f"{prefix}{connector}... ({len(valid_neighbors) - max_branch} more)")

        await trace(record_id, root.get("name"), 0)

        if output_format == "json":
            return json.dumps(
                {
                    "record_id": record_id,
                    "record_name": root.get("name"),
                    "record_registry": root.get("registry"),
                    "direction": direction,
                    "depth": depth,
                    "chain": chain_data,
                },
                indent=2,
            )

        return "\n".join(lines)

    except FAIRsharingError as e:
        return f"Error tracing influence: {e}"


@app.mcp.tool(
    name="fairsharing_detect_circular_dependencies",
    annotations=ToolAnnotations(
        readOnlyHint=True,
        idempotentHint=True,
        openWorldHint=True,
    ),
)
async def detect_circular_dependencies(
    record_id: Annotated[int, Field(ge=1, description="FAIRsharing record ID")],
    depth: Annotated[int, Field(default=3, ge=1, le=5, description="Traversal depth")] = 3,
    output_format: Annotated[
        str,
        Field(
            default="markdown",
            pattern="^(markdown|json)$",
            description="Output format: 'markdown' or 'json'",
        ),
    ] = "markdown",
) -> str:
    """Detect logical loops in the knowledge graph starting from a record.

    Performs a Depth-First Search (DFS) to find if the starting record (or any
    record in its path) is reachable from itself, indicating a circular dependency.

    Args:
        record_id: The ID of the record to check.
        depth: Maximum depth to traverse (default: 3).
        output_format: "markdown" (default) or "json" for structured output

    Returns:
        Report indicating whether a cycle was found and the path of the cycle.
    """
    client = app.get_client()
    depth = min(max(1, depth), 10)  # Cap depth at 10

    try:
        # DFS Stack: (current_id, path_of_ids, path_of_names)
        stack = [(str(record_id), [str(record_id)], [])]

        # We need to fetch the initial name for the report
        try:
            from fairsharing_mcp.queries import GET_RECORD_QUERY

            init_data = await client.query(GET_RECORD_QUERY, {"id": record_id})
            init_name = init_data.get("fairsharingRecord", {}).get("name", "Unknown")
            stack[0] = (str(record_id), [str(record_id)], [init_name])
        except Exception as e:
            return f"Could not fetch initial record {record_id}: {e}"

        cycles_found = []
        cycles_structured = []

        while stack:
            curr_id, path_ids, path_names = stack.pop()

            if len(path_ids) > depth:
                continue

            # Fetch associations
            try:
                data = await client.query(GET_RECORD_WITH_ASSOCIATIONS_QUERY, {"id": int(curr_id)})
                rec = data.get("fairsharingRecord", {})
            except Exception as e:
                logger.warning(f"Error processing record {curr_id}: {e}")
                continue

            # Check outgoing associations
            # We treat "upstream" (reverse) and "downstream" (forward) equally for cycle detection
            # depending on what we define as a dependency.
            # Usually: A implements B implies dependency A -> B.
            # So let's follow outgoing links (recordAssociations).

            neighbors = []
            for a in rec.get("recordAssociations", []):
                lr = a.get("linkedRecord", {})
                if lr.get("id"):
                    neighbors.append(lr)

            for n in neighbors:
                n_id = str(n.get("id"))
                n_name = n.get("name", "Unknown")

                if n_id in path_ids:
                    # Cycle detected!
                    cycle_path = path_ids + [n_id]
                    cycle_names = path_names + [n_name]

                    # Format cycle
                    formatted_cycle = " -> ".join(
                        [f"{name} [{id}]" for name, id in zip(cycle_names, cycle_path)]
                    )
                    cycles_found.append(formatted_cycle)
                    cycles_structured.append(
                        [{"id": cid, "name": cname} for cid, cname in zip(cycle_path, cycle_names)]
                    )
                else:
                    if len(path_ids) < depth:
                        stack.append((n_id, path_ids + [n_id], path_names + [n_name]))

        if output_format == "json":
            return json.dumps(
                {
                    "record_id": record_id,
                    "depth": depth,
                    "cycles_found": len(cycles_structured),
                    "cycles": cycles_structured,
                },
                indent=2,
            )

        if cycles_found:
            return (
                f"# Circular Dependencies Detected\n\nFound {len(cycles_found)} cycle(s):\n\n"
                + "\n\n".join([f"- {c}" for c in cycles_found])
            )
        else:
            return f"No circular dependencies detected starting from record {record_id} (Depth {depth})."

    except FAIRsharingError as e:
        return f"Error detecting cycles: {e}"
