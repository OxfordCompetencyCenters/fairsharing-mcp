"""FAIRsharing MCP tools — Subject, domain, and taxonomy browsing."""

import json
import logging
from collections import Counter
from typing import Annotated

from mcp.types import ToolAnnotations
from pydantic import Field

from fairsharing_mcp import app
from fairsharing_mcp.client import FAIRsharingError
from fairsharing_mcp.formatters import format_hierarchy_item
from fairsharing_mcp.queries import (
    BROWSE_SUBJECTS_QUERY,
    GET_DOMAIN_QUERY,
    GET_SUBJECT_QUERY,
    LIST_DOMAINS_QUERY,
    LIST_SUBJECTS_QUERY,
    LIST_TAXONOMIES_QUERY,
    SEARCH_DOMAINS_QUERY,
    SEARCH_RECORDS_QUERY,
    SEARCH_SUBJECTS_QUERY,
    SEARCH_TAXONOMIES_QUERY,
)

logger = logging.getLogger(__name__)


def _subject_to_dict(s: dict) -> dict:
    """Convert a subject record to a JSON-friendly dict."""
    return {
        "id": s.get("id"),
        "label": s.get("label", "Unknown"),
        "description": s.get("description", ""),
    }


def _domain_to_dict(d: dict) -> dict:
    """Convert a domain record to a JSON-friendly dict."""
    return {
        "id": d.get("id"),
        "label": d.get("label", "Unknown"),
        "description": d.get("description", ""),
    }


def _taxonomy_to_dict(t: dict) -> dict:
    """Convert a taxonomy record to a JSON-friendly dict."""
    return {
        "id": t.get("id"),
        "label": t.get("label", "Unknown"),
        "iri": t.get("iri", ""),
    }


def _hierarchy_to_dict(item: dict) -> dict:
    """Convert a hierarchy item (subject/domain) to a JSON-friendly dict."""
    result: dict = {
        "id": item.get("id"),
        "label": item.get("label", "Unknown"),
        "description": item.get("description", ""),
    }
    for rel in ("parents", "children", "ancestors"):
        entries = item.get(rel, [])
        if entries:
            result[rel] = [{"id": e.get("id"), "label": e.get("label")} for e in entries]
    return result


@app.mcp.tool(
    name="fairsharing_list_subjects",
    annotations=ToolAnnotations(
        readOnlyHint=True,
        idempotentHint=True,
        openWorldHint=True,
    ),
)
async def list_subjects(
    page: Annotated[int, Field(default=1, ge=1, description="Page number")] = 1,
    per_page: Annotated[int, Field(default=50, ge=1, le=100, description="Results per page")] = 50,
    bypass_cache: Annotated[
        bool, Field(default=False, description="If True, fetch fresh data from the API")
    ] = False,
    output_format: Annotated[
        str,
        Field(
            default="markdown",
            pattern="^(markdown|json)$",
            description="Output format: 'markdown' or 'json'",
        ),
    ] = "markdown",
) -> str:
    """List all scientific subjects (paginated).

    Subjects represent scientific disciplines like Genomics, Proteomics,
    Bioinformatics, etc. that classify FAIRsharing records.

    Args:
        page: Page number (default: 1)
        per_page: Results per page (default: 50, max: 100)
        bypass_cache: If True, fetch fresh data from the API (default: use 5-min cache).
        output_format: Output format: "markdown" (default) or "json".

    Returns:
        List of subjects with IDs and descriptions
    """
    client = app.get_client()
    per_page = min(max(1, per_page), 100)
    page = max(1, page)

    try:
        data = await client.query(
            LIST_SUBJECTS_QUERY,
            {"page": page, "perPage": per_page},
            cache=not bypass_cache,
        )
        result = data.get("subjects", {})
        records = result.get("records", [])
        total_count = result.get("totalCount", 0)
        total_pages = result.get("totalPages", 0)

        if not records:
            return "No subjects found."

        if output_format == "json":
            return json.dumps(
                {
                    "page": page,
                    "total_pages": total_pages,
                    "total_count": total_count,
                    "subjects": [_subject_to_dict(s) for s in records],
                },
                indent=2,
            )

        lines = [
            f"## Subjects (Page {page} of {total_pages}, Total: {total_count})",
            f"Showing {len(records)} of {total_count} on this page.",
            "",
        ]

        for s in records:
            label = s.get("label", "Unknown")
            sid = s.get("id", "N/A")
            desc = s.get("description", "")
            if desc and len(desc) > 100:
                desc = desc[:97] + "..."
            lines.append(f"- **{label}** (ID: {sid})")
            if desc:
                lines.append(f"  {desc}")

        if page < total_pages:
            lines.append("")
            lines.append(f"_Use page={page + 1} to see more subjects._")

        return "\n".join(lines)

    except FAIRsharingError as e:
        return f"Error listing subjects: {e}"


@app.mcp.tool(
    name="fairsharing_search_subjects",
    annotations=ToolAnnotations(
        readOnlyHint=True,
        idempotentHint=True,
        openWorldHint=True,
    ),
)
async def search_subjects(
    query: Annotated[str, Field(min_length=1, max_length=500, description="Search query")],
    output_format: Annotated[
        str,
        Field(
            default="markdown",
            pattern="^(markdown|json)$",
            description="Output format: 'markdown' or 'json'",
        ),
    ] = "markdown",
) -> str:
    """Search subjects by text.

    Args:
        query: Search text to find matching subjects
        output_format: Output format: "markdown" (default) or "json".

    Returns:
        List of matching subjects
    """
    client = app.get_client()

    if not query or not query.strip():
        return "Please provide a search query."

    try:
        data = await client.query(SEARCH_SUBJECTS_QUERY, {"q": query}, cache=True)
        records = data.get("searchSubjects", [])

        if not records:
            return f"No subjects found matching '{query}'."

        if output_format == "json":
            return json.dumps(
                {
                    "query": query,
                    "total": len(records),
                    "subjects": [_subject_to_dict(s) for s in records],
                },
                indent=2,
            )

        lines = [
            f"## Subject Search Results for '{query}' ({len(records)} found)",
            "",
        ]

        for s in records:
            label = s.get("label", "Unknown")
            sid = s.get("id", "N/A")
            desc = s.get("description", "")
            if desc and len(desc) > 100:
                desc = desc[:97] + "..."
            lines.append(f"- **{label}** (ID: {sid})")
            if desc:
                lines.append(f"  {desc}")

        return "\n".join(lines)

    except FAIRsharingError as e:
        return f"Error searching subjects: {e}"


@app.mcp.tool(
    name="fairsharing_get_subject",
    annotations=ToolAnnotations(
        readOnlyHint=True,
        idempotentHint=True,
        openWorldHint=True,
    ),
)
async def get_subject(
    subject_id: Annotated[int, Field(ge=1, description="Subject taxonomy ID")],
    output_format: Annotated[
        str,
        Field(
            default="markdown",
            pattern="^(markdown|json)$",
            description="Output format: 'markdown' or 'json'",
        ),
    ] = "markdown",
) -> str:
    """Get a subject with its hierarchy (parents, children, ancestors).

    Args:
        subject_id: The subject ID
        output_format: Output format: "markdown" (default) or "json".

    Returns:
        Subject details including hierarchical relationships
    """
    client = app.get_client()

    try:
        data = await client.query(GET_SUBJECT_QUERY, {"id": subject_id}, cache=True)
        subject = data.get("subject")

        if not subject:
            return f"No subject found with ID {subject_id}."

        if output_format == "json":
            return json.dumps({"subject": _hierarchy_to_dict(subject)}, indent=2)

        return format_hierarchy_item(subject, include_description=True)

    except FAIRsharingError as e:
        return f"Error fetching subject: {e}"


@app.mcp.tool(
    name="fairsharing_list_domains",
    annotations=ToolAnnotations(
        readOnlyHint=True,
        idempotentHint=True,
        openWorldHint=True,
    ),
)
async def list_domains(
    page: Annotated[int, Field(default=1, ge=1, description="Page number")] = 1,
    per_page: Annotated[int, Field(default=50, ge=1, le=100, description="Results per page")] = 50,
    bypass_cache: Annotated[
        bool, Field(default=False, description="If True, fetch fresh data from the API")
    ] = False,
    output_format: Annotated[
        str,
        Field(
            default="markdown",
            pattern="^(markdown|json)$",
            description="Output format: 'markdown' or 'json'",
        ),
    ] = "markdown",
) -> str:
    """List all technical domains (paginated).

    Domains represent technical classifications like "Data model",
    "Identifier schema", "File format", etc.

    Args:
        page: Page number (default: 1)
        per_page: Results per page (default: 50, max: 100)
        bypass_cache: If True, fetch fresh data from the API (default: use 5-min cache).
        output_format: Output format: "markdown" (default) or "json".

    Returns:
        List of domains with IDs and descriptions
    """
    client = app.get_client()
    per_page = min(max(1, per_page), 100)
    page = max(1, page)

    try:
        data = await client.query(
            LIST_DOMAINS_QUERY,
            {"page": page, "perPage": per_page},
            cache=not bypass_cache,
        )
        result = data.get("domains", {})
        records = result.get("records", [])
        total_count = result.get("totalCount", 0)
        total_pages = result.get("totalPages", 0)

        if not records:
            return "No domains found."

        if output_format == "json":
            return json.dumps(
                {
                    "page": page,
                    "total_pages": total_pages,
                    "total_count": total_count,
                    "domains": [_domain_to_dict(d) for d in records],
                },
                indent=2,
            )

        lines = [
            f"## Domains (Page {page} of {total_pages}, Total: {total_count})",
            f"Showing {len(records)} of {total_count} on this page.",
            "",
        ]

        for d in records:
            label = d.get("label", "Unknown")
            did = d.get("id", "N/A")
            desc = d.get("description", "")
            if desc and len(desc) > 100:
                desc = desc[:97] + "..."
            lines.append(f"- **{label}** (ID: {did})")
            if desc:
                lines.append(f"  {desc}")

        if page < total_pages:
            lines.append("")
            lines.append(f"_Use page={page + 1} to see more domains._")

        return "\n".join(lines)

    except FAIRsharingError as e:
        return f"Error listing domains: {e}"


@app.mcp.tool(
    name="fairsharing_search_domains",
    annotations=ToolAnnotations(
        readOnlyHint=True,
        idempotentHint=True,
        openWorldHint=True,
    ),
)
async def search_domains(
    query: Annotated[str, Field(min_length=1, max_length=500, description="Search query")],
    output_format: Annotated[
        str,
        Field(
            default="markdown",
            pattern="^(markdown|json)$",
            description="Output format: 'markdown' or 'json'",
        ),
    ] = "markdown",
) -> str:
    """Search domains by text.

    Args:
        query: Search text to find matching domains
        output_format: Output format: "markdown" (default) or "json".

    Returns:
        List of matching domains
    """
    client = app.get_client()

    if not query or not query.strip():
        return "Please provide a search query."

    try:
        data = await client.query(SEARCH_DOMAINS_QUERY, {"q": query}, cache=True)
        records = data.get("searchDomains", [])

        if not records:
            return f"No domains found matching '{query}'."

        if output_format == "json":
            return json.dumps(
                {
                    "query": query,
                    "total": len(records),
                    "domains": [_domain_to_dict(d) for d in records],
                },
                indent=2,
            )

        lines = [
            f"## Domain Search Results for '{query}' ({len(records)} found)",
            "",
        ]

        for d in records:
            label = d.get("label", "Unknown")
            did = d.get("id", "N/A")
            desc = d.get("description", "")
            if desc and len(desc) > 100:
                desc = desc[:97] + "..."
            lines.append(f"- **{label}** (ID: {did})")
            if desc:
                lines.append(f"  {desc}")

        return "\n".join(lines)

    except FAIRsharingError as e:
        return f"Error searching domains: {e}"


@app.mcp.tool(
    name="fairsharing_get_domain",
    annotations=ToolAnnotations(
        readOnlyHint=True,
        idempotentHint=True,
        openWorldHint=True,
    ),
)
async def get_domain(
    domain_id: Annotated[int, Field(ge=1, description="Domain taxonomy ID")],
    output_format: Annotated[
        str,
        Field(
            default="markdown",
            pattern="^(markdown|json)$",
            description="Output format: 'markdown' or 'json'",
        ),
    ] = "markdown",
) -> str:
    """Get a domain with its hierarchy (parents, children, ancestors).

    Args:
        domain_id: The domain ID
        output_format: Output format: "markdown" (default) or "json".

    Returns:
        Domain details including hierarchical relationships
    """
    client = app.get_client()

    try:
        data = await client.query(GET_DOMAIN_QUERY, {"id": domain_id}, cache=True)
        domain = data.get("domain")

        if not domain:
            return f"No domain found with ID {domain_id}."

        if output_format == "json":
            return json.dumps({"domain": _hierarchy_to_dict(domain)}, indent=2)

        return format_hierarchy_item(domain, include_description=True)

    except FAIRsharingError as e:
        return f"Error fetching domain: {e}"


@app.mcp.tool(
    name="fairsharing_list_taxonomies",
    annotations=ToolAnnotations(
        readOnlyHint=True,
        idempotentHint=True,
        openWorldHint=True,
    ),
)
async def list_taxonomies(
    page: Annotated[int, Field(default=1, ge=1, description="Page number")] = 1,
    per_page: Annotated[int, Field(default=50, ge=1, le=100, description="Results per page")] = 50,
    bypass_cache: Annotated[
        bool, Field(default=False, description="If True, fetch fresh data from the API")
    ] = False,
    output_format: Annotated[
        str,
        Field(
            default="markdown",
            pattern="^(markdown|json)$",
            description="Output format: 'markdown' or 'json'",
        ),
    ] = "markdown",
) -> str:
    """List taxonomies (species) used in FAIRsharing records.

    Args:
        page: Page number (default: 1)
        per_page: Results per page (default: 50, max: 100)
        bypass_cache: If True, fetch fresh data from the API (default: use 5-min cache).
        output_format: Output format: "markdown" (default) or "json".

    Returns:
        List of taxonomies/species
    """
    client = app.get_client()
    per_page = min(max(1, per_page), 100)
    page = max(1, page)

    try:
        data = await client.query(
            LIST_TAXONOMIES_QUERY,
            {"page": page, "perPage": per_page},
            cache=not bypass_cache,
        )
        result = data.get("taxonomies", {})
        records = result.get("records", [])
        total_count = result.get("totalCount", 0)
        total_pages = result.get("totalPages", 0)

        if not records:
            return "No taxonomies found."

        if output_format == "json":
            return json.dumps(
                {
                    "page": page,
                    "total_pages": total_pages,
                    "total_count": total_count,
                    "taxonomies": [_taxonomy_to_dict(t) for t in records],
                },
                indent=2,
            )

        lines = [
            f"## Taxonomies (Page {page} of {total_pages}, Total: {total_count})",
            f"Showing {len(records)} of {total_count} on this page.",
            "",
        ]

        for t in records:
            label = t.get("label", "Unknown")
            tid = t.get("id", "N/A")
            iri = t.get("iri", "")
            lines.append(f"- **{label}** (ID: {tid})" + (f" - {iri}" if iri else ""))

        if page < total_pages:
            lines.append("")
            lines.append(f"_Use page={page + 1} to see more taxonomies._")

        return "\n".join(lines)

    except FAIRsharingError as e:
        return f"Error listing taxonomies: {e}"


@app.mcp.tool(
    name="fairsharing_search_taxonomies",
    annotations=ToolAnnotations(
        readOnlyHint=True,
        idempotentHint=True,
        openWorldHint=True,
    ),
)
async def search_taxonomies(
    query: Annotated[str, Field(min_length=1, max_length=500, description="Search query")],
    output_format: Annotated[
        str,
        Field(
            default="markdown",
            pattern="^(markdown|json)$",
            description="Output format: 'markdown' or 'json'",
        ),
    ] = "markdown",
) -> str:
    """Search taxonomies (species) by text.

    Args:
        query: Search text (e.g., "Homo sapiens", "mouse", "arabidopsis")
        output_format: Output format: "markdown" (default) or "json".

    Returns:
        List of matching taxonomies
    """
    client = app.get_client()

    if not query or not query.strip():
        return "Please provide a search query."

    try:
        data = await client.query(SEARCH_TAXONOMIES_QUERY, {"q": query}, cache=True)
        records = data.get("searchTaxonomies", [])

        if not records:
            return f"No taxonomies found matching '{query}'."

        if output_format == "json":
            return json.dumps(
                {
                    "query": query,
                    "total": len(records),
                    "taxonomies": [_taxonomy_to_dict(t) for t in records],
                },
                indent=2,
            )

        lines = [
            f"## Taxonomy Search Results for '{query}' ({len(records)} found)",
            "",
        ]

        for t in records:
            label = t.get("label", "Unknown")
            tid = t.get("id", "N/A")
            iri = t.get("iri", "")
            lines.append(f"- **{label}** (ID: {tid})" + (f" - {iri}" if iri else ""))

        return "\n".join(lines)

    except FAIRsharingError as e:
        return f"Error searching taxonomies: {e}"


@app.mcp.tool(
    name="fairsharing_browse_subject_hierarchy",
    annotations=ToolAnnotations(
        readOnlyHint=True,
        idempotentHint=True,
        openWorldHint=True,
    ),
)
async def browse_subject_hierarchy(
    output_format: Annotated[
        str,
        Field(
            default="markdown",
            pattern="^(markdown|json)$",
            description="Output format: 'markdown' or 'json'",
        ),
    ] = "markdown",
) -> str:
    """Browse the hierarchical subject classification tree.

    Returns the top-level scientific subjects and their children,
    providing an overview of how FAIRsharing organizes scientific disciplines.

    Args:
        output_format: Output format: "markdown" (default) or "json".

    Returns:
        Hierarchical subject tree with record counts
    """
    client = app.get_client()

    try:
        data = await client.query(BROWSE_SUBJECTS_QUERY, cache=True)
        browse = data.get("browseSubjects", {})
        browse_data = browse.get("data")

        if isinstance(browse_data, str):
            browse_data = json.loads(browse_data)

        if not browse_data:
            return "No subject hierarchy available."

        if output_format == "json" and isinstance(browse_data, list):
            categories = []
            for top in browse_data:
                cat: dict = {
                    "id": top.get("id"),
                    "name": top.get("name"),
                    "description": top.get("description", ""),
                    "records_count": top.get("records_count", 0),
                    "children": [],
                }
                for child in top.get("children", []):
                    cat["children"].append(
                        {
                            "id": child.get("id"),
                            "name": child.get("name"),
                            "records_count": child.get("records_count", 0),
                            "children_count": len(child.get("children", [])),
                        }
                    )
                categories.append(cat)
            return json.dumps(
                {"total_categories": len(categories), "categories": categories}, indent=2
            )

        lines = [
            "# Subject Hierarchy",
            "",
        ]

        if isinstance(browse_data, list):
            lines.append(f"**Top-level categories:** {len(browse_data)}")
            lines.append("")

            for top_subject in browse_data:
                sname = top_subject.get("name", "Unknown")
                sid = top_subject.get("id", "")
                desc = top_subject.get("description", "")
                count = top_subject.get("records_count", 0)

                lines.append(f"## {sname} (ID: {sid})")
                if desc:
                    if len(desc) > 200:
                        desc = desc[:197] + "..."
                    lines.append(f"_{desc}_")
                if count:
                    lines.append(f"**Records:** {count:,}")

                children = top_subject.get("children", [])
                if children:
                    lines.append(f"**Sub-subjects ({len(children)}):**")
                    for child in sorted(children, key=lambda x: x.get("name", "")):
                        cname = child.get("name", "Unknown")
                        cid = child.get("id", "")
                        ccount = child.get("records_count", 0)
                        entry = f"- {cname} (ID: {cid})"
                        if ccount:
                            entry += f" - {ccount:,} records"

                        grandchildren = child.get("children", [])
                        if grandchildren:
                            entry += f" [{len(grandchildren)} sub-subjects]"
                        lines.append(entry)
                lines.append("")
        else:
            lines.append(f"Subject data format: {type(browse_data).__name__}")
            lines.append(str(browse_data)[:2000])

        return "\n".join(lines)

    except FAIRsharingError as e:
        return f"Error browsing subjects: {e}"


@app.mcp.tool(
    name="fairsharing_analyze_subject_landscape",
    annotations=ToolAnnotations(
        readOnlyHint=True,
        idempotentHint=True,
        openWorldHint=True,
    ),
)
async def analyze_subject_landscape(
    subject: Annotated[
        str, Field(min_length=1, description="Subject name (e.g., 'Genomics', 'Proteomics')")
    ],
    include_deprecated: Annotated[
        bool, Field(default=False, description="Include deprecated records")
    ] = False,
    output_format: Annotated[
        str,
        Field(
            default="markdown",
            pattern="^(markdown|json)$",
            description="Output format: 'markdown' or 'json'",
        ),
    ] = "markdown",
) -> str:
    """Analyze the resource landscape for a scientific subject.

    Shows how many standards, databases, policies, and collections exist for a subject,
    broken down by record type and status. Identifies gaps and coverage.

    Args:
        subject: Subject name (e.g., "Genomics", "Proteomics", "Bioinformatics")
        include_deprecated: Include deprecated records in analysis (default: False)
        output_format: Output format: "markdown" (default) or "json".

    Returns:
        Landscape analysis with registry breakdown, record types, and status distribution
    """
    client = app.get_client()

    try:
        # Fetch records by registry
        results = {}
        for reg in ["Standard", "Database", "Policy", "Collection"]:
            variables: dict = {
                "subjects": [subject],
                "registry": [reg],
                "perPage": 50,
                "page": 1,
            }
            if not include_deprecated:
                variables["status"] = ["ready"]

            data = await client.query(SEARCH_RECORDS_QUERY, variables)
            result = data.get("searchFairsharingRecords", {})
            results[reg] = {
                "total": result.get("totalCount", 0),
                "records": result.get("records", []),
            }

        total_all = sum(r["total"] for r in results.values())

        if output_format == "json":
            registries = {}
            for reg in ["Standard", "Database", "Policy", "Collection"]:
                recs = results[reg]["records"]
                types = Counter(r.get("type", "unknown") for r in recs)
                registries[reg] = {
                    "total": results[reg]["total"],
                    "types": dict(types.most_common()),
                    "top_records": [
                        {
                            "id": r.get("id"),
                            "name": r.get("name"),
                            "abbreviation": r.get("abbreviation", ""),
                            "type": r.get("type", ""),
                        }
                        for r in recs[:10]
                    ],
                }
            return json.dumps(
                {
                    "subject": subject,
                    "include_deprecated": include_deprecated,
                    "total": total_all,
                    "registries": registries,
                },
                indent=2,
            )

        lines = [
            f"# Subject Landscape: {subject}",
            "",
            "## Overview",
        ]

        lines.append(
            f"**Total resources:** {total_all:,}"
            + (" (active only)" if not include_deprecated else "")
        )
        lines.append("")

        reg_plural = {
            "Standard": "Standards",
            "Database": "Databases",
            "Policy": "Policies",
            "Collection": "Collections",
        }
        for reg in ["Standard", "Database", "Policy", "Collection"]:
            total = results[reg]["total"]
            records = results[reg]["records"]
            lines.append(f"### {reg_plural[reg]}: {total:,}")

            if records:
                # Group by type
                types = Counter(r.get("type", "unknown") for r in records)
                if types:
                    lines.append(
                        f"**By type:** {', '.join(f'{t}={c}' for t, c in types.most_common())}"
                    )

                # Show top records
                lines.append("**Top records (up to 10):**")
                for r in records[:10]:
                    name = r.get("name", "Unknown")
                    abbrev = r.get("abbreviation", "")
                    rec_type = r.get("type", "")
                    entry = f"  - {name}"
                    if abbrev:
                        entry += f" ({abbrev})"
                    if rec_type:
                        entry += f" [{rec_type}]"
                    entry += f" - ID: {r.get('id', '?')}"
                    lines.append(entry)
                if total > 10:
                    lines.append(f"  _(...and {total - 10} more)_")
            else:
                lines.append(f"_No {reg_plural[reg].lower()} found._")
            lines.append("")

        # Coverage assessment
        lines.append("## Coverage Assessment")
        std_count = results["Standard"]["total"]
        db_count = results["Database"]["total"]
        pol_count = results["Policy"]["total"]
        col_count = results["Collection"]["total"]

        if std_count == 0:
            lines.append("- **Standards:** No standards found - potential gap")
        elif std_count < 5:
            lines.append(f"- **Standards:** Limited coverage ({std_count} standards)")
        else:
            lines.append(f"- **Standards:** Good coverage ({std_count} standards)")

        if db_count == 0:
            lines.append("- **Databases:** No databases found - potential gap")
        elif db_count < 5:
            lines.append(f"- **Databases:** Limited coverage ({db_count} databases)")
        else:
            lines.append(f"- **Databases:** Good coverage ({db_count} databases)")

        if pol_count == 0:
            lines.append("- **Policies:** No policies found")
        else:
            lines.append(f"- **Policies:** {pol_count} policies reference this subject")

        if col_count > 0:
            lines.append(f"- **Collections:** {col_count} curated collections")

        # Ratio analysis
        if std_count > 0 and db_count > 0:
            ratio = db_count / std_count
            lines.append(f"- **DB-to-Standard ratio:** {ratio:.1f} (databases per standard)")

        return "\n".join(lines)

    except FAIRsharingError as e:
        return f"Error analyzing landscape: {e}"


@app.mcp.tool(
    name="fairsharing_analyze_taxonomy_landscape",
    annotations=ToolAnnotations(
        readOnlyHint=True,
        idempotentHint=True,
        openWorldHint=True,
    ),
)
async def analyze_taxonomy_landscape(
    taxonomies: Annotated[list[str], Field(min_length=1, description="List of taxonomy names")],
    output_format: Annotated[
        str,
        Field(
            default="markdown",
            pattern="^(markdown|json)$",
            description="Output format: 'markdown' or 'json'",
        ),
    ] = "markdown",
) -> str:
    """Compare resource coverage across different species/taxonomies.

    Args:
        taxonomies: List of species names (e.g., ["Homo sapiens", "Mus musculus"])
        output_format: Output format: "markdown" (default) or "json".

    Returns:
        Comparative breakdown of resources per species.
    """
    client = app.get_client()

    rows = []
    for tax in taxonomies:
        counts = {"Database": 0, "Standard": 0, "Policy": 0}

        for reg in ["Database", "Standard", "Policy"]:
            try:
                variables = {"taxonomies": [tax], "registry": [reg], "page": 1, "perPage": 1}
                data = await client.query(SEARCH_RECORDS_QUERY, variables)
                total = data.get("searchFairsharingRecords", {}).get("totalCount", 0)
                counts[reg] = total
            except Exception as e:
                logger.warning(f"Error fetching counts for {reg} in {tax}: {e}")
                pass

        total = sum(counts.values())
        rows.append({"taxonomy": tax, **counts, "total": total})

    if output_format == "json":
        return json.dumps({"taxonomies": rows}, indent=2)

    lines = [
        "# Taxonomy Landscape Analysis",
        "| Species | Databases | Standards | Policies | Total |",
        "|---------|-----------|-----------|----------|-------|",
    ]

    for row in rows:
        lines.append(
            f"| {row['taxonomy']} | {row['Database']} | {row['Standard']} | {row['Policy']} | {row['total']} |"
        )

    return "\n".join(lines)
