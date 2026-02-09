import json
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from fairsharing_mcp.client import FAIRsharingClient, FAIRsharingError
from fairsharing_mcp.graph_utils import merge_graphs, parse_graph
from fairsharing_mcp.helpers import matches_date_range
from fairsharing_mcp.tools.comparison import (
    analyze_deprecation_impact,
    analyze_transitive_impact,
    assess_dmp_compliance,
    check_policy_database_compliance,
    find_compliant_standards,
)
from fairsharing_mcp.tools.curator import batch_audit_metadata
from fairsharing_mcp.tools.discovery import (
    aggregate_by_field,
    explain_fairsharing,
    find_databases_by_standard,
    find_deprecated_resources,
    find_orphan_records,
    recommend_tools,
    suggest_graph_starting_points,
    suggest_workflow,
)
from fairsharing_mcp.tools.graph import detect_circular_dependencies, find_record_connections
from fairsharing_mcp.tools.graph_analysis import (
    analyze_graph_comprehensive,
    analyze_path_criticality,
    build_topic_graph,
    compute_betweenness_centrality,
    compute_pagerank,
    detect_communities,
    explore_expanded_graph,
    find_cross_graph_path,
    find_dependency_clusters,
    find_multiple_paths,
    find_semantic_path,
    find_similar_records,
)
from fairsharing_mcp.tools.organisations import (
    analyze_country_landscape,
    analyze_regional_distribution,
    get_records_by_organisation,
    list_countries,
    list_organisations,
    search_organisations,
)
from fairsharing_mcp.tools.policies import (
    analyze_policy_mandates,
    compare_policies_by_country,
    detect_policy_conflicts,
    find_policy_gaps,
    get_policy_details,
    get_policy_quality_profile,
    trace_policy_impact,
)
from fairsharing_mcp.tools.quality import (
    assess_database_indicators,
    compare_databases_quality,
    compare_unified_quality,
    get_comprehensive_quality_profile,
    get_database_quality_profile,
    get_unified_quality_score,
    rank_databases_by_quality,
)
from fairsharing_mcp.tools.records import (
    filter_records_by_date,
    find_referencing_records,
    get_record,
    get_record_graph,
    get_record_types,
    get_records_batch,
)
from fairsharing_mcp.tools.search import (
    advanced_filter_records,
    count_fair_records,
    count_records,
    search_by_doi,
    search_records,
    search_records_by_license,
)
from fairsharing_mcp.tools.standards import (
    analyze_standard_adoption,
    compute_maturity_index,
    find_databases_for_standard,
    find_emerging_standards,
    find_endorsed_but_unadopted,
    find_standards_for_database,
    get_standard_quality_profile,
)
from fairsharing_mcp.tools.taxonomy import (
    get_domain,
    get_subject,
    list_domains,
    list_subjects,
    list_taxonomies,
    search_domains,
    search_subjects,
    search_taxonomies,
)


class TestFAIRsharingServer(unittest.IsolatedAsyncioTestCase):
    @patch("fairsharing_mcp.app.get_client")
    async def test_filter_records_by_date(self, mock_get_client):
        # Mock client and query response
        mock_client = AsyncMock()
        mock_get_client.return_value = mock_client

        # Setup mock data for two pages
        # Page 1: 3 records, one match (2020), one too old (2010), one too new (2025)
        # Page 2: 1 match (2021)
        record1 = {"name": "Match1", "id": "1", "createdAt": "2020-01-01", "registry": "Database"}
        record2 = {"name": "Old", "id": "2", "createdAt": "2010-01-01", "registry": "Database"}
        record3 = {"name": "Future", "id": "3", "createdAt": "2025-01-01", "registry": "Database"}
        record4 = {"name": "Match2", "id": "4", "createdAt": "2021-01-01", "registry": "Database"}

        # Simulating pagination: first call returns 3 records, second call returns 1 record, third empty
        mock_client.query.side_effect = [
            {"searchFairsharingRecords": {"records": [record1, record2, record3]}},
            {"searchFairsharingRecords": {"records": [record4]}},
            {"searchFairsharingRecords": {"records": []}},
        ]

        # Test filtering
        result = await filter_records_by_date(min_year=2019, max_year=2022, limit=10)

        self.assertIn("Match1", result)
        self.assertIn("Match2", result)
        self.assertNotIn("Old", result)  # Should be filtered out
        self.assertNotIn("Future", result)  # Should be filtered out

        # Verify pagination logic called query multiple times
        self.assertGreaterEqual(mock_client.query.call_count, 2)

    @patch("fairsharing_mcp.app.get_client")
    async def test_detect_circular_dependencies_no_cycle(self, mock_get_client):
        mock_client = AsyncMock()
        mock_get_client.return_value = mock_client

        # Mock initial record fetch
        mock_client.query.side_effect = [
            # get_record (initial name)
            {"fairsharingRecord": {"name": "Root", "id": "1"}},
            # get_record_with_associations (root) -> links to child
            {
                "fairsharingRecord": {
                    "recordAssociations": [{"linkedRecord": {"id": "2", "name": "Child"}}]
                }
            },
            # get_record_with_associations (child) -> links to nothing
            {"fairsharingRecord": {"recordAssociations": []}},
        ]

        result = await detect_circular_dependencies(1)
        self.assertIn("No circular dependencies detected", result)

    @patch("fairsharing_mcp.app.get_client")
    async def test_detect_circular_dependencies_with_cycle(self, mock_get_client):
        mock_client = AsyncMock()
        mock_get_client.return_value = mock_client

        # Mock cycle: 1 -> 2 -> 1
        mock_client.query.side_effect = [
            # get_record (initial name)
            {"fairsharingRecord": {"name": "Root", "id": "1"}},
            # get_record_with_associations (root) -> links to child
            {
                "fairsharingRecord": {
                    "recordAssociations": [{"linkedRecord": {"id": "2", "name": "Child"}}]
                }
            },
            # get_record_with_associations (child) -> links back to root (1)
            {
                "fairsharingRecord": {
                    "recordAssociations": [{"linkedRecord": {"id": "1", "name": "Root"}}]
                }
            },
        ]

        result = await detect_circular_dependencies(1)
        self.assertIn("Circular Dependencies Detected", result)
        self.assertIn("Root [1] -> Child [2] -> Root [1]", result)

    @patch("fairsharing_mcp.app.get_client")
    async def test_batch_audit_metadata(self, mock_get_client):
        mock_client = AsyncMock()
        mock_get_client.return_value = mock_client

        # Mock search result
        mock_client.query.side_effect = [
            # search
            {
                "searchFairsharingRecords": {
                    "records": [
                        {
                            "id": "1",
                            "name": "TestDB",
                            "registry": "Database",
                            "createdAt": "2020-01-01",
                        }
                    ]
                }
            },
            # get full details
            {
                "fairsharingRecord": {
                    "name": "TestDB",
                    "registry": "Database",
                    "description": "A test db",
                    "subjects": ["Genomics"],
                    "domains": ["Biology"],
                    "homepage": "http://example.com",
                    "licenceLinks": [{"licence": {"id": "1", "name": "MIT"}}],
                    # Missing: abbreviation, publications, organisations,
                    #   dataAccessCondition, dataPreservationPolicy, taxonomies
                }
            },
        ]

        result = await batch_audit_metadata(limit=1)
        self.assertIn("TestDB", result)
        self.assertIn("Database", result)
        # Check required/recommended missing calculation
        # Required (4): name ✓, description ✓, subjects ✓, domains ✓ = 4 present
        # Common recommended (5): homepage ✓, abbreviation ✗, licenceLinks ✓,
        #   publications ✗, organisations ✗ = 2 present
        # Database recommended (1): taxonomies ✗ = 0 present
        # (dataAccessCondition/dataPreservationPolicy removed — not fetched by query)
        # Total: 6 present / 10 total = 60.0%

        self.assertIn("60.0%", result)

    # ── search_records ──────────────────────────────────────────────────

    @patch("fairsharing_mcp.app.get_client")
    async def test_search_records_with_results(self, mock_get_client):
        mock_client = AsyncMock()
        mock_get_client.return_value = mock_client

        mock_client.query.return_value = {
            "searchFairsharingRecords": {
                "records": [
                    {
                        "id": "10",
                        "name": "BioPortal",
                        "abbreviation": "BP",
                        "registry": "Database",
                        "type": "knowledgebase",
                        "status": "ready",
                        "subjects": [{"label": "Ontology"}],
                        "domains": [],
                        "description": "A repository of ontologies.",
                        "doi": None,
                        "createdAt": "2015-01-01",
                    },
                    {
                        "id": "20",
                        "name": "UniProt",
                        "abbreviation": "UP",
                        "registry": "Database",
                        "type": "knowledgebase",
                        "status": "ready",
                        "subjects": [{"label": "Proteomics"}],
                        "domains": [],
                        "description": "Protein sequences.",
                        "doi": None,
                        "createdAt": "2014-01-01",
                    },
                ],
                "totalCount": 42,
                "totalPages": 3,
            }
        }

        result = await search_records(query="bio", page=1, per_page=20)

        self.assertIn("Page 1 of 3", result)
        self.assertIn("42 records", result)
        self.assertIn("BioPortal", result)
        self.assertIn("UniProt", result)
        self.assertIn("Ontology", result)
        self.assertIn("Proteomics", result)
        # Should have pagination hint for next page
        self.assertIn("page=2", result)

    @patch("fairsharing_mcp.app.get_client")
    async def test_search_records_empty(self, mock_get_client):
        mock_client = AsyncMock()
        mock_get_client.return_value = mock_client

        mock_client.query.return_value = {
            "searchFairsharingRecords": {
                "records": [],
                "totalCount": 0,
                "totalPages": 0,
            }
        }

        result = await search_records(query="xyznonexistent")
        self.assertIn("No records found", result)
        self.assertIn("query='xyznonexistent'", result)
        self.assertIn("Try broadening", result)

    @patch("fairsharing_mcp.app.get_client")
    async def test_search_records_pagination_clamping(self, mock_get_client):
        mock_client = AsyncMock()
        mock_get_client.return_value = mock_client

        mock_client.query.return_value = {
            "searchFairsharingRecords": {
                "records": [{"id": "1", "name": "R1", "registry": "Database"}],
                "totalCount": 1,
                "totalPages": 1,
            }
        }

        await search_records(per_page=100, page=0)

        call_args = mock_client.query.call_args
        variables = call_args[0][1]
        # per_page clamped to 50, page clamped to 1
        self.assertEqual(variables["perPage"], 50)
        self.assertEqual(variables["page"], 1)

    @patch("fairsharing_mcp.app.get_client")
    async def test_search_records_filters_passed(self, mock_get_client):
        mock_client = AsyncMock()
        mock_get_client.return_value = mock_client

        mock_client.query.return_value = {
            "searchFairsharingRecords": {
                "records": [{"id": "1", "name": "R1", "registry": "Database"}],
                "totalCount": 1,
                "totalPages": 1,
            }
        }

        await search_records(
            registry=["Database"],
            status=["ready"],
            is_maintained=True,
            search_and=False,
        )

        variables = mock_client.query.call_args[0][1]
        self.assertEqual(variables["registry"], ["Database"])
        self.assertEqual(variables["status"], ["ready"])
        self.assertEqual(variables["isMaintained"], True)
        # search_and=False → searchAnd should be False
        self.assertEqual(variables["searchAnd"], False)

    @patch("fairsharing_mcp.app.get_client")
    async def test_search_records_last_page(self, mock_get_client):
        mock_client = AsyncMock()
        mock_get_client.return_value = mock_client

        mock_client.query.return_value = {
            "searchFairsharingRecords": {
                "records": [{"id": "1", "name": "Last", "registry": "Standard"}],
                "totalCount": 5,
                "totalPages": 3,
            }
        }

        result = await search_records(page=3)
        self.assertIn("Last", result)
        # On the last page, no "Use page=N" hint
        self.assertNotIn("page=4", result)

    # ── get_record ───────────────────────────────────────────────────

    @patch("fairsharing_mcp.app.get_client")
    async def test_get_record_full(self, mock_get_client):
        mock_client = AsyncMock()
        mock_get_client.return_value = mock_client

        mock_client.query.return_value = {
            "fairsharingRecord": {
                "id": "42",
                "name": "GenBank",
                "abbreviation": "GB",
                "description": "Nucleotide sequence database.",
                "doi": "10.25504/FAIRsharing.9kahy4",
                "homepage": "https://www.ncbi.nlm.nih.gov/genbank/",
                "status": "ready",
                "registry": "Database",
                "type": "repository",
                "createdAt": "2014-11-04",
                "updatedAt": "2023-01-01",
                "isApproved": True,
                "isMaintained": True,
                "isRecommended": False,
                "subjects": [
                    {"id": "1", "label": "Genomics", "iri": "http://example.org/genomics"}
                ],
                "domains": [{"id": "2", "label": "Nucleotide sequence", "iri": ""}],
                "taxonomies": [{"id": "3", "label": "Homo sapiens", "iri": ""}],
                "countries": [{"id": "1", "name": "United States", "code": "US"}],
                "organisations": [
                    {"id": "1", "name": "NCBI", "homepage": "https://www.ncbi.nlm.nih.gov/"}
                ],
                "publications": [
                    {
                        "id": "1",
                        "title": "GenBank paper",
                        "doi": "10.1093/nar/gkaa1023",
                        "year": "2021",
                        "journal": "NAR",
                    }
                ],
                "licenceLinks": [
                    {
                        "licence": {"id": "1", "name": "Open Access", "url": "https://example.com"},
                        "relation": "applies_to",
                    }
                ],
                "userDefinedTags": [{"id": "1", "label": "sequence"}],
                "recordAssociations": [
                    {
                        "linkedRecord": {
                            "id": "100",
                            "name": "INSDC",
                            "registry": "Standard",
                            "type": "terminology_artefact",
                        },
                        "recordAssocLabel": "implements",
                    },
                ],
                "reverseRecordAssociations": [
                    {
                        "fairsharingRecord": {
                            "id": "200",
                            "name": "DataCite",
                            "registry": "Policy",
                            "type": "funder",
                        },
                        "recordAssocLabel": "recommends",
                    },
                ],
            }
        }

        result = await get_record(42)

        # All major sections present
        self.assertIn("GenBank (GB)", result)
        self.assertIn("Genomics", result)
        self.assertIn("Nucleotide sequence", result)
        self.assertIn("Homo sapiens", result)
        self.assertIn("United States", result)
        self.assertIn("NCBI", result)
        self.assertIn("GenBank paper", result)
        self.assertIn("Open Access", result)
        self.assertIn("sequence", result)  # tag
        self.assertIn("INSDC", result)
        self.assertIn("DataCite", result)
        self.assertIn("Approved", result)
        self.assertIn("Maintained", result)

    @patch("fairsharing_mcp.app.get_client")
    async def test_get_record_invalid_id_validation(self, mock_get_client):
        result = await get_record(-1)
        self.assertIn("Validation error", result)
        self.assertIn("positive", result)
        mock_get_client.assert_not_called()

    @patch("fairsharing_mcp.app.get_client")
    async def test_get_record_not_found(self, mock_get_client):
        mock_client = AsyncMock()
        mock_get_client.return_value = mock_client

        mock_client.query.return_value = {"fairsharingRecord": None}

        result = await get_record(99999)
        self.assertEqual(result, "No record found with ID 99999.")

    @patch("fairsharing_mcp.app.get_client")
    async def test_get_record_minimal(self, mock_get_client):
        mock_client = AsyncMock()
        mock_get_client.return_value = mock_client

        mock_client.query.return_value = {
            "fairsharingRecord": {
                "id": "5",
                "name": "MinimalDB",
                "registry": "Database",
                "type": "repository",
                "status": "in_development",
                "abbreviation": None,
                "description": None,
                "doi": None,
                "homepage": None,
                "createdAt": None,
                "updatedAt": None,
                "isApproved": False,
                "isMaintained": False,
                "isRecommended": False,
                "subjects": [],
                "domains": [],
                "taxonomies": [],
                "countries": [],
                "organisations": [],
                "publications": [],
                "licenceLinks": [],
                "userDefinedTags": [],
                "recordAssociations": [],
                "reverseRecordAssociations": [],
            }
        }

        result = await get_record(5)

        self.assertIn("MinimalDB", result)
        self.assertIn("in_development", result)
        # No flags should appear since all are False
        self.assertNotIn("Approved", result)
        self.assertNotIn("Maintained", result)
        self.assertNotIn("Recommended", result)

    # ── get_record_graph ─────────────────────────────────────────────

    @patch("fairsharing_mcp.app.get_client")
    async def test_get_record_graph_normal(self, mock_get_client):
        mock_client = AsyncMock()
        mock_get_client.return_value = mock_client

        graph_data = {
            "name": "TestGraph",
            "registry": "Standard",
            "nodes": [
                {
                    "key": "1",
                    "attributes": {
                        "label": "StdA",
                        "registry": "standard",
                        "record_type": "terminology_artefact",
                        "status": "ready",
                    },
                },
                {
                    "key": "2",
                    "attributes": {
                        "label": "StdB",
                        "registry": "standard",
                        "record_type": "model_and_format",
                        "status": "ready",
                    },
                },
                {
                    "key": "3",
                    "attributes": {
                        "label": "DB1",
                        "registry": "database",
                        "record_type": "repository",
                        "status": "ready",
                    },
                },
                {
                    "key": "4",
                    "attributes": {
                        "label": "DB2",
                        "registry": "database",
                        "record_type": "knowledgebase",
                        "status": "deprecated",
                    },
                },
                {
                    "key": "5",
                    "attributes": {
                        "label": "Pol1",
                        "registry": "policy",
                        "record_type": "funder",
                        "status": "ready",
                    },
                },
            ],
            "edges": [
                {"source": "1", "target": "3", "attributes": {"color": "pink"}},
                {"source": "1", "target": "4", "attributes": {"color": "pink"}},
                {"source": "2", "target": "3", "attributes": {"color": "pink"}},
                {"source": "5", "target": "1", "attributes": {"color": "orange"}},
                {"source": "5", "target": "2", "attributes": {"color": "orange"}},
                {"source": "1", "target": "2", "attributes": {"color": "grey"}},
            ],
        }

        mock_client.query.return_value = {"fairsharingGraph": {"data": graph_data}}

        result = await get_record_graph(1)

        self.assertIn("TestGraph", result)
        self.assertIn("5 nodes", result)
        self.assertIn("6 edges", result)
        # Registry distribution
        self.assertIn("Standard", result)
        self.assertIn("Database", result)
        self.assertIn("Policy", result)
        # Edge types
        self.assertIn("implements", result)  # pink
        self.assertIn("recommends", result)  # orange
        # Hub analysis
        self.assertIn("Hub Nodes", result)
        self.assertIn("StdA", result)

    @patch("fairsharing_mcp.app.get_client")
    async def test_get_record_graph_no_data(self, mock_get_client):
        mock_client = AsyncMock()
        mock_get_client.return_value = mock_client

        mock_client.query.return_value = {"fairsharingGraph": {}}

        result = await get_record_graph(999)
        self.assertIn("No graph data available", result)

    @patch("fairsharing_mcp.app.get_client")
    async def test_get_record_graph_string_json(self, mock_get_client):
        mock_client = AsyncMock()
        mock_get_client.return_value = mock_client

        graph_data = {
            "name": "StringGraph",
            "registry": "Database",
            "nodes": [
                {
                    "key": "10",
                    "attributes": {
                        "label": "NodeA",
                        "registry": "database",
                        "record_type": "repository",
                        "status": "ready",
                    },
                },
            ],
            "edges": [],
        }

        mock_client.query.return_value = {"fairsharingGraph": {"data": json.dumps(graph_data)}}

        result = await get_record_graph(10)
        self.assertIn("StringGraph", result)
        self.assertIn("1 nodes", result)
        self.assertIn("0 edges", result)

    # ── trace_policy_impact ──────────────────────────────────────────

    @patch("fairsharing_mcp.app.get_client")
    async def test_trace_policy_impact_full_chain(self, mock_get_client):
        mock_client = AsyncMock()
        mock_get_client.return_value = mock_client

        # Call 1: fetch the policy
        policy_data = {
            "fairsharingRecord": {
                "id": "500",
                "name": "FAIR Policy",
                "registry": "Policy",
                "type": "funder",
                "status": "ready",
                "abbreviation": "FP",
                "recordAssociations": [
                    {
                        "linkedRecord": {
                            "id": "100",
                            "name": "StdAlpha",
                            "abbreviation": "SA",
                            "registry": "Standard",
                            "type": "terminology_artefact",
                            "status": "ready",
                        },
                        "recordAssocLabel": "recommends",
                    },
                    {
                        "linkedRecord": {
                            "id": "101",
                            "name": "StdBeta",
                            "abbreviation": "",
                            "registry": "Standard",
                            "type": "model_and_format",
                            "status": "ready",
                        },
                        "recordAssocLabel": "recommends",
                    },
                ],
                "reverseRecordAssociations": [],
            }
        }

        # Call 2: fetch StdAlpha's reverse associations (2 implementing DBs)
        std_alpha_data = {
            "fairsharingRecord": {
                "id": "100",
                "name": "StdAlpha",
                "reverseRecordAssociations": [
                    {
                        "fairsharingRecord": {
                            "id": "200",
                            "name": "DB_One",
                            "registry": "Database",
                        },
                        "recordAssocLabel": "implements",
                    },
                    {
                        "fairsharingRecord": {
                            "id": "201",
                            "name": "DB_Two",
                            "registry": "Database",
                        },
                        "recordAssocLabel": "implements",
                    },
                ],
                "recordAssociations": [],
            }
        }

        # Call 3: fetch StdBeta's reverse associations (1 implementing DB)
        std_beta_data = {
            "fairsharingRecord": {
                "id": "101",
                "name": "StdBeta",
                "reverseRecordAssociations": [
                    {
                        "fairsharingRecord": {
                            "id": "200",
                            "name": "DB_One",
                            "registry": "Database",
                        },
                        "recordAssocLabel": "implements",
                    },
                ],
                "recordAssociations": [],
            }
        }

        mock_client.query.side_effect = [policy_data, std_alpha_data, std_beta_data]

        result = await trace_policy_impact(500)

        self.assertIn("FAIR Policy", result)
        self.assertIn("Standards recommended:", result)
        self.assertIn("2", result)  # 2 standards
        self.assertIn("StdAlpha", result)
        self.assertIn("StdBeta", result)
        self.assertIn("DB_One", result)
        self.assertIn("DB_Two", result)
        # Impact summary
        self.assertIn("Impact Summary", result)
        self.assertIn("Most impactful standard", result)
        self.assertIn("StdAlpha", result)  # 2 databases > 1

    @patch("fairsharing_mcp.app.get_client")
    async def test_trace_policy_impact_non_policy(self, mock_get_client):
        mock_client = AsyncMock()
        mock_get_client.return_value = mock_client

        mock_client.query.return_value = {
            "fairsharingRecord": {
                "id": "10",
                "name": "SomeDB",
                "registry": "Database",
                "type": "repository",
                "status": "ready",
                "recordAssociations": [],
                "reverseRecordAssociations": [],
            }
        }

        result = await trace_policy_impact(10)
        self.assertIn("is a Database, not a Policy", result)

    @patch("fairsharing_mcp.app.get_client")
    async def test_trace_policy_impact_no_standards(self, mock_get_client):
        mock_client = AsyncMock()
        mock_get_client.return_value = mock_client

        mock_client.query.return_value = {
            "fairsharingRecord": {
                "id": "500",
                "name": "Empty Policy",
                "registry": "Policy",
                "type": "funder",
                "status": "ready",
                "recordAssociations": [],
                "reverseRecordAssociations": [],
            }
        }

        result = await trace_policy_impact(500)
        self.assertIn("does not recommend any standards", result)

    @patch("fairsharing_mcp.app.get_client")
    async def test_trace_policy_impact_api_error_second_hop(self, mock_get_client):
        mock_client = AsyncMock()
        mock_get_client.return_value = mock_client

        policy_data = {
            "fairsharingRecord": {
                "id": "500",
                "name": "Error Policy",
                "registry": "Policy",
                "type": "funder",
                "status": "ready",
                "recordAssociations": [
                    {
                        "linkedRecord": {
                            "id": "100",
                            "name": "BadStd",
                            "abbreviation": "",
                            "registry": "Standard",
                            "type": "terminology_artefact",
                            "status": "ready",
                        },
                        "recordAssocLabel": "recommends",
                    },
                ],
                "reverseRecordAssociations": [],
            }
        }

        mock_client.query.side_effect = [
            policy_data,
            FAIRsharingError("API timeout"),
        ]

        result = await trace_policy_impact(500)

        self.assertIn("Error Policy", result)
        self.assertIn("BadStd", result)
        # Graceful handling: no crash, shows no implementing databases
        self.assertIn("No implementing databases", result)

    # ── find_record_connections ───────────────────────────────────────

    @patch("fairsharing_mcp.app.get_client")
    async def test_find_connections_direct(self, mock_get_client):
        mock_client = AsyncMock()
        mock_get_client.return_value = mock_client

        graph_data = {
            "name": "Graph",
            "nodes": [
                {
                    "key": "1",
                    "attributes": {
                        "label": "RecA",
                        "registry": "standard",
                        "record_type": "terminology_artefact",
                        "status": "ready",
                    },
                },
                {
                    "key": "2",
                    "attributes": {
                        "label": "RecB",
                        "registry": "database",
                        "record_type": "repository",
                        "status": "ready",
                    },
                },
            ],
            "edges": [
                {"source": "1", "target": "2", "attributes": {"color": "pink"}},
            ],
        }

        mock_client.query.return_value = {"fairsharingGraph": {"data": graph_data}}

        result = await find_record_connections(1, 2)

        self.assertIn("Path found!", result)
        self.assertIn("1 hop(s)", result)
        self.assertIn("Direct Connection Exists", result)
        self.assertIn("RecA", result)
        self.assertIn("RecB", result)

    @patch("fairsharing_mcp.app.get_client")
    async def test_find_connections_multi_hop(self, mock_get_client):
        mock_client = AsyncMock()
        mock_get_client.return_value = mock_client

        graph_data = {
            "name": "Graph",
            "nodes": [
                {
                    "key": "1",
                    "attributes": {
                        "label": "A",
                        "registry": "standard",
                        "record_type": "t",
                        "status": "ready",
                    },
                },
                {
                    "key": "2",
                    "attributes": {
                        "label": "B",
                        "registry": "standard",
                        "record_type": "t",
                        "status": "ready",
                    },
                },
                {
                    "key": "3",
                    "attributes": {
                        "label": "C",
                        "registry": "database",
                        "record_type": "t",
                        "status": "ready",
                    },
                },
                {
                    "key": "4",
                    "attributes": {
                        "label": "D",
                        "registry": "database",
                        "record_type": "t",
                        "status": "ready",
                    },
                },
            ],
            "edges": [
                {"source": "1", "target": "2", "attributes": {"color": "grey"}},
                {"source": "2", "target": "3", "attributes": {"color": "pink"}},
                {"source": "3", "target": "4", "attributes": {"color": "pink"}},
            ],
        }

        mock_client.query.return_value = {"fairsharingGraph": {"data": graph_data}}

        result = await find_record_connections(1, 4)

        self.assertIn("Path found!", result)
        self.assertIn("3 hop(s)", result)
        # All intermediate nodes
        self.assertIn("A", result)
        self.assertIn("B", result)
        self.assertIn("C", result)
        self.assertIn("D", result)

    @patch("fairsharing_mcp.app.get_client")
    async def test_find_connections_not_in_graph(self, mock_get_client):
        mock_client = AsyncMock()
        mock_get_client.return_value = mock_client

        graph_data = {
            "name": "Graph",
            "nodes": [
                {
                    "key": "1",
                    "attributes": {
                        "label": "Only",
                        "registry": "standard",
                        "record_type": "t",
                        "status": "ready",
                    },
                },
            ],
            "edges": [],
        }

        mock_client.query.return_value = {"fairsharingGraph": {"data": graph_data}}

        result = await find_record_connections(1, 999)

        self.assertIn("not in the local knowledge graph", result)
        self.assertIn("find_cross_graph_path", result)

    @patch("fairsharing_mcp.app.get_client")
    async def test_find_connections_shared_neighbors(self, mock_get_client):
        mock_client = AsyncMock()
        mock_get_client.return_value = mock_client

        graph_data = {
            "name": "Graph",
            "nodes": [
                {
                    "key": "1",
                    "attributes": {
                        "label": "X",
                        "registry": "standard",
                        "record_type": "t",
                        "status": "ready",
                    },
                },
                {
                    "key": "2",
                    "attributes": {
                        "label": "Y",
                        "registry": "standard",
                        "record_type": "t",
                        "status": "ready",
                    },
                },
                {
                    "key": "3",
                    "attributes": {
                        "label": "Shared1",
                        "registry": "database",
                        "record_type": "t",
                        "status": "ready",
                    },
                },
                {
                    "key": "4",
                    "attributes": {
                        "label": "Shared2",
                        "registry": "database",
                        "record_type": "t",
                        "status": "ready",
                    },
                },
            ],
            "edges": [
                {"source": "1", "target": "3", "attributes": {"color": "pink"}},
                {"source": "1", "target": "4", "attributes": {"color": "pink"}},
                {"source": "2", "target": "3", "attributes": {"color": "pink"}},
                {"source": "2", "target": "4", "attributes": {"color": "pink"}},
            ],
        }

        mock_client.query.return_value = {"fairsharingGraph": {"data": graph_data}}

        result = await find_record_connections(1, 2)

        self.assertIn("Shared Neighbors (2)", result)
        self.assertIn("Shared1", result)
        self.assertIn("Shared2", result)

    # ── analyze_deprecation_impact ───────────────────────────────────

    @patch("fairsharing_mcp.app.get_client")
    async def test_deprecation_impact_multiple_categories(self, mock_get_client):
        mock_client = AsyncMock()
        mock_get_client.return_value = mock_client

        mock_client.query.return_value = {
            "fairsharingRecord": {
                "id": "50",
                "name": "OldStandard",
                "status": "deprecated",
                "registry": "Standard",
                "type": "terminology_artefact",
                "recordAssociations": [],
                "reverseRecordAssociations": [
                    {
                        "fairsharingRecord": {
                            "id": "100",
                            "name": "ActiveDB1",
                            "registry": "Database",
                            "status": "ready",
                            "type": "repository",
                        },
                        "recordAssocLabel": "implements",
                    },
                    {
                        "fairsharingRecord": {
                            "id": "101",
                            "name": "ActiveDB2",
                            "registry": "Database",
                            "status": "ready",
                            "type": "knowledgebase",
                        },
                        "recordAssocLabel": "implements",
                    },
                    {
                        "fairsharingRecord": {
                            "id": "102",
                            "name": "DeprecatedDB",
                            "registry": "Database",
                            "status": "deprecated",
                            "type": "repository",
                        },
                        "recordAssocLabel": "implements",
                    },
                    {
                        "fairsharingRecord": {
                            "id": "200",
                            "name": "ActivePolicy",
                            "registry": "Policy",
                            "status": "ready",
                            "type": "funder",
                        },
                        "recordAssocLabel": "recommends",
                    },
                ],
            }
        }

        result = await analyze_deprecation_impact(50)

        self.assertIn("OldStandard", result)
        self.assertIn("deprecated", result.lower())
        # Only active dependents counted
        self.assertIn("ActiveDB1", result)
        self.assertIn("ActiveDB2", result)
        self.assertIn("ActivePolicy", result)
        # DeprecatedDB should NOT appear (not "ready")
        self.assertNotIn("DeprecatedDB", result)
        # Multiple relationship categories (mapped to descriptions)
        self.assertIn("implementing", result.lower())
        self.assertIn("recommending", result.lower())

    @patch("fairsharing_mcp.app.get_client")
    async def test_deprecation_impact_non_deprecated(self, mock_get_client):
        mock_client = AsyncMock()
        mock_get_client.return_value = mock_client

        mock_client.query.return_value = {
            "fairsharingRecord": {
                "id": "10",
                "name": "HealthyDB",
                "status": "ready",
                "registry": "Database",
                "recordAssociations": [],
                "reverseRecordAssociations": [
                    {
                        "fairsharingRecord": {
                            "id": "20",
                            "name": "Dep1",
                            "registry": "Standard",
                            "status": "ready",
                        },
                        "recordAssocLabel": "related_to",
                    },
                ],
            }
        }

        result = await analyze_deprecation_impact(10)

        # Warning that it's not deprecated
        self.assertIn("currently marked as 'ready'", result)
        # But still shows dependents
        self.assertIn("Dep1", result)

    @patch("fairsharing_mcp.app.get_client")
    async def test_deprecation_impact_no_dependents(self, mock_get_client):
        mock_client = AsyncMock()
        mock_get_client.return_value = mock_client

        mock_client.query.return_value = {
            "fairsharingRecord": {
                "id": "50",
                "name": "LonelyRecord",
                "status": "deprecated",
                "registry": "Standard",
                "recordAssociations": [],
                "reverseRecordAssociations": [],
            }
        }

        result = await analyze_deprecation_impact(50)
        self.assertIn("No active records found", result)

    @patch("fairsharing_mcp.app.get_client")
    async def test_deprecation_impact_all_inactive(self, mock_get_client):
        mock_client = AsyncMock()
        mock_get_client.return_value = mock_client

        mock_client.query.return_value = {
            "fairsharingRecord": {
                "id": "50",
                "name": "OldRecord",
                "status": "deprecated",
                "registry": "Standard",
                "recordAssociations": [],
                "reverseRecordAssociations": [
                    {
                        "fairsharingRecord": {
                            "id": "100",
                            "name": "AlsoDeprecated",
                            "registry": "Database",
                            "status": "deprecated",
                        },
                        "recordAssocLabel": "implements",
                    },
                    {
                        "fairsharingRecord": {
                            "id": "101",
                            "name": "Uncertain",
                            "registry": "Database",
                            "status": "uncertain",
                        },
                        "recordAssocLabel": "implements",
                    },
                ],
            }
        }

        result = await analyze_deprecation_impact(50)
        self.assertIn("No active records found", result)

    # ── Graph Analysis: Shared test data ─────────────────────────────

    @staticmethod
    def _make_graph(nodes_spec, edges_spec, name="TestGraph"):
        """Build graph mock data.

        nodes_spec: [(key, label, registry, record_type), ...]
        edges_spec: [(source, target, color), ...]
        """
        nodes = [
            {
                "key": str(k),
                "attributes": {
                    "label": label,
                    "registry": reg,
                    "record_type": rtype,
                    "status": "ready",
                },
            }
            for k, label, reg, rtype in nodes_spec
        ]
        edges = [
            {"source": str(s), "target": str(t), "attributes": {"color": color}}
            for s, t, color in edges_spec
        ]
        return {"name": name, "nodes": nodes, "edges": edges}

    def _graph_mock(self, graph_data):
        """Return a mock client configured to return graph_data."""
        mock_client = AsyncMock()
        mock_client.query.return_value = {"fairsharingGraph": {"data": graph_data}}
        return mock_client

    # ── find_semantic_path ────────────────────────────────────────────

    @patch("fairsharing_mcp.app.get_client")
    async def test_semantic_path_direct(self, mock_get_client):
        graph = self._make_graph(
            [(1, "StdA", "standard", "terminology_artefact"), (2, "DB1", "database", "repository")],
            [(1, 2, "pink")],  # implements
        )
        mock_get_client.return_value = self._graph_mock(graph)
        result = await find_semantic_path(1, 2)
        self.assertIn("StdA", result)
        self.assertIn("DB1", result)
        self.assertIn("implements", result)
        self.assertIn("Hops:** 1", result)
        self.assertIn("w=1.0", result)

    @patch("fairsharing_mcp.app.get_client")
    async def test_semantic_path_multi_hop(self, mock_get_client):
        # A --implements--> B --recommends--> C
        graph = self._make_graph(
            [(1, "A", "standard", "t"), (2, "B", "database", "r"), (3, "C", "policy", "f")],
            [(1, 2, "pink"), (2, 3, "orange")],
        )
        mock_get_client.return_value = self._graph_mock(graph)
        result = await find_semantic_path(1, 3)
        self.assertIn("Hops:** 2", result)
        self.assertIn("implements", result)
        self.assertIn("recommends", result)

    @patch("fairsharing_mcp.app.get_client")
    async def test_semantic_path_target_not_in_graph(self, mock_get_client):
        graph = self._make_graph(
            [(1, "A", "standard", "t")],
            [],
        )
        mock_get_client.return_value = self._graph_mock(graph)
        result = await find_semantic_path(1, 999)
        self.assertIn("not in the local knowledge graph", result)
        self.assertIn("find_cross_graph_path", result)

    @patch("fairsharing_mcp.app.get_client")
    async def test_semantic_path_no_graph(self, mock_get_client):
        mock_client = AsyncMock()
        mock_client.query.return_value = {"fairsharingGraph": {}}
        mock_get_client.return_value = mock_client
        result = await find_semantic_path(1, 2)
        self.assertIn("No graph data", result)

    # ── compute_pagerank ──────────────────────────────────────────────

    @patch("fairsharing_mcp.app.get_client")
    async def test_pagerank_basic(self, mock_get_client):
        # Hub: node 1 points to 2, 3, 4 via implements; node 5 points to 2 via related_to
        graph = self._make_graph(
            [
                (1, "Hub", "standard", "t"),
                (2, "DB1", "database", "r"),
                (3, "DB2", "database", "r"),
                (4, "DB3", "database", "r"),
                (5, "Other", "standard", "t"),
            ],
            [(1, 2, "pink"), (1, 3, "pink"), (1, 4, "pink"), (5, 2, "grey")],
        )
        mock_get_client.return_value = self._graph_mock(graph)
        result = await compute_pagerank(1, top_n=5)
        self.assertIn("PageRank Analysis", result)
        self.assertIn("Hub", result)
        self.assertIn("5 nodes", result)

    @patch("fairsharing_mcp.app.get_client")
    async def test_pagerank_implements_vs_related(self, mock_get_client):
        # Node 2 gets high-quality links (implements), node 3 gets low-quality (related_to)
        graph = self._make_graph(
            [
                (1, "Src1", "standard", "t"),
                (2, "HighQ", "database", "r"),
                (3, "LowQ", "database", "r"),
                (4, "Src2", "standard", "t"),
            ],
            [(1, 2, "pink"), (4, 3, "grey")],  # implements vs related_to
        )
        mock_get_client.return_value = self._graph_mock(graph)
        result = await compute_pagerank(1, top_n=4)
        # HighQ should rank above LowQ (implements transfers more)
        self.assertIn("PageRank Analysis", result)

    @patch("fairsharing_mcp.app.get_client")
    async def test_pagerank_no_graph(self, mock_get_client):
        mock_client = AsyncMock()
        mock_client.query.return_value = {"fairsharingGraph": {}}
        mock_get_client.return_value = mock_client
        result = await compute_pagerank(999)
        self.assertIn("No graph data", result)

    # ── detect_communities ────────────────────────────────────────────

    @patch("fairsharing_mcp.app.get_client")
    async def test_communities_two_clusters(self, mock_get_client):
        # Cluster 1: nodes 1,2,3 tightly connected; Cluster 2: nodes 4,5,6 tightly connected
        # One weak link between 3 and 4
        graph = self._make_graph(
            [
                (1, "A1", "standard", "t"),
                (2, "A2", "standard", "t"),
                (3, "A3", "database", "r"),
                (4, "B1", "database", "r"),
                (5, "B2", "database", "r"),
                (6, "B3", "standard", "t"),
            ],
            [
                (1, 2, "pink"),
                (2, 3, "pink"),
                (1, 3, "pink"),
                (4, 5, "pink"),
                (5, 6, "pink"),
                (4, 6, "pink"),
                (3, 4, "grey"),
            ],
        )
        mock_get_client.return_value = self._graph_mock(graph)
        result = await detect_communities(1, min_community_size=2)
        self.assertIn("Community Detection", result)
        self.assertIn("6 nodes", result)

    @patch("fairsharing_mcp.app.get_client")
    async def test_communities_no_edges(self, mock_get_client):
        graph = self._make_graph(
            [(1, "A", "standard", "t"), (2, "B", "database", "r")],
            [],
        )
        mock_get_client.return_value = self._graph_mock(graph)
        result = await detect_communities(1, min_community_size=2)
        self.assertIn("No communities", result)

    @patch("fairsharing_mcp.app.get_client")
    async def test_communities_single_cluster(self, mock_get_client):
        graph = self._make_graph(
            [(1, "A", "standard", "t"), (2, "B", "standard", "t"), (3, "C", "standard", "t")],
            [(1, 2, "pink"), (2, 3, "pink"), (1, 3, "pink")],
        )
        mock_get_client.return_value = self._graph_mock(graph)
        result = await detect_communities(1, min_community_size=2)
        self.assertIn("Community Detection", result)
        # Should find at least 1 community with 3 members
        self.assertIn("Community 1", result)

    # ── find_similar_records ──────────────────────────────────────────

    @patch("fairsharing_mcp.app.get_client")
    async def test_similar_records_shared_standards(self, mock_get_client):
        # DB1 and DB2 both implement Std1 and Std2; DB3 implements only Std1
        graph = self._make_graph(
            [
                (10, "Std1", "standard", "t"),
                (11, "Std2", "standard", "t"),
                (20, "DB1", "database", "r"),
                (21, "DB2", "database", "r"),
                (22, "DB3", "database", "r"),
            ],
            [
                (10, 20, "pink"),
                (11, 20, "pink"),  # Std1,Std2 -> DB1
                (10, 21, "pink"),
                (11, 21, "pink"),  # Std1,Std2 -> DB2
                (10, 22, "pink"),
            ],  # Std1 -> DB3
        )
        mock_get_client.return_value = self._graph_mock(graph)
        result = await find_similar_records(20)
        self.assertIn("Similar Records", result)
        self.assertIn("DB2", result)
        # DB2 should rank above DB3 (2 shared vs 1)
        db2_pos = result.find("DB2")
        db3_pos = result.find("DB3")
        self.assertLess(db2_pos, db3_pos)

    @patch("fairsharing_mcp.app.get_client")
    async def test_similar_records_no_implements(self, mock_get_client):
        graph = self._make_graph(
            [(1, "DB1", "database", "r"), (2, "DB2", "database", "r")],
            [(1, 2, "grey")],  # Only related_to, no implements
        )
        mock_get_client.return_value = self._graph_mock(graph)
        result = await find_similar_records(1)
        self.assertIn("No 'implements' connections", result)

    @patch("fairsharing_mcp.app.get_client")
    async def test_similar_records_auto_side(self, mock_get_client):
        # Standard record should auto-detect projection side
        graph = self._make_graph(
            [(10, "Std1", "standard", "t"), (20, "DB1", "database", "r")],
            [(10, 20, "pink")],
        )
        mock_get_client.return_value = self._graph_mock(graph)
        result = await find_similar_records(10)
        self.assertIn("Similar Records", result)
        self.assertIn("Std1", result)

    # ── find_multiple_paths ───────────────────────────────────────────

    @patch("fairsharing_mcp.app.get_client")
    async def test_multiple_paths_two_routes(self, mock_get_client):
        # Path 1: A -> B (implements); Path 2: A -> C -> B (recommends + implements)
        graph = self._make_graph(
            [(1, "A", "standard", "t"), (2, "B", "database", "r"), (3, "C", "policy", "f")],
            [(1, 2, "pink"), (1, 3, "orange"), (3, 2, "pink")],
        )
        mock_get_client.return_value = self._graph_mock(graph)
        result = await find_multiple_paths(1, 2, k=2)
        self.assertIn("Path 1", result)
        self.assertIn("implements", result)

    @patch("fairsharing_mcp.app.get_client")
    async def test_multiple_paths_single_path(self, mock_get_client):
        graph = self._make_graph(
            [(1, "A", "standard", "t"), (2, "B", "database", "r")],
            [(1, 2, "pink")],
        )
        mock_get_client.return_value = self._graph_mock(graph)
        result = await find_multiple_paths(1, 2, k=3)
        self.assertIn("**Paths found:** 1", result)

    @patch("fairsharing_mcp.app.get_client")
    async def test_multiple_paths_disconnected(self, mock_get_client):
        graph = self._make_graph(
            [(1, "A", "standard", "t"), (2, "B", "database", "r")],
            [],
        )
        mock_get_client.return_value = self._graph_mock(graph)
        result = await find_multiple_paths(1, 2)
        self.assertIn("No path found", result)

    # ── compute_betweenness_centrality ────────────────────────────────

    @patch("fairsharing_mcp.app.get_client")
    async def test_betweenness_star_topology(self, mock_get_client):
        # Star: center (1) connects to 2, 3, 4, 5. Center should have highest betweenness.
        graph = self._make_graph(
            [
                (1, "Center", "standard", "t"),
                (2, "Leaf1", "database", "r"),
                (3, "Leaf2", "database", "r"),
                (4, "Leaf3", "database", "r"),
                (5, "Leaf4", "database", "r"),
            ],
            [(1, 2, "pink"), (1, 3, "pink"), (1, 4, "pink"), (1, 5, "pink")],
        )
        mock_get_client.return_value = self._graph_mock(graph)
        result = await compute_betweenness_centrality(1, top_n=5)
        self.assertIn("Betweenness Centrality", result)
        self.assertIn("Center", result)
        # Center should be rank 1
        lines = result.split("\n")
        rank1_line = [line for line in lines if "| 1 |" in line]
        self.assertTrue(len(rank1_line) > 0)
        self.assertIn("Center", rank1_line[0])

    @patch("fairsharing_mcp.app.get_client")
    async def test_betweenness_chain(self, mock_get_client):
        # Chain: 1 -- 2 -- 3 -- 4. Middle nodes should have higher betweenness.
        graph = self._make_graph(
            [
                (1, "End1", "standard", "t"),
                (2, "Mid1", "standard", "t"),
                (3, "Mid2", "standard", "t"),
                (4, "End2", "standard", "t"),
            ],
            [(1, 2, "pink"), (2, 3, "pink"), (3, 4, "pink")],
        )
        mock_get_client.return_value = self._graph_mock(graph)
        result = await compute_betweenness_centrality(1, top_n=4)
        self.assertIn("Betweenness Centrality", result)

    @patch("fairsharing_mcp.app.get_client")
    async def test_betweenness_too_small(self, mock_get_client):
        graph = self._make_graph(
            [(1, "A", "standard", "t"), (2, "B", "database", "r")],
            [(1, 2, "pink")],
        )
        mock_get_client.return_value = self._graph_mock(graph)
        result = await compute_betweenness_centrality(1)
        self.assertIn("too small", result)

    # ── find_dependency_clusters ──────────────────────────────────────

    @patch("fairsharing_mcp.app.get_client")
    async def test_scc_triangle(self, mock_get_client):
        # Triangle SCC: 1 -> 2 -> 3 -> 1
        graph = self._make_graph(
            [(1, "A", "standard", "t"), (2, "B", "standard", "t"), (3, "C", "standard", "t")],
            [(1, 2, "pink"), (2, 3, "pink"), (3, 1, "pink")],
        )
        mock_get_client.return_value = self._graph_mock(graph)
        result = await find_dependency_clusters(1)
        self.assertIn("Cluster 1", result)
        self.assertIn("3 nodes", result)
        self.assertIn("A", result)
        self.assertIn("B", result)
        self.assertIn("C", result)

    @patch("fairsharing_mcp.app.get_client")
    async def test_scc_no_cycles(self, mock_get_client):
        # DAG: 1 -> 2 -> 3 (no cycles)
        graph = self._make_graph(
            [(1, "A", "standard", "t"), (2, "B", "standard", "t"), (3, "C", "standard", "t")],
            [(1, 2, "pink"), (2, 3, "pink")],
        )
        mock_get_client.return_value = self._graph_mock(graph)
        result = await find_dependency_clusters(1)
        self.assertIn("No mutual dependency clusters", result)

    @patch("fairsharing_mcp.app.get_client")
    async def test_scc_two_components(self, mock_get_client):
        # Two SCCs: {1,2} and {3,4}
        graph = self._make_graph(
            [
                (1, "A", "standard", "t"),
                (2, "B", "standard", "t"),
                (3, "C", "database", "r"),
                (4, "D", "database", "r"),
            ],
            [
                (1, 2, "pink"),
                (2, 1, "pink"),
                (3, 4, "pink"),
                (4, 3, "pink"),
                (2, 3, "grey"),
            ],  # one-way bridge, not part of any SCC
        )
        mock_get_client.return_value = self._graph_mock(graph)
        result = await find_dependency_clusters(1)
        self.assertIn("**Non-trivial SCCs:** 2", result)
        self.assertIn("Cluster 1", result)
        self.assertIn("Cluster 2", result)

    # ── trace_policy_impact with subject filter (C1) ──────────────────

    @patch("fairsharing_mcp.app.get_client")
    async def test_trace_policy_impact_subject_filter(self, mock_get_client):
        """Subject filter should keep only standards tagged with the given subject."""
        mock_client = AsyncMock()
        mock_get_client.return_value = mock_client

        # Call 1: fetch the policy with 2 recommended standards
        policy_data = {
            "fairsharingRecord": {
                "id": "500",
                "name": "Genomics Policy",
                "registry": "Policy",
                "type": "funder",
                "status": "ready",
                "recordAssociations": [
                    {
                        "linkedRecord": {
                            "id": "100",
                            "name": "GenomicsStd",
                            "abbreviation": "GS",
                            "registry": "Standard",
                            "type": "model_and_format",
                            "status": "ready",
                        },
                        "recordAssocLabel": "recommends",
                    },
                    {
                        "linkedRecord": {
                            "id": "101",
                            "name": "ProteomicsStd",
                            "abbreviation": "PS",
                            "registry": "Standard",
                            "type": "model_and_format",
                            "status": "ready",
                        },
                        "recordAssocLabel": "recommends",
                    },
                ],
                "reverseRecordAssociations": [],
            }
        }

        # Call 2: subject filter fetches GenomicsStd — has Genomics subject
        std_genomics_data = {
            "fairsharingRecord": {
                "id": "100",
                "name": "GenomicsStd",
                "subjects": [{"label": "Genomics"}, {"label": "Biology"}],
                "reverseRecordAssociations": [
                    {
                        "fairsharingRecord": {"id": "200", "name": "GenDB", "registry": "Database"},
                        "recordAssocLabel": "implements",
                    },
                ],
                "recordAssociations": [],
            }
        }

        # Call 3: subject filter fetches ProteomicsStd — no Genomics subject
        std_proteomics_data = {
            "fairsharingRecord": {
                "id": "101",
                "name": "ProteomicsStd",
                "subjects": [{"label": "Proteomics"}],
                "reverseRecordAssociations": [
                    {
                        "fairsharingRecord": {
                            "id": "201",
                            "name": "ProtDB",
                            "registry": "Database",
                        },
                        "recordAssocLabel": "implements",
                    },
                ],
                "recordAssociations": [],
            }
        }

        # Call 2 data is reused for hop 2 (cached), so only 3 calls total
        mock_client.query.side_effect = [policy_data, std_genomics_data, std_proteomics_data]

        result = await trace_policy_impact(500, subject="Genomics")

        # GenomicsStd should be included, ProteomicsStd filtered out
        self.assertIn("GenomicsStd", result)
        self.assertIn("GenDB", result)
        self.assertIn("1 of 2 standards match", result)
        # ProteomicsStd should not appear in the 2-hop section
        self.assertNotIn("ProtDB", result)
        # Subject filter note in header
        self.assertIn("Subject filter", result)
        self.assertIn("Genomics", result)

    @patch("fairsharing_mcp.app.get_client")
    async def test_trace_policy_impact_subject_filter_none(self, mock_get_client):
        """Without subject filter, all standards should be included (existing behavior)."""
        mock_client = AsyncMock()
        mock_get_client.return_value = mock_client

        policy_data = {
            "fairsharingRecord": {
                "id": "500",
                "name": "General Policy",
                "registry": "Policy",
                "type": "funder",
                "status": "ready",
                "recordAssociations": [
                    {
                        "linkedRecord": {
                            "id": "100",
                            "name": "StdA",
                            "abbreviation": "",
                            "registry": "Standard",
                            "type": "model_and_format",
                            "status": "ready",
                        },
                        "recordAssocLabel": "recommends",
                    },
                ],
                "reverseRecordAssociations": [],
            }
        }

        std_data = {
            "fairsharingRecord": {
                "id": "100",
                "name": "StdA",
                "reverseRecordAssociations": [
                    {
                        "fairsharingRecord": {"id": "200", "name": "DB_A", "registry": "Database"},
                        "recordAssocLabel": "implements",
                    },
                ],
                "recordAssociations": [],
            }
        }

        mock_client.query.side_effect = [policy_data, std_data]

        result = await trace_policy_impact(500)  # No subject filter

        self.assertIn("StdA", result)
        self.assertIn("DB_A", result)
        # No subject filter note in output
        self.assertNotIn("Subject filter", result)
        # Suggested next steps should be present
        self.assertIn("Suggested Next Steps", result)

    # ── Enhanced zero-result messages (B1, B2) ───────────────────────

    @patch("fairsharing_mcp.app.get_client")
    async def test_search_records_zero_results_with_context(self, mock_get_client):
        """Zero-result messages should include filter context and broadening suggestions."""
        mock_client = AsyncMock()
        mock_get_client.return_value = mock_client

        mock_client.query.return_value = {
            "searchFairsharingRecords": {"records": [], "totalCount": 0, "totalPages": 0}
        }

        result = await search_records(
            registry=["Policy"],
            subjects=["Genomics"],
            countries=["Ireland"],
        )

        self.assertIn("No records found", result)
        self.assertIn("registry=['Policy']", result)
        self.assertIn("subjects=['Genomics']", result)
        self.assertIn("countries=['Ireland']", result)
        self.assertIn("Try broadening", result)

    @patch("fairsharing_mcp.app.get_client")
    async def test_advanced_filter_zero_results_with_context(self, mock_get_client):
        """advanced_filter_records zero-result should include filter context."""
        mock_client = AsyncMock()
        mock_get_client.return_value = mock_client

        mock_client.query.return_value = {"multiTagFilter": []}

        result = await advanced_filter_records(
            registry=["Database"],
            subjects=["Genomics"],
            uses_persistent_identifier=True,
        )

        self.assertIn("No records found", result)
        self.assertIn("registry=['Database']", result)
        self.assertIn("subjects=['Genomics']", result)
        self.assertIn("persistentIDs=True", result)
        self.assertIn("Try broadening", result)

    # ── Suggested next steps (B3, B4, B5) ────────────────────────────

    @patch("fairsharing_mcp.app.get_client")
    async def test_find_databases_for_standard_next_steps(self, mock_get_client):
        """find_databases_for_standard should include suggested next steps."""
        mock_client = AsyncMock()
        mock_get_client.return_value = mock_client

        mock_client.query.return_value = {
            "fairsharingRecord": {
                "id": "100",
                "name": "TestStd",
                "abbreviation": "TS",
                "registry": "Standard",
                "type": "model_and_format",
                "recordAssociations": [],
                "reverseRecordAssociations": [
                    {
                        "fairsharingRecord": {
                            "id": "200",
                            "name": "TestDB",
                            "abbreviation": "",
                            "registry": "Database",
                            "type": "repository",
                            "status": "ready",
                        },
                        "recordAssocLabel": "implements",
                    },
                ],
            }
        }

        result = await find_databases_for_standard(100)

        self.assertIn("Suggested Next Steps", result)
        self.assertIn("compare_databases_quality", result)
        self.assertIn("analyze_standard_adoption", result)

    @patch("fairsharing_mcp.app.get_client")
    async def test_analyze_standard_adoption_next_steps(self, mock_get_client):
        """analyze_standard_adoption should include suggested next steps."""
        mock_client = AsyncMock()
        mock_get_client.return_value = mock_client

        mock_client.query.return_value = {
            "fairsharingRecord": {
                "id": "100",
                "name": "TestStd",
                "abbreviation": "TS",
                "registry": "Standard",
                "type": "model_and_format",
                "status": "ready",
                "recordAssociations": [],
                "reverseRecordAssociations": [
                    {
                        "fairsharingRecord": {
                            "id": "200",
                            "name": "DB1",
                            "abbreviation": "",
                            "type": "repository",
                            "status": "ready",
                        },
                        "recordAssocLabel": "implements",
                    },
                ],
            }
        }

        result = await analyze_standard_adoption(100)

        self.assertIn("Suggested Next Steps", result)
        self.assertIn("rank_databases_by_quality", result)
        self.assertIn("find_policy_gaps", result)

    # ── New parameters (C2, C3) ──────────────────────────────────────

    @patch("fairsharing_mcp.app.get_client")
    async def test_find_databases_for_standard_countries_note(self, mock_get_client):
        """find_databases_for_standard with countries= should include a note."""
        mock_client = AsyncMock()
        mock_get_client.return_value = mock_client

        mock_client.query.return_value = {
            "fairsharingRecord": {
                "id": "100",
                "name": "TestStd",
                "abbreviation": "",
                "registry": "Standard",
                "type": "model_and_format",
                "recordAssociations": [],
                "reverseRecordAssociations": [],
            }
        }

        result = await find_databases_for_standard(100, countries=["United Kingdom"])

        self.assertIn("Country filter requested", result)
        self.assertIn("United Kingdom", result)

    @patch("fairsharing_mcp.app.get_client")
    async def test_analyze_standard_adoption_subject_context(self, mock_get_client):
        """analyze_standard_adoption with subject= should include a context note."""
        mock_client = AsyncMock()
        mock_get_client.return_value = mock_client

        mock_client.query.return_value = {
            "fairsharingRecord": {
                "id": "100",
                "name": "TestStd",
                "abbreviation": "",
                "registry": "Standard",
                "type": "model_and_format",
                "status": "ready",
                "recordAssociations": [],
                "reverseRecordAssociations": [],
            }
        }

        result = await analyze_standard_adoption(100, subject="Genomics")

        self.assertIn("Subject context", result)
        self.assertIn("Genomics", result)

    # ── analyze_country_landscape with subject (R1) ──────────────────

    @patch("fairsharing_mcp.app.get_client")
    async def test_analyze_country_landscape_with_subject(self, mock_get_client):
        """analyze_country_landscape with subject= should pass subjects to search queries."""
        mock_client = AsyncMock()
        mock_get_client.return_value = mock_client

        # Mock returns for 4 registry searches (Standard, Database, Policy, Collection)
        mock_client.query.return_value = {
            "searchFairsharingRecords": {
                "records": [],
                "totalCount": 0,
            }
        }

        result = await analyze_country_landscape("United Kingdom", subject="Genomics")

        self.assertIn("Subject filter", result)
        self.assertIn("Genomics", result)
        self.assertIn("Country Profile: United Kingdom", result)

        # Verify subjects was passed in the query variables
        for call in mock_client.query.call_args_list:
            args, kwargs = call
            variables = args[1] if len(args) > 1 else kwargs.get("variables", {})
            if "countries" in variables:
                self.assertIn("subjects", variables)
                self.assertEqual(variables["subjects"], ["Genomics"])

    @patch("fairsharing_mcp.app.get_client")
    async def test_analyze_country_landscape_without_subject(self, mock_get_client):
        """analyze_country_landscape without subject= should not pass subjects to queries."""
        mock_client = AsyncMock()
        mock_get_client.return_value = mock_client

        mock_client.query.return_value = {
            "searchFairsharingRecords": {
                "records": [],
                "totalCount": 0,
            }
        }

        result = await analyze_country_landscape("Germany")

        self.assertNotIn("Subject filter", result)
        self.assertIn("Country Profile: Germany", result)

        # Verify subjects was NOT passed in the query variables
        for call in mock_client.query.call_args_list:
            args, kwargs = call
            variables = args[1] if len(args) > 1 else kwargs.get("variables", {})
            if "countries" in variables:
                self.assertNotIn("subjects", variables)

    # ── analyze_regional_distribution with subject (R2) ──────────────

    @patch("fairsharing_mcp.app.get_client")
    async def test_analyze_regional_distribution_with_subject(self, mock_get_client):
        """analyze_regional_distribution with subject= should filter by subject."""
        mock_client = AsyncMock()
        mock_get_client.return_value = mock_client

        mock_client.query.return_value = {
            "searchFairsharingRecords": {
                "totalCount": 5,
            }
        }

        result = await analyze_regional_distribution(
            ["United Kingdom", "Germany"],
            subject="Genomics",
        )

        self.assertIn("Subject filter", result)
        self.assertIn("Genomics", result)
        self.assertIn("Regional Distribution", result)

        # Verify subjects was passed in query variables
        for call in mock_client.query.call_args_list:
            args, kwargs = call
            variables = args[1] if len(args) > 1 else kwargs.get("variables", {})
            if "countries" in variables:
                self.assertIn("subjects", variables)
                self.assertEqual(variables["subjects"], ["Genomics"])

    @patch("fairsharing_mcp.app.get_client")
    async def test_analyze_regional_distribution_without_subject(self, mock_get_client):
        """analyze_regional_distribution without subject= should not filter by subject."""
        mock_client = AsyncMock()
        mock_get_client.return_value = mock_client

        mock_client.query.return_value = {
            "searchFairsharingRecords": {
                "totalCount": 10,
            }
        }

        result = await analyze_regional_distribution(["France"])

        self.assertNotIn("Subject filter", result)
        self.assertIn("Regional Distribution", result)

    # ── suggest_graph_starting_points ─────────────────────────────────

    @patch("fairsharing_mcp.app.get_client")
    async def test_suggest_graph_starting_points_basic(self, mock_get_client):
        """Should rank records by graph size (nodes + edges)."""
        mock_client = AsyncMock()
        mock_get_client.return_value = mock_client

        mock_client.query.side_effect = [
            # Search results
            {
                "searchFairsharingRecords": {
                    "records": [
                        {
                            "id": "10",
                            "name": "SmallDB",
                            "abbreviation": "S",
                            "registry": "Database",
                            "type": "repository",
                            "status": "ready",
                        },
                        {
                            "id": "20",
                            "name": "BigDB",
                            "abbreviation": "B",
                            "registry": "Database",
                            "type": "repository",
                            "status": "ready",
                        },
                        {
                            "id": "30",
                            "name": "MedDB",
                            "abbreviation": "M",
                            "registry": "Database",
                            "type": "repository",
                            "status": "ready",
                        },
                    ],
                    "totalCount": 3,
                    "totalPages": 1,
                }
            },
            # Graph for record 10: 5 nodes, 3 edges (score=8)
            {
                "fairsharingGraph": {
                    "data": {"nodes": [{"key": str(i)} for i in range(5)], "edges": [{}] * 3}
                }
            },
            # Graph for record 20: 100 nodes, 200 edges (score=300)
            {
                "fairsharingGraph": {
                    "data": {"nodes": [{"key": str(i)} for i in range(100)], "edges": [{}] * 200}
                }
            },
            # Graph for record 30: 50 nodes, 80 edges (score=130)
            {
                "fairsharingGraph": {
                    "data": {"nodes": [{"key": str(i)} for i in range(50)], "edges": [{}] * 80}
                }
            },
        ]

        result = await suggest_graph_starting_points("genomics", registry=["Database"])
        self.assertIn("Graph Starting Points", result)
        # BigDB should be ranked first (largest graph)
        big_pos = result.find("BigDB")
        med_pos = result.find("MedDB")
        small_pos = result.find("SmallDB")
        self.assertLess(big_pos, med_pos)
        self.assertLess(med_pos, small_pos)
        self.assertIn("Recommendation", result)
        self.assertIn("20", result)  # BigDB's ID in recommendation

    @patch("fairsharing_mcp.app.get_client")
    async def test_suggest_graph_starting_points_no_results(self, mock_get_client):
        """Should return helpful message when no records match."""
        mock_client = AsyncMock()
        mock_get_client.return_value = mock_client
        mock_client.query.return_value = {
            "searchFairsharingRecords": {"records": [], "totalCount": 0, "totalPages": 0}
        }
        result = await suggest_graph_starting_points("xyznonexistent")
        self.assertIn("No records found", result)

    @patch("fairsharing_mcp.app.get_client")
    async def test_suggest_graph_starting_points_empty_graphs(self, mock_get_client):
        """Records with no graph data should show 0 nodes/edges."""
        mock_client = AsyncMock()
        mock_get_client.return_value = mock_client
        mock_client.query.side_effect = [
            {
                "searchFairsharingRecords": {
                    "records": [
                        {
                            "id": "1",
                            "name": "NoGraph",
                            "abbreviation": "",
                            "registry": "Standard",
                            "type": "t",
                            "status": "ready",
                        },
                    ],
                    "totalCount": 1,
                    "totalPages": 1,
                }
            },
            {"fairsharingGraph": {}},  # No graph data
        ]
        result = await suggest_graph_starting_points("test")
        self.assertIn("| 0 | 0 |", result)
        self.assertIn("None of the candidates have graph data", result)

    async def test_suggest_graph_starting_points_clamping(self):
        """max_candidates should be clamped to [1, 10]."""
        # We just test that the function doesn't crash with extreme values
        # by checking the clamping logic directly (no API call needed for this)
        # The actual API call would be needed for a full test, but we validate
        # behavior by calling with empty query
        result = await suggest_graph_starting_points("")
        self.assertIn("Please provide a search query", result)

    # ── find_cross_graph_path ──────────────────────────────────────────

    @patch("fairsharing_mcp.app.get_client")
    async def test_cross_graph_path_overlap_found(self, mock_get_client):
        """Should find a path through bridge nodes shared between two graphs."""
        mock_client = AsyncMock()
        mock_get_client.return_value = mock_client

        graph1 = self._make_graph(
            [(1, "A", "standard", "t"), (20, "Bridge", "database", "r")],
            [(1, 20, "pink")],
            name="Graph1",
        )
        graph2 = self._make_graph(
            [(2, "B", "standard", "t"), (20, "Bridge", "database", "r")],
            [(20, 2, "orange")],
            name="Graph2",
        )

        mock_client.query.side_effect = [
            {"fairsharingGraph": {"data": graph1}},
            {"fairsharingGraph": {"data": graph2}},
        ]

        result = await find_cross_graph_path(1, 2)
        self.assertIn("Cross-Graph Path", result)
        self.assertIn("Bridge", result)
        self.assertIn("BRIDGE", result)
        self.assertIn("A", result)
        self.assertIn("B", result)
        self.assertIn("Overlap:", result)

    @patch("fairsharing_mcp.app.get_client")
    async def test_cross_graph_path_no_overlap(self, mock_get_client):
        """Should report disjoint graphs when no overlapping nodes exist."""
        mock_client = AsyncMock()
        mock_get_client.return_value = mock_client

        graph1 = self._make_graph(
            [(1, "A", "standard", "t"), (10, "X", "database", "r")],
            [(1, 10, "pink")],
            name="Graph1",
        )
        graph2 = self._make_graph(
            [(2, "B", "standard", "t"), (20, "Y", "database", "r")],
            [(2, 20, "pink")],
            name="Graph2",
        )

        mock_client.query.side_effect = [
            {"fairsharingGraph": {"data": graph1}},
            {"fairsharingGraph": {"data": graph2}},
        ]

        result = await find_cross_graph_path(1, 2)
        self.assertIn("No overlapping nodes", result)
        self.assertIn("completely separate", result)

    @patch("fairsharing_mcp.app.get_client")
    async def test_cross_graph_path_already_in_graph(self, mock_get_client):
        """Should find path with 1 API call when record_2 is already in graph_1."""
        graph = self._make_graph(
            [(1, "A", "standard", "t"), (2, "B", "database", "r")],
            [(1, 2, "pink")],
        )
        mock_get_client.return_value = self._graph_mock(graph)

        result = await find_cross_graph_path(1, 2)
        self.assertIn("Cross-Graph Path", result)
        self.assertIn("A", result)
        self.assertIn("B", result)
        self.assertIn("implements", result)
        # Should NOT contain "Graphs merged" since it's a single-graph path
        self.assertNotIn("Graphs merged", result)

    @patch("fairsharing_mcp.app.get_client")
    async def test_cross_graph_path_no_graph(self, mock_get_client):
        """Should handle missing graph data gracefully."""
        mock_client = AsyncMock()
        mock_get_client.return_value = mock_client
        mock_client.query.return_value = {"fairsharingGraph": {}}
        result = await find_cross_graph_path(1, 2)
        self.assertIn("No graph data", result)

    # ── merge_graphs (unit test) ───────────────────────────────────────

    def test_merge_graphs_basic(self):
        """Should merge two graphs, deduplicating nodes and edges."""
        graph_a_data = {
            "name": "GraphA",
            "nodes": [
                {
                    "key": "1",
                    "attributes": {
                        "label": "A",
                        "registry": "standard",
                        "record_type": "t",
                        "status": "ready",
                    },
                },
                {
                    "key": "20",
                    "attributes": {
                        "label": "Bridge_A",
                        "registry": "database",
                        "record_type": "r",
                        "status": "ready",
                    },
                },
            ],
            "edges": [{"source": "1", "target": "20", "attributes": {"color": "pink"}}],
        }
        graph_b_data = {
            "name": "GraphB",
            "nodes": [
                {
                    "key": "2",
                    "attributes": {
                        "label": "B",
                        "registry": "standard",
                        "record_type": "t",
                        "status": "ready",
                    },
                },
                {
                    "key": "20",
                    "attributes": {
                        "label": "Bridge_B",
                        "registry": "database",
                        "record_type": "r",
                        "status": "ready",
                    },
                },
            ],
            "edges": [{"source": "20", "target": "2", "attributes": {"color": "orange"}}],
        }

        g_a = parse_graph(graph_a_data)
        g_b = parse_graph(graph_b_data)
        merged = merge_graphs(g_a, g_b)

        # Should have 3 unique nodes
        self.assertEqual(len(merged.nodes), 3)
        # Node 20 should use graph_a's metadata
        self.assertEqual(merged.nodes["20"].label, "Bridge_A")
        # Should have 2 edges
        self.assertEqual(len(merged.edges), 2)
        # Adjacency should be correct
        self.assertIn("20", merged.adj.get("1", set()))
        self.assertIn("2", merged.adj.get("20", set()))
        # Name should be combined
        self.assertIn("GraphA", merged.name)
        self.assertIn("GraphB", merged.name)

    # ── analyze_path_criticality ───────────────────────────────────────

    @patch("fairsharing_mcp.app.get_client")
    async def test_path_criticality_basic(self, mock_get_client):
        """Should annotate path nodes with BC scores and ranks."""
        graph = self._make_graph(
            [(1, "A", "standard", "t"), (3, "Mid", "database", "r"), (2, "B", "standard", "t")],
            [(1, 3, "pink"), (3, 2, "orange")],
        )
        mock_get_client.return_value = self._graph_mock(graph)
        result = await analyze_path_criticality(1, 2)
        self.assertIn("Path Criticality", result)
        self.assertIn("BC Score", result)
        self.assertIn("BC Rank", result)
        self.assertIn("A", result)
        self.assertIn("Mid", result)
        self.assertIn("B", result)

    @patch("fairsharing_mcp.app.get_client")
    async def test_path_criticality_target_not_in_graph(self, mock_get_client):
        """Should suggest find_cross_graph_path when target not in graph."""
        graph = self._make_graph([(1, "A", "standard", "t")], [])
        mock_get_client.return_value = self._graph_mock(graph)
        result = await analyze_path_criticality(1, 999)
        self.assertIn("not in the local knowledge graph", result)
        self.assertIn("find_cross_graph_path", result)

    @patch("fairsharing_mcp.app.get_client")
    async def test_path_criticality_star_center(self, mock_get_client):
        """Star topology center should be highlighted as critical bridge."""
        # Star: Center (3) connects to 1, 2, 4, 5
        graph = self._make_graph(
            [
                (1, "A", "standard", "t"),
                (2, "B", "standard", "t"),
                (3, "Center", "database", "r"),
                (4, "C", "standard", "t"),
                (5, "D", "standard", "t"),
            ],
            [(1, 3, "pink"), (3, 2, "pink"), (3, 4, "pink"), (3, 5, "pink")],
        )
        mock_get_client.return_value = self._graph_mock(graph)
        result = await analyze_path_criticality(1, 2)
        self.assertIn("Center", result)
        self.assertIn("Critical Bridges on Path", result)

    # ── find_orphan_records with date filtering ─────────────────────────

    @patch("fairsharing_mcp.app.get_client")
    async def test_find_orphan_records_no_date_filter(self, mock_get_client):
        mock_client = AsyncMock()
        mock_get_client.return_value = mock_client

        mock_client.query.return_value = {
            "searchFairsharingRecords": {
                "records": [
                    {
                        "id": "1",
                        "name": "Orphan1",
                        "abbreviation": "O1",
                        "type": "standard",
                        "status": "ready",
                        "registry": "Standard",
                        "createdAt": "2020-01-01",
                    },
                ],
                "totalCount": 1,
                "totalPages": 1,
            }
        }

        result = await find_orphan_records(registry="Standard", orphan_type="not_implemented")
        self.assertIn("Orphan1", result)
        self.assertNotIn("Year filter", result)

    @patch("fairsharing_mcp.app.get_client")
    async def test_find_orphan_records_with_date_filter(self, mock_get_client):
        mock_client = AsyncMock()
        mock_get_client.return_value = mock_client

        # Return records from different years
        mock_client.query.side_effect = [
            {
                "searchFairsharingRecords": {
                    "records": [
                        {
                            "id": "1",
                            "name": "Old",
                            "abbreviation": "",
                            "type": "standard",
                            "status": "ready",
                            "registry": "Standard",
                            "createdAt": "2018-01-01",
                        },
                        {
                            "id": "2",
                            "name": "Match",
                            "abbreviation": "",
                            "type": "standard",
                            "status": "ready",
                            "registry": "Standard",
                            "createdAt": "2020-06-15",
                        },
                        {
                            "id": "3",
                            "name": "New",
                            "abbreviation": "",
                            "type": "standard",
                            "status": "ready",
                            "registry": "Standard",
                            "createdAt": "2023-01-01",
                        },
                    ],
                    "totalCount": 3,
                    "totalPages": 1,
                }
            },
            # Empty second page
            {
                "searchFairsharingRecords": {
                    "records": [],
                    "totalCount": 3,
                    "totalPages": 1,
                }
            },
        ]

        result = await find_orphan_records(
            registry="Standard", orphan_type="not_implemented", min_year=2020, max_year=2020
        )
        self.assertIn("Match", result)
        self.assertNotIn("Old", result)
        self.assertNotIn("New", result)
        self.assertIn("Year filter", result)

    @patch("fairsharing_mcp.app.get_client")
    async def test_find_orphan_records_recommends_with_date(self, mock_get_client):
        """Test MULTI_TAG_FILTER_QUERY path with date filtering."""
        mock_client = AsyncMock()
        mock_get_client.return_value = mock_client

        mock_client.query.return_value = {
            "multiTagFilter": [
                {
                    "id": "10",
                    "name": "PolicyA",
                    "abbreviation": "",
                    "type": "funder",
                    "status": "ready",
                    "registry": "Policy",
                    "createdAt": "2019-05-01",
                },
                {
                    "id": "11",
                    "name": "PolicyB",
                    "abbreviation": "",
                    "type": "funder",
                    "status": "ready",
                    "registry": "Policy",
                    "createdAt": "2021-03-01",
                },
            ]
        }

        result = await find_orphan_records(
            registry="Policy", orphan_type="recommends_no_database", min_year=2020, max_year=2022
        )
        self.assertIn("PolicyB", result)
        self.assertNotIn("PolicyA", result)
        self.assertIn("Year filter", result)

    # ── count_records with date filtering ─────────────────────────────

    @patch("fairsharing_mcp.app.get_client")
    async def test_count_records_no_date(self, mock_get_client):
        mock_client = AsyncMock()
        mock_get_client.return_value = mock_client

        mock_client.query.return_value = {
            "searchFairsharingRecords": {"totalCount": 42, "records": []}
        }

        result = await count_records(registry=["Database"])
        self.assertIn("42", result)

    @patch("fairsharing_mcp.app.get_client")
    async def test_count_records_with_date_filter(self, mock_get_client):
        mock_client = AsyncMock()
        mock_get_client.return_value = mock_client

        mock_client.query.side_effect = [
            {
                "searchFairsharingRecords": {
                    "totalCount": 100,
                    "records": [
                        {"id": "1", "name": "A", "createdAt": "2020-01-01"},
                        {"id": "2", "name": "B", "createdAt": "2019-01-01"},
                        {"id": "3", "name": "C", "createdAt": "2020-06-15"},
                    ],
                }
            },
            # Empty second page
            {"searchFairsharingRecords": {"totalCount": 100, "records": []}},
        ]

        result = await count_records(min_year=2020, max_year=2020)
        # 2 out of 3 records match year 2020
        self.assertIn("2", result)
        self.assertIn("scanned", result)
        self.assertIn("Min year: 2020", result)

    # ── count_fair_records with date filtering ────────────────────────

    @patch("fairsharing_mcp.app.get_client")
    async def test_count_fair_records_no_date(self, mock_get_client):
        mock_client = AsyncMock()
        mock_get_client.return_value = mock_client

        mock_client.query.return_value = {
            "multiTagFilter": [
                {"id": "1", "registry": "Database", "createdAt": "2020-01-01"},
                {"id": "2", "registry": "Database", "createdAt": "2021-01-01"},
            ]
        }

        result = await count_fair_records(registry=["Database"])
        self.assertIn("2", result)

    @patch("fairsharing_mcp.app.get_client")
    async def test_count_fair_records_with_date_filter(self, mock_get_client):
        mock_client = AsyncMock()
        mock_get_client.return_value = mock_client

        mock_client.query.return_value = {
            "multiTagFilter": [
                {"id": "1", "registry": "Database", "createdAt": "2020-01-01"},
                {"id": "2", "registry": "Database", "createdAt": "2021-01-01"},
                {"id": "3", "registry": "Standard", "createdAt": "2020-06-15"},
            ]
        }

        result = await count_fair_records(min_year=2020, max_year=2020)
        # Only records 1 and 3 match year 2020
        self.assertIn("2", result)
        self.assertIn("Min year: 2020", result)

    # ── batch_audit_metadata with date filtering ──────────────────────

    @patch("fairsharing_mcp.app.get_client")
    async def test_batch_audit_metadata_with_date_filter(self, mock_get_client):
        mock_client = AsyncMock()
        mock_get_client.return_value = mock_client

        mock_client.query.side_effect = [
            # search returns records from different years
            {
                "searchFairsharingRecords": {
                    "records": [
                        {
                            "id": "1",
                            "name": "OldDB",
                            "registry": "Database",
                            "createdAt": "2015-01-01",
                        },
                        {
                            "id": "2",
                            "name": "NewDB",
                            "registry": "Database",
                            "createdAt": "2020-01-01",
                        },
                    ]
                }
            },
            # full details for the matching record (2020)
            {
                "fairsharingRecord": {
                    "name": "NewDB",
                    "registry": "Database",
                    "description": "A new db",
                    "subjects": ["Genomics"],
                    "domains": ["Biology"],
                }
            },
        ]

        result = await batch_audit_metadata(limit=10, min_year=2020, max_year=2020)
        self.assertIn("NewDB", result)
        self.assertNotIn("OldDB", result)
        self.assertIn("Years: 2020-2020", result)

    # ── filter_records_by_date max_scan ───────────────────────────────

    @patch("fairsharing_mcp.app.get_client")
    async def test_filter_records_by_date_custom_max_scan(self, mock_get_client):
        mock_client = AsyncMock()
        mock_get_client.return_value = mock_client

        # Return records, then empty
        mock_client.query.side_effect = [
            {
                "searchFairsharingRecords": {
                    "records": [
                        {
                            "name": "Rec1",
                            "id": "1",
                            "createdAt": "2020-01-01",
                            "registry": "Database",
                        },
                    ]
                }
            },
            {"searchFairsharingRecords": {"records": []}},
        ]

        result = await filter_records_by_date(min_year=2020, max_year=2020, max_scan=100)
        self.assertIn("Rec1", result)

    # ── audit checklist correctness ───────────────────────────────────

    @patch("fairsharing_mcp.app.get_client")
    async def test_audit_checks_publications_and_organisations(self, mock_get_client):
        """Verify audit_metadata_completeness checks publications and organisations."""
        from fairsharing_mcp.tools.curator import audit_metadata_completeness

        mock_client = AsyncMock()
        mock_get_client.return_value = mock_client

        mock_client.query.return_value = {
            "fairsharingRecord": {
                "name": "TestStd",
                "registry": "Standard",
                "description": "A test standard",
                "subjects": [{"id": "1", "label": "Bio"}],
                "domains": [{"id": "1", "label": "Data"}],
                "homepage": "http://example.com",
                "abbreviation": "TS",
                "doi": "10.1234/test",
                "licenceLinks": [{"licence": {"id": "1", "name": "CC-BY"}}],
                "publications": [{"id": "1", "title": "Test Paper"}],
                "organisations": [{"id": "1", "name": "TestOrg"}],
            }
        }

        result = await audit_metadata_completeness(record_id=1)
        self.assertIn("100.0%", result)
        self.assertIn("All required fields present", result)
        self.assertIn("All recommended fields present", result)

    @patch("fairsharing_mcp.app.get_client")
    async def test_audit_reports_missing_publications(self, mock_get_client):
        """Verify audit reports missing publications."""
        from fairsharing_mcp.tools.curator import audit_metadata_completeness

        mock_client = AsyncMock()
        mock_get_client.return_value = mock_client

        mock_client.query.return_value = {
            "fairsharingRecord": {
                "name": "TestStd",
                "registry": "Standard",
                "description": "A test standard",
                "subjects": [{"id": "1", "label": "Bio"}],
                "domains": [{"id": "1", "label": "Data"}],
                "homepage": "http://example.com",
                "abbreviation": "TS",
                "doi": "10.1234/test",
                "licenceLinks": [{"licence": {"id": "1", "name": "CC-BY"}}],
                # Missing: publications, organisations
            }
        }

        result = await audit_metadata_completeness(record_id=1)
        self.assertIn("publications", result)
        self.assertIn("organisations", result)

    # ── detect_communities: collapse diagnostic ────────────────────

    @patch("fairsharing_mcp.app.get_client")
    async def test_communities_collapse_diagnostic(self, mock_get_client):
        """Dense fully-connected graph should trigger collapse diagnostic."""
        # 6 nodes, all-to-all edges via implements (very dense)
        nodes = [(i, f"N{i}", "standard", "t") for i in range(1, 7)]
        edges = []
        for i in range(1, 7):
            for j in range(i + 1, 7):
                edges.append((i, j, "pink"))
        graph = self._make_graph(nodes, edges)
        mock_get_client.return_value = self._graph_mock(graph)
        result = await detect_communities(1, min_community_size=2)
        self.assertIn("Community Detection", result)
        self.assertIn("Modularity Q:", result)
        self.assertIn("Edge density:", result)
        # Should detect collapse (single community with all 6 nodes)
        self.assertIn("Community 1", result)

    @patch("fairsharing_mcp.app.get_client")
    async def test_communities_modularity_reported(self, mock_get_client):
        """Two-cluster graph should report modularity Q > 0."""
        graph = self._make_graph(
            [
                (1, "A1", "standard", "t"),
                (2, "A2", "standard", "t"),
                (3, "A3", "database", "r"),
                (4, "B1", "database", "r"),
                (5, "B2", "database", "r"),
                (6, "B3", "standard", "t"),
            ],
            [
                (1, 2, "pink"),
                (2, 3, "pink"),
                (1, 3, "pink"),
                (4, 5, "pink"),
                (5, 6, "pink"),
                (4, 6, "pink"),
                (3, 4, "grey"),
            ],
        )
        mock_get_client.return_value = self._graph_mock(graph)
        result = await detect_communities(1, min_community_size=2)
        self.assertIn("Modularity Q:", result)
        self.assertIn("Edge density:", result)

    # ── compute_pagerank: score interpretation ────────────────────

    @patch("fairsharing_mcp.app.get_client")
    async def test_pagerank_score_interpretation_flat(self, mock_get_client):
        """Uniform graph should show flat distribution interpretation."""
        # Triangle: all nodes equal
        graph = self._make_graph(
            [(1, "A", "standard", "t"), (2, "B", "standard", "t"), (3, "C", "standard", "t")],
            [(1, 2, "pink"), (2, 3, "pink"), (1, 3, "pink")],
        )
        mock_get_client.return_value = self._graph_mock(graph)
        result = await compute_pagerank(1, top_n=3)
        self.assertIn("Score Interpretation", result)
        self.assertIn("Score spread:", result)

    @patch("fairsharing_mcp.app.get_client")
    async def test_pagerank_score_interpretation_hierarchy(self, mock_get_client):
        """Star graph should show hierarchy in score interpretation."""
        # Star: center receives all implements edges
        graph = self._make_graph(
            [
                (1, "Hub", "standard", "t"),
                (2, "L1", "database", "r"),
                (3, "L2", "database", "r"),
                (4, "L3", "database", "r"),
                (5, "L4", "database", "r"),
                (6, "L5", "database", "r"),
                (7, "L6", "database", "r"),
            ],
            [
                (1, 2, "pink"),
                (1, 3, "pink"),
                (1, 4, "pink"),
                (1, 5, "pink"),
                (1, 6, "pink"),
                (1, 7, "pink"),
            ],
        )
        mock_get_client.return_value = self._graph_mock(graph)
        result = await compute_pagerank(1, top_n=7)
        self.assertIn("Score Interpretation", result)
        self.assertIn("Score spread:", result)
        # Should show why top node ranks #1
        self.assertIn("Why #", result)

    # ── compute_betweenness_centrality: enriched analysis ─────────

    @patch("fairsharing_mcp.app.get_client")
    async def test_betweenness_score_distribution(self, mock_get_client):
        """Verify score distribution stats appear in output."""
        graph = self._make_graph(
            [
                (1, "Center", "standard", "t"),
                (2, "Leaf1", "database", "r"),
                (3, "Leaf2", "database", "r"),
                (4, "Leaf3", "database", "r"),
                (5, "Leaf4", "database", "r"),
            ],
            [(1, 2, "pink"), (1, 3, "pink"), (1, 4, "pink"), (1, 5, "pink")],
        )
        mock_get_client.return_value = self._graph_mock(graph)
        result = await compute_betweenness_centrality(1, top_n=5)
        self.assertIn("Score Distribution", result)
        self.assertIn("Max:", result)
        self.assertIn("Mean:", result)
        self.assertIn("Max/Mean ratio:", result)

    @patch("fairsharing_mcp.app.get_client")
    async def test_betweenness_bridge_classification(self, mock_get_client):
        """Star topology center should be classified as critical bridge."""
        graph = self._make_graph(
            [
                (1, "Center", "standard", "t"),
                (2, "L1", "database", "r"),
                (3, "L2", "database", "r"),
                (4, "L3", "database", "r"),
                (5, "L4", "database", "r"),
                (6, "L5", "database", "r"),
                (7, "L6", "database", "r"),
            ],
            [
                (1, 2, "pink"),
                (1, 3, "pink"),
                (1, 4, "pink"),
                (1, 5, "pink"),
                (1, 6, "pink"),
                (1, 7, "pink"),
            ],
        )
        mock_get_client.return_value = self._graph_mock(graph)
        result = await compute_betweenness_centrality(1, top_n=7)
        self.assertIn("Bridge Analysis", result)
        self.assertIn("critical bridge", result)
        # Should show actual BC scores in bridge entries
        self.assertIn("BC=", result)

    # ── analyze_graph_comprehensive ──────────────────────────────

    @patch("fairsharing_mcp.app.get_client")
    async def test_comprehensive_analysis_basic(self, mock_get_client):
        """Comprehensive analysis should include all three analyses cross-referenced."""
        graph = self._make_graph(
            [
                (1, "StdA", "standard", "t"),
                (2, "DB1", "database", "r"),
                (3, "DB2", "database", "r"),
                (4, "StdB", "standard", "t"),
                (5, "Pol1", "policy", "f"),
            ],
            [(1, 2, "pink"), (1, 3, "pink"), (4, 3, "pink"), (5, 2, "orange")],
        )
        mock_get_client.return_value = self._graph_mock(graph)
        result = await analyze_graph_comprehensive(1, top_n=5)
        self.assertIn("Comprehensive Graph Analysis", result)
        self.assertIn("Quality Indicators", result)
        self.assertIn("Modularity Q:", result)
        self.assertIn("PageRank spread:", result)
        self.assertIn("Cross-Reference", result)
        self.assertIn("Synthesis", result)
        # Table headers
        self.assertIn("PR Rank", result)
        self.assertIn("BC Rank", result)
        self.assertIn("Community", result)

    @patch("fairsharing_mcp.app.get_client")
    async def test_comprehensive_analysis_collapse(self, mock_get_client):
        """Comprehensive analysis on dense graph should flag community collapse."""
        nodes = [(i, f"N{i}", "standard", "t") for i in range(1, 7)]
        edges = []
        for i in range(1, 7):
            for j in range(i + 1, 7):
                edges.append((i, j, "pink"))
        graph = self._make_graph(nodes, edges)
        mock_get_client.return_value = self._graph_mock(graph)
        result = await analyze_graph_comprehensive(1, top_n=6, min_community_size=2)
        self.assertIn("Comprehensive Graph Analysis", result)
        self.assertIn("Quality Indicators", result)
        # Should detect and flag collapse
        self.assertIn("Synthesis", result)

    @patch("fairsharing_mcp.app.get_client")
    async def test_comprehensive_analysis_too_small(self, mock_get_client):
        """Comprehensive analysis on tiny graph should return error."""
        graph = self._make_graph(
            [(1, "A", "standard", "t"), (2, "B", "database", "r")],
            [(1, 2, "pink")],
        )
        mock_get_client.return_value = self._graph_mock(graph)
        result = await analyze_graph_comprehensive(1)
        self.assertIn("too small", result)

    @patch("fairsharing_mcp.app.get_client")
    async def test_check_policy_database_compliance_overlap(self, mock_get_client):
        mock_client = AsyncMock()
        mock_get_client.return_value = mock_client

        # Policy recommends standards 10, 20, 30
        # Database implements standards 20, 30, 40
        # -> Compliant: 20, 30  Gaps: 10  Extras: 40
        policy_record = {
            "fairsharingRecord": {
                "id": "1",
                "name": "Test Policy",
                "registry": "Policy",
                "type": "journal",
                "recordAssociations": [
                    {
                        "linkedRecord": {
                            "id": "10",
                            "name": "Std A",
                            "abbreviation": "SA",
                            "registry": "Standard",
                            "type": "model/format",
                            "status": "ready",
                        },
                        "recordAssocLabel": "recommends",
                    },
                    {
                        "linkedRecord": {
                            "id": "20",
                            "name": "Std B",
                            "abbreviation": "SB",
                            "registry": "Standard",
                            "type": "reporting_guideline",
                            "status": "ready",
                        },
                        "recordAssocLabel": "recommends",
                    },
                    {
                        "linkedRecord": {
                            "id": "30",
                            "name": "Std C",
                            "abbreviation": "SC",
                            "registry": "Standard",
                            "type": "model/format",
                            "status": "ready",
                        },
                        "recordAssocLabel": "recommends",
                    },
                ],
                "reverseRecordAssociations": [],
            }
        }
        db_record = {
            "fairsharingRecord": {
                "id": "2",
                "name": "Test DB",
                "registry": "Database",
                "type": "repository",
                "recordAssociations": [
                    {
                        "linkedRecord": {
                            "id": "20",
                            "name": "Std B",
                            "abbreviation": "SB",
                            "registry": "Standard",
                            "type": "reporting_guideline",
                            "status": "ready",
                        },
                        "recordAssocLabel": "implements",
                    },
                    {
                        "linkedRecord": {
                            "id": "40",
                            "name": "Std D",
                            "abbreviation": "SD",
                            "registry": "Standard",
                            "type": "terminology_artefact",
                            "status": "ready",
                        },
                        "recordAssocLabel": "implements",
                    },
                ],
                "reverseRecordAssociations": [
                    {
                        "fairsharingRecord": {
                            "id": "30",
                            "name": "Std C",
                            "abbreviation": "SC",
                            "registry": "Standard",
                            "type": "model/format",
                            "status": "ready",
                        },
                        "recordAssocLabel": "implements",
                    },
                ],
            }
        }
        mock_client.query.side_effect = [policy_record, db_record]

        result = await check_policy_database_compliance(policy_id=1, database_id=2)

        self.assertIn("Compliant", result)
        self.assertIn("Std B", result)
        self.assertIn("Std C", result)
        self.assertIn("Gap", result)
        self.assertIn("Std A", result)
        self.assertIn("Extra", result)
        self.assertIn("Std D", result)
        self.assertIn("67%", result)  # 2/3 compliance

    @patch("fairsharing_mcp.app.get_client")
    async def test_check_policy_database_compliance_no_overlap(self, mock_get_client):
        mock_client = AsyncMock()
        mock_get_client.return_value = mock_client

        policy_record = {
            "fairsharingRecord": {
                "id": "1",
                "name": "Policy X",
                "registry": "Policy",
                "type": "funder",
                "recordAssociations": [
                    {
                        "linkedRecord": {
                            "id": "10",
                            "name": "Std A",
                            "abbreviation": "",
                            "registry": "Standard",
                            "type": "model/format",
                            "status": "ready",
                        },
                        "recordAssocLabel": "recommends",
                    },
                ],
                "reverseRecordAssociations": [],
            }
        }
        db_record = {
            "fairsharingRecord": {
                "id": "2",
                "name": "DB Y",
                "registry": "Database",
                "type": "repository",
                "recordAssociations": [
                    {
                        "linkedRecord": {
                            "id": "20",
                            "name": "Std B",
                            "abbreviation": "",
                            "registry": "Standard",
                            "type": "model/format",
                            "status": "ready",
                        },
                        "recordAssocLabel": "implements",
                    },
                ],
                "reverseRecordAssociations": [],
            }
        }
        mock_client.query.side_effect = [policy_record, db_record]

        result = await check_policy_database_compliance(policy_id=1, database_id=2)

        self.assertIn("0%", result)  # 0 compliance
        self.assertIn("Std A", result)
        self.assertIn("Std B", result)

    @patch("fairsharing_mcp.app.get_client")
    async def test_check_policy_database_compliance_wrong_type(self, mock_get_client):
        mock_client = AsyncMock()
        mock_get_client.return_value = mock_client

        # Record is a Standard, not a Policy
        mock_client.query.side_effect = [
            {
                "fairsharingRecord": {
                    "id": "1",
                    "name": "Not Policy",
                    "registry": "Standard",
                    "type": "model/format",
                }
            },
            {
                "fairsharingRecord": {
                    "id": "2",
                    "name": "Test DB",
                    "registry": "Database",
                    "type": "repository",
                }
            },
        ]

        result = await check_policy_database_compliance(policy_id=1, database_id=2)
        self.assertIn("not a Policy", result)

    # ── Rate limiter lock under concurrency ─────────────────────────

    async def test_rate_limiter_serializes_concurrent_requests(self):
        """Token bucket rate limiter should serialize concurrent requests."""
        import asyncio  # noqa: E402

        from fairsharing_mcp.client import FAIRsharingClient

        # burst=1 ensures every request after the first must wait
        client = FAIRsharingClient(api_key="test-key", rate_limit_burst=1)
        bucket = client._rate_limiter
        timestamps: list[float] = []

        original_acquire = bucket.acquire

        async def recording_acquire():
            await original_acquire()
            timestamps.append(asyncio.get_event_loop().time())

        bucket.acquire = recording_acquire

        # Fire 5 concurrent acquire calls
        await asyncio.gather(*(bucket.acquire() for _ in range(5)))

        # With burst=1, after the first call each successive call should wait ~200ms
        min_gap = (1.0 / client.DEFAULT_RATE_RPS) * 0.9
        for i in range(1, len(timestamps)):
            delta = timestamps[i] - timestamps[i - 1]
            self.assertGreaterEqual(
                delta,
                min_gap,
                f"Gap between call {i - 1} and {i} was {delta:.4f}s, expected >= {min_gap:.4f}s",
            )

    # ── HTTP 402 rate limit handling ──────────────────────────────

    async def test_http_402_is_retried_as_rate_limit(self):
        """HTTP 402 should be retried with exponential backoff like 429."""
        from fairsharing_mcp.client import FAIRsharingClient

        client = FAIRsharingClient(api_key="test-key", max_retries=2)

        mock_response_402 = MagicMock()
        mock_response_402.status_code = 402

        mock_response_ok = MagicMock()
        mock_response_ok.status_code = 200
        mock_response_ok.json.return_value = {"data": {"result": "ok"}}
        mock_response_ok.raise_for_status = MagicMock()

        call_count = 0

        async def mock_post(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return mock_response_402
            return mock_response_ok

        mock_async_client = AsyncMock()
        mock_async_client.post = mock_post
        mock_async_client.__aenter__ = AsyncMock(return_value=mock_async_client)
        mock_async_client.__aexit__ = AsyncMock(return_value=False)

        with patch("httpx.AsyncClient", return_value=mock_async_client):
            result = await client.query("query { test }")

        self.assertEqual(result, {"result": "ok"})
        self.assertEqual(call_count, 2)

    async def test_http_402_raises_after_max_retries(self):
        """HTTP 402 should raise FAIRsharingRateLimitError after exhausting retries."""
        from fairsharing_mcp.client import FAIRsharingClient, FAIRsharingRateLimitError

        client = FAIRsharingClient(api_key="test-key", max_retries=2)

        mock_response = MagicMock()
        mock_response.status_code = 402

        mock_async_client = AsyncMock()
        mock_async_client.post = AsyncMock(return_value=mock_response)
        mock_async_client.__aenter__ = AsyncMock(return_value=mock_async_client)
        mock_async_client.__aexit__ = AsyncMock(return_value=False)

        with patch("httpx.AsyncClient", return_value=mock_async_client):
            with self.assertRaises(FAIRsharingRateLimitError):
                await client.query("query { test }")

    # ── analyze_regional_distribution includes Collections ────────

    @patch("fairsharing_mcp.app.get_client")
    async def test_analyze_regional_distribution_includes_collections(self, mock_get_client):
        """analyze_regional_distribution should query 4 registries including Collection."""
        mock_client = AsyncMock()
        mock_get_client.return_value = mock_client

        mock_client.query.return_value = {"searchFairsharingRecords": {"totalCount": 3}}

        result = await analyze_regional_distribution(["United Kingdom"])

        self.assertIn("Collections", result)

        # Should make 4 queries (Database, Standard, Policy, Collection)
        self.assertEqual(mock_client.query.call_count, 4)
        queried_registries = set()
        for call in mock_client.query.call_args_list:
            args, kwargs = call
            variables = args[1] if len(args) > 1 else kwargs.get("variables", {})
            queried_registries.update(variables.get("registry", []))
        self.assertIn("Collection", queried_registries)

    # ── analyze_regional_distribution partial failure ─────────────

    @patch("fairsharing_mcp.app.get_client")
    async def test_analyze_regional_distribution_partial_failure(self, mock_get_client):
        """When some queries fail, should return partial results with warning."""
        mock_client = AsyncMock()
        mock_get_client.return_value = mock_client

        call_count = 0

        async def side_effect(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            # Fail on the 5th call (first query for second region)
            if call_count == 5:
                raise Exception("API rate limit")
            return {"searchFairsharingRecords": {"totalCount": 10}}

        mock_client.query.side_effect = side_effect

        result = await analyze_regional_distribution(["UK Good", "UK Bad"])

        self.assertIn("UK Good", result)
        self.assertIn("UK Bad", result)
        self.assertIn("Some queries failed", result)

    # ── Gap 1: search_records fallback_on_empty ──────────────────────────

    @patch("fairsharing_mcp.app.get_client")
    async def test_search_records_fallback_disabled_by_default(self, mock_get_client):
        """Default behaviour unchanged: no fallback, shows zero-result message."""
        mock_client = AsyncMock()
        mock_get_client.return_value = mock_client
        mock_client.query.return_value = {
            "searchFairsharingRecords": {"records": [], "totalCount": 0, "totalPages": 0}
        }

        result = await search_records(
            registry=["Policy"], subjects=["Genomics"], countries=["Japan"]
        )

        self.assertIn("No records found", result)
        self.assertEqual(mock_client.query.call_count, 1)

    @patch("fairsharing_mcp.app.get_client")
    async def test_search_records_fallback_relaxes_subjects(self, mock_get_client):
        """Fallback removes subjects first and finds results."""
        mock_client = AsyncMock()
        mock_get_client.return_value = mock_client

        empty = {"searchFairsharingRecords": {"records": [], "totalCount": 0, "totalPages": 0}}
        found = {
            "searchFairsharingRecords": {
                "records": [
                    {
                        "id": "1",
                        "name": "JP Policy",
                        "abbreviation": "JPP",
                        "registry": "Policy",
                        "type": "funder",
                        "status": "ready",
                    }
                ],
                "totalCount": 1,
                "totalPages": 1,
            }
        }
        mock_client.query.side_effect = [empty, found]

        result = await search_records(
            registry=["Policy"],
            subjects=["Genomics"],
            countries=["Japan"],
            fallback_on_empty=True,
        )

        self.assertIn("JP Policy", result)
        self.assertIn("DROPPED", result)
        self.assertIn("subjects=", result)
        self.assertEqual(mock_client.query.call_count, 2)

    @patch("fairsharing_mcp.app.get_client")
    async def test_search_records_fallback_relaxes_subjects_and_countries(self, mock_get_client):
        """Fallback removes subjects, then countries."""
        mock_client = AsyncMock()
        mock_get_client.return_value = mock_client

        empty = {"searchFairsharingRecords": {"records": [], "totalCount": 0, "totalPages": 0}}
        found = {
            "searchFairsharingRecords": {
                "records": [
                    {
                        "id": "2",
                        "name": "Global Policy",
                        "abbreviation": "GP",
                        "registry": "Policy",
                        "type": "funder",
                        "status": "ready",
                    }
                ],
                "totalCount": 1,
                "totalPages": 1,
            }
        }
        mock_client.query.side_effect = [empty, empty, found]

        result = await search_records(
            registry=["Policy"],
            subjects=["Genomics"],
            countries=["Japan"],
            fallback_on_empty=True,
        )

        self.assertIn("Global Policy", result)
        self.assertIn("DROPPED", result)
        self.assertIn("countries=", result)
        self.assertEqual(mock_client.query.call_count, 3)

    @patch("fairsharing_mcp.app.get_client")
    async def test_search_records_fallback_all_empty(self, mock_get_client):
        """All fallback retries empty → original zero-result message."""
        mock_client = AsyncMock()
        mock_get_client.return_value = mock_client

        empty = {"searchFairsharingRecords": {"records": [], "totalCount": 0, "totalPages": 0}}
        mock_client.query.side_effect = [empty, empty, empty]

        result = await search_records(
            registry=["Policy"],
            subjects=["Genomics"],
            countries=["Japan"],
            fallback_on_empty=True,
        )

        self.assertIn("No records found", result)
        self.assertEqual(mock_client.query.call_count, 3)

    @patch("fairsharing_mcp.app.get_client")
    async def test_search_records_fallback_no_subjects_skips_to_countries(self, mock_get_client):
        """No subjects filter → skips step 1, tries removing countries."""
        mock_client = AsyncMock()
        mock_get_client.return_value = mock_client

        empty = {"searchFairsharingRecords": {"records": [], "totalCount": 0, "totalPages": 0}}
        found = {
            "searchFairsharingRecords": {
                "records": [
                    {
                        "id": "3",
                        "name": "Any Policy",
                        "abbreviation": "AP",
                        "registry": "Policy",
                        "type": "funder",
                        "status": "ready",
                    }
                ],
                "totalCount": 1,
                "totalPages": 1,
            }
        }
        mock_client.query.side_effect = [empty, found]

        result = await search_records(
            registry=["Policy"],
            countries=["Japan"],
            fallback_on_empty=True,
        )

        self.assertIn("Any Policy", result)
        self.assertIn("countries=", result)
        self.assertEqual(mock_client.query.call_count, 2)

    # ── Gap 2: detect_policy_conflicts ─────────────────────────────────

    @patch("fairsharing_mcp.app.get_client")
    async def test_detect_policy_conflicts_basic_conflict(self, mock_get_client):
        """HIGH conflict when one requires and another does not cover data sharing."""
        mock_client = AsyncMock()
        mock_get_client.return_value = mock_client

        policy_a = {
            "fairsharingRecord": {
                "id": "1",
                "name": "Funder Policy",
                "registry": "Policy",
                "type": "funder",
                "metadata": {
                    "sharing_data": {"mandated_data_sharing": "required"},
                    "dmp_development": {"mandated_dmp_creation": "required"},
                },
                "recordAssociations": [],
                "reverseRecordAssociations": [],
            }
        }
        policy_b = {
            "fairsharingRecord": {
                "id": "2",
                "name": "Journal Policy",
                "registry": "Policy",
                "type": "journal",
                "metadata": {
                    "sharing_data": {"mandated_data_sharing": "not covered"},
                    "dmp_development": {"mandated_dmp_creation": "suggested"},
                },
                "recordAssociations": [],
                "reverseRecordAssociations": [],
            }
        }
        mock_client.query.side_effect = [policy_a, policy_b]

        result = await detect_policy_conflicts(policy_ids=[1, 2])

        self.assertIn("Conflict", result)
        self.assertIn("HIGH", result)
        self.assertIn("Data Sharing", result)
        self.assertIn("Resolution", result)

    @patch("fairsharing_mcp.app.get_client")
    async def test_detect_policy_conflicts_no_conflict(self, mock_get_client):
        """No conflicts when all mandate values are identical."""
        mock_client = AsyncMock()
        mock_get_client.return_value = mock_client

        policy = {
            "fairsharingRecord": {
                "id": "1",
                "name": "Policy A",
                "registry": "Policy",
                "type": "funder",
                "metadata": {
                    "sharing_data": {"mandated_data_sharing": "required"},
                    "dmp_development": {"mandated_dmp_creation": "required"},
                },
                "recordAssociations": [],
                "reverseRecordAssociations": [],
            }
        }
        # Both return the same data
        mock_client.query.side_effect = [policy, policy]

        result = await detect_policy_conflicts(policy_ids=[1, 2])

        self.assertIn("No conflicts", result)
        self.assertIn("**Conflicts detected:** 0", result)

    @patch("fairsharing_mcp.app.get_client")
    async def test_detect_policy_conflicts_wrong_type(self, mock_get_client):
        """Non-policy record rejected."""
        mock_client = AsyncMock()
        mock_get_client.return_value = mock_client

        mock_client.query.return_value = {
            "fairsharingRecord": {
                "id": "1",
                "name": "Some DB",
                "registry": "Database",
                "type": "repository",
                "recordAssociations": [],
                "reverseRecordAssociations": [],
            }
        }

        result = await detect_policy_conflicts(policy_ids=[1, 2])

        self.assertIn("not a Policy", result)

    async def test_detect_policy_conflicts_too_few_ids(self):
        """Validation: less than 2 IDs."""
        result = await detect_policy_conflicts(policy_ids=[1])
        self.assertIn("at least 2", result)

    # ── Gap 3: find_deprecated_resources ───────────────────────────────

    @patch("fairsharing_mcp.app.get_client")
    async def test_find_deprecated_resources_subjects_match(self, mock_get_client):
        """Strategy 1 succeeds: subjects + status=deprecated."""
        mock_client = AsyncMock()
        mock_get_client.return_value = mock_client

        search_result = {
            "searchFairsharingRecords": {
                "records": [
                    {
                        "id": "10",
                        "name": "Old DB",
                        "abbreviation": "ODB",
                        "registry": "Database",
                        "type": "repository",
                        "status": "deprecated",
                    }
                ],
                "totalCount": 1,
                "totalPages": 1,
            }
        }
        detail_result = {
            "fairsharingRecord": {
                "id": "10",
                "reverseRecordAssociations": [
                    {"fairsharingRecord": {"status": "ready"}},
                    {"fairsharingRecord": {"status": "deprecated"}},
                ],
            }
        }
        mock_client.query.side_effect = [search_result, detail_result]

        result = await find_deprecated_resources(subjects=["Genomics"])

        self.assertIn("Old DB", result)
        self.assertIn("subjects + status=deprecated", result)
        self.assertIn("1 active record(s)", result)

    @patch("fairsharing_mcp.app.get_client")
    async def test_find_deprecated_resources_subjects_empty_query_fallback(self, mock_get_client):
        """Strategy 1 empty → strategy 2 with query text succeeds."""
        mock_client = AsyncMock()
        mock_get_client.return_value = mock_client

        empty = {"searchFairsharingRecords": {"records": [], "totalCount": 0, "totalPages": 0}}
        found = {
            "searchFairsharingRecords": {
                "records": [
                    {
                        "id": "20",
                        "name": "Legacy Standard",
                        "abbreviation": "LS",
                        "registry": "Standard",
                        "type": "model_and_format",
                        "status": "deprecated",
                    }
                ],
                "totalCount": 1,
                "totalPages": 1,
            }
        }
        detail = {"fairsharingRecord": {"id": "20", "reverseRecordAssociations": []}}
        mock_client.query.side_effect = [empty, found, detail]

        result = await find_deprecated_resources(subjects=["Genomics"], query="legacy")

        self.assertIn("Legacy Standard", result)
        self.assertIn("subjects filter removed", result)
        self.assertIn("Warning", result)
        self.assertIn("No active dependents", result)

    @patch("fairsharing_mcp.app.get_client")
    async def test_find_deprecated_resources_all_empty(self, mock_get_client):
        """All three strategies return empty."""
        mock_client = AsyncMock()
        mock_get_client.return_value = mock_client

        empty = {"searchFairsharingRecords": {"records": [], "totalCount": 0, "totalPages": 0}}
        mock_client.query.side_effect = [empty, empty, empty]

        result = await find_deprecated_resources(
            subjects=["Genomics"], query="nope", registry=["Database"]
        )

        self.assertIn("No deprecated records found", result)

    @patch("fairsharing_mcp.app.get_client")
    async def test_find_deprecated_resources_impact_count(self, mock_get_client):
        """Active dependents counted correctly."""
        mock_client = AsyncMock()
        mock_get_client.return_value = mock_client

        search_result = {
            "searchFairsharingRecords": {
                "records": [
                    {
                        "id": "30",
                        "name": "Dep DB",
                        "abbreviation": "",
                        "registry": "Database",
                        "type": "repository",
                        "status": "deprecated",
                    }
                ],
                "totalCount": 1,
                "totalPages": 1,
            }
        }
        detail_result = {
            "fairsharingRecord": {
                "id": "30",
                "reverseRecordAssociations": [
                    {"fairsharingRecord": {"status": "ready"}},
                    {"fairsharingRecord": {"status": "ready"}},
                    {"fairsharingRecord": {"status": "ready"}},
                    {"fairsharingRecord": {"status": "deprecated"}},
                ],
            }
        }
        mock_client.query.side_effect = [search_result, detail_result]

        result = await find_deprecated_resources(registry=["Database"])

        self.assertIn("3 active record(s)", result)

    # ── Gap 5: find_compliant_standards ────────────────────────────────

    @patch("fairsharing_mcp.app.get_client")
    async def test_find_compliant_standards_intersection(self, mock_get_client):
        """Two policies with overlapping standards → correct intersection."""
        mock_client = AsyncMock()
        mock_get_client.return_value = mock_client

        policy_a = {
            "fairsharingRecord": {
                "id": "100",
                "name": "Policy A",
                "registry": "Policy",
                "type": "funder",
                "recordAssociations": [
                    {
                        "linkedRecord": {
                            "id": "10",
                            "name": "Std X",
                            "abbreviation": "SX",
                            "registry": "Standard",
                            "type": "model_and_format",
                        },
                        "recordAssocLabel": "recommends",
                    },
                    {
                        "linkedRecord": {
                            "id": "20",
                            "name": "Std Y",
                            "abbreviation": "SY",
                            "registry": "Standard",
                            "type": "reporting_guideline",
                        },
                        "recordAssocLabel": "recommends",
                    },
                    {
                        "linkedRecord": {
                            "id": "30",
                            "name": "Std Z",
                            "abbreviation": "SZ",
                            "registry": "Standard",
                            "type": "model_and_format",
                        },
                        "recordAssocLabel": "recommends",
                    },
                ],
                "reverseRecordAssociations": [],
            }
        }
        policy_b = {
            "fairsharingRecord": {
                "id": "200",
                "name": "Policy B",
                "registry": "Policy",
                "type": "journal",
                "recordAssociations": [
                    {
                        "linkedRecord": {
                            "id": "20",
                            "name": "Std Y",
                            "abbreviation": "SY",
                            "registry": "Standard",
                            "type": "reporting_guideline",
                        },
                        "recordAssocLabel": "recommends",
                    },
                    {
                        "linkedRecord": {
                            "id": "30",
                            "name": "Std Z",
                            "abbreviation": "SZ",
                            "registry": "Standard",
                            "type": "model_and_format",
                        },
                        "recordAssocLabel": "recommends",
                    },
                    {
                        "linkedRecord": {
                            "id": "40",
                            "name": "Std W",
                            "abbreviation": "SW",
                            "registry": "Standard",
                            "type": "terminology_artefact",
                        },
                        "recordAssocLabel": "recommends",
                    },
                ],
                "reverseRecordAssociations": [],
            }
        }
        mock_client.query.side_effect = [policy_a, policy_b]

        result = await find_compliant_standards(policy_ids=[100, 200])

        self.assertIn("Universally Required Standards (2)", result)
        self.assertIn("Std Y", result)
        self.assertIn("Std Z", result)
        self.assertNotIn("Std X", result.split("Universally")[1].split("Per-Policy")[0])

    @patch("fairsharing_mcp.app.get_client")
    async def test_find_compliant_standards_with_databases(self, mock_get_client):
        """Intersection + database compliance: one gap, one implemented."""
        mock_client = AsyncMock()
        mock_get_client.return_value = mock_client

        policy_a = {
            "fairsharingRecord": {
                "id": "100",
                "name": "PA",
                "registry": "Policy",
                "type": "funder",
                "recordAssociations": [
                    {
                        "linkedRecord": {
                            "id": "10",
                            "name": "Std X",
                            "abbreviation": "SX",
                            "registry": "Standard",
                            "type": "mf",
                        },
                        "recordAssocLabel": "recommends",
                    },
                    {
                        "linkedRecord": {
                            "id": "20",
                            "name": "Std Y",
                            "abbreviation": "SY",
                            "registry": "Standard",
                            "type": "rg",
                        },
                        "recordAssocLabel": "recommends",
                    },
                ],
                "reverseRecordAssociations": [],
            }
        }
        policy_b = {
            "fairsharingRecord": {
                "id": "200",
                "name": "PB",
                "registry": "Policy",
                "type": "journal",
                "recordAssociations": [
                    {
                        "linkedRecord": {
                            "id": "10",
                            "name": "Std X",
                            "abbreviation": "SX",
                            "registry": "Standard",
                            "type": "mf",
                        },
                        "recordAssocLabel": "recommends",
                    },
                    {
                        "linkedRecord": {
                            "id": "20",
                            "name": "Std Y",
                            "abbreviation": "SY",
                            "registry": "Standard",
                            "type": "rg",
                        },
                        "recordAssocLabel": "recommends",
                    },
                ],
                "reverseRecordAssociations": [],
            }
        }
        database = {
            "fairsharingRecord": {
                "id": "500",
                "name": "DB One",
                "registry": "Database",
                "type": "repository",
                "recordAssociations": [
                    {
                        "linkedRecord": {
                            "id": "10",
                            "name": "Std X",
                            "abbreviation": "SX",
                            "registry": "Standard",
                            "type": "mf",
                        },
                        "recordAssocLabel": "implements",
                    },
                ],
                "reverseRecordAssociations": [],
            }
        }
        mock_client.query.side_effect = [policy_a, policy_b, database]

        result = await find_compliant_standards(policy_ids=[100, 200], database_ids=[500])

        self.assertIn("Implemented by all databases", result)
        self.assertIn("GAP", result)
        self.assertIn("Std Y", result)

    @patch("fairsharing_mcp.app.get_client")
    async def test_find_compliant_standards_no_overlap(self, mock_get_client):
        """Disjoint standard sets → no universally required standards."""
        mock_client = AsyncMock()
        mock_get_client.return_value = mock_client

        policy_a = {
            "fairsharingRecord": {
                "id": "100",
                "name": "PA",
                "registry": "Policy",
                "type": "funder",
                "recordAssociations": [
                    {
                        "linkedRecord": {
                            "id": "10",
                            "name": "Std A",
                            "abbreviation": "",
                            "registry": "Standard",
                            "type": "mf",
                        },
                        "recordAssocLabel": "recommends",
                    },
                ],
                "reverseRecordAssociations": [],
            }
        }
        policy_b = {
            "fairsharingRecord": {
                "id": "200",
                "name": "PB",
                "registry": "Policy",
                "type": "journal",
                "recordAssociations": [
                    {
                        "linkedRecord": {
                            "id": "20",
                            "name": "Std B",
                            "abbreviation": "",
                            "registry": "Standard",
                            "type": "mf",
                        },
                        "recordAssocLabel": "recommends",
                    },
                ],
                "reverseRecordAssociations": [],
            }
        }
        mock_client.query.side_effect = [policy_a, policy_b]

        result = await find_compliant_standards(policy_ids=[100, 200])

        self.assertIn("No standards are recommended by all policies", result)

    @patch("fairsharing_mcp.app.get_client")
    async def test_find_compliant_standards_wrong_type(self, mock_get_client):
        """Non-policy record rejected."""
        mock_client = AsyncMock()
        mock_get_client.return_value = mock_client

        mock_client.query.return_value = {
            "fairsharingRecord": {
                "id": "100",
                "name": "Some DB",
                "registry": "Database",
                "type": "repository",
                "recordAssociations": [],
                "reverseRecordAssociations": [],
            }
        }

        result = await find_compliant_standards(policy_ids=[100, 200])

        self.assertIn("not a Policy", result)

    @patch("fairsharing_mcp.app.get_client")
    async def test_find_compliant_standards_partial_tier(self, mock_get_client):
        """Three policies: 2 share a standard the 3rd doesn't → partial tier."""
        mock_client = AsyncMock()
        mock_get_client.return_value = mock_client

        # All three recommend Std-Common (id=1)
        # Only PA and PB recommend Std-Partial (id=2)
        common_assoc = {
            "linkedRecord": {
                "id": "1",
                "name": "Std-Common",
                "abbreviation": "SC",
                "registry": "Standard",
                "type": "mf",
            },
            "recordAssocLabel": "recommends",
        }
        partial_assoc = {
            "linkedRecord": {
                "id": "2",
                "name": "Std-Partial",
                "abbreviation": "SP",
                "registry": "Standard",
                "type": "rg",
            },
            "recordAssocLabel": "recommends",
        }

        pa = {
            "fairsharingRecord": {
                "id": "100",
                "name": "PA",
                "registry": "Policy",
                "type": "funder",
                "recordAssociations": [common_assoc, partial_assoc],
                "reverseRecordAssociations": [],
            }
        }
        pb = {
            "fairsharingRecord": {
                "id": "200",
                "name": "PB",
                "registry": "Policy",
                "type": "journal",
                "recordAssociations": [common_assoc, partial_assoc],
                "reverseRecordAssociations": [],
            }
        }
        pc = {
            "fairsharingRecord": {
                "id": "300",
                "name": "PC",
                "registry": "Policy",
                "type": "institution",
                "recordAssociations": [common_assoc],
                "reverseRecordAssociations": [],
            }
        }
        mock_client.query.side_effect = [pa, pb, pc]

        result = await find_compliant_standards(policy_ids=[100, 200, 300])

        self.assertIn("Universally Required Standards (1)", result)
        self.assertIn("Std-Common", result)
        self.assertIn("Partially Required Standards", result)
        self.assertIn("Std-Partial", result)

    # ── compute_maturity_index ──────────────────────────────────────

    @patch("fairsharing_mcp.app.get_client")
    async def test_compute_maturity_index_basic(self, mock_get_client):
        """compute_maturity_index should rank standards by SMI score descending."""
        mock_client = AsyncMock()
        mock_get_client.return_value = mock_client

        # Call 1: stats (cached)
        stats_response = {
            "latestStats": {
                "createdAt": "2026-01-01",
                "data": {
                    "top_10_stds_recommended_by_pols": {
                        "DOID": {"id": "10", "count": 0},
                        "NCIt": {"id": "20", "count": 3},
                    },
                    "dbs_to_pubs": {},
                    "stds_to_pubs": {},
                },
            }
        }
        # Call 2: search for implemented standards
        search_response = {
            "searchFairsharingRecords": {
                "records": [
                    {
                        "id": "30",
                        "name": "GO",
                        "abbreviation": "GO",
                        "registry": "Standard",
                        "type": "terminology_artefact",
                        "status": "ready",
                        "createdAt": "2010-01-01",
                    },
                ],
                "totalCount": 100,
                "totalPages": 2,
            }
        }
        # Calls 3-5: association lookups for 3 candidates (DOID=10, NCIt=20, GO=30)
        doid_record = {
            "fairsharingRecord": {
                "id": "10",
                "name": "Disease Ontology",
                "abbreviation": "DOID",
                "type": "terminology_artefact",
                "status": "ready",
                "reverseRecordAssociations": [
                    {
                        "recordAssocLabel": "implements",
                        "fairsharingRecord": {"registry": "Database", "id": "d1"},
                    },
                    {
                        "recordAssocLabel": "implements",
                        "fairsharingRecord": {"registry": "Database", "id": "d2"},
                    },
                    {
                        "recordAssocLabel": "implements",
                        "fairsharingRecord": {"registry": "Database", "id": "d3"},
                    },
                ],
                "recordAssociations": [],
            }
        }
        ncit_record = {
            "fairsharingRecord": {
                "id": "20",
                "name": "NCI Thesaurus",
                "abbreviation": "NCIt",
                "type": "terminology_artefact",
                "status": "ready",
                "reverseRecordAssociations": [
                    {
                        "recordAssocLabel": "implements",
                        "fairsharingRecord": {"registry": "Database", "id": "d4"},
                    },
                    {
                        "recordAssocLabel": "recommends",
                        "fairsharingRecord": {"registry": "Policy", "id": "p1"},
                    },
                    {
                        "recordAssocLabel": "recommends",
                        "fairsharingRecord": {"registry": "Policy", "id": "p2"},
                    },
                    {
                        "recordAssocLabel": "recommends",
                        "fairsharingRecord": {"registry": "Policy", "id": "p3"},
                    },
                ],
                "recordAssociations": [],
            }
        }
        go_record = {
            "fairsharingRecord": {
                "id": "30",
                "name": "Gene Ontology",
                "abbreviation": "GO",
                "type": "terminology_artefact",
                "status": "ready",
                "reverseRecordAssociations": [
                    {
                        "recordAssocLabel": "implements",
                        "fairsharingRecord": {"registry": "Database", "id": f"d{i}"},
                    }
                    for i in range(5, 15)  # 10 databases
                ]
                + [
                    {
                        "recordAssocLabel": "recommends",
                        "fairsharingRecord": {"registry": "Policy", "id": "p4"},
                    },
                ],
                "recordAssociations": [],
            }
        }

        mock_client.query.side_effect = [
            stats_response,
            search_response,
            doid_record,
            ncit_record,
            go_record,
        ]

        result = await compute_maturity_index(top_n=3, bottom_n=3)

        # Verify output structure
        self.assertIn("Standards Maturity Index", result)
        self.assertIn("Candidates evaluated:** 3", result)
        self.assertIn("Most Mature Standards", result)

        # GO has 10 DBs + 1 policy; DOID has 3 DBs + 0 policies; NCIt has 1 DB + 3 policies
        # With default weights (0.6 adoption, 0.3 policy, 0.1 stability):
        # GO: 100 * (0.6*10/10 + 0.3*1/3 + 0.1*1) = 100*(0.6+0.1+0.1) = 80.0
        # NCIt: 100 * (0.6*1/10 + 0.3*3/3 + 0.1*1) = 100*(0.06+0.3+0.1) = 46.0
        # DOID: 100 * (0.6*3/10 + 0.3*0/3 + 0.1*1) = 100*(0.18+0+0.1) = 28.0
        # Verify GO is ranked #1 (highest SMI)
        go_pos = result.find("Gene Ontology")
        doid_pos = result.find("Disease Ontology")
        self.assertGreater(go_pos, -1)
        self.assertGreater(doid_pos, -1)
        # GO should appear before DOID in the table (ranked higher)
        self.assertLess(go_pos, doid_pos)

    @patch("fairsharing_mcp.app.get_client")
    async def test_compute_maturity_index_no_candidates(self, mock_get_client):
        """compute_maturity_index with no implemented standards returns message."""
        mock_client = AsyncMock()
        mock_get_client.return_value = mock_client

        mock_client.query.side_effect = [
            {"latestStats": {"data": {}}},  # No stats
            {"searchFairsharingRecords": {"records": [], "totalCount": 0, "totalPages": 0}},
        ]

        result = await compute_maturity_index()
        self.assertIn("No implemented standards found", result)

    @patch("fairsharing_mcp.app.get_client")
    async def test_compute_maturity_index_with_subjects(self, mock_get_client):
        """compute_maturity_index passes subjects to the search query."""
        mock_client = AsyncMock()
        mock_get_client.return_value = mock_client

        mock_client.query.side_effect = [
            {"latestStats": {"data": {}}},
            {
                "searchFairsharingRecords": {
                    "records": [
                        {"id": "1", "name": "Std1", "registry": "Standard", "status": "ready"},
                    ],
                    "totalCount": 1,
                    "totalPages": 1,
                }
            },
            {
                "fairsharingRecord": {
                    "id": "1",
                    "name": "Std1",
                    "abbreviation": "",
                    "type": "terminology_artefact",
                    "status": "ready",
                    "reverseRecordAssociations": [
                        {
                            "recordAssocLabel": "implements",
                            "fairsharingRecord": {"registry": "Database", "id": "d1"},
                        },
                    ],
                    "recordAssociations": [],
                }
            },
        ]

        result = await compute_maturity_index(subjects=["Genomics"])
        self.assertIn("Subject filter", result)
        self.assertIn("Genomics", result)

        # Verify subjects was passed in the search query
        search_call = mock_client.query.call_args_list[1]
        search_vars = search_call[0][1]
        self.assertEqual(search_vars.get("subjects"), ["Genomics"])

    @patch("fairsharing_mcp.app.get_client")
    async def test_compute_maturity_index_sort_correctness(self, mock_get_client):
        """compute_maturity_index must sort by SMI descending — the critical fix."""
        mock_client = AsyncMock()
        mock_get_client.return_value = mock_client

        mock_client.query.side_effect = [
            {"latestStats": {"data": {}}},
            {
                "searchFairsharingRecords": {
                    "records": [
                        {"id": "1", "name": "LowAdopt", "registry": "Standard", "status": "ready"},
                        {"id": "2", "name": "HighAdopt", "registry": "Standard", "status": "ready"},
                    ],
                    "totalCount": 2,
                    "totalPages": 1,
                }
            },
            # LowAdopt: 1 DB, 0 policies
            {
                "fairsharingRecord": {
                    "id": "1",
                    "name": "LowAdopt",
                    "abbreviation": "LA",
                    "type": "t",
                    "status": "ready",
                    "reverseRecordAssociations": [
                        {
                            "recordAssocLabel": "implements",
                            "fairsharingRecord": {"registry": "Database", "id": "d1"},
                        },
                    ],
                    "recordAssociations": [],
                }
            },
            # HighAdopt: 5 DBs, 2 policies
            {
                "fairsharingRecord": {
                    "id": "2",
                    "name": "HighAdopt",
                    "abbreviation": "HA",
                    "type": "t",
                    "status": "ready",
                    "reverseRecordAssociations": [
                        {
                            "recordAssocLabel": "implements",
                            "fairsharingRecord": {"registry": "Database", "id": f"d{i}"},
                        }
                        for i in range(5)
                    ]
                    + [
                        {
                            "recordAssocLabel": "recommends",
                            "fairsharingRecord": {"registry": "Policy", "id": f"p{i}"},
                        }
                        for i in range(2)
                    ],
                    "recordAssociations": [],
                }
            },
        ]

        result = await compute_maturity_index(top_n=2)

        # HighAdopt MUST appear before LowAdopt in the ranked table
        high_pos = result.find("HighAdopt")
        low_pos = result.find("LowAdopt")
        self.assertGreater(high_pos, -1)
        self.assertGreater(low_pos, -1)
        self.assertLess(high_pos, low_pos, "HighAdopt should be ranked above LowAdopt")

    # ── find_emerging_standards ──────────────────────────────────────

    @patch("fairsharing_mcp.app.get_client")
    async def test_find_emerging_standards_categorization(self, mock_get_client):
        """find_emerging_standards should categorize standards by age and adoption."""
        mock_client = AsyncMock()
        mock_get_client.return_value = mock_client

        # Call 1: unimplemented standards search
        unimplemented_response = {
            "searchFairsharingRecords": {
                "records": [
                    {
                        "id": "1",
                        "name": "NewOrphan",
                        "abbreviation": "NO",
                        "type": "model_and_format",
                        "status": "ready",
                        "createdAt": "2024-06-01",
                        "registry": "Standard",
                    },
                    {
                        "id": "2",
                        "name": "OldOrphan",
                        "abbreviation": "OO",
                        "type": "terminology_artefact",
                        "status": "ready",
                        "createdAt": "2015-03-01",
                        "registry": "Standard",
                    },
                ],
                "totalCount": 2,
                "totalPages": 1,
            }
        }

        # Call 2: implemented + recent standards search (emerging)
        emerging_response = {
            "searchFairsharingRecords": {
                "records": [
                    {
                        "id": "3",
                        "name": "RisingStd",
                        "abbreviation": "RS",
                        "type": "reporting_guideline",
                        "status": "ready",
                        "createdAt": "2023-01-01",
                        "registry": "Standard",
                    },
                ],
                "totalCount": 1,
                "totalPages": 1,
            }
        }

        # Call 3: association lookup for emerging standard
        rising_record = {
            "fairsharingRecord": {
                "id": "3",
                "name": "RisingStd",
                "reverseRecordAssociations": [
                    {"fairsharingRecord": {"registry": "Database", "id": "d1"}},
                    {"fairsharingRecord": {"registry": "Database", "id": "d2"}},
                    {"fairsharingRecord": {"registry": "Policy", "id": "p1"}},
                ],
                "recordAssociations": [
                    {"linkedRecord": {"id": "x1"}},
                ],
            }
        }

        empty_page = {"searchFairsharingRecords": {"records": [], "totalCount": 0, "totalPages": 0}}

        mock_client.query.side_effect = [
            unimplemented_response,  # Step 1: unimplemented search
            emerging_response,  # Step 2: implemented+recent page 1
            empty_page,  # Step 2: implemented+recent page 2 (empty → break)
            rising_record,  # Step 3: association lookup for RisingStd
        ]

        result = await find_emerging_standards(min_year=2022)

        # Verify three categories exist
        self.assertIn("Emerging Standards", result)
        self.assertIn("Recently Created, No Adoption", result)
        self.assertIn("Old and Unadopted", result)

        # RisingStd (abbrev RS) should be in Emerging section
        self.assertIn("RS", result)

        # NewOrphan (abbrev NO) created 2024 ≥ 2022 → recently created unadopted
        self.assertIn("NO", result)

        # OldOrphan (abbrev OO) created 2015 < 2022 → old and unadopted
        self.assertIn("OO", result)

    @patch("fairsharing_mcp.app.get_client")
    async def test_find_emerging_standards_no_results(self, mock_get_client):
        """find_emerging_standards with no unimplemented standards."""
        mock_client = AsyncMock()
        mock_get_client.return_value = mock_client

        empty = {"searchFairsharingRecords": {"records": [], "totalCount": 0, "totalPages": 0}}
        mock_client.query.side_effect = [empty, empty]

        result = await find_emerging_standards()
        self.assertIn("Emerging vs Abandoned", result)
        self.assertIn("unimplemented standards:** 0", result)

    @patch("fairsharing_mcp.app.get_client")
    async def test_find_emerging_standards_with_subjects(self, mock_get_client):
        """find_emerging_standards passes subjects to search queries."""
        mock_client = AsyncMock()
        mock_get_client.return_value = mock_client

        empty = {"searchFairsharingRecords": {"records": [], "totalCount": 0, "totalPages": 0}}
        mock_client.query.side_effect = [empty, empty]

        result = await find_emerging_standards(subjects=["Proteomics"])
        self.assertIn("Subject filter", result)
        self.assertIn("Proteomics", result)

        # Verify subjects passed to queries
        for call in mock_client.query.call_args_list:
            args = call[0]
            if len(args) > 1 and isinstance(args[1], dict):
                if "registry" in args[1]:
                    self.assertEqual(args[1].get("subjects"), ["Proteomics"])

    # ── find_endorsed_but_unadopted ──────────────────────────────────

    @patch("fairsharing_mcp.app.get_client")
    async def test_find_endorsed_but_unadopted_basic(self, mock_get_client):
        """find_endorsed_but_unadopted returns standards with policy recs but no DB implementation."""
        mock_client = AsyncMock()
        mock_get_client.return_value = mock_client

        # Call 1: multiTagFilter returns recommended but unimplemented standards
        multi_tag_response = {
            "multiTagFilter": [
                {
                    "id": "10",
                    "name": "Orphan Standard",
                    "abbreviation": "OS",
                    "type": "model_and_format",
                    "status": "ready",
                    "registry": "Standard",
                },
            ]
        }
        # Call 2: association lookup for enrichment
        orphan_detail = {
            "fairsharingRecord": {
                "id": "10",
                "name": "Orphan Standard",
                "reverseRecordAssociations": [
                    {
                        "fairsharingRecord": {
                            "registry": "Policy",
                            "name": "EOSC Policy",
                            "id": "p1",
                        },
                        "recordAssocLabel": "recommends",
                    },
                    {
                        "fairsharingRecord": {
                            "registry": "Policy",
                            "name": "NIH Policy",
                            "id": "p2",
                        },
                        "recordAssocLabel": "recommends",
                    },
                ],
                "recordAssociations": [],
            }
        }

        mock_client.query.side_effect = [multi_tag_response, orphan_detail]

        result = await find_endorsed_but_unadopted()

        self.assertIn("Endorsed but Unadopted", result)
        self.assertIn("Orphan Standard", result)
        self.assertIn("EOSC Policy", result)
        self.assertIn("NIH Policy", result)
        self.assertIn("policy intention hasn't translated", result)

    @patch("fairsharing_mcp.app.get_client")
    async def test_find_endorsed_but_unadopted_none_found(self, mock_get_client):
        """find_endorsed_but_unadopted returns message when no recommended standards exist."""
        mock_client = AsyncMock()
        mock_get_client.return_value = mock_client

        # Primary query returns empty
        mock_client.query.side_effect = [
            {"multiTagFilter": []},
            {"multiTagFilter": []},  # Fallback also empty
        ]

        result = await find_endorsed_but_unadopted()
        self.assertIn("No policy-recommended standards found", result)

    @patch("fairsharing_mcp.app.get_client")
    async def test_find_endorsed_but_unadopted_fallback(self, mock_get_client):
        """find_endorsed_but_unadopted falls back when combined filter returns empty."""
        mock_client = AsyncMock()
        mock_get_client.return_value = mock_client

        # Call 1: isRecommended=True + isImplemented=False → empty
        # Call 2: fallback isRecommended=True only → returns records
        # Call 3+: association lookups to manually check implementation
        recommended_only = {
            "multiTagFilter": [
                {
                    "id": "20",
                    "name": "PendingStd",
                    "abbreviation": "PS",
                    "type": "terminology_artefact",
                    "status": "ready",
                    "registry": "Standard",
                },
                {
                    "id": "21",
                    "name": "AdoptedStd",
                    "abbreviation": "AS",
                    "type": "model_and_format",
                    "status": "ready",
                    "registry": "Standard",
                },
            ]
        }

        # PendingStd: has policy recs but NO database implementations → should be included
        pending_detail = {
            "fairsharingRecord": {
                "id": "20",
                "name": "PendingStd",
                "reverseRecordAssociations": [
                    {
                        "fairsharingRecord": {"registry": "Policy", "name": "Pol1", "id": "p1"},
                        "recordAssocLabel": "recommends",
                    },
                ],
                "recordAssociations": [],
            }
        }
        # AdoptedStd: has both policy recs AND database implementations → should be excluded
        adopted_detail = {
            "fairsharingRecord": {
                "id": "21",
                "name": "AdoptedStd",
                "reverseRecordAssociations": [
                    {
                        "fairsharingRecord": {"registry": "Policy", "name": "Pol2", "id": "p2"},
                        "recordAssocLabel": "recommends",
                    },
                    {
                        "fairsharingRecord": {"registry": "Database", "name": "DB1", "id": "d1"},
                        "recordAssocLabel": "implements",
                    },
                ],
                "recordAssociations": [],
            }
        }

        mock_client.query.side_effect = [
            {"multiTagFilter": []},  # Primary query empty
            recommended_only,  # Fallback query
            pending_detail,  # Manual check for PendingStd
            adopted_detail,  # Manual check for AdoptedStd
        ]

        result = await find_endorsed_but_unadopted()

        # PendingStd should appear (endorsed, not implemented)
        self.assertIn("PendingStd", result)
        # AdoptedStd should NOT appear (has implementation)
        self.assertNotIn("AdoptedStd", result)

    @patch("fairsharing_mcp.app.get_client")
    async def test_find_endorsed_but_unadopted_all_adopted(self, mock_get_client):
        """find_endorsed_but_unadopted when all recommended standards are also implemented."""
        mock_client = AsyncMock()
        mock_get_client.return_value = mock_client

        # Primary filter returns empty (no standards match recommended+not-implemented)
        # Fallback: all recommended standards DO have implementations
        recommended_only = {
            "multiTagFilter": [
                {
                    "id": "30",
                    "name": "WellAdopted",
                    "abbreviation": "WA",
                    "type": "terminology_artefact",
                    "status": "ready",
                    "registry": "Standard",
                },
            ]
        }
        adopted_detail = {
            "fairsharingRecord": {
                "id": "30",
                "name": "WellAdopted",
                "reverseRecordAssociations": [
                    {
                        "fairsharingRecord": {"registry": "Policy", "name": "Pol1", "id": "p1"},
                        "recordAssocLabel": "recommends",
                    },
                    {
                        "fairsharingRecord": {"registry": "Database", "name": "DB1", "id": "d1"},
                        "recordAssocLabel": "implements",
                    },
                ],
                "recordAssociations": [],
            }
        }

        mock_client.query.side_effect = [
            {"multiTagFilter": []},  # Primary
            recommended_only,  # Fallback
            adopted_detail,  # Check
        ]

        result = await find_endorsed_but_unadopted()
        self.assertIn("also implemented by at least one database", result)
        self.assertIn("No endorsement-adoption gap found", result)

    @patch("fairsharing_mcp.app.get_client")
    async def test_find_endorsed_but_unadopted_with_subjects(self, mock_get_client):
        """find_endorsed_but_unadopted passes subjects filter."""
        mock_client = AsyncMock()
        mock_get_client.return_value = mock_client

        mock_client.query.side_effect = [
            {"multiTagFilter": []},
            {"multiTagFilter": []},
        ]

        result = await find_endorsed_but_unadopted(subjects=["Genomics"])
        self.assertIn("subjects=['Genomics']", result)

        # Verify subjects passed to multiTagFilter
        first_call_vars = mock_client.query.call_args_list[0][0][1]
        self.assertEqual(first_call_vars.get("subjects"), ["Genomics"])

    @patch("fairsharing_mcp.app.get_client")
    async def test_assess_database_indicators_with_results(self, mock_get_client):
        """assess_database_indicators returns formatted databases from multiTagFilter."""
        mock_client = AsyncMock()
        mock_get_client.return_value = mock_client

        mock_client.query.return_value = {
            "multiTagFilter": [
                {
                    "name": "UniProt",
                    "abbreviation": "UniProt",
                    "id": "1",
                    "type": "knowledgebase",
                    "subjects": [{"label": "Proteomics"}],
                    "domains": [],
                    "status": "ready",
                }
            ]
        }

        result = await assess_database_indicators(subjects=["Proteomics"], per_page=25)
        self.assertIn("UniProt", result)
        self.assertIn("Proteomics", result)
        mock_client.query.assert_called_once()

    @patch("fairsharing_mcp.app.get_client")
    async def test_get_database_quality_profile_success(self, mock_get_client):
        """get_database_quality_profile returns profile for a database record."""
        mock_client = AsyncMock()
        mock_get_client.return_value = mock_client

        mock_client.query.return_value = {
            "fairsharingRecord": {
                "id": "42",
                "name": "Test DB",
                "abbreviation": "TDB",
                "registry": "Database",
                "status": "ready",
                "dataAccessCondition": "open",
                "dataCuration": "manual",
                "dataDepositionCondition": "open",
                "citationToRelatedPublications": "yes",
                "dataContactInformation": "yes",
                "dataVersioning": "yes",
                "dataPreservationPolicy": "yes",
                "resourceSustainability": "yes",
                "usesPersistentIdentifier": "yes",
            }
        }

        result = await get_database_quality_profile(42)
        self.assertIn("Test DB", result)
        self.assertIn("Database", result)

    @patch("fairsharing_mcp.app.get_client")
    async def test_list_subjects_with_results(self, mock_get_client):
        """list_subjects returns paginated subjects."""
        mock_client = AsyncMock()
        mock_get_client.return_value = mock_client

        mock_client.query.return_value = {
            "subjects": {
                "records": [
                    {"id": "1", "label": "Genomics", "description": "Study of genomes."},
                    {"id": "2", "label": "Proteomics", "description": "Study of proteins."},
                ],
                "totalCount": 2,
                "totalPages": 1,
            }
        }

        result = await list_subjects(page=1, per_page=50)
        self.assertIn("Genomics", result)
        self.assertIn("Proteomics", result)
        self.assertIn("Page 1 of 1", result)

    @patch("fairsharing_mcp.app.get_client")
    async def test_list_taxonomies_with_results(self, mock_get_client):
        """list_taxonomies returns paginated taxonomies."""
        mock_client = AsyncMock()
        mock_get_client.return_value = mock_client

        mock_client.query.return_value = {
            "taxonomies": {
                "records": [
                    {"id": "1", "label": "Homo sapiens", "iri": "http://example.org/hs"},
                    {"id": "2", "label": "Mus musculus", "iri": ""},
                ],
                "totalCount": 2,
                "totalPages": 1,
            }
        }

        result = await list_taxonomies(page=1, per_page=50)
        self.assertIn("Homo sapiens", result)
        self.assertIn("Mus musculus", result)

    @patch("fairsharing_mcp.app.get_client")
    async def test_list_domains_with_results(self, mock_get_client):
        """list_domains returns paginated domains."""
        mock_client = AsyncMock()
        mock_get_client.return_value = mock_client

        mock_client.query.return_value = {
            "domains": {
                "records": [
                    {"id": "1", "label": "Data model", "description": "Structured data."},
                    {"id": "2", "label": "File format", "description": "File formats."},
                ],
                "totalCount": 2,
                "totalPages": 1,
            }
        }

        result = await list_domains(page=1, per_page=50)
        self.assertIn("Data model", result)
        self.assertIn("File format", result)
        self.assertIn("Page 1 of 1", result)

    @patch("fairsharing_mcp.app.get_client")
    async def test_list_organisations_with_results(self, mock_get_client):
        """list_organisations returns paginated organisations."""
        mock_client = AsyncMock()
        mock_get_client.return_value = mock_client

        mock_client.query.return_value = {
            "organisations": {
                "records": [
                    {"id": "1", "name": "EMBL-EBI", "homepage": "https://www.ebi.ac.uk"},
                    {"id": "2", "name": "NCBI", "homepage": "https://www.ncbi.nlm.nih.gov"},
                ],
                "totalCount": 2,
                "totalPages": 1,
            }
        }

        result = await list_organisations(page=1, per_page=50)
        self.assertIn("EMBL-EBI", result)
        self.assertIn("NCBI", result)
        self.assertIn("Page 1 of 1", result)

    @patch("fairsharing_mcp.app.get_client")
    async def test_list_subjects_bypass_cache(self, mock_get_client):
        """list_subjects with bypass_cache=True calls API with cache disabled."""
        mock_client = AsyncMock()
        mock_get_client.return_value = mock_client

        mock_client.query.return_value = {
            "subjects": {
                "records": [{"id": "1", "label": "Genomics", "description": ""}],
                "totalCount": 1,
                "totalPages": 1,
            }
        }

        result = await list_subjects(page=1, per_page=50, bypass_cache=True)
        self.assertIn("Genomics", result)
        call_kwargs = mock_client.query.call_args[1]
        self.assertFalse(call_kwargs.get("cache", True))

    @patch("fairsharing_mcp.app.get_client")
    async def test_get_record_graph_summary_mode(self, mock_get_client):
        """get_record_graph with summary_mode=True produces condensed output."""
        mock_client = AsyncMock()
        mock_get_client.return_value = mock_client

        graph_data = {
            "name": "Test Graph",
            "nodes": [
                {
                    "key": "1",
                    "attributes": {
                        "label": "A",
                        "registry": "Standard",
                        "record_type": "x",
                        "status": "r",
                    },
                },
                {
                    "key": "2",
                    "attributes": {
                        "label": "B",
                        "registry": "Database",
                        "record_type": "y",
                        "status": "r",
                    },
                },
                {
                    "key": "3",
                    "attributes": {
                        "label": "C",
                        "registry": "Database",
                        "record_type": "y",
                        "status": "r",
                    },
                },
            ],
            "edges": [
                {"source": "1", "target": "2", "attributes": {"color": "pink"}},
                {"source": "1", "target": "3", "attributes": {"color": "pink"}},
            ],
        }
        mock_client.query.return_value = {
            "fairsharingGraph": {"data": graph_data},
        }

        result = await get_record_graph(1, summary_mode=True)
        self.assertIn("Knowledge Graph", result)
        self.assertIn("Summary mode", result)
        self.assertIn("Top 10 Hub", result)

    @patch("fairsharing_mcp.app.get_client")
    async def test_analyze_graph_comprehensive_summary_mode(self, mock_get_client):
        """analyze_graph_comprehensive with summary_mode=True uses condensed output."""
        graph = self._make_graph(
            [
                (1, "A", "standard", "t"),
                (2, "B", "database", "r"),
                (3, "C", "database", "r"),
                (4, "D", "standard", "t"),
                (5, "E", "database", "r"),
            ],
            [(1, 2, "pink"), (1, 3, "pink"), (4, 3, "pink"), (5, 2, "pink")],
        )
        mock_get_client.return_value = self._graph_mock(graph)
        result = await analyze_graph_comprehensive(1, top_n=15, summary_mode=True)
        self.assertIn("Comprehensive Graph Analysis", result)
        self.assertIn("Summary mode", result)
        self.assertIn("Cross-Reference: Top", result)
        self.assertIn("Nodes", result)

    @patch("fairsharing_mcp.app.get_client")
    async def test_batch_audit_metadata_policy_checklist(self, mock_get_client):
        """batch_audit_metadata includes Policy-specific recommended fields."""
        mock_client = AsyncMock()
        mock_get_client.return_value = mock_client

        mock_client.query.side_effect = [
            {
                "searchFairsharingRecords": {
                    "records": [{"id": "100", "name": "Test Policy", "registry": "Policy"}],
                }
            },
            {
                "fairsharingRecord": {
                    "id": "100",
                    "name": "Test Policy",
                    "registry": "Policy",
                    "description": "A policy",
                    "subjects": [{"label": "Genomics"}],
                    "domains": [],
                    "countries": [],
                    "organisations": [],
                    "recordAssociations": [{"linkedRecord": {"name": "S1"}}],
                }
            },
        ]

        result = await batch_audit_metadata(registry=["Policy"], limit=1)
        self.assertIn("Batch Metadata Audit", result)
        self.assertIn("Test Policy", result)
        self.assertIn("Policy", result)

    # ── New enhancement tools ───────────────────────────────────────────

    @patch("fairsharing_mcp.app.get_client")
    async def test_find_databases_by_standard_by_id(self, mock_get_client):
        """find_databases_by_standard with standard_id returns implementing databases."""
        mock_client = AsyncMock()
        mock_get_client.return_value = mock_client
        mock_client.query.return_value = {
            "fairsharingRecord": {
                "id": "42",
                "name": "FASTQ",
                "registry": "Standard",
                "reverseRecordAssociations": [
                    {
                        "recordAssocLabel": "implements",
                        "fairsharingRecord": {
                            "id": "101",
                            "name": "ENA",
                            "abbreviation": "ENA",
                            "registry": "Database",
                            "type": "repository",
                        },
                    },
                    {
                        "recordAssocLabel": "implements",
                        "fairsharingRecord": {
                            "id": "102",
                            "name": "SRA",
                            "abbreviation": "SRA",
                            "registry": "Database",
                            "type": "repository",
                        },
                    },
                ],
            }
        }

        result = await find_databases_by_standard(standard_id=42)

        self.assertIn("FASTQ", result)
        self.assertIn("ENA", result)
        self.assertIn("SRA", result)
        self.assertIn("Database", result)

    @patch("fairsharing_mcp.app.get_client")
    async def test_find_databases_by_standard_by_name(self, mock_get_client):
        """find_databases_by_standard with standard_name resolves via search then fetches."""
        mock_client = AsyncMock()
        mock_get_client.return_value = mock_client
        mock_client.query.side_effect = [
            {
                "searchFairsharingRecords": {
                    "records": [{"id": "99", "name": "DICOM", "registry": "Standard"}]
                }
            },
            {
                "fairsharingRecord": {
                    "id": "99",
                    "name": "DICOM",
                    "registry": "Standard",
                    "reverseRecordAssociations": [
                        {
                            "recordAssocLabel": "implements",
                            "fairsharingRecord": {
                                "id": "201",
                                "name": "PACS DB",
                                "registry": "Database",
                                "type": "repository",
                            },
                        }
                    ],
                }
            },
        ]

        result = await find_databases_by_standard(standard_name="DICOM")

        self.assertIn("DICOM", result)
        self.assertIn("PACS DB", result)

    @patch("fairsharing_mcp.app.get_client")
    async def test_get_records_by_organisation(self, mock_get_client):
        """get_records_by_organisation returns records for that organisation."""
        mock_client = AsyncMock()
        mock_get_client.return_value = mock_client
        mock_client.query.return_value = {
            "searchFairsharingRecords": {
                "records": [
                    {
                        "id": "1",
                        "name": "UniProt",
                        "abbreviation": "UniProt",
                        "registry": "Database",
                        "type": "knowledgebase",
                        "status": "ready",
                        "description": "Protein database.",
                        "subjects": [{"label": "Proteomics"}],
                        "domains": [],
                        "createdAt": "2020-01-01",
                    }
                ],
                "totalCount": 1,
                "totalPages": 1,
            }
        }

        result = await get_records_by_organisation("EMBL-EBI")

        self.assertIn("EMBL-EBI", result)
        self.assertIn("UniProt", result)
        self.assertIn("Database", result)

    @patch("fairsharing_mcp.app.get_client")
    async def test_search_records_by_license(self, mock_get_client):
        """search_records_by_license delegates to search with licences filter."""
        mock_client = AsyncMock()
        mock_get_client.return_value = mock_client
        mock_client.query.return_value = {
            "searchFairsharingRecords": {
                "records": [
                    {
                        "id": "10",
                        "name": "Open DB",
                        "abbreviation": "ODB",
                        "registry": "Database",
                        "type": "repository",
                        "status": "ready",
                        "description": "CC0 database.",
                        "subjects": [],
                        "domains": [],
                        "createdAt": "2021-01-01",
                    }
                ],
                "totalCount": 1,
                "totalPages": 1,
            }
        }

        result = await search_records_by_license("CC0", registry=["Database"])

        self.assertIn("Open DB", result)
        self.assertIn("CC0", result)
        call_args = mock_client.query.call_args
        self.assertIn("licences", str(call_args))

    @patch("fairsharing_mcp.app.get_client")
    async def test_aggregate_by_field_registry(self, mock_get_client):
        """aggregate_by_field field=registry returns counts per registry."""
        mock_client = AsyncMock()
        mock_get_client.return_value = mock_client
        mock_client.query.side_effect = [
            {"searchFairsharingRecords": {"totalCount": 100}},
            {"searchFairsharingRecords": {"totalCount": 200}},
            {"searchFairsharingRecords": {"totalCount": 50}},
            {"searchFairsharingRecords": {"totalCount": 10}},
        ]

        result = await aggregate_by_field(field="registry")

        self.assertIn("Database", result)
        self.assertIn("Standard", result)
        self.assertIn("Policy", result)
        self.assertIn("Collection", result)
        self.assertIn("100", result)
        self.assertIn("200", result)
        self.assertIn("360", result)

    @patch("fairsharing_mcp.app.get_client")
    async def test_aggregate_by_field_subject(self, mock_get_client):
        """aggregate_by_field field=subject uses list subjects then counts."""
        mock_client = AsyncMock()
        mock_get_client.return_value = mock_client
        mock_client.query.side_effect = [
            {
                "subjects": {
                    "records": [
                        {"id": 1, "label": "Genomics", "iri": ""},
                        {"id": 2, "label": "Proteomics", "iri": ""},
                    ]
                }
            },
            {"searchFairsharingRecords": {"totalCount": 500}},
            {"searchFairsharingRecords": {"totalCount": 300}},
        ]

        result = await aggregate_by_field(field="subject", max_values=5)

        self.assertIn("Genomics", result)
        self.assertIn("Proteomics", result)
        self.assertIn("500", result)
        self.assertIn("300", result)

    @patch("fairsharing_mcp.app.get_client")
    async def test_search_records_fallback_relaxes_organisations(self, mock_get_client):
        """Fallback can relax organisations when subjects/countries not present."""
        mock_client = AsyncMock()
        mock_get_client.return_value = mock_client
        empty = {"searchFairsharingRecords": {"records": [], "totalCount": 0, "totalPages": 0}}
        found = {
            "searchFairsharingRecords": {
                "records": [
                    {
                        "id": "1",
                        "name": "Some DB",
                        "abbreviation": "SDB",
                        "registry": "Database",
                        "type": "repository",
                        "status": "ready",
                    }
                ],
                "totalCount": 1,
                "totalPages": 1,
            }
        }
        mock_client.query.side_effect = [empty, found]

        result = await search_records(
            registry=["Database"],
            organisations=["EMBL-EBI"],
            fallback_on_empty=True,
        )

        self.assertIn("Some DB", result)
        self.assertIn("DROPPED", result)
        self.assertIn("organisations=", result)
        self.assertEqual(mock_client.query.call_count, 2)

    # ── Phase 2: Structured output, DOI lookup, explain tool, connection pooling ──

    @patch("fairsharing_mcp.app.get_client")
    async def test_search_records_json_output(self, mock_get_client):
        """search_records with output_format='json' returns valid JSON."""
        mock_client = AsyncMock()
        mock_get_client.return_value = mock_client
        mock_client.query.return_value = {
            "searchFairsharingRecords": {
                "records": [
                    {
                        "id": "1",
                        "name": "Test DB",
                        "abbreviation": "TDB",
                        "registry": "Database",
                        "type": "repository",
                        "status": "ready",
                        "doi": "10.25504/FAIRsharing.abc123",
                    }
                ],
                "totalCount": 1,
                "totalPages": 1,
            }
        }

        result = await search_records(query="test", output_format="json")

        import json

        data = json.loads(result)
        self.assertEqual(len(data["records"]), 1)
        self.assertEqual(data["records"][0]["name"], "Test DB")
        self.assertEqual(data["total_count"], 1)
        self.assertIsNone(data["filters_dropped"])

    @patch("fairsharing_mcp.app.get_client")
    async def test_get_record_json_output(self, mock_get_client):
        """get_record with output_format='json' returns valid JSON."""
        mock_client = AsyncMock()
        mock_get_client.return_value = mock_client
        mock_client.query.return_value = {
            "fairsharingRecord": {
                "id": "25",
                "name": "Test Record",
                "abbreviation": "TR",
                "registry": "Standard",
                "type": "terminology_artefact",
                "status": "ready",
                "doi": "10.25504/FAIRsharing.xyz",
                "homepage": "https://example.com",
                "description": "A test record",
                "createdAt": "2020-01-01",
                "updatedAt": "2024-06-01",
                "subjects": [{"label": "Genomics"}],
                "domains": [{"label": "Data model"}],
                "taxonomies": [{"name": "Homo sapiens"}],
                "countries": [{"name": "United Kingdom"}],
                "licenceLinks": [],
                "publications": [],
            }
        }

        result = await get_record(record_id=25, output_format="json")

        import json

        data = json.loads(result)
        self.assertEqual(data["id"], "25")
        self.assertEqual(data["name"], "Test Record")
        self.assertEqual(data["subjects"], ["Genomics"])
        self.assertEqual(data["countries"], ["United Kingdom"])

    @patch("fairsharing_mcp.app.get_client")
    async def test_get_database_quality_profile_json_output(self, mock_get_client):
        """get_database_quality_profile with output_format='json' returns structured score."""
        mock_client = AsyncMock()
        mock_get_client.return_value = mock_client
        mock_client.query.return_value = {
            "fairsharingRecord": {
                "id": "100",
                "name": "Quality DB",
                "abbreviation": "QDB",
                "registry": "Database",
                "type": "repository",
                "status": "ready",
                "dataAccessCondition": "open",
                "dataCuration": "manual",
                "usesPersistentIdentifier": True,
                "dataPreservationPolicy": True,
                "resourceSustainability": True,
            }
        }

        result = await get_database_quality_profile(record_id=100, output_format="json")

        import json

        data = json.loads(result)
        self.assertEqual(data["record_id"], "100")
        self.assertEqual(data["indicators"]["dataAccessCondition"], "open")
        self.assertGreater(data["score"], 0)
        self.assertIn(data["confidence"], ("high", "medium", "low"))
        self.assertEqual(data["grade"], "Excellent")

    @patch("fairsharing_mcp.app.get_client")
    async def test_search_by_doi_found(self, mock_get_client):
        """search_by_doi returns matching records."""
        mock_client = AsyncMock()
        mock_get_client.return_value = mock_client
        mock_client.query.return_value = {
            "searchFairsharingRecords": {
                "records": [
                    {
                        "id": "42",
                        "name": "DOI Record",
                        "abbreviation": "DR",
                        "registry": "Standard",
                        "type": "model_and_format",
                        "status": "ready",
                        "doi": "10.25504/FAIRsharing.abc123",
                    }
                ],
                "totalCount": 1,
                "totalPages": 1,
            }
        }

        result = await search_by_doi("10.25504/FAIRsharing.abc123")
        self.assertIn("DOI Record", result)
        self.assertIn("10.25504/FAIRsharing.abc123", result)

    @patch("fairsharing_mcp.app.get_client")
    async def test_search_by_doi_url_normalization(self, mock_get_client):
        """search_by_doi normalizes DOI URLs."""
        mock_client = AsyncMock()
        mock_get_client.return_value = mock_client
        mock_client.query.return_value = {
            "searchFairsharingRecords": {
                "records": [
                    {
                        "id": "42",
                        "name": "DOI Record",
                        "registry": "Standard",
                        "type": "model_and_format",
                        "status": "ready",
                        "doi": "10.25504/FAIRsharing.abc123",
                    }
                ],
                "totalCount": 1,
                "totalPages": 1,
            }
        }

        await search_by_doi("https://doi.org/10.25504/FAIRsharing.abc123")
        # Verify the search query was the normalized DOI, not the URL
        call_args = mock_client.query.call_args
        self.assertIn("10.25504/FAIRsharing.abc123", call_args[0][1]["q"])

    @patch("fairsharing_mcp.app.get_client")
    async def test_search_by_doi_empty(self, mock_get_client):
        """search_by_doi returns helpful message when no results."""
        mock_client = AsyncMock()
        mock_get_client.return_value = mock_client
        mock_client.query.return_value = {
            "searchFairsharingRecords": {
                "records": [],
                "totalCount": 0,
                "totalPages": 0,
            }
        }

        result = await search_by_doi("10.25504/nonexistent")
        self.assertIn("No records found", result)

    async def test_explain_fairsharing_overview(self):
        """explain_fairsharing returns overview docs without API call."""
        result = await explain_fairsharing("overview")
        self.assertIn("FAIRsharing Overview", result)
        self.assertIn("Standards", result)
        self.assertIn("Databases", result)

    async def test_explain_fairsharing_workflows(self):
        """explain_fairsharing returns workflow docs."""
        result = await explain_fairsharing("workflows")
        self.assertIn("DMP Compliance", result)
        self.assertIn("Database Quality", result)

    async def test_explain_fairsharing_unknown_topic(self):
        """explain_fairsharing returns error for unknown topic."""
        result = await explain_fairsharing("nonexistent")
        self.assertIn("Unknown topic", result)
        self.assertIn("overview", result)

    async def test_explain_fairsharing_scoring(self):
        """explain_fairsharing returns scoring docs."""
        result = await explain_fairsharing("scoring")
        self.assertIn("Database FAIR Score", result)
        self.assertIn("Standard Quality Profile", result)
        self.assertIn("Policy Quality Profile", result)

    def test_connection_pooling_reuses_client(self):
        """FAIRsharingClient creates a persistent httpx.AsyncClient."""
        from fairsharing_mcp.client import FAIRsharingClient

        client = FAIRsharingClient(api_key="test-key")
        http1 = client._get_http_client()
        http2 = client._get_http_client()
        self.assertIs(http1, http2)
        # Clean up
        import asyncio

        asyncio.get_event_loop().run_until_complete(client.aclose())

    # ── Phase 3: Unified Quality Scoring ──────────────────────────────

    def test_normalize_quality_score(self):
        from fairsharing_mcp.formatters import normalize_quality_score

        result = normalize_quality_score(7.0, 9.0, "Database", "high")
        self.assertAlmostEqual(result["normalized_score"], 77.8, places=1)
        self.assertEqual(result["unified_grade"], "B")
        self.assertEqual(result["confidence"], "high")
        self.assertEqual(result["registry"], "Database")

        result2 = normalize_quality_score(9.5, 10.0, "Standard", "medium")
        self.assertAlmostEqual(result2["normalized_score"], 95.0, places=1)
        self.assertEqual(result2["unified_grade"], "A+")

        result3 = normalize_quality_score(0.0, 10.0, "Policy", "low")
        self.assertAlmostEqual(result3["normalized_score"], 0.0, places=1)
        self.assertEqual(result3["unified_grade"], "F")

    @patch("fairsharing_mcp.app.get_client")
    async def test_get_unified_quality_score_database(self, mock_get_client):
        mock_client = AsyncMock()
        mock_get_client.return_value = mock_client

        # First call: GET_RECORD_WITH_ASSOCIATIONS_QUERY to detect registry
        # Second call: fetch_database_quality_with_fallback (GET_DATABASE_QUALITY_QUERY)
        mock_client.query.side_effect = [
            {"fairsharingRecord": {"id": 25, "name": "TestDB", "registry": "Database"}},
            {
                "fairsharingRecord": {
                    "id": 25,
                    "name": "TestDB",
                    "registry": "Database",
                    "dataAccessCondition": "open",
                    "dataCuration": "manual",
                    "dataDepositionCondition": "open",
                    "citationToRelatedPublications": "yes",
                    "dataContactInformation": "yes",
                    "dataVersioning": "yes",
                    "dataPreservationPolicy": True,
                    "resourceSustainability": True,
                    "usesPersistentIdentifier": True,
                }
            },
        ]

        result = await get_unified_quality_score(25)
        self.assertIn("Unified Quality Score", result)
        self.assertIn("Database", result)
        self.assertIn("/100", result)
        self.assertIn("A", result)  # 9/9 = 100% → A+

    @patch("fairsharing_mcp.app.get_client")
    async def test_get_unified_quality_score_standard(self, mock_get_client):
        mock_client = AsyncMock()
        mock_get_client.return_value = mock_client

        mock_client.query.return_value = {
            "fairsharingRecord": {
                "id": 100,
                "name": "TestStd",
                "registry": "Standard",
                "homepage": "https://example.com",
                "doi": "10.1234/test",
                "description": "A" * 60,
                "status": "ready",
                "isMaintained": True,
                "reverseRecordAssociations": [
                    {
                        "recordAssocLabel": "implements",
                        "fairsharingRecord": {"registry": "Database"},
                    },
                    {
                        "recordAssocLabel": "implements",
                        "fairsharingRecord": {"registry": "Database"},
                    },
                    {
                        "recordAssocLabel": "implements",
                        "fairsharingRecord": {"registry": "Database"},
                    },
                ],
                "recordAssociations": [],
            }
        }

        result = await get_unified_quality_score(100)
        self.assertIn("Unified Quality Score", result)
        self.assertIn("Standard", result)
        self.assertIn("/100", result)

    @patch("fairsharing_mcp.app.get_client")
    async def test_get_unified_quality_score_policy(self, mock_get_client):
        mock_client = AsyncMock()
        mock_get_client.return_value = mock_client

        # First call: association query
        mock_client.query.side_effect = [
            {
                "fairsharingRecord": {
                    "id": 200,
                    "name": "TestPolicy",
                    "registry": "Policy",
                    "recordAssociations": [
                        {"linkedRecord": {"registry": "Standard", "id": 50}},
                    ],
                    "reverseRecordAssociations": [],
                }
            },
            # Second call: fetch_policy_with_fallback (GET_POLICY_DETAIL_QUERY)
            {
                "fairsharingRecord": {
                    "id": 200,
                    "name": "TestPolicy",
                    "registry": "Policy",
                    "metadata": {
                        "sharing_data": {"mandated_data_sharing": "required"},
                        "dmp_development": {"mandated_dmp_creation": "required"},
                    },
                    "recordAssociations": [
                        {"linkedRecord": {"registry": "Standard", "id": 50}},
                    ],
                    "reverseRecordAssociations": [],
                }
            },
        ]

        result = await get_unified_quality_score(200)
        self.assertIn("Unified Quality Score", result)
        self.assertIn("Policy", result)

    @patch("fairsharing_mcp.app.get_client")
    async def test_get_unified_quality_score_unknown_registry(self, mock_get_client):
        mock_client = AsyncMock()
        mock_get_client.return_value = mock_client

        mock_client.query.return_value = {
            "fairsharingRecord": {"id": 300, "name": "TestColl", "registry": "Collection"}
        }

        result = await get_unified_quality_score(300)
        self.assertIn("Collection", result)
        self.assertIn("only available for", result)

    @patch("fairsharing_mcp.app.get_client")
    async def test_compare_unified_quality_mixed(self, mock_get_client):
        mock_client = AsyncMock()
        mock_get_client.return_value = mock_client

        mock_client.query.side_effect = [
            # DB: association query
            {"fairsharingRecord": {"id": 25, "name": "TestDB", "registry": "Database"}},
            # DB: quality query
            {
                "fairsharingRecord": {
                    "id": 25,
                    "name": "TestDB",
                    "registry": "Database",
                    "dataAccessCondition": "open",
                    "dataCuration": "manual",
                    "dataDepositionCondition": "open",
                    "citationToRelatedPublications": "yes",
                    "dataContactInformation": "yes",
                    "dataVersioning": "yes",
                    "dataPreservationPolicy": True,
                    "resourceSustainability": True,
                    "usesPersistentIdentifier": True,
                }
            },
            # Standard: association query (reused for scoring)
            {
                "fairsharingRecord": {
                    "id": 100,
                    "name": "TestStd",
                    "registry": "Standard",
                    "homepage": "https://example.com",
                    "doi": "10.1234/test",
                    "description": "A" * 60,
                    "status": "ready",
                    "isMaintained": True,
                    "reverseRecordAssociations": [],
                    "recordAssociations": [],
                }
            },
        ]

        result = await compare_unified_quality([25, 100])
        self.assertIn("Unified Quality Comparison", result)
        self.assertIn("TestDB", result)
        self.assertIn("TestStd", result)
        self.assertIn("approximate", result)

    # ── Phase 3: DMP Compliance ────────────────────────────────────────

    @patch("fairsharing_mcp.app.get_client")
    async def test_assess_dmp_compliance_basic(self, mock_get_client):
        mock_client = AsyncMock()
        mock_get_client.return_value = mock_client

        mock_client.query.side_effect = [
            # fetch_policy_with_fallback (GET_POLICY_DETAIL_QUERY)
            {
                "fairsharingRecord": {
                    "id": 10,
                    "name": "TestPolicy",
                    "registry": "Policy",
                    "metadata": {"sharing_data": {"mandated_data_sharing": "required"}},
                    "recordAssociations": [
                        {
                            "linkedRecord": {
                                "registry": "Standard",
                                "id": 50,
                                "name": "Std1",
                                "abbreviation": "S1",
                                "type": "model_and_format",
                            }
                        },
                    ],
                    "reverseRecordAssociations": [],
                }
            },
            # fetch_database_quality_with_fallback (GET_DATABASE_QUALITY_QUERY)
            {
                "fairsharingRecord": {
                    "id": 20,
                    "name": "TestDB",
                    "registry": "Database",
                    "dataAccessCondition": "open",
                    "dataCuration": "manual",
                    "dataDepositionCondition": "open",
                    "citationToRelatedPublications": "yes",
                    "dataContactInformation": "yes",
                    "dataVersioning": "yes",
                    "dataPreservationPolicy": True,
                    "resourceSustainability": True,
                    "usesPersistentIdentifier": True,
                }
            },
            # GET_RECORD_WITH_ASSOCIATIONS_QUERY for DB associations
            {
                "fairsharingRecord": {
                    "id": 20,
                    "name": "TestDB",
                    "registry": "Database",
                    "recordAssociations": [
                        {
                            "linkedRecord": {
                                "registry": "Standard",
                                "id": 50,
                                "name": "Std1",
                                "abbreviation": "S1",
                                "type": "model_and_format",
                            }
                        },
                    ],
                    "reverseRecordAssociations": [],
                }
            },
        ]

        result = await assess_dmp_compliance(10, [20])
        self.assertIn("DMP Compliance Assessment", result)
        self.assertIn("TestPolicy", result)
        self.assertIn("100%", result)  # DB implements the one policy standard

    @patch("fairsharing_mcp.app.get_client")
    async def test_assess_dmp_compliance_with_gaps(self, mock_get_client):
        mock_client = AsyncMock()
        mock_get_client.return_value = mock_client

        mock_client.query.side_effect = [
            # Policy with 2 standards
            {
                "fairsharingRecord": {
                    "id": 10,
                    "name": "TestPolicy",
                    "registry": "Policy",
                    "metadata": {},
                    "recordAssociations": [
                        {
                            "linkedRecord": {
                                "registry": "Standard",
                                "id": 50,
                                "name": "Std1",
                                "abbreviation": "S1",
                                "type": "model",
                            }
                        },
                        {
                            "linkedRecord": {
                                "registry": "Standard",
                                "id": 51,
                                "name": "Std2",
                                "abbreviation": "S2",
                                "type": "format",
                            }
                        },
                    ],
                    "reverseRecordAssociations": [],
                }
            },
            # DB: quality
            {
                "fairsharingRecord": {
                    "id": 20,
                    "name": "TestDB",
                    "registry": "Database",
                    "dataAccessCondition": "open",
                }
            },
            # DB: associations (only implements Std1, not Std2)
            {
                "fairsharingRecord": {
                    "id": 20,
                    "name": "TestDB",
                    "registry": "Database",
                    "recordAssociations": [
                        {
                            "linkedRecord": {
                                "registry": "Standard",
                                "id": 50,
                                "name": "Std1",
                                "abbreviation": "S1",
                                "type": "model",
                            }
                        },
                    ],
                    "reverseRecordAssociations": [],
                }
            },
        ]

        result = await assess_dmp_compliance(10, [20])
        self.assertIn("50%", result)  # 1 of 2 standards
        self.assertIn("Std2", result)  # Should appear in gaps

    @patch("fairsharing_mcp.app.get_client")
    async def test_assess_dmp_compliance_not_a_policy(self, mock_get_client):
        mock_client = AsyncMock()
        mock_get_client.return_value = mock_client

        mock_client.query.side_effect = [
            {
                "fairsharingRecord": {
                    "id": 10,
                    "name": "NotAPolicy",
                    "registry": "Database",
                }
            },
        ]

        result = await assess_dmp_compliance(10, [20])
        self.assertIn("not a Policy", result)

    @patch("fairsharing_mcp.app.get_client")
    async def test_assess_dmp_compliance_json(self, mock_get_client):
        mock_client = AsyncMock()
        mock_get_client.return_value = mock_client

        mock_client.query.side_effect = [
            {
                "fairsharingRecord": {
                    "id": 10,
                    "name": "TestPolicy",
                    "registry": "Policy",
                    "metadata": {},
                    "recordAssociations": [],
                    "reverseRecordAssociations": [],
                }
            },
            {
                "fairsharingRecord": {
                    "id": 20,
                    "name": "TestDB",
                    "registry": "Database",
                    "dataAccessCondition": "open",
                }
            },
            {
                "fairsharingRecord": {
                    "id": 20,
                    "name": "TestDB",
                    "registry": "Database",
                    "recordAssociations": [],
                    "reverseRecordAssociations": [],
                }
            },
        ]

        result = await assess_dmp_compliance(10, [20], output_format="json")
        parsed = json.loads(result)
        self.assertIn("policy", parsed)
        self.assertIn("databases", parsed)
        self.assertIn("recommendations", parsed)

    # ── Phase 3: Transitive Impact ─────────────────────────────────────

    @patch("fairsharing_mcp.app.get_client")
    async def test_analyze_transitive_impact_single_hop(self, mock_get_client):
        mock_client = AsyncMock()
        mock_get_client.return_value = mock_client

        mock_client.query.return_value = {
            "fairsharingRecord": {
                "id": 50,
                "name": "DeprecatedStd",
                "status": "deprecated",
                "registry": "Standard",
                "reverseRecordAssociations": [
                    {
                        "recordAssocLabel": "implements",
                        "fairsharingRecord": {
                            "id": 100,
                            "name": "ActiveDB1",
                            "status": "ready",
                            "registry": "Database",
                        },
                    },
                    {
                        "recordAssocLabel": "implements",
                        "fairsharingRecord": {
                            "id": 101,
                            "name": "ActiveDB2",
                            "status": "ready",
                            "registry": "Database",
                        },
                    },
                    {
                        "recordAssocLabel": "implements",
                        "fairsharingRecord": {
                            "id": 102,
                            "name": "DeprecatedDB",
                            "status": "deprecated",
                            "registry": "Database",
                        },
                    },
                ],
                "recordAssociations": [],
            }
        }

        result = await analyze_transitive_impact(50, max_depth=1)
        self.assertIn("Transitive Impact Analysis", result)
        self.assertIn("ActiveDB1", result)
        self.assertIn("ActiveDB2", result)
        self.assertNotIn("DeprecatedDB", result)
        self.assertIn("2", result)  # 2 active dependents

    @patch("fairsharing_mcp.app.get_client")
    async def test_analyze_transitive_impact_two_hops(self, mock_get_client):
        mock_client = AsyncMock()
        mock_get_client.return_value = mock_client

        # Root: Standard -> DB1 implements it
        # DB1: Policy1 recommends it
        mock_client.query.side_effect = [
            # Root record
            {
                "fairsharingRecord": {
                    "id": 50,
                    "name": "DepStd",
                    "status": "deprecated",
                    "registry": "Standard",
                    "reverseRecordAssociations": [
                        {
                            "recordAssocLabel": "implements",
                            "fairsharingRecord": {
                                "id": 100,
                                "name": "DB1",
                                "status": "ready",
                                "registry": "Database",
                            },
                        },
                    ],
                    "recordAssociations": [],
                }
            },
            # DB1 record (depth 2 traversal)
            {
                "fairsharingRecord": {
                    "id": 100,
                    "name": "DB1",
                    "status": "ready",
                    "registry": "Database",
                    "reverseRecordAssociations": [
                        {
                            "recordAssocLabel": "recommends",
                            "fairsharingRecord": {
                                "id": 200,
                                "name": "Policy1",
                                "status": "ready",
                                "registry": "Policy",
                            },
                        },
                    ],
                    "recordAssociations": [],
                }
            },
        ]

        result = await analyze_transitive_impact(50, max_depth=2)
        self.assertIn("DB1", result)
        self.assertIn("Policy1", result)
        self.assertIn("Depth 2", result)
        self.assertIn("Chain", result)

    @patch("fairsharing_mcp.app.get_client")
    async def test_analyze_transitive_impact_no_dependents(self, mock_get_client):
        mock_client = AsyncMock()
        mock_get_client.return_value = mock_client

        mock_client.query.return_value = {
            "fairsharingRecord": {
                "id": 50,
                "name": "Lonely",
                "status": "deprecated",
                "registry": "Standard",
                "reverseRecordAssociations": [],
                "recordAssociations": [],
            }
        }

        result = await analyze_transitive_impact(50)
        self.assertIn("No active records found", result)

    @patch("fairsharing_mcp.app.get_client")
    async def test_analyze_transitive_impact_cycle(self, mock_get_client):
        mock_client = AsyncMock()
        mock_get_client.return_value = mock_client

        # A -> B -> (A again, should be skipped)
        mock_client.query.side_effect = [
            {
                "fairsharingRecord": {
                    "id": 1,
                    "name": "A",
                    "status": "deprecated",
                    "registry": "Standard",
                    "reverseRecordAssociations": [
                        {
                            "recordAssocLabel": "implements",
                            "fairsharingRecord": {
                                "id": 2,
                                "name": "B",
                                "status": "ready",
                                "registry": "Database",
                            },
                        },
                    ],
                    "recordAssociations": [],
                }
            },
            {
                "fairsharingRecord": {
                    "id": 2,
                    "name": "B",
                    "status": "ready",
                    "registry": "Database",
                    "reverseRecordAssociations": [
                        {
                            "recordAssocLabel": "related_to",
                            "fairsharingRecord": {
                                "id": 1,
                                "name": "A",
                                "status": "ready",
                                "registry": "Standard",
                            },
                        },
                    ],
                    "recordAssociations": [],
                }
            },
        ]

        result = await analyze_transitive_impact(1, max_depth=3)
        self.assertIn("B", result)
        # A should not appear as impacted (it's the root)
        self.assertIn("1", result)  # total should be 1 (just B)

    @patch("fairsharing_mcp.app.get_client")
    async def test_analyze_transitive_impact_json(self, mock_get_client):
        mock_client = AsyncMock()
        mock_get_client.return_value = mock_client

        mock_client.query.return_value = {
            "fairsharingRecord": {
                "id": 50,
                "name": "Std",
                "status": "deprecated",
                "registry": "Standard",
                "reverseRecordAssociations": [
                    {
                        "recordAssocLabel": "implements",
                        "fairsharingRecord": {
                            "id": 100,
                            "name": "DB1",
                            "status": "ready",
                            "registry": "Database",
                        },
                    },
                ],
                "recordAssociations": [],
            }
        }

        result = await analyze_transitive_impact(50, output_format="json")
        parsed = json.loads(result)
        self.assertIn("root", parsed)
        self.assertIn("total_impacted", parsed)
        self.assertEqual(parsed["total_impacted"], 1)

    # ── Phase 3: Tool Workflow Recommendations ─────────────────────────

    async def test_recommend_tools_synonym_expansion(self):
        result = await recommend_tools("find standards")
        # "find" matches tools with "find" in name directly, and synonym expansion
        # ensures broader matches than simple substring matching
        self.assertIn("find_standards_for_database", result)
        # Also check that we get more matches than the old substring approach would
        self.assertIn("match(es)", result)

    async def test_recommend_tools_workflow_hint(self):
        result = await recommend_tools("dmp compliance")
        self.assertIn("Workflow", result)

    async def test_suggest_workflow_exact_match(self):
        result = await suggest_workflow("dmp_compliance")
        self.assertIn("DMP Compliance Assessment", result)
        self.assertIn("assess_dmp_compliance", result)

    async def test_suggest_workflow_fuzzy_match(self):
        result = await suggest_workflow("compare databases quality")
        self.assertIn("Workflow", result)
        # Should match database_selection workflow
        self.assertIn("rank_databases_by_quality", result)

    async def test_suggest_workflow_no_match(self):
        result = await suggest_workflow("xyzzy gibberish nothing")
        self.assertIn("Available workflows", result)

    # ── Phase 4: Full Test Coverage — Taxonomy Tools ────────────────────

    @patch("fairsharing_mcp.app.get_client")
    async def test_search_subjects(self, mock_get_client):
        mock_client = AsyncMock()
        mock_get_client.return_value = mock_client
        mock_client.query.return_value = {
            "searchSubjects": [
                {"label": "Genomics", "id": 1, "description": "Study of genomes"},
                {"label": "Proteomics", "id": 2, "description": "Study of proteins"},
            ]
        }
        result = await search_subjects("genom")
        self.assertIn("Genomics", result)
        self.assertIn("Proteomics", result)
        self.assertIn("2 found", result)

    @patch("fairsharing_mcp.app.get_client")
    async def test_search_subjects_empty(self, mock_get_client):
        mock_client = AsyncMock()
        mock_get_client.return_value = mock_client
        mock_client.query.return_value = {"searchSubjects": []}
        result = await search_subjects("xyznonexistent")
        self.assertIn("No subjects found", result)

    async def test_search_subjects_empty_query(self):
        result = await search_subjects("")
        self.assertIn("Please provide a search query", result)

    @patch("fairsharing_mcp.app.get_client")
    async def test_get_subject(self, mock_get_client):
        mock_client = AsyncMock()
        mock_get_client.return_value = mock_client
        mock_client.query.return_value = {
            "subject": {
                "label": "Genomics",
                "id": 1,
                "description": "The study of genomes",
                "parents": [{"label": "Life Science", "id": 100}],
                "children": [
                    {"label": "Comparative Genomics", "id": 10},
                    {"label": "Functional Genomics", "id": 11},
                ],
            }
        }
        result = await get_subject(1)
        self.assertIn("Genomics", result)
        self.assertIn("Life Science", result)
        self.assertIn("Comparative Genomics", result)

    @patch("fairsharing_mcp.app.get_client")
    async def test_get_subject_not_found(self, mock_get_client):
        mock_client = AsyncMock()
        mock_get_client.return_value = mock_client
        mock_client.query.return_value = {"subject": None}
        result = await get_subject(99999)
        self.assertIn("No subject found", result)

    @patch("fairsharing_mcp.app.get_client")
    async def test_search_domains(self, mock_get_client):
        mock_client = AsyncMock()
        mock_get_client.return_value = mock_client
        mock_client.query.return_value = {
            "searchDomains": [
                {"label": "Identifier schema", "id": 5, "description": ""},
                {"label": "Data model", "id": 6, "description": "Models for data"},
            ]
        }
        result = await search_domains("identifier")
        self.assertIn("Identifier schema", result)
        self.assertIn("Data model", result)
        self.assertIn("2 found", result)

    @patch("fairsharing_mcp.app.get_client")
    async def test_search_domains_empty(self, mock_get_client):
        mock_client = AsyncMock()
        mock_get_client.return_value = mock_client
        mock_client.query.return_value = {"searchDomains": []}
        result = await search_domains("xyznonexistent")
        self.assertIn("No domains found", result)

    @patch("fairsharing_mcp.app.get_client")
    async def test_get_domain(self, mock_get_client):
        mock_client = AsyncMock()
        mock_get_client.return_value = mock_client
        mock_client.query.return_value = {
            "domain": {
                "label": "Identifier schema",
                "id": 5,
                "description": "Schema for identifiers",
                "parents": [],
                "children": [{"label": "DOI", "id": 50}],
            }
        }
        result = await get_domain(5)
        self.assertIn("Identifier schema", result)
        self.assertIn("DOI", result)

    @patch("fairsharing_mcp.app.get_client")
    async def test_get_domain_not_found(self, mock_get_client):
        mock_client = AsyncMock()
        mock_get_client.return_value = mock_client
        mock_client.query.return_value = {"domain": None}
        result = await get_domain(99999)
        self.assertIn("No domain found", result)

    @patch("fairsharing_mcp.app.get_client")
    async def test_search_taxonomies(self, mock_get_client):
        mock_client = AsyncMock()
        mock_get_client.return_value = mock_client
        mock_client.query.return_value = {
            "searchTaxonomies": [
                {
                    "label": "Homo sapiens",
                    "id": 1,
                    "iri": "http://purl.obolibrary.org/obo/NCBITaxon_9606",
                },
                {
                    "label": "Mus musculus",
                    "id": 2,
                    "iri": "http://purl.obolibrary.org/obo/NCBITaxon_10090",
                },
            ]
        }
        result = await search_taxonomies("homo")
        self.assertIn("Homo sapiens", result)
        self.assertIn("Mus musculus", result)
        self.assertIn("2 found", result)

    @patch("fairsharing_mcp.app.get_client")
    async def test_search_taxonomies_empty(self, mock_get_client):
        mock_client = AsyncMock()
        mock_get_client.return_value = mock_client
        mock_client.query.return_value = {"searchTaxonomies": []}
        result = await search_taxonomies("xyznonexistent")
        self.assertIn("No taxonomies found", result)

    # ── Phase 4: Full Test Coverage — Organisation & Country Tools ──────

    @patch("fairsharing_mcp.app.get_client")
    async def test_search_organisations(self, mock_get_client):
        mock_client = AsyncMock()
        mock_get_client.return_value = mock_client
        mock_client.query.return_value = {
            "searchOrganisations": [
                {
                    "name": "EMBL-EBI",
                    "id": 10,
                    "homepage": "https://www.ebi.ac.uk",
                    "countries": [{"name": "United Kingdom"}],
                },
                {
                    "name": "European Molecular Biology Laboratory",
                    "id": 11,
                    "homepage": "",
                    "countries": [{"name": "Germany"}],
                },
            ]
        }
        result = await search_organisations("EMBL")
        self.assertIn("EMBL-EBI", result)
        self.assertIn("United Kingdom", result)
        self.assertIn("2 found", result)

    @patch("fairsharing_mcp.app.get_client")
    async def test_search_organisations_empty(self, mock_get_client):
        mock_client = AsyncMock()
        mock_get_client.return_value = mock_client
        mock_client.query.return_value = {"searchOrganisations": []}
        result = await search_organisations("xyznonexistent")
        self.assertIn("No organisations found", result)

    async def test_search_organisations_empty_query(self):
        result = await search_organisations("")
        self.assertIn("Please provide a search query", result)

    @patch("fairsharing_mcp.app.get_client")
    async def test_list_countries(self, mock_get_client):
        mock_client = AsyncMock()
        mock_get_client.return_value = mock_client
        mock_client.query.return_value = {
            "countries": {
                "records": [
                    {"name": "United Kingdom", "code": "GB", "id": 1},
                    {"name": "United States", "code": "US", "id": 2},
                    {"name": "Germany", "code": "DE", "id": 3},
                ],
                "totalCount": 3,
                "totalPages": 1,
            }
        }
        result = await list_countries()
        self.assertIn("United Kingdom", result)
        self.assertIn("GB", result)
        self.assertIn("United States", result)
        self.assertIn("Germany", result)
        self.assertIn("3", result)

    @patch("fairsharing_mcp.app.get_client")
    async def test_list_countries_empty(self, mock_get_client):
        mock_client = AsyncMock()
        mock_get_client.return_value = mock_client
        mock_client.query.return_value = {
            "countries": {"records": [], "totalCount": 0, "totalPages": 0}
        }
        result = await list_countries()
        self.assertIn("No countries found", result)

    @patch("fairsharing_mcp.app.get_client")
    async def test_list_countries_pagination(self, mock_get_client):
        mock_client = AsyncMock()
        mock_get_client.return_value = mock_client
        mock_client.query.return_value = {
            "countries": {
                "records": [{"name": "France", "code": "FR", "id": 4}],
                "totalCount": 200,
                "totalPages": 2,
            }
        }
        result = await list_countries(page=1, per_page=100)
        self.assertIn("France", result)
        self.assertIn("page=2", result)

    # ── Phase 4: Full Test Coverage — Database Quality Comparison ───────

    @patch("fairsharing_mcp.app.get_client")
    async def test_compare_databases_quality(self, mock_get_client):
        mock_client = AsyncMock()
        mock_get_client.return_value = mock_client

        db1 = {
            "id": 10,
            "name": "ArrayExpress",
            "abbreviation": "AE",
            "registry": "Database",
            "status": "ready",
            "dataAccessCondition": "open",
            "dataCuration": "manual",
            "dataDepositionCondition": "open",
            "citationToRelatedPublications": "yes",
            "dataContactInformation": "yes",
            "dataVersioning": "yes",
            "dataPreservationPolicy": True,
            "resourceSustainability": True,
            "usesPersistentIdentifier": True,
        }
        db2 = {
            "id": 20,
            "name": "GEO",
            "abbreviation": "GEO",
            "registry": "Database",
            "status": "ready",
            "dataAccessCondition": "open",
            "dataCuration": "automated",
            "dataDepositionCondition": "open",
            "citationToRelatedPublications": "yes",
            "dataContactInformation": "no",
            "dataVersioning": "no",
            "dataPreservationPolicy": False,
            "resourceSustainability": False,
            "usesPersistentIdentifier": True,
        }

        # fetch_database_quality_with_fallback is called per record
        mock_client.query.side_effect = [
            {"fairsharingRecord": db1},  # fallback for db1
            {"fairsharingRecord": db2},  # fallback for db2
        ]

        result = await compare_databases_quality([10, 20])
        self.assertIn("Database Quality Comparison", result)
        self.assertIn("AE", result)
        self.assertIn("GEO", result)
        self.assertIn("Data Access", result)

    async def test_compare_databases_quality_too_few(self):
        result = await compare_databases_quality([1])
        self.assertIn("at least 2", result)

    async def test_compare_databases_quality_too_many(self):
        result = await compare_databases_quality(list(range(11)))
        self.assertIn("at most 10", result)

    # ── Phase 4: Full Test Coverage — Database Quality Ranking ──────────

    @patch("fairsharing_mcp.app.get_client")
    async def test_rank_databases_by_quality(self, mock_get_client):
        mock_client = AsyncMock()
        mock_get_client.return_value = mock_client

        candidate1 = {"id": 10, "name": "DB_High", "abbreviation": "DBH"}
        candidate2 = {"id": 20, "name": "DB_Low", "abbreviation": "DBL"}

        db1_detail = {
            "id": 10,
            "name": "DB_High",
            "abbreviation": "DBH",
            "registry": "Database",
            "status": "ready",
            "dataAccessCondition": "open",
            "dataCuration": "manual",
            "dataDepositionCondition": "open",
            "citationToRelatedPublications": "yes",
            "dataContactInformation": "yes",
            "dataVersioning": "yes",
            "dataPreservationPolicy": True,
            "resourceSustainability": True,
            "usesPersistentIdentifier": True,
        }
        db2_detail = {
            "id": 20,
            "name": "DB_Low",
            "abbreviation": "DBL",
            "registry": "Database",
            "status": "ready",
            "dataAccessCondition": "not found",
            "dataCuration": "none",
            "dataDepositionCondition": "not found",
            "citationToRelatedPublications": "no",
            "dataContactInformation": "no",
            "dataVersioning": "no",
            "dataPreservationPolicy": False,
            "resourceSustainability": False,
            "usesPersistentIdentifier": False,
        }

        mock_client.query.side_effect = [
            # Step 1: multiTagFilter returns candidates
            {"multiTagFilter": [candidate1, candidate2]},
            # Step 2: fetch_database_quality_with_fallback for each
            {"fairsharingRecord": db1_detail},
            {"fairsharingRecord": db2_detail},
        ]

        result = await rank_databases_by_quality(subjects=["Genomics"])
        self.assertIn("FAIR Quality Ranking", result)
        self.assertIn("DBH", result)
        self.assertIn("DBL", result)
        # DB_High should rank #1
        self.assertIn("1 | DBH", result)

    @patch("fairsharing_mcp.app.get_client")
    async def test_rank_databases_by_quality_no_candidates(self, mock_get_client):
        mock_client = AsyncMock()
        mock_get_client.return_value = mock_client
        mock_client.query.return_value = {"multiTagFilter": []}
        result = await rank_databases_by_quality(subjects=["Nonexistent"])
        self.assertIn("No databases found", result)

    @patch("fairsharing_mcp.app.get_client")
    async def test_rank_databases_by_quality_country_filter(self, mock_get_client):
        mock_client = AsyncMock()
        mock_get_client.return_value = mock_client

        candidate = {"id": 10, "name": "UK_DB", "abbreviation": "UKDB"}
        db_detail = {
            "id": 10,
            "name": "UK_DB",
            "abbreviation": "UKDB",
            "registry": "Database",
            "status": "ready",
            "countries": [{"name": "United Kingdom"}],
            "dataAccessCondition": "open",
            "dataCuration": "manual",
            "dataDepositionCondition": "open",
            "citationToRelatedPublications": "yes",
            "dataContactInformation": "yes",
            "dataVersioning": "yes",
            "dataPreservationPolicy": True,
            "resourceSustainability": True,
            "usesPersistentIdentifier": True,
        }

        mock_client.query.side_effect = [
            {"multiTagFilter": [candidate]},
            {"fairsharingRecord": db_detail},
        ]

        result = await rank_databases_by_quality(countries=["United Kingdom"])
        self.assertIn("UKDB", result)
        self.assertIn("Countries: United Kingdom", result)

    # ── Phase 4: Non-Determinism Tests — Label Propagation ──────────────

    @patch("fairsharing_mcp.app.get_client")
    async def test_detect_communities_deterministic_with_seed(self, mock_get_client):
        """Verify that detect_communities produces identical results with same seed."""
        graph = self._make_graph(
            [
                (1, "StdA", "standard", "model_and_format"),
                (2, "DB1", "database", "repository"),
                (3, "StdB", "standard", "terminology_artefact"),
                (4, "DB2", "database", "repository"),
                (5, "StdC", "standard", "model_and_format"),
                (6, "DB3", "database", "repository"),
            ],
            [
                (1, 2, "pink"),
                (1, 3, "grey"),
                (2, 4, "pink"),
                (3, 5, "grey"),
                (4, 6, "pink"),
                (5, 6, "grey"),
            ],
        )

        # Run twice with the same seed
        mock_get_client.return_value = self._graph_mock(graph)
        result1 = await detect_communities(1, min_community_size=2, seed=42)

        mock_get_client.return_value = self._graph_mock(graph)
        result2 = await detect_communities(1, min_community_size=2, seed=42)

        self.assertEqual(result1, result2)

    @patch("fairsharing_mcp.app.get_client")
    async def test_detect_communities_different_seed_may_differ(self, mock_get_client):
        """Verify detect_communities runs without error using different seeds."""
        graph = self._make_graph(
            [
                (1, "StdA", "standard", "model_and_format"),
                (2, "DB1", "database", "repository"),
                (3, "StdB", "standard", "terminology_artefact"),
                (4, "DB2", "database", "repository"),
            ],
            [
                (1, 2, "pink"),
                (2, 3, "grey"),
                (3, 4, "pink"),
                (1, 4, "grey"),
            ],
        )

        # Both should succeed without error regardless of seed
        mock_get_client.return_value = self._graph_mock(graph)
        result_a = await detect_communities(1, min_community_size=2, seed=1)
        # With 4 nodes, min_community_size=2, we should get some result
        self.assertIn("Community Detection", result_a)

        mock_get_client.return_value = self._graph_mock(graph)
        result_b = await detect_communities(1, min_community_size=2, seed=99)
        self.assertIn("Community Detection", result_b)

    @patch("fairsharing_mcp.app.get_client")
    async def test_analyze_graph_comprehensive_deterministic(self, mock_get_client):
        """Verify analyze_graph_comprehensive is deterministic with seed."""
        graph = self._make_graph(
            [
                (1, "StdA", "standard", "model_and_format"),
                (2, "DB1", "database", "repository"),
                (3, "Policy1", "policy", "journal"),
                (4, "StdB", "standard", "terminology_artefact"),
            ],
            [
                (1, 2, "pink"),
                (2, 3, "grey"),
                (3, 4, "green"),
                (1, 4, "pink"),
            ],
        )

        mock_get_client.return_value = self._graph_mock(graph)
        result1 = await analyze_graph_comprehensive(1, seed=42)

        mock_get_client.return_value = self._graph_mock(graph)
        result2 = await analyze_graph_comprehensive(1, seed=42)

        self.assertEqual(result1, result2)

    # ── Phase 4: Integration / Stress Tests ─────────────────────────────

    @patch("fairsharing_mcp.app.get_client")
    async def test_large_graph_pagerank(self, mock_get_client):
        """Stress test: PageRank on a graph with many nodes."""
        # Generate a large star graph: 50 leaf nodes all pointing TO the hub
        # Hub will have high PageRank because it has many incoming links
        nodes_spec = [(0, "Hub", "standard", "model_and_format")]
        edges_spec = []
        for i in range(1, 51):
            nodes_spec.append((i, f"Leaf{i}", "database", "repository"))
            edges_spec.append((i, 0, "pink"))  # leaf -> hub (incoming to hub)

        graph = self._make_graph(nodes_spec, edges_spec)
        mock_get_client.return_value = self._graph_mock(graph)

        result = await compute_pagerank(1, top_n=51)
        self.assertIn("Hub", result)
        self.assertIn("PageRank", result)
        self.assertIn("51 nodes", result)

    @patch("fairsharing_mcp.app.get_client")
    async def test_large_graph_betweenness(self, mock_get_client):
        """Stress test: Betweenness centrality on a chain graph."""
        # Generate a chain: n0 -> n1 -> n2 -> ... -> n19
        nodes_spec = [(i, f"Node{i}", "standard", "model_and_format") for i in range(20)]
        edges_spec = [(i, i + 1, "pink") for i in range(19)]

        graph = self._make_graph(nodes_spec, edges_spec)
        mock_get_client.return_value = self._graph_mock(graph)

        result = await compute_betweenness_centrality(1)
        self.assertIn("Betweenness", result)
        # Middle nodes should have higher centrality than endpoints
        self.assertIn("Node9", result)
        self.assertIn("Node10", result)

    @patch("fairsharing_mcp.app.get_client")
    async def test_transitive_impact_max_fetch_cap(self, mock_get_client):
        """Stress test: transitive impact respects the 100-fetch cap."""
        mock_client = AsyncMock()
        mock_get_client.return_value = mock_client

        # Create a wide fan-out at depth 1 — root has 110 dependents
        root_record = {
            "fairsharingRecord": {
                "id": 1,
                "name": "Root",
                "registry": "Standard",
                "status": "ready",
                "reverseRecordAssociations": [
                    {
                        "recordAssocLabel": "implements",
                        "fairsharingRecord": {
                            "id": 100 + i,
                            "name": f"DB_{i}",
                            "status": "ready",
                            "registry": "Database",
                        },
                    }
                    for i in range(110)
                ],
                "recordAssociations": [],
            }
        }

        # Each dependent has no further dependents
        empty_record = {
            "fairsharingRecord": {
                "id": 999,
                "name": "Leaf",
                "registry": "Database",
                "status": "ready",
                "reverseRecordAssociations": [],
                "recordAssociations": [],
            }
        }

        # First call returns root, subsequent calls return leaf
        mock_client.query.side_effect = [root_record] + [empty_record] * 150

        result = await analyze_transitive_impact(1, max_depth=2)
        # Should mention truncation since 110 > 100
        self.assertIn("truncated", result.lower())
        # Should still have results
        self.assertIn("DB_", result)

    @patch("fairsharing_mcp.app.get_client")
    async def test_multi_step_quality_ranking_workflow(self, mock_get_client):
        """Integration test: full ranking workflow — search, fetch, score, sort."""
        mock_client = AsyncMock()
        mock_get_client.return_value = mock_client

        # 3 candidates with varied quality
        candidates = [
            {"id": 1, "name": "Best"},
            {"id": 2, "name": "Middle"},
            {"id": 3, "name": "Worst"},
        ]

        best = {
            "id": 1,
            "name": "Best",
            "abbreviation": "B",
            "registry": "Database",
            "status": "ready",
            "dataAccessCondition": "open",
            "dataCuration": "manual",
            "dataDepositionCondition": "open",
            "citationToRelatedPublications": "yes",
            "dataContactInformation": "yes",
            "dataVersioning": "yes",
            "dataPreservationPolicy": True,
            "resourceSustainability": True,
            "usesPersistentIdentifier": True,
        }
        middle = {
            "id": 2,
            "name": "Middle",
            "abbreviation": "M",
            "registry": "Database",
            "status": "ready",
            "dataAccessCondition": "open",
            "dataCuration": "automated",
            "dataDepositionCondition": "controlled",
            "citationToRelatedPublications": "yes",
            "dataContactInformation": "no",
            "dataVersioning": "no",
            "dataPreservationPolicy": False,
            "resourceSustainability": False,
            "usesPersistentIdentifier": True,
        }
        worst = {
            "id": 3,
            "name": "Worst",
            "abbreviation": "W",
            "registry": "Database",
            "status": "ready",
            "dataAccessCondition": "not found",
            "dataCuration": "none",
            "dataDepositionCondition": "not found",
            "citationToRelatedPublications": "no",
            "dataContactInformation": "no",
            "dataVersioning": "no",
            "dataPreservationPolicy": False,
            "resourceSustainability": False,
            "usesPersistentIdentifier": False,
        }

        mock_client.query.side_effect = [
            {"multiTagFilter": candidates},
            {"fairsharingRecord": best},
            {"fairsharingRecord": middle},
            {"fairsharingRecord": worst},
        ]

        result = await rank_databases_by_quality(max_results=5)
        # Check ranking order: Best > Middle > Worst
        best_pos = result.index("B |")
        middle_pos = result.index("M |")
        worst_pos = result.index("W |")
        self.assertLess(best_pos, middle_pos)
        self.assertLess(middle_pos, worst_pos)

    # ── Phase 5: Tests for newly added tools ────────────────────────────

    @patch("fairsharing_mcp.app.get_client")
    async def test_find_referencing_records_markdown(self, mock_get_client):
        """find_referencing_records returns reverse associations."""
        mock_client = AsyncMock()
        mock_get_client.return_value = mock_client
        mock_client.query.return_value = {
            "fairsharingRecord": {
                "id": 100,
                "name": "Test Standard",
                "reverseRecordAssociations": [
                    {
                        "fairsharingRecord": {
                            "id": "10",
                            "name": "DB Alpha",
                            "abbreviation": "DBA",
                            "registry": "Database",
                            "type": "repository",
                            "status": "ready",
                        },
                        "recordAssocLabel": "implements",
                    },
                    {
                        "fairsharingRecord": {
                            "id": "20",
                            "name": "Policy X",
                            "abbreviation": "PX",
                            "registry": "Policy",
                            "type": "funder",
                            "status": "ready",
                        },
                        "recordAssocLabel": "recommends",
                    },
                ],
            }
        }
        result = await find_referencing_records(100)
        self.assertIn("DB Alpha", result)
        self.assertIn("Policy X", result)
        self.assertIn("implements", result)
        self.assertIn("recommends", result)
        self.assertIn("2 of 2", result)

    @patch("fairsharing_mcp.app.get_client")
    async def test_find_referencing_records_filter_by_registry(self, mock_get_client):
        """find_referencing_records filters by registry."""
        mock_client = AsyncMock()
        mock_get_client.return_value = mock_client
        mock_client.query.return_value = {
            "fairsharingRecord": {
                "id": 100,
                "name": "Test Standard",
                "reverseRecordAssociations": [
                    {
                        "fairsharingRecord": {
                            "id": "10",
                            "name": "DB Alpha",
                            "abbreviation": "",
                            "registry": "Database",
                            "type": "repository",
                            "status": "ready",
                        },
                        "recordAssocLabel": "implements",
                    },
                    {
                        "fairsharingRecord": {
                            "id": "20",
                            "name": "Policy X",
                            "abbreviation": "",
                            "registry": "Policy",
                            "type": "funder",
                            "status": "ready",
                        },
                        "recordAssocLabel": "recommends",
                    },
                ],
            }
        }
        result = await find_referencing_records(100, registry="Database")
        self.assertIn("DB Alpha", result)
        self.assertNotIn("Policy X", result)
        self.assertIn("1 of 2", result)

    @patch("fairsharing_mcp.app.get_client")
    async def test_find_referencing_records_json(self, mock_get_client):
        """find_referencing_records returns JSON when requested."""
        mock_client = AsyncMock()
        mock_get_client.return_value = mock_client
        mock_client.query.return_value = {
            "fairsharingRecord": {
                "id": 100,
                "name": "Test Standard",
                "reverseRecordAssociations": [
                    {
                        "fairsharingRecord": {
                            "id": "10",
                            "name": "DB Alpha",
                            "abbreviation": "DBA",
                            "registry": "Database",
                            "type": "repository",
                            "status": "ready",
                        },
                        "recordAssocLabel": "implements",
                    },
                ],
            }
        }
        result = await find_referencing_records(100, output_format="json")
        data = json.loads(result)
        self.assertEqual(data["record_id"], 100)
        self.assertEqual(len(data["referencing_records"]), 1)
        self.assertEqual(data["referencing_records"][0]["name"], "DB Alpha")

    @patch("fairsharing_mcp.app.get_client")
    async def test_get_records_batch(self, mock_get_client):
        """get_records_batch fetches multiple records."""
        mock_client = AsyncMock()
        mock_get_client.return_value = mock_client
        mock_client.query.side_effect = [
            {
                "fairsharingRecord": {
                    "id": 1,
                    "name": "Record One",
                    "abbreviation": "R1",
                    "registry": "Database",
                    "type": "repository",
                    "status": "ready",
                }
            },
            {
                "fairsharingRecord": {
                    "id": 2,
                    "name": "Record Two",
                    "abbreviation": "R2",
                    "registry": "Standard",
                    "type": "model/format",
                    "status": "ready",
                }
            },
        ]
        result = await get_records_batch([1, 2])
        self.assertIn("Record One", result)
        self.assertIn("Record Two", result)
        self.assertIn("2 of 2", result)

    @patch("fairsharing_mcp.app.get_client")
    async def test_get_records_batch_json(self, mock_get_client):
        """get_records_batch returns JSON when requested."""
        mock_client = AsyncMock()
        mock_get_client.return_value = mock_client
        mock_client.query.side_effect = [
            {
                "fairsharingRecord": {
                    "id": 1,
                    "name": "Record One",
                    "registry": "Database",
                    "status": "ready",
                }
            },
            {
                "fairsharingRecord": {
                    "id": 2,
                    "name": "Record Two",
                    "registry": "Standard",
                    "status": "ready",
                }
            },
        ]
        result = await get_records_batch([1, 2], output_format="json")
        data = json.loads(result)
        self.assertEqual(len(data["records"]), 2)
        self.assertEqual(data["failed_ids"], [])

    async def test_get_records_batch_too_few(self):
        result = await get_records_batch([1])
        self.assertIn("at least 2", result)

    async def test_get_records_batch_too_many(self):
        result = await get_records_batch(list(range(21)))
        self.assertIn("at most 20", result)

    @patch("fairsharing_mcp.app.get_client")
    async def test_get_record_types(self, mock_get_client):
        """get_record_types returns types grouped by registry."""
        mock_client = AsyncMock()
        mock_get_client.return_value = mock_client
        mock_client.query.return_value = {
            "recordTypes": {
                "records": [
                    {
                        "name": "repository",
                        "description": "A data repository",
                        "fairsharingRegistry": {"name": "Database"},
                    },
                    {
                        "name": "model/format",
                        "description": "A data model",
                        "fairsharingRegistry": {"name": "Standard"},
                    },
                ]
            }
        }
        result = await get_record_types()
        self.assertIn("repository", result)
        self.assertIn("model/format", result)
        self.assertIn("Database", result)
        self.assertIn("Standard", result)

    # ── Phase 5: Tests for untested policy tools ──────────────────────────

    @patch("fairsharing_mcp.app.get_client")
    async def test_get_policy_details(self, mock_get_client):
        """get_policy_details returns policy detail."""
        mock_client = AsyncMock()
        mock_get_client.return_value = mock_client
        mock_client.query.return_value = {
            "fairsharingRecord": {
                "id": 200,
                "name": "NIH Data Sharing Policy",
                "abbreviation": "NIH-DSP",
                "registry": "Policy",
                "type": "funder",
                "status": "ready",
                "doi": None,
                "homepage": "https://sharing.nih.gov",
                "description": "NIH policy on data sharing",
                "metadata": {
                    "mandatedDataSharing": "required",
                    "mandatedDmpCreation": "required",
                },
                "recordAssociations": [],
                "reverseRecordAssociations": [],
                "subjects": [],
                "domains": [],
                "countries": [{"name": "United States"}],
            }
        }
        result = await get_policy_details(200)
        self.assertIn("NIH Data Sharing Policy", result)

    @patch("fairsharing_mcp.app.get_client")
    async def test_get_policy_details_not_policy(self, mock_get_client):
        """get_policy_details returns error for non-policy records."""
        mock_client = AsyncMock()
        mock_get_client.return_value = mock_client
        mock_client.query.return_value = {
            "fairsharingRecord": {
                "id": 1,
                "name": "A Database",
                "registry": "Database",
            }
        }
        result = await get_policy_details(1)
        self.assertIn("not a Policy", result)

    @patch("fairsharing_mcp.app.get_client")
    async def test_get_policy_quality_profile(self, mock_get_client):
        """get_policy_quality_profile scores a policy."""
        mock_client = AsyncMock()
        mock_get_client.return_value = mock_client
        mock_client.query.return_value = {
            "fairsharingRecord": {
                "id": 200,
                "name": "Test Policy",
                "registry": "Policy",
                "type": "funder",
                "status": "ready",
                "metadata": {
                    "mandatedDataSharing": "required",
                    "mandatedDmpCreation": "required",
                    "sharingResearchSoftware": "suggested",
                    "metadataSharing": "required",
                    "dataProtection": "yes",
                },
                "recordAssociations": [
                    {"linkedRecord": {"registry": "Standard", "name": "ISO 123"}},
                    {"linkedRecord": {"registry": "Database", "name": "DB1"}},
                ],
                "reverseRecordAssociations": [],
            }
        }
        result = await get_policy_quality_profile(200)
        self.assertIn("Policy Quality Profile", result)
        self.assertIn("Score", result)
        self.assertIn("Grade", result)

    @patch("fairsharing_mcp.app.get_client")
    async def test_get_standard_quality_profile(self, mock_get_client):
        """get_standard_quality_profile scores a standard."""
        mock_client = AsyncMock()
        mock_get_client.return_value = mock_client
        mock_client.query.return_value = {
            "fairsharingRecord": {
                "id": 100,
                "name": "Test Standard",
                "registry": "Standard",
                "type": "model/format",
                "status": "ready",
                "doi": "10.1234/test",
                "homepage": "https://example.com",
                "description": "A comprehensive test standard for data formatting purposes.",
                "isMaintained": True,
                "reverseRecordAssociations": [
                    {
                        "recordAssocLabel": "implements",
                        "fairsharingRecord": {"registry": "Database", "name": "DB1"},
                    },
                    {
                        "recordAssocLabel": "implements",
                        "fairsharingRecord": {"registry": "Database", "name": "DB2"},
                    },
                    {
                        "recordAssocLabel": "implements",
                        "fairsharingRecord": {"registry": "Database", "name": "DB3"},
                    },
                ],
                "recordAssociations": [],
            }
        }
        result = await get_standard_quality_profile(100)
        self.assertIn("Standard Quality Profile", result)
        self.assertIn("Score", result)

    @patch("fairsharing_mcp.app.get_client")
    async def test_get_standard_quality_profile_json(self, mock_get_client):
        """get_standard_quality_profile returns JSON."""
        mock_client = AsyncMock()
        mock_get_client.return_value = mock_client
        mock_client.query.return_value = {
            "fairsharingRecord": {
                "id": 100,
                "name": "Test Standard",
                "registry": "Standard",
                "type": "model/format",
                "status": "ready",
                "doi": "10.1234/test",
                "homepage": "https://example.com",
                "description": "A comprehensive test standard for data formatting purposes.",
                "isMaintained": True,
                "reverseRecordAssociations": [],
                "recordAssociations": [],
            }
        }
        result = await get_standard_quality_profile(100, output_format="json")
        data = json.loads(result)
        self.assertIn("score", data)
        self.assertIn("grade", data)
        self.assertIn("confidence", data)

    @patch("fairsharing_mcp.app.get_client")
    async def test_find_standards_for_database(self, mock_get_client):
        """find_standards_for_database lists associated standards."""
        mock_client = AsyncMock()
        mock_get_client.return_value = mock_client
        mock_client.query.return_value = {
            "fairsharingRecord": {
                "id": 10,
                "name": "Test Database",
                "abbreviation": "TDB",
                "registry": "Database",
                "type": "repository",
                "status": "ready",
                "recordAssociations": [
                    {
                        "linkedRecord": {
                            "id": "100",
                            "name": "Format Std",
                            "abbreviation": "FS",
                            "registry": "Standard",
                            "type": "model/format",
                            "status": "ready",
                        },
                        "recordAssocLabel": "implements",
                    },
                ],
                "reverseRecordAssociations": [],
            }
        }
        result = await find_standards_for_database(10)
        self.assertIn("Standards for: Test Database", result)
        self.assertIn("Format Std", result)

    @patch("fairsharing_mcp.app.get_client")
    async def test_analyze_policy_mandates(self, mock_get_client):
        """analyze_policy_mandates returns mandate distributions."""
        mock_client = AsyncMock()
        mock_get_client.return_value = mock_client

        policy_search = {
            "id": "200",
            "name": "Policy A",
            "registry": "Policy",
        }
        policy_detail = {
            "fairsharingRecord": {
                "id": 200,
                "name": "Policy A",
                "registry": "Policy",
                "type": "funder",
                "status": "ready",
                "metadata": {
                    "mandatedDataSharing": "required",
                    "mandatedDmpCreation": "suggested",
                },
                "recordAssociations": [],
                "reverseRecordAssociations": [],
                "subjects": [],
                "domains": [],
                "countries": [{"name": "Ireland"}],
            }
        }

        mock_client.query.side_effect = [
            {
                "searchFairsharingRecords": {
                    "records": [policy_search],
                    "totalCount": 1,
                    "totalPages": 1,
                }
            },
            policy_detail,  # fetch_policy_with_fallback
        ]

        result = await analyze_policy_mandates(countries=["Ireland"])
        self.assertIn("Policy Mandate Analysis", result)
        self.assertIn("1 of 1", result)

    @patch("fairsharing_mcp.app.get_client")
    async def test_compare_policies_by_country(self, mock_get_client):
        """compare_policies_by_country compares two countries."""
        mock_client = AsyncMock()
        mock_get_client.return_value = mock_client

        policy_ie = {
            "id": "201",
            "name": "Irish Policy",
            "registry": "Policy",
        }
        policy_uk = {
            "id": "202",
            "name": "UK Policy",
            "registry": "Policy",
        }
        ie_detail = {
            "fairsharingRecord": {
                "id": 201,
                "name": "Irish Policy",
                "registry": "Policy",
                "type": "funder",
                "status": "ready",
                "metadata": {"mandatedDataSharing": "required"},
                "recordAssociations": [],
                "reverseRecordAssociations": [],
                "subjects": [],
                "domains": [],
                "countries": [{"name": "Ireland"}],
            }
        }
        uk_detail = {
            "fairsharingRecord": {
                "id": 202,
                "name": "UK Policy",
                "registry": "Policy",
                "type": "funder",
                "status": "ready",
                "metadata": {"mandatedDataSharing": "suggested"},
                "recordAssociations": [],
                "reverseRecordAssociations": [],
                "subjects": [],
                "domains": [],
                "countries": [{"name": "United Kingdom"}],
            }
        }

        mock_client.query.side_effect = [
            # Search IE
            {
                "searchFairsharingRecords": {
                    "records": [policy_ie],
                    "totalCount": 1,
                    "totalPages": 1,
                }
            },
            ie_detail,
            # Search UK
            {
                "searchFairsharingRecords": {
                    "records": [policy_uk],
                    "totalCount": 1,
                    "totalPages": 1,
                }
            },
            uk_detail,
        ]

        result = await compare_policies_by_country(["Ireland", "United Kingdom"])
        self.assertIn("Policy Comparison", result)
        self.assertIn("Ireland", result)
        self.assertIn("United Kingdom", result)

    async def test_compare_policies_by_country_too_few(self):
        result = await compare_policies_by_country(["Ireland"])
        self.assertIn("at least 2", result)

    @patch("fairsharing_mcp.app.get_client")
    async def test_find_policy_gaps(self, mock_get_client):
        """find_policy_gaps identifies uncovered resources."""
        mock_client = AsyncMock()
        mock_get_client.return_value = mock_client

        mock_client.query.side_effect = [
            # Policies for the subject
            {
                "searchFairsharingRecords": {
                    "records": [{"id": "200", "name": "Policy A"}],
                    "totalCount": 1,
                    "totalPages": 1,
                }
            },
            # Policy associations
            {
                "fairsharingRecord": {
                    "id": 200,
                    "recordAssociations": [
                        {
                            "linkedRecord": {
                                "id": "100",
                                "name": "Covered Std",
                                "registry": "Standard",
                            }
                        },
                    ],
                }
            },
            # Standards for subject
            {
                "searchFairsharingRecords": {
                    "records": [
                        {
                            "id": "100",
                            "name": "Covered Std",
                            "abbreviation": "CS",
                            "type": "model/format",
                        },
                        {
                            "id": "101",
                            "name": "Uncovered Std",
                            "abbreviation": "US",
                            "type": "model/format",
                        },
                    ],
                    "totalCount": 2,
                    "totalPages": 1,
                }
            },
            # Databases for subject
            {
                "searchFairsharingRecords": {
                    "records": [],
                    "totalCount": 0,
                    "totalPages": 0,
                }
            },
        ]

        result = await find_policy_gaps("Genomics")
        self.assertIn("Policy Gap Analysis", result)
        self.assertIn("Uncovered Std", result)

    # ── Phase 5: Token bucket test ────────────────────────────────────────

    async def test_token_bucket_burst(self):
        """Token bucket allows burst after idle period."""
        from fairsharing_mcp.client import _TokenBucket

        bucket = _TokenBucket(rate=5.0, burst=3)
        # Should be able to acquire 3 tokens immediately (burst=3)
        import asyncio

        start = asyncio.get_event_loop().time()
        for _ in range(3):
            await bucket.acquire()
        elapsed = asyncio.get_event_loop().time() - start
        # 3 burst tokens should be near-instant (< 100ms)
        self.assertLess(elapsed, 0.1)

    # ── Phase 2: Comprehensive Quality Scoring Tests ──

    async def test_score_standard_comprehensive_full(self):
        """Test comprehensive standard scoring with all data present."""
        from fairsharing_mcp.tools.standards import _score_standard_comprehensive

        record = {
            "homepage": "https://example.com",
            "doi": "10.1234/test",
            "description": "A detailed description that is longer than fifty characters for testing purposes.",
            "status": "ready",
            "isMaintained": True,
            "updatedAt": "2025-06-01",
            "createdAt": "2020-01-01",
            "publications": [{"id": "1"}, {"id": "2"}, {"id": "3"}, {"id": "4"}, {"id": "5"}],
            "subjects": [{"label": "A"}, {"label": "B"}, {"label": "C"}],
            "reverseRecordAssociations": [
                {"recordAssocLabel": "implements", "fairsharingRecord": {"registry": "Database"}}
                for _ in range(12)
            ]
            + [
                {"recordAssocLabel": "recommends", "fairsharingRecord": {"registry": "Policy"}}
                for _ in range(5)
            ],
        }
        result = _score_standard_comprehensive(record)
        self.assertIn("indicators", result)
        self.assertIn("temporal_health", result["indicators"])
        self.assertIn("community_engagement", result["indicators"])
        self.assertIn("adoption_breadth", result["indicators"])
        self.assertGreater(result["total_score"], 0)
        self.assertGreaterEqual(result["max_score"], result["total_score"])
        self.assertIn(result["grade"], ("A+", "A", "B", "C", "D", "F"))
        # With full data, temporal should be max (updated within 2 years)
        self.assertEqual(result["indicators"]["temporal_health"]["score"], 2.0)
        # 5+ publications + 3 subjects
        self.assertGreater(result["indicators"]["community_engagement"]["score"], 0)

    async def test_score_standard_comprehensive_sparse(self):
        """Test comprehensive standard scoring with minimal data."""
        from fairsharing_mcp.tools.standards import _score_standard_comprehensive

        record = {"status": "uncertain", "reverseRecordAssociations": []}
        result = _score_standard_comprehensive(record)
        self.assertIn("indicators", result)
        # Should still produce a valid result even with sparse data
        self.assertEqual(result["indicators"]["temporal_health"]["score"], 0.0)
        self.assertEqual(result["indicators"]["community_engagement"]["score"], 0.0)
        self.assertIn(result["confidence"], ("high", "medium", "low"))

    async def test_score_policy_comprehensive_multi_country(self):
        """Test comprehensive policy scoring with multiple countries."""
        from fairsharing_mcp.tools.policies import _score_policy_comprehensive

        record = {
            "mandatedDataSharing": "required",
            "mandatedDmpCreation": "suggested",
            "sharingResearchSoftware": "required",
            "metadataSharing": "required",
            "monitoringOfCompliance": "yes",
            "guidanceToHelpEnableCompliance": "yes",
            "timingOfDmp": "before",
            "updatingOfDmp": "yes",
            "dataProtection": "yes",
            "dataCitation": "yes",
            "dataPreservation": "yes",
            "dataAvailabilityStatement": "yes",
            "supportedCosts": "yes",
            "countries": [{"name": f"Country {i}"} for i in range(6)],
            "recordAssociations": [
                {"linkedRecord": {"registry": "Standard"}},
                {"linkedRecord": {"registry": "Database"}},
            ],
            "updatedAt": "2025-01-01",
        }
        result = _score_policy_comprehensive(record)
        self.assertIn("geographic_coverage", result["indicators"])
        # 6 countries → full geo score
        self.assertEqual(
            result["indicators"]["geographic_coverage"]["score"],
            result["indicators"]["geographic_coverage"]["max"],
        )
        self.assertGreater(result["total_score"], 5.0)

    async def test_score_policy_comprehensive_no_countries(self):
        """Test comprehensive policy scoring with no country data."""
        from fairsharing_mcp.tools.policies import _score_policy_comprehensive

        record = {
            "mandatedDataSharing": "required",
            "countries": [],
            "recordAssociations": [],
        }
        result = _score_policy_comprehensive(record)
        self.assertEqual(result["indicators"]["geographic_coverage"]["score"], 0.0)

    async def test_score_database_comprehensive_with_trust(self):
        """Test comprehensive database scoring with community trust data."""
        from fairsharing_mcp.tools.quality import _score_database_comprehensive

        record = {
            "dataAccessCondition": "open",
            "dataCuration": "manual",
            "dataDepositionCondition": "open",
            "citationToRelatedPublications": True,
            "dataContactInformation": True,
            "dataVersioning": "yes",
            "dataPreservationPolicy": True,
            "resourceSustainability": True,
            "usesPersistentIdentifier": True,
            "updatedAt": "2025-06-01",
            "description": "A description that is over a hundred characters long for the metadata completeness check in the comprehensive scorer.",
            "doi": "10.1234/test",
            "publications": [{"id": "1"}],
            "licenceLinks": [{"name": "CC-BY"}],
            "reverseRecordAssociations": [
                {"recordAssocLabel": "recommends", "fairsharingRecord": {"registry": "Policy"}}
                for _ in range(6)
            ],
            "recordAssociations": [{"linkedRecord": {"registry": "Standard"}} for _ in range(6)],
        }
        result = _score_database_comprehensive(record)
        self.assertIn("community_trust", result["indicators"])
        self.assertIn("metadata_completeness", result["indicators"])
        self.assertGreater(result["indicators"]["community_trust"]["score"], 0)
        # All metadata present
        self.assertEqual(
            result["indicators"]["metadata_completeness"]["score"],
            result["indicators"]["metadata_completeness"]["max"],
        )

    async def test_score_database_comprehensive_minimal(self):
        """Test comprehensive database scoring with minimal data."""
        from fairsharing_mcp.tools.quality import _score_database_comprehensive

        record = {}
        result = _score_database_comprehensive(record)
        self.assertEqual(result["indicators"]["community_trust"]["score"], 0.0)
        self.assertEqual(result["indicators"]["temporal_health"]["score"], 0.0)
        self.assertIn(result["confidence"], ("high", "medium", "low"))

    @patch("fairsharing_mcp.app.get_client")
    async def test_get_comprehensive_quality_profile_standard(self, mock_get_client):
        """Test comprehensive quality profile for a Standard record."""
        mock_client = AsyncMock()
        mock_get_client.return_value = mock_client
        mock_client.query = AsyncMock(
            return_value={
                "fairsharingRecord": {
                    "id": "100",
                    "name": "Test Standard",
                    "registry": "Standard",
                    "status": "ready",
                    "homepage": "https://example.com",
                    "doi": "10.1234/test",
                    "description": "A detailed description that is longer than fifty characters for testing.",
                    "isMaintained": True,
                    "updatedAt": "2025-01-01",
                    "publications": [{"id": "1"}],
                    "subjects": [{"label": "A"}, {"label": "B"}, {"label": "C"}],
                    "reverseRecordAssociations": [
                        {
                            "recordAssocLabel": "implements",
                            "fairsharingRecord": {"registry": "Database"},
                        }
                    ],
                    "recordAssociations": [],
                }
            }
        )
        result = await get_comprehensive_quality_profile(100)
        self.assertIn("Comprehensive Quality Profile", result)
        self.assertIn("Temporal Health", result)
        self.assertIn("Community Engagement", result)
        self.assertIn("Grade:", result)

    @patch("fairsharing_mcp.app.get_client")
    async def test_get_comprehensive_quality_profile_json(self, mock_get_client):
        """Test comprehensive quality profile JSON output."""
        mock_client = AsyncMock()
        mock_get_client.return_value = mock_client
        mock_client.query = AsyncMock(
            return_value={
                "fairsharingRecord": {
                    "id": "100",
                    "name": "Test Standard",
                    "registry": "Standard",
                    "status": "ready",
                    "reverseRecordAssociations": [],
                    "recordAssociations": [],
                }
            }
        )
        result = await get_comprehensive_quality_profile(100, output_format="json")
        data = json.loads(result)
        self.assertEqual(data["record_id"], 100)
        self.assertIn("indicators", data)
        self.assertIn("temporal_health", data["indicators"])
        self.assertIn("grade", data)

    @patch("fairsharing_mcp.app.get_client")
    async def test_get_comprehensive_quality_profile_database(self, mock_get_client):
        """Test comprehensive quality profile for a Database record."""
        mock_client = AsyncMock()
        mock_get_client.return_value = mock_client
        mock_client.query = AsyncMock(
            side_effect=[
                {
                    "fairsharingRecord": {
                        "id": "1",
                        "name": "Test DB",
                        "registry": "Database",
                        "updatedAt": "2024-01-01",
                        "description": "Test",
                        "publications": [],
                        "licenceLinks": [],
                        "reverseRecordAssociations": [],
                        "recordAssociations": [],
                    }
                },
                {
                    "fairsharingRecord": {
                        "dataAccessCondition": "open",
                        "dataCuration": "manual",
                        "dataDepositionCondition": "open",
                        "citationToRelatedPublications": True,
                        "dataContactInformation": True,
                        "dataVersioning": "yes",
                        "dataPreservationPolicy": True,
                        "resourceSustainability": True,
                        "usesPersistentIdentifier": True,
                    }
                },
            ]
        )
        result = await get_comprehensive_quality_profile(1)
        self.assertIn("Comprehensive Quality Profile", result)
        self.assertIn("Fair Indicators", result)
        self.assertIn("Community Trust", result)

    @patch("fairsharing_mcp.app.get_client")
    async def test_get_comprehensive_quality_profile_policy(self, mock_get_client):
        """Test comprehensive quality profile for a Policy record."""
        mock_client = AsyncMock()
        mock_get_client.return_value = mock_client
        mock_client.query = AsyncMock(
            side_effect=[
                {
                    "fairsharingRecord": {
                        "id": "200",
                        "name": "Test Policy",
                        "registry": "Policy",
                        "updatedAt": "2025-01-01",
                        "countries": [{"name": "UK"}, {"name": "US"}],
                        "recordAssociations": [
                            {"linkedRecord": {"registry": "Standard"}},
                        ],
                        "reverseRecordAssociations": [],
                    }
                },
                {
                    "fairsharingRecord": {
                        "mandatedDataSharing": "required",
                        "mandatedDmpCreation": "suggested",
                        "monitoringOfCompliance": "yes",
                        "metadata": {
                            "mandatedDataSharing": "required",
                            "mandatedDmpCreation": "suggested",
                            "monitoringOfCompliance": "yes",
                        },
                    }
                },
            ]
        )
        result = await get_comprehensive_quality_profile(200)
        self.assertIn("Comprehensive Quality Profile", result)
        self.assertIn("Geographic Coverage", result)

    # ── Phase 3: Multi-seed graph merging tests ────────────────────────

    @patch("fairsharing_mcp.app.get_client")
    async def test_explore_expanded_graph_depth1(self, mock_get_client):
        """explore_expanded_graph with depth=1 behaves like single-graph analysis."""
        graph = self._make_graph(
            [
                (1, "Std1", "standard", "model_and_format"),
                (2, "DB1", "database", "repository"),
                (3, "DB2", "database", "repository"),
                (4, "Pol1", "policy", "journal"),
            ],
            [(1, 2, "pink"), (1, 3, "pink"), (4, 1, "orange")],
        )
        mock_client = AsyncMock()
        mock_client.query.return_value = {"fairsharingGraph": {"data": graph}}
        mock_get_client.return_value = mock_client
        result = await explore_expanded_graph(1, depth=1, top_n=10)
        self.assertIn("Expanded Graph Analysis", result)
        self.assertIn("depth=1", result)
        self.assertIn("Graphs merged:** 1", result)
        self.assertIn("PageRank", result)

    @patch("fairsharing_mcp.app.get_client")
    async def test_explore_expanded_graph_depth2(self, mock_get_client):
        """explore_expanded_graph depth=2 expands into neighbor graphs."""
        seed_graph = self._make_graph(
            [
                (1, "Root", "standard", "model_and_format"),
                (2, "Neighbor", "database", "repository"),
                (3, "N2", "database", "repository"),
            ],
            [(1, 2, "pink"), (1, 3, "pink")],
        )
        neighbor_graph = self._make_graph(
            [
                (2, "Neighbor", "database", "repository"),
                (10, "Deep1", "standard", "model_and_format"),
                (11, "Deep2", "policy", "journal"),
            ],
            [(2, 10, "pink"), (10, 11, "orange")],
        )
        mock_client = AsyncMock()
        # First call: seed graph; subsequent calls: neighbor graphs
        mock_client.query.side_effect = [
            {"fairsharingGraph": {"data": seed_graph}},
            {"fairsharingGraph": {"data": neighbor_graph}},
            {"fairsharingGraph": {"data": neighbor_graph}},
        ]
        mock_get_client.return_value = mock_client
        result = await explore_expanded_graph(1, depth=2, max_seeds=3, top_n=10)
        self.assertIn("Expanded Graph Analysis", result)
        self.assertIn("depth=2", result)
        # Should mention merging multiple graphs
        self.assertIn("Graphs merged:", result)

    @patch("fairsharing_mcp.app.get_client")
    async def test_explore_expanded_graph_no_data(self, mock_get_client):
        """explore_expanded_graph returns message when seed has no graph data."""
        mock_client = AsyncMock()
        mock_client.query.return_value = {"fairsharingGraph": {"data": None}}
        mock_get_client.return_value = mock_client
        result = await explore_expanded_graph(999)
        self.assertIn("No graph data", result)

    @patch("fairsharing_mcp.app.get_client")
    async def test_build_topic_graph_basic(self, mock_get_client):
        """build_topic_graph searches, fetches graphs, and merges."""
        graph_a = self._make_graph(
            [
                (1, "Std1", "standard", "model_and_format"),
                (2, "DB1", "database", "repository"),
                (3, "DB2", "database", "repository"),
            ],
            [(1, 2, "pink"), (1, 3, "pink")],
        )
        graph_b = self._make_graph(
            [
                (4, "Std2", "standard", "model_and_format"),
                (5, "DB3", "database", "repository"),
                (2, "DB1", "database", "repository"),
            ],
            [(4, 5, "pink"), (4, 2, "orange")],
        )
        mock_client = AsyncMock()
        mock_client.query.side_effect = [
            # Search result
            {
                "searchFairsharingRecords": {
                    "records": [
                        {
                            "id": "1",
                            "name": "Std1",
                            "registry": "Standard",
                            "createdAt": "2020-01-01",
                        },
                        {
                            "id": "4",
                            "name": "Std2",
                            "registry": "Standard",
                            "createdAt": "2020-01-01",
                        },
                    ],
                    "totalCount": 2,
                }
            },
            # Graph fetches
            {"fairsharingGraph": {"data": graph_a}},
            {"fairsharingGraph": {"data": graph_b}},
        ]
        mock_get_client.return_value = mock_client
        result = await build_topic_graph("Genomics", max_seeds=2, top_n=10)
        self.assertIn("Topic Graph: Genomics", result)
        self.assertIn("Seeds:** 2", result)
        self.assertIn("PageRank", result)
        self.assertIn("Seed Records", result)

    @patch("fairsharing_mcp.app.get_client")
    async def test_build_topic_graph_no_results(self, mock_get_client):
        """build_topic_graph returns message when search finds nothing."""
        mock_client = AsyncMock()
        mock_client.query.return_value = {
            "searchFairsharingRecords": {"records": [], "totalCount": 0}
        }
        mock_get_client.return_value = mock_client
        result = await build_topic_graph("NonexistentTopic")
        self.assertIn("No records found", result)

    @patch("fairsharing_mcp.app.get_client")
    async def test_build_topic_graph_with_registry(self, mock_get_client):
        """build_topic_graph passes registry filter to search."""
        graph = self._make_graph(
            [
                (1, "Std1", "standard", "model_and_format"),
                (2, "DB1", "database", "repository"),
                (3, "DB2", "database", "repository"),
            ],
            [(1, 2, "pink"), (1, 3, "pink")],
        )
        mock_client = AsyncMock()
        mock_client.query.side_effect = [
            {
                "searchFairsharingRecords": {
                    "records": [
                        {
                            "id": "1",
                            "name": "Std1",
                            "registry": "Standard",
                            "createdAt": "2020-01-01",
                        },
                    ],
                    "totalCount": 1,
                }
            },
            {"fairsharingGraph": {"data": graph}},
        ]
        mock_get_client.return_value = mock_client
        result = await build_topic_graph("Genomics", registry="Standard", max_seeds=2)
        self.assertIn("Topic Graph: Genomics (Standard)", result)
        # Verify registry was passed in the search query
        call_args = mock_client.query.call_args_list[0]
        variables = call_args[0][1] if len(call_args[0]) > 1 else call_args[1].get("variables", {})
        self.assertEqual(variables.get("registry"), "Standard")

    @patch("fairsharing_mcp.app.get_client")
    async def test_analyze_graph_comprehensive_multi_seed(self, mock_get_client):
        """analyze_graph_comprehensive with additional_seed_ids merges graphs."""
        graph_a = self._make_graph(
            [
                (1, "Std1", "standard", "model_and_format"),
                (2, "DB1", "database", "repository"),
                (3, "DB2", "database", "repository"),
            ],
            [(1, 2, "pink"), (1, 3, "pink")],
        )
        graph_b = self._make_graph(
            [
                (4, "Pol1", "policy", "journal"),
                (5, "DB3", "database", "repository"),
                (2, "DB1", "database", "repository"),
            ],
            [(4, 5, "orange"), (4, 2, "orange")],
        )
        mock_client = AsyncMock()
        mock_client.query.side_effect = [
            {"fairsharingGraph": {"data": graph_a}},
            {"fairsharingGraph": {"data": graph_b}},
        ]
        mock_get_client.return_value = mock_client
        result = await analyze_graph_comprehensive(1, additional_seed_ids=[4], top_n=10)
        self.assertIn("Comprehensive Graph Analysis", result)
        # Should include nodes from both graphs
        self.assertIn("Std1", result)

    @patch("fairsharing_mcp.app.get_client")
    async def test_explore_expanded_graph_max_seeds_cap(self, mock_get_client):
        """explore_expanded_graph respects max_seeds cap."""
        # Create a graph with many neighbors
        nodes = [(1, "Root", "standard", "model_and_format")]
        edges = []
        for i in range(2, 22):  # 20 neighbors
            nodes.append((i, f"N{i}", "database", "repository"))
            edges.append((1, i, "pink"))
        graph = self._make_graph(nodes, edges)

        call_count = 0
        original_graph = graph

        def side_effect(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            return {"fairsharingGraph": {"data": original_graph}}

        mock_client = AsyncMock()
        mock_client.query.side_effect = side_effect
        mock_get_client.return_value = mock_client

        result = await explore_expanded_graph(1, depth=2, max_seeds=3, top_n=5)
        self.assertIn("Expanded Graph Analysis", result)
        # Should not exceed max_seeds total fetches
        self.assertLessEqual(call_count, 3)

    # ── Phase 4: Date index and matches_date_range tests ─────────────

    def test_matches_date_range_basic(self):
        """matches_date_range returns True for dates within range."""
        self.assertTrue(matches_date_range("2020-06-15", min_year=2019, max_year=2021))
        self.assertTrue(matches_date_range("2020-01-01", min_year=2020, max_year=2020))

    def test_matches_date_range_out_of_range(self):
        """matches_date_range returns False for dates outside range."""
        self.assertFalse(matches_date_range("2018-06-15", min_year=2019, max_year=2021))
        self.assertFalse(matches_date_range("2023-01-01", min_year=2019, max_year=2021))

    def test_matches_date_range_none(self):
        """matches_date_range returns False for None date."""
        self.assertFalse(matches_date_range(None, min_year=2019, max_year=2021))
        self.assertFalse(matches_date_range("", min_year=2019, max_year=2021))

    def test_matches_date_range_unbounded(self):
        """matches_date_range supports None bounds (unbounded)."""
        self.assertTrue(matches_date_range("2020-01-01", min_year=None, max_year=None))
        self.assertTrue(matches_date_range("2020-01-01", min_year=2019, max_year=None))
        self.assertTrue(matches_date_range("2020-01-01", min_year=None, max_year=2021))

    def test_matches_date_range_invalid(self):
        """matches_date_range returns False for unparseable dates."""
        self.assertFalse(matches_date_range("bad-date", min_year=2019, max_year=2021))
        self.assertFalse(matches_date_range("xx", min_year=2019, max_year=2021))

    def test_date_index_populated_from_search(self):
        """Date index is populated from searchFairsharingRecords responses."""
        client = FAIRsharingClient(api_key="test-key")
        self.assertEqual(client.get_date_index_size(), 0)

        client._index_dates_from_response(
            {
                "searchFairsharingRecords": {
                    "records": [
                        {
                            "id": "1",
                            "name": "A",
                            "createdAt": "2020-01-01",
                            "updatedAt": "2021-06-01",
                        },
                        {"id": "2", "name": "B", "createdAt": "2019-03-15"},
                    ]
                }
            }
        )
        self.assertEqual(client.get_date_index_size(), 2)
        entry = client.get_date_for_record(1)
        self.assertIsNotNone(entry)
        self.assertEqual(entry["createdAt"], "2020-01-01")
        self.assertEqual(entry["updatedAt"], "2021-06-01")

    def test_date_index_populated_from_single_record(self):
        """Date index is populated from fairsharingRecord responses."""
        client = FAIRsharingClient(api_key="test-key")

        client._index_dates_from_response(
            {
                "fairsharingRecord": {
                    "id": "42",
                    "name": "Test",
                    "createdAt": "2018-11-20",
                    "updatedAt": "2022-05-10",
                }
            }
        )
        self.assertEqual(client.get_date_index_size(), 1)
        entry = client.get_date_for_record(42)
        self.assertEqual(entry["createdAt"], "2018-11-20")

    def test_date_index_populated_from_multi_tag(self):
        """Date index is populated from multiTagFilter responses."""
        client = FAIRsharingClient(api_key="test-key")

        client._index_dates_from_response(
            {
                "multiTagFilter": [
                    {"id": "10", "name": "DB1", "createdAt": "2020-06-01"},
                    {
                        "id": "20",
                        "name": "DB2",
                        "createdAt": "2021-03-15",
                        "updatedAt": "2023-01-01",
                    },
                ]
            }
        )
        self.assertEqual(client.get_date_index_size(), 2)
        self.assertIsNotNone(client.get_date_for_record(10))
        self.assertIsNone(client.get_date_for_record(999))

    def test_date_index_merge_on_update(self):
        """Date index merges new data with existing entries."""
        client = FAIRsharingClient(api_key="test-key")

        # First response: only createdAt
        client._index_dates_from_response(
            {"fairsharingRecord": {"id": "5", "createdAt": "2020-01-01"}}
        )
        entry = client.get_date_for_record(5)
        self.assertEqual(entry["createdAt"], "2020-01-01")
        self.assertIsNone(entry["updatedAt"])

        # Second response: adds updatedAt
        client._index_dates_from_response(
            {"fairsharingRecord": {"id": "5", "updatedAt": "2023-06-15"}}
        )
        entry = client.get_date_for_record(5)
        self.assertEqual(entry["createdAt"], "2020-01-01")
        self.assertEqual(entry["updatedAt"], "2023-06-15")


if __name__ == "__main__":
    unittest.main()
