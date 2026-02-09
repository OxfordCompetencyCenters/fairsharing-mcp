"""FAIRsharing MCP tools — Policy analysis."""

import json
from collections import Counter

from fairsharing_mcp import app, config, helpers
from fairsharing_mcp.client import FAIRsharingError
from fairsharing_mcp.constants import (
    POLICY_COMPREHENSIVE_WEIGHTS,
    POLICY_MANDATE_FIELDS,
    POLICY_TYPES,
)
from fairsharing_mcp.formatters import format_policy_detail
from fairsharing_mcp.queries import (
    GET_RECORD_WITH_ASSOCIATIONS_QUERY,
    SEARCH_RECORDS_QUERY,
)


def _score_policy(record: dict) -> dict:
    """Compute quality score for a Policy record (pure function, no API calls).

    Expects mandate fields to have been extracted by helpers.extract_policy_mandates().
    Returns dict with: score, max, grade, components, confidence, confidence_note.
    """
    score = 0.0
    components = []

    # 1. Mandate Clarity (Max 4)
    mandate_fields = [
        "mandatedDataSharing",
        "mandatedDmpCreation",
        "sharingResearchSoftware",
        "metadataSharing",
    ]
    defined_mandates = sum(1 for f in mandate_fields if record.get(f))

    if defined_mandates > 0:
        m_score = min(4.0, defined_mandates * 1.0)
        score += m_score
        components.append(f"- {defined_mandates}/4 Core Mandates Defined (+{m_score})")

    # 2. Coverage Breadth (Max 3)
    coverage_fields = [
        "dataProtection",
        "dataAvailabilityStatement",
        "dataCitation",
        "dataPreservation",
        "supportedCosts",
        "monitoringOfCompliance",
    ]
    defined_coverage = sum(1 for f in coverage_fields if record.get(f))

    if defined_coverage > 0:
        c_score = min(3.0, defined_coverage * 0.5)
        score += c_score
        components.append(f"- {defined_coverage} Coverage Areas Defined (+{c_score})")

    # 3. Recommendations (Max 3)
    outgoing = record.get("recordAssociations", [])
    has_linked_standards = any(
        a.get("linkedRecord", {}).get("registry") == "Standard" for a in outgoing
    )
    has_linked_dbs = any(a.get("linkedRecord", {}).get("registry") == "Database" for a in outgoing)

    if has_linked_standards:
        score += 1.5
        components.append("- Recommends Standards (+1.5)")
    if has_linked_dbs:
        score += 1.5
        components.append("- Recommends Databases (+1.5)")

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
    mandate_data_available = not record.get("_mandate_data_unavailable") and not record.get(
        "_mandate_extraction_failed"
    )
    total_fields_checked = len(mandate_fields) + len(coverage_fields) + 2  # +2 for recommendations
    total_fields_present = (
        defined_mandates
        + defined_coverage
        + (1 if has_linked_standards else 0)
        + (1 if has_linked_dbs else 0)
    )

    if mandate_data_available and total_fields_present >= total_fields_checked - 2:
        confidence = "high"
        confidence_note = (
            f"Mandate data extracted successfully; "
            f"{total_fields_present}/{total_fields_checked} scoring fields present."
        )
    elif mandate_data_available:
        confidence = "medium"
        confidence_note = (
            f"{total_fields_present}/{total_fields_checked} scoring fields present. "
            f"Missing fields scored as zero."
        )
    else:
        confidence = "low"
        if record.get("_mandate_data_unavailable"):
            confidence_note = (
                "Mandate data could not be loaded (detail query failed). "
                "Score is based on associations only and likely understates quality."
            )
        elif record.get("_mandate_extraction_failed"):
            confidence_note = (
                "Mandate data could not be extracted from metadata. "
                "Score is based on associations only and likely understates quality."
            )
        else:
            confidence_note = (
                f"Only {total_fields_present}/{total_fields_checked} fields present. "
                "Score may not reflect actual policy quality."
            )

    return {
        "score": score,
        "max": 10.0,
        "grade": grade,
        "components": components,
        "confidence": confidence,
        "confidence_note": confidence_note,
    }


def _score_policy_comprehensive(record: dict) -> dict:
    """Compute comprehensive quality score for a Policy with domain-specific indicators.

    Extends _score_policy() with geographic coverage, compliance infrastructure,
    and temporal health metrics. Pure function, no API calls.
    """
    from datetime import datetime, timezone

    basic = _score_policy(record)
    indicators: dict[str, dict] = {}
    total_score = 0.0
    max_score = sum(POLICY_COMPREHENSIVE_WEIGHTS.values())

    # ── Mandate Specificity (from basic scorer — maps to basic mandate clarity) ──
    mandate_fields = [
        "mandatedDataSharing",
        "mandatedDmpCreation",
        "sharingResearchSoftware",
        "metadataSharing",
    ]
    defined_mandates = sum(1 for f in mandate_fields if record.get(f))
    mandate_score = min(POLICY_COMPREHENSIVE_WEIGHTS["mandate_specificity"], defined_mandates * 1.0)
    indicators["mandate_specificity"] = {
        "score": mandate_score,
        "max": POLICY_COMPREHENSIVE_WEIGHTS["mandate_specificity"],
        "details": [f"{defined_mandates}/4 core mandate fields defined"],
    }
    total_score += mandate_score

    # ── Recommendation Coverage (from basic scorer) ──
    rec_max = POLICY_COMPREHENSIVE_WEIGHTS["recommendation_coverage"]
    rec_score = 0.0
    rec_details = []
    outgoing = record.get("recordAssociations", [])
    has_standards = any(a.get("linkedRecord", {}).get("registry") == "Standard" for a in outgoing)
    has_dbs = any(a.get("linkedRecord", {}).get("registry") == "Database" for a in outgoing)
    if has_standards:
        rec_score += rec_max / 2
        rec_details.append("Recommends standards")
    if has_dbs:
        rec_score += rec_max / 2
        rec_details.append("Recommends databases")
    if not rec_details:
        rec_details.append("No standard/database recommendations")
    indicators["recommendation_coverage"] = {
        "score": rec_score,
        "max": rec_max,
        "details": rec_details,
    }
    total_score += rec_score

    # ── Geographic Coverage ──
    geo_max = POLICY_COMPREHENSIVE_WEIGHTS["geographic_coverage"]
    geo_score = 0.0
    geo_details = []
    countries = record.get("countries", [])
    country_count = len(countries) if isinstance(countries, list) else 0
    if country_count >= 5:
        geo_score = geo_max
        geo_details.append(f"Covers {country_count} countries (+{geo_max})")
    elif country_count >= 2:
        geo_score = geo_max * 0.67
        geo_details.append(f"Covers {country_count} countries (+{geo_score:.1f})")
    elif country_count == 1:
        geo_score = geo_max * 0.33
        name = (
            countries[0].get("name", "1 country") if isinstance(countries[0], dict) else "1 country"
        )
        geo_details.append(f"Covers {name} (+{geo_score:.1f})")
    else:
        geo_details.append("No country coverage data (+0.0)")
    indicators["geographic_coverage"] = {
        "score": round(geo_score, 1),
        "max": geo_max,
        "details": geo_details,
    }
    total_score += geo_score

    # ── Compliance Infrastructure ──
    ci_max = POLICY_COMPREHENSIVE_WEIGHTS["compliance_infrastructure"]
    ci_score = 0.0
    ci_details = []
    ci_fields = {
        "monitoringOfCompliance": "Compliance monitoring",
        "guidanceToHelpEnableCompliance": "Compliance guidance",
        "timingOfDmp": "DMP timing specified",
        "updatingOfDmp": "DMP updating specified",
    }
    for field, label in ci_fields.items():
        if record.get(field):
            ci_score += ci_max / len(ci_fields)
            ci_details.append(f"{label}: yes")
    if not ci_details:
        ci_details.append("No compliance infrastructure fields defined")
    indicators["compliance_infrastructure"] = {
        "score": round(ci_score, 1),
        "max": ci_max,
        "details": ci_details,
    }
    total_score += ci_score

    # ── Temporal Health ──
    temporal_max = POLICY_COMPREHENSIVE_WEIGHTS["temporal_health"]
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
async def get_policy_details(
    record_id: int,
    output_format: str = "markdown",
) -> str:
    """Get detailed policy information including all mandate attributes.

    Fetches a policy record with all 17 mandate/scope fields such as
    mandated data sharing level, DMP requirements, data protection coverage,
    compliance monitoring, and recommended standards/databases.

    Args:
        record_id: The FAIRsharing policy record ID
        output_format: "markdown" (default) or "json" for structured data.

    Returns:
        Full policy detail with mandate levels, coverage, compliance info,
        and recommended standards/databases
    """
    try:
        record = await helpers.fetch_policy_with_fallback(record_id)

        if not record:
            return f"No record found with ID {record_id}."

        if record.get("registry", "").lower() != "policy":
            return f"Record {record_id} ({record.get('name', 'Unknown')}) is a {record.get('registry', 'Unknown')}, not a Policy."

        if output_format == "json":
            # Return the policy data dict directly
            safe_record = {}
            for key, value in record.items():
                if not key.startswith("_"):
                    safe_record[key] = value
            return json.dumps(safe_record, indent=2, default=str)

        return format_policy_detail(record)

    except FAIRsharingError as e:
        return f"Error fetching policy details: {e}"


@app.mcp.tool()
async def compare_policies_by_country(
    countries: list[str],
    policy_type: str | None = None,
    subject: str | None = None,
    max_per_country: int = 10,
    output_format: str = "markdown",
) -> str:
    """Compare policies across countries with mandate-level aggregation.

    Fetches policies for each country, retrieves their mandate attributes,
    and produces a comparison matrix showing which countries require vs suggest
    various data sharing and management mandates.

    Args:
        countries: List of country names to compare (e.g., ["Ireland", "United Kingdom"])
        policy_type: Filter by policy type: "journal", "funder", "institution", "project", "society"
        subject: Filter by scientific subject (e.g., "Genomics")
        max_per_country: Maximum policies to analyze per country (default: 10, max: 25)
        output_format: "markdown" (default) or "json" for structured data.

    Returns:
        Comparison matrix of mandate levels across countries
    """
    client = app.get_client()
    max_per_country = min(max(1, max_per_country), 25)

    if len(countries) < 2:
        return "Please provide at least 2 countries to compare."
    if len(countries) > 10:
        return "Please provide at most 10 countries to compare."
    if policy_type and policy_type not in POLICY_TYPES:
        return (
            f"Invalid policy_type '{policy_type}'. Valid types: {', '.join(sorted(POLICY_TYPES))}."
        )

    try:
        country_data: dict[str, list[dict]] = {}

        for country in countries:
            # Search for policies in this country
            variables: dict = {
                "registry": ["Policy"],
                "countries": [country],
                "status": ["ready"],
                "page": 1,
                "perPage": max_per_country,
            }
            if policy_type:
                variables["recordType"] = [policy_type]
            if subject:
                variables["subjects"] = [subject]

            data = await client.query(SEARCH_RECORDS_QUERY, variables)
            result = data.get("searchFairsharingRecords", {})
            records = result.get("records", [])

            # Fetch detailed policy data for each record
            policy_details = []
            for rec in records[:max_per_country]:
                rec_id = rec.get("id")
                if rec_id:
                    detail = await helpers.fetch_policy_with_fallback(int(rec_id))
                    if detail:
                        policy_details.append(detail)

            country_data[country] = policy_details

        if output_format == "json":
            return json.dumps(
                {
                    "countries": countries,
                    "policies": {
                        country: [
                            {
                                "id": p.get("id", ""),
                                "name": p.get("name", "Unknown"),
                                "type": p.get("type", ""),
                                "mandatedDataSharing": p.get("mandatedDataSharing"),
                                "mandatedDmpCreation": p.get("mandatedDmpCreation"),
                                "sharingResearchSoftware": p.get("sharingResearchSoftware"),
                                "metadataSharing": p.get("metadataSharing"),
                                "dataProtection": p.get("dataProtection"),
                                "dataAvailabilityStatement": p.get("dataAvailabilityStatement"),
                                "dataCitation": p.get("dataCitation"),
                                "dataPreservation": p.get("dataPreservation"),
                                "licencesForOutputs": p.get("licencesForOutputs"),
                            }
                            for p in policies
                        ]
                        for country, policies in country_data.items()
                    },
                },
                indent=2,
            )

        # Build comparison output
        lines = [
            f"# Policy Comparison: {' vs '.join(countries)}",
            "",
        ]

        if policy_type:
            lines.append(f"**Policy type filter:** {policy_type}")
        if subject:
            lines.append(f"**Subject filter:** {subject}")
        lines.append("")

        # Summary table
        lines.append("## Summary")
        lines.append("| Country | # Policies |")
        lines.append("|---------|-----------|")
        empty_countries = []
        for country in countries:
            count = len(country_data[country])
            lines.append(f"| {country} | {count} |")
            if count == 0:
                empty_countries.append(country)
        lines.append("")

        # Explicit warnings for countries with no results
        if empty_countries:
            type_hint = f" '{policy_type}'" if policy_type else ""
            for ec in empty_countries:
                lines.append(
                    f"**Warning: {ec} has no{type_hint} policies in FAIRsharing.** "
                    f"Try without the policy_type filter, or check available types with "
                    f"search_records(registry='Policy', countries=['{ec}'])."
                )
            lines.append("")

        # Check if mandate fields are available
        has_mandate_data = any(
            any(p.get(f) is not None for f in POLICY_MANDATE_FIELDS)
            for policies in country_data.values()
            for p in policies
        )

        if has_mandate_data:
            # Mandate comparison table
            mandate_display = {
                "mandatedDataSharing": "Data Sharing",
                "sharingResearchSoftware": "Software Sharing",
                "mandatedDmpCreation": "DMP Creation",
                "metadataSharing": "Metadata Sharing",
            }

            lines.append("## Mandate Comparison")
            header = "| Area |"
            separator = "|------|"
            for country in countries:
                header += f" {country} (% required) |"
                separator += "------|"
            lines.append(header)
            lines.append(separator)

            differences = []
            for field, label in mandate_display.items():
                row = f"| {label} |"
                country_rates = {}
                for country in countries:
                    policies = country_data[country]
                    if not policies:
                        row += " N/A |"
                        continue
                    required_count = sum(
                        1 for p in policies if str(p.get(field, "")).lower() == "required"
                    )
                    total = len(policies)
                    pct = (required_count / total * 100) if total > 0 else 0
                    country_rates[country] = pct
                    row += f" {pct:.0f}% ({required_count}/{total}) |"
                lines.append(row)

                # Track notable differences
                if country_rates:
                    rates = list(country_rates.values())
                    if max(rates) - min(rates) > 20:
                        high = max(country_rates, key=country_rates.get)
                        low = min(country_rates, key=country_rates.get)
                        differences.append(
                            f"- {high} has higher {label.lower()} mandate rate "
                            f"({country_rates[high]:.0f}% vs {country_rates[low]:.0f}% in {low})"
                        )
            lines.append("")

            # Coverage comparison
            coverage_display = {
                "dataProtection": "Data Protection",
                "dataAvailabilityStatement": "Data Availability Statement",
                "dataCitation": "Data Citation",
                "dataPreservation": "Data Preservation",
                "licencesForOutputs": "Licences for Outputs",
            }

            lines.append("## Coverage Comparison")
            header = "| Area |"
            separator = "|------|"
            for country in countries:
                header += f" {country} (% yes) |"
                separator += "------|"
            lines.append(header)
            lines.append(separator)

            for field, label in coverage_display.items():
                row = f"| {label} |"
                for country in countries:
                    policies = country_data[country]
                    if not policies:
                        row += " N/A |"
                        continue
                    yes_count = sum(1 for p in policies if str(p.get(field, "")).lower() == "yes")
                    total = len(policies)
                    pct = (yes_count / total * 100) if total > 0 else 0
                    row += f" {pct:.0f}% ({yes_count}/{total}) |"
                lines.append(row)
            lines.append("")

            if differences:
                lines.append("## Notable Differences")
                lines.extend(differences)
                lines.append("")
        else:
            lines.append("_Mandate fields not available from the API for these records._")
            lines.append("")

        # List policies per country
        for country in countries:
            policies = country_data[country]
            if policies:
                lines.append(f"## {country} Policies ({len(policies)})")
                for p in policies:
                    pname = p.get("name", "Unknown")
                    ptype = p.get("type", "")
                    pid = p.get("id", "")
                    lines.append(f"- {pname} [{ptype}] (ID: {pid})")
                lines.append("")

        return "\n".join(lines)

    except FAIRsharingError as e:
        return f"Error comparing policies: {e}"


@app.mcp.tool()
async def analyze_policy_mandates(
    countries: list[str] | None = None,
    subject: str | None = None,
    policy_type: str | None = None,
    max_policies: int = 25,
    output_format: str = "markdown",
) -> str:
    """Aggregate distribution of mandate levels across a filtered policy set.

    Analyzes how many policies require, suggest, or don't cover various
    data sharing and management areas. Identifies gaps where few policies
    have mandates.

    Args:
        countries: Filter by country names (optional, e.g., ["Ireland"])
        subject: Filter by scientific subject (optional)
        policy_type: Filter by policy type: "journal", "funder", "institution", etc.
        max_policies: Maximum policies to analyze (default: 25, max: 50)
        output_format: "markdown" (default) or "json" for structured data.

    Returns:
        Mandate level distribution with gap analysis
    """
    client = app.get_client()
    max_policies = min(max(1, max_policies), 50)

    if policy_type and policy_type not in POLICY_TYPES:
        return (
            f"Invalid policy_type '{policy_type}'. Valid types: {', '.join(sorted(POLICY_TYPES))}."
        )

    try:
        # Search for policies
        variables: dict = {
            "registry": ["Policy"],
            "status": ["ready"],
            "page": 1,
            "perPage": max_policies,
        }
        if countries:
            variables["countries"] = countries
        if subject:
            variables["subjects"] = [subject]
        if policy_type:
            variables["recordType"] = [policy_type]

        data = await client.query(SEARCH_RECORDS_QUERY, variables)
        result = data.get("searchFairsharingRecords", {})
        records = result.get("records", [])
        total_available = result.get("totalCount", 0)

        if not records:
            filter_parts = []
            if countries:
                filter_parts.append(f"countries={countries}")
            if subject:
                filter_parts.append(f"subject={subject}")
            if policy_type:
                filter_parts.append(f"policy_type={policy_type}")

            msg = "No policies found"
            if filter_parts:
                msg += f" for filters: {', '.join(filter_parts)}"
            msg += "."
            msg += (
                " Try broadening your search by removing one filter at a time"
                " (e.g., drop the subject or country filter)."
            )
            return msg

        # Fetch detailed data for each policy
        policy_details = []
        for rec in records[:max_policies]:
            rec_id = rec.get("id")
            if rec_id:
                detail = await helpers.fetch_policy_with_fallback(int(rec_id))
                if detail:
                    policy_details.append(detail)

        if not policy_details:
            return "Could not fetch detailed policy data."

        n = len(policy_details)

        if output_format == "json":
            mandate_fields = {
                "mandatedDataSharing": "Data Sharing",
                "sharingResearchSoftware": "Software Sharing",
                "mandatedDmpCreation": "DMP Creation",
                "metadataSharing": "Metadata Sharing",
            }
            mandates: dict[str, dict] = {}
            for field, label in mandate_fields.items():
                counts = Counter(str(p.get(field, "unknown")).lower() for p in policy_details)
                mandates[field] = {
                    "label": label,
                    "required": counts.get("required", 0),
                    "suggested": counts.get("suggested", 0),
                    "not_covered": counts.get("not covered", 0),
                    "other_unknown": n
                    - counts.get("required", 0)
                    - counts.get("suggested", 0)
                    - counts.get("not covered", 0),
                }
            return json.dumps(
                {
                    "record_id": None,
                    "policies_analyzed": n,
                    "total_available": total_available,
                    "mandates": mandates,
                },
                indent=2,
            )

        lines = [
            "# Policy Mandate Analysis",
            "",
        ]

        filter_parts = []
        if countries:
            filter_parts.append(f"countries={countries}")
        if subject:
            filter_parts.append(f"subject={subject}")
        if policy_type:
            filter_parts.append(f"type={policy_type}")
        if filter_parts:
            lines.append(f"**Filters:** {', '.join(filter_parts)}")
        lines.append(f"**Policies analyzed:** {n} of {total_available} available")
        lines.append("")

        # Check if mandate fields are available
        has_mandate_data = any(
            p.get(f) is not None for f in POLICY_MANDATE_FIELDS for p in policy_details
        )

        if has_mandate_data:
            # Mandate level distribution
            mandate_display = {
                "mandatedDataSharing": "Data Sharing",
                "sharingResearchSoftware": "Software Sharing",
                "mandatedDmpCreation": "DMP Creation",
                "metadataSharing": "Metadata Sharing",
            }

            lines.append("## Mandate Level Distribution")
            lines.append("| Area | Required | Suggested | Not Covered | Other/Unknown |")
            lines.append("|------|----------|-----------|-------------|---------------|")

            gaps = []
            for field, label in mandate_display.items():
                counts = Counter(str(p.get(field, "unknown")).lower() for p in policy_details)
                required = counts.get("required", 0)
                suggested = counts.get("suggested", 0)
                not_covered = counts.get("not covered", 0)
                other = n - required - suggested - not_covered

                req_pct = (required / n * 100) if n > 0 else 0
                sug_pct = (suggested / n * 100) if n > 0 else 0
                nc_pct = (not_covered / n * 100) if n > 0 else 0

                lines.append(
                    f"| {label} | {required} ({req_pct:.0f}%) | "
                    f"{suggested} ({sug_pct:.0f}%) | "
                    f"{not_covered} ({nc_pct:.0f}%) | "
                    f"{other} |"
                )

                if req_pct < 30:
                    gaps.append(f"- Only {req_pct:.0f}% require {label.lower()}")

            lines.append("")

            # Coverage distribution
            coverage_display = {
                "dataProtection": "Data Protection",
                "dataAvailabilityStatement": "Data Availability Statement",
                "licencesForOutputs": "Licences for Outputs",
                "dataCitation": "Data Citation",
                "dataPreservation": "Data Preservation",
                "guidanceToHelpEnableCompliance": "Compliance Guidance",
                "monitoringOfCompliance": "Compliance Monitoring",
                "supportedCosts": "Supported Costs",
            }

            lines.append("## Coverage Distribution")
            lines.append("| Area | Yes | No | Unknown |")
            lines.append("|------|-----|------|---------|")

            for field, label in coverage_display.items():
                counts = Counter(str(p.get(field, "unknown")).lower() for p in policy_details)
                yes_count = counts.get("yes", 0)
                no_count = counts.get("no", 0)
                unknown = n - yes_count - no_count

                yes_pct = (yes_count / n * 100) if n > 0 else 0

                lines.append(f"| {label} | {yes_count} ({yes_pct:.0f}%) | {no_count} | {unknown} |")

                if yes_pct < 40:
                    gaps.append(f"- Only {yes_pct:.0f}% cover {label.lower()}")

            lines.append("")

            if gaps:
                lines.append("## Gaps Identified")
                lines.extend(gaps)
                lines.append("")
        else:
            lines.append("_Mandate fields not available from the API for these records._")
            lines.append("")

        # Policy type breakdown
        type_counts = Counter(p.get("type", "unknown") for p in policy_details)
        lines.append("## Policy Types")
        for ptype, count in type_counts.most_common():
            lines.append(f"- **{ptype}:** {count}")
        lines.append("")

        # Country breakdown
        country_counter: Counter = Counter()
        for p in policy_details:
            for c in p.get("countries", []):
                cname = c.get("name", "")
                if cname:
                    country_counter[cname] += 1
        if country_counter:
            lines.append("## Countries Represented")
            for cname, count in country_counter.most_common(20):
                lines.append(f"- **{cname}:** {count}")
            lines.append("")

        return "\n".join(lines)

    except FAIRsharingError as e:
        return f"Error analyzing policy mandates: {e}"


@app.mcp.tool()
async def trace_policy_impact(
    record_id: int,
    subject: str | None = None,
    output_format: str = "markdown",
) -> str:
    """Trace a policy's impact: Policy -> recommended Standards -> implementing Databases.

    Performs a two-hop traversal to show the full reach of a policy through
    the standards it recommends and the databases that implement those standards.

    Args:
        record_id: The FAIRsharing policy record ID
        subject: Optional subject filter (e.g., "Genomics") to restrict the
                 traced standards to those tagged with the given subject
        output_format: "markdown" (default) or "json" for structured data.

    Returns:
        Impact chain showing policy -> standards -> databases with summary stats
    """
    client = app.get_client()

    try:
        # Fetch the policy with associations
        data = await client.query(GET_RECORD_WITH_ASSOCIATIONS_QUERY, {"id": record_id})
        policy = data.get("fairsharingRecord")

        if not policy:
            return f"No record found with ID {record_id}."

        policy_name = policy.get("name", "Unknown")
        policy_registry = policy.get("registry", "")

        if policy_registry.lower() != "policy":
            return f"Record {record_id} ({policy_name}) is a {policy_registry}, not a Policy."

        lines = [
            f"# Policy Impact Trace: {policy_name}",
            f"**ID:** {record_id} | **Type:** {policy.get('type', 'N/A')} | **Status:** {policy.get('status', 'N/A')}",
        ]
        if subject:
            lines.append(f"**Subject filter:** {subject}")
        lines.append("")

        # Extract recommended standards and databases from outgoing associations
        outgoing = policy.get("recordAssociations", [])
        recommended_standards = []
        recommended_databases = []

        for a in outgoing:
            lr = a.get("linkedRecord", {})
            label = a.get("recordAssocLabel", "")
            if lr.get("registry") == "Standard":
                recommended_standards.append(
                    {
                        "id": lr.get("id"),
                        "name": lr.get("name", "Unknown"),
                        "abbreviation": lr.get("abbreviation", ""),
                        "type": lr.get("type", ""),
                        "status": lr.get("status", ""),
                        "label": label,
                    }
                )
            elif lr.get("registry") == "Database":
                recommended_databases.append(
                    {
                        "id": lr.get("id"),
                        "name": lr.get("name", "Unknown"),
                        "abbreviation": lr.get("abbreviation", ""),
                        "label": label,
                    }
                )

        if output_format == "json":
            # Perform hop-2 traversal for JSON output
            impact_chains = []
            all_implementing_dbs: set[str] = set()
            for std in recommended_standards[:20]:
                std_id = std["id"]
                implementing = []
                try:
                    std_data = await client.query(
                        GET_RECORD_WITH_ASSOCIATIONS_QUERY, {"id": std_id}
                    )
                    std_record = std_data.get("fairsharingRecord", {})
                    for a in std_record.get("reverseRecordAssociations", []):
                        lr = a.get("fairsharingRecord", {})
                        if lr.get("registry") == "Database" and a.get("recordAssocLabel", "") in (
                            "implements",
                            "related_to",
                        ):
                            implementing.append(
                                {"id": lr.get("id", ""), "name": lr.get("name", "Unknown")}
                            )
                            all_implementing_dbs.add(lr.get("name", "Unknown"))
                except FAIRsharingError:
                    pass
                impact_chains.append(
                    {
                        "standard_id": std["id"],
                        "standard_name": std["name"],
                        "implementing_databases": implementing,
                    }
                )
            return json.dumps(
                {
                    "policy_id": record_id,
                    "impact": {
                        "recommended_standards": [
                            {"id": s["id"], "name": s["name"], "type": s.get("type", "")}
                            for s in recommended_standards
                        ],
                        "recommended_databases": [
                            {"id": d["id"], "name": d["name"]} for d in recommended_databases
                        ],
                        "standard_to_database_chains": impact_chains,
                        "unique_implementing_databases": len(all_implementing_dbs),
                    },
                },
                indent=2,
            )

        lines.append("## Direct Recommendations")
        lines.append(f"- **Standards recommended:** {len(recommended_standards)}")
        lines.append(f"- **Databases recommended:** {len(recommended_databases)}")

        # Break down recommended standards by type
        if recommended_standards:
            type_counts: dict[str, int] = {}
            for std in recommended_standards:
                stype = std.get("type") or "unknown"
                type_counts[stype] = type_counts.get(stype, 0) + 1
            lines.append("")
            lines.append("**Standards by type:**")
            for stype in sorted(type_counts):
                lines.append(f"  - {stype}: {type_counts[stype]}")
        lines.append("")

        if recommended_databases:
            lines.append("### Directly Recommended Databases")
            for db in recommended_databases:
                entry = f"- {db['name']}"
                if db.get("abbreviation"):
                    entry += f" ({db['abbreviation']})"
                entry += f" [ID: {db['id']}]"
                lines.append(entry)
            lines.append("")

        # Hop 2: For each standard, find implementing databases
        if recommended_standards:
            # Apply subject filter if specified
            if subject:
                pre_filter_count = len(recommended_standards)
                filtered_standards = []
                for std in recommended_standards:
                    std_id = std["id"]
                    try:
                        std_data = await client.query(
                            GET_RECORD_WITH_ASSOCIATIONS_QUERY, {"id": std_id}
                        )
                        std_record = std_data.get("fairsharingRecord", {})
                        std_subjects = [
                            s.get("label", "").lower()
                            for s in std_record.get("subjects", [])
                            if s.get("label")
                        ]
                        if subject.lower() in std_subjects:
                            # Cache the fetched record to avoid re-fetching in hop 2
                            std["_cached_record"] = std_record
                            filtered_standards.append(std)
                    except FAIRsharingError:
                        # Keep on error to avoid false negatives
                        filtered_standards.append(std)
                lines.append(
                    f"_Subject filter applied: {len(filtered_standards)} of "
                    f"{pre_filter_count} standards match '{subject}'._"
                )
                lines.append("")
                recommended_standards = filtered_standards

            if config.get_truncation_warning() and len(recommended_standards) > 20:
                lines.append(
                    f"_Showing impact for the first 20 of {len(recommended_standards)} "
                    "recommended standards._"
                )
                lines.append("")

            lines.append("## Standards -> Implementing Databases (2-hop)")
            lines.append("")

            all_implementing_dbs = set()
            standard_db_map: dict[str, list] = {}

            for std in recommended_standards[:20]:  # Limit to 20 standards
                std_id = std["id"]
                std_name = std["name"]
                std_abbrev = std.get("abbreviation", "")
                std_type = std.get("type", "")
                display_name = f"{std_name}" + (f" ({std_abbrev})" if std_abbrev else "")
                if std_type:
                    display_name = f"[{std_type}] {display_name}"

                try:
                    # Use cached record from subject filter if available
                    cached = std.get("_cached_record")
                    if cached:
                        std_record = cached
                    else:
                        std_data = await client.query(
                            GET_RECORD_WITH_ASSOCIATIONS_QUERY, {"id": std_id}
                        )
                        std_record = std_data.get("fairsharingRecord", {})
                    std_incoming = std_record.get("reverseRecordAssociations", [])

                    dbs = []
                    for a in std_incoming:
                        lr = a.get("fairsharingRecord", {})
                        label = a.get("recordAssocLabel", "")
                        if lr.get("registry") == "Database" and label in (
                            "implements",
                            "related_to",
                        ):
                            db_name = lr.get("name", "Unknown")
                            db_id = lr.get("id", "")
                            dbs.append({"name": db_name, "id": db_id})
                            all_implementing_dbs.add(db_name)

                    standard_db_map[display_name] = dbs

                except FAIRsharingError:
                    standard_db_map[display_name] = []

            # Display the chain
            for std_name, dbs in standard_db_map.items():
                lines.append(f"### {std_name}")
                if dbs:
                    lines.append(f"**Implementing databases ({len(dbs)}):**")
                    for db in dbs[:15]:
                        lines.append(f"  - {db['name']} [ID: {db['id']}]")
                    if len(dbs) > 15:
                        lines.append(f"  _(...and {len(dbs) - 15} more)_")
                else:
                    lines.append("_No implementing databases found._")
                lines.append("")

            # Summary
            lines.append("## Impact Summary")
            lines.append(
                f"- **Standards recommended by this policy:** {len(recommended_standards)}"
            )
            lines.append(f"- **Databases directly recommended:** {len(recommended_databases)}")
            lines.append(
                f"- **Unique databases implementing recommended standards:** {len(all_implementing_dbs)}"
            )
            total_reach = len(all_implementing_dbs) + len(recommended_databases)
            lines.append(f"- **Total unique database reach:** {total_reach}")

            if standard_db_map:
                most_impactful = max(standard_db_map.items(), key=lambda x: len(x[1]))
                if most_impactful[1]:
                    lines.append(
                        f"- **Most impactful standard:** {most_impactful[0]} ({len(most_impactful[1])} databases)"
                    )
        else:
            lines.append("_This policy does not recommend any standards directly._")
            lines.append("")

        # Suggested next steps to guide multi-hop exploration
        lines.append("")
        lines.append("## Suggested Next Steps")
        lines.append(
            "- To check FAIR quality of implementing databases: "
            "get_database_quality_profile(record_id=DB_ID)"
        )
        lines.append(
            "- To find policies in other countries with similar mandates: "
            "compare_policies_by_country(countries=[...], subject=...)"
        )
        lines.append(
            "- To check adoption depth of a standard: analyze_standard_adoption(record_id=STD_ID)"
        )

        return "\n".join(lines)

    except FAIRsharingError as e:
        return f"Error tracing policy impact: {e}"


@app.mcp.tool()
async def find_policy_gaps(
    subject: str,
    country: str | None = None,
    output_format: str = "markdown",
) -> str:
    """Find standards/databases in a subject NOT covered by any policy.

    Compares the set of standards/databases recommended by policies against
    all standards/databases in a subject area to identify uncovered resources.

    Args:
        subject: Scientific subject to analyze (e.g., "Genomics")
        country: Optional country filter for policies
        output_format: "markdown" (default) or "json" for structured data.

    Returns:
        Coverage analysis showing covered and uncovered standards/databases
    """
    client = app.get_client()

    try:
        lines = [
            f"# Policy Gap Analysis: {subject}",
        ]
        if country:
            lines.append(f"**Country filter:** {country}")
        lines.append("")

        # Step 1: Fetch all policies for the subject (+ optional country)
        policy_vars: dict = {
            "registry": ["Policy"],
            "subjects": [subject],
            "status": ["ready"],
            "page": 1,
            "perPage": 50,
        }
        if country:
            policy_vars["countries"] = [country]

        policy_data = await client.query(SEARCH_RECORDS_QUERY, policy_vars)
        policy_result = policy_data.get("searchFairsharingRecords", {})
        policy_records = policy_result.get("records", [])
        policy_total = policy_result.get("totalCount", 0)

        lines.append(f"## Policies Found: {policy_total}")
        if config.get_truncation_warning() and policy_total > 30:
            lines.append("")
            lines.append(
                f"_Analysis uses the first 30 policies only (of {policy_total} total). "
                "Coverage and gaps are based on this subset._"
            )
            lines.append("")

        if policy_total == 0:
            warning_parts = [f"subject='{subject}'"]
            if country:
                warning_parts.append(f"country='{country}'")
            lines.append("")
            lines.append(
                f"**Warning:** No policies found for {', '.join(warning_parts)}. "
                f"Try broadening filters (e.g., remove the country filter). "
                f"The coverage analysis below reflects 0% policy coverage."
            )
            lines.append("")

        # Collect all recommended record IDs from policies
        covered_ids: set[str] = set()
        covered_names: dict[str, str] = {}  # id -> name

        for prec in policy_records[:30]:
            pid = prec.get("id")
            if pid:
                try:
                    pdata = await client.query(GET_RECORD_WITH_ASSOCIATIONS_QUERY, {"id": pid})
                    precord = pdata.get("fairsharingRecord", {})
                    for a in precord.get("recordAssociations", []):
                        lr = a.get("linkedRecord", {})
                        lr_id = str(lr.get("id", ""))
                        lr_registry = lr.get("registry", "")
                        if lr_id and lr_registry in ("Standard", "Database"):
                            covered_ids.add(lr_id)
                            covered_names[lr_id] = lr.get("name", "Unknown")
                except FAIRsharingError:
                    continue

        lines.append(f"**Resources covered by policies:** {len(covered_ids)}")
        lines.append("")

        # Step 2: Fetch all standards and databases for the subject
        uncovered_standards = []
        uncovered_databases = []
        covered_standards = []
        covered_databases = []

        for reg in ["Standard", "Database"]:
            reg_vars: dict = {
                "subjects": [subject],
                "registry": [reg],
                "status": ["ready"],
                "page": 1,
                "perPage": 50,
            }
            reg_data = await client.query(SEARCH_RECORDS_QUERY, reg_vars)
            reg_result = reg_data.get("searchFairsharingRecords", {})
            reg_records = reg_result.get("records", [])

            for r in reg_records:
                rid = str(r.get("id", ""))
                rname = r.get("name", "Unknown")
                rabbrev = r.get("abbreviation", "")
                entry = {
                    "id": rid,
                    "name": rname,
                    "abbreviation": rabbrev,
                    "type": r.get("type", ""),
                }

                if rid in covered_ids:
                    if reg == "Standard":
                        covered_standards.append(entry)
                    else:
                        covered_databases.append(entry)
                else:
                    if reg == "Standard":
                        uncovered_standards.append(entry)
                    else:
                        uncovered_databases.append(entry)

        total_resources = (
            len(uncovered_standards)
            + len(uncovered_databases)
            + len(covered_standards)
            + len(covered_databases)
        )
        total_covered = len(covered_standards) + len(covered_databases)
        coverage_pct = (total_covered / total_resources * 100) if total_resources > 0 else 0

        if output_format == "json":
            return json.dumps(
                {
                    "subject": subject,
                    "gaps": {
                        "total_resources": total_resources,
                        "total_covered": total_covered,
                        "coverage_pct": round(coverage_pct, 1),
                        "uncovered_standards": uncovered_standards,
                        "uncovered_databases": uncovered_databases,
                        "covered_standards": covered_standards,
                        "covered_databases": covered_databases,
                        "policy_count": policy_total,
                    },
                },
                indent=2,
            )

        lines.append("## Coverage Overview")
        lines.append(f"- **Total resources in subject:** {total_resources}")
        lines.append(f"- **Covered by policies:** {total_covered} ({coverage_pct:.0f}%)")
        lines.append(
            f"- **Not covered:** {total_resources - total_covered} ({100 - coverage_pct:.0f}%)"
        )
        lines.append("")

        # Uncovered resources
        if uncovered_standards:
            lines.append(f"## Uncovered Standards ({len(uncovered_standards)})")
            for s in uncovered_standards[:30]:
                entry_line = f"- {s['name']}"
                if s.get("abbreviation"):
                    entry_line += f" ({s['abbreviation']})"
                entry_line += f" [{s['type']}] (ID: {s['id']})"
                lines.append(entry_line)
            if len(uncovered_standards) > 30:
                lines.append(f"_(...and {len(uncovered_standards) - 30} more)_")
            lines.append("")

        if uncovered_databases:
            lines.append(f"## Uncovered Databases ({len(uncovered_databases)})")
            for d in uncovered_databases[:30]:
                entry_line = f"- {d['name']}"
                if d.get("abbreviation"):
                    entry_line += f" ({d['abbreviation']})"
                entry_line += f" [{d['type']}] (ID: {d['id']})"
                lines.append(entry_line)
            if len(uncovered_databases) > 30:
                lines.append(f"_(...and {len(uncovered_databases) - 30} more)_")
            lines.append("")

        # Covered resources
        if covered_standards:
            lines.append(f"## Well-Covered Standards ({len(covered_standards)})")
            for s in covered_standards[:20]:
                entry_line = f"- {s['name']}"
                if s.get("abbreviation"):
                    entry_line += f" ({s['abbreviation']})"
                entry_line += f" (ID: {s['id']})"
                lines.append(entry_line)
            if len(covered_standards) > 20:
                lines.append(f"_(...and {len(covered_standards) - 20} more)_")
            lines.append("")

        if covered_databases:
            lines.append(f"## Well-Covered Databases ({len(covered_databases)})")
            for d in covered_databases[:20]:
                entry_line = f"- {d['name']}"
                if d.get("abbreviation"):
                    entry_line += f" ({d['abbreviation']})"
                entry_line += f" (ID: {d['id']})"
                lines.append(entry_line)
            if len(covered_databases) > 20:
                lines.append(f"_(...and {len(covered_databases) - 20} more)_")
            lines.append("")

        if config.get_truncation_warning():
            lines.append("")
            lines.append(
                "_Policy list and uncovered standards/databases display are capped at 30; "
                "more may exist._"
            )

        return "\n".join(lines)

    except FAIRsharingError as e:
        return f"Error analyzing policy gaps: {e}"


@app.mcp.tool()
async def get_policy_quality_profile(
    record_id: int,
    output_format: str = "markdown",
) -> str:
    """Generate a quality profile and score for a Policy.

    Computes a completeness score based on mandate fields,
    coverage areas, and linked recommendations.

    Args:
        record_id: The Policy record ID
        output_format: "markdown" (default) or "json" for structured data.

    Returns:
        Quality profile with score and component breakdown
    """
    try:
        # Must use helpers.fetch_policy_with_fallback to get metadata fields
        record = await helpers.fetch_policy_with_fallback(record_id)

        if not record:
            return f"No record found with ID {record_id}."

        if record.get("registry", "").lower() != "policy":
            return f"Record {record_id} is a {record.get('registry')}, not a Policy."

        result = _score_policy(record)
        score = result["score"]
        grade = result["grade"]
        components = result["components"]
        pol_confidence = result["confidence"]
        pol_confidence_note = result["confidence_note"]

        if output_format == "json":
            return json.dumps(
                {
                    "record_id": record.get("id"),
                    "name": record.get("name"),
                    "score": score,
                    "max_score": 10.0,
                    "grade": grade,
                    "confidence": pol_confidence,
                    "confidence_note": pol_confidence_note,
                    "components": components,
                },
                indent=2,
            )

        lines = [
            f"# Policy Quality Profile: {record.get('name')}",
            f"**Score:** {score:.1f}/10.0 (Grade: {grade})",
            f"**Confidence:** {pol_confidence} — {pol_confidence_note}",
            "",
            "_Scoring uses heuristic weights (mandates 4, coverage 4, recommendations 2) "
            "that are not empirically calibrated. Scores are best used for "
            "relative comparison, not absolute quality judgments._",
            "",
            "## Scoring Breakdown",
        ]
        lines.extend(components)
        lines.append("")

        # Check mandate coverage for improvement suggestions
        mandate_fields = [
            "mandatedDataSharing",
            "mandatedDmpCreation",
            "sharingResearchSoftware",
            "metadataSharing",
        ]
        defined_mandates = sum(1 for f in mandate_fields if record.get(f))
        if defined_mandates < 2:
            lines.append("## Improvement Suggestions")
            lines.append("- Define data sharing and DMP mandates")

        return "\n".join(lines)

    except FAIRsharingError as e:
        return f"Error scoring policy: {e}"


@app.mcp.tool()
async def detect_policy_conflicts(
    policy_ids: list[int],
    output_format: str = "markdown",
) -> str:
    """Detect conflicts and friction between 2-5 specific policies.

    Fetches each policy's mandate data and performs pairwise comparison across
    three dimensions: mandate levels (data sharing, DMP, software, metadata),
    coverage areas (data protection, licences, preservation, citation, availability),
    and DMP timing. Produces a conflict matrix showing where policies disagree,
    with severity ratings and resolution hints.

    Use case: A researcher subject to multiple overlapping policies (funder +
    institution + journal) needs to know where they conflict so they can adopt
    the strictest requirement.

    Args:
        policy_ids: List of 2-5 policy record IDs to compare

    Returns:
        Conflict matrix with per-field comparison and actionable resolution hints
    """
    if len(policy_ids) < 2:
        return "Please provide at least 2 policy IDs to compare."
    if len(policy_ids) > 5:
        return "Please provide at most 5 policy IDs to compare."

    try:
        # Fetch all policies
        policies = []
        for pid in policy_ids:
            record = await helpers.fetch_policy_with_fallback(pid)
            if not record:
                return f"No record found with ID {pid}."
            if record.get("registry", "").lower() != "policy":
                return (
                    f"Record {pid} ({record.get('name', 'Unknown')}) is a "
                    f"{record.get('registry', 'Unknown')}, not a Policy."
                )
            policies.append(record)

        # Build short names
        names = []
        for p in policies:
            name = p.get("abbreviation") or p.get("name", "Unknown")
            if len(name) > 20:
                name = name[:17] + "..."
            names.append(name)

        # Define comparison dimensions
        dimensions = {
            "Mandate Level Comparison": {
                "Data Sharing": "mandatedDataSharing",
                "DMP Creation": "mandatedDmpCreation",
                "Software Sharing": "sharingResearchSoftware",
                "Metadata Sharing": "metadataSharing",
            },
            "Coverage Comparison": {
                "Data Protection": "dataProtection",
                "Licences for Outputs": "licencesForOutputs",
                "Data Preservation": "dataPreservation",
                "Data Citation": "dataCitation",
                "Data Availability Statement": "dataAvailabilityStatement",
            },
            "DMP Timing Comparison": {
                "DMP Timing": "timingOfDmp",
                "DMP Updating": "updatingOfDmp",
            },
        }

        all_conflicts = []

        if output_format == "json":
            # Pre-compute all comparisons for JSON output
            all_comparisons: dict[str, dict] = {}
            for dim_title, fields in dimensions.items():
                dim_data: dict[str, dict] = {}
                for label, field_key in fields.items():
                    values = [str(p.get(field_key) or "N/A") for p in policies]
                    non_na = [v.lower() for v in values if v.lower() != "n/a"]
                    unique_values = set(non_na)
                    severity = None
                    if len(unique_values) > 1:
                        has_required = "required" in unique_values or "yes" in unique_values
                        has_not_covered = "not covered" in unique_values or "no" in unique_values
                        if has_required and has_not_covered:
                            severity = "HIGH"
                        elif has_required:
                            severity = "MEDIUM"
                        else:
                            severity = "LOW"
                    dim_data[label] = {"values": values, "conflict_severity": severity}
                    if severity:
                        all_conflicts.append((label, severity, values, names))
                all_comparisons[dim_title] = dim_data

            return json.dumps(
                {
                    "policies": [
                        {"id": policy_ids[i], "name": names[i]} for i in range(len(policies))
                    ],
                    "dimensions": {
                        dim: {
                            label: {
                                "values": dict(zip(names, info["values"])),
                                "conflict_severity": info["conflict_severity"],
                            }
                            for label, info in dim_fields.items()
                        }
                        for dim, dim_fields in all_comparisons.items()
                    },
                    "conflicts": [
                        {
                            "field": label,
                            "severity": sev,
                            "values": dict(zip(pol_names, vals)),
                        }
                        for label, sev, vals, pol_names in all_conflicts
                    ],
                    "total_fields": sum(len(f) for f in dimensions.values()),
                    "total_conflicts": len(all_conflicts),
                },
                indent=2,
            )

        lines = ["# Policy Conflict Analysis", ""]
        policy_labels = [f"{names[i]} (ID: {policy_ids[i]})" for i in range(len(policies))]
        lines.append(f"**Policies compared:** {', '.join(policy_labels)}")
        lines.append("")

        for dim_title, fields in dimensions.items():
            lines.append(f"## {dim_title}")
            header = "| Area |"
            separator = "|------|"
            for name in names:
                header += f" {name} |"
                separator += "------|"
            header += " Conflict? |"
            separator += "------|"
            lines.append(header)
            lines.append(separator)

            for label, field_key in fields.items():
                values = []
                row = f"| {label} |"
                for p in policies:
                    val = str(p.get(field_key) or "N/A")
                    values.append(val)
                    row += f" {val} |"

                # Determine conflict
                non_na = [v.lower() for v in values if v.lower() != "n/a"]
                unique_values = set(non_na)

                if len(unique_values) <= 1:
                    row += " No |"
                else:
                    has_required = "required" in unique_values or "yes" in unique_values
                    has_not_covered = "not covered" in unique_values or "no" in unique_values
                    if has_required and has_not_covered:
                        severity = "HIGH"
                    elif has_required:
                        severity = "MEDIUM"
                    else:
                        severity = "LOW"
                    row += f" **YES ({severity})** |"
                    all_conflicts.append((label, severity, values, names))

                lines.append(row)
            lines.append("")

        # Conflicts summary with resolution hints
        if all_conflicts:
            lines.append(f"## Conflicts Found ({len(all_conflicts)})")
            for i, (label, severity, values, pol_names) in enumerate(all_conflicts, 1):
                value_parts = [f"{pol_names[j]}: {values[j]}" for j in range(len(values))]
                lines.append(f"{i}. **{label}** ({severity}): {', '.join(value_parts)}")
                if severity == "HIGH":
                    lines.append(
                        "   *Resolution:* Adopt the strictest requirement "
                        "to comply with all policies."
                    )
                elif severity == "MEDIUM":
                    lines.append(
                        "   *Resolution:* Consider adopting the higher requirement "
                        "for broader compliance."
                    )
                else:
                    lines.append(
                        "   *Resolution:* Note the difference; adopt the option "
                        "that best fits your compliance needs."
                    )
            lines.append("")

        # Summary stats
        total_fields = sum(len(f) for f in dimensions.values())
        high_count = sum(1 for _, s, _, _ in all_conflicts if s == "HIGH")
        med_count = sum(1 for _, s, _, _ in all_conflicts if s == "MEDIUM")
        low_count = sum(1 for _, s, _, _ in all_conflicts if s == "LOW")

        lines.append("## Summary")
        lines.append(f"- **Total fields compared:** {total_fields}")
        lines.append(f"- **Conflicts detected:** {len(all_conflicts)}")
        if all_conflicts:
            lines.append(f"- **High severity:** {high_count}")
            lines.append(f"- **Medium severity:** {med_count}")
            lines.append(f"- **Low severity:** {low_count}")
        else:
            lines.append("_No conflicts detected -- all policies are aligned._")

        return "\n".join(lines)

    except FAIRsharingError as e:
        return f"Error detecting policy conflicts: {e}"
