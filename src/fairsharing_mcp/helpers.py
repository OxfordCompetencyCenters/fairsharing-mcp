"""FAIRsharing MCP Server - Shared helper functions."""

from __future__ import annotations

import logging

from fairsharing_mcp import app
from fairsharing_mcp.client import FAIRsharingAuthError, FAIRsharingError
from fairsharing_mcp.formatters import extract_fair_indicators
from fairsharing_mcp.queries import (
    GET_DATABASE_QUALITY_QUERY,
    GET_POLICY_DETAIL_QUERY,
    GET_RECORD_QUERY,
)

logger = logging.getLogger(__name__)


def extract_policy_mandates(record: dict) -> tuple[dict, bool]:
    """Extract policy mandate fields from the metadata JSON blob into flat keys.

    The FAIRsharing API stores policy mandate data inside a JSON `metadata`
    field rather than as top-level GraphQL fields.  This helper maps them to
    the flat key names the rest of the codebase expects (e.g. ``mandatedDataSharing``).
    The record dict is mutated in place. Returns (record, extracted): extracted is
    True when metadata was present and extraction was performed; False when
    metadata was missing or invalid. When extracted is False, the record is
    marked with _mandate_extraction_failed=True so callers can surface that
    mandate data could not be loaded.
    """
    meta = record.get("metadata")
    if not meta or not isinstance(meta, dict):
        record["_mandate_extraction_failed"] = True
        return record, False

    sharing_data = meta.get("sharing_data") or {}
    record.setdefault("mandatedDataSharing", sharing_data.get("mandated_data_sharing"))
    record.setdefault("exceptionsToDataSharing", sharing_data.get("exceptions_to_data_sharing"))

    record.setdefault(
        "sharingResearchSoftware", (meta.get("sharing_research_software") or {}).get("value")
    )

    dmp = meta.get("dmp_development") or {}
    record.setdefault("mandatedDmpCreation", dmp.get("mandated_dmp_creation"))
    record.setdefault("timingOfDmp", dmp.get("timing_of_dmp"))
    record.setdefault("updatingOfDmp", dmp.get("updating_of_dmp"))

    record.setdefault("dataProtection", (meta.get("data_protection") or {}).get("value"))
    record.setdefault(
        "dataAvailabilityStatement", (meta.get("data_availability_statement") or {}).get("required")
    )
    record.setdefault(
        "licencesForOutputs", (meta.get("licences_for_outputs") or {}).get("required")
    )
    record.setdefault("dataCitation", (meta.get("data_citation") or {}).get("value"))
    record.setdefault("dataPreservation", (meta.get("data_preservation") or {}).get("value"))
    record.setdefault("supportedCosts", (meta.get("supported_costs") or {}).get("value"))

    compliance = meta.get("compliance") or {}
    record.setdefault(
        "guidanceToHelpEnableCompliance", compliance.get("guidance_to_help_enable_compliance")
    )
    record.setdefault("monitoringOfCompliance", compliance.get("monitoring_of_compliance"))

    record.setdefault("metadataSharing", (meta.get("sharing_metadata") or {}).get("value"))

    return record, True


async def fetch_policy_with_fallback(record_id: int) -> dict | None:
    """Fetch a policy record with mandate data extracted from the metadata blob.

    Auth errors (invalid API key) are re-raised immediately — falling back
    would hide a configuration problem.  Transient errors (timeout, rate limit,
    server error) trigger the fallback to the basic record query.
    """
    client = app.get_client()
    try:
        data = await client.query(GET_POLICY_DETAIL_QUERY, {"id": record_id})
        record = data.get("fairsharingRecord")
        if record:
            extract_policy_mandates(record)  # mutates in place
            return record
    except FAIRsharingAuthError:
        raise  # Do not fall back on auth errors — the key is bad
    except FAIRsharingError:
        logger.warning("Policy detail query failed for record %s, trying basic query", record_id)

    # Fallback to basic query (no metadata field, so no mandate data)
    try:
        data = await client.query(GET_RECORD_QUERY, {"id": record_id})
        record = data.get("fairsharingRecord")
        if record:
            logger.info("Basic record query fallback succeeded for record %s", record_id)
            record["_mandate_data_unavailable"] = True
            if record.get("registry") != "Policy":
                logger.warning(
                    "Fallback record %s is a %s, not a Policy",
                    record_id,
                    record.get("registry", "?"),
                )
            return record
    except FAIRsharingAuthError:
        raise  # Do not swallow auth errors
    except FAIRsharingError:
        logger.warning("Basic record query also failed for record %s", record_id)

    return None


async def fetch_database_quality_with_fallback(record_id: int) -> dict | None:
    """Fetch a database record with FAIR indicators, falling back to basic query.

    Auth errors are re-raised immediately; transient errors trigger fallback.
    After fetching, FAIR indicator values are extracted from the ``metadata`` blob
    and merged into the record dict under their camelCase keys for backwards
    compatibility with scoring and formatting code.
    """
    client = app.get_client()
    try:
        data = await client.query(GET_DATABASE_QUALITY_QUERY, {"id": record_id})
        record = data.get("fairsharingRecord")
        if record:
            logger.info("Database quality query succeeded for record %s", record_id)
            indicators = extract_fair_indicators(record)
            record.update(indicators)
            return record
    except FAIRsharingAuthError:
        raise  # Do not fall back on auth errors
    except FAIRsharingError:
        logger.warning("Database quality query failed for record %s, trying basic query", record_id)

    # Fallback to basic query
    try:
        data = await client.query(GET_RECORD_QUERY, {"id": record_id})
        record = data.get("fairsharingRecord")
        if record:
            logger.info("Basic record query fallback succeeded for record %s", record_id)
            indicators = extract_fair_indicators(record)
            record.update(indicators)
            return record
    except FAIRsharingAuthError:
        raise  # Do not swallow auth errors
    except FAIRsharingError:
        logger.warning("Basic record query also failed for record %s", record_id)

    return None


def matches_date_range(
    date_str: str | None,
    min_year: int | None = None,
    max_year: int | None = None,
) -> bool:
    """Check if an ISO date string's year falls within [min_year, max_year].

    Returns True if the date matches the range, False if it doesn't or if
    date_str is None/unparseable. Either bound can be None (unbounded).
    """
    if not date_str:
        return False
    try:
        year = int(date_str[:4])
    except (ValueError, IndexError):
        return False
    if min_year is not None and year < min_year:
        return False
    if max_year is not None and year > max_year:
        return False
    return True
