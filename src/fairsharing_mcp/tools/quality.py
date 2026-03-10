"""FAIRsharing MCP tools — FAIR quality assessment and database indicators."""

import json
import logging
from typing import Annotated

from mcp.types import ToolAnnotations
from pydantic import Field

from fairsharing_mcp import app, config, helpers
from fairsharing_mcp.client import FAIRsharingAuthError, FAIRsharingError
from fairsharing_mcp.constants import DATABASE_COMPREHENSIVE_WEIGHTS, DATABASE_FAIR_INDICATOR_FIELDS
from fairsharing_mcp.formatters import (
    build_fairsharing_url,
    compute_fair_score_detailed,
    extract_fair_indicators,
    format_database_quality_profile,
    normalize_quality_score,
)
from fairsharing_mcp.queries import (
    ADVANCED_SEARCH_QUERY,
    GET_RECORD_WITH_ASSOCIATIONS_QUERY,
    MULTI_TAG_FILTER_QUERY,
    SEARCH_RECORDS_QUERY,
)
from fairsharing_mcp.tools.policies import _score_policy, _score_policy_comprehensive
from fairsharing_mcp.tools.standards import _score_standard, _score_standard_comprehensive
from fairsharing_mcp.validation import validate_record_id


@app.mcp.tool(
    name="fairsharing_assess_database_indicators",
    annotations=ToolAnnotations(
        readOnlyHint=True,
        idempotentHint=True,
        openWorldHint=True,
    ),
)
async def assess_database_indicators(
    query: Annotated[
        str | None, Field(default=None, max_length=500, description="Text search query")
    ] = None,
    subjects: Annotated[list[str] | None, Field(default=None, description="Subject filter")] = None,
    domains: Annotated[list[str] | None, Field(default=None, description="Domain filter")] = None,
    data_access: Annotated[
        str | None, Field(default=None, description="Data access condition filter")
    ] = None,
    data_curation: Annotated[
        str | None, Field(default=None, description="Data curation process filter")
    ] = None,
    data_deposition_condition: Annotated[
        str | None, Field(default=None, description="Data deposition condition filter")
    ] = None,
    data_versioning: Annotated[
        str | None, Field(default=None, description="Data versioning policy filter")
    ] = None,
    data_contact_info: Annotated[
        str | None, Field(default=None, description="Data contact information filter")
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
    is_maintained: Annotated[
        bool | None, Field(default=None, description="Filter by maintenance status")
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
    """Find databases matching FAIR quality indicators.

    Search databases based on data quality, openness, and sustainability criteria.
    Uses the multiTagFilter API which supports all FAIR indicator fields.
    The API returns the full result set (no server-side pagination); pagination
    is applied client-side. Very broad filters may be slow.
    FAIR quality indicators are available only for database records (API limitation).

    Args:
        query: Text search query
        subjects: Filter by subjects (e.g., "Genomics")
        domains: Filter by domains
        data_access: Data access condition: "open", "partially open", "controlled", "not found"
        data_curation: Curation level: "manual", "automated", "manual/automated", "none", "not found"
        data_deposition_condition: Deposition condition: "open", "controlled", "not applicable", "not found"
        data_versioning: Data versioning: "yes", "no", "not found"
        data_contact_info: Contact information: "yes", "no", "not found"
        uses_persistent_identifier: Has persistent identifiers (DOI, etc.)
        has_preservation_policy: Has data preservation policy
        has_resource_sustainability: Has resource sustainability plan
        is_maintained: Is actively maintained
        page: Page number (used for client-side slicing)
        per_page: Results per page (default: 25, max: 50)
        output_format: Output format: "markdown" (default) for human-readable output,
            "json" for machine-readable structured data suitable for programmatic chaining.

    Returns:
        Databases matching the quality criteria with FAIR indicator details
    """
    client = app.get_client()

    per_page = min(max(1, per_page), 50)
    page = max(1, page)

    # Check if any FAIR indicator filter is active
    has_fair_filter = any(
        [
            data_access,
            data_curation,
            data_deposition_condition,
            data_versioning,
            data_contact_info,
            uses_persistent_identifier is not None,
            has_preservation_policy is not None,
            has_resource_sustainability is not None,
        ]
    )

    try:
        # Try advancedSearch first — server-side FAIR indicator filtering in one call
        where = helpers.build_advanced_search_where(
            registry=["Database"],
            status=["ready"],
            subjects=subjects,
            domains=domains,
            is_maintained=is_maintained,
            data_access=data_access,
            data_curation=data_curation,
            data_deposition_condition=data_deposition_condition,
            data_versioning=data_versioning,
            data_contact_info=data_contact_info,
            uses_persistent_identifier=uses_persistent_identifier,
            has_preservation_policy=has_preservation_policy,
            has_resource_sustainability=has_resource_sustainability,
        )
        adv_vars: dict = {"where": where}
        if query:
            adv_vars["q"] = query
        data = await client.query(ADVANCED_SEARCH_QUERY, adv_vars, timeout=90, max_retries=1)
        records = data.get("advancedSearch", [])
    except FAIRsharingAuthError:
        raise
    except FAIRsharingError:
        logging.getLogger(__name__).warning(
            "advancedSearch failed, falling back to paginated search"
        )
        # Fallback: paginated SEARCH_RECORDS_QUERY + client-side FAIR filtering
        variables: dict = {
            "registry": ["Database"],
            "status": ["ready"],
            "load": True,
        }
        if query:
            variables["q"] = query
        if subjects:
            variables["subjects"] = subjects
        if domains:
            variables["domains"] = domains
        if is_maintained is not None:
            variables["isMaintained"] = is_maintained

        if has_fair_filter:
            search_vars: dict = {
                "registry": ["Database"],
                "status": ["ready"],
                "perPage": 200,
                "searchAnd": True,
            }
            if query:
                search_vars["q"] = query
            if subjects:
                search_vars["subjects"] = subjects
            if domains:
                search_vars["domains"] = domains
            if is_maintained is not None:
                search_vars["isMaintained"] = is_maintained
            all_records: list = []
            page_num = 1
            while True:
                search_vars["page"] = page_num
                pdata = await client.query(SEARCH_RECORDS_QUERY, search_vars)
                presult = pdata.get("searchFairsharingRecords", {})
                all_records.extend(presult.get("records", []))
                if page_num >= presult.get("totalPages", 1):
                    break
                page_num += 1
        else:
            fdata = await client.query(MULTI_TAG_FILTER_QUERY, variables)
            all_records = fdata.get("multiTagFilter", [])

        # Client-side FAIR indicator filtering
        records = []
        for r in all_records:
            indicators = extract_fair_indicators(r)
            if data_access and indicators.get("dataAccessCondition") != data_access:
                continue
            if data_curation and indicators.get("dataCuration") != data_curation:
                continue
            if data_deposition_condition and (
                indicators.get("dataDepositionCondition") != data_deposition_condition
            ):
                continue
            if data_versioning and indicators.get("dataVersioning") != data_versioning:
                continue
            if data_contact_info and (
                indicators.get("dataContactInformation") != data_contact_info
            ):
                continue
            if uses_persistent_identifier is not None:
                upi = indicators.get("usesPersistentIdentifier")
                upi_bool = upi if isinstance(upi, bool) else (upi == "yes" if upi else False)
                if upi_bool != uses_persistent_identifier:
                    continue
            if has_preservation_policy is not None:
                pp = indicators.get("dataPreservationPolicy")
                pp_bool = pp if isinstance(pp, bool) else (pp == "yes" if pp else False)
                if pp_bool != has_preservation_policy:
                    continue
            if has_resource_sustainability is not None:
                rs = indicators.get("resourceSustainability")
                rs_bool = rs if isinstance(rs, bool) else (rs == "yes" if rs else False)
                if rs_bool != has_resource_sustainability:
                    continue
            records.append(r)

    try:
        if not records:
            filter_parts = []
            if query:
                filter_parts.append(f"query='{query}'")
            if subjects:
                filter_parts.append(f"subjects={subjects}")
            if domains:
                filter_parts.append(f"domains={domains}")
            if data_access:
                filter_parts.append(f"data_access={data_access}")
            if data_curation:
                filter_parts.append(f"data_curation={data_curation}")
            if uses_persistent_identifier is not None:
                filter_parts.append(f"persistent_ids={uses_persistent_identifier}")
            if has_preservation_policy is not None:
                filter_parts.append(f"preservation_policy={has_preservation_policy}")

            msg = "No databases found"
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

        # Build filter description
        filters_used = []
        if query:
            filters_used.append(f"query='{query}'")
        if subjects:
            filters_used.append(f"subjects={subjects}")
        if data_access:
            filters_used.append(f"data_access={data_access} (client-side)")
        if data_curation:
            filters_used.append(f"curation={data_curation} (client-side)")
        if data_deposition_condition:
            filters_used.append(f"deposition={data_deposition_condition} (client-side)")
        if data_versioning:
            filters_used.append(f"versioning={data_versioning} (client-side)")
        if data_contact_info:
            filters_used.append(f"contact_info={data_contact_info} (client-side)")
        if uses_persistent_identifier:
            filters_used.append("persistent IDs (client-side)")
        if has_preservation_policy:
            filters_used.append("preservation policy (client-side)")
        if has_resource_sustainability:
            filters_used.append("resource sustainability (client-side)")
        if is_maintained:
            filters_used.append("maintained")

        lines = [
            f"## Database Quality Search (Page {page} of {total_pages}, Total: {total_count:,})",
        ]
        if filters_used:
            lines.append(f"**Filters:** {', '.join(filters_used)}")
        has_fair_filters = any(
            [
                data_access,
                data_curation,
                data_deposition_condition,
                data_versioning,
                data_contact_info,
                uses_persistent_identifier is not None,
                has_preservation_policy is not None,
                has_resource_sustainability is not None,
            ]
        )
        if has_fair_filters:
            lines.append(
                "_Note: FAIR indicator filters applied client-side (API-level FAIR indicator "
                "filters are deprecated). Results reflect metadata extracted from each record's "
                "metadata blob._"
            )
        lines.append("")

        for i, record in enumerate(page_records, start_idx + 1):
            name = record.get("name", "Unknown")
            abbrev = record.get("abbreviation", "")
            rec_type = record.get("type", "")
            rec_id = record.get("id", "")
            rec_doi = record.get("doi")
            fs_url = build_fairsharing_url(rec_doi)

            entry = f"### {i}. {name}"
            if abbrev:
                entry += f" ({abbrev})"
            lines.append(entry)
            lines.append(f"- **Type:** {rec_type}")

            subj_labels = [s.get("label", "") for s in record.get("subjects", []) if s.get("label")]
            if subj_labels:
                lines.append(f"- **Subjects:** {', '.join(subj_labels[:5])}")

            dom_labels = [d.get("label", "") for d in record.get("domains", []) if d.get("label")]
            if dom_labels:
                lines.append(f"- **Domains:** {', '.join(dom_labels[:5])}")

            lines.append(f"- **ID:** {rec_id}")
            lines.append(f"- **Status:** {record.get('status', 'N/A')}")
            if fs_url and rec_doi:
                suffix = rec_doi.split("FAIRsharing.", 1)[1]
                lines.append(f"- **FAIRsharing:** [FAIRsharing.{suffix}]({fs_url})")
            lines.append("")

        if page < total_pages:
            lines.append(f"_Use page={page + 1} to see more results._")
        if config.get_truncation_warning():
            lines.append("")
            lines.append(
                "_List may be incomplete; API does not provide total count for this query._"
            )

        return "\n".join(lines)

    except FAIRsharingError as e:
        return f"Error assessing databases: {e}"


@app.mcp.tool(
    name="fairsharing_get_database_quality_profile",
    annotations=ToolAnnotations(
        readOnlyHint=True,
        idempotentHint=True,
        openWorldHint=True,
    ),
)
async def get_database_quality_profile(
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
    """Get a database's FAIR quality indicators as a detailed profile.

    Fetches all 9 FAIR quality indicator fields for a database and presents
    them with traffic-light ratings (Good/Fair/Gap) and an overall FAIR score.
    Validates that the record is a Database; returns an error for other
    registry types.
    FAIR quality indicators are available only for database records (API limitation).

    Args:
        record_id: The FAIRsharing database record ID
        output_format: Output format: "markdown" (default) for human-readable output,
            "json" for machine-readable structured data suitable for programmatic chaining.

    Returns:
        FAIR indicator profile with values, ratings, and overall score
    """
    try:
        record = await helpers.fetch_database_quality_with_fallback(record_id)

        if not record:
            return f"No record found with ID {record_id}."

        if record.get("registry", "").lower() != "database":
            return f"Record {record_id} ({record.get('name', 'Unknown')}) is a {record.get('registry', 'Unknown')}, not a Database."

        if output_format == "json":
            detailed = compute_fair_score_detailed(record)
            indicators = {}
            for field in DATABASE_FAIR_INDICATOR_FIELDS:
                val = record.get(field)
                indicators[field] = val if val is not None else None
            return json.dumps(
                {
                    "record_id": record.get("id"),
                    "name": record.get("name"),
                    "abbreviation": record.get("abbreviation"),
                    "registry": record.get("registry"),
                    "indicators": indicators,
                    "score": detailed["score"],
                    "total_rated": detailed["total_rated"],
                    "total_possible": detailed["total_possible"],
                    "grade": detailed["grade"],
                    "confidence": detailed["confidence"],
                    "confidence_note": detailed["confidence_note"],
                    "missing": detailed["missing"],
                    "imputed": detailed["imputed"],
                },
                indent=2,
            )

        return format_database_quality_profile(record)

    except FAIRsharingError as e:
        return f"Error fetching database quality profile: {e}"


@app.mcp.tool(
    name="fairsharing_compare_databases_quality",
    annotations=ToolAnnotations(
        readOnlyHint=True,
        idempotentHint=True,
        openWorldHint=True,
    ),
)
async def compare_databases_quality(
    record_ids: Annotated[
        list[int], Field(min_length=2, max_length=50, description="List of record IDs")
    ],
    output_format: Annotated[
        str,
        Field(
            default="markdown",
            pattern="^(markdown|json)$",
            description="Output format: 'markdown' or 'json'",
        ),
    ] = "markdown",
) -> str:
    """Compare FAIR quality indicators across multiple databases side by side.

    Fetches FAIR indicator fields for 2-10 databases and shows them in a
    comparison matrix with per-database scores.
    FAIR quality indicators are available only for database records (API limitation).

    Args:
        record_ids: List of 2-10 database record IDs to compare
        output_format: "markdown" (default) or "json" for structured data.

    Returns:
        Side-by-side FAIR indicator comparison matrix
    """
    if len(record_ids) < 2:
        return "Please provide at least 2 database record IDs to compare."
    if len(record_ids) > 10:
        return "Please provide at most 10 database record IDs to compare."

    try:
        records = []
        for rid in record_ids:
            record = await helpers.fetch_database_quality_with_fallback(rid)
            if record:
                records.append(record)
            else:
                records.append({"id": rid, "name": f"Record {rid} (not found)"})

        if not records:
            return "Could not fetch any of the specified records."

        indicator_fields = [
            "dataAccessCondition",
            "dataCuration",
            "dataDepositionCondition",
            "citationToRelatedPublications",
            "dataContactInformation",
            "dataVersioning",
            "dataPreservationPolicy",
            "resourceSustainability",
            "usesPersistentIdentifier",
        ]

        if output_format == "json":
            json_records = []
            for r in records:
                indicators = {f: r.get(f) for f in indicator_fields}
                detailed = compute_fair_score_detailed(r)
                json_records.append(
                    {
                        "id": r.get("id"),
                        "name": r.get("name"),
                        "abbreviation": r.get("abbreviation"),
                        "status": r.get("status"),
                        "indicators": indicators,
                        "score": detailed["score"],
                        "total_rated": detailed["total_rated"],
                        "grade": detailed["grade"],
                        "confidence": detailed["confidence"],
                    }
                )
            return json.dumps({"databases": json_records}, indent=2)

        # Build comparison
        lines = [
            f"# Database Quality Comparison ({len(records)} databases)",
            "",
        ]

        # Header row
        names = []
        for r in records:
            name = r.get("abbreviation") or r.get("name", "Unknown")
            if len(name) > 20:
                name = name[:17] + "..."
            names.append(name)

        header = "| Indicator |"
        separator = "|-----------|"
        for name in names:
            header += f" {name} |"
            separator += "------|"
        lines.append(header)
        lines.append(separator)

        indicator_display = {
            "dataAccessCondition": "Data Access",
            "dataCuration": "Curation",
            "dataDepositionCondition": "Deposition",
            "citationToRelatedPublications": "Citations",
            "dataContactInformation": "Contact Info",
            "dataVersioning": "Versioning",
            "dataPreservationPolicy": "Preservation",
            "resourceSustainability": "Sustainability",
            "usesPersistentIdentifier": "Persistent IDs",
        }

        scores = [0.0] * len(records)
        rated = [0] * len(records)

        for field, label in indicator_display.items():
            row = f"| {label} |"
            for idx, r in enumerate(records):
                val = r.get(field)
                if val is None:
                    row += " N/A |"
                else:
                    display_val = str(val)
                    if len(display_val) > 18:
                        display_val = display_val[:15] + "..."
                    row += f" {display_val} |"

                    # Score calculation
                    if isinstance(val, bool):
                        if val:
                            scores[idx] += 1
                        rated[idx] += 1
                    elif isinstance(val, str):
                        val_lower = val.lower()
                        if val_lower in ("open", "manual", "manual/automated", "yes"):
                            scores[idx] += 1
                        elif val_lower in ("partially open", "automated", "controlled"):
                            scores[idx] += 0.5
                        rated[idx] += 1
            lines.append(row)

        # Score row
        score_row = "| **Score** |"
        for idx in range(len(records)):
            if rated[idx] > 0:
                score_row += f" {scores[idx]:.1f}/{rated[idx]} |"
            else:
                score_row += " N/A |"
        lines.append(score_row)

        lines.append("")

        # Individual record details
        lines.append("## Records")
        for r in records:
            name = r.get("name", "Unknown")
            rid = r.get("id", "?")
            status = r.get("status", "N/A")
            lines.append(f"- **{name}** (ID: {rid}) - Status: {status}")
        lines.append("")

        return "\n".join(lines)

    except FAIRsharingError as e:
        return f"Error comparing databases: {e}"


@app.mcp.tool(
    name="fairsharing_rank_databases_by_quality",
    annotations=ToolAnnotations(
        readOnlyHint=True,
        idempotentHint=True,
        openWorldHint=True,
    ),
)
async def rank_databases_by_quality(
    subjects: Annotated[list[str] | None, Field(default=None, description="Subject filter")] = None,
    domains: Annotated[list[str] | None, Field(default=None, description="Domain filter")] = None,
    countries: Annotated[
        list[str] | None, Field(default=None, description="Country filter")
    ] = None,
    max_results: Annotated[
        int, Field(default=15, ge=1, le=100, description="Maximum results to return")
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
    """Rank databases by FAIR quality score.

    Finds databases matching optional filters, fetches their FAIR quality indicators,
    scores each using a 0-9 scale, and returns a ranked list from best to worst.
    Enables queries like "Top 10 most FAIR-compliant genomics databases".
    FAIR quality indicators are available only for database records (API limitation).

    Args:
        subjects: Filter by subjects (e.g., ["Genomics"])
        domains: Filter by domains
        countries: Filter by countries. Applied client-side after initial query —
                   may miss matches beyond the evaluation limit.
        max_results: Number of top results to return (default: 15, range: 5-30)

    Returns:
        Ranked table of databases with FAIR scores and grades
    """
    client = app.get_client()

    max_results = min(max(5, max_results), 30)
    # Fetch more candidates than needed since some may lack indicator data
    fetch_limit = max_results * 2

    try:
        # Step 1: Find candidate databases via multiTagFilter
        variables: dict = {
            "registry": ["Database"],
            "status": ["ready"],
            "load": True,
        }
        if subjects:
            variables["subjects"] = subjects
        if domains:
            variables["domains"] = domains

        data = await client.query(MULTI_TAG_FILTER_QUERY, variables)
        candidates = data.get("multiTagFilter", [])

        if not candidates:
            filter_parts = []
            if subjects:
                filter_parts.append(f"subjects={subjects}")
            if domains:
                filter_parts.append(f"domains={domains}")
            if countries:
                filter_parts.append(f"countries={countries}")

            msg = "No databases found"
            if filter_parts:
                msg += f" for filters: {', '.join(filter_parts)}"
            msg += "."
            msg += (
                " Try broadening your search by removing one filter at a time"
                " (e.g., drop the subject or domain filter)."
            )
            return msg

        # Step 2: Fetch quality details for top candidates
        scored_records = []
        for candidate in candidates[:fetch_limit]:
            rec_id = candidate.get("id")
            if not rec_id:
                continue

            record = await helpers.fetch_database_quality_with_fallback(int(rec_id))
            if not record:
                continue

            # Optional country filter (applied client-side)
            if countries:
                rec_countries = [c.get("name", "") for c in record.get("countries", [])]
                if not any(c.lower() in [rc.lower() for rc in rec_countries] for c in countries):
                    continue

            detailed = compute_fair_score_detailed(record)
            score = detailed["score"]
            total_rated = detailed["total_rated"]
            grade = detailed["grade"]
            if total_rated > 0:
                scored_records.append(
                    {
                        "record": record,
                        "score": score,
                        "total_rated": total_rated,
                        "grade": grade,
                        "pct": (score / total_rated) * 100,
                        "confidence": detailed["confidence"],
                    }
                )

        if not scored_records:
            filter_parts = []
            if subjects:
                filter_parts.append(f"subjects={subjects}")
            if domains:
                filter_parts.append(f"domains={domains}")
            if countries:
                filter_parts.append(f"countries={countries}")

            msg = "No databases with FAIR indicator data found"
            if filter_parts:
                msg += f" for filters: {', '.join(filter_parts)}"
            msg += f" (evaluated {len(candidates)} candidates)."
            msg += (
                " The candidate databases may lack FAIR indicator metadata."
                " Try assess_database_indicators for indicator-specific filtering."
            )
            return msg

        # Step 3: Sort by score descending
        scored_records.sort(key=lambda x: x["score"], reverse=True)
        top_records = scored_records[:max_results]

        if output_format == "json":
            return json.dumps(
                {
                    "candidates_evaluated": len(scored_records),
                    "candidates_found": len(candidates),
                    "rankings": [
                        {
                            "rank": i,
                            "id": entry["record"].get("id"),
                            "name": entry["record"].get("name"),
                            "abbreviation": entry["record"].get("abbreviation"),
                            "score": entry["score"],
                            "total_rated": entry["total_rated"],
                            "grade": entry["grade"],
                            "confidence": entry.get("confidence"),
                            "pct": round(entry["pct"], 1),
                        }
                        for i, entry in enumerate(top_records, 1)
                    ],
                },
                indent=2,
            )

        lines = [
            f"# FAIR Quality Ranking ({len(top_records)} databases)",
            "",
        ]

        filter_parts = []
        if subjects:
            filter_parts.append(f"Subjects: {', '.join(subjects)}")
        if domains:
            filter_parts.append(f"Domains: {', '.join(domains)}")
        if countries:
            filter_parts.append(f"Countries: {', '.join(countries)}")
        if filter_parts:
            lines.append(f"**Filters:** {', '.join(filter_parts)}")

        lines.append(f"**Candidates evaluated:** {len(scored_records)} of {len(candidates)} found")
        lines.append("")

        # Ranking table
        lines.append(
            "| Rank | Database | Score | Grade | Conf. | Access | PIDs | Preservation | Curation |"
        )
        lines.append(
            "|------|----------|-------|-------|-------|--------|------|-------------|----------|"
        )

        for rank, entry in enumerate(top_records, 1):
            rec = entry["record"]
            name = rec.get("abbreviation") or rec.get("name", "Unknown")
            if len(name) > 25:
                name = name[:22] + "..."
            score_str = f"{entry['score']:.1f}/{entry['total_rated']}"
            grade = entry["grade"]
            conf = entry.get("confidence", "?")

            access = str(rec.get("dataAccessCondition", "N/A"))
            pids = str(rec.get("usesPersistentIdentifier", "N/A"))
            preservation = str(rec.get("dataPreservationPolicy", "N/A"))
            curation = str(rec.get("dataCuration", "N/A"))

            lines.append(
                f"| {rank} | {name} | {score_str} | {grade} | "
                f"{conf} | {access[:12]} | {pids[:12]} | {preservation[:12]} | {curation[:12]} |"
            )

        lines.append("")

        # Detailed listing
        lines.append("## Detailed Results")
        for rank, entry in enumerate(top_records, 1):
            rec = entry["record"]
            name = rec.get("name", "Unknown")
            abbrev = rec.get("abbreviation", "")
            rec_id = rec.get("id", "?")
            lines.append(
                f"{rank}. **{name}**"
                + (f" ({abbrev})" if abbrev else "")
                + f" [ID: {rec_id}] - Score: {entry['score']:.1f}/{entry['total_rated']} ({entry['grade']})"
            )

        return "\n".join(lines)

    except FAIRsharingError as e:
        return f"Error ranking databases: {e}"


async def _compute_quality_for_record(record_id: int) -> dict:
    """Fetch a record, detect its registry, compute quality score, normalize to 0-100.

    Returns a dict with: record_id, name, registry, raw_score, raw_max,
    normalized_score, unified_grade, confidence, confidence_note, components.
    Raises FAIRsharingError on fetch failures. Returns an error dict for
    unsupported registry types.
    """
    client = app.get_client()
    record_id = validate_record_id(record_id)

    data = await client.query(GET_RECORD_WITH_ASSOCIATIONS_QUERY, {"id": record_id})
    record = data.get("fairsharingRecord")
    if not record:
        return {"error": f"No record found with ID {record_id}."}

    name = record.get("name", "Unknown")
    registry = record.get("registry", "Unknown")
    registry_lower = registry.lower()

    if registry_lower == "database":
        db_record = await helpers.fetch_database_quality_with_fallback(record_id)
        if not db_record:
            return {"error": f"Could not fetch quality data for database {record_id}."}
        detailed = compute_fair_score_detailed(db_record)
        raw_score = detailed["score"]
        raw_max = 9.0
        confidence = detailed["confidence"]
        components = [
            f"- FAIR score: {raw_score:.1f}/{detailed['total_rated']} rated "
            f"(of {detailed['total_possible']} indicators)",
            f"- Grade: {detailed['grade']}",
            f"- Missing: {detailed['missing']}, Imputed: {detailed['imputed']}",
        ]
    elif registry_lower == "standard":
        result = _score_standard(record)
        raw_score = result["score"]
        raw_max = result["max"]
        confidence = result["confidence"]
        components = result["components"]
    elif registry_lower == "policy":
        # Need mandate data — fetch via fallback
        pol_record = await helpers.fetch_policy_with_fallback(record_id)
        if not pol_record:
            return {"error": f"Could not fetch quality data for policy {record_id}."}
        result = _score_policy(pol_record)
        raw_score = result["score"]
        raw_max = result["max"]
        confidence = result["confidence"]
        components = result["components"]
    else:
        return {
            "error": (
                f"Record {record_id} ({name}) is a {registry}. "
                "Unified quality scoring is only available for Database, Standard, and Policy records."
            )
        }

    normalized = normalize_quality_score(raw_score, raw_max, registry, confidence)

    return {
        "record_id": record_id,
        "name": name,
        "registry": registry,
        "raw_score": raw_score,
        "raw_max": raw_max,
        "normalized_score": normalized["normalized_score"],
        "unified_grade": normalized["unified_grade"],
        "confidence": confidence,
        "confidence_note": normalized["confidence_note"],
        "components": components,
    }


@app.mcp.tool(
    name="fairsharing_get_unified_quality_score",
    annotations=ToolAnnotations(
        readOnlyHint=True,
        idempotentHint=True,
        openWorldHint=True,
    ),
)
async def get_unified_quality_score(
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
    """Get a normalized 0-100 quality score for any record (Database, Standard, or Policy).

    Detects the record's registry type, applies the appropriate quality scorer
    (DB FAIR indicators, Standard maturity, or Policy mandate completeness),
    and normalizes the result to a common 0-100 scale for cross-registry comparison.

    IMPORTANT: Cross-registry comparisons are approximate. The underlying criteria
    differ by registry type:
    - Database: 9 FAIR indicator fields (access, curation, PIDs, etc.)
    - Standard: metadata completeness + adoption + maintenance
    - Policy: mandate coverage + breadth + recommendations

    Args:
        record_id: Any FAIRsharing record ID (Database, Standard, or Policy)
        output_format: "markdown" (default) or "json"

    Returns:
        Unified quality score with original breakdown and cross-registry caveats
    """
    try:
        result = await _compute_quality_for_record(record_id)

        if "error" in result:
            return result["error"]

        if output_format == "json":
            return json.dumps(result, indent=2)

        lines = [
            f"# Unified Quality Score: {result['name']}",
            f"**Registry:** {result['registry']}",
            f"**Normalized Score:** {result['normalized_score']}/100 (Grade: {result['unified_grade']})",
            f"**Original Score:** {result['raw_score']:.1f}/{result['raw_max']:.0f}",
            f"**Confidence:** {result['confidence']} — {result['confidence_note']}",
            "",
            "## Scoring Breakdown",
        ]
        lines.extend(result["components"])
        lines.append("")
        lines.append(
            "_Cross-registry comparisons are approximate — Database (FAIR indicators), "
            "Standard (adoption/maintenance), and Policy (mandate coverage) scores measure "
            "different aspects of quality using heuristic, uncalibrated weights. "
            "Use for relative ranking within a registry, not absolute cross-registry judgments._"
        )

        return "\n".join(lines)

    except FAIRsharingError as e:
        return f"Error computing unified quality score: {e}"


@app.mcp.tool(
    name="fairsharing_compare_unified_quality",
    annotations=ToolAnnotations(
        readOnlyHint=True,
        idempotentHint=True,
        openWorldHint=True,
    ),
)
async def compare_unified_quality(
    record_ids: Annotated[
        list[int], Field(min_length=2, max_length=50, description="List of record IDs")
    ],
    output_format: Annotated[
        str,
        Field(
            default="markdown",
            pattern="^(markdown|json)$",
            description="Output format: 'markdown' or 'json'",
        ),
    ] = "markdown",
) -> str:
    """Compare 2-10 records of ANY registry type on a common 0-100 quality scale.

    Fetches each record, computes its registry-specific quality score, and normalizes
    all scores to a 0-100 scale for side-by-side comparison. Records can be a mix of
    Databases, Standards, and Policies.

    IMPORTANT: Cross-registry comparisons are approximate — Database, Standard,
    and Policy scores measure different aspects of quality.

    Args:
        record_ids: List of 2-10 record IDs (can mix registries)
        output_format: "markdown" (default) or "json"

    Returns:
        Comparison table with unified scores, grades, and original breakdowns
    """
    if len(record_ids) < 2:
        return "Please provide at least 2 record IDs to compare."
    if len(record_ids) > 10:
        return "Please provide at most 10 record IDs to compare."

    try:
        results = []
        errors = []
        for rid in record_ids:
            result = await _compute_quality_for_record(rid)
            if "error" in result:
                errors.append(result["error"])
            else:
                results.append(result)

        if not results:
            return "Could not compute quality for any of the provided records.\n" + "\n".join(
                errors
            )

        # Sort by normalized score descending
        results.sort(key=lambda r: r["normalized_score"], reverse=True)

        if output_format == "json":
            return json.dumps({"records": results, "errors": errors}, indent=2)

        lines = [
            f"# Unified Quality Comparison ({len(results)} records)",
            "",
        ]

        # Comparison table
        lines.append("| Rank | Record | Registry | Score/100 | Grade | Raw | Confidence |")
        lines.append("|------|--------|----------|-----------|-------|-----|------------|")

        for rank, r in enumerate(results, 1):
            name = r["name"]
            if len(name) > 30:
                name = name[:27] + "..."
            lines.append(
                f"| {rank} | {name} | {r['registry']} | "
                f"{r['normalized_score']} | {r['unified_grade']} | "
                f"{r['raw_score']:.1f}/{r['raw_max']:.0f} | {r['confidence']} |"
            )

        lines.append("")

        if errors:
            lines.append("## Errors")
            for err in errors:
                lines.append(f"- {err}")
            lines.append("")

        lines.append(
            "**Note:** Cross-registry comparisons are approximate. "
            "Database, Standard, and Policy scores measure different aspects of quality."
        )

        return "\n".join(lines)

    except FAIRsharingError as e:
        return f"Error comparing unified quality: {e}"


def _score_database_comprehensive(record: dict) -> dict:
    """Compute comprehensive quality score for a Database with domain-specific indicators.

    Extends the FAIR indicator scoring with temporal health, community trust,
    and metadata completeness. Pure function, no API calls.
    """
    from datetime import datetime, timezone

    detailed = compute_fair_score_detailed(record)
    indicators: dict[str, dict] = {}
    total_score = 0.0
    max_score = sum(DATABASE_COMPREHENSIVE_WEIGHTS.values())

    # ── FAIR Indicators (from existing scorer) ──
    fair_max = DATABASE_COMPREHENSIVE_WEIGHTS["fair_indicators"]
    fair_score = detailed["score"]
    indicators["fair_indicators"] = {
        "score": fair_score,
        "max": fair_max,
        "details": [
            f"FAIR score: {fair_score:.1f}/{detailed['total_possible']}",
            f"Grade: {detailed['grade']} (conservative: {detailed['grade_conservative']})",
            f"Missing: {detailed['missing']}, Imputed: {detailed['imputed']}",
        ],
    }
    total_score += fair_score

    # ── Temporal Health ──
    temporal_max = DATABASE_COMPREHENSIVE_WEIGHTS["temporal_health"]
    temporal_score = 0.0
    temporal_details = []
    updated_at = record.get("updatedAt") or record.get("createdAt")
    if updated_at:
        try:
            year = int(updated_at[:4])
            now_year = datetime.now(timezone.utc).year
            age = now_year - year
            if age <= 2:
                temporal_score = temporal_max
                temporal_details.append(f"Updated within {age} year(s) (+{temporal_max})")
            elif age <= 5:
                temporal_score = temporal_max * 0.5
                temporal_details.append(f"Updated {age} years ago (+{temporal_score:.1f})")
            else:
                temporal_details.append(f"Last update {age} years ago (+0.0)")
        except (ValueError, IndexError):
            temporal_details.append("Update date unavailable")
    else:
        temporal_details.append("No update date available")
    indicators["temporal_health"] = {
        "score": temporal_score,
        "max": temporal_max,
        "details": temporal_details,
    }
    total_score += temporal_score

    # ── Community Trust ──
    trust_max = DATABASE_COMPREHENSIVE_WEIGHTS["community_trust"]
    trust_score = 0.0
    trust_details = []
    reverse_assocs = record.get("reverseRecordAssociations", [])
    policy_recs = sum(
        1
        for a in reverse_assocs
        if a.get("recordAssocLabel") == "recommends"
        and a.get("fairsharingRecord", {}).get("registry") == "Policy"
    )
    if policy_recs >= 5:
        ps = trust_max / 2
        trust_score += ps
        trust_details.append(f"Recommended by {policy_recs} policies (+{ps:.1f})")
    elif policy_recs >= 1:
        ps = trust_max / 4
        trust_score += ps
        trust_details.append(f"Recommended by {policy_recs} policy(ies) (+{ps:.1f})")
    else:
        trust_details.append("Not recommended by any policies (+0.0)")

    assocs = record.get("recordAssociations", [])
    std_count = sum(1 for a in assocs if a.get("linkedRecord", {}).get("registry") == "Standard")
    if std_count >= 5:
        ss = trust_max / 2
        trust_score += ss
        trust_details.append(f"Implements {std_count} standards (+{ss:.1f})")
    elif std_count >= 1:
        ss = trust_max / 4
        trust_score += ss
        trust_details.append(f"Implements {std_count} standard(s) (+{ss:.1f})")
    else:
        trust_details.append("No standards implemented (+0.0)")

    indicators["community_trust"] = {
        "score": round(trust_score, 1),
        "max": trust_max,
        "details": trust_details,
    }
    total_score += trust_score

    # ── Metadata Completeness ──
    meta_max = DATABASE_COMPREHENSIVE_WEIGHTS["metadata_completeness"]
    meta_score = 0.0
    meta_details = []
    meta_checks = {
        "publications": bool(record.get("publications")),
        "description": bool(record.get("description") and len(record.get("description", "")) > 100),
        "doi": bool(record.get("doi")),
        "licenceLinks": bool(record.get("licenceLinks")),
    }
    present = sum(meta_checks.values())
    meta_score = (present / len(meta_checks)) * meta_max
    for field, has in meta_checks.items():
        meta_details.append(f"{field}: {'yes' if has else 'missing'}")

    indicators["metadata_completeness"] = {
        "score": round(meta_score, 1),
        "max": meta_max,
        "details": meta_details,
    }
    total_score += meta_score

    # ── Grade ──
    pct = (total_score / max_score) * 100 if max_score > 0 else 0.0
    if pct >= 90:
        grade = "A+"
    elif pct >= 80:
        grade = "A"
    elif pct >= 65:
        grade = "B"
    elif pct >= 50:
        grade = "C"
    elif pct >= 35:
        grade = "D"
    else:
        grade = "F"

    return {
        "basic_fair": detailed,
        "indicators": indicators,
        "total_score": round(total_score, 1),
        "max_score": max_score,
        "normalized_pct": round(pct, 1),
        "grade": grade,
        "confidence": detailed["confidence"],
        "confidence_note": detailed["confidence_note"],
    }


@app.mcp.tool(
    name="fairsharing_get_comprehensive_quality_profile",
    annotations=ToolAnnotations(
        readOnlyHint=True,
        idempotentHint=True,
        openWorldHint=True,
    ),
)
async def get_comprehensive_quality_profile(
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
    """Get a comprehensive quality profile with domain-specific indicators.

    Goes beyond the basic quality score by adding temporal health, community
    engagement/trust, and metadata completeness indicators specific to each
    registry type (Database, Standard, Policy).

    Use get_unified_quality_score for quick cross-registry comparison.
    Use this tool for deep, registry-specific quality assessment.

    Args:
        record_id: Any FAIRsharing record ID (Database, Standard, or Policy).
        output_format: "markdown" (default) or "json" for structured data.

    Returns:
        Comprehensive quality profile with per-indicator breakdowns.
    """
    try:
        record_id = validate_record_id(record_id)
        client = app.get_client()

        data = await client.query(GET_RECORD_WITH_ASSOCIATIONS_QUERY, {"id": record_id})
        record = data.get("fairsharingRecord")
        if not record:
            return f"No record found with ID {record_id}."

        name = record.get("name", "Unknown")
        registry = record.get("registry", "Unknown")
        registry_lower = registry.lower()

        if registry_lower == "database":
            db_record = await helpers.fetch_database_quality_with_fallback(record_id)
            if not db_record:
                return f"Could not fetch quality data for database {record_id}."
            # Merge association data into db_record for comprehensive scoring
            db_record["reverseRecordAssociations"] = record.get("reverseRecordAssociations", [])
            db_record["recordAssociations"] = record.get("recordAssociations", [])
            db_record["publications"] = record.get(
                "publications", db_record.get("publications", [])
            )
            db_record["licenceLinks"] = record.get(
                "licenceLinks", db_record.get("licenceLinks", [])
            )
            db_record.setdefault("description", record.get("description", ""))
            db_record.setdefault("doi", record.get("doi"))
            db_record.setdefault("updatedAt", record.get("updatedAt"))
            db_record.setdefault("createdAt", record.get("createdAt"))
            result = _score_database_comprehensive(db_record)
        elif registry_lower == "standard":
            result = _score_standard_comprehensive(record)
        elif registry_lower == "policy":
            pol_record = await helpers.fetch_policy_with_fallback(record_id)
            if not pol_record:
                return f"Could not fetch quality data for policy {record_id}."
            # Merge association + country data
            pol_record["recordAssociations"] = record.get("recordAssociations", [])
            pol_record["countries"] = record.get("countries", pol_record.get("countries", []))
            pol_record.setdefault("updatedAt", record.get("updatedAt"))
            pol_record.setdefault("createdAt", record.get("createdAt"))
            result = _score_policy_comprehensive(pol_record)
        else:
            return (
                f"Record {record_id} ({name}) is a {registry}. "
                "Comprehensive quality profiling is available for Database, Standard, and Policy records."
            )

        if output_format == "json":
            return json.dumps(
                {
                    "record_id": record_id,
                    "name": name,
                    "registry": registry,
                    "total_score": result["total_score"],
                    "max_score": result["max_score"],
                    "normalized_pct": result["normalized_pct"],
                    "grade": result["grade"],
                    "confidence": result["confidence"],
                    "confidence_note": result["confidence_note"],
                    "indicators": result["indicators"],
                },
                indent=2,
            )

        # Markdown output
        lines = [
            f"# Comprehensive Quality Profile: {name}",
            f"**Registry:** {registry}",
            f"**Score:** {result['total_score']}/{result['max_score']} "
            f"({result['normalized_pct']}%) — Grade: **{result['grade']}**",
            f"**Confidence:** {result['confidence']} — {result['confidence_note']}",
            "",
        ]

        for indicator_name, indicator_data in result["indicators"].items():
            title = indicator_name.replace("_", " ").title()
            lines.append(f"### {title} ({indicator_data['score']:.1f}/{indicator_data['max']:.1f})")
            for detail in indicator_data["details"]:
                lines.append(f"- {detail}")
            lines.append("")

        lines.append(
            "_Comprehensive scoring uses heuristic, uncalibrated weights. "
            "Temporal health, community trust, and metadata completeness indicators "
            "supplement the core scoring methodology. Use for relative ranking "
            "within a registry type._"
        )

        return "\n".join(lines)

    except FAIRsharingError as e:
        return f"Error computing comprehensive quality profile: {e}"
