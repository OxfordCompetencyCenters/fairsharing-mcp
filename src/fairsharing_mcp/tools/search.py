"""FAIRsharing MCP tools — Search, count, and filter records."""

import json
import logging
import re
from typing import Annotated

from mcp.types import ToolAnnotations
from pydantic import Field

from fairsharing_mcp import app, config
from fairsharing_mcp.client import FAIRsharingError
from fairsharing_mcp.formatters import format_record_summary
from fairsharing_mcp.helpers import matches_date_range
from fairsharing_mcp.queries import (
    MULTI_TAG_FILTER_QUERY,
    SEARCH_RECORDS_COMPACT_QUERY,
    SEARCH_RECORDS_QUERY,
)

logger = logging.getLogger(__name__)


@app.mcp.tool(
    name="fairsharing_search_records",
    annotations=ToolAnnotations(
        readOnlyHint=True,
        idempotentHint=True,
        openWorldHint=True,
    ),
)
async def search_records(
    query: Annotated[
        str | None, Field(default=None, max_length=500, description="Search query")
    ] = None,
    registry: Annotated[
        list[str] | None,
        Field(default=None, description="Registry filter (Standard, Database, Policy)"),
    ] = None,
    record_type: Annotated[
        list[str] | None, Field(default=None, description="Record type filter")
    ] = None,
    status: Annotated[list[str] | None, Field(default=None, description="Status filter")] = None,
    subjects: Annotated[list[str] | None, Field(default=None, description="Subject filter")] = None,
    domains: Annotated[list[str] | None, Field(default=None, description="Domain filter")] = None,
    taxonomies: Annotated[
        list[str] | None, Field(default=None, description="Taxonomy filter")
    ] = None,
    countries: Annotated[
        list[str] | None, Field(default=None, description="Country filter")
    ] = None,
    organisations: Annotated[
        list[str] | None, Field(default=None, description="Organisation filter")
    ] = None,
    user_defined_tags: Annotated[
        list[str] | None, Field(default=None, description="User-defined tag filter")
    ] = None,
    licences: Annotated[list[str] | None, Field(default=None, description="Licence filter")] = None,
    journals: Annotated[list[str] | None, Field(default=None, description="Journal filter")] = None,
    is_recommended: Annotated[
        bool | None, Field(default=None, description="Filter by recommended status")
    ] = None,
    is_approved: Annotated[
        bool | None, Field(default=None, description="Filter by approved status")
    ] = None,
    is_maintained: Annotated[
        bool | None, Field(default=None, description="Filter by maintenance status")
    ] = None,
    has_publication: Annotated[
        bool | None, Field(default=None, description="Filter by publication status")
    ] = None,
    is_implemented: Annotated[
        bool | None, Field(default=None, description="Filter by implementation status")
    ] = None,
    search_and: Annotated[
        bool, Field(default=True, description="Use AND logic for filters")
    ] = True,
    page: Annotated[int, Field(default=1, ge=1, description="Page number")] = 1,
    per_page: Annotated[int, Field(default=20, ge=1, le=50, description="Results per page")] = 20,
    fallback_on_empty: Annotated[
        bool, Field(default=False, description="Progressively relax filters if no results")
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
    """Search FAIRsharing records with powerful filters.

    Args:
        query: Text search query (searches name, description, etc.)
        registry: Filter by registry type: "Database", "Standard", "Policy", "Collection"
        record_type: Filter by record type: "knowledgebase", "repository", "terminology_artefact", "model_and_format", "reporting_guideline", "identifier_schema", "journal", "funder", etc.
        status: Filter by status: "ready", "deprecated", "in_development", "uncertain"
        subjects: Filter by scientific subjects (e.g., "Genomics", "Proteomics")
        domains: Filter by technical domains (e.g., "Data model", "Identifier schema")
        taxonomies: Filter by species (e.g., "Homo sapiens", "Mus musculus")
        countries: Filter by country names (e.g., "United Kingdom", "United States")
        organisations: Filter by organisation names (e.g., "EMBL-EBI")
        user_defined_tags: Filter by community tags (e.g., "COVID-19")
        licences: Filter by licence names
        journals: Filter by journal titles
        is_recommended: Filter for recommended records only
        is_approved: Filter for curator-approved records only
        is_maintained: Filter for actively maintained records only
        has_publication: Filter for records with publications
        is_implemented: Filter for standards implemented by at least one database
        search_and: If True (default), ALL filters must match. If False, ANY filter can match.
        page: Page number (default: 1)
        per_page: Results per page (default: 20, max: 50)
        fallback_on_empty: If True and the search returns 0 results, automatically
            retry by progressively removing filters (subjects first, then countries).
            Returns results with a note about which filters were relaxed. Default: False.
        output_format: Output format: "markdown" (default) for human-readable output,
            "json" for machine-readable structured data suitable for programmatic chaining.

    Returns:
        Formatted list of matching records with key details
    """
    client = app.get_client()

    per_page = min(max(1, per_page), config.get_max_per_page())
    page = max(1, page)

    variables: dict = {"page": page, "perPage": per_page}

    # Map parameters to GraphQL variables. When search_and is True (default),
    # we omit searchAnd so the API uses its default AND behavior for filters.
    param_map = {
        "q": query,
        "searchAnd": search_and if not search_and else None,
        "registry": registry,
        "recordType": record_type,
        "status": status,
        "subjects": subjects,
        "domains": domains,
        "taxonomies": taxonomies,
        "countries": countries,
        "organisations": organisations,
        "userDefinedTags": user_defined_tags,
        "licences": licences,
        "journals": journals,
        "isRecommended": is_recommended,
        "isApproved": is_approved,
        "isMaintained": is_maintained,
        "hasPublication": has_publication,
        "isImplemented": is_implemented,
    }

    for k, v in param_map.items():
        if v is not None:
            variables[k] = v

    try:
        data = await client.query(SEARCH_RECORDS_QUERY, variables)
        result = data.get("searchFairsharingRecords", {})
        records = result.get("records", [])
        total_count = result.get("totalCount", 0)
        total_pages = result.get("totalPages", 0)

        # Progressive fallback: try relaxing one filter at a time (comprehensive fallback)
        relaxed_filters = []
        if not records and fallback_on_empty:
            # Try relaxing each filter type individually (order by typical restrictiveness)
            fallback_keys = [
                "subjects",
                "countries",
                "organisations",
                "taxonomies",
                "domains",
                "journals",
                "licences",
                "userDefinedTags",
                "recordType",
                "status",
            ]
            for key in fallback_keys:
                if not variables.get(key):
                    continue
                # Drop only this one filter
                fallback_vars = {k: v for k, v in variables.items() if k != key}
                data = await client.query(SEARCH_RECORDS_QUERY, fallback_vars)
                result = data.get("searchFairsharingRecords", {})
                records = result.get("records", [])
                total_count = result.get("totalCount", 0)
                total_pages = result.get("totalPages", 0)
                if records:
                    relaxed_filters = [f"{key}={variables[key]}"]
                    break

        if not records:
            # Build informative zero-result message with filter context
            filter_parts = []
            if variables.get("q"):
                filter_parts.append(f"query='{variables['q']}'")
            if variables.get("registry"):
                filter_parts.append(f"registry={variables['registry']}")
            if variables.get("subjects"):
                filter_parts.append(f"subjects={variables['subjects']}")
            if variables.get("countries"):
                filter_parts.append(f"countries={variables['countries']}")
            if variables.get("recordType"):
                filter_parts.append(f"type={variables['recordType']}")
            if variables.get("status"):
                filter_parts.append(f"status={variables['status']}")

            msg = "No records found"
            if filter_parts:
                msg += f" for filters: {', '.join(filter_parts)}"
            msg += "."
            msg += (
                " Try broadening your search by removing one filter at a time"
                " (e.g., drop the subject or country filter)."
            )
            return msg

        if output_format == "json":
            return json.dumps(
                {
                    "records": [
                        {
                            "id": r.get("id"),
                            "name": r.get("name"),
                            "abbreviation": r.get("abbreviation"),
                            "registry": r.get("registry"),
                            "type": r.get("type"),
                            "status": r.get("status"),
                            "doi": r.get("doi"),
                        }
                        for r in records
                    ],
                    "total_count": total_count,
                    "total_pages": total_pages,
                    "page": page,
                    "filters_dropped": relaxed_filters if relaxed_filters else None,
                },
                indent=2,
            )

        lines = []
        if relaxed_filters:
            lines.append("---")
            lines.append(
                f"**WARNING: Original search returned 0 results. "
                f"The following filter(s) were DROPPED to find results: "
                f"{', '.join(relaxed_filters)}. "
                f"Results below may not match your original intent.**"
            )
            lines.append("---")
            lines.append("")
        lines.append(
            f"## Search Results (Page {page} of {total_pages}, Total: {total_count:,} records)"
        )
        lines.append(f"Showing {len(records)} of {total_count:,} results on this page.")
        lines.append("")

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
        return f"Error searching records: {e}"


@app.mcp.tool(
    name="fairsharing_search_records_by_license",
    annotations=ToolAnnotations(
        readOnlyHint=True,
        idempotentHint=True,
        openWorldHint=True,
    ),
)
async def search_records_by_license(
    licence: Annotated[
        str, Field(min_length=1, max_length=500, description="Licence name to filter by")
    ],
    registry: Annotated[
        list[str] | None,
        Field(default=None, description="Registry filter (Standard, Database, Policy)"),
    ] = None,
    record_type: Annotated[
        list[str] | None, Field(default=None, description="Record type filter")
    ] = None,
    status: Annotated[list[str] | None, Field(default=None, description="Status filter")] = None,
    subjects: Annotated[list[str] | None, Field(default=None, description="Subject filter")] = None,
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
    """Search FAIRsharing records by licence (e.g. CC0, CC-BY, MIT).

    Use this for "find CC0 databases" or "standards with open licence". For
    available licence names, use the list_licences tool.

    Args:
        licence: Licence name to filter by (e.g., "CC0", "CC-BY", "CC-BY-SA").
        registry: Optional filter by registry: ["Database"], ["Standard"], ["Policy"], ["Collection"].
        record_type: Optional record type filter.
        status: Optional status filter: ["ready"], ["deprecated"], etc.
        subjects: Optional subject filter (e.g., ["Genomics"]).
        page: Page number (default: 1).
        per_page: Results per page (default: 20, max: 50).
        output_format: Output format: "markdown" (default) for human-readable output,
            "json" for machine-readable structured data suitable for programmatic chaining.

    Returns:
        Formatted list of records that use the given licence.
    """
    if not licence or not licence.strip():
        return "Please provide a licence name (e.g. CC0, CC-BY). Use list_licences to see options."
    return await search_records(
        licences=[licence.strip()],
        registry=registry,
        record_type=record_type,
        status=status,
        subjects=subjects,
        page=page,
        per_page=per_page,
        output_format=output_format,
    )


@app.mcp.tool(
    name="fairsharing_count_records",
    annotations=ToolAnnotations(
        readOnlyHint=True,
        idempotentHint=True,
        openWorldHint=True,
    ),
)
async def count_records(
    query: Annotated[
        str | None, Field(default=None, max_length=500, description="Search query")
    ] = None,
    registry: Annotated[
        list[str] | None,
        Field(default=None, description="Registry filter (Standard, Database, Policy)"),
    ] = None,
    record_type: Annotated[
        list[str] | None, Field(default=None, description="Record type filter")
    ] = None,
    status: Annotated[list[str] | None, Field(default=None, description="Status filter")] = None,
    subjects: Annotated[list[str] | None, Field(default=None, description="Subject filter")] = None,
    domains: Annotated[list[str] | None, Field(default=None, description="Domain filter")] = None,
    taxonomies: Annotated[
        list[str] | None, Field(default=None, description="Taxonomy filter")
    ] = None,
    countries: Annotated[
        list[str] | None, Field(default=None, description="Country filter")
    ] = None,
    organisations: Annotated[
        list[str] | None, Field(default=None, description="Organisation filter")
    ] = None,
    is_recommended: Annotated[
        bool | None, Field(default=None, description="Filter by recommended status")
    ] = None,
    is_maintained: Annotated[
        bool | None, Field(default=None, description="Filter by maintenance status")
    ] = None,
    has_publication: Annotated[
        bool | None, Field(default=None, description="Filter by publication status")
    ] = None,
    is_implemented: Annotated[
        bool | None, Field(default=None, description="Filter by implementation status")
    ] = None,
    search_and: Annotated[
        bool, Field(default=True, description="Use AND logic for filters")
    ] = True,
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
    """Count records matching filters without returning full data.

    Efficient counting tool that returns only the totalCount for a filtered search.
    When no date filters are used, uses minimal data transfer (perPage=1).
    When min_year/max_year are set, uses best-effort client-side date filtering:
    the API has no date-range filter, so we scan records (limit from
    FAIRSHARING_MAX_SCAN env, typically 2000) and count matches.
    Ideal for computing percentages and ratios by making two count_records calls
    and dividing.

    Args:
        query: Text search query
        registry: Filter by registry: "Database", "Standard", "Policy", "Collection"
        record_type: Filter by record type
        status: Filter by status: "ready", "deprecated", "in_development", "uncertain"
        subjects: Filter by subjects (e.g., "Genomics")
        domains: Filter by domains
        taxonomies: Filter by species
        countries: Filter by country names
        organisations: Filter by organisation names
        is_recommended: Filter for recommended records
        is_maintained: Filter for maintained records
        has_publication: Filter for records with publications
        is_implemented: Filter for standards implemented by a database
        search_and: If True (default), ALL filters must match
        min_year: Minimum creation year (inclusive). Triggers scan mode.
        max_year: Maximum creation year (inclusive). Triggers scan mode.
        output_format: Output format: "markdown" (default) or "json" for structured data.

    Returns:
        Total count of matching records with filter summary
    """
    client = app.get_client()

    has_date_filter = min_year is not None or max_year is not None

    if min_year and max_year and min_year > max_year:
        return f"Error: min_year ({min_year}) cannot be greater than max_year ({max_year})."

    variables: dict = {
        "page": 1,
        "perPage": 1 if not has_date_filter else config.get_max_per_page(),
    }

    param_map = {
        "q": query,
        "searchAnd": search_and if not search_and else None,
        "registry": registry,
        "recordType": record_type,
        "status": status,
        "subjects": subjects,
        "domains": domains,
        "taxonomies": taxonomies,
        "countries": countries,
        "organisations": organisations,
        "isRecommended": is_recommended,
        "isMaintained": is_maintained,
        "hasPublication": has_publication,
        "isImplemented": is_implemented,
    }

    for k, v in param_map.items():
        if v is not None:
            variables[k] = v

    try:
        if has_date_filter:
            # Scan mode: iterate pages and count date-matching records
            date_count = 0
            total_scanned = 0
            page = 1
            per_page = config.get_max_per_page()
            max_scan_pages = config.get_max_scan() // per_page
            server_total = 0
            while page <= max_scan_pages:
                variables["page"] = page
                data = await client.query(SEARCH_RECORDS_COMPACT_QUERY, variables)
                result = data.get("searchFairsharingRecords", {})
                records = result.get("records", [])
                if page == 1:
                    server_total = result.get("totalCount", 0)
                if not records:
                    break
                total_scanned += len(records)
                for r in records:
                    if matches_date_range(r.get("createdAt"), min_year, max_year):
                        date_count += 1
                page += 1

            total_count = date_count
            scan_note = f" (scanned {total_scanned:,} of {server_total:,} total records)"
        else:
            data = await client.query(SEARCH_RECORDS_COMPACT_QUERY, variables)
            result = data.get("searchFairsharingRecords", {})
            total_count = result.get("totalCount", 0)
            scan_note = ""

        # Build filter description
        filters_used = []
        if query:
            filters_used.append(f"Query: '{query}'")
        if registry:
            filters_used.append(f"Registry: {', '.join(registry)}")
        if record_type:
            filters_used.append(f"Type: {', '.join(record_type)}")
        if status:
            filters_used.append(f"Status: {', '.join(status)}")
        if subjects:
            filters_used.append(f"Subjects: {', '.join(subjects)}")
        if domains:
            filters_used.append(f"Domains: {', '.join(domains)}")
        if taxonomies:
            filters_used.append(f"Taxonomies: {', '.join(taxonomies)}")
        if countries:
            filters_used.append(f"Countries: {', '.join(countries)}")
        if organisations:
            filters_used.append(f"Organisations: {', '.join(organisations)}")
        if is_recommended is not None:
            filters_used.append(f"Recommended: {is_recommended}")
        if is_maintained is not None:
            filters_used.append(f"Maintained: {is_maintained}")
        if has_publication is not None:
            filters_used.append(f"Has publication: {has_publication}")
        if is_implemented is not None:
            filters_used.append(f"Implemented: {is_implemented}")
        if min_year is not None:
            filters_used.append(f"Min year: {min_year}")
        if max_year is not None:
            filters_used.append(f"Max year: {max_year}")

        if output_format == "json":
            return json.dumps({"total_count": total_count}, indent=2)

        lines = [f"**Total matching records: {total_count:,}**{scan_note}"]
        if filters_used:
            lines.append(f"Filters: {', '.join(filters_used)}")

        return "\n".join(lines)

    except FAIRsharingError as e:
        return f"Error counting records: {e}"


@app.mcp.tool(
    name="fairsharing_count_fair_records",
    annotations=ToolAnnotations(
        readOnlyHint=True,
        idempotentHint=True,
        openWorldHint=True,
    ),
)
async def count_fair_records(
    query: Annotated[
        str | None, Field(default=None, max_length=500, description="Search query")
    ] = None,
    registry: Annotated[
        list[str] | None,
        Field(default=None, description="Registry filter (Standard, Database, Policy)"),
    ] = None,
    record_type: Annotated[
        list[str] | None, Field(default=None, description="Record type filter")
    ] = None,
    subjects: Annotated[list[str] | None, Field(default=None, description="Subject filter")] = None,
    domains: Annotated[list[str] | None, Field(default=None, description="Domain filter")] = None,
    uses_persistent_identifier: Annotated[
        bool | None, Field(default=None, description="Filter by persistent identifier usage")
    ] = None,
    has_preservation_policy: Annotated[
        bool | None, Field(default=None, description="Filter by preservation policy")
    ] = None,
    has_resource_sustainability: Annotated[
        bool | None, Field(default=None, description="Filter by resource sustainability")
    ] = None,
    data_access: Annotated[
        str | None, Field(default=None, description="Data access condition filter")
    ] = None,
    data_curation: Annotated[
        str | None, Field(default=None, description="Data curation level filter")
    ] = None,
    recommends_database: Annotated[
        bool | None, Field(default=None, description="Filter by database recommendation")
    ] = None,
    recommends_standard: Annotated[
        bool | None, Field(default=None, description="Filter by standard recommendation")
    ] = None,
    is_maintained: Annotated[
        bool | None, Field(default=None, description="Filter by maintenance status")
    ] = None,
    is_recommended: Annotated[
        bool | None, Field(default=None, description="Filter by recommended status")
    ] = None,
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
    """Count records matching FAIR quality indicator filters.

    Uses the multiTagFilter API which supports FAIR indicator fields like persistent
    identifiers, preservation policy, data access conditions, and more. Unlike
    count_records, this can filter by FAIR quality criteria. Does NOT hardcode
    registry or status, so can count across all registries.
    The multiTagFilter API returns the full result set (no server-side pagination);
    very broad filters may be slow or hit response limits.

    Note: Country filtering is not supported by the multiTagFilter API.
    Use count_records for country-filtered counts.

    Args:
        query: Text search query
        registry: Filter by registry: "Database", "Standard", "Policy", "Collection"
        record_type: Filter by record type
        subjects: Filter by subjects (e.g., "Genomics")
        domains: Filter by domains
        uses_persistent_identifier: Has persistent identifiers
        has_preservation_policy: Has data preservation policy
        has_resource_sustainability: Has resource sustainability plan
        data_access: Data access condition: "open", "partially open", "controlled", "not found"
        data_curation: Curation level: "manual", "automated", "manual/automated", "none", "not found"
        recommends_database: Recommends at least one database (for policies)
        recommends_standard: Recommends at least one standard (for policies)
        is_maintained: Is actively maintained
        is_recommended: Is recommended
        min_year: Minimum creation year (inclusive). Client-side filter.
        max_year: Maximum creation year (inclusive). Client-side filter.

    Returns:
        Count of matching records with filter summary
    """
    client = app.get_client()

    if min_year and max_year and min_year > max_year:
        return f"Error: min_year ({min_year}) cannot be greater than max_year ({max_year})."

    has_date_filter = min_year is not None or max_year is not None

    variables: dict = {"load": False}

    if query:
        variables["q"] = query
    if registry:
        variables["registry"] = registry
    if record_type:
        variables["recordType"] = record_type
    if subjects:
        variables["subjects"] = subjects
    if domains:
        variables["domains"] = domains
    if uses_persistent_identifier is not None:
        variables["usesPersistentIdentifier"] = uses_persistent_identifier
    if has_preservation_policy is not None:
        variables["dataPreservationPolicy"] = has_preservation_policy
    if has_resource_sustainability is not None:
        variables["resourceSustainability"] = has_resource_sustainability
    if data_access:
        variables["dataAccessCondition"] = [data_access]
    if data_curation:
        variables["dataCuration"] = [data_curation]
    if recommends_database is not None:
        variables["recommendsDatabase"] = recommends_database
    if recommends_standard is not None:
        variables["recommendsStandard"] = recommends_standard
    if is_maintained is not None:
        variables["isMaintained"] = is_maintained
    if is_recommended is not None:
        variables["isRecommended"] = is_recommended

    try:
        data = await client.query(MULTI_TAG_FILTER_QUERY, variables)
        records = data.get("multiTagFilter", [])

        # Apply client-side date filter
        if has_date_filter:
            records = [
                r for r in records if matches_date_range(r.get("createdAt"), min_year, max_year)
            ]

        total_count = len(records)

        # Registry breakdown
        registry_counts: dict[str, int] = {}
        for r in records:
            reg = r.get("registry", "Unknown")
            registry_counts[reg] = registry_counts.get(reg, 0) + 1

        if output_format == "json":
            return json.dumps(
                {
                    "total_count": total_count,
                    "registry_breakdown": registry_counts,
                },
                indent=2,
            )

        # Build filter description
        filters_used = []
        if query:
            filters_used.append(f"Query: '{query}'")
        if registry:
            filters_used.append(f"Registry: {', '.join(registry)}")
        if record_type:
            filters_used.append(f"Type: {', '.join(record_type)}")
        if subjects:
            filters_used.append(f"Subjects: {', '.join(subjects)}")
        if domains:
            filters_used.append(f"Domains: {', '.join(domains)}")
        if uses_persistent_identifier is not None:
            filters_used.append(f"Persistent IDs: {uses_persistent_identifier}")
        if has_preservation_policy is not None:
            filters_used.append(f"Preservation policy: {has_preservation_policy}")
        if has_resource_sustainability is not None:
            filters_used.append(f"Sustainability: {has_resource_sustainability}")
        if data_access:
            filters_used.append(f"Data access: {data_access}")
        if data_curation:
            filters_used.append(f"Curation: {data_curation}")
        if recommends_database is not None:
            filters_used.append(f"Recommends DB: {recommends_database}")
        if recommends_standard is not None:
            filters_used.append(f"Recommends standard: {recommends_standard}")
        if is_maintained is not None:
            filters_used.append(f"Maintained: {is_maintained}")
        if is_recommended is not None:
            filters_used.append(f"Recommended: {is_recommended}")
        if min_year is not None:
            filters_used.append(f"Min year: {min_year}")
        if max_year is not None:
            filters_used.append(f"Max year: {max_year}")

        lines = [f"**Total matching records: {total_count:,}**"]
        if filters_used:
            lines.append(f"Filters: {', '.join(filters_used)}")
        if registry_counts:
            breakdown = ", ".join(f"{reg}: {cnt}" for reg, cnt in sorted(registry_counts.items()))
            lines.append(f"By registry: {breakdown}")

        return "\n".join(lines)

    except FAIRsharingError as e:
        return f"Error counting FAIR records: {e}"


@app.mcp.tool(
    name="fairsharing_advanced_filter_records",
    annotations=ToolAnnotations(
        readOnlyHint=True,
        idempotentHint=True,
        openWorldHint=True,
    ),
)
async def advanced_filter_records(
    query: Annotated[
        str | None, Field(default=None, max_length=500, description="Search query")
    ] = None,
    registry: Annotated[
        list[str] | None,
        Field(default=None, description="Registry filter (Standard, Database, Policy)"),
    ] = None,
    record_type: Annotated[
        list[str] | None, Field(default=None, description="Record type filter")
    ] = None,
    status: Annotated[list[str] | None, Field(default=None, description="Status filter")] = None,
    subjects: Annotated[list[str] | None, Field(default=None, description="Subject filter")] = None,
    domains: Annotated[list[str] | None, Field(default=None, description="Domain filter")] = None,
    taxonomies: Annotated[
        list[str] | None, Field(default=None, description="Taxonomy filter")
    ] = None,
    user_defined_tags: Annotated[
        list[str] | None, Field(default=None, description="User-defined tag filter")
    ] = None,
    is_recommended: Annotated[
        bool | None, Field(default=None, description="Filter by recommended status")
    ] = None,
    is_approved: Annotated[
        bool | None, Field(default=None, description="Filter by approved status")
    ] = None,
    is_maintained: Annotated[
        bool | None, Field(default=None, description="Filter by maintenance status")
    ] = None,
    has_publication: Annotated[
        bool | None, Field(default=None, description="Filter by publication status")
    ] = None,
    is_implemented: Annotated[
        bool | None, Field(default=None, description="Filter by implementation status")
    ] = None,
    uses_persistent_identifier: Annotated[
        bool | None, Field(default=None, description="Filter by persistent identifier usage")
    ] = None,
    has_preservation_policy: Annotated[
        bool | None, Field(default=None, description="Filter by preservation policy")
    ] = None,
    has_resource_sustainability: Annotated[
        bool | None, Field(default=None, description="Filter by resource sustainability")
    ] = None,
    data_access: Annotated[
        str | None, Field(default=None, description="Data access condition filter")
    ] = None,
    data_curation: Annotated[
        str | None, Field(default=None, description="Data curation level filter")
    ] = None,
    data_deposition_condition: Annotated[
        str | None, Field(default=None, description="Data deposition condition filter")
    ] = None,
    citation_to_publications: Annotated[
        str | None, Field(default=None, description="Citation to publications filter")
    ] = None,
    data_contact_info: Annotated[
        str | None, Field(default=None, description="Data contact information filter")
    ] = None,
    data_versioning: Annotated[
        str | None, Field(default=None, description="Data versioning filter")
    ] = None,
    recommends_database: Annotated[
        bool | None, Field(default=None, description="Filter by database recommendation")
    ] = None,
    recommends_standard: Annotated[
        bool | None, Field(default=None, description="Filter by standard recommendation")
    ] = None,
    page: Annotated[int, Field(default=1, ge=1, description="Page number")] = 1,
    per_page: Annotated[int, Field(default=25, ge=1, le=50, description="Results per page")] = 25,
    output_format: Annotated[
        str,
        Field(
            default="markdown",
            pattern="^(markdown|json)$",
            description="Output format: 'markdown' or 'json'",
        ),
    ] = "markdown",
) -> str:
    """Advanced record filtering using ALL multiTagFilter parameters.

    Unlike assess_database_indicators which is locked to Database/ready, this tool
    exposes every parameter of the multiTagFilter API without restrictions. Enables
    queries like "find policies that recommend databases" or "find any record type
    with persistent identifiers".
    The multiTagFilter API returns the full result set (no server-side pagination);
    pagination is applied client-side. Very broad filters may be slow.

    Newly exposed parameters not available in other tools:
    - recommends_database/recommends_standard: find policies that recommend DBs/standards
    - citation_to_publications: filter by citation availability
    - user_defined_tags: filter by community tags
    - Any registry/status combo with FAIR indicator filters

    Args:
        query: Text search query
        registry: Filter by registry: "Database", "Standard", "Policy", "Collection"
        record_type: Filter by record type
        status: Filter by status
        subjects: Filter by subjects
        domains: Filter by domains
        taxonomies: Filter by species
        user_defined_tags: Filter by community tags
        is_recommended: Filter for recommended records
        is_approved: Filter for approved records
        is_maintained: Filter for maintained records
        has_publication: Filter for records with publications
        is_implemented: Filter for implemented records
        uses_persistent_identifier: Has persistent identifiers
        has_preservation_policy: Has preservation policy
        has_resource_sustainability: Has sustainability plan
        data_access: Data access: "open", "partially open", "controlled", "not found"
        data_curation: Curation: "manual", "automated", "manual/automated", "none", "not found"
        data_deposition_condition: Deposition: "open", "controlled", "not applicable", "not found"
        citation_to_publications: Citation availability: "yes", "no", "not found"
        data_contact_info: Contact info: "yes", "no", "not found"
        data_versioning: Versioning: "yes", "no", "not found"
        recommends_database: Recommends at least one database (policies)
        recommends_standard: Recommends at least one standard (policies)
        page: Page number (client-side pagination)
        per_page: Results per page (default: 25, max: 50)
        output_format: Output format: "markdown" (default) for human-readable output,
            "json" for machine-readable structured data suitable for programmatic chaining.

    Returns:
        Matching records with details
    """
    client = app.get_client()

    per_page = min(max(1, per_page), config.get_max_per_page())
    page = max(1, page)

    variables: dict = {"load": True}

    if query:
        variables["q"] = query
    if registry:
        variables["registry"] = registry
    if record_type:
        variables["recordType"] = record_type
    if status:
        variables["status"] = status
    if subjects:
        variables["subjects"] = subjects
    if domains:
        variables["domains"] = domains
    if taxonomies:
        variables["taxonomies"] = taxonomies
    if user_defined_tags:
        variables["userDefinedTags"] = user_defined_tags
    if is_recommended is not None:
        variables["isRecommended"] = is_recommended
    if is_approved is not None:
        variables["isApproved"] = is_approved
    if is_maintained is not None:
        variables["isMaintained"] = is_maintained
    if has_publication is not None:
        variables["hasPublication"] = has_publication
    if is_implemented is not None:
        variables["isImplemented"] = is_implemented
    if uses_persistent_identifier is not None:
        variables["usesPersistentIdentifier"] = uses_persistent_identifier
    if has_preservation_policy is not None:
        variables["dataPreservationPolicy"] = has_preservation_policy
    if has_resource_sustainability is not None:
        variables["resourceSustainability"] = has_resource_sustainability
    if data_access:
        variables["dataAccessCondition"] = [data_access]
    if data_curation:
        variables["dataCuration"] = [data_curation]
    if data_deposition_condition:
        variables["dataDepositionCondition"] = [data_deposition_condition]
    if citation_to_publications:
        variables["citationToRelatedPublications"] = [citation_to_publications]
    if data_contact_info:
        variables["dataContactInformation"] = [data_contact_info]
    if data_versioning:
        variables["dataVersioning"] = [data_versioning]
    if recommends_database is not None:
        variables["recommendsDatabase"] = recommends_database
    if recommends_standard is not None:
        variables["recommendsStandard"] = recommends_standard

    try:
        data = await client.query(MULTI_TAG_FILTER_QUERY, variables)
        records = data.get("multiTagFilter", [])

        if not records:
            # Build informative zero-result message with filter context
            filter_parts = []
            if variables.get("q"):
                filter_parts.append(f"query='{variables['q']}'")
            if variables.get("registry"):
                filter_parts.append(f"registry={variables['registry']}")
            if variables.get("subjects"):
                filter_parts.append(f"subjects={variables['subjects']}")
            if variables.get("status"):
                filter_parts.append(f"status={variables['status']}")
            if variables.get("recordType"):
                filter_parts.append(f"type={variables['recordType']}")
            if variables.get("usesPersistentIdentifier") is not None:
                filter_parts.append(f"persistentIDs={variables['usesPersistentIdentifier']}")
            if variables.get("dataAccessCondition"):
                filter_parts.append(f"dataAccess={variables['dataAccessCondition']}")

            msg = "No records found"
            if filter_parts:
                msg += f" for filters: {', '.join(filter_parts)}"
            msg += "."
            msg += (
                " Try broadening your search by removing one filter at a time"
                " (e.g., drop the subject filter or relax FAIR indicator criteria)."
            )
            return msg

        total_count = len(records)

        # Client-side pagination
        start_idx = (page - 1) * per_page
        end_idx = start_idx + per_page
        page_records = records[start_idx:end_idx]
        total_pages = (total_count + per_page - 1) // per_page

        if not page_records:
            return f"No results on page {page} (total: {total_count} records, {total_pages} pages)."

        if output_format == "json":
            return json.dumps(
                {
                    "total_count": total_count,
                    "records": [
                        {
                            "id": r.get("id"),
                            "name": r.get("name"),
                            "abbreviation": r.get("abbreviation"),
                            "registry": r.get("registry"),
                            "type": r.get("type"),
                            "status": r.get("status"),
                            "subjects": [
                                s.get("label", "") for s in r.get("subjects", []) if s.get("label")
                            ],
                            "domains": [
                                d.get("label", "") for d in r.get("domains", []) if d.get("label")
                            ],
                        }
                        for r in page_records
                    ],
                    "page": page,
                    "total_pages": total_pages,
                },
                indent=2,
            )

        lines = [
            f"## Advanced Filter Results (Page {page} of {total_pages}, Total: {total_count:,})",
            "",
        ]

        for i, record in enumerate(page_records, start_idx + 1):
            name = record.get("name", "Unknown")
            abbrev = record.get("abbreviation", "")
            rec_registry = record.get("registry", "")
            rec_type = record.get("type", "")
            rec_id = record.get("id", "")

            entry = f"### {i}. {name}"
            if abbrev:
                entry += f" ({abbrev})"
            lines.append(entry)
            lines.append(f"- **Registry:** {rec_registry} | **Type:** {rec_type}")

            subj_labels = [s.get("label", "") for s in record.get("subjects", []) if s.get("label")]
            if subj_labels:
                lines.append(f"- **Subjects:** {', '.join(subj_labels[:5])}")

            dom_labels = [d.get("label", "") for d in record.get("domains", []) if d.get("label")]
            if dom_labels:
                lines.append(f"- **Domains:** {', '.join(dom_labels[:5])}")

            lines.append(f"- **ID:** {rec_id} | **Status:** {record.get('status', 'N/A')}")
            lines.append("")

        if page < total_pages:
            lines.append(f"_Use page={page + 1} to see more results._")
        if config.get_truncation_warning() and total_count >= 500:
            lines.append("")
            lines.append(
                "_Note: multiTagFilter returns a single list; very large result sets "
                "may be subject to API response limits._"
            )

        return "\n".join(lines)

    except FAIRsharingError as e:
        return f"Error in advanced filter: {e}"


@app.mcp.tool(
    name="fairsharing_search_by_doi",
    annotations=ToolAnnotations(
        readOnlyHint=True,
        idempotentHint=True,
        openWorldHint=True,
    ),
)
async def search_by_doi(
    doi: Annotated[str, Field(min_length=1, max_length=500, description="DOI or FAIRsharing URL")],
    output_format: Annotated[
        str,
        Field(
            default="markdown",
            pattern="^(markdown|json)$",
            description="Output format: 'markdown' or 'json'",
        ),
    ] = "markdown",
) -> str:
    """Look up a FAIRsharing record by its DOI.

    Accepts a full DOI (e.g. "10.25504/FAIRsharing.2abjs5"), a DOI URL
    (e.g. "https://doi.org/10.25504/FAIRsharing.2abjs5"), or a FAIRsharing URL
    (e.g. "https://fairsharing.org/FAIRsharing.2abjs5").
    Searches the FAIRsharing API by DOI text and returns the matching record(s).

    Args:
        doi: A DOI string, DOI URL, or FAIRsharing URL.
        output_format: Output format: "markdown" (default) for human-readable output,
            "json" for machine-readable structured data suitable for programmatic chaining.

    Returns:
        Matching record details, or a message if not found.
    """
    if not doi or not doi.strip():
        return (
            "Please provide a DOI (e.g. '10.25504/FAIRsharing.2abjs5') "
            "or FAIRsharing URL (e.g. 'https://fairsharing.org/FAIRsharing.2abjs5')."
        )

    doi = doi.strip()

    # Normalize: extract DOI from URL forms
    # https://doi.org/10.25504/FAIRsharing.xxx -> 10.25504/FAIRsharing.xxx
    doi_match = re.match(r"https?://doi\.org/(10\.\d+/.+)", doi)
    if doi_match:
        doi = doi_match.group(1)

    # https://fairsharing.org/FAIRsharing.xxx -> extract shorthand for search
    fs_match = re.match(r"https?://(?:www\.)?fairsharing\.org/(FAIRsharing\.\w+)", doi)
    if fs_match:
        doi = fs_match.group(1)

    client = app.get_client()

    try:
        # Search using the DOI or shorthand as the query text
        data = await client.query(
            SEARCH_RECORDS_QUERY,
            {"q": doi, "page": 1, "perPage": 10},
        )
        result = data.get("searchFairsharingRecords", {})
        records = result.get("records", [])

        # Filter to records whose DOI actually contains the search string
        if records:
            doi_lower = doi.lower()
            exact_matches = [r for r in records if doi_lower in (r.get("doi") or "").lower()]
            if exact_matches:
                records = exact_matches

        if not records:
            if output_format == "json":
                return json.dumps(
                    {"doi": doi, "error": f"No records found matching DOI '{doi}'."},
                    indent=2,
                )
            return (
                f"No records found matching DOI '{doi}'. "
                "Verify the DOI is correct. FAIRsharing DOIs typically start with "
                "'10.25504/FAIRsharing.'."
            )

        if output_format == "json":
            record = records[0]
            return json.dumps(
                {
                    "doi": doi,
                    "record": {
                        "id": record.get("id"),
                        "name": record.get("name"),
                        "abbreviation": record.get("abbreviation"),
                        "registry": record.get("registry"),
                        "type": record.get("type"),
                        "status": record.get("status"),
                        "doi": record.get("doi"),
                    },
                },
                indent=2,
            )

        lines = [
            f"## DOI Lookup Results ({len(records)} found)",
            f"**Query:** {doi}",
            "",
        ]

        for i, record in enumerate(records, 1):
            name = record.get("name", "Unknown")
            abbrev = record.get("abbreviation", "")
            rec_registry = record.get("registry", "")
            rec_type = record.get("type", "")
            rec_id = record.get("id", "")
            rec_doi = record.get("doi", "")
            status = record.get("status", "")

            entry = f"### {i}. {name}"
            if abbrev:
                entry += f" ({abbrev})"
            lines.append(entry)
            lines.append(f"- **Registry:** {rec_registry} | **Type:** {rec_type}")
            lines.append(f"- **Status:** {status}")
            if rec_doi:
                lines.append(f"- **DOI:** {rec_doi}")
            lines.append(f"- **ID:** {rec_id}")
            lines.append("")

        return "\n".join(lines)

    except FAIRsharingError as e:
        return f"Error searching by DOI: {e}"
