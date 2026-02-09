"""FAIRsharing MCP tools — Standard-database relationships."""

import json
import logging
from collections import Counter

from fairsharing_mcp import app
from fairsharing_mcp.client import FAIRsharingError
from fairsharing_mcp.constants import STANDARD_COMPREHENSIVE_WEIGHTS
from fairsharing_mcp.queries import (
    GET_LATEST_STATS_QUERY,
    GET_RECORD_WITH_ASSOCIATIONS_QUERY,
    MULTI_TAG_FILTER_QUERY,
    SEARCH_RECORDS_COMPACT_QUERY,
)

logger = logging.getLogger(__name__)


def _score_standard(record: dict) -> dict:
    """Compute quality score for a Standard record (pure function, no API calls).

    Returns dict with: score, max, grade, components, confidence, confidence_note.
    """
    score = 0.0
    components = []

    # 1. Identity & Access (Max 3)
    if record.get("homepage"):
        score += 1.0
        components.append("- Homepage available (+1.0)")
    if record.get("doi"):
        score += 1.0
        components.append("- DOI/PID available (+1.0)")
    if record.get("description") and len(record.get("description", "")) > 50:
        score += 1.0
        components.append("- Detailed description (+1.0)")

    # 2. Status & Maintenance (Max 3)
    status = record.get("status", "").lower()
    if status == "ready":
        score += 2.0
        components.append("- Status is Ready (+2.0)")
    elif status == "uncertain":
        score += 0.5
        components.append("- Status is Uncertain (+0.5)")

    if record.get("isMaintained"):
        score += 1.0
        components.append("- Actively Maintained (+1.0)")

    # 3. Usage & Connectivity (Max 4)
    incoming = record.get("reverseRecordAssociations", [])
    implementers = sum(
        1
        for a in incoming
        if a.get("recordAssocLabel") == "implements"
        and a.get("fairsharingRecord", {}).get("registry") == "Database"
    )
    recommenders = sum(
        1
        for a in incoming
        if a.get("recordAssocLabel") == "recommends"
        and a.get("fairsharingRecord", {}).get("registry") == "Policy"
    )

    if implementers > 0:
        if implementers >= 11:
            impl_score = 2.0
        elif implementers >= 6:
            impl_score = 1.5
        elif implementers >= 3:
            impl_score = 1.0
        else:
            impl_score = 0.5
        score += impl_score
        components.append(f"- Implemented by {implementers} databases (+{impl_score})")

    if recommenders > 0:
        if recommenders >= 5:
            rec_score = 2.0
        elif recommenders >= 3:
            rec_score = 1.5
        else:
            rec_score = 1.0
        score += rec_score
        components.append(f"- Recommended by {recommenders} policies (+{rec_score})")

    # Grade
    grade = "D"
    if score >= 9:
        grade = "A+"
    elif score >= 8:
        grade = "A"
    elif score >= 6:
        grade = "B"
    elif score >= 4:
        grade = "C"

    # Confidence assessment
    data_points_checked = 0
    data_points_present = 0
    for field in ("homepage", "doi", "description", "isMaintained"):
        data_points_checked += 1
        if record.get(field):
            data_points_present += 1
    data_points_checked += 1
    data_points_present += 1  # status always present
    data_points_checked += 2
    data_points_present += 2  # adoption data always fetched

    if data_points_present >= data_points_checked - 1:
        confidence = "high"
        confidence_note = (
            f"All key metadata fields present ({data_points_present}/{data_points_checked})."
        )
    elif data_points_present >= data_points_checked // 2:
        confidence = "medium"
        missing_fields = []
        if not record.get("homepage"):
            missing_fields.append("homepage")
        if not record.get("doi"):
            missing_fields.append("DOI")
        if not record.get("description"):
            missing_fields.append("description")
        confidence_note = (
            f"Missing: {', '.join(missing_fields)}. Score reflects available data only."
        )
    else:
        confidence = "low"
        confidence_note = (
            f"Only {data_points_present}/{data_points_checked} data points available. "
            "Score may not reflect actual quality."
        )

    return {
        "score": score,
        "max": 10.0,
        "grade": grade,
        "components": components,
        "confidence": confidence,
        "confidence_note": confidence_note,
    }


def _score_standard_comprehensive(record: dict) -> dict:
    """Compute comprehensive quality score for a Standard with domain-specific indicators.

    Extends _score_standard() with temporal health and community engagement metrics.
    Pure function, no API calls.

    Returns dict with: basic (from _score_standard), indicators (per-category breakdown),
    total_score, max_score, grade, confidence, confidence_note.
    """
    from datetime import datetime, timezone

    basic = _score_standard(record)
    indicators: dict[str, dict] = {}
    total_score = 0.0
    max_score = sum(STANDARD_COMPREHENSIVE_WEIGHTS.values())

    # ── Identity & Access + Status & Maintenance + Usage (from basic scorer) ──
    basic_identity = 0.0
    basic_maintenance = 0.0
    basic_adoption = 0.0
    for comp in basic["components"]:
        if any(k in comp for k in ("Homepage", "DOI", "description")):
            # Extract score from component string
            try:
                basic_identity += float(comp.split("+")[1].rstrip(")"))
            except (IndexError, ValueError):
                pass
        elif any(k in comp for k in ("Status", "Maintained")):
            try:
                basic_maintenance += float(comp.split("+")[1].rstrip(")"))
            except (IndexError, ValueError):
                pass
        elif any(k in comp for k in ("Implemented", "Recommended")):
            try:
                basic_adoption += float(comp.split("+")[1].rstrip(")"))
            except (IndexError, ValueError):
                pass

    indicators["identity_access"] = {
        "score": basic_identity,
        "max": STANDARD_COMPREHENSIVE_WEIGHTS["identity_access"],
        "details": [
            c
            for c in basic["components"]
            if any(k in c for k in ("Homepage", "DOI", "description"))
        ],
    }
    indicators["maintenance"] = {
        "score": basic_maintenance,
        "max": STANDARD_COMPREHENSIVE_WEIGHTS["maintenance"],
        "details": [
            c for c in basic["components"] if any(k in c for k in ("Status", "Maintained"))
        ],
    }
    indicators["adoption_breadth"] = {
        "score": basic_adoption,
        "max": STANDARD_COMPREHENSIVE_WEIGHTS["adoption_breadth"],
        "details": [
            c for c in basic["components"] if any(k in c for k in ("Implemented", "Recommended"))
        ],
    }
    total_score += basic_identity + basic_maintenance + basic_adoption

    # ── Temporal Health (max from weights) ──
    temporal_max = STANDARD_COMPREHENSIVE_WEIGHTS["temporal_health"]
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

    # ── Community Engagement (max from weights) ──
    community_max = STANDARD_COMPREHENSIVE_WEIGHTS["community_engagement"]
    community_score = 0.0
    community_details = []
    pubs = record.get("publications", [])
    pub_count = len(pubs) if isinstance(pubs, list) else 0
    if pub_count >= 5:
        pub_score = community_max * 0.75
        community_score += pub_score
        community_details.append(f"{pub_count} publications (+{pub_score:.1f})")
    elif pub_count >= 1:
        pub_score = community_max * 0.5
        community_score += pub_score
        community_details.append(f"{pub_count} publication(s) (+{pub_score:.1f})")
    else:
        community_details.append("No publications (+0.0)")

    subjects = record.get("subjects", [])
    subject_count = len(subjects) if isinstance(subjects, list) else 0
    if subject_count >= 3:
        subj_score = community_max * 0.25
        community_score += subj_score
        community_details.append(f"{subject_count} subject areas (+{subj_score:.1f})")

    indicators["community_engagement"] = {
        "score": community_score,
        "max": community_max,
        "details": community_details,
    }
    total_score += community_score

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
        "basic": basic,
        "indicators": indicators,
        "total_score": round(total_score, 1),
        "max_score": max_score,
        "normalized_pct": round(pct, 1),
        "grade": grade,
        "confidence": basic["confidence"],
        "confidence_note": basic["confidence_note"],
    }


@app.mcp.tool()
async def find_standards_for_database(
    record_id: int,
    output_format: str = "markdown",
) -> str:
    """Find all standards used by or related to a specific database.

    Given a database record, identifies which standards it implements,
    what formats it uses, and what reporting guidelines apply.
    The record is typically a Database; if another registry type is passed,
    associations to Standards are still returned (with a note).

    Args:
        record_id: The database record ID (typically a Database)
        output_format: "markdown" (default) or "json" for structured data.

    Returns:
        List of associated standards grouped by relationship type
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

        lines = [
            f"# Standards for: {name}" + (f" ({abbrev})" if abbrev else ""),
            f"**Registry:** {registry} | **Type:** {record.get('type', 'N/A')}",
            "",
        ]
        if registry.lower() != "database":
            lines.append(
                f"_Note: This record is a {registry}, not a Database. Showing Standards linked to it._"
            )
            lines.append("")

        # Look at outgoing associations to find standards
        outgoing = record.get("recordAssociations", [])
        standards_out: dict[str, list] = {}
        for a in outgoing:
            lr = a.get("linkedRecord", {})
            if lr.get("registry", "").lower() == "standard":
                label = a.get("recordAssocLabel", "related_to")
                standards_out.setdefault(label, []).append(lr)

        # Look at incoming associations from standards
        incoming = record.get("reverseRecordAssociations", [])
        standards_in: dict[str, list] = {}
        for a in incoming:
            lr = a.get("fairsharingRecord", {})
            if lr.get("registry", "").lower() == "standard":
                label = a.get("recordAssocLabel", "related_to")
                standards_in.setdefault(label, []).append(lr)

        total_standards = sum(len(v) for v in standards_out.values()) + sum(
            len(v) for v in standards_in.values()
        )

        if output_format == "json":
            all_stds = []
            for label, items in standards_out.items():
                for s in items:
                    all_stds.append(
                        {
                            "id": s.get("id", ""),
                            "name": s.get("name", "Unknown"),
                            "registry": "Standard",
                            "relationship": label,
                            "direction": "outgoing",
                        }
                    )
            for label, items in standards_in.items():
                for s in items:
                    all_stds.append(
                        {
                            "id": s.get("id", ""),
                            "name": s.get("name", "Unknown"),
                            "registry": "Standard",
                            "relationship": label,
                            "direction": "incoming",
                        }
                    )
            return json.dumps(
                {
                    "database_id": record.get("id", record_id),
                    "database_name": name,
                    "standards": all_stds,
                },
                indent=2,
            )

        if total_standards == 0:
            lines.append("_No associated standards found for this record._")
            return "\n".join(lines)

        lines.append(f"**Total associated standards:** {total_standards}")
        lines.append("")

        def _format_standard_list(standards: list) -> list[str]:
            result = []
            for s in sorted(standards, key=lambda x: x.get("name", "")):
                sname = s.get("name", "Unknown")
                sabbrev = s.get("abbreviation", "")
                stype = s.get("type", "")
                sid = s.get("id", "")
                sstatus = s.get("status", "")
                entry = f"  - **{sname}**"
                if sabbrev:
                    entry += f" ({sabbrev})"
                if stype:
                    entry += f" [{stype}]"
                entry += f" (ID: {sid})"
                if sstatus and sstatus != "ready":
                    entry += f" _{sstatus}_"
                result.append(entry)
            return result

        if standards_out:
            lines.append("## Standards This Database References (outgoing)")
            for label in sorted(standards_out):
                items = standards_out[label]
                lines.append(f"### {label} ({len(items)})")
                lines.extend(_format_standard_list(items))
            lines.append("")

        if standards_in:
            lines.append("## Standards That Reference This Database (incoming)")
            for label in sorted(standards_in):
                items = standards_in[label]
                lines.append(f"### {label} ({len(items)})")
                lines.extend(_format_standard_list(items))
            lines.append("")

        # Categorize by standard type
        all_standards = []
        for items in standards_out.values():
            all_standards.extend(items)
        for items in standards_in.values():
            all_standards.extend(items)

        type_counts = Counter(s.get("type", "unknown") for s in all_standards)
        lines.append("## Standards by Type")
        for stype, count in type_counts.most_common():
            lines.append(f"- **{stype}:** {count}")

        # Semantic category grouping
        category_map = {
            "model/format": "Data Formats",
            "reporting_guideline": "Reporting Guidelines",
            "terminology_artefact": "Terminologies & Ontologies",
            "metric": "Metrics",
            "identifier_schema": "Identifier Schemas",
        }
        by_category: dict[str, list] = {}
        for s in all_standards:
            stype = s.get("type", "unknown")
            category = category_map.get(stype, "Other")
            by_category.setdefault(category, []).append(s)

        if len(by_category) > 1:
            lines.append("")
            lines.append("## Standards by Category")
            for cat in [
                "Data Formats",
                "Reporting Guidelines",
                "Terminologies & Ontologies",
                "Identifier Schemas",
                "Metrics",
                "Other",
            ]:
                if cat in by_category:
                    items = by_category[cat]
                    names = sorted(s.get("name", "Unknown") for s in items)
                    lines.append(f"- **{cat} ({len(items)}):** {', '.join(names[:10])}")
                    if len(names) > 10:
                        lines[-1] += f" _(+{len(names) - 10} more)_"

        return "\n".join(lines)

    except FAIRsharingError as e:
        return f"Error finding standards: {e}"


@app.mcp.tool()
async def find_databases_for_standard(
    record_id: int,
    countries: list[str] | None = None,
    output_format: str = "markdown",
) -> str:
    """Find all databases that implement or use a specific standard.

    Given a standard record, identifies which databases implement it
    and how they relate.
    The record is typically a Standard; if another registry type is passed,
    associations to Databases are still returned (with a note).

    Args:
        record_id: The standard record ID (typically a Standard)
        countries: Optional country filter — when provided, the output notes
                   which countries were requested (country filtering is best-effort
                   as database country data requires additional lookups)
        output_format: "markdown" (default) or "json" for structured data.

    Returns:
        List of databases implementing or related to this standard
    """
    client = app.get_client()

    try:
        data = await client.query(GET_RECORD_WITH_ASSOCIATIONS_QUERY, {"id": record_id})
        record = data.get("fairsharingRecord")

        if not record:
            return f"No record found with ID {record_id}."

        name = record.get("name", "Unknown")
        abbrev = record.get("abbreviation", "")

        lines = [
            f"# Databases for: {name}" + (f" ({abbrev})" if abbrev else ""),
            f"**Registry:** {record.get('registry', 'N/A')} | **Type:** {record.get('type', 'N/A')}",
        ]
        if record.get("registry", "").lower() != "standard":
            lines.append(
                f"_Note: This record is a {record.get('registry', 'Unknown')}, not a Standard. "
                "Showing Databases linked to it._"
            )
            lines.append("")
        if countries:
            lines.append(
                f"**Country filter requested:** {', '.join(countries)} "
                f"(note: country data is not available from association lookups; "
                f"use search_records with countries= to verify specific databases)"
            )
        lines.append("")

        # Check incoming associations from databases
        incoming = record.get("reverseRecordAssociations", [])
        dbs_by_label: dict[str, list] = {}
        for a in incoming:
            lr = a.get("fairsharingRecord", {})
            if lr.get("registry", "").lower() == "database":
                label = a.get("recordAssocLabel", "related_to")
                dbs_by_label.setdefault(label, []).append(lr)

        # Check outgoing to databases
        outgoing = record.get("recordAssociations", [])
        dbs_out: dict[str, list] = {}
        for a in outgoing:
            lr = a.get("linkedRecord", {})
            if lr.get("registry", "").lower() == "database":
                label = a.get("recordAssocLabel", "related_to")
                dbs_out.setdefault(label, []).append(lr)

        total = sum(len(v) for v in dbs_by_label.values()) + sum(len(v) for v in dbs_out.values())

        if output_format == "json":
            all_dbs = []
            for label, items in dbs_by_label.items():
                for d in items:
                    all_dbs.append(
                        {
                            "id": d.get("id", ""),
                            "name": d.get("name", "Unknown"),
                            "abbreviation": d.get("abbreviation", ""),
                            "type": d.get("type", ""),
                            "status": d.get("status", ""),
                            "relationship": label,
                            "direction": "incoming",
                        }
                    )
            for label, items in dbs_out.items():
                for d in items:
                    all_dbs.append(
                        {
                            "id": d.get("id", ""),
                            "name": d.get("name", "Unknown"),
                            "abbreviation": d.get("abbreviation", ""),
                            "type": d.get("type", ""),
                            "status": d.get("status", ""),
                            "relationship": label,
                            "direction": "outgoing",
                        }
                    )
            return json.dumps(
                {
                    "standard_name": name,
                    "databases": all_dbs,
                },
                indent=2,
            )

        if total == 0:
            lines.append("_No databases found for this standard._")
            return "\n".join(lines)

        lines.append(f"**Total databases:** {total}")
        lines.append("")

        def _format_db_list(dbs: list) -> list[str]:
            result = []
            active = [d for d in dbs if d.get("status") == "ready"]
            deprecated = [d for d in dbs if d.get("status") == "deprecated"]
            other = [d for d in dbs if d.get("status") not in ("ready", "deprecated")]

            for d in sorted(active + other + deprecated, key=lambda x: x.get("name", "")):
                dname = d.get("name", "Unknown")
                dabbrev = d.get("abbreviation", "")
                dtype = d.get("type", "")
                did = d.get("id", "")
                dstatus = d.get("status", "")
                entry = f"  - **{dname}**"
                if dabbrev:
                    entry += f" ({dabbrev})"
                if dtype:
                    entry += f" [{dtype}]"
                entry += f" (ID: {did})"
                if dstatus and dstatus != "ready":
                    entry += f" _{dstatus}_"
                result.append(entry)
            return result

        if dbs_by_label:
            lines.append("## Databases Pointing to This Standard (incoming)")
            for label in sorted(dbs_by_label):
                items = dbs_by_label[label]
                active = [d for d in items if d.get("status") == "ready"]
                lines.append(f"### {label} ({len(items)} total, {len(active)} active)")
                lines.extend(_format_db_list(items))
            lines.append("")

        if dbs_out:
            lines.append("## Databases This Standard Points To (outgoing)")
            for label in sorted(dbs_out):
                items = dbs_out[label]
                lines.append(f"### {label} ({len(items)})")
                lines.extend(_format_db_list(items))
            lines.append("")

        # Type distribution
        all_dbs = []
        for items in dbs_by_label.values():
            all_dbs.extend(items)
        for items in dbs_out.values():
            all_dbs.extend(items)

        type_counts = Counter(d.get("type", "unknown") for d in all_dbs)
        status_counts = Counter(d.get("status", "unknown") for d in all_dbs)

        lines.append("## Summary")
        lines.append(f"**By type:** {', '.join(f'{t}={c}' for t, c in type_counts.most_common())}")
        lines.append(
            f"**By status:** {', '.join(f'{s}={c}' for s, c in status_counts.most_common())}"
        )

        # Suggested next steps to guide multi-hop exploration
        lines.append("")
        lines.append("## Suggested Next Steps")
        lines.append(
            "- To compare FAIR quality of these databases: "
            "compare_databases_quality(record_ids=[ID1, ID2, ...])"
        )
        lines.append(
            "- To check which policies recommend this standard: "
            f"analyze_standard_adoption(record_id={record_id})"
        )

        return "\n".join(lines)

    except FAIRsharingError as e:
        return f"Error finding databases: {e}"


@app.mcp.tool()
async def analyze_standard_adoption(
    record_id: int,
    subject: str | None = None,
    output_format: str = "markdown",
) -> str:
    """Analyze adoption of a standard: which databases implement it, policies recommend it, collections include it.

    Provides adoption metrics and a health assessment of the standard's ecosystem.

    Args:
        record_id: The FAIRsharing record ID (ideally a Standard)
        subject: Optional subject context (e.g., "Genomics"). Displayed in the
                 output for context — does not filter results (association lookups
                 cannot be filtered by subject).
        output_format: "markdown" (default) or "json" for structured data.

    Returns:
        Adoption analysis with databases, policies, collections, and health metrics
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

        lines = [
            f"# Adoption Analysis: {name}" + (f" ({abbrev})" if abbrev else ""),
            f"**Registry:** {registry} | **Type:** {record.get('type', 'N/A')} | **Status:** {record.get('status', 'N/A')}",
        ]
        if subject:
            lines.append(f"**Subject context:** {subject}")
        lines.append("")

        outgoing = record.get("recordAssociations", [])
        incoming = record.get("reverseRecordAssociations", [])

        # Categorize incoming relationships (who points TO this record)
        implementing_dbs = []
        recommending_policies = []
        including_collections = []
        related_standards = []
        other_incoming = []

        for a in incoming:
            lr = a.get("fairsharingRecord", {})
            label = a.get("recordAssocLabel", "")
            reg = lr.get("registry", "")

            entry = {
                "name": lr.get("name", "Unknown"),
                "abbreviation": lr.get("abbreviation", ""),
                "id": lr.get("id", ""),
                "type": lr.get("type", ""),
                "status": lr.get("status", ""),
                "label": label,
            }

            if label == "implements" or (
                reg == "Database" and label in ("implements", "related_to")
            ):
                implementing_dbs.append(entry)
            elif reg == "Policy" and label in ("recommends", "profiles"):
                recommending_policies.append(entry)
            elif reg == "Collection" and label == "collects":
                including_collections.append(entry)
            elif reg == "Standard":
                related_standards.append(entry)
            else:
                other_incoming.append(entry)

        # Categorize outgoing
        outgoing_standards = []
        outgoing_dbs = []
        other_outgoing = []
        for a in outgoing:
            lr = a.get("linkedRecord", {})
            reg = lr.get("registry", "")
            entry = {
                "name": lr.get("name", "Unknown"),
                "abbreviation": lr.get("abbreviation", ""),
                "id": lr.get("id", ""),
                "label": a.get("recordAssocLabel", ""),
            }
            if reg == "Standard":
                outgoing_standards.append(entry)
            elif reg == "Database":
                outgoing_dbs.append(entry)
            else:
                other_outgoing.append(entry)

        if output_format == "json":
            # Health rating
            health_score = 0
            if len(implementing_dbs) >= 10:
                health_score += 3
            elif len(implementing_dbs) >= 3:
                health_score += 2
            elif len(implementing_dbs) >= 1:
                health_score += 1
            if len(recommending_policies) >= 3:
                health_score += 2
            elif len(recommending_policies) >= 1:
                health_score += 1
            if record.get("status") == "ready":
                health_score += 1
            health_labels = {
                0: "Minimal",
                1: "Low",
                2: "Moderate",
                3: "Good",
                4: "Strong",
                5: "Very Strong",
                6: "Excellent",
            }
            return json.dumps(
                {
                    "record_id": record.get("id"),
                    "name": name,
                    "abbreviation": abbrev,
                    "registry": registry,
                    "implementing_databases": [
                        {"id": e["id"], "name": e["name"], "status": e.get("status", "")}
                        for e in implementing_dbs
                    ],
                    "recommending_policies": [
                        {"id": e["id"], "name": e["name"]} for e in recommending_policies
                    ],
                    "including_collections": [
                        {"id": e["id"], "name": e["name"]} for e in including_collections
                    ],
                    "related_standards": [
                        {"id": e["id"], "name": e["name"]} for e in related_standards
                    ],
                    "total_inbound": len(incoming),
                    "total_outbound": len(outgoing),
                    "health_score": health_score,
                    "health_label": health_labels.get(min(health_score, 6), "Unknown"),
                },
                indent=2,
            )

        def _format_entries(entries: list, limit: int = 20) -> list[str]:
            result = []
            for e in entries[:limit]:
                line = f"  - {e['name']}"
                if e.get("abbreviation"):
                    line += f" ({e['abbreviation']})"
                line += f" [ID: {e['id']}]"
                if e.get("status") and e["status"] != "ready":
                    line += f" _{e['status']}_"
                if e.get("label"):
                    line += f" ({e['label']})"
                result.append(line)
            if len(entries) > limit:
                result.append(f"  _(...and {len(entries) - limit} more)_")
            return result

        # Report
        lines.append("## Implementing Databases")
        if implementing_dbs:
            active = [d for d in implementing_dbs if d.get("status") == "ready"]
            deprecated = [d for d in implementing_dbs if d.get("status") == "deprecated"]
            lines.append(
                f"**{len(implementing_dbs)} databases** ({len(active)} active, {len(deprecated)} deprecated)"
            )
            lines.extend(_format_entries(implementing_dbs))
        else:
            lines.append("_No databases found implementing this standard._")
        lines.append("")

        lines.append("## Recommending Policies")
        if recommending_policies:
            lines.append(f"**{len(recommending_policies)} policies**")
            lines.extend(_format_entries(recommending_policies))
        else:
            lines.append("_No policies found recommending this standard._")
        lines.append("")

        lines.append("## Including Collections")
        if including_collections:
            lines.append(f"**{len(including_collections)} collections**")
            lines.extend(_format_entries(including_collections))
        else:
            lines.append("_No collections found including this standard._")
        lines.append("")

        if related_standards:
            lines.append("## Related Standards")
            lines.append(f"**{len(related_standards)} standards**")
            lines.extend(_format_entries(related_standards))
            lines.append("")

        if outgoing_standards:
            lines.append("## Standards This Record References")
            lines.extend(_format_entries(outgoing_standards))
            lines.append("")

        # Adoption health summary
        lines.append("## Adoption Summary")
        lines.append(f"- **Database adoption:** {len(implementing_dbs)} databases")
        lines.append(f"- **Policy endorsement:** {len(recommending_policies)} policies")
        lines.append(f"- **Collection membership:** {len(including_collections)} collections")
        lines.append(f"- **Related standards:** {len(related_standards)}")
        lines.append(f"- **Total inbound links:** {len(incoming)}")
        lines.append(f"- **Total outbound links:** {len(outgoing)}")

        # Health rating
        health_score = 0
        if len(implementing_dbs) >= 10:
            health_score += 3
        elif len(implementing_dbs) >= 3:
            health_score += 2
        elif len(implementing_dbs) >= 1:
            health_score += 1
        if len(recommending_policies) >= 3:
            health_score += 2
        elif len(recommending_policies) >= 1:
            health_score += 1
        if record.get("status") == "ready":
            health_score += 1

        health_labels = {
            0: "Minimal",
            1: "Low",
            2: "Moderate",
            3: "Good",
            4: "Strong",
            5: "Very Strong",
            6: "Excellent",
        }
        health = health_labels.get(min(health_score, 6), "Unknown")
        lines.append(f"- **Adoption health:** {health} ({health_score}/6)")

        # Suggested next steps to guide multi-hop exploration
        lines.append("")
        lines.append("## Suggested Next Steps")
        lines.append(
            "- For FAIR quality of implementing databases: rank_databases_by_quality(subject=...)"
        )
        lines.append("- To find policy gaps: find_policy_gaps(subject=..., country=...)")

        return "\n".join(lines)

    except FAIRsharingError as e:
        return f"Error analyzing adoption: {e}"


@app.mcp.tool()
async def get_standard_quality_profile(
    record_id: int,
    output_format: str = "markdown",
) -> str:
    """Generate a quality profile and score for a Standard.

    Computes a maturity score based on metadata completeness,
    maintenance status, and ecosystem usage.

    Args:
        record_id: The Standard record ID
        output_format: "markdown" (default) or "json" for structured data.

    Returns:
        Quality profile with score and component breakdown
    """
    client = app.get_client()

    try:
        # Use simple get query first to check type, then complex one for usage
        data = await client.query(GET_RECORD_WITH_ASSOCIATIONS_QUERY, {"id": record_id})
        record = data.get("fairsharingRecord")

        if not record:
            return f"No record found with ID {record_id}."

        if record.get("registry", "").lower() != "standard":
            return f"Record {record_id} is a {record.get('registry')}, not a Standard."

        result = _score_standard(record)
        score = result["score"]
        grade = result["grade"]
        components = result["components"]
        std_confidence = result["confidence"]
        std_confidence_note = result["confidence_note"]

        if output_format == "json":
            return json.dumps(
                {
                    "record_id": record.get("id"),
                    "name": record.get("name"),
                    "score": score,
                    "max_score": 10.0,
                    "grade": grade,
                    "confidence": std_confidence,
                    "confidence_note": std_confidence_note,
                    "components": components,
                },
                indent=2,
            )

        lines = [
            f"# Standard Quality Profile: {record.get('name')}",
            f"**Score:** {score:.1f}/10.0 (Grade: {grade})",
            f"**Confidence:** {std_confidence} — {std_confidence_note}",
            "",
            "_Scoring uses heuristic weights (identity 3, maintenance 2, adoption 3, "
            "policy 2) that are not empirically calibrated. Scores are best used for "
            "relative comparison, not absolute quality judgments._",
            "",
            "## Scoring Breakdown",
        ]
        lines.extend(components)
        lines.append("")

        if score < 5:
            lines.append("## Improvement Suggestions")
            if not record.get("doi"):
                lines.append("- Add a persistent identifier (DOI)")
            if not record.get("isMaintained"):
                lines.append("- Confirm maintenance status")
            # Check implementers from record associations
            incoming = record.get("reverseRecordAssociations", [])
            implementers = sum(
                1
                for a in incoming
                if a.get("recordAssocLabel") == "implements"
                and a.get("fairsharingRecord", {}).get("registry") == "Database"
            )
            if implementers == 0:
                lines.append("- Encourage database adoption")

        return "\n".join(lines)

    except FAIRsharingError as e:
        return f"Error scoring standard: {e}"


@app.mcp.tool()
async def compute_maturity_index(
    top_n: int = 10,
    bottom_n: int = 10,
    subjects: list[str] | None = None,
    weight_adoption: float = 0.6,
    weight_policy: float = 0.3,
    weight_stability: float = 0.1,
    output_format: str = "markdown",
) -> str:
    """Compute a Standards Maturity Index (SMI) across the platform and return ranked results.

    Uses platform statistics to identify the most-adopted standards, then computes
    a weighted maturity score for each. Returns both the top-N most mature and
    bottom-N least mature standards, pre-sorted by score.

    The SMI formula:
        SMI = 100 * (W_a * adoption_norm + W_p * policy_norm + W_s * stability)

    Where:
    - adoption_norm = implementing_databases / max_databases_across_all
    - policy_norm = recommending_policies / max_policies_across_all
    - stability = 1.0 if status is "ready", 0.5 if "uncertain", 0.0 otherwise

    API cost: 1 stats call + 1 search call + N association lookups (one per candidate).

    Args:
        top_n: Number of most-mature standards to return (default: 10, max: 25)
        bottom_n: Number of least-mature standards to return (default: 10, max: 25)
        subjects: Optional subject filter to scope the analysis
        weight_adoption: Weight for database adoption (default: 0.6)
        weight_policy: Weight for policy endorsement (default: 0.3)
        weight_stability: Weight for status stability (default: 0.1)

    Returns:
        Ranked tables of most-mature and least-mature standards with SMI scores
    """
    client = app.get_client()

    top_n = min(max(1, top_n), 25)
    bottom_n = min(max(1, bottom_n), 25)

    # Normalize weights to sum to 1.0
    total_weight = weight_adoption + weight_policy + weight_stability
    if total_weight <= 0:
        return "Error: Weights must sum to a positive number."
    w_a = weight_adoption / total_weight
    w_p = weight_policy / total_weight
    w_s = weight_stability / total_weight

    try:
        # Step 1: Get platform statistics to find top-adopted standards
        stats_data = await client.query(GET_LATEST_STATS_QUERY, cache=True)
        latest = stats_data.get("latestStats", {})
        rich = latest.get("data")
        if isinstance(rich, str):
            rich = json.loads(rich)

        # Collect candidate record IDs from stats (most-implemented standards)
        candidate_ids: list[dict] = []  # [{id, name, source}]

        if rich and isinstance(rich, dict):
            # Top standards recommended by policies
            top_stds_by_pol = rich.get("top_10_stds_recommended_by_pols", {})
            for name, info in top_stds_by_pol.items():
                sid = info.get("id")
                if sid:
                    candidate_ids.append({"id": int(sid), "name": name, "source": "stats"})

        # Step 2: Also search for implemented standards (broader coverage)
        search_vars: dict = {
            "registry": ["Standard"],
            "status": ["ready"],
            "isImplemented": True,
            "page": 1,
            "perPage": 50,
        }
        if subjects:
            search_vars["subjects"] = subjects

        data = await client.query(SEARCH_RECORDS_COMPACT_QUERY, search_vars)
        result = data.get("searchFairsharingRecords", {})
        search_records_list = result.get("records", [])
        total_implemented = result.get("totalCount", 0)

        seen_ids = {c["id"] for c in candidate_ids}
        for rec in search_records_list:
            rid = int(rec.get("id", 0))
            if rid and rid not in seen_ids:
                candidate_ids.append(
                    {
                        "id": rid,
                        "name": rec.get("name", "Unknown"),
                        "source": "search",
                    }
                )
                seen_ids.add(rid)

        if not candidate_ids:
            return "No implemented standards found to compute maturity index."

        # Step 3: Fetch adoption details for each candidate
        scored: list[dict] = []
        for cand in candidate_ids:
            try:
                rec_data = await client.query(
                    GET_RECORD_WITH_ASSOCIATIONS_QUERY, {"id": cand["id"]}
                )
                record = rec_data.get("fairsharingRecord")
                if not record:
                    continue

                incoming = record.get("reverseRecordAssociations", [])

                db_count = sum(
                    1
                    for a in incoming
                    if a.get("fairsharingRecord", {}).get("registry") == "Database"
                    and a.get("recordAssocLabel") in ("implements", "related_to")
                )
                policy_count = sum(
                    1
                    for a in incoming
                    if a.get("fairsharingRecord", {}).get("registry") == "Policy"
                    and a.get("recordAssocLabel") in ("recommends", "profiles")
                )

                status = record.get("status", "").lower()
                stability = 1.0 if status == "ready" else (0.5 if status == "uncertain" else 0.0)

                scored.append(
                    {
                        "id": cand["id"],
                        "name": record.get("name", cand["name"]),
                        "abbreviation": record.get("abbreviation", ""),
                        "type": record.get("type", ""),
                        "db_count": db_count,
                        "policy_count": policy_count,
                        "stability": stability,
                        "status": record.get("status", ""),
                    }
                )
            except FAIRsharingError:
                logger.warning("Failed to fetch record %s for maturity index", cand["id"])
                continue

        if not scored:
            return "Could not compute maturity index — no records returned adoption data."

        # Step 4: Compute SMI scores
        max_dbs = max((s["db_count"] for s in scored), default=1) or 1
        max_pols = max((s["policy_count"] for s in scored), default=1) or 1

        for s in scored:
            adoption_norm = s["db_count"] / max_dbs
            policy_norm = s["policy_count"] / max_pols
            s["smi"] = round(
                100.0 * (w_a * adoption_norm + w_p * policy_norm + w_s * s["stability"]), 1
            )

        # Step 5: Sort by SMI descending
        scored.sort(key=lambda x: x["smi"], reverse=True)

        # Confidence assessment for the maturity index
        coverage_pct = (len(scored) / total_implemented * 100) if total_implemented > 0 else 0
        if coverage_pct >= 50:
            smi_confidence = "high"
            smi_confidence_note = (
                f"Evaluated {len(scored)} of {total_implemented} implemented standards "
                f"({coverage_pct:.0f}% coverage)."
            )
        elif coverage_pct >= 20:
            smi_confidence = "medium"
            smi_confidence_note = (
                f"Evaluated {len(scored)} of {total_implemented} implemented standards "
                f"({coverage_pct:.0f}% coverage). Rankings are based on a sample; "
                f"normalization values (max_dbs={max_dbs}, max_pols={max_pols}) "
                f"reflect this sample, not the full registry."
            )
        else:
            smi_confidence = "low"
            smi_confidence_note = (
                f"Evaluated only {len(scored)} of {total_implemented} implemented standards "
                f"({coverage_pct:.0f}% coverage). Rankings may not be representative. "
                f"Normalization values (max_dbs={max_dbs}, max_pols={max_pols}) "
                f"reflect a small sample, not the full registry."
            )

        if output_format == "json":
            return json.dumps(
                {
                    "candidates_evaluated": len(scored),
                    "total_implemented": total_implemented,
                    "weights": {"adoption": w_a, "policy": w_p, "stability": w_s},
                    "normalization": {"max_databases": max_dbs, "max_policies": max_pols},
                    "confidence": smi_confidence,
                    "confidence_note": smi_confidence_note,
                    "top": [
                        {
                            "rank": i,
                            "id": s["id"],
                            "name": s["name"],
                            "abbreviation": s.get("abbreviation", ""),
                            "db_count": s["db_count"],
                            "policy_count": s["policy_count"],
                            "status": s["status"],
                            "smi": s["smi"],
                        }
                        for i, s in enumerate(scored[:top_n], 1)
                    ],
                    "bottom": [
                        {
                            "rank": i,
                            "id": s["id"],
                            "name": s["name"],
                            "abbreviation": s.get("abbreviation", ""),
                            "db_count": s["db_count"],
                            "policy_count": s["policy_count"],
                            "status": s["status"],
                            "smi": s["smi"],
                        }
                        for i, s in enumerate(
                            reversed(scored[-bottom_n:]) if len(scored) > top_n else [], 1
                        )
                    ],
                },
                indent=2,
            )

        # Build output
        lines = [
            "# Standards Maturity Index (SMI)",
            f"**Candidates evaluated:** {len(scored)} | **Total implemented standards:** {total_implemented:,}",
            f"**Weights:** adoption={w_a:.2f}, policy={w_p:.2f}, stability={w_s:.2f}",
            f"**Normalization:** max_databases={max_dbs}, max_policies={max_pols}",
            f"**Confidence:** {smi_confidence} — {smi_confidence_note}",
            "",
            "_SMI weights are configurable defaults, not empirically calibrated. "
            "Normalization is relative to the evaluated sample, not the full registry. "
            "Use SMI for ranking within a consistent context, not as absolute maturity measures._",
        ]
        if subjects:
            lines.append(f"**Subject filter:** {', '.join(subjects)}")
        lines.append("")

        # Top N most mature
        lines.append(f"## Top {min(top_n, len(scored))} Most Mature Standards")
        lines.append("")
        lines.append("| Rank | Name | ID | DBs | Policies | Status | SMI |")
        lines.append("|------|------|----|-----|----------|--------|-----|")

        for rank, s in enumerate(scored[:top_n], 1):
            display = s["name"]
            if s.get("abbreviation"):
                display = f"{s['abbreviation']} ({s['name'][:30]})"
            lines.append(
                f"| {rank} | {display} | {s['id']} | {s['db_count']} "
                f"| {s['policy_count']} | {s['status']} | {s['smi']} |"
            )

        lines.append("")

        # Bottom N least mature (from the evaluated set)
        if len(scored) > top_n:
            bottom_set = scored[-bottom_n:] if len(scored) >= bottom_n else scored
            bottom_set = list(reversed(bottom_set))  # Show lowest first

            lines.append(f"## Bottom {len(bottom_set)} Least Mature (of evaluated)")
            lines.append("")
            lines.append("| Rank | Name | ID | DBs | Policies | Status | SMI |")
            lines.append("|------|------|----|-----|----------|--------|-----|")

            for rank, s in enumerate(bottom_set, 1):
                display = s["name"]
                if s.get("abbreviation"):
                    display = f"{s['abbreviation']} ({s['name'][:30]})"
                lines.append(
                    f"| {rank} | {display} | {s['id']} | {s['db_count']} "
                    f"| {s['policy_count']} | {s['status']} | {s['smi']} |"
                )

            lines.append("")

        lines.append("## SMI Formula")
        lines.append(
            "SMI = 100 * (W_adoption * DBs/MaxDBs + W_policy * Policies/MaxPolicies + W_stability * StatusScore)"
        )
        lines.append("")
        lines.append("## Suggested Next Steps")
        lines.append(
            "- For detailed adoption of a specific standard: "
            "analyze_standard_adoption(record_id=...)"
        )
        lines.append("- To find truly emerging standards: find_emerging_standards()")
        lines.append("- To find endorsed but unadopted standards: find_endorsed_but_unadopted()")

        return "\n".join(lines)

    except FAIRsharingError as e:
        return f"Error computing maturity index: {e}"


@app.mcp.tool()
async def find_emerging_standards(
    min_year: int | None = None,
    subjects: list[str] | None = None,
    max_results: int = 20,
    output_format: str = "markdown",
) -> str:
    """Find genuinely emerging standards — recently created with low-but-nonzero adoption.

    Distinguishes truly emerging standards from abandoned ones by cross-referencing
    creation date with adoption data. Standards are classified into:
    - **Emerging**: Created recently (within min_year) with some adoption signals
    - **Recently created, no adoption**: New but with zero links (may be emerging or stalled)
    - **Old and unadopted**: Created long ago with no adoption (likely abandoned/niche)

    API cost: 1 search for unimplemented + 1 search for recently created + up to
    max_results association lookups.

    Args:
        min_year: Consider standards created on or after this year as "recent"
            (default: 4 years ago from current year, i.e., 2022 for 2026)
        subjects: Optional subject filter
        max_results: Maximum results per category (default: 20, max: 50)
        output_format: "markdown" (default) or "json" for structured data.

    Returns:
        Categorized list of standards with adoption signals and creation dates
    """
    client = app.get_client()

    max_results = min(max(1, max_results), 50)
    if min_year is None:
        min_year = 2022  # Default: last 4 years

    try:
        # Step 1: Find unimplemented standards (the "not adopted" pool)
        orphan_vars: dict = {
            "registry": ["Standard"],
            "status": ["ready"],
            "isImplemented": False,
            "page": 1,
            "perPage": 50,
        }
        if subjects:
            orphan_vars["subjects"] = subjects

        data = await client.query(SEARCH_RECORDS_COMPACT_QUERY, orphan_vars)
        result = data.get("searchFairsharingRecords", {})
        unimplemented = result.get("records", [])
        total_unimplemented = result.get("totalCount", 0)

        # Fetch more pages if available (up to 200 records to scan)
        total_pages = result.get("totalPages", 1)
        page = 2
        while page <= min(total_pages, 4) and len(unimplemented) < 200:
            orphan_vars["page"] = page
            data = await client.query(SEARCH_RECORDS_COMPACT_QUERY, orphan_vars)
            result = data.get("searchFairsharingRecords", {})
            batch = result.get("records", [])
            if not batch:
                break
            unimplemented.extend(batch)
            page += 1

        # Classify by creation date
        recent_unadopted: list[dict] = []
        old_unadopted: list[dict] = []

        for rec in unimplemented:
            date_str = rec.get("createdAt", "")
            try:
                year = int(date_str[:4]) if date_str else 0
            except (ValueError, IndexError):
                year = 0

            entry = {
                "id": rec.get("id", ""),
                "name": rec.get("name", "Unknown"),
                "abbreviation": rec.get("abbreviation", ""),
                "type": rec.get("type", ""),
                "created_year": year,
                "created_at": date_str[:10] if date_str else "unknown",
            }

            if year >= min_year:
                recent_unadopted.append(entry)
            else:
                old_unadopted.append(entry)

        # Step 2: Find recently created standards WITH implementation (truly emerging)
        emerging_vars: dict = {
            "registry": ["Standard"],
            "status": ["ready"],
            "isImplemented": True,
            "page": 1,
            "perPage": 50,
        }
        if subjects:
            emerging_vars["subjects"] = subjects

        # Scan for recently created implemented standards
        emerging: list[dict] = []
        page = 1
        max_scan_pages = 10
        while page <= max_scan_pages and len(emerging) < max_results:
            emerging_vars["page"] = page
            data = await client.query(SEARCH_RECORDS_COMPACT_QUERY, emerging_vars)
            result = data.get("searchFairsharingRecords", {})
            records = result.get("records", [])
            if not records:
                break
            for rec in records:
                date_str = rec.get("createdAt", "")
                try:
                    year = int(date_str[:4]) if date_str else 0
                except (ValueError, IndexError):
                    year = 0
                if year >= min_year:
                    emerging.append(
                        {
                            "id": rec.get("id", ""),
                            "name": rec.get("name", "Unknown"),
                            "abbreviation": rec.get("abbreviation", ""),
                            "type": rec.get("type", ""),
                            "created_year": year,
                            "created_at": date_str[:10] if date_str else "unknown",
                        }
                    )
            page += 1

        # Step 3: For emerging standards, fetch adoption counts
        for entry in emerging[:max_results]:
            try:
                rec_data = await client.query(
                    GET_RECORD_WITH_ASSOCIATIONS_QUERY, {"id": entry["id"]}
                )
                record = rec_data.get("fairsharingRecord", {})
                incoming = record.get("reverseRecordAssociations", [])
                entry["db_count"] = sum(
                    1
                    for a in incoming
                    if a.get("fairsharingRecord", {}).get("registry") == "Database"
                )
                entry["policy_count"] = sum(
                    1
                    for a in incoming
                    if a.get("fairsharingRecord", {}).get("registry") == "Policy"
                )
                entry["total_links"] = len(incoming) + len(record.get("recordAssociations", []))
            except FAIRsharingError:
                entry["db_count"] = 0
                entry["policy_count"] = 0
                entry["total_links"] = 0

        if output_format == "json":
            return json.dumps(
                {
                    "categories": {
                        "emerging": [
                            {
                                "id": e["id"],
                                "name": e["name"],
                                "abbreviation": e.get("abbreviation", ""),
                                "type": e.get("type", ""),
                                "created_at": e.get("created_at", ""),
                                "db_count": e.get("db_count", 0),
                                "policy_count": e.get("policy_count", 0),
                                "total_links": e.get("total_links", 0),
                            }
                            for e in emerging[:max_results]
                        ],
                        "recently_created_unadopted": [
                            {
                                "id": e["id"],
                                "name": e["name"],
                                "abbreviation": e.get("abbreviation", ""),
                                "type": e.get("type", ""),
                                "created_at": e.get("created_at", ""),
                            }
                            for e in recent_unadopted[:max_results]
                        ],
                        "old_unadopted": [
                            {
                                "id": e["id"],
                                "name": e["name"],
                                "abbreviation": e.get("abbreviation", ""),
                                "type": e.get("type", ""),
                                "created_at": e.get("created_at", ""),
                            }
                            for e in old_unadopted[:max_results]
                        ],
                    },
                },
                indent=2,
            )

        # Build output
        lines = [
            "# Emerging vs Abandoned Standards Analysis",
            f"**Cutoff year:** {min_year} (standards created on or after this are 'recent')",
            f"**Total unimplemented standards:** {total_unimplemented:,}",
        ]
        if subjects:
            lines.append(f"**Subject filter:** {', '.join(subjects)}")
        lines.append("")

        # Category 1: Truly emerging (recently created + some adoption)
        lines.append(f"## Emerging Standards ({len(emerging[:max_results])} found)")
        lines.append("_Recently created AND already adopted by at least one database._")
        lines.append("")

        if emerging:
            lines.append("| Name | ID | Type | Created | DBs | Policies | Links |")
            lines.append("|------|----|------|---------|-----|----------|-------|")
            for e in emerging[:max_results]:
                display = e["abbreviation"] or e["name"][:40]
                lines.append(
                    f"| {display} | {e['id']} | {e['type']} | {e['created_at']} "
                    f"| {e.get('db_count', '?')} | {e.get('policy_count', '?')} "
                    f"| {e.get('total_links', '?')} |"
                )
        else:
            lines.append("_No standards found that are both recent and adopted._")
        lines.append("")

        # Category 2: Recently created but no adoption
        lines.append(f"## Recently Created, No Adoption ({len(recent_unadopted)} found)")
        lines.append(
            "_Created recently but not yet implemented by any database. May be emerging or stalled._"
        )
        lines.append("")

        if recent_unadopted:
            lines.append("| Name | ID | Type | Created |")
            lines.append("|------|----|------|---------|")
            for e in recent_unadopted[:max_results]:
                display = e["abbreviation"] or e["name"][:40]
                lines.append(f"| {display} | {e['id']} | {e['type']} | {e['created_at']} |")
            if len(recent_unadopted) > max_results:
                lines.append(f"_...and {len(recent_unadopted) - max_results} more._")
        else:
            lines.append("_No recently created unadopted standards found._")
        lines.append("")

        # Category 3: Old and unadopted (likely abandoned)
        lines.append(f"## Old and Unadopted ({len(old_unadopted)} found)")
        lines.append(
            f"_Created before {min_year} with no database implementation. Likely abandoned or hyper-niche._"
        )
        lines.append("")

        if old_unadopted:
            # Sort oldest first
            old_unadopted.sort(key=lambda x: x["created_year"])
            lines.append("| Name | ID | Type | Created |")
            lines.append("|------|----|------|---------|")
            for e in old_unadopted[:max_results]:
                display = e["abbreviation"] or e["name"][:40]
                lines.append(f"| {display} | {e['id']} | {e['type']} | {e['created_at']} |")
            if len(old_unadopted) > max_results:
                lines.append(f"_...and {len(old_unadopted) - max_results} more._")
        else:
            lines.append("_No old unadopted standards found._")

        return "\n".join(lines)

    except FAIRsharingError as e:
        return f"Error finding emerging standards: {e}"


@app.mcp.tool()
async def find_endorsed_but_unadopted(
    subjects: list[str] | None = None,
    max_results: int = 20,
    output_format: str = "markdown",
) -> str:
    """Find standards recommended by policies but NOT implemented by any database.

    Identifies a specific gap: standards that have institutional endorsement
    (policy recommendations) but lack real-world adoption (database implementation).
    These represent standards where policy intention hasn't translated to practice.

    API cost: 1 multiTagFilter call + up to max_results association lookups.

    Args:
        subjects: Optional subject filter
        max_results: Maximum results (default: 20, max: 50)
        output_format: "markdown" (default) or "json" for structured data.

    Returns:
        List of endorsed-but-unadopted standards with policy details
    """
    client = app.get_client()

    max_results = min(max(1, max_results), 50)

    try:
        # Find standards that are recommended but not implemented
        variables: dict = {
            "load": True,
            "registry": ["Standard"],
            "status": ["ready"],
            "isRecommended": True,
            "isImplemented": False,
        }
        if subjects:
            variables["subjects"] = subjects

        data = await client.query(MULTI_TAG_FILTER_QUERY, variables)
        records = data.get("multiTagFilter", [])

        if not records:
            # Try without the isImplemented=False filter (it may not be supported
            # on multiTagFilter) and check manually
            variables_fallback: dict = {
                "load": True,
                "registry": ["Standard"],
                "status": ["ready"],
                "isRecommended": True,
            }
            if subjects:
                variables_fallback["subjects"] = subjects

            data = await client.query(MULTI_TAG_FILTER_QUERY, variables_fallback)
            all_recommended = data.get("multiTagFilter", [])

            if not all_recommended:
                return (
                    "No policy-recommended standards found"
                    + (f" for subjects={subjects}" if subjects else "")
                    + ". Policy endorsement data may be sparse in FAIRsharing."
                )

            # Manually check which are not implemented by fetching associations
            records = []
            for rec in all_recommended[: max_results * 2]:  # Over-fetch to account for filtering
                try:
                    rec_data = await client.query(
                        GET_RECORD_WITH_ASSOCIATIONS_QUERY, {"id": rec.get("id")}
                    )
                    record = rec_data.get("fairsharingRecord", {})
                    if not record:
                        continue
                    incoming = record.get("reverseRecordAssociations", [])
                    has_db = any(
                        a.get("fairsharingRecord", {}).get("registry") == "Database"
                        and a.get("recordAssocLabel") == "implements"
                        for a in incoming
                    )
                    if not has_db:
                        # Add policy info to the record
                        rec["_policies"] = [
                            a.get("fairsharingRecord", {}).get("name", "Unknown")
                            for a in incoming
                            if a.get("fairsharingRecord", {}).get("registry") == "Policy"
                        ]
                        rec["_policy_count"] = len(rec["_policies"])
                        records.append(rec)
                        if len(records) >= max_results:
                            break
                except FAIRsharingError:
                    continue

            if not records:
                return (
                    f"All {len(all_recommended)} policy-recommended standards "
                    "are also implemented by at least one database. "
                    "No endorsement-adoption gap found."
                )
        else:
            # Enrich with policy details
            for rec in records[:max_results]:
                try:
                    rec_data = await client.query(
                        GET_RECORD_WITH_ASSOCIATIONS_QUERY, {"id": rec.get("id")}
                    )
                    record = rec_data.get("fairsharingRecord", {})
                    if not record:
                        continue
                    incoming = record.get("reverseRecordAssociations", [])
                    rec["_policies"] = [
                        a.get("fairsharingRecord", {}).get("name", "Unknown")
                        for a in incoming
                        if a.get("fairsharingRecord", {}).get("registry") == "Policy"
                    ]
                    rec["_policy_count"] = len(rec["_policies"])
                except FAIRsharingError:
                    rec["_policies"] = []
                    rec["_policy_count"] = 0

        if output_format == "json":
            records_to_show = sorted(
                records[:max_results],
                key=lambda r: r.get("_policy_count", 0),
                reverse=True,
            )
            return json.dumps(
                {
                    "standards": [
                        {
                            "id": rec.get("id", ""),
                            "name": rec.get("name", "Unknown"),
                            "recommenders": rec.get("_policies", []),
                            "implementors": [],
                        }
                        for rec in records_to_show
                    ],
                },
                indent=2,
            )

        # Build output
        lines = [
            "# Endorsed but Unadopted Standards",
            f"**Found:** {len(records)} standards recommended by policies but NOT implemented by any database",
        ]
        if subjects:
            lines.append(f"**Subject filter:** {', '.join(subjects)}")
        lines.append("")
        lines.append(
            "_These represent gaps where policy intention hasn't translated to database adoption._"
        )
        lines.append("")

        # Sort by policy count descending (most endorsed first)
        records_to_show = sorted(
            records[:max_results],
            key=lambda r: r.get("_policy_count", 0),
            reverse=True,
        )

        for i, rec in enumerate(records_to_show, 1):
            name = rec.get("name", "Unknown")
            abbrev = rec.get("abbreviation", "")
            rec_id = rec.get("id", "")
            rec_type = rec.get("type", "")
            policies = rec.get("_policies", [])

            entry = f"### {i}. {name}"
            if abbrev:
                entry += f" ({abbrev})"
            lines.append(entry)
            lines.append(f"- **ID:** {rec_id} | **Type:** {rec_type}")
            if policies:
                lines.append(f"- **Recommending policies ({len(policies)}):**")
                for p in policies[:5]:
                    lines.append(f"  - {p}")
                if len(policies) > 5:
                    lines.append(f"  - _...and {len(policies) - 5} more_")
            else:
                lines.append("- **Recommending policies:** (details not available)")
            lines.append("")

        lines.append("## Suggested Next Steps")
        lines.append(
            "- To understand why a standard isn't adopted: "
            "get_standard_quality_profile(record_id=...)"
        )
        lines.append(
            "- To find databases that COULD implement these: "
            "find_databases_for_standard(record_id=...)"
        )

        return "\n".join(lines)

    except FAIRsharingError as e:
        return f"Error finding endorsed-but-unadopted standards: {e}"
