"""FAIRsharing MCP tools — Curator and metadata audit operations."""

import asyncio
import json
import logging
from typing import Annotated

from mcp.types import ToolAnnotations
from pydantic import Field

from fairsharing_mcp import app
from fairsharing_mcp.client import FAIRsharingError
from fairsharing_mcp.formatters import escape_md_table
from fairsharing_mcp.queries import GET_RECORD_WITH_ASSOCIATIONS_QUERY, SEARCH_RECORDS_QUERY

logger = logging.getLogger(__name__)


def _audit_record_completeness(record: dict) -> dict:
    """Helper to audit a record dictionary for metadata completeness.

    Args:
        record: The record dictionary (from GraphQL).

    Returns:
        Dict with score and missing fields.
    """
    registry = record.get("registry", "Unknown")

    # Define checklist based on Registry
    common_required = ["name", "description", "subjects", "domains"]
    common_recommended = [
        "homepage",
        "abbreviation",
        "licenceLinks",
        "publications",
        "organisations",
    ]

    specific_required = []
    specific_recommended = []

    if registry == "Standard":
        specific_recommended = ["doi"]
    elif registry == "Database":
        # Note: FAIR indicator fields (dataAccessCondition, dataPreservationPolicy, etc.)
        # are NOT fetched by GET_RECORD_WITH_ASSOCIATIONS_QUERY and would always show
        # as missing. Use get_database_quality_profile for FAIR indicator auditing.
        specific_recommended = ["taxonomies"]
    elif registry == "Policy":
        specific_recommended = ["countries", "organisations", "recordAssociations"]
    elif registry == "Collection":
        specific_required = ["recordAssociations"]
        specific_recommended = ["subjects", "domains"]

    required_checklist = common_required + specific_required
    recommended_checklist = common_recommended + specific_recommended

    missing_required = []
    missing_recommended = []
    present_count = 0
    total_checks = len(required_checklist) + len(recommended_checklist)

    for field in required_checklist:
        val = record.get(field)
        if not val or (isinstance(val, list) and len(val) == 0):
            missing_required.append(field)
        else:
            present_count += 1

    for field in recommended_checklist:
        val = record.get(field)
        if not val or (isinstance(val, list) and len(val) == 0):
            missing_recommended.append(field)
        else:
            present_count += 1

    score = (present_count / total_checks) * 100 if total_checks > 0 else 0

    return {
        "score": score,
        "missing_required": missing_required,
        "missing_recommended": missing_recommended,
        "registry": registry,
        "name": record.get("name", "Unknown"),
    }


@app.mcp.tool(
    name="fairsharing_audit_metadata_completeness",
    annotations=ToolAnnotations(
        readOnlyHint=True,
        idempotentHint=True,
        openWorldHint=True,
    ),
)
async def audit_metadata_completeness(
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
    """Audit a record for missing critical and recommended metadata.

    Generates a scorecard checking for fields required or recommended
    for FAIR compliance and high-quality curation. The checklist is based
    on fields returned by the record associations query; some database
    FAIR indicator fields (e.g. dataAccessCondition) and policy mandate
    fields are not requested in that query, so the audit may list them as
    missing even when the API was not asked for them. For database FAIR
    indicators use get_database_quality_profile; for policy mandates use
    get_policy_details.

    Args:
        record_id: The ID of the record to audit.
        output_format: Output format: "markdown" (default) for human-readable output,
            "json" for machine-readable structured data suitable for programmatic chaining.

    Returns:
        Mark down scorecard listing missing fields and completeness score.
    """
    client = app.get_client()

    try:
        data = await client.query(GET_RECORD_WITH_ASSOCIATIONS_QUERY, {"id": record_id})
        record = data.get("fairsharingRecord")

        if not record:
            return f"No record found with ID {record_id}."

        audit = _audit_record_completeness(record)

        if output_format == "json":
            return json.dumps(
                {
                    "record_id": record_id,
                    "name": audit["name"],
                    "registry": audit["registry"],
                    "score": round(audit["score"], 1),
                    "missing_required": audit["missing_required"],
                    "missing_recommended": audit["missing_recommended"],
                },
                indent=2,
            )

        lines = [
            f"# Metadata Audit: {audit['name']}",
            f"**Registry:** {audit['registry']} | **Score:** {audit['score']:.1f}%",
            "",
            "## Critical Issues (Missing Required Fields)",
        ]

        if audit["missing_required"]:
            for f in audit["missing_required"]:
                lines.append(f"- [ ] Missing `{f}`")
        else:
            lines.append("✅ All required fields present.")

        lines.append("")
        lines.append("## Improvement Opportunities (Missing Recommended Fields)")

        if audit["missing_recommended"]:
            for f in audit["missing_recommended"]:
                lines.append(f"- [ ] Consider adding `{f}`")
        else:
            lines.append("✅ All recommended fields present.")

        return "\n".join(lines)

    except FAIRsharingError as e:
        return f"Error auditing metadata: {e}"


@app.mcp.tool(
    name="fairsharing_batch_audit_metadata",
    annotations=ToolAnnotations(
        readOnlyHint=True,
        idempotentHint=True,
        openWorldHint=True,
    ),
)
async def batch_audit_metadata(
    query: Annotated[str, Field(default="*", max_length=500, description="Search query")] = "*",
    registry: Annotated[
        list[str] | None,
        Field(default=None, description="Registry filter (Standard, Database, Policy)"),
    ] = None,
    limit: Annotated[
        int, Field(default=10, ge=1, le=25, description="Maximum records to return")
    ] = 10,
    min_year: Annotated[
        int | None,
        Field(default=None, ge=1990, le=2030, description="Minimum creation year (inclusive)"),
    ] = None,
    max_year: Annotated[
        int | None,
        Field(default=None, ge=1990, le=2030, description="Maximum creation year (inclusive)"),
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
    """Audit metadata completeness for multiple records.

    Useful for identifying records that need curation in a specific domain or registry.
    Uses the same checklist as audit_metadata_completeness (based on the record
    associations query; see that tool's docstring for scope limits).

    Args:
        query: Search query (default: "*").
        registry: Optional filter (Standard, Database, Policy).
        limit: Max records to audit (default: 10, max: 25).
        min_year: Minimum creation year (inclusive). Client-side filter.
        max_year: Maximum creation year (inclusive). Client-side filter.

    Returns:
        Markdown table summarizing audit scores and missing fields.
    """
    client = app.get_client()

    limit = min(max(1, limit), 25)

    if min_year and max_year and min_year > max_year:
        return f"Error: min_year ({min_year}) cannot be greater than max_year ({max_year})."

    has_date_filter = min_year is not None or max_year is not None

    try:
        # When date filtering, fetch more candidates to compensate for filtering
        fetch_limit = limit * 5 if has_date_filter else limit

        # Search for records
        vars = {
            "q": query if query != "*" else None,
            "registry": registry,
            "page": 1,
            "perPage": min(fetch_limit, 50),
        }
        data = await client.query(SEARCH_RECORDS_QUERY, vars)
        search_results = data.get("searchFairsharingRecords", {}).get("records", [])

        if not search_results:
            return "No records found matching criteria."

        # Apply date filter if specified
        if has_date_filter:
            filtered = []
            for rec in search_results:
                date_str = rec.get("createdAt")
                if not date_str:
                    continue
                try:
                    year = int(date_str[:4])
                    if min_year and year < min_year:
                        continue
                    if max_year and year > max_year:
                        continue
                    filtered.append(rec)
                except (ValueError, IndexError):
                    continue
            search_results = filtered[:limit]

            if not search_results:
                year_range = f"{min_year or '...'}-{max_year or '...'}"
                return f"No records found matching criteria in year range {year_range}."

        # Audit each record — fetch full details in parallel chunks (chunk size 5)
        results = []
        failed_count = 0
        total_attempted = 0
        chunk_size = 5
        for i in range(0, len(search_results), chunk_size):
            chunk = search_results[i : i + chunk_size]
            total_attempted += len(chunk)
            chunk_data = await asyncio.gather(
                *[
                    client.query(GET_RECORD_WITH_ASSOCIATIONS_QUERY, {"id": s.get("id")})
                    for s in chunk
                ],
                return_exceptions=True,
            )
            for d_data in chunk_data:
                if isinstance(d_data, Exception):
                    logger.error(f"Error fetching details: {d_data}")
                    failed_count += 1
                    continue
                full_record = d_data.get("fairsharingRecord")
                if full_record:
                    audit = _audit_record_completeness(full_record)
                    results.append(audit)

        if output_format == "json":
            return json.dumps(
                {
                    "query": query,
                    "registry": registry,
                    "total_attempted": total_attempted,
                    "failed_count": failed_count,
                    "results": [
                        {
                            "name": r["name"],
                            "registry": r["registry"],
                            "score": round(r["score"], 1),
                            "missing_required": r["missing_required"],
                            "missing_recommended": r["missing_recommended"],
                        }
                        for r in results
                    ],
                },
                indent=2,
            )

        # Format output
        header = f"# Batch Metadata Audit (Query: '{query}', Registry: {registry or 'All'}"
        if has_date_filter:
            header += f", Years: {min_year or '...'}-{max_year or '...'}"
        header += ")"

        lines = [
            header,
            "",
        ]

        if failed_count > 0:
            lines.append(
                f"**Warning:** {failed_count} of {total_attempted} record(s) "
                f"could not be fetched and are excluded from results."
            )
            lines.append("")

        lines.extend(
            [
                "| Record | Registry | Score | Missing Required | Missing Recommended |",
                "|--------|----------|-------|------------------|---------------------|",
            ]
        )

        for r in results:
            missing_req = ", ".join(r["missing_required"]) if r["missing_required"] else "None"
            missing_rec = (
                ", ".join(r["missing_recommended"]) if r["missing_recommended"] else "None"
            )
            # Truncate for table
            if len(missing_req) > 30:
                missing_req = missing_req[:27] + "..."
            if len(missing_rec) > 30:
                missing_rec = missing_rec[:27] + "..."

            name = escape_md_table(r["name"])
            lines.append(
                f"| {name} | {r['registry']} | {r['score']:.1f}% | {missing_req} | {missing_rec} |"
            )

        return "\n".join(lines)

    except FAIRsharingError as e:
        return f"Error executing batch audit: {e}"
