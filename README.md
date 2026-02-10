# fairsharing-mcp

An MCP (Model Context Protocol) server that exposes the [FAIRsharing](https://fairsharing.org) GraphQL API as **95 tools** for discovering, analyzing, and comparing data standards, databases, and policies in life sciences research.

[FAIRsharing](https://fairsharing.org) is a curated registry of standards, databases, and data policies used and recommended by journals, funders, and institutions. This MCP server gives AI assistants structured access to its knowledge graph.

## Prerequisites

- Python 3.10+
- [uv](https://docs.astral.sh/uv/) (recommended) or pip
- A FAIRsharing API key (free — register at [fairsharing.org](https://fairsharing.org/users/sign_in))

## Installation

```bash
git clone https://github.com/fairsharing/fairsharing-mcp.git
cd fairsharing-mcp
uv sync
```

## MCP Client Configuration

### Claude Desktop

Add to your Claude Desktop config (`~/Library/Application Support/Claude/claude_desktop_config.json` on macOS, `%APPDATA%\Claude\claude_desktop_config.json` on Windows):

```json
{
  "mcpServers": {
    "fairsharing": {
      "command": "uv",
      "args": ["--directory", "/path/to/fairsharing-mcp", "run", "fairsharing-mcp"],
      "env": {
        "FAIRSHARING_API_KEY": "your-api-key"
      }
    }
  }
}
```

### VS Code (Copilot / Claude Code)

Add to `.vscode/settings.json`:

```json
{
  "mcp": {
    "servers": {
      "fairsharing": {
        "command": "uv",
        "args": ["--directory", "/path/to/fairsharing-mcp", "run", "fairsharing-mcp"],
        "env": {
          "FAIRSHARING_API_KEY": "your-api-key"
        }
      }
    }
  }
}
```

## Environment Variables

| Variable | Required | Description |
|----------|----------|-------------|
| `FAIRSHARING_API_KEY` | Yes | API key from [fairsharing.org](https://fairsharing.org/users/sign_in) |
| `FAIRSHARING_API_URL` | No | GraphQL endpoint (default: `https://api.fairsharing.org/graphql/`) |
| `FAIRSHARING_MAX_SCAN` | No | Max records to scan for date filtering (default: 2000, max: 50000) |
| `FAIRSHARING_MAX_PER_PAGE` | No | Max results per page (default: 50, API cap: 50) |
| `FAIRSHARING_TRUNCATION_WARNING` | No | Show truncation warnings (default: true, set to 0 to disable) |

See `.env.example` for the full list including display limits.

## Tools (95 total)

All tools are prefixed with `fairsharing_` and support both `markdown` (default) and `json` output formats via the `output_format` parameter.

### Search & Discovery (6 tools)

| Tool | Description |
|------|-------------|
| `fairsharing_search_records` | Search records with filters (registry, subjects, domains, countries, etc.) |
| `fairsharing_search_records_by_license` | Search records filtered by licence type |
| `fairsharing_count_records` | Count matching records with filters |
| `fairsharing_count_fair_records` | Count records matching FAIR indicator criteria |
| `fairsharing_advanced_filter_records` | Search with all filters including FAIR indicators |
| `fairsharing_search_by_doi` | Look up a record by DOI or FAIRsharing URL |

### Records (6 tools)

| Tool | Description |
|------|-------------|
| `fairsharing_get_record` | Get detailed information about a record |
| `fairsharing_get_record_graph` | Get a record's relationship graph |
| `fairsharing_get_record_types` | List all record types |
| `fairsharing_filter_records_by_date` | Find records by creation/update year range |
| `fairsharing_get_records_batch` | Bulk fetch 2-50 records by ID list |
| `fairsharing_find_referencing_records` | Reverse-lookup records that reference a given record |

### Taxonomy (11 tools)

| Tool | Description |
|------|-------------|
| `fairsharing_list_subjects` | List scientific subjects (paginated) |
| `fairsharing_search_subjects` | Search subjects by name |
| `fairsharing_get_subject` | Get subject details with hierarchy |
| `fairsharing_list_domains` | List technical domains (paginated) |
| `fairsharing_search_domains` | Search domains by name |
| `fairsharing_get_domain` | Get domain details with hierarchy |
| `fairsharing_list_taxonomies` | List species taxonomies (paginated) |
| `fairsharing_search_taxonomies` | Search taxonomies by name |
| `fairsharing_browse_subject_hierarchy` | Browse subject parent/child relationships |
| `fairsharing_analyze_subject_landscape` | Analyze records distribution for a subject |
| `fairsharing_analyze_taxonomy_landscape` | Analyze records distribution for taxonomies |

### Organisations & Countries (6 tools)

| Tool | Description |
|------|-------------|
| `fairsharing_list_organisations` | List organisations (paginated) |
| `fairsharing_search_organisations` | Search organisations by name |
| `fairsharing_get_records_by_organisation` | Get records associated with an organisation |
| `fairsharing_list_countries` | List countries |
| `fairsharing_analyze_country_landscape` | Analyze records for a country |
| `fairsharing_analyze_regional_distribution` | Compare records across regions |

### Standards (7 tools)

| Tool | Description |
|------|-------------|
| `fairsharing_find_standards_for_database` | Find standards used by a database |
| `fairsharing_find_databases_for_standard` | Find databases implementing a standard |
| `fairsharing_analyze_standard_adoption` | Analyze adoption metrics for a standard |
| `fairsharing_get_standard_quality_profile` | Quality scorecard for a standard |
| `fairsharing_compute_maturity_index` | Platform-wide Standards Maturity Index |
| `fairsharing_find_emerging_standards` | Discover emerging/recently-created standards |
| `fairsharing_find_endorsed_but_unadopted` | Find policy-endorsed standards lacking implementations |

### Quality & FAIR Indicators (7 tools)

| Tool | Description |
|------|-------------|
| `fairsharing_assess_database_indicators` | Search databases filtered by FAIR indicators |
| `fairsharing_get_database_quality_profile` | Detailed FAIR quality profile for a database |
| `fairsharing_compare_databases_quality` | Side-by-side quality comparison |
| `fairsharing_rank_databases_by_quality` | Rank databases by FAIR score |
| `fairsharing_get_unified_quality_score` | Normalized 0-100 quality score (any record type) |
| `fairsharing_compare_unified_quality` | Compare quality across mixed record types |
| `fairsharing_get_comprehensive_quality_profile` | Domain-specific comprehensive scoring |

### Policies (7 tools)

| Tool | Description |
|------|-------------|
| `fairsharing_get_policy_details` | Get policy details with mandates |
| `fairsharing_get_policy_quality_profile` | Quality scorecard for a policy |
| `fairsharing_analyze_policy_mandates` | Analyze mandates across policies |
| `fairsharing_compare_policies_by_country` | Compare policies between countries |
| `fairsharing_trace_policy_impact` | Trace standards/databases linked to a policy |
| `fairsharing_find_policy_gaps` | Find gaps in policy coverage for a subject |
| `fairsharing_detect_policy_conflicts` | Detect conflicts between 2-5 policies |

### Graph Structure (7 tools)

| Tool | Description |
|------|-------------|
| `fairsharing_get_relationship_types` | List available relationship types |
| `fairsharing_get_collection_contents` | Get records in a collection |
| `fairsharing_find_graph_hubs` | Find highly-connected nodes in a record's graph |
| `fairsharing_analyze_record_ecosystem` | Analyze a record's associations by type |
| `fairsharing_find_record_connections` | Find path between two records in a graph |
| `fairsharing_detect_circular_dependencies` | Detect cycles in a record's graph |
| `fairsharing_trace_influence_chain` | Trace upstream/downstream influence chains |

### Graph Analysis (13 tools)

Advanced algorithms operating on a record's local neighborhood graph. Pure Python, no networkx dependency.

| Tool | Description |
|------|-------------|
| `fairsharing_find_semantic_path` | Weighted shortest path (Dijkstra) |
| `fairsharing_compute_pagerank` | Weighted PageRank influence ranking |
| `fairsharing_detect_communities` | Label propagation community detection |
| `fairsharing_find_similar_records` | Bipartite projection similarity (Jaccard) |
| `fairsharing_find_multiple_paths` | Yen's K-shortest paths |
| `fairsharing_compute_betweenness_centrality` | Brandes' betweenness centrality |
| `fairsharing_find_dependency_clusters` | Tarjan's strongly connected components |
| `fairsharing_find_cross_graph_path` | Path finding across two merged graphs |
| `fairsharing_analyze_path_criticality` | Path finding + betweenness annotation |
| `fairsharing_analyze_graph_comprehensive` | Combined PageRank + communities + centrality |
| `fairsharing_find_path_across_graphs` | Multi-record graph path finding |
| `fairsharing_explore_expanded_graph` | Multi-hop expanded neighborhood analysis |
| `fairsharing_build_topic_graph` | Topic-level graph from search results |

### Comparison & Compliance (9 tools)

| Tool | Description |
|------|-------------|
| `fairsharing_compare_records` | Side-by-side comparison of two records |
| `fairsharing_compare_multiple_records` | Compare 2-50 records at once |
| `fairsharing_compare_subject_landscapes` | Compare record distributions across subjects |
| `fairsharing_compare_collections` | Compare two collections |
| `fairsharing_check_policy_database_compliance` | Check if a database complies with a policy |
| `fairsharing_analyze_deprecation_impact` | Analyze impact of deprecating a record |
| `fairsharing_find_compliant_standards` | Find standards satisfying multiple policies |
| `fairsharing_assess_dmp_compliance` | Full DMP compliance assessment |
| `fairsharing_analyze_transitive_impact` | Multi-hop BFS impact analysis |

### Discovery & Utilities (14 tools)

| Tool | Description |
|------|-------------|
| `fairsharing_get_statistics` | Platform-wide statistics |
| `fairsharing_aggregate_by_field` | Aggregate records by field (registry, subject, etc.) |
| `fairsharing_search_publications` | Search publications linked to records |
| `fairsharing_find_orphan_records` | Find records missing relationships |
| `fairsharing_suggest_graph_starting_points` | Suggest records with rich graphs |
| `fairsharing_get_registries` | List registry types |
| `fairsharing_list_licences` | List available licences |
| `fairsharing_check_api_health` | Verify API connectivity and auth |
| `fairsharing_find_databases_by_standard` | Find databases by standard name/ID |
| `fairsharing_find_deprecated_resources` | Find deprecated resources with fallback search |
| `fairsharing_suggest_related_resources` | Suggest related records |
| `fairsharing_recommend_tools` | Get tool recommendations for a query |
| `fairsharing_suggest_workflow` | Get step-by-step workflow for an intent |
| `fairsharing_explain_fairsharing` | Static reference docs (no API call) |

### Curator (2 tools)

| Tool | Description |
|------|-------------|
| `fairsharing_audit_metadata_completeness` | Audit a record for missing metadata |
| `fairsharing_batch_audit_metadata` | Batch audit metadata for multiple records |

## Usage Examples

### Search for genomics databases

```
Use fairsharing_search_records with subjects=["Genomics"] and registry=["Database"]
```

### DMP compliance check

```
Use fairsharing_assess_dmp_compliance with a policy ID and list of database IDs to check
if your databases meet a funder's data management requirements.
```

### Explore a record's ecosystem

```
1. fairsharing_get_record to get details
2. fairsharing_analyze_record_ecosystem to see relationships
3. fairsharing_compute_pagerank to find influential neighbors
```

### Compare policies across countries

```
Use fairsharing_compare_policies_by_country with countries=["United Kingdom", "United States"]
to compare data sharing mandates.
```

### Standards maturity assessment

```
Use fairsharing_compute_maturity_index to see the platform-wide Standards Maturity Index,
ranking standards by adoption, policy endorsement, and stability.
```

## Output Formats

All 95 tools accept `output_format` parameter:

- **`"markdown"`** (default) — Human-readable formatted output
- **`"json"`** — Machine-readable structured data, suitable for programmatic chaining between tools

## Architecture

```
server.py -> tools/__init__.py -> tools/*.py -> app.py -> client.py -> FAIRsharing GraphQL API
```

- **FastMCP** framework with stdio transport
- **Async GraphQL client** with token bucket rate limiting (5 RPS), LRU response cache (500 entries), connection pooling
- **95 tools** across 12 modules with MCP annotations (`readOnlyHint`, `idempotentHint`, `openWorldHint`)
- **Pydantic Field validation** on all tool parameters (range constraints, patterns, descriptions)
- **Pure Python** graph algorithms (no networkx) — CPU-bound work offloaded to `asyncio.to_thread()`

See `CLAUDE.md` for detailed architecture documentation.

## Development

```bash
# Run all tests (256 tests)
python -m pytest tests/test_server.py

# Run specific tests
python -m pytest tests/test_server.py -k "pagerank" -v

# Lint
ruff check src/ tests/

# Format
ruff format src/ tests/
```

## Limitations

- **Local graph scope**: Graph analysis tools operate on a single record's local neighborhood, not the full platform graph. Use `fairsharing_explore_expanded_graph` or `fairsharing_build_topic_graph` for broader analysis.
- **No server-side date filtering**: The FAIRsharing API has no date range filter. Date filtering is done client-side by scanning pages (configurable via `FAIRSHARING_MAX_SCAN`).
- **Rate limiting**: The client rate-limits to 5 requests/second with burst of 3. Large batch operations may take time.
- **FAIR indicator coverage**: Not all records have complete FAIR indicator data. Quality scores include confidence ratings when data is sparse.

## License

MIT
