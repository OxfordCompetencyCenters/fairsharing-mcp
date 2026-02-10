"""FAIRsharing MCP tools — Organisations, countries, and regional analysis."""

import asyncio
import json
import logging
from collections import Counter
from typing import Annotated

from mcp.types import ToolAnnotations
from pydantic import Field

from fairsharing_mcp import app, config
from fairsharing_mcp.client import FAIRsharingError
from fairsharing_mcp.constants import POLICY_MANDATE_FIELDS
from fairsharing_mcp.formatters import format_record_summary
from fairsharing_mcp.helpers import fetch_policy_with_fallback
from fairsharing_mcp.queries import (
    LIST_COUNTRIES_QUERY,
    LIST_ORGANISATIONS_QUERY,
    SEARCH_ORGANISATIONS_QUERY,
    SEARCH_RECORDS_COMPACT_QUERY,
    SEARCH_RECORDS_QUERY,
)

logger = logging.getLogger(__name__)


def _org_to_dict(o: dict) -> dict:
    countries = o.get("countries", [])
    return {
        "id": o.get("id"),
        "name": o.get("name", "Unknown"),
        "homepage": o.get("homepage", ""),
        "countries": [c.get("name", "") for c in (countries or []) if c.get("name")],
    }


@app.mcp.tool(
    name="fairsharing_list_organisations",
    annotations=ToolAnnotations(
        readOnlyHint=True,
        idempotentHint=True,
        openWorldHint=True,
    ),
)
async def list_organisations(
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
    """List organisations in FAIRsharing.

    Args:
        page: Page number (default: 1)
        per_page: Results per page (default: 50, max: 100)
        bypass_cache: If True, fetch fresh data from the API (default: use 5-min cache).
        output_format: Output format: "markdown" (default) or "json".

    Returns:
        List of organisations
    """
    client = app.get_client()
    per_page = min(max(1, per_page), 100)
    page = max(1, page)

    try:
        data = await client.query(
            LIST_ORGANISATIONS_QUERY,
            {"page": page, "perPage": per_page},
            cache=not bypass_cache,
        )
        result = data.get("organisations", {})
        records = result.get("records", [])
        total_count = result.get("totalCount", 0)
        total_pages = result.get("totalPages", 0)

        if not records:
            return "No organisations found."

        if output_format == "json":
            return json.dumps(
                {
                    "page": page,
                    "total_pages": total_pages,
                    "total_count": total_count,
                    "organisations": [_org_to_dict(o) for o in records],
                },
                indent=2,
            )

        lines = [
            f"## Organisations (Page {page} of {total_pages}, Total: {total_count})",
            f"Showing {len(records)} of {total_count} on this page.",
            "",
        ]

        for o in records:
            name = o.get("name", "Unknown")
            oid = o.get("id", "N/A")
            homepage = o.get("homepage", "")
            countries = o.get("countries", [])
            country_names = [c.get("name", "") for c in (countries or []) if c.get("name")]
            line = f"- **{name}** (ID: {oid})"
            if country_names:
                line += f" - {', '.join(country_names[:3])}"
            if homepage:
                line += f" [{homepage}]"
            lines.append(line)

        if page < total_pages:
            lines.append("")
            lines.append(f"_Use page={page + 1} to see more organisations._")

        return "\n".join(lines)

    except FAIRsharingError as e:
        return f"Error listing organisations: {e}"


@app.mcp.tool(
    name="fairsharing_search_organisations",
    annotations=ToolAnnotations(
        readOnlyHint=True,
        idempotentHint=True,
        openWorldHint=True,
    ),
)
async def search_organisations(
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
    """Search organisations by text.

    Args:
        query: Search text (organisation name)
        output_format: Output format: "markdown" (default) or "json".

    Returns:
        List of matching organisations
    """
    client = app.get_client()

    if not query or not query.strip():
        return "Please provide a search query."

    try:
        data = await client.query(SEARCH_ORGANISATIONS_QUERY, {"q": query}, cache=True)
        records = data.get("searchOrganisations", [])

        if not records:
            return f"No organisations found matching '{query}'."

        if output_format == "json":
            return json.dumps(
                {
                    "query": query,
                    "total": len(records),
                    "organisations": [_org_to_dict(o) for o in records],
                },
                indent=2,
            )

        lines = [
            f"## Organisation Search Results for '{query}' ({len(records)} found)",
            "",
        ]

        for o in records:
            name = o.get("name", "Unknown")
            oid = o.get("id", "N/A")
            homepage = o.get("homepage", "")
            countries = o.get("countries", [])
            country_names = [c.get("name", "") for c in (countries or []) if c.get("name")]
            line = f"- **{name}** (ID: {oid})"
            if country_names:
                line += f" - {', '.join(country_names[:3])}"
            if homepage:
                line += f" [{homepage}]"
            lines.append(line)

        return "\n".join(lines)

    except FAIRsharingError as e:
        return f"Error searching organisations: {e}"


@app.mcp.tool(
    name="fairsharing_get_records_by_organisation",
    annotations=ToolAnnotations(
        readOnlyHint=True,
        idempotentHint=True,
        openWorldHint=True,
    ),
)
async def get_records_by_organisation(
    organisation: Annotated[str, Field(min_length=1, description="Organisation name")],
    registry: Annotated[
        list[str] | None,
        Field(default=None, description="Registry filter (Standard, Database, Policy)"),
    ] = None,
    page: Annotated[int, Field(default=1, ge=1, description="Page number")] = 1,
    per_page: Annotated[int, Field(default=20, ge=1, le=50, description="Results per page")] = 20,
    output_format: Annotated[
        str,
        Field(
            default="markdown",
            pattern="^(markdown|json)$",
            description="Output format: 'markdown' or 'json'",
        ),
    ] = "markdown",
) -> str:
    """List FAIRsharing records associated with a specific organisation.

    Use this for "what records does organisation X curate?" or "resources from
    EMBL-EBI". Organisation name is matched by the API (use search_organisations
    first if unsure of the exact name).

    Args:
        organisation: Organisation name (e.g., "EMBL-EBI", "University of Oxford").
        registry: Optional filter by registry: ["Database"], ["Standard"], ["Policy"], ["Collection"].
        page: Page number (default: 1).
        per_page: Results per page (default: 20, max: 50).
        output_format: Output format: "markdown" (default) or "json".

    Returns:
        Formatted list of records associated with the organisation.
    """
    if not organisation or not organisation.strip():
        return "Please provide an organisation name."

    client = app.get_client()
    per_page = min(max(1, per_page), config.get_max_per_page())
    page = max(1, page)

    variables: dict = {
        "organisations": [organisation.strip()],
        "page": page,
        "perPage": per_page,
    }
    if registry:
        variables["registry"] = registry

    try:
        data = await client.query(SEARCH_RECORDS_QUERY, variables)
        result = data.get("searchFairsharingRecords", {})
        records = result.get("records", [])
        total_count = result.get("totalCount", 0)
        total_pages = result.get("totalPages", 0)

        if not records:
            return f"No records found for organisation '{organisation.strip()}'." + (
                f" (Registry filter: {registry})" if registry else ""
            )

        if output_format == "json":
            return json.dumps(
                {
                    "organisation": organisation.strip(),
                    "page": page,
                    "total_pages": total_pages,
                    "total_count": total_count,
                    "records": [
                        {
                            "id": r.get("id"),
                            "name": r.get("name"),
                            "abbreviation": r.get("abbreviation", ""),
                            "registry": r.get("registry"),
                            "type": r.get("type", ""),
                        }
                        for r in records
                    ],
                },
                indent=2,
            )

        lines = [
            f"## Records for organisation: {organisation.strip()}",
            f"(Page {page} of {total_pages}, Total: {total_count:,} records)",
            f"Showing {len(records)} of {total_count:,} on this page.",
            "",
        ]
        for i, record in enumerate(records, 1):
            lines.append(f"### {i}. " + format_record_summary(record).lstrip("### "))
            lines.append("")

        if page < total_pages:
            lines.append(f"_Use page={page + 1} to see more results._")
        if config.get_truncation_warning():
            lines.append("")
            lines.append(f"_Results may be truncated; total available: {total_count:,}._")

        return "\n".join(lines)

    except FAIRsharingError as e:
        return f"Error fetching records by organisation: {e}"


@app.mcp.tool(
    name="fairsharing_list_countries",
    annotations=ToolAnnotations(
        readOnlyHint=True,
        idempotentHint=True,
        openWorldHint=True,
    ),
)
async def list_countries(
    page: Annotated[int, Field(default=1, ge=1, description="Page number")] = 1,
    per_page: Annotated[
        int, Field(default=100, ge=1, le=250, description="Results per page")
    ] = 100,
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
    """List all countries in FAIRsharing.

    Args:
        page: Page number (default: 1)
        per_page: Results per page (default: 100, max: 250)
        bypass_cache: If True, fetch fresh data from the API (default: use 5-min cache).
        output_format: Output format: "markdown" (default) or "json".

    Returns:
        List of countries with codes
    """
    client = app.get_client()
    per_page = min(max(1, per_page), 250)
    page = max(1, page)

    try:
        data = await client.query(
            LIST_COUNTRIES_QUERY,
            {"page": page, "perPage": per_page},
            cache=not bypass_cache,
        )
        result = data.get("countries", {})
        records = result.get("records", [])
        total_count = result.get("totalCount", 0)
        total_pages = result.get("totalPages", 0)

        if not records:
            return "No countries found."

        if output_format == "json":
            return json.dumps(
                {
                    "page": page,
                    "total_pages": total_pages,
                    "total_count": total_count,
                    "countries": [
                        {"id": c.get("id"), "name": c.get("name"), "code": c.get("code", "")}
                        for c in records
                    ],
                },
                indent=2,
            )

        lines = [
            f"## Countries (Page {page} of {total_pages}, Total: {total_count})",
            f"Showing {len(records)} of {total_count} on this page.",
            "",
        ]

        for c in records:
            name = c.get("name", "Unknown")
            code = c.get("code", "N/A")
            cid = c.get("id", "N/A")
            lines.append(f"- **{name}** ({code}) - ID: {cid}")

        if page < total_pages:
            lines.append("")
            lines.append(f"_Use page={page + 1} to see more countries._")

        return "\n".join(lines)

    except FAIRsharingError as e:
        return f"Error listing countries: {e}"


@app.mcp.tool(
    name="fairsharing_analyze_country_landscape",
    annotations=ToolAnnotations(
        readOnlyHint=True,
        idempotentHint=True,
        openWorldHint=True,
    ),
)
async def analyze_country_landscape(
    country: Annotated[str, Field(min_length=1, description="Country name")],
    subject: Annotated[str | None, Field(default=None, description="Subject filter")] = None,
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
    """Comprehensive profile of a SINGLE country's presence in FAIRsharing.

    Single-call tool that provides a full country overview: resource counts by
    registry, top records per registry, policy mandate summary, and coverage
    assessment. Replaces the need for 10+ separate tool calls.

    IMPORTANT: For comparing multiple countries side-by-side, use
    analyze_regional_distribution instead — it accepts a list of countries in
    one call and is far more efficient than calling this tool multiple times
    in parallel.

    Args:
        country: Country name (e.g., "Ireland", "United Kingdom", "Germany")
        subject: Optional subject filter (e.g., "Genomics") to restrict the
                 profile to resources in a specific scientific domain
        include_deprecated: Include deprecated records (default: False)
        output_format: Output format: "markdown" (default) or "json".

    Returns:
        Full country profile with registry overview, top records, and policy analysis
    """
    client = app.get_client()

    try:
        registries = ["Standard", "Database", "Policy", "Collection"]
        reg_plural = {
            "Standard": "Standards",
            "Database": "Databases",
            "Policy": "Policies",
            "Collection": "Collections",
        }

        # Fetch counts and top records per registry in parallel
        async def fetch_registry(reg: str):
            variables: dict = {
                "countries": [country],
                "registry": [reg],
                "page": 1,
                "perPage": 10,
            }
            if subject:
                variables["subjects"] = [subject]
            if not include_deprecated:
                variables["status"] = ["ready"]
            data = await client.query(SEARCH_RECORDS_COMPACT_QUERY, variables)
            result = data.get("searchFairsharingRecords", {})
            return reg, {
                "total": result.get("totalCount", 0),
                "records": result.get("records", []),
            }

        results = await asyncio.gather(*(fetch_registry(reg) for reg in registries))
        registry_data = {reg: data for reg, data in results}

        total_all = sum(rd["total"] for rd in registry_data.values())

        if output_format == "json":
            reg_json = {}
            for reg in registries:
                rd = registry_data[reg]
                reg_json[reg] = {
                    "total": rd["total"],
                    "top_records": [
                        {
                            "id": r.get("id"),
                            "name": r.get("name"),
                            "abbreviation": r.get("abbreviation", ""),
                            "type": r.get("type", ""),
                        }
                        for r in rd["records"]
                    ],
                }
            return json.dumps(
                {
                    "country": country,
                    "subject": subject,
                    "include_deprecated": include_deprecated,
                    "total": total_all,
                    "registries": reg_json,
                },
                indent=2,
            )

        lines = [
            f"# Country Profile: {country}",
        ]
        if subject:
            lines.append(f"**Subject filter:** {subject}")
        lines.extend(
            [
                "",
                "## Overview",
                f"**Total resources:** {total_all:,}"
                + (" (active only)" if not include_deprecated else ""),
                "",
            ]
        )

        # Registry summary table
        lines.append("| Registry | Count |")
        lines.append("|----------|-------|")
        for reg in registries:
            cnt = registry_data[reg]["total"]
            lines.append(f"| {reg_plural[reg]} | {cnt:,} |")
        lines.append(f"| **Total** | {total_all:,} |")
        lines.append("")

        # Top records per registry
        for reg in registries:
            rd = registry_data[reg]
            if rd["records"]:
                lines.append(f"## Top {reg_plural[reg]} ({rd['total']:,} total)")
                for i, rec in enumerate(rd["records"], 1):
                    name = rec.get("name", "Unknown")
                    abbrev = rec.get("abbreviation", "")
                    rec_type = rec.get("type", "")
                    rec_id = rec.get("id", "")
                    entry = f"{i}. **{name}**"
                    if abbrev:
                        entry += f" ({abbrev})"
                    if rec_type:
                        entry += f" [{rec_type}]"
                    entry += f" (ID: {rec_id})"
                    lines.append(entry)
                if rd["total"] > 10:
                    lines.append(f"_(...and {rd['total'] - 10} more)_")
                lines.append("")

        # Policy mandate summary (if policies exist)
        policy_records = registry_data["Policy"]["records"]
        if policy_records:
            lines.append("## Policy Mandate Summary")

            policy_details = []
            for prec in policy_records[:5]:
                pid = prec.get("id")
                if pid:
                    detail = await fetch_policy_with_fallback(int(pid))
                    if detail:
                        policy_details.append(detail)

            if policy_details:
                has_mandate_data = any(
                    p.get(f) is not None for f in POLICY_MANDATE_FIELDS for p in policy_details
                )

                if has_mandate_data:
                    mandate_display = {
                        "mandatedDataSharing": "Data Sharing",
                        "sharingResearchSoftware": "Software Sharing",
                        "mandatedDmpCreation": "DMP Creation",
                        "metadataSharing": "Metadata Sharing",
                    }
                    n_policies = len(policy_details)

                    lines.append(f"_Based on {n_policies} policies analyzed:_")
                    lines.append("")
                    lines.append("| Area | Required | Suggested | Not Covered |")
                    lines.append("|------|----------|-----------|-------------|")

                    for field, label in mandate_display.items():
                        counts = Counter(
                            str(p.get(field, "unknown")).lower() for p in policy_details
                        )
                        required = counts.get("required", 0)
                        suggested = counts.get("suggested", 0)
                        not_covered = counts.get("not covered", 0)
                        lines.append(f"| {label} | {required} | {suggested} | {not_covered} |")
                    lines.append("")
                else:
                    lines.append("_Mandate fields not available from the API for these policies._")
                    lines.append("")

                # Policy types
                type_counts = Counter(p.get("type", "unknown") for p in policy_details)
                lines.append(
                    "**Policy types:** "
                    + ", ".join(f"{t}={c}" for t, c in type_counts.most_common())
                )
                lines.append("")

        # Coverage assessment
        lines.append("## Coverage Assessment")
        std_count = registry_data["Standard"]["total"]
        db_count = registry_data["Database"]["total"]
        pol_count = registry_data["Policy"]["total"]

        if std_count == 0:
            lines.append("- **Standards:** No standards registered - potential gap")
        elif std_count < 5:
            lines.append(f"- **Standards:** Limited ({std_count})")
        else:
            lines.append(f"- **Standards:** Good coverage ({std_count})")

        if db_count == 0:
            lines.append("- **Databases:** No databases registered - potential gap")
        elif db_count < 5:
            lines.append(f"- **Databases:** Limited ({db_count})")
        else:
            lines.append(f"- **Databases:** Good coverage ({db_count})")

        if pol_count == 0:
            lines.append("- **Policies:** No policies found")
        else:
            lines.append(f"- **Policies:** {pol_count} policies registered")

        if std_count > 0 and db_count > 0:
            ratio = db_count / std_count
            lines.append(f"- **DB-to-Standard ratio:** {ratio:.1f}")

        return "\n".join(lines)

    except FAIRsharingError as e:
        return f"Error analyzing country landscape: {e}"


@app.mcp.tool(
    name="fairsharing_analyze_regional_distribution",
    annotations=ToolAnnotations(
        readOnlyHint=True,
        idempotentHint=True,
        openWorldHint=True,
    ),
)
async def analyze_regional_distribution(
    regions: Annotated[list[str], Field(min_length=1, description="List of region names")],
    subject: Annotated[str | None, Field(default=None, description="Subject filter")] = None,
    output_format: Annotated[
        str,
        Field(
            default="markdown",
            pattern="^(markdown|json)$",
            description="Output format: 'markdown' or 'json'",
        ),
    ] = "markdown",
) -> str:
    """Compare resource counts across multiple countries in one call.

    PREFERRED tool for multi-country comparison. Accepts a list of countries and
    queries them sequentially, returning a side-by-side matrix of Databases,
    Standards, Policies, and Collections per country. Much more efficient and
    reliable than calling analyze_country_landscape for each country separately.

    Use this when the question involves comparing, ranking, or summarizing
    multiple countries (e.g., "Compare the UK, US, Germany, and France").

    Args:
        regions: List of country names (e.g., ["United Kingdom", "United States", "France"])
        subject: Optional subject filter (e.g., "Genomics") to restrict counts
                 to resources in a specific scientific domain
        output_format: Output format: "markdown" (default) or "json".

    Returns:
        Comparative matrix of resource counts per region with totals.
    """
    client = app.get_client()

    registries = ["Database", "Standard", "Policy", "Collection"]

    grand_totals = {reg: 0 for reg in registries}
    rows: list[dict] = []
    failed_regions: list[str] = []

    async def fetch_region_registry(region: str, reg: str):
        try:
            variables: dict = {
                "countries": [region],
                "registry": [reg],
                "status": ["ready"],
                "page": 1,
                "perPage": 1,
            }
            if subject:
                variables["subjects"] = [subject]
            data = await client.query(SEARCH_RECORDS_QUERY, variables)
            total = data.get("searchFairsharingRecords", {}).get("totalCount", 0)
            return (region, reg, total, None)
        except Exception as e:
            return (region, reg, 0, e)

    tasks = [fetch_region_registry(region, reg) for region in regions for reg in registries]
    results = await asyncio.gather(*tasks)

    for region in regions:
        counts = {reg: 0 for reg in registries}
        region_failed = False
        for r, reg, total, err in results:
            if r != region:
                continue
            counts[reg] = total
            if err is not None:
                logger.warning(f"Error fetching counts for {reg} in {region}: {err}")
                region_failed = True

        if region_failed:
            failed_regions.append(region)
        total = sum(counts.values())
        for reg in registries:
            grand_totals[reg] += counts[reg]

        rows.append({"region": region, **counts, "total": total})

    if output_format == "json":
        return json.dumps(
            {
                "subject": subject,
                "regions": rows,
                "grand_totals": {**grand_totals, "total": sum(grand_totals.values())},
                "failed_regions": failed_regions,
            },
            indent=2,
        )

    lines = ["# Regional Distribution Analysis"]
    if subject:
        lines.append(f"**Subject filter:** {subject}")
        lines.append("")
    lines.extend(
        [
            "| Region | Databases | Standards | Policies | Collections | Total |",
            "|--------|-----------|-----------|----------|-------------|-------|",
        ]
    )

    for row in rows:
        lines.append(
            f"| {row['region']} | {row['Database']} | {row['Standard']} "
            f"| {row['Policy']} | {row['Collection']} | {row['total']} |"
        )

    lines.append("")
    lines.append(
        f"**Grand Total:** {sum(grand_totals.values())} records processed across {len(regions)} regions."
    )

    # Warn about regions with zero results
    empty_regions = [
        r["region"] for r in rows if r["total"] == 0 and r["region"] not in failed_regions
    ]
    if empty_regions:
        lines.append("")
        for er in empty_regions:
            lines.append(
                f"**Warning: {er} has 0 records in FAIRsharing.** "
                f"Check spelling — country names must match exactly "
                f"(e.g., 'United Kingdom' not 'UK', 'United States' not 'USA')."
            )

    # Report partial failures
    if failed_regions:
        lines.append("")
        lines.append(
            f"**Note:** Some queries failed for: {', '.join(failed_regions)}. "
            f"Counts shown may be incomplete. This is often caused by API rate limits — "
            f"try again in a moment."
        )

    return "\n".join(lines)
