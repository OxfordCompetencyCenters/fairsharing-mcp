"""FAIRsharing MCP Server - Formatting helpers for record display."""

import json as _json

from fairsharing_mcp import config
from fairsharing_mcp.constants import (
    DATABASE_FAIR_INDICATOR_FIELDS,
    POLICY_BOOLEAN_FIELDS,
    POLICY_DMP_FIELDS,
    POLICY_MANDATE_FIELDS,
    RECORD_STATUS_DESCRIPTIONS,
    RECORD_TYPE_DESCRIPTIONS,
    UNIFIED_GRADE_THRESHOLDS,
)


def build_fairsharing_url(doi: str | None) -> str | None:
    """Convert a FAIRsharing DOI to its canonical web URL.

    e.g. '10.25504/FAIRsharing.1943d4' → 'https://fairsharing.org/FAIRsharing.1943d4'
    Returns None if doi is missing or not a FAIRsharing DOI.
    """
    if not doi or "FAIRsharing." not in doi:
        return None
    suffix = doi.split("FAIRsharing.", 1)[1]
    return f"https://fairsharing.org/FAIRsharing.{suffix}"


def _format_status(status: str | None) -> str:
    """Format a status value with its human-readable description."""
    if not status:
        return "N/A"
    desc = RECORD_STATUS_DESCRIPTIONS.get(status)
    if desc:
        return f"{status} _({desc})_"
    return status


def _format_type(rec_type: str | None) -> str:
    """Format a record type value with its human-readable description."""
    if not rec_type:
        return ""
    desc = RECORD_TYPE_DESCRIPTIONS.get(rec_type.lower())
    if desc:
        return f"{rec_type} _({desc})_"
    return rec_type


def escape_md_table(value: str) -> str:
    """Escape a string for safe inclusion in a markdown table cell.

    Replaces pipe characters and newlines that would break table formatting.
    """
    return value.replace("|", "\\|").replace("\n", " ").replace("\r", "")


# Remedy pointers for truncated lists. A notice that names a problem without naming
# an exit is what led a client to report association data as unavailable when it was
# fully retrievable — always pair a cap with the tool that lifts it.
LIST_ASSOCIATIONS_REMEDY = "Call `fairsharing_list_associations` for the complete, paginated list."
PAGINATE_REMEDY = "Increase `per_page` or request the next `page` for more."
SEARCH_RECORDS_REMEDY = (
    "Call `fairsharing_search_records` with the same filter to page through all of them."
)


def truncation_notice(
    shown: int,
    total: int,
    item: str = "",
    remedy: str = "",
    indent: str = "",
) -> str:
    """Build a uniform 'Showing X of Y' notice for a truncated list.

    Always states the true total, so a reader can tell how much is hidden rather than
    only that something is. Pass `remedy` whenever a tool or parameter can retrieve
    the rest — see LIST_ASSOCIATIONS_REMEDY and PAGINATE_REMEDY.

    Args:
        shown: Number of entries actually rendered.
        total: True total available.
        item: Optional noun for the entries, e.g. "standards".
        remedy: Optional sentence telling the caller how to get the rest.
        indent: Optional leading whitespace to match surrounding list depth.

    Returns:
        A single markdown-italic line, e.g. "_Showing 15 of 41 metrics. Call ..._"
    """
    suffix = f" {item}" if item else ""
    note = f"{indent}_Showing {shown} of {total}{suffix}."
    if remedy:
        note += f" {remedy}"
    return note + "_"


def extract_fair_indicators(record: dict) -> dict:
    """Extract FAIR indicator values from a record's metadata JSON blob.

    The FAIRsharing API no longer exposes FAIR indicator fields as direct GraphQL
    fields on FairsharingRecord. They are now only available inside the ``metadata``
    JSON blob with snake_case keys. This helper extracts them and returns a dict
    with camelCase keys matching the old direct field names for backwards compatibility.

    For ``data_access_condition`` the value is a nested dict like ``{'type': 'open'}``
    — this function extracts the ``type`` value automatically.
    """
    meta = record.get("metadata") or {}
    if isinstance(meta, str):
        try:
            meta = _json.loads(meta)
        except Exception:
            meta = {}

    def _get(key: str, nested_key: str | None = None):
        val = meta.get(key)
        if nested_key and isinstance(val, dict):
            return val.get(nested_key)
        if isinstance(val, dict):
            return val.get("type") or val.get("value") or str(val)
        return val

    return {
        "dataAccessCondition": _get("data_access_condition", "type"),
        "dataCuration": _get("data_curation"),
        "dataDepositionCondition": _get("data_deposition_condition"),
        "citationToRelatedPublications": _get("citation_to_related_publications"),
        "dataContactInformation": _get("data_contact_information"),
        "dataVersioning": _get("data_versioning"),
        "dataPreservationPolicy": _get("data_preservation_policy"),
        "resourceSustainability": _get("resource_sustainability"),
        "usesPersistentIdentifier": _get("uses_persistent_identifier"),
    }


def format_record_summary(record: dict) -> str:
    """Format a single record as a summary string."""
    lines = []
    name = record.get("name", "Unknown")
    abbrev = record.get("abbreviation")
    if abbrev:
        lines.append(f"### {name} ({abbrev})")
    else:
        lines.append(f"### {name}")

    registry = record.get("registry", "")
    rec_type = record.get("type", "")
    if registry or rec_type:
        type_display = _format_type(rec_type) if rec_type else ""
        lines.append(
            f"- **Type:** {registry} / {type_display}"
            if type_display
            else f"- **Type:** {registry}"
        )

    status = record.get("status")
    if status:
        lines.append(f"- **Status:** {_format_status(status)}")

    description = record.get("description", "")
    if description:
        desc_limit = config.get_display_limit("description_chars")
        if desc_limit and len(description) > desc_limit:
            description = description[: desc_limit - 3] + "... (truncated)"
        lines.append(f"- **Description:** {description}")

    subjects = record.get("subjects", [])
    if subjects:
        subject_labels = [s.get("label", "") for s in subjects if s.get("label")]
        if subject_labels:
            subj_limit = config.get_display_limit("subjects") or len(subject_labels)
            shown = subject_labels[:subj_limit] if subj_limit else subject_labels
            lines.append(f"- **Subjects:** {', '.join(shown)}")
            if subj_limit and len(subject_labels) > subj_limit:
                lines.append(
                    truncation_notice(subj_limit, len(subject_labels), "subjects", indent="  ")
                )

    domains = record.get("domains", [])
    if domains:
        domain_labels = [d.get("label", "") for d in domains if d.get("label")]
        if domain_labels:
            dom_limit = config.get_display_limit("domains") or len(domain_labels)
            shown = domain_labels[:dom_limit] if dom_limit else domain_labels
            lines.append(f"- **Domains:** {', '.join(shown)}")
            if dom_limit and len(domain_labels) > dom_limit:
                lines.append(
                    truncation_notice(dom_limit, len(domain_labels), "domains", indent="  ")
                )

    doi = record.get("doi")
    url = build_fairsharing_url(doi)
    if url and doi:
        suffix = doi.split("FAIRsharing.", 1)[1]
        lines.append(f"- **FAIRsharing:** [FAIRsharing.{suffix}]({url})")
    elif doi:
        lines.append(f"- **DOI:** {doi}")

    rec_id = record.get("id")
    if rec_id:
        lines.append(f"- **ID:** {rec_id}")

    # FAIR indicator summary for database records (when fields are present)
    fair_fields = [
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
    n_fair = sum(1 for f in fair_fields if record.get(f) is not None)
    if n_fair > 0:
        lines.append(
            f"- **FAIR:** {n_fair}/9 indicators present "
            f"_(full breakdown: `get_database_quality_profile({rec_id})`)_"
        )

    return "\n".join(lines)


def format_record_detail(record: dict) -> str:
    """Format a detailed record view."""
    lines = []

    name = record.get("name", "Unknown")
    abbrev = record.get("abbreviation")
    if abbrev:
        lines.append(f"# {name} ({abbrev})")
    else:
        lines.append(f"# {name}")

    lines.append("")

    # Basic info
    lines.append("## Basic Information")
    lines.append(f"- **ID:** {record.get('id', 'N/A')}")
    lines.append(f"- **Registry:** {record.get('registry', 'N/A')}")
    lines.append(f"- **Type:** {_format_type(record.get('type')) or 'N/A'}")
    lines.append(f"- **Status:** {_format_status(record.get('status'))}")

    doi = record.get("doi")
    url = build_fairsharing_url(doi)
    if url and doi:
        suffix = doi.split("FAIRsharing.", 1)[1]
        lines.append(f"- **FAIRsharing:** [FAIRsharing.{suffix}]({url})")
        lines.append(f"- **DOI:** {doi}")
    elif doi:
        lines.append(f"- **DOI:** {doi}")

    homepage = record.get("homepage")
    if homepage:
        lines.append(f"- **Homepage:** {homepage}")

    created_at = record.get("createdAt")
    updated_at = record.get("updatedAt")
    if created_at:
        lines.append(f"- **Created:** {created_at}")
    if updated_at:
        lines.append(f"- **Updated:** {updated_at}")

    # Flags
    flags = []
    if record.get("isApproved"):
        flags.append("Approved")
    if record.get("isMaintained"):
        flags.append("Maintained")
    if record.get("isRecommended"):
        flags.append("Recommended")
    if flags:
        lines.append(f"- **Flags:** {', '.join(flags)}")

    lines.append("")

    # Description
    description = record.get("description")
    if description:
        lines.append("## Description")
        lines.append(description)
        lines.append("")

    # Subjects
    subjects = record.get("subjects", [])
    if subjects:
        lines.append("## Subjects")
        for s in subjects:
            label = s.get("label", "Unknown")
            iri = s.get("iri", "")
            lines.append(f"- {label}" + (f" ({iri})" if iri else ""))
        lines.append("")

    # Domains
    domains = record.get("domains", [])
    if domains:
        lines.append("## Domains")
        for d in domains:
            label = d.get("label", "Unknown")
            iri = d.get("iri", "")
            lines.append(f"- {label}" + (f" ({iri})" if iri else ""))
        lines.append("")

    # Object Types
    obj_types = [
        ot["label"]
        for ot in record.get("objectTypes", [])
        if ot.get("label") and ot["label"] != "object type not found"
    ]
    if obj_types:
        lines.append(f"**Object Types:** {', '.join(obj_types)}")
        lines.append("")

    # Taxonomies
    taxonomies = record.get("taxonomies", [])
    if taxonomies:
        lines.append("## Taxonomies (Species)")
        tax_limit = config.get_display_limit("taxonomies")
        to_show = taxonomies[:tax_limit] if tax_limit else taxonomies
        for t in to_show:
            label = t.get("label", "Unknown")
            lines.append(f"- {label}")
        if tax_limit and len(taxonomies) > tax_limit:
            lines.append(truncation_notice(tax_limit, len(taxonomies), "taxonomies"))
        lines.append("")

    # Countries
    countries = record.get("countries", [])
    if countries:
        lines.append("## Countries")
        country_names = [c.get("name", "") for c in countries if c.get("name")]
        lines.append(", ".join(country_names))
        lines.append("")

    # Organisations
    organisations = record.get("organisations", [])
    if organisations:
        lines.append("## Organisations")
        org_limit = config.get_display_limit("organisations")
        to_show = organisations[:org_limit] if org_limit else organisations
        for org in to_show:
            name = org.get("name", "Unknown")
            homepage = org.get("homepage", "")
            lines.append(f"- {name}" + (f" ({homepage})" if homepage else ""))
        if org_limit and len(organisations) > org_limit:
            lines.append(truncation_notice(org_limit, len(organisations), "organisations"))
        lines.append("")

    # Publications
    publications = record.get("publications", [])
    if publications:
        lines.append("## Publications")
        pub_limit = config.get_display_limit("publications")
        to_show = publications[:pub_limit] if pub_limit else publications
        for pub in to_show:
            title = pub.get("title", "Unknown")
            year = pub.get("year", "")
            doi = pub.get("doi", "")
            journal = pub.get("journal", "")
            pub_line = f"- {title}"
            if year:
                pub_line += f" ({year})"
            if journal:
                pub_line += f" - {journal}"
            if doi:
                pub_line += f" [DOI: {doi}]"
            lines.append(pub_line)
        if pub_limit and len(publications) > pub_limit:
            lines.append(truncation_notice(pub_limit, len(publications), "publications"))
        lines.append("")

    # Licences
    licence_links = record.get("licenceLinks", [])
    if licence_links:
        lines.append("## Licences")
        for ll in licence_links:
            licence = ll.get("licence", {})
            name = licence.get("name", "Unknown")
            url = licence.get("url", "")
            relation = ll.get("relation", "")
            lines.append(
                f"- {name}" + (f" ({relation})" if relation else "") + (f" - {url}" if url else "")
            )
        lines.append("")

    # Tags
    tags = record.get("userDefinedTags", [])
    if tags:
        lines.append("## Tags")
        tag_labels = [t.get("label", "") for t in tags if t.get("label")]
        lines.append(", ".join(tag_labels))
        lines.append("")

    # Relationships.
    # assoc_limit is resolved up front, NOT inside the outgoing branch: a record with
    # zero outgoing but some incoming associations used to raise UnboundLocalError here.
    assoc_limit = config.get_display_limit("associations")
    associations = record.get("recordAssociations", [])
    reverse_associations = record.get("reverseRecordAssociations", [])

    def _render_associations(assocs: list, key: str, heading: str) -> None:
        lines.append(heading)
        to_show = assocs[:assoc_limit] if assoc_limit else assocs
        for assoc in to_show:
            linked = assoc.get(key, {})
            label = assoc.get("recordAssocLabel", "related to")
            name = linked.get("name", "Unknown")
            registry = linked.get("registry", "")
            rec_id = linked.get("id", "")
            lines.append(f"- **{label}** {name} ({registry}, ID: {rec_id})")
        if assoc_limit and len(assocs) > assoc_limit:
            lines.append(
                truncation_notice(assoc_limit, len(assocs), remedy=LIST_ASSOCIATIONS_REMEDY)
            )
        lines.append("")

    if associations:
        _render_associations(associations, "linkedRecord", "## Related Records (Outgoing)")

    if reverse_associations:
        _render_associations(
            reverse_associations, "fairsharingRecord", "## Related Records (Incoming)"
        )

    return "\n".join(lines)


def format_hierarchy_item(item: dict, include_description: bool = False) -> str:
    """Format a hierarchical item (subject/domain)."""
    lines = []
    lines.append(f"# {item.get('label', 'Unknown')}")
    lines.append("")
    lines.append(f"- **ID:** {item.get('id', 'N/A')}")

    iri = item.get("iri")
    if iri:
        lines.append(f"- **IRI:** {iri}")

    description = item.get("description")
    if description and include_description:
        lines.append(f"- **Description:** {description}")

    lines.append("")

    parents = item.get("parents", [])
    if parents:
        lines.append("## Parents")
        for p in parents:
            lines.append(f"- {p.get('label', 'Unknown')} (ID: {p.get('id', 'N/A')})")
        lines.append("")

    children = item.get("children", [])
    if children:
        lines.append("## Children")
        child_limit = config.get_display_limit("children")
        to_show = children[:child_limit] if child_limit else children
        for c in to_show:
            lines.append(f"- {c.get('label', 'Unknown')} (ID: {c.get('id', 'N/A')})")
        if child_limit and len(children) > child_limit:
            lines.append(truncation_notice(child_limit, len(children), "children"))
        lines.append("")

    ancestors = item.get("ancestors", [])
    if ancestors:
        lines.append("## Ancestors (Full Path)")
        for a in ancestors:
            lines.append(f"- {a.get('label', 'Unknown')} (ID: {a.get('id', 'N/A')})")
        lines.append("")

    return "\n".join(lines)


def format_policy_detail(record: dict) -> str:
    """Format a policy record with mandate attributes."""
    lines = []

    name = record.get("name", "Unknown")
    abbrev = record.get("abbreviation")
    if abbrev:
        lines.append(f"# {name} ({abbrev})")
    else:
        lines.append(f"# {name}")

    lines.append("")
    if record.get("_mandate_data_unavailable"):
        lines.append(
            "_Note: Mandate data could not be loaded (detail query failed; basic record only)._"
        )
        lines.append("")
    elif record.get("_mandate_extraction_failed"):
        lines.append("_Note: Mandate data could not be extracted from this record's metadata._")
        lines.append("")
    lines.append("## Basic Information")
    lines.append(f"- **ID:** {record.get('id', 'N/A')}")
    lines.append(f"- **Registry:** {record.get('registry', 'N/A')}")
    lines.append(f"- **Type:** {_format_type(record.get('type')) or 'N/A'}")
    lines.append(f"- **Status:** {_format_status(record.get('status'))}")

    doi = record.get("doi")
    url = build_fairsharing_url(doi)
    if url and doi:
        suffix = doi.split("FAIRsharing.", 1)[1]
        lines.append(f"- **FAIRsharing:** [FAIRsharing.{suffix}]({url})")
        lines.append(f"- **DOI:** {doi}")
    elif doi:
        lines.append(f"- **DOI:** {doi}")
    homepage = record.get("homepage")
    if homepage:
        lines.append(f"- **Homepage:** {homepage}")

    created_at = record.get("createdAt")
    updated_at = record.get("updatedAt")
    if created_at:
        lines.append(f"- **Created:** {created_at}")
    if updated_at:
        lines.append(f"- **Updated:** {updated_at}")

    flags = []
    if record.get("isApproved"):
        flags.append("Approved")
    if record.get("isMaintained"):
        flags.append("Maintained")
    if record.get("isRecommended"):
        flags.append("Recommended")
    if flags:
        lines.append(f"- **Flags:** {', '.join(flags)}")
    lines.append("")

    # Description
    description = record.get("description")
    if description:
        lines.append("## Description")
        lines.append(description)
        lines.append("")

    # Countries
    countries = record.get("countries", [])
    if countries:
        lines.append("## Countries")
        country_names = [c.get("name", "") for c in countries if c.get("name")]
        lines.append(", ".join(country_names))
        lines.append("")

    # Organisations
    organisations = record.get("organisations", [])
    if organisations:
        lines.append("## Organisations")
        for org in organisations[:10]:
            oname = org.get("name", "Unknown")
            ohomepage = org.get("homepage", "")
            lines.append(f"- {oname}" + (f" ({ohomepage})" if ohomepage else ""))
        lines.append("")

    # Subjects & Domains
    subjects = record.get("subjects", [])
    if subjects:
        lines.append("## Subjects")
        for s in subjects:
            lines.append(f"- {s.get('label', 'Unknown')}")
        lines.append("")

    domains = record.get("domains", [])
    if domains:
        lines.append("## Domains")
        for d in domains:
            lines.append(f"- {d.get('label', 'Unknown')}")
        lines.append("")

    # === Policy Mandate Fields ===
    mandate_fields_present = any(
        record.get(f) is not None
        for f in POLICY_MANDATE_FIELDS + POLICY_BOOLEAN_FIELDS + POLICY_DMP_FIELDS
    )

    if mandate_fields_present:
        lines.append("## Policy Mandate Levels")
        lines.append("| Area | Level |")
        lines.append("|------|-------|")
        mandate_display = {
            "mandatedDataSharing": "Data Sharing",
            "sharingResearchSoftware": "Software Sharing",
            "mandatedDmpCreation": "DMP Creation",
            "metadataSharing": "Metadata Sharing",
        }
        for field, label in mandate_display.items():
            val = record.get(field)
            if val is not None:
                lines.append(f"| {label} | {val} |")
        lines.append("")

        # DMP details
        timing = record.get("timingOfDmp")
        updating = record.get("updatingOfDmp")
        if timing or updating:
            lines.append("## DMP Details")
            if timing:
                lines.append(f"- **Timing of DMP:** {timing}")
            if updating:
                lines.append(f"- **Updating of DMP:** {updating}")
            lines.append("")

        # Coverage table
        lines.append("## Policy Coverage")
        lines.append("| Area | Covered |")
        lines.append("|------|---------|")
        coverage_display = {
            "exceptionsToDataSharing": "Exceptions to Data Sharing",
            "dataProtection": "Data Protection",
            "dataAvailabilityStatement": "Data Availability Statement",
            "licencesForOutputs": "Licences for Outputs",
            "dataCitation": "Data Citation",
            "dataPreservation": "Data Preservation",
        }
        for field, label in coverage_display.items():
            val = record.get(field)
            if val is not None:
                lines.append(f"| {label} | {val} |")
        lines.append("")

        # Compliance section
        compliance_display = {
            "supportedCosts": "Supported Costs",
            "guidanceToHelpEnableCompliance": "Compliance Guidance",
            "monitoringOfCompliance": "Compliance Monitoring",
        }
        has_compliance = any(record.get(f) is not None for f in compliance_display)
        if has_compliance:
            lines.append("## Compliance")
            lines.append("| Area | Value |")
            lines.append("|------|-------|")
            for field, label in compliance_display.items():
                val = record.get(field)
                if val is not None:
                    lines.append(f"| {label} | {val} |")
            lines.append("")
    else:
        lines.append("## Policy Mandate Fields")
        lines.append("_Mandate fields not available for this record via the API._")
        lines.append("")

    # Recommended standards/databases from associations
    associations = record.get("recordAssociations", [])
    if associations:
        recommended_standards = []
        recommended_databases = []
        other_assocs = []
        for a in associations:
            lr = a.get("linkedRecord", {})
            label = a.get("recordAssocLabel", "")
            entry = {
                "name": lr.get("name", "Unknown"),
                "abbreviation": lr.get("abbreviation", ""),
                "id": lr.get("id", ""),
                "registry": lr.get("registry", ""),
                "type": lr.get("type", ""),
            }
            if lr.get("registry") == "Standard":
                recommended_standards.append(entry)
            elif lr.get("registry") == "Database":
                recommended_databases.append(entry)
            else:
                other_assocs.append(entry)

        rec_limit = config.get_display_limit("recommended")
        if recommended_standards:
            lines.append(f"## Recommended Standards ({len(recommended_standards)})")
            to_show = recommended_standards[:rec_limit] if rec_limit else recommended_standards
            for s in to_show:
                entry_line = f"- {s['name']}"
                if s.get("abbreviation"):
                    entry_line += f" ({s['abbreviation']})"
                entry_line += f" [ID: {s['id']}]"
                lines.append(entry_line)
            if rec_limit and len(recommended_standards) > rec_limit:
                lines.append(truncation_notice(rec_limit, len(recommended_standards), "standards"))
            lines.append("")

        if recommended_databases:
            lines.append(f"## Recommended Databases ({len(recommended_databases)})")
            to_show = recommended_databases[:rec_limit] if rec_limit else recommended_databases
            for d in to_show:
                entry_line = f"- {d['name']}"
                if d.get("abbreviation"):
                    entry_line += f" ({d['abbreviation']})"
                entry_line += f" [ID: {d['id']}]"
                lines.append(entry_line)
            if rec_limit and len(recommended_databases) > rec_limit:
                lines.append(truncation_notice(rec_limit, len(recommended_databases), "databases"))
            lines.append("")

    # Publications
    publications = record.get("publications", [])
    if publications:
        lines.append("## Publications")
        pub_limit = config.get_display_limit("publications")
        to_show = publications[:pub_limit] if pub_limit else publications
        for pub in to_show:
            title = pub.get("title", "Unknown")
            year = pub.get("year", "")
            pub_doi = pub.get("doi", "")
            journal = pub.get("journal", "")
            pub_line = f"- {title}"
            if year:
                pub_line += f" ({year})"
            if journal:
                pub_line += f" - {journal}"
            if pub_doi:
                pub_line += f" [DOI: {pub_doi}]"
            lines.append(pub_line)
        if pub_limit and len(publications) > pub_limit:
            lines.append(truncation_notice(pub_limit, len(publications), "publications"))
        lines.append("")

    return "\n".join(lines)


def format_database_quality_profile(record: dict) -> str:
    """Format a database record's FAIR quality indicator profile."""
    lines = []

    name = record.get("name", "Unknown")
    abbrev = record.get("abbreviation")
    if abbrev:
        lines.append(f"# FAIR Quality Profile: {name} ({abbrev})")
    else:
        lines.append(f"# FAIR Quality Profile: {name}")

    lines.append("")
    lines.append(f"- **ID:** {record.get('id', 'N/A')}")
    lines.append(f"- **Registry:** {record.get('registry', 'N/A')}")
    lines.append(f"- **Type:** {_format_type(record.get('type')) or 'N/A'}")
    lines.append(f"- **Status:** {_format_status(record.get('status'))}")

    doi = record.get("doi")
    url = build_fairsharing_url(doi)
    if url and doi:
        suffix = doi.split("FAIRsharing.", 1)[1]
        lines.append(f"- **FAIRsharing:** [FAIRsharing.{suffix}]({url})")
        lines.append(f"- **DOI:** {doi}")
    elif doi:
        lines.append(f"- **DOI:** {doi}")
    homepage = record.get("homepage")
    if homepage:
        lines.append(f"- **Homepage:** {homepage}")

    created_at = record.get("createdAt")
    updated_at = record.get("updatedAt")
    if created_at:
        lines.append(f"- **Created:** {created_at}")
    if updated_at:
        lines.append(f"- **Updated:** {updated_at}")
    lines.append("")

    # Subjects & Domains
    subjects = record.get("subjects", [])
    if subjects:
        subj_labels = [s.get("label", "") for s in subjects if s.get("label")]
        if subj_labels:
            lines.append(f"**Subjects:** {', '.join(subj_labels)}")

    domains = record.get("domains", [])
    if domains:
        dom_labels = [d.get("label", "") for d in domains if d.get("label")]
        if dom_labels:
            lines.append(f"**Domains:** {', '.join(dom_labels)}")
    lines.append("")

    # FAIR Indicators table
    indicator_display = {
        "dataAccessCondition": ("Data Access Condition", "Findable/Accessible"),
        "dataCuration": ("Data Curation", "Reusable"),
        "dataDepositionCondition": ("Data Deposition Condition", "Accessible"),
        "citationToRelatedPublications": ("Citation to Related Publications", "Reusable"),
        "dataContactInformation": ("Data Contact Information", "Accessible"),
        "dataVersioning": ("Data Versioning", "Reusable"),
        "dataPreservationPolicy": ("Data Preservation Policy", "Accessible"),
        "resourceSustainability": ("Resource Sustainability", "Accessible"),
        "usesPersistentIdentifier": ("Uses Persistent Identifier", "Findable"),
    }

    has_indicators = any(record.get(f) is not None for f in indicator_display)

    if has_indicators:
        lines.append("## FAIR Quality Indicators")
        lines.append("| Indicator | Value | FAIR Aspect | Rating |")
        lines.append("|-----------|-------|-------------|--------|")

        for field, (label, aspect) in indicator_display.items():
            val = record.get(field)
            if val is None:
                rating = "Unknown"
            elif isinstance(val, bool):
                rating = "Good" if val else "Gap"
            elif isinstance(val, str):
                val_lower = val.lower() if val else ""
                if val_lower in ("open", "manual", "manual/automated", "yes"):
                    rating = "Good"
                elif val_lower in ("partially open", "automated", "controlled"):
                    rating = "Fair"
                elif val_lower in ("none", "not found", "no", "not applicable"):
                    rating = "Gap"
                else:
                    rating = "Fair"
            else:
                rating = "Unknown"

            display_val = str(val) if val is not None else "N/A"
            lines.append(f"| {label} | {display_val} | {aspect} | {rating} |")

        detailed = compute_fair_score_detailed(record)
        score = detailed["score"]
        total_rated = detailed["total_rated"]
        total_possible = detailed["total_possible"]
        grade = detailed["grade"]

        lines.append("")
        lines.append(
            f"**Overall FAIR Score:** {score:.1f}/{total_rated} rated "
            f"({total_possible - total_rated} of {total_possible} indicators missing)"
        )
        if total_rated > 0:
            lines.append(f"**Grade:** {grade} ({detailed['pct_rated']:.0f}% of rated indicators)")
            # Show conservative grade when it differs (missing data causes inflation)
            if detailed["grade_conservative"] != grade:
                lines.append(
                    f"**Conservative grade:** {detailed['grade_conservative']} "
                    f"({detailed['pct_possible']:.0f}% of all {total_possible} indicators, "
                    f"missing treated as 0)"
                )
        lines.append(f"**Confidence:** {detailed['confidence']} — {detailed['confidence_note']}")
    else:
        lines.append("## FAIR Quality Indicators")
        lines.append("_FAIR indicator fields not available for this record via the API._")

    lines.append("")
    return "\n".join(lines)


def compute_fair_score(record: dict) -> tuple[float, int, str]:
    """Compute FAIR quality score from a database record's indicator fields.

    Returns (score, total_rated, grade).

    Note: Unknown or ambiguous string values receive 0.5 partial credit.
    Use ``compute_fair_score_detailed`` for confidence metadata.
    """
    detailed = compute_fair_score_detailed(record)
    return detailed["score"], detailed["total_rated"], detailed["grade"]


def compute_fair_score_detailed(record: dict) -> dict:
    """Compute FAIR quality score with confidence metadata.

    Returns a dict with:
        score: float — the raw score
        total_rated: int — number of indicators that had non-None values
        total_possible: int — total number of FAIR indicator fields (always 9)
        missing: int — indicators with None values (not present in API response)
        imputed: int — indicators where an unknown string value received 0.5 partial credit
        grade: str — letter grade (Excellent/Good/Fair/Needs Improvement/Unknown)
        confidence: str — "high", "medium", or "low" based on data completeness
        confidence_note: str — human-readable explanation of confidence level
    """
    score = 0.0
    total_rated = 0
    total_possible = len(DATABASE_FAIR_INDICATOR_FIELDS)
    missing = 0
    imputed = 0

    for field in DATABASE_FAIR_INDICATOR_FIELDS:
        val = record.get(field)
        if val is None:
            missing += 1
            continue
        if isinstance(val, bool):
            if val:
                score += 1
            total_rated += 1
        elif isinstance(val, str):
            val_lower = val.lower() if val else ""
            if val_lower in ("open", "manual", "manual/automated", "yes"):
                score += 1
            elif val_lower in ("partially open", "automated", "controlled"):
                score += 0.5
            # "none", "not found", "no", "not applicable" = 0
            elif val_lower not in ("none", "not found", "no", "not applicable"):
                score += 0.5  # Unknown string values get partial credit
                imputed += 1
            total_rated += 1

    # Grade based on rated indicators (optimistic — ignores missing data)
    if total_rated > 0:
        pct_rated = (score / total_rated) * 100
        if pct_rated >= 80:
            grade = "Excellent"
        elif pct_rated >= 60:
            grade = "Good"
        elif pct_rated >= 40:
            grade = "Fair"
        else:
            grade = "Needs Improvement"
    else:
        pct_rated = 0.0
        grade = "Unknown"

    # Conservative grade based on all 9 possible indicators (missing = 0)
    pct_possible = (score / total_possible) * 100 if total_possible > 0 else 0.0
    if total_possible > 0 and total_rated > 0:
        if pct_possible >= 80:
            grade_conservative = "Excellent"
        elif pct_possible >= 60:
            grade_conservative = "Good"
        elif pct_possible >= 40:
            grade_conservative = "Fair"
        else:
            grade_conservative = "Needs Improvement"
    else:
        grade_conservative = "Unknown"

    # Confidence assessment
    if missing == 0 and imputed == 0:
        confidence = "high"
        confidence_note = "All 9 indicators present with known values."
    elif missing <= 2 and imputed <= 1:
        confidence = "medium"
        parts = []
        if missing:
            parts.append(f"{missing} indicator(s) missing from API")
        if imputed:
            parts.append(f"{imputed} indicator(s) had ambiguous values (scored as 0.5)")
        confidence_note = "; ".join(parts) + "."
    else:
        confidence = "low"
        parts = []
        if missing:
            parts.append(f"{missing} of {total_possible} indicators missing from API")
        if imputed:
            parts.append(f"{imputed} had ambiguous values (scored as 0.5)")
        confidence_note = "; ".join(parts) + ". Score may not reflect actual quality."

    return {
        "score": score,
        "total_rated": total_rated,
        "total_possible": total_possible,
        "missing": missing,
        "imputed": imputed,
        "grade": grade,
        "grade_conservative": grade_conservative,
        "pct_rated": round(pct_rated, 1),
        "pct_possible": round(pct_possible, 1),
        "confidence": confidence,
        "confidence_note": confidence_note,
    }


def normalize_quality_score(
    raw_score: float,
    raw_max: float,
    registry: str,
    confidence: str,
) -> dict:
    """Normalize a registry-specific quality score to a unified 0-100 scale.

    Args:
        raw_score: The raw score from the registry-specific scorer.
        raw_max: Maximum possible raw score (9 for Database, 10 for Standard/Policy).
        registry: "Database", "Standard", or "Policy".
        confidence: "high", "medium", or "low" from the original scorer.

    Returns:
        Dict with normalized_score, raw_score, raw_max, registry,
        unified_grade, confidence, confidence_note.
    """
    if raw_max <= 0:
        normalized = 0.0
    else:
        normalized = (raw_score / raw_max) * 100

    unified_grade = "F"
    for grade_label, threshold in UNIFIED_GRADE_THRESHOLDS:
        if normalized >= threshold:
            unified_grade = grade_label
            break

    caveat_by_registry = {
        "Database": "Based on 9 FAIR indicator fields (access, curation, PIDs, etc.).",
        "Standard": "Based on metadata completeness, maintenance, and adoption.",
        "Policy": "Based on mandate coverage, breadth, and recommendations.",
    }
    confidence_note = caveat_by_registry.get(registry, f"Scoring criteria for {registry} records.")

    return {
        "normalized_score": round(normalized, 1),
        "raw_score": raw_score,
        "raw_max": raw_max,
        "registry": registry,
        "unified_grade": unified_grade,
        "confidence": confidence,
        "confidence_note": confidence_note,
    }
