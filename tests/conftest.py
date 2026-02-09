"""Shared test fixtures for FAIRsharing MCP server tests."""

from unittest.mock import AsyncMock, patch

import pytest


@pytest.fixture
def mock_client():
    """Create a mock FAIRsharing client with AsyncMock query method."""
    client = AsyncMock()
    client.query = AsyncMock()
    return client


@pytest.fixture
def patch_get_client(mock_client):
    """Patch app.get_client to return the mock client."""
    with patch("fairsharing_mcp.app.get_client", return_value=mock_client):
        yield mock_client


# -- Common mock data factories --


def make_record(
    id="1",
    name="Test Record",
    abbreviation="TR",
    registry="Database",
    record_type="repository",
    status="ready",
    **extra,
):
    """Create a mock FAIRsharing record dict."""
    rec = {
        "id": id,
        "name": name,
        "abbreviation": abbreviation,
        "registry": registry,
        "type": record_type,
        "status": status,
        "doi": extra.get("doi", "10.1234/test"),
        "homepage": extra.get("homepage", "https://example.com"),
        "description": extra.get("description", "A test record for unit testing."),
        "createdAt": extra.get("createdAt", "2023-01-01"),
        "updatedAt": extra.get("updatedAt", "2024-01-01"),
        "subjects": extra.get("subjects", [{"id": "1", "label": "Genomics"}]),
        "domains": extra.get("domains", [{"id": "1", "label": "Bioinformatics"}]),
        "taxonomies": extra.get("taxonomies", []),
        "organisations": extra.get("organisations", [{"id": "1", "name": "Test Org"}]),
        "publications": extra.get("publications", []),
        "licenceLinks": extra.get("licenceLinks", []),
        "countries": extra.get("countries", [{"name": "United Kingdom"}]),
        "recordAssociations": extra.get("recordAssociations", []),
        "reverseRecordAssociations": extra.get("reverseRecordAssociations", []),
    }
    rec.update({k: v for k, v in extra.items() if k not in rec})
    return rec


def make_standard(id="100", name="Test Standard", abbreviation="TS", **extra):
    """Create a mock Standard record."""
    return make_record(
        id=id,
        name=name,
        abbreviation=abbreviation,
        registry="Standard",
        record_type="model/format",
        **extra,
    )


def make_policy(id="200", name="Test Policy", abbreviation="TP", **extra):
    """Create a mock Policy record."""
    defaults = {
        "mandatedDataSharing": "required",
        "mandatedDmpCreation": "suggested",
        "sharingResearchSoftware": "not covered",
        "metadataSharing": "required",
        "dataProtection": "yes",
        "dataAvailabilityStatement": "yes",
        "dataCitation": "no",
        "dataPreservation": "yes",
        "licencesForOutputs": "no",
        "monitoringOfCompliance": "yes",
        "supportedCosts": "no",
        "timingOfDmp": "before",
        "updatingOfDmp": "yes",
        "guidanceToHelpEnableCompliance": "yes",
    }
    defaults.update(extra)
    return make_record(
        id=id,
        name=name,
        abbreviation=abbreviation,
        registry="Policy",
        record_type="funder",
        **defaults,
    )


def make_search_result(records, total_count=None, total_pages=None):
    """Wrap records in a searchFairsharingRecords response."""
    return {
        "searchFairsharingRecords": {
            "records": records,
            "totalCount": total_count or len(records),
            "totalPages": total_pages or 1,
        }
    }
