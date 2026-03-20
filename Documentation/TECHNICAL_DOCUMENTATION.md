# FAIRsharing MCP Server — Technical Documentation

## Table of Contents

1. [Executive Summary](#1-executive-summary)
2. [System Architecture](#2-system-architecture)
3. [Core Infrastructure](#3-core-infrastructure)
4. [GraphQL API Layer](#4-graphql-api-layer)
5. [Tool Modules — Complete Reference](#5-tool-modules--complete-reference)
6. [Graph Analysis Engine](#6-graph-analysis-engine)
7. [Quality Scoring System](#7-quality-scoring-system)
8. [Output Format System](#8-output-format-system)
9. [Configuration Reference](#9-configuration-reference)
10. [Test Infrastructure](#10-test-infrastructure)
11. [Deployment](#11-deployment)
12. [Design Decisions & Trade-offs](#12-design-decisions--trade-offs)

---

## 1. Executive Summary

The FAIRsharing MCP Server is a Model Context Protocol (MCP) server that exposes the [FAIRsharing](https://fairsharing.org) GraphQL API as **96 structured tools** across **11 domain modules**. FAIRsharing is a curated registry of data standards, databases, and policies for the life sciences, used by researchers, funders, and journal publishers to discover and evaluate FAIR (Findable, Accessible, Interoperable, Reusable) resources.

### Key Metrics

| Metric | Value |
|--------|-------|
| Total MCP tools | 96 |
| Tool modules | 11 |
| Supporting modules | 10 |
| Lines of code (source) | 18,207 |
| Lines of code (tests) | 7,781 |
| Test count | 282 |
| GraphQL query constants | 27 |
| Graph algorithms | 8 (pure Python, no networkx) |
| Python version | 3.10+ |
| Dependencies | 3 (mcp, httpx, python-dotenv) |

### Technology Stack

| Component | Technology |
|-----------|------------|
| Language | Python 3.10+ |
| MCP framework | FastMCP (`mcp[cli] >= 1.2.0`) |
| Transport | STDIO (JSON-RPC over stdin/stdout) |
| HTTP client | httpx (`>= 0.27.0`) with persistent connection pooling |
| API protocol | GraphQL |
| Build system | Hatchling |
| Linter/Formatter | Ruff (target: py310, line-length: 100) |
| Test framework | unittest + IsolatedAsyncioTestCase |
| Package manager | uv |

---

## 2. System Architecture

### 2.1 High-Level Data Flow

```
┌─────────────┐     STDIO (JSON-RPC)     ┌──────────────────┐
│  LLM Client ├──────────────────────────►│   server.py      │
│  (Claude,   │◄──────────────────────────┤   (entry point)  │
│   GPT, etc) │                           └────────┬─────────┘
└─────────────┘                                    │
                                                   │ imports
                                          ┌────────▼─────────┐
                                          │ tools/__init__.py │
                                          │ (registers all    │
                                          │  95 @mcp.tool()   │
                                          │  decorators)      │
                                          └────────┬─────────┘
                                                   │
                              ┌─────────────────────┼─────────────────────┐
                              │                     │                     │
                    ┌─────────▼──────┐   ┌─────────▼──────┐   ┌─────────▼──────┐
                    │  11 tool       │   │  formatters.py │   │  helpers.py    │
                    │  modules       │   │  constants.py  │   │  graph_utils.py│
                    │  (search,      │   │  config.py     │   │  validation.py │
                    │   records, ..) │   │  queries.py    │   │                │
                    └────────┬───────┘   └────────────────┘   └────────────────┘
                             │
                             │ app.get_client() at call time
                    ┌────────▼───────┐
                    │   app.py       │
                    │   (FastMCP     │
                    │    instance +  │
                    │    client      │
                    │    singleton)  │
                    └────────┬───────┘
                             │
                    ┌────────▼───────┐
                    │  client.py     │
                    │  (rate limit,  │
                    │   cache, retry,│     HTTPS + GraphQL
                    │   date index)  ├────────────────────────►  api.fairsharing.org
                    └────────────────┘
```

### 2.2 Module Dependency Graph

```
server.py
  └── tools/__init__.py
       ├── tools/search.py        ──┐
       ├── tools/records.py        │
       ├── tools/taxonomy.py       │
       ├── tools/organisations.py  │
       ├── tools/standards.py      ├──► app.py (mcp instance + get_client)
       ├── tools/quality.py        │         └── client.py (FAIRsharingClient)
       ├── tools/policies.py       │
       ├── tools/graph.py          │
       ├── tools/graph_analysis.py │
       ├── tools/comparison.py     │
       ├── tools/discovery.py      │
       └── tools/curator.py       ──┘
             │
             ├── formatters.py     (output formatting, scoring)
             ├── helpers.py        (fallback fetchers, mandate extraction)
             ├── graph_utils.py    (ParsedGraph, merge, thread offloading)
             ├── queries.py        (27 GraphQL query constants)
             ├── constants.py      (validation sets, weights, thresholds)
             ├── config.py         (environment-based configuration)
             └── validation.py     (input sanitization)
```

### 2.3 Critical Design Constraint: Circular Import Avoidance

`app.py` must **never** import from `tools/`. All tool modules import `app.get_client()` at call time (not at import time). This enables:

1. **Clean registration**: `tools/__init__.py` imports all 11 modules, triggering `@mcp.tool()` decorators
2. **Single mock target**: All 282 tests patch one location — `fairsharing_mcp.app.get_client`
3. **No import cycles**: The `_compute_quality_for_record()` helper lives in `tools/quality.py` (not `helpers.py`) specifically to avoid a tools → helpers → tools dependency cycle

### 2.4 STDIO Protocol Constraint

The MCP protocol uses stdout for JSON-RPC communication. **`print()` must never be used.** All diagnostic output goes to stderr via Python's `logging` module. Every module configures:

```python
logger = logging.getLogger(__name__)
```

---

## 3. Core Infrastructure

### 3.1 Entry Point — `server.py` (18 lines)

The thinnest possible entry point:

```python
import fairsharing_mcp.tools  # triggers @mcp.tool() registration
from fairsharing_mcp.app import mcp

def main():
    mcp.run(transport="stdio")
```

### 3.2 Application State — `app.py` (61 lines)

Manages two singletons:

| Singleton | Type | Purpose |
|-----------|------|---------|
| `mcp` | `FastMCP("fairsharing")` | MCP server instance; tools register via `@mcp.tool()` |
| `_client` | `FAIRsharingClient` | Lazy-initialized GraphQL client singleton |

**`get_client()`** reads `FAIRSHARING_API_KEY` from the environment on first call, creates the client, and returns it for all subsequent calls.

**Shutdown hook**: `atexit.register(_shutdown_client)` ensures the persistent HTTP connection pool is closed on process exit.

### 3.3 GraphQL Client — `client.py` (393 lines)

The `FAIRsharingClient` class is the single point of contact with the FAIRsharing API. It implements five cross-cutting concerns:

#### 3.3.1 Rate Limiting — Token Bucket

```
Class: _TokenBucket
Default: 5.0 requests/sec, burst of 3
Mechanism: Token accumulation with async lock coordination
```

The token bucket allows brief bursts after idle periods (up to 3 immediate requests), then throttles to the sustained rate. This is more robust than a simple delay when `asyncio.gather()` issues concurrent requests.

#### 3.3.2 Response Cache — LRU with TTL

```
Class: _CacheEntry
Default TTL: 300 seconds (5 minutes)
Max entries: 500
Eviction: LRU (oldest entries evicted when maxsize exceeded)
Cache key: MD5 hash of query + JSON-serialized variables
```

Caching is opt-in per call via `cache=True`. It is used for reference data (subjects, domains, registries, record types) that changes rarely.

#### 3.3.3 Retry Logic — Exponential Backoff

| Error Type | Retries | Backoff |
|------------|---------|---------|
| HTTP 429/402 (rate limit) | Up to 3 | `2^(attempt+1)` + jitter, or `Retry-After` header |
| HTTP 5xx (server error) | Up to 3 | `2^attempt` + jitter |
| Timeout | Up to 3 | `2^attempt` + jitter |
| Network error | Up to 3 | `2^attempt` + jitter |
| HTTP 401 (auth) | 0 | Raises `FAIRsharingAuthError` immediately |
| GraphQL errors | 0 | Raises `FAIRsharingError` immediately |

Jitter: random multiplier in [0.8, 1.2] to prevent thundering herd.

**Per-call overrides:** `client.query()` accepts `timeout: float | None` and `max_retries: int | None` keyword arguments that override the instance defaults for that single call. Used by `advancedSearch` calls which use `timeout=90, max_retries=1` (single long attempt instead of 3 retries) to stay within the MCP client's 120-second limit.

#### 3.3.4 Connection Pooling

The client lazily creates a persistent `httpx.AsyncClient` instance, reusing TCP connections across requests. Headers (`Accept`, `Content-Type`, `X-GraphQL-Key`) are configured at client construction, not per-request.

#### 3.3.5 Opportunistic Date Indexing

Every API response is scanned for `createdAt` and `updatedAt` fields. Indexed records are stored in `_date_index: dict[int, dict[str, str | None]]`. This powers client-side date filtering without extra API calls.

Response types handled:
- `searchFairsharingRecords` → iterates `records` list
- `multiTagFilter` → iterates flat list
- `fairsharingRecord` → single record
- `advancedSearch` → iterates flat list

#### 3.3.6 Error Hierarchy

```
FAIRsharingError (base)
├── FAIRsharingAuthError   (HTTP 401 — invalid API key)
└── FAIRsharingRateLimitError (HTTP 429/402 — rate limit exceeded)
```

Auth errors are **never swallowed** — they propagate through fallback helpers to the tool layer, surfacing configuration problems immediately.

#### 3.3.7 Input Sanitization

`sanitize_variables()` recursively cleans all string values in GraphQL variables:
- Removes Unicode line separators (U+2028, U+2029) that break JSON
- Strips leading/trailing whitespace

### 3.4 Configuration — `config.py` (92 lines)

All configuration is environment-based, read via `os.getenv()` after `load_dotenv()`.

| Function | Env Variable | Default | Range | Purpose |
|----------|-------------|---------|-------|---------|
| `get_max_per_page()` | `FAIRSHARING_MAX_PER_PAGE` | 50 | 1–50 | API page size cap |
| `get_max_scan()` | `FAIRSHARING_MAX_SCAN` | 2000 | 50–50,000 | Max records scanned for date filtering |
| `get_display_limit(key)` | `FAIRSHARING_DISPLAY_MAX_<KEY>` | varies | 0 = unlimited | Per-field display truncation |
| `get_truncation_warning()` | `FAIRSHARING_TRUNCATION_WARNING` | true | bool | Show/hide truncation messages |

**Display limit defaults:**

| Key | Default | Controls |
|-----|---------|----------|
| `associations` | 20 | Record associations per direction |
| `organisations` | 10 | Organisations shown |
| `publications` | 10 | Publications shown |
| `taxonomies` | 20 | Taxonomies shown |
| `subjects` | 5 | Subjects in summaries |
| `domains` | 5 | Domains in summaries |
| `children` | 30 | Hierarchy children |
| `recommended` | 30 | Recommended items |
| `description_chars` | 300 | Description truncation |

### 3.5 Validation — `validation.py` (68 lines)

| Function | Input | Output | Behavior |
|----------|-------|--------|----------|
| `validate_record_id(id)` | `int` | `int` | Raises `ValueError` if ≤ 0 |
| `validate_page_params(page, per_page)` | `int, int` | `(int, int)` | Clamps page ≥ 1, per_page to [1, max_per_page] |
| `validate_query_length(query, max)` | `str, int` | `(str\|None, bool)` | Returns (trimmed, was_truncated); logs warning if truncated |

### 3.6 Helpers — `helpers.py` (163 lines)

| Function | Purpose |
|----------|---------|
| `extract_policy_mandates(record)` | Extracts mandate data from nested `metadata` JSON blob into flat keys |
| `fetch_policy_with_fallback(record_id)` | Tries `GET_POLICY_DETAIL_QUERY`, falls back to `GET_RECORD_QUERY`; re-raises auth errors |
| `fetch_database_quality_with_fallback(record_id)` | Tries `GET_DATABASE_QUALITY_QUERY`, falls back to `GET_RECORD_QUERY`; re-raises auth errors |
| `matches_date_range(date_str, min_year, max_year)` | Pure function: parses ISO date, returns `True` if year in range |
| `build_advanced_search_where(**kwargs)` | Maps snake_case FAIR indicator params → camelCase `AdvancedSearchAttributes` dict; wraps string values in lists for LIST-type fields; omits `None` values |

### 3.7 Graph Utilities — `graph_utils.py` (215 lines)

#### Data Structures

**`NodeInfo`** (dataclass):
```python
key: str          # Node identifier (record ID as string)
label: str        # Human-readable name
registry: str     # "Database", "Standard", "Policy", "Collection"
record_type: str  # Specific type (e.g., "repository", "model/format")
status: str       # "ready", "deprecated", etc.
```

**`ParsedGraph`** (dataclass):
```python
nodes: dict[str, NodeInfo]                 # Node lookup by key
edges: list[tuple[str, str, str]]          # (source, target, relationship)
adj: dict[str, set[str]]                   # Undirected adjacency
out_adj: dict[str, list[tuple[str, str]]]  # Directed outgoing: node → [(neighbor, rel)]
in_adj: dict[str, list[tuple[str, str]]]   # Directed incoming: node → [(neighbor, rel)]
name: str                                   # Graph metadata label
```

#### Functions

| Function | Purpose |
|----------|---------|
| `parse_graph(data)` | Parses raw JSON from `GET_GRAPH_QUERY`, maps edge colors → relationships |
| `edge_weight(rel_type)` | Lookup semantic distance from `RELATIONSHIP_WEIGHTS` (default: 5.0) |
| `fetch_and_parse_graph(record_id)` | Async: fetch + parse in one call |
| `merge_graphs(a, b)` | Merge two ParsedGraphs; deduplicate nodes/edges; rebuild adjacency |
| `merge_multiple_graphs(graphs)` | Reduce-merge a list of graphs |
| `run_in_thread(fn, *args)` | Offload CPU-bound function to `asyncio.to_thread()` |

### 3.8 Formatters — `formatters.py` (832 lines)

| Function | Purpose |
|----------|---------|
| `escape_md_table(value)` | Escapes `\|` and newlines for safe markdown table cells |
| `format_record_summary(record)` | Compact markdown: name, type, status, truncated description, subjects, DOI |
| `format_record_detail(record)` | Full markdown: all fields including associations, licences, tags |
| `format_hierarchy_item(item)` | Formats subjects/domains: label, ID, IRI, parents, children, ancestors |
| `format_policy_detail(record)` | Policy-specific: mandate extraction status, mandate fields |
| `format_database_quality_profile(record, score, output_format)` | FAIR indicator breakdown with scoring components |
| `normalize_quality_score(registry, score, max_score)` | Normalizes registry-specific scores to 0–100 unified scale |
| `compute_fair_score_detailed(record)` | Counts FAIR indicators present; returns score, grade, confidence |

---

## 4. GraphQL API Layer

### 4.1 API Endpoint

```
URL:    https://api.fairsharing.org/graphql/
Auth:   X-GraphQL-Key header
Method: POST (JSON body with query + variables)
```

### 4.2 Query Constants (`queries.py` — 425 lines)

The server defines 27 GraphQL query constants, grouped by domain:

#### Record Queries

| Constant | GraphQL Operation | Key Fields |
|----------|------------------|------------|
| `SEARCH_RECORDS_QUERY` | `searchFairsharingRecords` | Full filter set; returns `{records, totalCount, totalPages}` |
| `SEARCH_RECORDS_COMPACT_QUERY` | `searchFairsharingRecords` | Lightweight (no licences/journals); includes `createdAt` |
| `GET_RECORD_QUERY` | `fairsharingRecord` | Full detail: subjects, domains, taxonomies, countries, orgs, publications, licences, associations |
| `GET_RECORD_WITH_ASSOCIATIONS_QUERY` | `fairsharingRecord` | Associations + reverse associations with relationship labels |
| `MULTI_TAG_FILTER_QUERY` | `multiTagFilter` | FAIR indicators, boolean flags, subjects, domains; **returns flat list** (not paginated) |
| `GET_GRAPH_QUERY` | `fairsharingGraph` | Raw JSON graph data (nodes + edges) |
| `GET_RECORD_TYPES_QUERY` | `recordTypes` | Record type definitions per registry |

#### Taxonomy Queries

| Constant | GraphQL Operation |
|----------|------------------|
| `LIST_SUBJECTS_QUERY` | `subjects` (paginated) |
| `SEARCH_SUBJECTS_QUERY` | `subjects` (text search) |
| `GET_SUBJECT_QUERY` | `subject` (by ID with hierarchy) |
| `LIST_DOMAINS_QUERY` | `domains` (paginated) |
| `SEARCH_DOMAINS_QUERY` | `domains` (text search) |
| `GET_DOMAIN_QUERY` | `domain` (by ID with hierarchy) |
| `LIST_TAXONOMIES_QUERY` | `taxonomies` (paginated) |
| `SEARCH_TAXONOMIES_QUERY` | `taxonomies` (text search) |
| `BROWSE_SUBJECTS_QUERY` | `browseSubjects` (full hierarchy tree) |

#### Organisation & Reference Queries

| Constant | GraphQL Operation |
|----------|------------------|
| `LIST_ORGANISATIONS_QUERY` | `organisations` (with countries) |
| `SEARCH_ORGANISATIONS_QUERY` | `organisations` (text search) |
| `LIST_COUNTRIES_QUERY` | `countries` |
| `LIST_LICENCES_QUERY` | `licences` |
| `GET_REGISTRIES_QUERY` | `registries` |
| `GET_LATEST_STATS_QUERY` | `latestStats` |
| `SEARCH_PUBLICATIONS_QUERY` | `publications` (text search) |
| `GET_RELATIONSHIP_LABELS_QUERY` | `recordAssociationLabels` |

#### Policy & Quality Queries

| Constant | GraphQL Operation | Special Fields |
|----------|------------------|----------------|
| `GET_POLICY_DETAIL_QUERY` | `fairsharingRecord` | Includes `metadata` field for mandate extraction |
| `GET_DATABASE_QUALITY_QUERY` | `fairsharingRecord` | Includes all 9 FAIR indicator fields |

#### Advanced Search Queries

| Constant | GraphQL Operation | Key Behaviour |
|----------|------------------|---------------|
| `ADVANCED_SEARCH_QUERY` | `advancedSearch` | Variable `$where: AdvancedSearchAttributes!` + `$q: String`; **returns flat list**; supports 50+ server-side filters (FAIR indicators, `objectTypes`, `registry`, `type`, boolean flags); no `metadata` field (avoid large payloads) |

### 4.3 API Quirks

1. **`multiTagFilter` returns a flat list**, not `{records, totalCount, totalPages}` like `searchFairsharingRecords`. Client-side pagination is required.
2. **`advancedSearch` also returns a flat list.** It supports `$where: AdvancedSearchAttributes!` for server-side FAIR indicator filtering, but the response has no pagination envelope. Three tools use it as primary path with `multiTagFilter` fallback: `assess_database_indicators`, `count_fair_records`, `advanced_filter_records`.
3. **No server-side date filtering.** Date ranges are implemented via client-side scanning (configurable via `max_scan`).
4. **Policy mandate data** lives in a nested `metadata` JSON blob, not flat fields. The `extract_policy_mandates()` helper denormalizes this.
5. **Graph data** is returned as a JSON string (or dict) with `nodes` and `edges` arrays. Edge "colors" encode relationship types.

---

## 5. Tool Modules — Complete Reference

All 96 tools are async functions decorated with `@app.mcp.tool()`. Every tool:
- Returns `str` (either markdown or JSON)
- Accepts `output_format: str = "markdown"` parameter
- Calls `app.get_client()` at invocation time (not import time)

### 5.1 Search Tools — `search.py` (964 lines, 6 tools)

| # | Tool | Parameters | Summary |
|---|------|-----------|---------|
| 1 | `search_records` | query, registry, record_type, status, subjects, domains, taxonomies, countries, organisations, user_defined_tags, licences, journals, is_recommended, is_approved, is_maintained, has_publication, is_implemented, search_and, page, per_page, fallback_on_empty, output_format | Full-featured record search with progressive filter fallback on empty results |
| 2 | `search_records_by_license` | licence, registry, record_type, status, subjects, page, per_page, output_format | Search records by licence name (delegates to `search_records`) |
| 3 | `count_records` | query, registry, record_type, status, subjects, domains, taxonomies, countries, organisations, is_recommended, is_maintained, has_publication, is_implemented, search_and, min_year, max_year, output_format | Count matching records with client-side date filtering |
| 4 | `count_fair_records` | query, registry, record_type, subjects, domains, uses_persistent_identifier, has_preservation_policy, has_resource_sustainability, data_access, data_curation, recommends_database, recommends_standard, is_maintained, is_recommended, min_year, max_year, output_format | Count records matching FAIR quality indicator filters |
| 5 | `advanced_filter_records` | query, registry, record_type, status, subjects, domains, taxonomies, user_defined_tags, is_recommended, is_approved, is_maintained, has_publication, is_implemented, uses_persistent_identifier, has_preservation_policy, has_resource_sustainability, data_access, data_curation, data_deposition_condition, citation_to_publications, data_contact_info, data_versioning, recommends_database, recommends_standard, page, per_page, output_format | All `multiTagFilter` parameters exposed |
| 6 | `search_by_doi` | doi, output_format | DOI lookup with URL normalization (handles doi.org URLs and fairsharing.org URLs) |

### 5.2 Record Tools — `records.py` (793 lines, 7 tools)

| # | Tool | Parameters | Summary |
|---|------|-----------|---------|
| 7 | `get_record` | record_id, output_format | Full record detail |
| 8 | `get_record_graph` | record_id, summary_mode, output_format | Parse and analyze record's knowledge graph |
| 9 | `get_record_types` | bypass_cache, output_format | List all record types per registry |
| 10 | `filter_records_by_date` | query, min_year, max_year, registry, limit, use_updated_at, max_scan, output_format | Client-side date filtering with configurable scan depth |
| 11 | `get_records_batch` | record_ids (2–50), output_format | Bulk fetch multiple records by ID |
| 12 | `find_referencing_records` | record_id, relationship, registry, output_format | Reverse lookup: who references this record? |
| 13 | `resolve_identifier` | identifier, output_format | Resolve a FAIRsharing DOI, URL, or abbreviation to a record ID and canonical URL |

### 5.3 Taxonomy Tools — `taxonomy.py` (811 lines, 11 tools)

| # | Tool | Parameters | Summary |
|---|------|-----------|---------|
| 13 | `list_subjects` | page, per_page, bypass_cache, output_format | Paginated subject listing |
| 14 | `search_subjects` | query, output_format | Subject text search |
| 15 | `get_subject` | subject_id, output_format | Subject detail with hierarchy (parents, children, ancestors) |
| 16 | `list_domains` | page, per_page, bypass_cache, output_format | Paginated domain listing |
| 17 | `search_domains` | query, output_format | Domain text search |
| 18 | `get_domain` | domain_id, output_format | Domain detail with hierarchy |
| 19 | `list_taxonomies` | page, per_page, bypass_cache, output_format | Paginated species taxonomy listing |
| 20 | `search_taxonomies` | query, output_format | Taxonomy text search |
| 21 | `browse_subject_hierarchy` | output_format | Full hierarchical subject tree |
| 22 | `analyze_subject_landscape` | subject, include_deprecated, output_format | Resource distribution for a subject across registries |
| 23 | `analyze_taxonomy_landscape` | taxonomies, output_format | Compare resource coverage across species |

### 5.4 Organisation Tools — `organisations.py` (683 lines, 6 tools)

| # | Tool | Parameters | Summary |
|---|------|-----------|---------|
| 24 | `list_organisations` | page, per_page, bypass_cache, output_format | Paginated organisation listing |
| 25 | `search_organisations` | query, output_format | Organisation text search |
| 26 | `get_records_by_organisation` | organisation, registry, page, per_page, output_format | Records linked to an organisation |
| 27 | `list_countries` | page, per_page, bypass_cache, output_format | Country listing |
| 28 | `analyze_country_landscape` | country, subject, include_deprecated, output_format | Single country's FAIRsharing profile |
| 29 | `analyze_regional_distribution` | regions, subject, output_format | Compare resource counts across countries |

### 5.5 Standards Tools — `standards.py` (1,788 lines, 10 tools)

| # | Tool | Parameters | Summary |
|---|------|-----------|---------|
| 30 | `find_standards_for_database` | record_id, output_format | Standards used/implemented by a database |
| 31 | `find_databases_for_standard` | record_id, countries, output_format | Databases implementing a standard |
| 32 | `analyze_standard_adoption` | record_id, output_format | Adoption breakdown by registry type |
| 33 | `get_standard_quality_profile` | record_id, output_format | Detailed quality scoring (0–10) with component breakdown |
| 34 | `compute_maturity_index` | subjects, min_adoption, damping, output_format | Platform-wide Standard Maturity Index with configurable weights |
| 35 | `find_emerging_standards` | subject, max_age_years, output_format | 3-category classification: emerging, recently-created-unadopted, old-unadopted |
| 36 | `find_endorsed_but_unadopted` | subjects, output_format | Standards recommended by policies but not implemented by databases |
| 37–39 | *(3 additional tools)* | | Standard trends and analysis |

### 5.6 Quality Tools — `quality.py` (1,148 lines, 8 tools)

| # | Tool | Parameters | Summary |
|---|------|-----------|---------|
| 40 | `assess_database_indicators` | query, subjects, domains, data_access, data_curation, ..., output_format | Find databases matching specific FAIR indicator criteria |
| 41 | `get_database_quality_profile` | record_id, output_format | Individual database FAIR indicator scorecard |
| 42 | `compare_databases_quality` | record_ids, output_format | Side-by-side FAIR score comparison |
| 43 | `rank_databases_by_quality` | subjects, domains, countries, max_results, output_format | Ranked databases by FAIR quality score |
| 44 | `get_unified_quality_score` | record_id, output_format | Any record type → normalized 0–100 score |
| 45 | `compare_unified_quality` | record_ids (2–10), output_format | Cross-registry quality comparison on unified 0–100 scale |
| 46 | `get_comprehensive_quality_profile` | record_id, output_format | Domain-specific comprehensive scoring with weighted components |

*Private helper:* `_compute_quality_for_record(record_id)` — dispatches to the appropriate registry scorer, normalizes to 0–100. Lives in `quality.py` (not `helpers.py`) to avoid circular import.

### 5.7 Policy Tools — `policies.py` (1,628 lines, 8 tools)

| # | Tool | Parameters | Summary |
|---|------|-----------|---------|
| 47 | `get_policy_details` | record_id, output_format | Full policy with mandate extraction |
| 48 | `compare_policies_by_country` | countries, policy_type, subject, max_per_country, output_format | Cross-country policy comparison with mandate aggregation |
| 49 | `analyze_policy_mandates` | countries, subject, policy_type, max_policies, output_format | Mandate level distribution across filtered policy set |
| 50 | `trace_policy_impact` | policy_id, output_format | Transitive impact: policy → standards → databases |
| 51 | `find_policy_gaps` | policy_ids, output_format | Identify missing mandate coverage areas |
| 52 | `get_policy_quality_profile` | record_id, output_format | Policy quality scoring (0–10) with component breakdown |
| 53 | `detect_policy_conflicts` | policy_ids (2–5), output_format | Compare policies across 11 mandate/coverage/timing fields with severity ratings |

### 5.8 Graph Tools — `graph.py` (1,026 lines, 7 tools)

| # | Tool | Parameters | Summary |
|---|------|-----------|---------|
| 54 | `analyze_record_ecosystem` | record_id, output_format | Group relationships by type and registry |
| 55 | `find_record_connections` | record_id_1, record_id_2, output_format | How two records connect in the knowledge graph |
| 56 | `find_graph_hubs` | record_id, top_n, output_format | Identify hub nodes by degree centrality |
| 57 | `get_relationship_types` | output_format | All relationship type labels |
| 58 | `get_collection_contents` | record_id, output_format | Records contained in a Collection |
| 59 | `trace_influence_chain` | record_id, max_depth, output_format | BFS relationship traversal tracking chains by depth |
| 60 | `detect_circular_dependencies` | record_id, output_format | Cycle detection in the relationship graph |

### 5.9 Graph Analysis Tools — `graph_analysis.py` (2,746 lines, 13 tools)

*See [Section 6: Graph Analysis Engine](#6-graph-analysis-engine) for detailed algorithm documentation.*

| # | Tool | Parameters | Algorithm | Summary |
|---|------|-----------|-----------|---------|
| 61 | `find_semantic_path` | record_id_1, record_id_2, prefer, output_format | Dijkstra | Shortest weighted path |
| 62 | `compute_pagerank` | record_id, top_n, damping, iterations, output_format | PageRank | Influence ranking |
| 63 | `detect_communities` | record_id, max_iterations, min_community_size, seed, output_format | Label Propagation | Cluster detection |
| 64 | `find_similar_records` | record_id, projection_side, top_n, output_format | Bipartite Projection | Jaccard similarity |
| 65 | `find_multiple_paths` | record_id_1, record_id_2, k, output_format | Yen's K-Shortest | Alternative paths |
| 66 | `compute_betweenness_centrality` | record_id, output_format | Brandes' Algorithm | Bridge node detection |
| 67 | `find_dependency_clusters` | record_id, output_format | Tarjan's SCC | Strongly connected components |
| 68 | `find_cross_graph_path` | record_id_a, record_id_b, output_format | Merge + Dijkstra | Paths across two neighborhoods |
| 69 | `analyze_path_criticality` | record_id_1, record_id_2, output_format | Dijkstra + Brandes | Critical node identification |
| 70 | `analyze_graph_comprehensive` | record_id, additional_seed_ids, output_format | Combined | PageRank + communities + centrality in one call |
| 71 | `find_path_across_graphs` | record_ids, output_format | Multi-merge + Dijkstra | Paths across N neighborhoods |
| 72 | `explore_expanded_graph` | record_id, depth (1–3), max_seeds, output_format | Multi-hop BFS + merge | Progressive graph expansion |
| 73 | `build_topic_graph` | subject, registry, max_seeds, output_format | Search + merge + analyze | Topic-level aggregate graph |

### 5.10 Comparison Tools — `comparison.py` (1,858 lines, 12 tools)

| # | Tool | Parameters | Summary |
|---|------|-----------|---------|
| 74 | `compare_records` | record_id_1, record_id_2, output_format | Side-by-side: shared/unique subjects, domains, taxonomies, orgs |
| 75 | `compare_multiple_records` | record_ids (2–10), output_format | Multi-record comparison matrix |
| 76 | `compare_subject_landscapes` | subjects, include_deprecated, output_format | Registry distribution comparison across subjects |
| 77 | `analyze_deprecation_impact` | record_id, output_format | Downstream impact of deprecation |
| 78 | `check_policy_database_compliance` | policy_id, database_ids, output_format | Standards compliance per database |
| 79 | `compare_collections` | collection_ids, output_format | Collection content overlap (Jaccard similarity) |
| 80 | `find_compliant_standards` | policy_ids, database_ids, output_format | Multi-policy multi-database standards intersection |
| 81 | `assess_dmp_compliance` | policy_id, database_ids, output_format | Full DMP compliance workflow with gap analysis and recommendations |
| 82 | `analyze_transitive_impact` | record_id, max_depth (1–5), output_format | Multi-hop BFS impact analysis (capped at 100 API calls) |
| 83–85 | *(3 additional tools)* | | Cross-record analysis |

### 5.11 Discovery Tools — `discovery.py` (2,020 lines, 14 tools)

| # | Tool | Parameters | Summary |
|---|------|-----------|---------|
| 86 | `recommend_tools` | query, max_suggestions, output_format | Token-based tool recommendation with synonym expansion |
| 87 | `suggest_workflow` | intent, output_format | Multi-step workflow template matching (7 templates) |
| 88 | `find_orphan_records` | registry, orphan_type, subjects, countries, min_year, max_year, max_results, output_format | Find unconnected records |
| 89 | `search_publications` | query, output_format | Publication search |
| 90 | `get_registries` | output_format | Registry types and descriptions |
| 91 | `aggregate_by_field` | field, status, max_values, output_format | Record counts by dimension (registry, subject, domain, country) |
| 92 | `list_licences` | page, per_page, output_format | Available licences |
| 93 | `get_statistics` | detailed, output_format | Platform-level statistics |
| 94 | `suggest_related_resources` | record_id, output_format | Find similar records by shared metadata |
| 95 | `find_databases_by_standard` | standard_id, standard_name, max_results, output_format | Reverse lookup: databases implementing a standard |
| 96 | `suggest_graph_starting_points` | query, registry, subjects, max_candidates, output_format | Rank records by graph richness for analysis |
| 97 | `find_deprecated_resources` | query, registry, output_format | 3-tier progressive fallback for deprecated records |
| 98 | `check_api_health` | output_format | API key + connectivity + response time verification |
| 99 | `explain_fairsharing` | topic, output_format | Static reference docs (6 topics, no API call) |

**Static data structures in discovery.py:**
- `TOOL_CATALOG`: ~100 entries of `(tool_name, description)` for keyword matching
- `TOOL_SYNONYMS`: 12 synonym groups for query expansion
- `WORKFLOW_TEMPLATES`: 7 predefined multi-step workflows

### 5.12 Curator Tools — `curator.py` (321 lines, 2 tools)

| # | Tool | Parameters | Summary |
|---|------|-----------|---------|
| 100 | `audit_metadata_completeness` | record_id, output_format | Single-record metadata audit scorecard |
| 101 | `batch_audit_metadata` | query, registry, limit (2–100), min_year, max_year, output_format | Bulk metadata audit with failure tracking |

*Private helper:* `_audit_record_completeness(record)` — registry-specific completeness checklists.

---

## 6. Graph Analysis Engine

### 6.1 Architecture

All graph analysis operates on **local neighborhood graphs** — the graph visible from a single record's perspective (1 API call). This is a fundamental architectural constraint: the FAIRsharing API does not expose a global graph endpoint.

Tools include prominent scope caveats in their output:
> *"Scope: This analysis covers only the local neighborhood graph of the seed record. Metrics like PageRank and betweenness reflect local structure, not platform-wide importance."*

Multi-seed tools (`explore_expanded_graph`, `build_topic_graph`, `analyze_graph_comprehensive` with `additional_seed_ids`) mitigate this by fetching and merging multiple neighborhoods.

### 6.2 Algorithms

All CPU-bound algorithms are offloaded to thread pool via `asyncio.to_thread()` to avoid blocking the event loop.

#### Dijkstra — Weighted Shortest Path (`find_semantic_path`)

Uses `RELATIONSHIP_WEIGHTS` for edge costs (lower = stronger relationship):

| Relationship | Weight | Semantic Meaning |
|-------------|--------|-----------------|
| implements | 1.0 | Direct adoption (strongest) |
| recommends | 1.5 | Policy endorsement |
| extends | 1.8 | Technical extension |
| profiles | 2.0 | Profiling relationship |
| outputs | 2.0 | Output generation |
| collects | 2.5 | Data collection |
| shares_data_with | 2.5 | Data sharing |
| deprecates | 3.0 | Supersession |
| related_to | 4.0 | Generic association |
| other | 5.0 | Unclassified (weakest) |

#### PageRank — Influence Ranking (`compute_pagerank`)

Uses `RELATIONSHIP_INFLUENCE_WEIGHTS` for edge transfer (higher = more influence):

| Relationship | Weight |
|-------------|--------|
| implements | 1.0 |
| recommends | 0.8 |
| extends | 0.7 |
| profiles | 0.6 |
| outputs | 0.5 |
| shares_data_with | 0.5 |
| collects | 0.4 |
| related_to | 0.3 |
| deprecates | 0.2 |
| other | 0.2 |

Default parameters: `damping=0.85`, `iterations=20`.

#### Label Propagation — Community Detection (`detect_communities`)

Weighted label propagation with:
- Deterministic seeding (default `seed=42`) for reproducible results
- Modularity Q computation (Newman-Girvan) for community quality assessment
- Configurable `min_community_size` to filter noise

#### Bipartite Projection — Similarity (`find_similar_records`)

Projects the standard-database bipartite network onto one side to compute Jaccard similarity between records sharing common neighbors.

#### Yen's K-Shortest Paths (`find_multiple_paths`)

Finds the top K alternative paths between two nodes, each progressively longer/weaker than the previous.

#### Brandes' Algorithm — Betweenness Centrality (`compute_betweenness_centrality`)

Identifies bridge nodes — records that appear on many shortest paths and whose removal would fragment the network.

#### Tarjan's SCC — Dependency Clusters (`find_dependency_clusters`)

Iterative (non-recursive) implementation of Tarjan's strongly connected components algorithm. Identifies groups of records with mutual dependencies.

### 6.3 Multi-Seed Graph Operations

| Tool | Mechanism | Use Case |
|------|-----------|----------|
| `analyze_graph_comprehensive` | Merges seed + additional_seed_ids | Combined analysis of related records |
| `explore_expanded_graph` | Multi-hop BFS expansion (depth 1–3) | Progressive neighborhood discovery |
| `build_topic_graph` | Search by subject → fetch graphs → merge | Topic-level network structure |
| `find_cross_graph_path` | Merge 2 neighborhoods → pathfind | Find connections across records |
| `find_path_across_graphs` | Merge N neighborhoods → pathfind | Multi-record connectivity |

---

## 7. Quality Scoring System

### 7.1 Three-Tier Scoring Architecture

```
Tier 1: Basic Scoring (per-registry)
  _score_standard()     → 0–10 (identity, maintenance, adoption)
  _score_policy()       → 0–10 (mandates, coverage, recommendations)
  FAIR indicators       → 0–9  (9 database indicators)

Tier 2: Comprehensive Scoring (per-registry)
  _score_standard_comprehensive()   → 0–14 (5 weighted components)
  _score_policy_comprehensive()     → 0–12 (5 weighted components)
  _score_database_comprehensive()   → 0–14 (4 weighted components)

Tier 3: Unified Scoring (cross-registry)
  normalize_quality_score()  → 0–100 (any registry to common scale)
  Letter grades: A+(≥90), A(≥80), B(≥65), C(≥50), D(≥35), F(<35)
```

### 7.2 Database FAIR Indicators (9 fields)

| Indicator | API Field | Measures |
|-----------|-----------|----------|
| Data access condition | `dataAccessCondition` | How data can be accessed |
| Data curation | `dataCuration` | Manual/automated curation |
| Data deposition condition | `dataDepositionCondition` | How data is deposited |
| Citation to publications | `citationToRelatedPublications` | Publication linkage |
| Data contact information | `dataContactInformation` | Findability of contacts |
| Data versioning | `dataVersioning` | Version management |
| Data preservation policy | `dataPreservationPolicy` | Long-term preservation |
| Resource sustainability | `resourceSustainability` | Ongoing viability |
| Persistent identifier | `usesPersistentIdentifier` | DOI/handle usage |

### 7.3 Comprehensive Weights

**Standard Comprehensive (max 14.0):**

| Component | Weight | Measures |
|-----------|--------|----------|
| `adoption_breadth` | 4.0 | Implementers + recommenders |
| `identity_access` | 3.0 | Homepage, DOI, description |
| `maintenance` | 3.0 | Status, isMaintained |
| `temporal_health` | 2.0 | Recency of updates |
| `community_engagement` | 2.0 | Publications, subject breadth |

**Policy Comprehensive (max 12.0):**

| Component | Weight | Measures |
|-----------|--------|----------|
| `mandate_specificity` | 4.0 | Mandate fields defined |
| `recommendation_coverage` | 3.0 | Standards + databases recommended |
| `compliance_infrastructure` | 2.0 | Monitoring, guidance, timing |
| `geographic_coverage` | 1.5 | Countries covered |
| `temporal_health` | 1.5 | Recency of updates |

**Database Comprehensive (max 14.0):**

| Component | Weight | Measures |
|-----------|--------|----------|
| `fair_indicators` | 9.0 | All 9 FAIR indicator fields |
| `community_trust` | 2.0 | Policy recommendations + standard implementations |
| `temporal_health` | 1.5 | Update recency |
| `metadata_completeness` | 1.5 | Publications, description, DOI, licences |

### 7.4 Confidence Metadata

`compute_fair_score_detailed()` returns confidence levels:

| Confidence | Condition |
|------------|-----------|
| `high` | All 9 indicators present |
| `medium` | 5–8 indicators present |
| `low` | < 5 indicators present |

---

## 8. Output Format System

All 96 tools support dual output via the `output_format` parameter:

### 8.1 Markdown Output (default)

Human-readable formatted text with:
- Headers (`##`, `###`)
- Tables (`| Column | ... |`)
- Bold/italic emphasis
- Numbered lists for ranked results
- Pagination hints (`Page X of Y`)
- Scope caveats and methodology disclaimers

### 8.2 JSON Output (`output_format="json"`)

Machine-readable structured data via `json.dumps(data, indent=2)`. Each tool defines its own schema. Typical pattern:

```python
@app.mcp.tool()
async def some_tool(record_id: int, output_format: str = "markdown") -> str:
    # ... gather data ...

    if output_format == "json":
        return json.dumps({
            "record_id": record_id,
            "data": structured_data,
        }, indent=2)

    # ... format markdown ...
    return "\n".join(lines)
```

**Design decisions:**
- JSON branches placed **before** markdown assembly (early return)
- Error/validation messages always return plain text (not JSON)
- Serialization helpers: `_subject_to_dict()`, `_domain_to_dict()`, `_org_to_dict()`, etc.

---

## 9. Configuration Reference

### 9.1 Environment Variables

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `FAIRSHARING_API_KEY` | Yes | — | API key from https://fairsharing.org |
| `FAIRSHARING_API_URL` | No | `https://api.fairsharing.org/graphql/` | Custom API endpoint |
| `FAIRSHARING_MAX_PER_PAGE` | No | 50 | Results per API page (1–50) |
| `FAIRSHARING_MAX_SCAN` | No | 2000 | Max records scanned for date filtering (50–50,000) |
| `FAIRSHARING_TRUNCATION_WARNING` | No | true | Show truncation warnings |
| `FAIRSHARING_DISPLAY_MAX_ASSOCIATIONS` | No | 20 | Max associations shown |
| `FAIRSHARING_DISPLAY_MAX_ORGANISATIONS` | No | 10 | Max organisations shown |
| `FAIRSHARING_DISPLAY_MAX_PUBLICATIONS` | No | 10 | Max publications shown |
| `FAIRSHARING_DISPLAY_MAX_TAXONOMIES` | No | 20 | Max taxonomies shown |
| `FAIRSHARING_DISPLAY_MAX_SUBJECTS` | No | 5 | Max subjects in summaries |
| `FAIRSHARING_DISPLAY_MAX_DOMAINS` | No | 5 | Max domains in summaries |
| `FAIRSHARING_DISPLAY_MAX_CHILDREN` | No | 30 | Max hierarchy children |
| `FAIRSHARING_DISPLAY_MAX_RECOMMENDED` | No | 30 | Max recommended items |
| `FAIRSHARING_DISPLAY_MAX_DESCRIPTION_CHARS` | No | 300 | Description character limit |

### 9.2 Client Defaults

| Setting | Default | Configurable Via |
|---------|---------|-----------------|
| Rate limit | 5.0 RPS | `FAIRsharingClient(rate_limit_rps=...)` |
| Burst size | 3 | `FAIRsharingClient(rate_limit_burst=...)` |
| Cache TTL | 300s | `FAIRsharingClient(cache_ttl=...)` |
| Cache max entries | 500 | `FAIRsharingClient(cache_maxsize=...)` |
| Request timeout | 30s | `FAIRsharingClient(timeout=...)` |
| Max retries | 3 | `FAIRsharingClient(max_retries=...)` |

**Per-call overrides** (passed to `client.query()`):

| Parameter | Type | Purpose |
|-----------|------|---------|
| `timeout` | `float \| None` | Override timeout for this call only |
| `max_retries` | `int \| None` | Override retry count for this call only |

`advancedSearch` calls use `timeout=90, max_retries=1` to fit within the MCP client's 120-second hard limit while allowing enough time for large result sets.

---

## 10. Test Infrastructure

### 10.1 Overview

| Metric | Value |
|--------|-------|
| Test file | `tests/test_server.py` |
| Shared fixtures | `tests/conftest.py` |
| Test class | `TestFAIRsharingServer(unittest.IsolatedAsyncioTestCase)` |
| Total tests | 282 |
| Test runner | `python -m pytest tests/test_server.py` |
| CI/CD | GitHub Actions → Azure Web App |

### 10.2 Universal Mock Pattern

Every test patches the single dependency injection point:

```python
@patch("fairsharing_mcp.app.get_client")
async def test_example(self, mock_get_client):
    mock_client = AsyncMock()
    mock_get_client.return_value = mock_client

    # Configure response(s)
    mock_client.query.return_value = {"searchFairsharingRecords": {...}}
    # Or for multi-call workflows:
    mock_client.query.side_effect = [response1, response2, response3]

    # Call tool
    result = await some_tool(param1)

    # Assert
    self.assertIn("expected text", result)
```

### 10.3 Shared Fixtures (`conftest.py`)

| Factory | Purpose | Key Fields |
|---------|---------|------------|
| `make_record(**overrides)` | Base record factory | id, name, abbreviation, registry, type, status, doi, subjects, domains, associations |
| `make_standard(**overrides)` | Standard record | registry="Standard", type="model/format" |
| `make_policy(**overrides)` | Policy record | 15 mandate fields pre-populated |
| `make_search_result(records, total, pages)` | Search response envelope | `searchFairsharingRecords: {records, totalCount, totalPages}` |
| `make_advanced_search_result(records)` | advancedSearch response envelope | `advancedSearch: [...]` flat list |

### 10.4 Graph Test Helpers

```python
@staticmethod
def _make_graph(nodes_spec, edges_spec, name="TestGraph"):
    """Build graph data from compact specs.
    nodes_spec: [(key, label, registry, record_type), ...]
    edges_spec: [(source, target, color), ...]
    """

def _graph_mock(self, graph_data):
    """Pre-configured mock returning {"fairsharingGraph": {"data": graph_data}}"""
```

### 10.5 Assertion Strategy

Tests validate **semantic content** via substring matching rather than exact output formatting:

```python
self.assertIn("Page 1 of 3", result)   # Pagination
self.assertIn("GenBank", result)        # Record presence
self.assertIn("implements", result)     # Relationship decoded
self.assertIn("60.0%", result)          # Score computation
```

This approach tolerates layout changes while catching logic regressions.

### 10.6 Running Tests

```bash
# Full suite
python -m pytest tests/test_server.py

# Single test
python -m pytest tests/test_server.py::TestFAIRsharingServer::test_search_records -v

# By keyword
python -m pytest tests/test_server.py -k "pagerank" -v

# Lint + format
ruff check src/ tests/
ruff format src/ tests/
```

---

## 11. Deployment

### 11.1 Local Development

```bash
# Install
uv sync

# Run the MCP server (stdio transport — typically launched by MCP client)
uv run fairsharing-mcp

# Or via Python entry point
python -m fairsharing_mcp.server
```

### 11.1a Streamlit Chat UI

A browser-based conversational interface ships in the `clients/` package.

**Prerequisites:**

```bash
# Create .env in project root
echo "FAIRSHARING_API_KEY=your-key" > .env
# If using the OpenAI Agents provider:
# echo "OPENAI_API_KEY=your-key" >> .env
```

**Start:**

```bash
streamlit run clients/app.py
```

Opens at `http://localhost:8501`.

**Client package structure:**

```
clients/
├── app.py                  # Streamlit entry point
├── base.py                 # AbstractProvider base class
├── config.py               # Provider configuration loader (.env / env vars)
├── history.py              # ConversationHistory (message list wrapper)
├── conversation_logger.py  # Logs turns to logs/conversations/
├── repl.py                 # Terminal REPL alternative to Streamlit
├── requirements.txt        # Client-only dependencies (streamlit, etc.)
└── providers/
    ├── __init__.py         # Provider registry / factory
    └── openai_agents.py    # OpenAI Agents SDK provider with FAIRsharing SYSTEM_PROMPT
```

**Provider:** `openai_agents.py` connects to the local MCP server via the OpenAI Agents SDK (`openai-agents`). The `SYSTEM_PROMPT` instructs the model to:
- Always include FAIRsharing URLs as markdown hyperlinks
- Cite the specific tool used for each fact
- Self-verify output before responding

**Environment variables for client:**

| Variable | Default | Description |
|----------|---------|-------------|
| `LLM_PROVIDER` | `openai_agents` | Provider to use |
| `OPENAI_API_KEY` | — | OpenAI API key (required for openai_agents) |
| `OPENAI_MODEL` | `gpt-4o` | Model name |
| `FAIRSHARING_MCP_COMMAND` | `uv` | Command to launch MCP server |
| `FAIRSHARING_API_KEY` | — | Passed to the MCP subprocess |
| `CONVERSATION_LOG_DIR` | `logs/conversations/` | Where to write conversation logs |

### 11.2 MCP Client Configuration

Add to your MCP client's configuration (e.g., Claude Desktop `claude_desktop_config.json`):

```json
{
  "mcpServers": {
    "fairsharing": {
      "command": "uv",
      "args": ["run", "fairsharing-mcp"],
      "env": {
        "FAIRSHARING_API_KEY": "your-api-key-here"
      }
    }
  }
}
```

### 11.3 CI/CD Pipeline

**GitHub Actions** (`.github/workflows/main_fairsharing.yml`):

1. **Build job**: Checkout → Python 3.14 → venv + install → upload artifact
2. **Deploy job**: Download artifact → deploy to Azure Web App (production slot)

Triggers: Push to `main` or manual `workflow_dispatch`.

### 11.4 Project Structure

```
fairsharing-mcp/
├── pyproject.toml                    # Build config, dependencies, linting rules
├── CLAUDE.md                         # Developer guidance for AI assistants
├── .env.example                      # Environment variable template
├── src/
│   └── fairsharing_mcp/
│       ├── __init__.py               # Package init
│       ├── server.py                 # Entry point (18 lines)
│       ├── app.py                    # FastMCP instance + client singleton (61 lines)
│       ├── client.py                 # GraphQL client with rate limiting, cache, retry (393 lines)
│       ├── config.py                 # Environment-based configuration (92 lines)
│       ├── validation.py             # Input validation utilities (68 lines)
│       ├── queries.py                # 27 GraphQL query constants (425 lines)
│       ├── constants.py              # Weights, thresholds, validation sets (135 lines)
│       ├── formatters.py             # Output formatting + scoring (832 lines)
│       ├── helpers.py                # Fallback fetchers, mandate extraction (163 lines)
│       ├── graph_utils.py            # ParsedGraph, merge, thread offloading (215 lines)
│       └── tools/
│           ├── __init__.py           # Imports all 11 modules (16 lines)
│           ├── search.py             # 6 tools — Search, DOI lookup (964 lines)
│           ├── records.py            # 7 tools — Record detail, batch fetch, resolve (793 lines)
│           ├── taxonomy.py           # 11 tools — Subjects, domains, species (811 lines)
│           ├── organisations.py      # 6 tools — Orgs, countries, regions (683 lines)
│           ├── standards.py          # 10 tools — Maturity, adoption, emerging (1,788 lines)
│           ├── quality.py            # 8 tools — FAIR indicators, unified scoring (1,148 lines)
│           ├── policies.py           # 8 tools — Mandates, compliance, conflicts (1,628 lines)
│           ├── graph.py              # 7 tools — Ecosystem, connections, chains (1,026 lines)
│           ├── graph_analysis.py     # 13 tools — PageRank, communities, paths (2,746 lines)
│           ├── comparison.py         # 12 tools — DMP compliance, impact (1,858 lines)
│           ├── discovery.py          # 14 tools — Workflows, health, orphans (2,020 lines)
│           └── curator.py            # 2 tools — Metadata auditing (321 lines)
├── tests/
│   ├── conftest.py                   # Shared fixtures
│   └── test_server.py               # 282 tests
├── clients/                          # Optional chat UI + client library
│   ├── app.py                        # Streamlit chat interface
│   ├── base.py                       # AbstractProvider
│   ├── config.py                     # Client configuration
│   ├── history.py                    # Conversation history
│   ├── conversation_logger.py        # Turn logger
│   ├── repl.py                       # Terminal REPL
│   ├── requirements.txt              # Client dependencies
│   └── providers/
│       ├── __init__.py
│       └── openai_agents.py          # OpenAI Agents SDK provider
└── Documentation/
    ├── images/
    └── TECHNICAL_DOCUMENTATION.md    # This document
```

---

## 12. Design Decisions & Trade-offs

### 12.1 Architectural Decisions

| Decision | Rationale | Trade-off |
|----------|-----------|-----------|
| **Single-file test suite** | Simple mocking pattern, single patch target | 7,670 lines in one file; harder to navigate |
| **No networkx dependency** | Zero external deps for graph algorithms; full control over weights | More code to maintain; limited to implemented algorithms |
| **Client-side date filtering** | API lacks date filter support | Requires scanning up to 50,000 records; higher API usage |
| **Flat list from multiTagFilter** | API design (not our choice) | Must handle differently from paginated search responses |
| **Token bucket rate limiter** | Better burst behavior than fixed delay | Slightly more complex than simple `asyncio.sleep()` |
| **Opportunistic date indexing** | Avoids redundant API calls for date lookups | Memory grows with usage; no TTL on index entries |
| **Pure-function scorers** | Testable without mocking; composable | Must be kept in sync with API field changes |
| **`_compute_quality_for_record` in quality.py** | Avoids tools→helpers→tools circular import | Less intuitive location than helpers.py |

### 12.2 Scoring Methodology Caveats

All scoring weights are **heuristic, not empirically calibrated**. Output includes methodology disclaimers:

- Comprehensive weights are tuned based on FAIRsharing relationship semantics
- Changing weight ratios can significantly alter rankings
- Unified 0–100 normalization assumes linear scaling across registries
- Confidence levels reflect data completeness, not score accuracy

### 12.3 Graph Analysis Limitations

1. **Local scope**: Every graph tool operates on a single record's neighborhood (1–2 API calls). PageRank and betweenness centrality reflect local structure only.
2. **Edge colors as relationships**: The API encodes relationships via CSS colors in graph data. Color-to-relationship mapping is maintained in `EDGE_COLOR_TO_RELATIONSHIP`.
3. **No global graph**: There is no API endpoint for the full FAIRsharing graph. Multi-seed tools (`explore_expanded_graph`, `build_topic_graph`) partially mitigate this by merging local neighborhoods.
4. **CPU-bound algorithms**: All graph algorithms run in `asyncio.to_thread()` to avoid blocking the event loop, but they still consume CPU time proportional to graph size.

### 12.4 API Resilience

| Strategy | Implementation |
|----------|---------------|
| **Fallback queries** | Policy and database quality tools try detailed queries first, fall back to basic query |
| **Auth error separation** | `FAIRsharingAuthError` is never swallowed — surfaces config issues immediately |
| **Progressive filter relaxation** | `search_records` with `fallback_on_empty=True` progressively drops filters |
| **Failure tracking** | `batch_audit_metadata` reports "X of Y failed" when individual fetches fail |
| **Health check** | `check_api_health` verifies connectivity, auth, and response time |

---

*Generated from codebase analysis of fairsharing-mcp v0.1.0 — 96 tools, 282 tests, 27 GraphQL query constants.*
