"""FAIRsharing MCP tools — Cross-entity comparison."""

import asyncio
import json
from collections import Counter, deque
from typing import Annotated

from mcp.types import ToolAnnotations
from pydantic import Field

from fairsharing_mcp import app, helpers
from fairsharing_mcp.client import FAIRsharingError
from fairsharing_mcp.formatters import compute_fair_score_detailed, normalize_quality_score
from fairsharing_mcp.queries import (
    GET_RECORD_WITH_ASSOCIATIONS_QUERY,
    SEARCH_RECORDS_COMPACT_QUERY,
)
from fairsharing_mcp.validation import validate_record_id


@app.mcp.tool(
    name="fairsharing_compare_records",
    annotations=ToolAnnotations(
        readOnlyHint=True,
        idempotentHint=True,
        openWorldHint=True,
    ),
)
async def compare_records(
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
    """Compare two FAIRsharing records side by side.

    Shows shared and unique subjects, domains, taxonomies, organisations,
    and relationships. Useful for understanding overlap and differences.

    Args:
        record_id_1: First record ID
        record_id_2: Second record ID
        output_format: "markdown" (default) or "json"

    Returns:
        Side-by-side comparison showing shared and unique attributes
    """
    try:
        record_id_1 = validate_record_id(record_id_1)
        record_id_2 = validate_record_id(record_id_2)
    except ValueError as e:
        return f"Validation error: {e}"

    client = app.get_client()

    try:
        # Fetch both records in parallel
        data1, data2 = await asyncio.gather(
            client.query(GET_RECORD_WITH_ASSOCIATIONS_QUERY, {"id": record_id_1}),
            client.query(GET_RECORD_WITH_ASSOCIATIONS_QUERY, {"id": record_id_2}),
        )

        rec1 = data1.get("fairsharingRecord")
        rec2 = data2.get("fairsharingRecord")

        if not rec1:
            return f"No record found with ID {record_id_1}."
        if not rec2:
            return f"No record found with ID {record_id_2}."

        name1 = rec1.get("name", "Unknown") + (
            f" ({rec1.get('abbreviation')})" if rec1.get("abbreviation") else ""
        )
        name2 = rec2.get("name", "Unknown") + (
            f" ({rec2.get('abbreviation')})" if rec2.get("abbreviation") else ""
        )

        if output_format == "json":

            def _extract_set(rec, field, key="label"):
                return {item.get(key, "") for item in rec.get(field, []) if item.get(key)}

            subj1 = _extract_set(rec1, "subjects")
            subj2 = _extract_set(rec2, "subjects")
            dom1 = _extract_set(rec1, "domains")
            dom2 = _extract_set(rec2, "domains")
            tax1 = _extract_set(rec1, "taxonomies")
            tax2 = _extract_set(rec2, "taxonomies")
            org1 = _extract_set(rec1, "organisations", "name")
            org2 = _extract_set(rec2, "organisations", "name")

            def _set_comparison(s1, s2):
                return {
                    "shared": sorted(s1 & s2),
                    "only_first": sorted(s1 - s2),
                    "only_second": sorted(s2 - s1),
                }

            return json.dumps(
                {
                    "records": [
                        {
                            "id": record_id_1,
                            "name": rec1.get("name", "N/A"),
                            "abbreviation": rec1.get("abbreviation", ""),
                            "registry": rec1.get("registry", "N/A"),
                            "type": rec1.get("type", "N/A"),
                            "status": rec1.get("status", "N/A"),
                        },
                        {
                            "id": record_id_2,
                            "name": rec2.get("name", "N/A"),
                            "abbreviation": rec2.get("abbreviation", ""),
                            "registry": rec2.get("registry", "N/A"),
                            "type": rec2.get("type", "N/A"),
                            "status": rec2.get("status", "N/A"),
                        },
                    ],
                    "subjects": _set_comparison(subj1, subj2),
                    "domains": _set_comparison(dom1, dom2),
                    "taxonomies": _set_comparison(tax1, tax2),
                    "organisations": _set_comparison(org1, org2),
                },
                indent=2,
                default=str,
            )

        lines = [
            f"# Comparison: {name1} vs {name2}",
            "",
            "## Basic Info",
            f"| Attribute | {rec1.get('abbreviation') or 'Record 1'} | {rec2.get('abbreviation') or 'Record 2'} |",
            "|-----------|---------|---------|",
            f"| **Name** | {rec1.get('name', 'N/A')} | {rec2.get('name', 'N/A')} |",
            f"| **Registry** | {rec1.get('registry', 'N/A')} | {rec2.get('registry', 'N/A')} |",
            f"| **Type** | {rec1.get('type', 'N/A')} | {rec2.get('type', 'N/A')} |",
            f"| **Status** | {rec1.get('status', 'N/A')} | {rec2.get('status', 'N/A')} |",
            "",
        ]

        def _compare_sets(label: str, items1: set, items2: set) -> list[str]:
            shared = items1 & items2
            only1 = items1 - items2
            only2 = items2 - items1
            result = [f"## {label}"]
            if shared:
                result.append(f"**Shared ({len(shared)}):** {', '.join(sorted(shared))}")
            if only1:
                result.append(
                    f"**Only in {rec1.get('abbreviation', 'Record 1')} ({len(only1)}):** {', '.join(sorted(only1))}"
                )
            if only2:
                result.append(
                    f"**Only in {rec2.get('abbreviation', 'Record 2')} ({len(only2)}):** {', '.join(sorted(only2))}"
                )
            if not (shared or only1 or only2):
                result.append("_Neither record has entries._")
            result.append("")
            return result

        # Compare subjects
        subj1 = {s.get("label", "") for s in rec1.get("subjects", []) if s.get("label")}
        subj2 = {s.get("label", "") for s in rec2.get("subjects", []) if s.get("label")}
        lines.extend(_compare_sets("Subjects", subj1, subj2))

        # Compare domains
        dom1 = {d.get("label", "") for d in rec1.get("domains", []) if d.get("label")}
        dom2 = {d.get("label", "") for d in rec2.get("domains", []) if d.get("label")}
        lines.extend(_compare_sets("Domains", dom1, dom2))

        # Compare taxonomies
        tax1 = {t.get("label", "") for t in rec1.get("taxonomies", []) if t.get("label")}
        tax2 = {t.get("label", "") for t in rec2.get("taxonomies", []) if t.get("label")}
        lines.extend(_compare_sets("Taxonomies", tax1, tax2))

        # Compare organisations
        org1 = {o.get("name", "") for o in rec1.get("organisations", []) if o.get("name")}
        org2 = {o.get("name", "") for o in rec2.get("organisations", []) if o.get("name")}
        lines.extend(_compare_sets("Organisations", org1, org2))

        # Compare outgoing relationship targets (with relationship type labels)
        out1 = {
            f"{a['linkedRecord']['name']} [{a.get('recordAssocLabel', 'related_to')}]"
            for a in rec1.get("recordAssociations", [])
            if a.get("linkedRecord", {}).get("name")
        }
        out2 = {
            f"{a['linkedRecord']['name']} [{a.get('recordAssocLabel', 'related_to')}]"
            for a in rec2.get("recordAssociations", [])
            if a.get("linkedRecord", {}).get("name")
        }
        lines.extend(_compare_sets("Outgoing Relationships (shared targets)", out1, out2))

        # Compare incoming relationship sources (with relationship type labels)
        in1 = {
            f"{a['fairsharingRecord']['name']} [{a.get('recordAssocLabel', 'related_to')}]"
            for a in rec1.get("reverseRecordAssociations", [])
            if a.get("fairsharingRecord", {}).get("name")
        }
        in2 = {
            f"{a['fairsharingRecord']['name']} [{a.get('recordAssocLabel', 'related_to')}]"
            for a in rec2.get("reverseRecordAssociations", [])
            if a.get("fairsharingRecord", {}).get("name")
        }
        lines.extend(_compare_sets("Incoming Relationships (shared sources)", in1, in2))

        # Summary
        lines.append("## Overlap Summary")
        shared_subj = len(subj1 & subj2)
        shared_dom = len(dom1 & dom2)
        shared_tax = len(tax1 & tax2)
        shared_org = len(org1 & org2)
        shared_out = len(out1 & out2)
        shared_in = len(in1 & in2)
        lines.append(
            f"| Dimension | Shared | Only {rec1.get('abbreviation', 'R1')} | Only {rec2.get('abbreviation', 'R2')} |"
        )
        lines.append("|-----------|--------|---------|---------|")
        lines.append(f"| Subjects | {shared_subj} | {len(subj1 - subj2)} | {len(subj2 - subj1)} |")
        lines.append(f"| Domains | {shared_dom} | {len(dom1 - dom2)} | {len(dom2 - dom1)} |")
        lines.append(f"| Taxonomies | {shared_tax} | {len(tax1 - tax2)} | {len(tax2 - tax1)} |")
        lines.append(f"| Organisations | {shared_org} | {len(org1 - org2)} | {len(org2 - org1)} |")
        lines.append(f"| Outgoing links | {shared_out} | {len(out1 - out2)} | {len(out2 - out1)} |")
        lines.append(f"| Incoming links | {shared_in} | {len(in1 - in2)} | {len(in2 - in1)} |")

        return "\n".join(lines)

    except FAIRsharingError as e:
        return f"Error comparing records: {e}"


@app.mcp.tool(
    name="fairsharing_compare_multiple_records",
    annotations=ToolAnnotations(
        readOnlyHint=True,
        idempotentHint=True,
        openWorldHint=True,
    ),
)
async def compare_multiple_records(
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
    """Compare 2-10 FAIRsharing records side by side.

    Generalizes compare_records to support N-way comparison. Fetches all records,
    builds an attribute matrix, and computes shared/unique attributes plus pairwise
    Jaccard overlap scores.

    Args:
        record_ids: List of 2-10 record IDs to compare
        output_format: "markdown" (default) or "json"

    Returns:
        Basic info table, shared/unique attributes, and pairwise overlap matrix
    """
    client = app.get_client()

    if len(record_ids) < 2:
        return "Please provide at least 2 record IDs to compare."
    if len(record_ids) > 10:
        return "Please provide at most 10 record IDs to compare."

    try:
        records = []
        for rid in record_ids:
            data = await client.query(GET_RECORD_WITH_ASSOCIATIONS_QUERY, {"id": rid})
            rec = data.get("fairsharingRecord")
            if rec:
                records.append(rec)
            else:
                records.append({"id": rid, "name": f"Record {rid} (not found)"})

        if not records:
            return "Could not fetch any of the specified records."

        n = len(records)

        # Build short names for each record
        names = []
        for r in records:
            name = r.get("abbreviation") or r.get("name", "Unknown")
            if len(name) > 18:
                name = name[:15] + "..."
            names.append(name)

        lines = [
            f"# Comparison of {n} Records",
            "",
        ]

        # Basic info table
        lines.append("## Basic Information")
        header = "| Attribute |"
        separator = "|-----------|"
        for name in names:
            header += f" {name} |"
            separator += "------|"
        lines.append(header)
        lines.append(separator)

        for attr, key in [("Registry", "registry"), ("Type", "type"), ("Status", "status")]:
            row = f"| **{attr}** |"
            for r in records:
                row += f" {r.get(key, 'N/A')} |"
            lines.append(row)

        id_row = "| **ID** |"
        for r in records:
            id_row += f" {r.get('id', 'N/A')} |"
        lines.append(id_row)
        lines.append("")

        # Extract sets for each dimension
        def _extract_labels(rec: dict, field: str, key: str = "label") -> set[str]:
            return {item.get(key, "") for item in rec.get(field, []) if item.get(key)}

        def _extract_names(rec: dict, field: str, key: str = "name") -> set[str]:
            return {item.get(key, "") for item in rec.get(field, []) if item.get(key)}

        dimensions = {
            "Subjects": [_extract_labels(r, "subjects") for r in records],
            "Domains": [_extract_labels(r, "domains") for r in records],
            "Taxonomies": [_extract_labels(r, "taxonomies") for r in records],
            "Organisations": [_extract_names(r, "organisations") for r in records],
        }

        if output_format == "json":
            # Compute combined sets for Jaccard
            combined = []
            for r in records:
                s = (
                    _extract_labels(r, "subjects")
                    | _extract_labels(r, "domains")
                    | _extract_labels(r, "taxonomies")
                )
                combined.append(s)

            # Compute pairwise Jaccard
            jaccard_matrix = []
            for i in range(n):
                row = []
                for j in range(n):
                    if i == j:
                        row.append(None)
                    else:
                        union = combined[i] | combined[j]
                        inter = combined[i] & combined[j]
                        row.append(round(len(inter) / len(union), 4) if union else 0.0)
                jaccard_matrix.append(row)

            records_json = []
            for i, r in enumerate(records):
                rec_data = {
                    "id": r.get("id"),
                    "name": r.get("name", "Unknown"),
                    "abbreviation": r.get("abbreviation", ""),
                    "registry": r.get("registry", "N/A"),
                    "type": r.get("type", "N/A"),
                    "status": r.get("status", "N/A"),
                }
                for dim_name, sets_list in dimensions.items():
                    rec_data[dim_name.lower()] = sorted(sets_list[i])
                records_json.append(rec_data)

            # Shared/unique per dimension
            dim_analysis = {}
            for dim_name, sets_list in dimensions.items():
                non_empty = [s for s in sets_list if s]
                shared_all = (
                    sorted(set.intersection(*non_empty))
                    if len(non_empty) == n and non_empty
                    else []
                )
                unique_per_record = {}
                for i in range(n):
                    unique = sets_list[i] - set.union(
                        *(s for j, s in enumerate(sets_list) if j != i and s), set()
                    )
                    if unique:
                        unique_per_record[names[i]] = sorted(unique)
                dim_analysis[dim_name.lower()] = {
                    "shared_by_all": shared_all,
                    "unique_per_record": unique_per_record,
                }

            return json.dumps(
                {
                    "records": records_json,
                    "dimensions": dim_analysis,
                    "jaccard_matrix": {
                        "labels": names,
                        "values": jaccard_matrix,
                    },
                },
                indent=2,
                default=str,
            )

        # Shared by all vs unique
        for dim_name, sets_list in dimensions.items():
            non_empty = [s for s in sets_list if s]
            if not non_empty:
                continue

            shared_all = set.intersection(*non_empty) if len(non_empty) == n else set()

            lines.append(f"## {dim_name}")
            if shared_all:
                lines.append(
                    f"**Shared by all ({len(shared_all)}):** {', '.join(sorted(shared_all))}"
                )
            else:
                lines.append("**Shared by all:** None")

            for i, name in enumerate(names):
                unique = sets_list[i] - set.union(
                    *(s for j, s in enumerate(sets_list) if j != i and s), set()
                )
                if unique:
                    lines.append(f"**Only in {name} ({len(unique)}):** {', '.join(sorted(unique))}")
            lines.append("")

        # Pairwise Jaccard overlap matrix
        lines.append("## Pairwise Overlap (Jaccard Index)")
        lines.append("_Based on combined subjects + domains + taxonomies_")
        lines.append("")

        # Compute combined sets
        combined = []
        for r in records:
            s = (
                _extract_labels(r, "subjects")
                | _extract_labels(r, "domains")
                | _extract_labels(r, "taxonomies")
            )
            combined.append(s)

        header = "| |"
        separator = "|-|"
        for name in names:
            header += f" {name} |"
            separator += "------|"
        lines.append(header)
        lines.append(separator)

        for i, name_i in enumerate(names):
            row = f"| **{name_i}** |"
            for j in range(n):
                if i == j:
                    row += " - |"
                else:
                    union = combined[i] | combined[j]
                    inter = combined[i] & combined[j]
                    jaccard = len(inter) / len(union) if union else 0
                    row += f" {jaccard:.2f} |"
            lines.append(row)

        lines.append("")

        # Connection overlap
        out_targets = []
        for r in records:
            targets = {
                a["linkedRecord"]["name"]
                for a in r.get("recordAssociations", [])
                if a.get("linkedRecord", {}).get("name")
            }
            out_targets.append(targets)

        non_empty_targets = [s for s in out_targets if s]
        if len(non_empty_targets) >= 2:
            shared_targets = (
                set.intersection(*non_empty_targets) if len(non_empty_targets) == n else set()
            )
            lines.append("## Shared Relationship Targets")
            if shared_targets:
                lines.append(
                    f"**All {n} records link to ({len(shared_targets)}):** {', '.join(sorted(list(shared_targets)[:15]))}"
                )
                if len(shared_targets) > 15:
                    lines.append(f"_(...and {len(shared_targets) - 15} more)_")
            else:
                lines.append("_No relationship targets shared by all records._")

        return "\n".join(lines)

    except FAIRsharingError as e:
        return f"Error comparing multiple records: {e}"


@app.mcp.tool(
    name="fairsharing_compare_subject_landscapes",
    annotations=ToolAnnotations(
        readOnlyHint=True,
        idempotentHint=True,
        openWorldHint=True,
    ),
)
async def compare_subject_landscapes(
    subjects: Annotated[list[str], Field(min_length=1, description="Subject names to compare")],
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
    """Compare resource counts across multiple subjects in a single matrix.

    Builds a comparison matrix showing Database, Standard, Policy, and Collection
    counts for each subject. Computes DB-to-Standard ratios and identifies coverage
    gaps across subjects. Much more efficient than calling analyze_subject_landscape
    multiple times.

    Args:
        subjects: List of 2-8 subject names to compare (e.g., ["Genomics", "Proteomics", "Metabolomics"])
        include_deprecated: Include deprecated records (default: False)
        output_format: "markdown" (default) or "json"

    Returns:
        Comparison matrix with counts, ratios, and gap analysis
    """
    client = app.get_client()

    if len(subjects) < 2:
        return "Please provide at least 2 subjects to compare."
    if len(subjects) > 8:
        return "Please provide at most 8 subjects to compare."

    try:
        registries = ["Standard", "Database", "Policy", "Collection"]
        # counts[subject][registry] = totalCount
        counts: dict[str, dict[str, int]] = {}

        for subject in subjects:
            counts[subject] = {}
            for reg in registries:
                variables: dict = {
                    "subjects": [subject],
                    "registry": [reg],
                    "page": 1,
                    "perPage": 1,
                }
                if not include_deprecated:
                    variables["status"] = ["ready"]

                data = await client.query(SEARCH_RECORDS_COMPACT_QUERY, variables)
                result = data.get("searchFairsharingRecords", {})
                counts[subject][reg] = result.get("totalCount", 0)

        if output_format == "json":
            subjects_data = []
            for subject in subjects:
                std_count = counts[subject]["Standard"]
                db_count = counts[subject]["Database"]
                ratio = db_count / std_count if std_count > 0 else None
                gaps = []
                if counts[subject]["Standard"] == 0:
                    gaps.append("no standards")
                if counts[subject]["Database"] == 0:
                    gaps.append("no databases")
                if counts[subject]["Policy"] == 0:
                    gaps.append("no policies")
                subjects_data.append(
                    {
                        "subject": subject,
                        "counts": counts[subject],
                        "total": sum(counts[subject].values()),
                        "db_to_standard_ratio": ratio,
                        "gaps": gaps,
                    }
                )
            return json.dumps(
                {
                    "subjects": subjects_data,
                    "include_deprecated": include_deprecated,
                },
                indent=2,
                default=str,
            )

        lines = [
            f"# Subject Landscape Comparison ({len(subjects)} subjects)",
            "",
        ]

        if not include_deprecated:
            lines.append("_Active records only (status=ready)_")
            lines.append("")

        # Build comparison matrix
        header = "| Registry |"
        separator = "|----------|"
        for subject in subjects:
            short = subject[:15] + "..." if len(subject) > 15 else subject
            header += f" {short} |"
            separator += "------|"
        lines.append(header)
        lines.append(separator)

        for reg in registries:
            row = f"| **{reg}** |"
            for subject in subjects:
                cnt = counts[subject][reg]
                row += f" {cnt:,} |"
            lines.append(row)

        # Total row
        total_row = "| **Total** |"
        for subject in subjects:
            total = sum(counts[subject].values())
            total_row += f" {total:,} |"
        lines.append(total_row)
        lines.append("")

        # DB-to-Standard ratios
        lines.append("## DB-to-Standard Ratios")
        for subject in subjects:
            std_count = counts[subject]["Standard"]
            db_count = counts[subject]["Database"]
            if std_count > 0:
                ratio = db_count / std_count
                lines.append(
                    f"- **{subject}:** {ratio:.2f} databases per standard ({db_count} DBs / {std_count} standards)"
                )
            else:
                lines.append(f"- **{subject}:** No standards found (gap)")
        lines.append("")

        # Gap analysis
        lines.append("## Coverage Gaps")
        for subject in subjects:
            gaps = []
            if counts[subject]["Standard"] == 0:
                gaps.append("no standards")
            if counts[subject]["Database"] == 0:
                gaps.append("no databases")
            if counts[subject]["Policy"] == 0:
                gaps.append("no policies")
            if gaps:
                lines.append(f"- **{subject}:** {', '.join(gaps)}")

        if not any(
            counts[s]["Standard"] == 0 or counts[s]["Database"] == 0 or counts[s]["Policy"] == 0
            for s in subjects
        ):
            lines.append("_No major gaps detected across subjects._")

        # Ranking
        lines.append("")
        lines.append("## Subject Rankings")
        by_total = sorted(subjects, key=lambda s: sum(counts[s].values()), reverse=True)
        for rank, subject in enumerate(by_total, 1):
            total = sum(counts[subject].values())
            lines.append(f"{rank}. **{subject}:** {total:,} total resources")

        return "\n".join(lines)

    except FAIRsharingError as e:
        return f"Error comparing subject landscapes: {e}"


@app.mcp.tool(
    name="fairsharing_analyze_deprecation_impact",
    annotations=ToolAnnotations(
        readOnlyHint=True,
        idempotentHint=True,
        openWorldHint=True,
    ),
)
async def analyze_deprecation_impact(
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
    """Assess the impact of a deprecated record on the ecosystem.

    Identifies active records that still rely on a deprecated standard or database.
    Checks for implementing databases, recommending policies, and collections.

    Args:
        record_id: The ID of the deprecated record
        output_format: "markdown" (default) or "json"

    Returns:
        Impact analysis showing active dependents and their relationships
    """
    client = app.get_client()

    try:
        data = await client.query(GET_RECORD_WITH_ASSOCIATIONS_QUERY, {"id": record_id})
        record = data.get("fairsharingRecord")

        if not record:
            return f"No record found with ID {record_id}."

        name = record.get("name", "Unknown")
        status = record.get("status", "Unknown")

        lines = [
            f"# Deprecation Impact Analysis: {name}",
            f"**Status:** {status}",
            "",
        ]

        if status.lower() != "deprecated":
            lines.append(
                f"_Note: This record is currently marked as '{status}', not 'deprecated'._"
            )
            lines.append("")

        # Analyze incoming connections (dependents)
        incoming = record.get("reverseRecordAssociations", [])
        dependents: dict[str, list] = {}  # relationship -> [records]

        for a in incoming:
            lr = a.get("fairsharingRecord", {})
            lr_status = lr.get("status", "unknown")

            # Only care about ACTIVE dependents
            if lr_status.lower() == "ready":
                label = a.get("recordAssocLabel", "related_to")
                dependents.setdefault(label, []).append(lr)

        total_impacted = sum(len(v) for v in dependents.values())

        if output_format == "json":
            return json.dumps(
                {
                    "record_id": record_id,
                    "name": name,
                    "status": status,
                    "total_impacted": total_impacted,
                    "impact": {
                        label: [
                            {
                                "id": r.get("id"),
                                "name": r.get("name", "Unknown"),
                                "registry": r.get("registry", ""),
                            }
                            for r in sorted(
                                recs, key=lambda x: (x.get("registry", ""), x.get("name", ""))
                            )
                        ]
                        for label, recs in dependents.items()
                    },
                },
                indent=2,
                default=str,
            )

        if total_impacted == 0:
            lines.append("No active records found that depend on this record.")
            return "\n".join(lines)

        lines.append(f"**Total active dependents:** {total_impacted}")
        lines.append("")

        # Group by relationship type
        relation_descriptions = {
            "implements": "Databases implementing this standard",
            "recommends": "Policies recommending this resource",
            "collects": "Collections including this resource",
            "related_to": "Other related records",
            "extends": "Standards/Databases extending this one",
        }

        for label, records in dependents.items():
            desc = relation_descriptions.get(label, f"Records with '{label}' relationship")
            lines.append(f"## {desc} ({len(records)})")

            # Sort by registry then name
            sorted_records = sorted(
                records, key=lambda x: (x.get("registry", ""), x.get("name", ""))
            )

            for r in sorted_records:
                rname = r.get("name", "Unknown")
                rid = r.get("id", "")
                rreg = r.get("registry", "")
                lines.append(f"- **{rname}** ({rreg}) [ID: {rid}]")
            lines.append("")

        return "\n".join(lines)

    except FAIRsharingError as e:
        return f"Error analyzing deprecation impact: {e}"


@app.mcp.tool(
    name="fairsharing_check_policy_database_compliance",
    annotations=ToolAnnotations(
        readOnlyHint=True,
        idempotentHint=True,
        openWorldHint=True,
    ),
)
async def check_policy_database_compliance(
    policy_id: Annotated[int, Field(ge=1, description="Policy record ID")],
    database_id: Annotated[int, Field(ge=1, description="Database record ID")],
    output_format: Annotated[
        str,
        Field(
            default="markdown",
            pattern="^(markdown|json)$",
            description="Output format: 'markdown' or 'json'",
        ),
    ] = "markdown",
) -> str:
    """Cross-reference a policy's recommended standards with a database's implemented standards.

    Shows which standards the database already implements that the policy recommends
    (compliant), which it's missing (gaps), and which it implements beyond the policy's
    scope (extras). Results are grouped by standard type.

    Args:
        policy_id: The FAIRsharing policy record ID
        database_id: The FAIRsharing database record ID
        output_format: "markdown" (default) or "json"

    Returns:
        Compliance report with compliant/gap/extra standards grouped by type
    """
    client = app.get_client()

    try:
        data_pol = await client.query(GET_RECORD_WITH_ASSOCIATIONS_QUERY, {"id": policy_id})
        data_db = await client.query(GET_RECORD_WITH_ASSOCIATIONS_QUERY, {"id": database_id})

        policy = data_pol.get("fairsharingRecord")
        database = data_db.get("fairsharingRecord")

        if not policy:
            return f"No record found with ID {policy_id}."
        if not database:
            return f"No record found with ID {database_id}."

        if policy.get("registry", "").lower() != "policy":
            return (
                f"Record {policy_id} ({policy.get('name', 'Unknown')}) is a "
                f"{policy.get('registry', 'Unknown')}, not a Policy."
            )
        if database.get("registry", "").lower() != "database":
            return (
                f"Record {database_id} ({database.get('name', 'Unknown')}) is a "
                f"{database.get('registry', 'Unknown')}, not a Database."
            )

        pol_name = policy.get("name", "Unknown")
        db_name = database.get("name", "Unknown")

        # Extract policy's recommended standards (outgoing to Standard registry)
        policy_standards: dict[str, dict] = {}
        for a in policy.get("recordAssociations", []):
            lr = a.get("linkedRecord", {})
            if lr.get("registry") == "Standard" and lr.get("id"):
                sid = str(lr["id"])
                policy_standards[sid] = {
                    "name": lr.get("name", "Unknown"),
                    "abbreviation": lr.get("abbreviation", ""),
                    "type": lr.get("type", "unknown"),
                    "label": a.get("recordAssocLabel", ""),
                }

        # Extract database's implemented standards (outgoing + incoming)
        db_standards: dict[str, dict] = {}
        for a in database.get("recordAssociations", []):
            lr = a.get("linkedRecord", {})
            if lr.get("registry") == "Standard" and lr.get("id"):
                sid = str(lr["id"])
                db_standards[sid] = {
                    "name": lr.get("name", "Unknown"),
                    "abbreviation": lr.get("abbreviation", ""),
                    "type": lr.get("type", "unknown"),
                    "label": a.get("recordAssocLabel", ""),
                }
        for a in database.get("reverseRecordAssociations", []):
            lr = a.get("fairsharingRecord", {})
            if lr.get("registry") == "Standard" and lr.get("id"):
                sid = str(lr["id"])
                if sid not in db_standards:
                    db_standards[sid] = {
                        "name": lr.get("name", "Unknown"),
                        "abbreviation": lr.get("abbreviation", ""),
                        "type": lr.get("type", "unknown"),
                        "label": a.get("recordAssocLabel", ""),
                    }

        pol_ids = set(policy_standards.keys())
        db_ids = set(db_standards.keys())

        compliant_ids = pol_ids & db_ids
        gap_ids = pol_ids - db_ids
        extra_ids = db_ids - pol_ids

        total_recommended = len(pol_ids)
        compliance_pct = (len(compliant_ids) / total_recommended * 100) if total_recommended else 0

        if output_format == "json":
            return json.dumps(
                {
                    "policy_id": policy_id,
                    "policy_name": pol_name,
                    "database_id": database_id,
                    "database_name": db_name,
                    "compliance": {
                        "standards_recommended": total_recommended,
                        "standards_implemented": len(db_ids),
                        "compliant_count": len(compliant_ids),
                        "gap_count": len(gap_ids),
                        "extra_count": len(extra_ids),
                        "compliance_pct": round(compliance_pct, 1),
                        "compliant": [
                            {"id": sid, **policy_standards[sid]} for sid in sorted(compliant_ids)
                        ],
                        "gaps": [{"id": sid, **policy_standards[sid]} for sid in sorted(gap_ids)],
                        "extras": [{"id": sid, **db_standards[sid]} for sid in sorted(extra_ids)],
                    },
                },
                indent=2,
                default=str,
            )

        lines = [
            f"# Policy-Database Compliance: {pol_name} vs {db_name}",
            "",
            f"**Policy:** {pol_name} (ID: {policy_id})",
            f"**Database:** {db_name} (ID: {database_id})",
            "",
            "## Overview",
            f"- **Standards recommended by policy:** {total_recommended}",
            f"- **Standards implemented by database:** {len(db_ids)}",
            f"- **Compliant (overlap):** {len(compliant_ids)}",
            f"- **Gaps (policy recommends, database lacks):** {len(gap_ids)}",
            f"- **Extras (database has, policy doesn't require):** {len(extra_ids)}",
            f"- **Compliance rate:** {compliance_pct:.0f}%",
            "",
        ]

        def _format_grouped(ids: set, source: dict, title: str) -> list[str]:
            if not ids:
                return [f"## {title}", "_None._", ""]
            by_type: dict[str, list] = {}
            for sid in ids:
                info = source[sid]
                stype = info["type"] or "unknown"
                by_type.setdefault(stype, []).append(info)
            result = [f"## {title} ({len(ids)})"]
            for stype in sorted(by_type):
                items = by_type[stype]
                result.append(f"### {stype} ({len(items)})")
                for s in sorted(items, key=lambda x: x["name"]):
                    entry = f"- {s['name']}"
                    if s.get("abbreviation"):
                        entry += f" ({s['abbreviation']})"
                    result.append(entry)
            result.append("")
            return result

        lines.extend(_format_grouped(compliant_ids, policy_standards, "Compliant Standards"))
        lines.extend(_format_grouped(gap_ids, policy_standards, "Gap Standards"))
        lines.extend(_format_grouped(extra_ids, db_standards, "Extra Standards"))

        return "\n".join(lines)

    except FAIRsharingError as e:
        return f"Error checking compliance: {e}"


@app.mcp.tool(
    name="fairsharing_compare_collections",
    annotations=ToolAnnotations(
        readOnlyHint=True,
        idempotentHint=True,
        openWorldHint=True,
    ),
)
async def compare_collections(
    collection_id_1: Annotated[int, Field(ge=1, description="First collection record ID")],
    collection_id_2: Annotated[int, Field(ge=1, description="Second collection record ID")],
    output_format: Annotated[
        str,
        Field(
            default="markdown",
            pattern="^(markdown|json)$",
            description="Output format: 'markdown' or 'json'",
        ),
    ] = "markdown",
) -> str:
    """Compare the contents of two FAIRsharing Collections.

    Identifies shared and unique records between two collections,
    broken down by registry (Standards, Databases, Policies).

    Args:
        collection_id_1: First Collection ID
        collection_id_2: Second Collection ID
        output_format: "markdown" (default) or "json"

    Returns:
        Comparison matrix and lists of unique/shared records
    """
    client = app.get_client()

    try:
        # Helper to fetch and extract contents
        async def fetch_contents(cid):
            data = await client.query(GET_RECORD_WITH_ASSOCIATIONS_QUERY, {"id": cid})
            rec = data.get("fairsharingRecord", {})
            if not rec:
                return None

            contents = {}  # id -> record dict
            for a in rec.get("recordAssociations", []):
                if a.get("recordAssocLabel") == "collects":
                    lr = a.get("linkedRecord", {})
                    if lr.get("id"):
                        contents[lr["id"]] = lr
            return {"name": rec.get("name"), "contents": contents}

        c1 = await fetch_contents(collection_id_1)
        c2 = await fetch_contents(collection_id_2)

        if not c1:
            return f"Collection {collection_id_1} not found."
        if not c2:
            return f"Collection {collection_id_2} not found."

        ids1 = set(c1["contents"].keys())
        ids2 = set(c2["contents"].keys())

        shared = ids1 & ids2
        unique1 = ids1 - ids2
        unique2 = ids2 - ids1

        if output_format == "json":

            def _serialize_records(id_set, source):
                return [
                    {
                        "id": i,
                        "name": source[i].get("name"),
                        "registry": source[i].get("registry"),
                    }
                    for i in sorted(id_set)
                ]

            return json.dumps(
                {
                    "collections": [
                        {"id": collection_id_1, "name": c1["name"], "record_count": len(ids1)},
                        {"id": collection_id_2, "name": c2["name"], "record_count": len(ids2)},
                    ],
                    "shared_count": len(shared),
                    "unique_to_first_count": len(unique1),
                    "unique_to_second_count": len(unique2),
                    "jaccard_similarity": len(shared) / len(ids1 | ids2) if (ids1 or ids2) else 0.0,
                    "shared_records": _serialize_records(shared, c1["contents"]),
                    "unique_to_first": _serialize_records(unique1, c1["contents"]),
                    "unique_to_second": _serialize_records(unique2, c2["contents"]),
                },
                indent=2,
                default=str,
            )

        lines = [
            "# Collection Comparison",
            f"**Collection A:** {c1['name']} ({len(ids1)} records)",
            f"**Collection B:** {c2['name']} ({len(ids2)} records)",
            "",
            "## Overlap Summary",
            f"- **Shared:** {len(shared)} records",
            f"- **Unique to A:** {len(unique1)} records",
            f"- **Unique to B:** {len(unique2)} records",
            f"- **Jaccard Similarity:** {len(shared) / len(ids1 | ids2):.2f}"
            if (ids1 or ids2)
            else "- **Jaccard Similarity:** 0.0",
            "",
        ]

        # Break down by Registry
        def analyze_set(id_set, source_contents, title):
            if not id_set:
                return
            by_reg = Counter()
            for i in id_set:
                rec = source_contents[i]
                by_reg[rec.get("registry", "Unknown")] += 1

            lines.append(f"### {title}")
            for reg, count in by_reg.most_common():
                lines.append(f"- **{reg}:** {count}")

            # List top few
            lines.append("")
            lines.append("_Examples:_")
            sorted_ids = sorted(list(id_set))
            for i in sorted_ids[:5]:
                rec = source_contents[i]
                lines.append(f"- {rec.get('name')} ({rec.get('registry')})")
            if len(id_set) > 5:
                lines.append(f"  _(...and {len(id_set) - 5} more)_")
            lines.append("")

        analyze_set(shared, c1["contents"], "Shared Records")  # Can use either content dict
        analyze_set(unique1, c1["contents"], f"Unique to {c1['name']}")
        analyze_set(unique2, c2["contents"], f"Unique to {c2['name']}")

        return "\n".join(lines)

    except FAIRsharingError as e:
        return f"Error comparing collections: {e}"


@app.mcp.tool(
    name="fairsharing_find_compliant_standards",
    annotations=ToolAnnotations(
        readOnlyHint=True,
        idempotentHint=True,
        openWorldHint=True,
    ),
)
async def find_compliant_standards(
    policy_ids: Annotated[
        list[int], Field(min_length=2, max_length=5, description="List of policy record IDs")
    ],
    database_ids: Annotated[
        list[int] | None,
        Field(default=None, min_length=1, max_length=20, description="List of database record IDs"),
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
    """Find standards recommended by ALL given policies, optionally filtered by database implementation.

    Computes the intersection of standards across multiple policies (e.g., funder +
    institution + journal). Optionally further filters to standards implemented by
    ALL given databases. Returns tiered results: universally compliant, partially
    compliant (N-1 policies), and gaps.

    Use case: Building a Data Management Plan that must satisfy multiple overlapping
    policies, and identifying which candidate databases already implement the
    required standards.

    Args:
        policy_ids: List of 2-10 policy record IDs
        database_ids: Optional list of 1-10 database record IDs to check implementation
        output_format: "markdown" (default) or "json"

    Returns:
        Tiered compliance report showing universal, partial, and gap standards
    """
    client = app.get_client()

    if len(policy_ids) < 2:
        return "Please provide at least 2 policy IDs."
    if len(policy_ids) > 10:
        return "Please provide at most 10 policy IDs."
    if database_ids and len(database_ids) > 10:
        return "Please provide at most 10 database IDs."

    try:
        # Step 1: For each policy, extract recommended standards
        policy_standards: list[dict[str, dict]] = []
        policy_names: list[str] = []

        for pid in policy_ids:
            data = await client.query(GET_RECORD_WITH_ASSOCIATIONS_QUERY, {"id": pid})
            record = data.get("fairsharingRecord")
            if not record:
                return f"No record found with ID {pid}."
            if record.get("registry", "").lower() != "policy":
                return (
                    f"Record {pid} ({record.get('name', 'Unknown')}) is a "
                    f"{record.get('registry', 'Unknown')}, not a Policy."
                )

            name = record.get("abbreviation") or record.get("name", "Unknown")
            policy_names.append(name)

            stds: dict[str, dict] = {}
            for a in record.get("recordAssociations", []):
                lr = a.get("linkedRecord", {})
                if lr.get("registry") == "Standard" and lr.get("id"):
                    sid = str(lr["id"])
                    stds[sid] = {
                        "name": lr.get("name", "Unknown"),
                        "abbreviation": lr.get("abbreviation", ""),
                        "type": lr.get("type", ""),
                    }
            policy_standards.append(stds)

        # Step 2: Compute intersection tiers
        all_std_ids = [set(ps.keys()) for ps in policy_standards]
        n_policies = len(policy_ids)

        # Universal: recommended by ALL policies
        universal_ids = set.intersection(*all_std_ids) if all_std_ids else set()

        # Partial (N-1): recommended by all but one policy
        partial_ids: set[str] = set()
        if n_policies >= 3:
            for i in range(n_policies):
                others = [s for j, s in enumerate(all_std_ids) if j != i]
                if others:
                    partial = set.intersection(*others)
                    partial_ids |= partial - universal_ids

        # Build master info dict for all standards
        std_info: dict[str, dict] = {}
        std_coverage: dict[str, list[str]] = {}  # sid -> list of policy names
        for i, ps in enumerate(policy_standards):
            for sid, info in ps.items():
                if sid not in std_info:
                    std_info[sid] = info
                std_coverage.setdefault(sid, []).append(policy_names[i])

        # Step 3: If database_ids provided, compute database implementations
        db_standards: list[dict[str, dict]] = []
        db_names: list[str] = []

        if database_ids:
            for did in database_ids:
                data = await client.query(GET_RECORD_WITH_ASSOCIATIONS_QUERY, {"id": did})
                record = data.get("fairsharingRecord")
                if not record:
                    return f"No record found with ID {did}."
                if record.get("registry", "").lower() != "database":
                    return (
                        f"Record {did} ({record.get('name', 'Unknown')}) is a "
                        f"{record.get('registry', 'Unknown')}, not a Database."
                    )

                name = record.get("abbreviation") or record.get("name", "Unknown")
                db_names.append(name)

                stds: dict[str, dict] = {}
                for a in record.get("recordAssociations", []):
                    lr = a.get("linkedRecord", {})
                    if lr.get("registry") == "Standard" and lr.get("id"):
                        stds[str(lr["id"])] = {
                            "name": lr.get("name", "Unknown"),
                            "abbreviation": lr.get("abbreviation", ""),
                            "type": lr.get("type", ""),
                        }
                for a in record.get("reverseRecordAssociations", []):
                    lr = a.get("fairsharingRecord", {})
                    if lr.get("registry") == "Standard" and lr.get("id"):
                        sid = str(lr["id"])
                        if sid not in stds:
                            stds[sid] = {
                                "name": lr.get("name", "Unknown"),
                                "abbreviation": lr.get("abbreviation", ""),
                                "type": lr.get("type", ""),
                            }
                db_standards.append(stds)

        # Step 4: Compute final intersections if databases provided
        all_db_std_ids = [set(ds.keys()) for ds in db_standards]
        db_implemented = set.intersection(*all_db_std_ids) if all_db_std_ids else None

        # Step 5: Build output
        if output_format == "json":
            return json.dumps(
                {
                    "policies": [
                        {
                            "id": policy_ids[i],
                            "name": policy_names[i],
                            "standards_count": len(policy_standards[i]),
                        }
                        for i in range(n_policies)
                    ],
                    "databases": [
                        {"id": database_ids[i], "name": db_names[i]}
                        for i in range(len(database_ids))
                    ]
                    if database_ids
                    else [],
                    "universal_standards": [
                        {"id": sid, **std_info[sid]} for sid in sorted(universal_ids)
                    ],
                    "partial_standards": [
                        {
                            "id": sid,
                            **std_info[sid],
                            "covered_by": std_coverage.get(sid, []),
                        }
                        for sid in sorted(partial_ids)
                    ],
                    "db_implemented": sorted(db_implemented)
                    if db_implemented is not None
                    else None,
                    "standards": {
                        sid: {
                            **info,
                            "covered_by": std_coverage.get(sid, []),
                        }
                        for sid, info in std_info.items()
                    },
                },
                indent=2,
                default=str,
            )

        lines = [
            "# Compliant Standards Analysis",
            "",
            "**Policies ({n}):** {pols}".format(
                n=n_policies,
                pols=", ".join(
                    f"{policy_names[i]} (ID: {policy_ids[i]})" for i in range(n_policies)
                ),
            ),
        ]
        if database_ids:
            lines.append(
                "**Databases ({n}):** {dbs}".format(
                    n=len(database_ids),
                    dbs=", ".join(
                        f"{db_names[i]} (ID: {database_ids[i]})" for i in range(len(database_ids))
                    ),
                )
            )
        lines.append("")

        # Universal standards
        lines.append(f"## Universally Required Standards ({len(universal_ids)})")
        lines.append("_Recommended by ALL policies_")
        lines.append("")
        if universal_ids:
            for sid in sorted(universal_ids, key=lambda s: std_info.get(s, {}).get("name", "")):
                info = std_info[sid]
                entry = f"- **{info['name']}**"
                if info.get("abbreviation"):
                    entry += f" ({info['abbreviation']})"
                if info.get("type"):
                    entry += f" [{info['type']}]"

                if db_implemented is not None:
                    if sid in db_implemented:
                        entry += " -- Implemented by all databases"
                    else:
                        implementing = [
                            db_names[j] for j, ds in enumerate(db_standards) if sid in ds
                        ]
                        if implementing:
                            entry += f" -- Implemented by: {', '.join(implementing)}"
                        else:
                            entry += " -- **GAP: Not implemented by any database**"
                lines.append(entry)
        else:
            lines.append("_No standards are recommended by all policies._")
        lines.append("")

        # Partial standards (N-1)
        if n_policies >= 3 and partial_ids:
            lines.append(f"## Partially Required Standards ({len(partial_ids)})")
            lines.append(f"_Recommended by {n_policies - 1} of {n_policies} policies_")
            lines.append("")
            for sid in sorted(partial_ids, key=lambda s: std_info.get(s, {}).get("name", "")):
                info = std_info[sid]
                entry = f"- **{info['name']}**"
                if info.get("abbreviation"):
                    entry += f" ({info['abbreviation']})"
                coverage = std_coverage.get(sid, [])
                entry += f" (by: {', '.join(coverage)})"
                lines.append(entry)
            lines.append("")

        # Database gap analysis
        if db_implemented is not None and universal_ids:
            fully_compliant = universal_ids & db_implemented
            gaps = universal_ids - db_implemented

            pct = (len(fully_compliant) / len(universal_ids) * 100) if universal_ids else 0
            lines.append("## Database Compliance Summary")
            lines.append(f"- **Standards required by all policies:** {len(universal_ids)}")
            lines.append(f"- **Implemented by all databases:** {len(fully_compliant)} ({pct:.0f}%)")
            lines.append(f"- **Gaps (not implemented):** {len(gaps)}")
            lines.append("")

            if gaps:
                lines.append("### Standards Gaps")
                for sid in sorted(gaps, key=lambda s: std_info.get(s, {}).get("name", "")):
                    info = std_info[sid]
                    entry = f"- {info['name']}"
                    if info.get("abbreviation"):
                        entry += f" ({info['abbreviation']})"
                    lines.append(entry)
                lines.append("")

        # Per-policy statistics
        lines.append("## Per-Policy Statistics")
        for i, pname in enumerate(policy_names):
            count = len(policy_standards[i])
            lines.append(f"- **{pname}:** {count} standards recommended")

        return "\n".join(lines)

    except FAIRsharingError as e:
        return f"Error finding compliant standards: {e}"


@app.mcp.tool(
    name="fairsharing_assess_dmp_compliance",
    annotations=ToolAnnotations(
        readOnlyHint=True,
        idempotentHint=True,
        openWorldHint=True,
    ),
)
async def assess_dmp_compliance(
    policy_id: Annotated[int, Field(ge=1, description="Policy record ID")],
    database_ids: Annotated[
        list[int],
        Field(min_length=1, max_length=20, description="List of database record IDs"),
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
    """Generate a complete DMP compliance plan for one policy against 1-5 databases.

    Combines policy mandate details, standards compliance checking, FAIR quality
    assessment, and gap analysis into a single actionable report. This is the
    primary tool for DMP compliance assessment — it replaces the need to call
    get_policy_details, check_policy_database_compliance, and
    get_database_quality_profile separately.

    Args:
        policy_id: The FAIRsharing policy record ID
        database_ids: List of 1-5 database record IDs to assess
        output_format: "markdown" (default) or "json"

    Returns:
        Comprehensive DMP compliance report with per-database compliance matrix,
        FAIR quality scores, gap analysis, and prioritized recommendations
    """
    if not database_ids or len(database_ids) < 1:
        return "Please provide at least 1 database record ID."
    if len(database_ids) > 5:
        return "Please provide at most 5 database record IDs."

    client = app.get_client()

    try:
        # 1. Fetch policy with mandate data
        policy = await helpers.fetch_policy_with_fallback(policy_id)
        if not policy:
            return f"No record found with ID {policy_id}."
        if policy.get("registry", "").lower() != "policy":
            return (
                f"Record {policy_id} ({policy.get('name', 'Unknown')}) is a "
                f"{policy.get('registry', 'Unknown')}, not a Policy."
            )

        pol_name = policy.get("name", "Unknown")

        # Extract policy's recommended standards
        policy_standards: dict[str, dict] = {}
        for a in policy.get("recordAssociations", []):
            lr = a.get("linkedRecord", {})
            if lr.get("registry") == "Standard" and lr.get("id"):
                sid = str(lr["id"])
                policy_standards[sid] = {
                    "name": lr.get("name", "Unknown"),
                    "abbreviation": lr.get("abbreviation", ""),
                    "type": lr.get("type", "unknown"),
                }

        # Extract mandate summary
        mandate_summary = {}
        for field in (
            "mandatedDataSharing",
            "mandatedDmpCreation",
            "sharingResearchSoftware",
            "metadataSharing",
        ):
            val = policy.get(field)
            if val:
                mandate_summary[field] = val

        # 2. Fetch databases (quality + associations) in parallel
        db_data: list[dict] = []
        for db_id in database_ids:
            db_record = await helpers.fetch_database_quality_with_fallback(db_id)
            assoc_data = await client.query(GET_RECORD_WITH_ASSOCIATIONS_QUERY, {"id": db_id})
            assoc_record = assoc_data.get("fairsharingRecord", {})
            if not db_record:
                db_data.append({"id": db_id, "error": f"Could not fetch database {db_id}."})
                continue
            if db_record.get("registry", "").lower() != "database":
                db_data.append(
                    {
                        "id": db_id,
                        "error": f"Record {db_id} ({db_record.get('name', 'Unknown')}) is not a Database.",
                    }
                )
                continue

            # Extract DB's implemented standards
            db_standards: dict[str, dict] = {}
            for a in assoc_record.get("recordAssociations", []):
                lr = a.get("linkedRecord", {})
                if lr.get("registry") == "Standard" and lr.get("id"):
                    sid = str(lr["id"])
                    db_standards[sid] = {
                        "name": lr.get("name", "Unknown"),
                        "abbreviation": lr.get("abbreviation", ""),
                        "type": lr.get("type", "unknown"),
                    }
            for a in assoc_record.get("reverseRecordAssociations", []):
                lr = a.get("fairsharingRecord", {})
                if lr.get("registry") == "Standard" and lr.get("id"):
                    sid = str(lr["id"])
                    if sid not in db_standards:
                        db_standards[sid] = {
                            "name": lr.get("name", "Unknown"),
                            "abbreviation": lr.get("abbreviation", ""),
                            "type": lr.get("type", "unknown"),
                        }

            # Compute FAIR score
            detailed = compute_fair_score_detailed(db_record)
            normalized = normalize_quality_score(
                detailed["score"], 9.0, "Database", detailed["confidence"]
            )

            # Compliance sets
            pol_ids = set(policy_standards.keys())
            db_ids_set = set(db_standards.keys())
            compliant = pol_ids & db_ids_set
            gaps = pol_ids - db_ids_set

            compliance_pct = (len(compliant) / len(pol_ids) * 100) if pol_ids else 100.0

            db_data.append(
                {
                    "id": db_id,
                    "name": db_record.get("name", "Unknown"),
                    "abbreviation": db_record.get("abbreviation", ""),
                    "standards": db_standards,
                    "compliant": compliant,
                    "gaps": gaps,
                    "compliance_pct": compliance_pct,
                    "fair_score": detailed["score"],
                    "fair_total_rated": detailed["total_rated"],
                    "fair_grade": detailed["grade"],
                    "fair_confidence": detailed["confidence"],
                    "normalized_score": normalized["normalized_score"],
                    "unified_grade": normalized["unified_grade"],
                    "indicators": {
                        f: db_record.get(f)
                        for f in (
                            "dataAccessCondition",
                            "dataCuration",
                            "dataDepositionCondition",
                            "dataPreservationPolicy",
                            "resourceSustainability",
                            "usesPersistentIdentifier",
                        )
                    },
                }
            )

        # 3. Cross-DB gap analysis
        valid_dbs = [d for d in db_data if "error" not in d]
        all_db_standards = set()
        for d in valid_dbs:
            all_db_standards.update(d["standards"].keys())
        universal_gaps = set(policy_standards.keys()) - all_db_standards

        # 4. Generate recommendations
        recommendations = []

        # Critical: standards required by policy, implemented by no DB
        for sid in universal_gaps:
            s = policy_standards[sid]
            name = s["abbreviation"] or s["name"]
            recommendations.append(
                ("Critical", f"No database implements **{name}** (policy-required standard)")
            )

        # High: mandate vs FAIR indicator mismatches
        for d in valid_dbs:
            db_label = d.get("abbreviation") or d["name"]
            if mandate_summary.get("mandatedDataSharing") in ("required", "suggested"):
                access = d["indicators"].get("dataAccessCondition")
                if access and access.lower() in ("not found", "none"):
                    recommendations.append(
                        (
                            "High",
                            f"Policy mandates data sharing but **{db_label}** has dataAccessCondition='{access}'",
                        )
                    )
            if mandate_summary.get("mandatedDmpCreation") in ("required", "suggested"):
                preservation = d["indicators"].get("dataPreservationPolicy")
                if preservation is False or (
                    isinstance(preservation, str) and preservation.lower() in ("no", "not found")
                ):
                    recommendations.append(
                        (
                            "High",
                            f"Policy mandates DMP creation but **{db_label}** lacks data preservation policy",
                        )
                    )

        # Medium: standards implemented by some DBs but not all
        if len(valid_dbs) > 1:
            for sid in set(policy_standards.keys()) - universal_gaps:
                implementing = [d for d in valid_dbs if sid in d["standards"]]
                if 0 < len(implementing) < len(valid_dbs):
                    s = policy_standards[sid]
                    name = s["abbreviation"] or s["name"]
                    recommendations.append(
                        (
                            "Medium",
                            f"**{name}** implemented by {len(implementing)}/{len(valid_dbs)} databases",
                        )
                    )

        # Low: DB-specific FAIR improvements
        for d in valid_dbs:
            if d["fair_score"] < 5 and d["fair_total_rated"] > 0:
                db_label = d.get("abbreviation") or d["name"]
                recommendations.append(
                    (
                        "Low",
                        f"**{db_label}** FAIR score is {d['fair_score']:.1f}/{d['fair_total_rated']} ({d['fair_grade']})",
                    )
                )

        # 5. Format output
        if output_format == "json":
            return json.dumps(
                {
                    "policy": {
                        "id": policy_id,
                        "name": pol_name,
                        "mandates": mandate_summary,
                        "recommended_standards_count": len(policy_standards),
                    },
                    "databases": [
                        {k: v for k, v in d.items() if k not in ("standards",)} for d in db_data
                    ],
                    "universal_gaps": [
                        {"id": sid, **policy_standards[sid]} for sid in universal_gaps
                    ],
                    "recommendations": [{"severity": s, "message": m} for s, m in recommendations],
                },
                indent=2,
                default=str,
            )

        lines = [
            "# DMP Compliance Assessment",
            f"**Policy:** {pol_name} (ID: {policy_id})",
            f"**Databases assessed:** {len(database_ids)}",
            "",
        ]

        # Mandate summary
        if mandate_summary:
            lines.append("## Policy Mandates")
            for field, val in mandate_summary.items():
                lines.append(f"- **{field}:** {val}")
            lines.append("")
        elif policy.get("_mandate_data_unavailable") or policy.get("_mandate_extraction_failed"):
            lines.append("## Policy Mandates")
            lines.append(
                "_Mandate data could not be loaded. Compliance assessment is based on standards only._"
            )
            lines.append("")

        # Per-database compliance
        lines.append("## Per-Database Compliance")
        lines.append("")
        lines.append(f"**Policy recommends {len(policy_standards)} standard(s)**")
        lines.append("")

        if policy_standards:
            lines.append("| Database | Compliance | FAIR Score | FAIR/100 | Grade |")
            lines.append("|----------|-----------|------------|----------|-------|")
            for d in db_data:
                if "error" in d:
                    lines.append(f"| ID {d['id']} | ERROR | — | — | — |")
                else:
                    db_label = d.get("abbreviation") or d["name"]
                    if len(db_label) > 25:
                        db_label = db_label[:22] + "..."
                    lines.append(
                        f"| {db_label} | {d['compliance_pct']:.0f}% "
                        f"({len(d['compliant'])}/{len(policy_standards)}) | "
                        f"{d['fair_score']:.1f}/{d['fair_total_rated']} | "
                        f"{d['normalized_score']}/100 | {d['unified_grade']} |"
                    )
            lines.append("")
        else:
            lines.append("_Policy has no recommended standards._")
            lines.append("")

        # Gap analysis
        if universal_gaps:
            lines.append(
                f"## Standards Gap ({len(universal_gaps)} not implemented by any database)"
            )
            for sid in sorted(universal_gaps):
                s = policy_standards[sid]
                name = s["name"]
                abbrev = s["abbreviation"]
                entry = f"- **{name}**"
                if abbrev:
                    entry += f" ({abbrev})"
                lines.append(entry)
            lines.append("")

        # Recommendations
        if recommendations:
            severity_order = {"Critical": 0, "High": 1, "Medium": 2, "Low": 3}
            recommendations.sort(key=lambda r: severity_order.get(r[0], 99))
            lines.append("## Recommendations")
            for severity, msg in recommendations:
                lines.append(f"- **[{severity}]** {msg}")
            lines.append("")

        # Errors
        db_errors = [d for d in db_data if "error" in d]
        if db_errors:
            lines.append("## Errors")
            for d in db_errors:
                lines.append(f"- {d['error']}")

        return "\n".join(lines)

    except FAIRsharingError as e:
        return f"Error assessing DMP compliance: {e}"


@app.mcp.tool(
    name="fairsharing_analyze_transitive_impact",
    annotations=ToolAnnotations(
        readOnlyHint=True,
        idempotentHint=True,
        openWorldHint=True,
    ),
)
async def analyze_transitive_impact(
    record_id: Annotated[int, Field(ge=1, description="FAIRsharing record ID")],
    max_depth: Annotated[
        int, Field(default=3, ge=1, le=5, description="Maximum traversal depth")
    ] = 3,
    output_format: Annotated[
        str,
        Field(
            default="markdown",
            pattern="^(markdown|json)$",
            description="Output format: 'markdown' or 'json'",
        ),
    ] = "markdown",
) -> str:
    """Perform multi-hop deprecation impact analysis via BFS.

    Starting from a (typically deprecated) record, traverses its active
    dependents (via reverseRecordAssociations), then each dependent's
    dependents, up to max_depth hops. Tracks the full chain path for each
    impacted record.

    IMPORTANT: Each hop requires one API call per node visited. The tool
    caps total fetches at 100 to avoid runaway queries.

    Args:
        record_id: The record to trace impact from (typically deprecated)
        max_depth: Maximum traversal depth (default: 3, range: 1-5)
        output_format: "markdown" (default) or "json"

    Returns:
        Multi-level impact report with chain paths, depth/registry aggregations
    """
    max_depth = min(max(1, max_depth), 5)
    client = app.get_client()
    max_fetch = 100

    try:
        # Fetch the root record
        data = await client.query(GET_RECORD_WITH_ASSOCIATIONS_QUERY, {"id": record_id})
        root = data.get("fairsharingRecord")
        if not root:
            return f"No record found with ID {record_id}."

        root_name = root.get("name", "Unknown")
        root_status = root.get("status", "Unknown")
        root_registry = root.get("registry", "Unknown")

        visited: set[int] = {record_id}
        queue: deque[tuple[int, str, str, list[tuple[str, str]], int]] = deque()
        # (id, name, registry, path_so_far, depth)
        queue.append((record_id, root_name, root_registry, [], 0))

        results_by_depth: dict[int, list[dict]] = {}
        total_fetched = 0
        truncated = False

        while queue:
            if total_fetched >= max_fetch:
                truncated = True
                break

            current_id, current_name, current_registry, path, depth = queue.popleft()

            if depth >= max_depth:
                continue

            # Fetch current node's reverse associations
            if current_id != record_id:
                # Root already fetched
                try:
                    data = await client.query(
                        GET_RECORD_WITH_ASSOCIATIONS_QUERY, {"id": current_id}
                    )
                except FAIRsharingError:
                    continue
                current_record = data.get("fairsharingRecord", {})
            else:
                current_record = root

            total_fetched += 1

            for assoc in current_record.get("reverseRecordAssociations", []):
                dependent = assoc.get("fairsharingRecord", {})
                dep_id = dependent.get("id")
                dep_status = (dependent.get("status") or "").lower()
                dep_name = dependent.get("name", "Unknown")
                dep_registry = dependent.get("registry", "Unknown")
                rel_label = assoc.get("recordAssocLabel", "related_to")

                if dep_status != "ready" or not dep_id or dep_id in visited:
                    continue

                visited.add(dep_id)
                new_path = path + [(current_name, rel_label)]
                next_depth = depth + 1

                impact_entry = {
                    "id": dep_id,
                    "name": dep_name,
                    "registry": dep_registry,
                    "relationship": rel_label,
                    "depth": next_depth,
                    "chain": new_path + [(dep_name, "")],
                }
                results_by_depth.setdefault(next_depth, []).append(impact_entry)

                if next_depth < max_depth:
                    queue.append((dep_id, dep_name, dep_registry, new_path, next_depth))

        total_impacted = sum(len(v) for v in results_by_depth.values())

        if output_format == "json":
            return json.dumps(
                {
                    "root": {
                        "id": record_id,
                        "name": root_name,
                        "status": root_status,
                        "registry": root_registry,
                    },
                    "max_depth": max_depth,
                    "total_impacted": total_impacted,
                    "total_fetched": total_fetched,
                    "truncated": truncated,
                    "by_depth": {
                        str(d): [
                            {**e, "chain": [f"{n} --[{r}]-->" if r else n for n, r in e["chain"]]}
                            for e in entries
                        ]
                        for d, entries in sorted(results_by_depth.items())
                    },
                },
                indent=2,
                default=str,
            )

        # Markdown output
        lines = [
            f"# Transitive Impact Analysis: {root_name}",
            f"**Status:** {root_status}",
            f"**Registry:** {root_registry}",
            f"**Max depth:** {max_depth}",
            f"**Total active dependents found:** {total_impacted}",
            f"**API calls used:** {total_fetched}/{max_fetch}",
            "",
        ]

        if root_status.lower() != "deprecated":
            lines.append(
                f"_Note: This record is '{root_status}', not 'deprecated'. "
                "Impact analysis still shows active dependents._"
            )
            lines.append("")

        if total_impacted == 0:
            lines.append("No active records found that depend on this record.")
            return "\n".join(lines)

        # Per-depth sections
        for depth in sorted(results_by_depth.keys()):
            entries = results_by_depth[depth]
            label = "Direct Dependents" if depth == 1 else f"Depth {depth} (Transitive)"
            lines.append(f"## {label} ({len(entries)} records)")
            lines.append("")

            for e in sorted(entries, key=lambda x: (x["registry"], x["name"])):
                # Render chain path
                chain_parts = []
                for name, rel in e["chain"]:
                    if rel:
                        chain_parts.append(f"{name} --[{rel}]-->")
                    else:
                        chain_parts.append(name)
                chain_str = " ".join(chain_parts)
                lines.append(
                    f"- **{e['name']}** ({e['registry']}) [ID: {e['id']}] via {e['relationship']}"
                )
                if depth > 1:
                    lines.append(f"  - Chain: {chain_str}")
            lines.append("")

        # Aggregation: by registry × depth
        lines.append("## Impact Summary")
        all_registries = sorted(
            {e["registry"] for entries in results_by_depth.values() for e in entries}
        )
        all_depths = sorted(results_by_depth.keys())

        header = "| Registry |" + "".join(f" Depth {d} |" for d in all_depths) + " Total |"
        sep = "|----------|" + "--------|" * len(all_depths) + "-------|"
        lines.append(header)
        lines.append(sep)
        for reg in all_registries:
            row = f"| {reg} |"
            total = 0
            for d in all_depths:
                count = sum(1 for e in results_by_depth.get(d, []) if e["registry"] == reg)
                row += f" {count} |"
                total += count
            row += f" {total} |"
            lines.append(row)
        lines.append("")

        # Risk highlight: chains reaching Policy records
        policy_impacts = [
            e for entries in results_by_depth.values() for e in entries if e["registry"] == "Policy"
        ]
        if policy_impacts:
            lines.append(f"**Risk:** {len(policy_impacts)} policy record(s) transitively impacted:")
            for e in policy_impacts:
                chain_parts = []
                for name, rel in e["chain"]:
                    if rel:
                        chain_parts.append(f"{name} --[{rel}]-->")
                    else:
                        chain_parts.append(name)
                lines.append(f"- {' '.join(chain_parts)}")
            lines.append("")

        if truncated:
            lines.append(
                f"**WARNING:** Traversal was truncated at {max_fetch} API calls. "
                "Some transitive dependents may not be shown."
            )

        return "\n".join(lines)

    except FAIRsharingError as e:
        return f"Error analyzing transitive impact: {e}"
