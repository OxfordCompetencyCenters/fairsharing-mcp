"""FAIRsharing MCP tools — Individual record operations."""

import asyncio
import json
import logging
from collections import Counter

from fairsharing_mcp import app, config
from fairsharing_mcp.client import FAIRsharingError
from fairsharing_mcp.formatters import (
    escape_md_table,
    format_record_detail,
    format_record_summary,
)
from fairsharing_mcp.graph_utils import GraphParseError, parse_graph
from fairsharing_mcp.helpers import matches_date_range
from fairsharing_mcp.queries import (
    GET_GRAPH_QUERY,
    GET_RECORD_QUERY,
    GET_RECORD_TYPES_QUERY,
    GET_RECORD_WITH_ASSOCIATIONS_QUERY,
    SEARCH_RECORDS_QUERY,
)
from fairsharing_mcp.validation import validate_record_id

logger = logging.getLogger(__name__)


@app.mcp.tool()
async def get_record(record_id: int, output_format: str = "markdown") -> str:
    """Get detailed information about a specific FAIRsharing record.

    Args:
        record_id: The FAIRsharing record ID (integer)
        output_format: Output format: "markdown" (default) for human-readable output,
            "json" for machine-readable structured data suitable for programmatic chaining.

    Returns:
        Comprehensive record details including relationships, publications, etc.
    """
    try:
        record_id = validate_record_id(record_id)
    except ValueError as e:
        return f"Validation error: {e}"

    client = app.get_client()

    try:
        data = await client.query(GET_RECORD_QUERY, {"id": record_id})
        record = data.get("fairsharingRecord")

        if not record:
            return f"No record found with ID {record_id}."

        if output_format == "json":
            # Return the raw API record data as structured JSON
            return json.dumps(
                {
                    "id": record.get("id"),
                    "name": record.get("name"),
                    "abbreviation": record.get("abbreviation"),
                    "registry": record.get("registry"),
                    "type": record.get("type"),
                    "status": record.get("status"),
                    "doi": record.get("doi"),
                    "homepage": record.get("homepage"),
                    "description": record.get("description"),
                    "created_at": record.get("createdAt"),
                    "updated_at": record.get("updatedAt"),
                    "subjects": [
                        s.get("label") for s in record.get("subjects", []) if s.get("label")
                    ],
                    "domains": [
                        d.get("label") for d in record.get("domains", []) if d.get("label")
                    ],
                    "taxonomies": [
                        t.get("name") for t in record.get("taxonomies", []) if t.get("name")
                    ],
                    "countries": [
                        c.get("name") for c in record.get("countries", []) if c.get("name")
                    ],
                    "licence_links": [
                        {
                            "name": ll.get("licence", {}).get("name"),
                            "url": ll.get("licence", {}).get("url"),
                        }
                        for ll in record.get("licenceLinks", [])
                        if ll.get("licence")
                    ],
                    "publications": [
                        {
                            "title": p.get("title"),
                            "doi": p.get("doi"),
                            "year": p.get("year"),
                        }
                        for p in record.get("publications", [])
                    ],
                },
                indent=2,
            )

        out = format_record_detail(record)
        if config.get_truncation_warning():
            out += "\n\n_Display of nested lists (e.g. associations, subjects) may be truncated._"
        return out

    except FAIRsharingError as e:
        return f"Error fetching record: {e}"


@app.mcp.tool()
async def get_record_graph(
    record_id: int, summary_mode: bool = False, output_format: str = "markdown"
) -> str:
    """Get and analyze the knowledge graph around a record.

    Returns a structural analysis of the network: node counts by registry/type/status,
    hub nodes (most connected), edge distribution, and the record's direct neighbors.
    The graph can contain thousands of nodes and edges spanning multiple registries.

    Args:
        record_id: The FAIRsharing record ID (integer)
        summary_mode: If True, output is condensed (top 10 hubs, no full neighbor lists)
            to reduce context size for very large graphs. Use when graph has 500+ nodes.
        output_format: Output format: "markdown" (default) for human-readable output,
            "json" for machine-readable structured data suitable for programmatic chaining.

    Returns:
        Analyzed graph summary with hub nodes, distribution stats, and direct connections
    """
    try:
        record_id = validate_record_id(record_id)
    except ValueError as e:
        return f"Validation error: {e}"

    client = app.get_client()

    try:
        data = await client.query(GET_GRAPH_QUERY, {"id": record_id})
        graph_container = data.get("fairsharingGraph", {})
        graph_data = graph_container.get("data")

        if not graph_data:
            return f"No graph data available for record ID {record_id}."

        try:
            parsed = parse_graph(graph_data)
        except GraphParseError as e:
            return f"Error parsing graph data for record ID {record_id}: {e}"

        node_map = {
            k: {
                "label": n.label,
                "registry": n.registry,
                "record_type": n.record_type,
                "status": n.status,
            }
            for k, n in parsed.nodes.items()
        }
        graph_registry = (
            parsed.nodes.get(str(record_id)).registry
            if str(record_id) in parsed.nodes
            else "Unknown"
        )

        n_nodes = len(parsed.nodes)
        n_edges = len(parsed.edges)
        use_summary = summary_mode or (n_nodes > 500)

        if output_format == "json":
            # Build degree map for hub data
            node_degree: Counter = Counter()
            for s, t, _ in parsed.edges:
                node_degree[s] += 1
                node_degree[t] += 1

            source_key = str(record_id)
            direct_out = [
                {"target": t, "relationship": rel} for s, t, rel in parsed.edges if s == source_key
            ]
            direct_in = [
                {"source": s, "relationship": rel} for s, t, rel in parsed.edges if t == source_key
            ]

            return json.dumps(
                {
                    "record_id": record_id,
                    "name": parsed.name,
                    "registry": graph_registry,
                    "node_count": n_nodes,
                    "edge_count": n_edges,
                    "nodes": node_map,
                    "edges": [
                        {"source": s, "target": t, "relationship": rel}
                        for s, t, rel in parsed.edges
                    ],
                    "registry_distribution": dict(
                        Counter(n["registry"] for n in node_map.values())
                    ),
                    "record_type_distribution": dict(
                        Counter(n["record_type"] for n in node_map.values())
                    ),
                    "status_distribution": dict(Counter(n["status"] for n in node_map.values())),
                    "relationship_distribution": dict(Counter(rel for _, _, rel in parsed.edges)),
                    "top_hubs": [
                        {
                            "node_id": nid,
                            "label": node_map.get(nid, {}).get("label", nid),
                            "registry": node_map.get(nid, {}).get("registry", "?"),
                            "degree": deg,
                        }
                        for nid, deg in node_degree.most_common(20)
                    ],
                    "direct_connections": {
                        "outgoing": direct_out,
                        "incoming": direct_in,
                    },
                },
                indent=2,
            )

        lines = [
            f"# Knowledge Graph: {parsed.name}",
            f"**Registry:** {graph_registry}",
            f"**Network Size:** {n_nodes:,} nodes, {n_edges:,} edges",
            "",
        ]
        if use_summary:
            lines.append("_(Summary mode — condensed output for large graph.)_")
            lines.append("")

        # --- Registry distribution ---
        registries = Counter(n["registry"] for n in node_map.values())
        lines.append("## Node Distribution by Registry")
        for reg, count in registries.most_common():
            lines.append(f"- **{reg.title()}:** {count:,}")
        lines.append("")

        # --- Record type distribution ---
        record_types = Counter(n["record_type"] for n in node_map.values())
        lines.append("## Node Distribution by Record Type")
        max_types = 5 if use_summary else 10
        for rt, count in record_types.most_common(max_types):
            lines.append(f"- **{rt}:** {count:,}")
        if len(record_types) > max_types:
            lines.append(f"- _({len(record_types) - max_types} more types)_")
        lines.append("")

        # --- Status distribution ---
        statuses = Counter(n["status"] for n in node_map.values())
        lines.append("## Status Distribution")
        for st, count in statuses.most_common():
            lines.append(f"- **{st}:** {count:,}")
        lines.append("")

        # --- Edge distribution (relationship types) ---
        rel_meaning = {
            "implements": "database uses standard (implements)",
            "related_to": "standard-standard or cross-type (related_to)",
            "collects": "collection membership (collects)",
            "recommends": "policy references (recommends)",
            "extends": "extension (extends)",
            "deprecates": "deprecation/self",
            "shares_data_with": "shares data with",
            "other": "other relationship",
            "outputs": "outputs",
            "profiles": "profiles",
        }
        rel_counts = Counter(rel for _, _, rel in parsed.edges)
        lines.append("## Edge Distribution (Relationship Types)")
        for rel, count in rel_counts.most_common():
            meaning = rel_meaning.get(rel, rel)
            lines.append(f"- **{meaning}**: {count:,}")
        lines.append("")

        # --- Hub analysis ---
        node_degree: Counter = Counter()
        for s, t, _ in parsed.edges:
            node_degree[s] += 1
            node_degree[t] += 1

        hub_count = 10 if use_summary else 20
        lines.append(f"## Top {hub_count} Hub Nodes (Most Connected)")
        for node_id, degree in node_degree.most_common(hub_count):
            info = node_map.get(node_id, {})
            label = info.get("label", node_id)
            reg = info.get("registry", "?")
            lines.append(f"- **{label}** (ID: {node_id}, {reg}): {degree} connections")
        lines.append("")

        # --- Direct neighbors of the queried record ---
        source_key = str(record_id)
        direct_out = [(t, rel) for s, t, rel in parsed.edges if s == source_key]
        direct_in = [(s, rel) for s, t, rel in parsed.edges if t == source_key]

        if direct_out or direct_in:
            lines.append(f"## Direct Connections of Record {record_id}")
            lines.append(f"- **Outgoing edges:** {len(direct_out)}")
            lines.append(f"- **Incoming edges:** {len(direct_in)}")
            lines.append("")

            if not use_summary:
                if direct_out:
                    out_by_reg: dict[str, list] = {}
                    for target_key, _ in direct_out:
                        target_info = node_map.get(target_key, {})
                        reg = target_info.get("registry", "unknown")
                        out_by_reg.setdefault(reg, []).append(target_info.get("label", target_key))
                    lines.append("### Outgoing by Registry")
                    for reg in sorted(out_by_reg):
                        items = out_by_reg[reg]
                        lines.append(f"- **{reg.title()}** ({len(items)}): {', '.join(items[:10])}")
                        if len(items) > 10:
                            lines.append(f"  _(...and {len(items) - 10} more)_")
                    lines.append("")

                if direct_in:
                    in_by_reg: dict[str, list] = {}
                    for source_key_in, _ in direct_in:
                        source_info = node_map.get(source_key_in, {})
                        reg = source_info.get("registry", "unknown")
                        in_by_reg.setdefault(reg, []).append(
                            source_info.get("label", source_key_in)
                        )
                    lines.append("### Incoming by Registry")
                    for reg in sorted(in_by_reg):
                        items = in_by_reg[reg]
                        lines.append(f"- **{reg.title()}** ({len(items)}): {', '.join(items[:10])}")
                        if len(items) > 10:
                            lines.append(f"  _(...and {len(items) - 10} more)_")
                    lines.append("")
            else:
                lines.append("_(Use summary_mode=False for full neighbor lists.)_")
                lines.append("")

        return "\n".join(lines)

    except FAIRsharingError as e:
        return f"Error fetching graph: {e}"


@app.mcp.tool()
async def get_record_types(bypass_cache: bool = False, output_format: str = "markdown") -> str:
    """Get all record types with descriptions.

    Record types are more specific classifications within each registry,
    such as "Knowledgebase", "Terminology artefact", "Model/format", etc.

    Args:
        bypass_cache: If True, fetch fresh data from the API (default: use 5-min cache).
        output_format: Output format: "markdown" (default) for human-readable output,
            "json" for machine-readable structured data suitable for programmatic chaining.

    Returns:
        List of record types organized by registry
    """
    client = app.get_client()

    try:
        data = await client.query(GET_RECORD_TYPES_QUERY, cache=not bypass_cache)
        result = data.get("recordTypes", {})
        record_types = result.get("records", [])

        if not record_types:
            return "No record type information available."

        # Group by registry
        by_registry: dict[str, list] = {}
        for rt in record_types:
            reg = rt.get("fairsharingRegistry", {})
            registry = reg.get("name", "Other") if reg else "Other"
            if registry not in by_registry:
                by_registry[registry] = []
            by_registry[registry].append(rt)

        if output_format == "json":
            return json.dumps(
                {
                    "record_types": [
                        {
                            "name": rt.get("name", "Unknown"),
                            "description": rt.get("description", ""),
                            "registry": (
                                rt.get("fairsharingRegistry", {}).get("name", "Other")
                                if rt.get("fairsharingRegistry")
                                else "Other"
                            ),
                        }
                        for rt in record_types
                    ],
                },
                indent=2,
            )

        lines = [
            "## FAIRsharing Record Types",
            "",
        ]

        for registry, types in sorted(by_registry.items()):
            lines.append(f"### {registry}")
            for rt in types:
                name = rt.get("name", "Unknown")
                desc = rt.get("description", "")
                lines.append(f"- **{name}**" + (f": {desc}" if desc else ""))
            lines.append("")

        return "\n".join(lines)

    except FAIRsharingError as e:
        return f"Error fetching record types: {e}"


@app.mcp.tool()
async def filter_records_by_date(
    query: str = "*",
    min_year: int | None = None,
    max_year: int | None = None,
    registry: list[str] | None = None,
    limit: int = 10,
    use_updated_at: bool = False,
    max_scan: int | None = None,
    output_format: str = "markdown",
) -> str:
    """Filter records by creation or update year (client-side filtering).

    Since the API does not support date range queries, this tool searches
    for records and filters them locally. Scanning is best-effort up to
    max_scan records (default from FAIRSHARING_MAX_SCAN, typically 2000).

    Args:
        query: Search query (default: "*").
        min_year: Minimum year (inclusive).
        max_year: Maximum year (inclusive).
        registry: Optional filter (Standard, Database, Policy).
        limit: Max records to return (default: 10, max: 200).
        use_updated_at: If True, filter by 'updatedAt' instead of 'createdAt'.
        max_scan: Maximum records to scan (default: from env, cap from FAIRSHARING_MAX_SCAN).

    Returns:
        List of matching records with their dates.
    """
    client = app.get_client()

    # Validate years
    if min_year and max_year and min_year > max_year:
        return f"Error: min_year ({min_year}) cannot be greater than max_year ({max_year})."

    limit = min(max(1, limit), 200)
    _max_scan = config.get_max_scan()
    max_scan = min(max(50, max_scan or _max_scan), _max_scan)
    per_page = config.get_max_per_page()
    max_scan_pages = max_scan // per_page

    try:
        matches = []
        page = 1
        total_scanned = 0
        skipped_parse = 0

        while len(matches) < limit and page <= max_scan_pages:
            vars = {
                "q": query if query != "*" else None,
                "registry": registry,
                "page": page,
                "perPage": per_page,
            }

            data = await client.query(SEARCH_RECORDS_QUERY, vars)
            result = data.get("searchFairsharingRecords", {})
            records = result.get("records", [])

            if not records:
                break

            total_scanned += len(records)

            date_field = "updatedAt" if use_updated_at else "createdAt"
            for rec in records:
                date_str = rec.get(date_field)
                if not matches_date_range(date_str, min_year, max_year):
                    continue

                try:
                    year = int(date_str[:4])
                except (ValueError, IndexError):
                    skipped_parse += 1
                    continue

                matches.append(
                    {
                        "name": rec.get("name"),
                        "id": rec.get("id"),
                        "year": year,
                        "registry": rec.get("registry"),
                        "date_type": "Updated" if use_updated_at else "Created",
                    }
                )

                if len(matches) >= limit:
                    break

            page += 1

        # Count index matches for additional context
        date_field = "updatedAt" if use_updated_at else "createdAt"
        index_size = 0
        index_date_matches = 0
        if hasattr(client, "_date_index"):
            index_size = len(client._date_index)
            if index_size > 0 and (min_year is not None or max_year is not None):
                for _rid, dates in client._date_index.items():
                    if matches_date_range(dates.get(date_field), min_year, max_year):
                        index_date_matches += 1

        if output_format == "json":
            return json.dumps(
                {
                    "query": query,
                    "min_year": min_year,
                    "max_year": max_year,
                    "date_type": date_field,
                    "total_scanned": total_scanned,
                    "total_matches": len(matches),
                    "date_index_size": index_size,
                    "date_index_matches": index_date_matches,
                    "records": matches,
                },
                indent=2,
            )

        lines = [
            f"# Date Filter Results (Query: '{query}', Range: {min_year}-{max_year}, Type: {'Updated' if use_updated_at else 'Created'})",
            f"Evaluated {total_scanned} records, found {len(matches)} matches.",
            "",
            "| Year | Date Type | Record | Registry | ID |",
            "|------|-----------|--------|----------|----|",
        ]
        if config.get_truncation_warning() and total_scanned > 0:
            lines.insert(2, f"Scanned {total_scanned:,} records (scan limit: {max_scan:,}).")
            lines.insert(3, "")

        for m in matches:
            lines.append(
                f"| {m['year']} | {m['date_type']} | {m['name']} | {m['registry']} | {m['id']} |"
            )

        if not matches:
            lines.append(
                f"\n_No records matched the date criteria in the scanned sample ({total_scanned} records)._"
            )
        if skipped_parse > 0:
            lines.append("")
            lines.append(f"_Skipped {skipped_parse} records due to parse errors._")
        if config.get_truncation_warning() and total_scanned >= max_scan:
            lines.append("")
            lines.append(f"_Scan limited to {max_scan} records; more may exist._")
        if index_size > 0:
            lines.append("")
            lines.append(
                f"_Date index: {index_date_matches:,} of {index_size:,} indexed records "
                f"match the date range._"
            )

        return "\n".join(lines)

    except FAIRsharingError as e:
        return f"Error filtering by date: {e}"


@app.mcp.tool()
async def get_records_batch(
    record_ids: list[int],
    output_format: str = "markdown",
) -> str:
    """Fetch multiple records by ID in a single call.

    Retrieves 2-20 records in parallel (chunked to avoid rate limit bursts)
    and returns them as a combined summary. Use this instead of calling
    get_record in a loop.

    Args:
        record_ids: List of 2-20 FAIRsharing record IDs.
        output_format: "markdown" (default) or "json" for structured data.

    Returns:
        Combined record summaries or JSON array of record data.
    """
    if len(record_ids) < 2:
        return "Please provide at least 2 record IDs."
    if len(record_ids) > 20:
        return "Please provide at most 20 record IDs."

    client = app.get_client()
    results: list[dict | None] = []
    failed_ids: list[int] = []
    chunk_size = 5

    try:
        for i in range(0, len(record_ids), chunk_size):
            chunk = record_ids[i : i + chunk_size]
            chunk_data = await asyncio.gather(
                *[client.query(GET_RECORD_QUERY, {"id": rid}) for rid in chunk],
                return_exceptions=True,
            )
            for rid, data in zip(chunk, chunk_data):
                if isinstance(data, Exception):
                    logger.warning(f"Failed to fetch record {rid}: {data}")
                    failed_ids.append(rid)
                    results.append(None)
                else:
                    record = data.get("fairsharingRecord")
                    if record:
                        results.append(record)
                    else:
                        failed_ids.append(rid)
                        results.append(None)

        fetched = [r for r in results if r is not None]

        if not fetched:
            return "No records could be retrieved for the given IDs."

        if output_format == "json":
            json_records = []
            for record in fetched:
                json_records.append(
                    {
                        "id": record.get("id"),
                        "name": record.get("name"),
                        "abbreviation": record.get("abbreviation"),
                        "registry": record.get("registry"),
                        "type": record.get("type"),
                        "status": record.get("status"),
                        "doi": record.get("doi"),
                        "homepage": record.get("homepage"),
                        "description": record.get("description"),
                        "created_at": record.get("createdAt"),
                        "updated_at": record.get("updatedAt"),
                    }
                )
            return json.dumps({"records": json_records, "failed_ids": failed_ids}, indent=2)

        lines = [
            f"# Batch Record Retrieval ({len(fetched)} of {len(record_ids)} fetched)",
            "",
        ]
        if failed_ids:
            lines.append(
                f"**Warning:** Could not fetch {len(failed_ids)} record(s): "
                f"{', '.join(str(i) for i in failed_ids)}"
            )
            lines.append("")

        for record in fetched:
            lines.append(format_record_summary(record))
            lines.append("")

        return "\n".join(lines)

    except FAIRsharingError as e:
        return f"Error fetching records batch: {e}"


@app.mcp.tool()
async def find_referencing_records(
    record_id: int,
    relationship: str | None = None,
    registry: str | None = None,
    output_format: str = "markdown",
) -> str:
    """Find records that reference (point to) a given record — reverse lookup.

    Answers questions like "which databases implement this standard?",
    "which policies recommend this database?", or "who collects this record?".

    Uses the record's reverse associations. For outgoing associations
    (what this record points to), use get_record instead.

    Args:
        record_id: The FAIRsharing record ID to look up.
        relationship: Optional filter by relationship label (case-insensitive
            substring match). Common labels include "implements", "recommends",
            "collects", "related to", "extends", "deprecates".
        registry: Optional filter by referencing record's registry
            ("Standard", "Database", "Policy", "Collection").
        output_format: "markdown" (default) or "json" for structured data.

    Returns:
        List of records that reference the target, grouped by relationship type.
    """
    try:
        record_id = validate_record_id(record_id)
    except ValueError as e:
        return f"Validation error: {e}"

    client = app.get_client()

    try:
        data = await client.query(GET_RECORD_WITH_ASSOCIATIONS_QUERY, {"id": record_id})
        record = data.get("fairsharingRecord")
        if not record:
            return f"No record found with ID {record_id}."

        reverse_assocs = record.get("reverseRecordAssociations") or []

        # Apply filters
        filtered = []
        for assoc in reverse_assocs:
            ref_record = assoc.get("fairsharingRecord") or {}
            label = assoc.get("recordAssocLabel") or ""

            if relationship and relationship.lower() not in label.lower():
                continue
            if registry and (ref_record.get("registry") or "").lower() != registry.lower():
                continue

            filtered.append(
                {
                    "id": ref_record.get("id"),
                    "name": ref_record.get("name", "Unknown"),
                    "abbreviation": ref_record.get("abbreviation", ""),
                    "registry": ref_record.get("registry", "Unknown"),
                    "type": ref_record.get("type", ""),
                    "status": ref_record.get("status", ""),
                    "relationship": label,
                }
            )

        record_name = record.get("name", f"Record {record_id}")

        if output_format == "json":
            return json.dumps(
                {
                    "record_id": record_id,
                    "record_name": record_name,
                    "total_reverse_associations": len(reverse_assocs),
                    "filtered_count": len(filtered),
                    "filters": {
                        "relationship": relationship,
                        "registry": registry,
                    },
                    "referencing_records": filtered,
                },
                indent=2,
            )

        # Markdown output grouped by relationship
        lines = [
            f"# Records Referencing: {record_name} (ID: {record_id})",
            "",
        ]

        filter_parts = []
        if relationship:
            filter_parts.append(f"relationship='{relationship}'")
        if registry:
            filter_parts.append(f"registry='{registry}'")
        if filter_parts:
            lines.append(f"**Filters:** {', '.join(filter_parts)}")

        lines.append(f"**Found:** {len(filtered)} of {len(reverse_assocs)} reverse associations")
        lines.append("")

        if not filtered:
            lines.append("_No matching reverse associations found._")
            return "\n".join(lines)

        # Group by relationship label
        by_rel: dict[str, list[dict]] = {}
        for item in filtered:
            rel = item["relationship"] or "Unknown"
            by_rel.setdefault(rel, []).append(item)

        for rel_label, items in sorted(by_rel.items()):
            lines.append(f"## {rel_label} ({len(items)})")
            lines.append("")
            lines.append("| Record | Registry | Type | Status | ID |")
            lines.append("|--------|----------|------|--------|----|")
            for item in items:
                name = escape_md_table(item["name"])
                abbr = item["abbreviation"]
                display = f"{name} ({abbr})" if abbr else name
                lines.append(
                    f"| {display} | {item['registry']} | "
                    f"{item['type']} | {item['status']} | {item['id']} |"
                )
            lines.append("")

        return "\n".join(lines)

    except FAIRsharingError as e:
        return f"Error fetching reverse associations: {e}"
