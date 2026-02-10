# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What This Is

An MCP (Model Context Protocol) server exposing the FAIRsharing GraphQL API as 95 tools across 11 domain modules. FAIRsharing catalogues data standards, databases, and policies for life sciences research. The server uses FastMCP with stdio transport.

## Commands

```bash
# Run the MCP server
uv run fairsharing-mcp

# Run all tests (256 tests, single file)
python -m pytest tests/test_server.py

# Run a specific test
python -m pytest tests/test_server.py::TestFAIRsharingServer::test_search_records -v

# Run tests matching a keyword
python -m pytest tests/test_server.py -k "pagerank" -v

# Lint
ruff check src/ tests/

# Format
ruff format src/ tests/

# Install dependencies
uv sync
```

## Architecture

### Dependency Flow

```
server.py → tools/__init__.py → tools/*.py → app.py → client.py → httpx → FAIRsharing GraphQL API
```

- **`server.py`** — Thin entry point (~18 lines). Imports `tools` to trigger registration, then calls `mcp.run()`.
- **`app.py`** — Shared state: the `FastMCP` instance (`mcp`) and `get_client()` singleton factory. Has NO tool imports (critical for avoiding circular imports).
- **`client.py`** — Async GraphQL client with token bucket rate limiting (5 RPS, burst 3), LRU response cache (500 entries, 5-minute TTL), retry logic (3x exponential backoff), persistent `httpx.AsyncClient` connection pooling, and opportunistic date indexing from all responses.
- **`tools/__init__.py`** — Imports all 11 tool modules, which triggers `@mcp.tool()` decorator registration.

### Key Design Patterns

**Circular import avoidance:** `app.py` must never import from `tools/`. All tool modules import `app.get_client()` at call time. The `_compute_quality_for_record()` helper lives in `tools/quality.py` (not `helpers.py`) specifically to avoid a tools→helpers→tools cycle.

**Single mock target for tests:** All 256 tests patch `fairsharing_mcp.app.get_client` to inject a mock client. Tests use `unittest.IsolatedAsyncioTestCase`. Shared fixtures live in `tests/conftest.py`.

**Fallback queries:** When a detailed query fails (e.g., policy mandate fields unavailable), helpers fall back to the basic `GET_RECORD_QUERY`. See `helpers.fetch_policy_with_fallback()` and `helpers.fetch_database_quality_with_fallback()`. Auth errors (`FAIRsharingAuthError`) are re-raised, not swallowed.

**STDIO protocol constraint:** Never use `print()` — all logging goes to stderr. The MCP protocol uses stdout for JSON-RPC communication.

### Tool Modules (in `src/fairsharing_mcp/tools/`)

| Module | Tools | Domain |
|---|---|---|
| `search.py` | 6 | Record search, DOI lookup, counting, license filtering |
| `records.py` | 6 | Record detail, date filtering, batch fetch, reverse lookup |
| `taxonomy.py` | 11 | Subjects, domains, taxonomies |
| `organisations.py` | 6 | Organisations, countries, regions |
| `standards.py` | 7 | Maturity index, adoption, emerging/endorsed standards |
| `quality.py` | 7 | FAIR indicators, unified/comprehensive quality scoring |
| `policies.py` | 7 | Policy mandates, compliance, conflict detection |
| `graph.py` | 7 | Record graph/connections |
| `graph_analysis.py` | 13 | PageRank, communities, paths, centrality, expanded/topic graphs (pure Python, no networkx) |
| `comparison.py` | 9 | DMP compliance, transitive impact, cross-record comparison |
| `discovery.py` | 14 | Workflows, tool recommendations, orphan/deprecated record finding |
| `curator.py` | 2 | Batch metadata auditing |

### Supporting Modules

- **`queries.py`** — 26 GraphQL query string constants.
- **`constants.py`** — Validation sets (registries, record types), relationship/influence weights, edge colors, FAIR indicator fields, grade thresholds.
- **`formatters.py`** — Output formatting functions, `compute_fair_score()`, `compute_fair_score_detailed()`, `normalize_quality_score()`.
- **`helpers.py`** — Fallback fetchers, `extract_policy_mandates()`, `matches_date_range()`.
- **`config.py`** — Environment-based configuration (display limits, scan caps, truncation warnings).
- **`graph_utils.py`** — `ParsedGraph` dataclass, `parse_graph()`, `fetch_and_parse_graph()`, `merge_graphs()`.
- **`validation.py`** — Input validation utilities.

### API Quirks

- `multiTagFilter` returns a **flat list**, not `{records, totalCount, totalPages}` like `searchFairsharingRecords`.
- The API has no date filtering; date ranges are implemented via client-side scanning and filtering.
- All 95 tools support `output_format="json"` for structured output alongside the default markdown format.
- Graph analysis tools operate on a **single record's local neighborhood**, not the full platform graph. Use `explore_expanded_graph` or `build_topic_graph` for multi-seed merged analysis.
- CPU-bound graph algorithms are offloaded to `asyncio.to_thread()` to avoid blocking the event loop.

## Environment

Required: `FAIRSHARING_API_KEY` (from https://fairsharing.org after registration).

Optional: See `.env.example` for `FAIRSHARING_API_URL`, scan limits, display limits, and truncation warning config.
