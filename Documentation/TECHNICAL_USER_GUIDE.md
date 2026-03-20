# FAIRsharing MCP Server — Developer Guide

This guide is for developers who are about to write code against this codebase: adding a tool, fixing a bug, writing a test, debugging unexpected behaviour, or trying to understand why a quality score came out the way it did. It is written in the order you need the information — conceptual model first, practical patterns second, reference last.

This guide does not duplicate the complete tool parameter inventory, GraphQL query constants, or configuration variable listings. Those live in `TECHNICAL_DOCUMENTATION.md`. Read that document when you want to look something up. Read this one when you want to understand how the system works.

---

## 1. What This Server Is and How It Fits Together

FAIRsharing is a curated registry of three types of resources: data standards (formats, schemas, ontologies, reporting guidelines), databases (repositories, knowledgebases, biobanks), and policies (funder mandates, journal policies). Everything in FAIRsharing is a "record" with a numeric ID and a DOI-derived URL. These two identifiers are not interchangeable — the mapping between them is non-deterministic, which matters every time you construct a URL. More on this in Section 7.

This server is a FastMCP STDIO server. MCP stands for Model Context Protocol — a protocol that lets LLM clients (Claude Desktop, VS Code Copilot, the included Streamlit app) call structured tools and receive string responses. The transport is stdio: the LLM client launches this process as a subprocess and speaks JSON-RPC over stdin/stdout. That last point has a sharp consequence explained in Section 2.

The server exposes 96 tools. Every tool is an async Python function decorated with `@app.mcp.tool()`. The decoration happens at import time — when Python imports `fairsharing_mcp.tools`, all 96 decorators run and register the tools with FastMCP. After that, `server.py` calls `mcp.run()` and the server waits for requests. The entry point is deliberately thin:

```python
# server.py — the entire file
import fairsharing_mcp.tools  # noqa: F401 — triggers all @mcp.tool() registrations
from fairsharing_mcp.app import mcp

def main():
    transport = os.getenv("MCP_TRANSPORT", "stdio")
    mcp.run(transport=transport)
```

At runtime, a tool call travels through these layers:

```
[LLM Client]
    │  JSON-RPC over stdio (or HTTP if MCP_TRANSPORT=streamable-http)
    ▼
[server.py] — mcp.run() receives the call, dispatches to the right function
    ▼
[tools/search.py (or records.py, etc.)] — the tool function executes
    ▼
[app.get_client()] — returns the FAIRsharingClient singleton
    ▼
[client.py] — rate limiting, cache check, HTTP POST, retry, date index
    ▼
[api.fairsharing.org/graphql/] — GraphQL API
```

The response travels back up the same chain. The tool function returns a string — either markdown (the default) or JSON. That string is wrapped in a JSON-RPC response and written to stdout.

The server holds almost no state between calls. The two persistent objects are the `FAIRsharingClient` singleton (created on the first tool call, reused for all subsequent calls) and its LRU response cache (500 entries, 5-minute TTL). Everything else is created fresh per call. This makes the server straightforward to reason about: if a tool misbehaves, the cause is almost always in the tool function itself or in the API response — not in accumulated server state.

---

## 2. Two Constraints You Must Never Violate

These constraints are not style preferences. Violating either produces bugs that are hard to trace because the failure mode is silent or occurs at a layer far from where the bad code lives.

### 2.1 Never Use `print()`

The MCP protocol uses stdout as its transport channel. When the LLM client reads from the server's stdout, it expects a stream of JSON-RPC messages. If your code writes plain text to stdout — via `print()` — the client's JSON parser receives garbage. The connection either breaks silently or the client reports a protocol error with no indication of what caused it.

`app.py` configures logging to stderr from the very first lines:

```python
# app.py — module-level, runs at import time
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    handlers=[logging.StreamHandler()],  # StreamHandler() defaults to sys.stderr
)
```

Every module in this codebase declares a module-level logger:

```python
logger = logging.getLogger(__name__)
```

Use it. Never use `print()`.

```python
# WRONG — corrupts the MCP channel and breaks the client connection
print(f"Searching for {query}...")

# CORRECT — goes to stderr, invisible to the JSON-RPC parser
logger.info("Searching for %s", query)
logger.warning("advancedSearch failed, falling back to paginated search")
logger.debug("Query variables: %s", variables)
```

If you suspect `print()` is in the codebase, `grep -r 'print(' src/fairsharing_mcp/` should return nothing meaningful.

### 2.2 Never Import From `tools/` Inside `app.py`

`app.py` creates the `FastMCP` instance (`mcp`) that all tools decorate against. If `app.py` imported a tool module, and that tool module imports `from fairsharing_mcp.app import mcp` (as all tools do), Python would be asked to complete the import of `app.py` before it has finished initializing. The result is that the second importer receives a partially-initialized module object and raises `AttributeError: module 'fairsharing_mcp.app' has no attribute 'mcp'` — at startup, not at the call site.

The solution is that `app.py` exports exactly two things: `mcp` (the FastMCP instance) and `get_client()` (the client factory). It imports nothing from `tools/`. Tools import `app` at module level — this works because `app.py` doesn't import them back. The client is obtained at call time:

```python
# tools/my_module.py — the correct pattern

from fairsharing_mcp import app          # fine: app.py doesn't import tools
from fairsharing_mcp.client import FAIRsharingError
from fairsharing_mcp.queries import MY_QUERY

@app.mcp.tool(name="fairsharing_my_tool", ...)
async def my_tool(record_id: int) -> str:
    client = app.get_client()            # called at runtime, not at import time
    data = await client.query(MY_QUERY, {"id": record_id})
    # ...
```

```python
# app.py — NEVER add this
from fairsharing_mcp.tools import search   # circular import — do not do this
```

This constraint has one non-obvious consequence: the `_compute_quality_for_record()` helper lives in `tools/quality.py` rather than `helpers.py`. If it were in `helpers.py`, and another tool module imported helpers, and helpers imported from quality to use that helper, you would have a `tools → helpers → tools` cycle. The location is deliberate, not an oversight.

The broader lesson: any helper function that is called by multiple tool modules must either live in a support module that doesn't import from tools (like `helpers.py`, `formatters.py`, or `graph_utils.py`), or in the specific tool module that owns it.

---

## 3. How a Tool Call Actually Executes

Tracing one complete call through the stack is the fastest way to understand where things can go wrong.

Suppose an LLM calls `fairsharing_search_records(query="HDF5", registry=["Standard"])`. FastMCP deserializes the JSON-RPC payload and calls the Python function `search_records(query="HDF5", registry=["Standard"])`. The function's first line is:

```python
client = app.get_client()
```

`get_client()` checks whether a `FAIRsharingClient` singleton exists. If this is the first call since the process started, it reads `FAIRSHARING_API_KEY` from the environment and creates the client. If the env var is missing, it raises `FAIRsharingError` immediately — there is no retry or fallback for a missing API key. On all subsequent calls, the existing singleton is returned directly.

The function then calls `await client.query(SEARCH_RECORDS_QUERY, variables)`. Inside `client.query()`:

1. **Rate limit check.** `await self._rate_limiter.acquire()` blocks until the token bucket allows another request. The default is 5 requests per second with a burst of 3 — meaning after an idle period, up to 3 requests fire immediately, then the server throttles to 5 RPS. The bucket refills continuously, so brief pauses let the burst capacity rebuild.

2. **Cache check.** The cache key is an MD5 hash of the query string plus JSON-serialized variables. Most tool calls pass `cache=False` (the default), so this is usually a miss. Reference data tools — `list_subjects`, `get_record_types`, `get_registries` — pass `cache=True`.

3. **HTTP POST.** A persistent `httpx.AsyncClient` sends the request to `https://api.fairsharing.org/graphql/`. The `X-GraphQL-Key` auth header is set once at client construction, not per-request. The default timeout is 30 seconds.

4. **Retry.** On HTTP 5xx or a network timeout: exponential backoff (2^attempt × random jitter in [0.8, 1.2]), up to 3 attempts. On HTTP 429 (rate limit): respects the `Retry-After` header if present, otherwise same backoff. On HTTP 401 (bad API key): raises `FAIRsharingAuthError` immediately with no retry — a configuration problem should fail loudly, not quietly exhaust the retry budget.

5. **Date index.** `_index_dates_from_response(data)` opportunistically populates an in-memory dict of `{record_id: {createdAt, updatedAt}}` from every API response. Tools that filter by date (like `filter_records_by_date`) use this index to skip records already known to be out of range without re-fetching them.

6. **Return.** The `data` dict (the `data` key of the GraphQL response) is returned to the tool function.

The tool function then formats the response and returns a string. That string goes to FastMCP, which wraps it in a JSON-RPC response and writes it to stdout.

### The Fallback Pattern

Several tools try a detailed query first, then fall back to a basic query on transient error. The canonical implementation is in `helpers.py`:

```python
async def fetch_policy_with_fallback(record_id: int) -> dict | None:
    client = app.get_client()
    try:
        data = await client.query(GET_POLICY_DETAIL_QUERY, {"id": record_id})
        record = data.get("fairsharingRecord")
        if record:
            extract_policy_mandates(record)  # mutates in place
            return record
    except FAIRsharingAuthError:
        raise  # never fall back on a bad API key — surface the config problem
    except FAIRsharingError:
        logger.warning("Policy detail query failed for record %s, trying basic query", record_id)

    # Fallback: basic record query — no metadata blob, no mandate data
    try:
        data = await client.query(GET_RECORD_QUERY, {"id": record_id})
        record = data.get("fairsharingRecord")
        if record:
            record["_mandate_data_unavailable"] = True
            return record
    except FAIRsharingAuthError:
        raise  # still never swallow auth errors
    except FAIRsharingError:
        logger.warning("Basic record query also failed for record %s", record_id)

    return None
```

The pattern has two invariants: `FAIRsharingAuthError` is always re-raised (never swallowed), and transient errors (timeout, rate limit, server 5xx) trigger the fallback. If you write new fallback logic, model it on this.

---

## 4. Adding a New Tool

Adding a tool is straightforward once you understand the patterns. There are seven steps.

### Step 1: Choose the right module

The 11 tool modules are organized by domain:

- `search.py` — text search, DOI lookup, filtered counts, advanced filtering
- `records.py` — individual record fetch, batch fetch, date filtering, reverse lookup, identifier resolution
- `taxonomy.py` — subjects, domains, species taxonomies, hierarchy browsing
- `organisations.py` — organisations, countries, regional distribution
- `standards.py` — standard quality, adoption analysis, maturity index, emerging standards
- `quality.py` — FAIR indicator scoring, unified quality comparison, comprehensive profiles
- `policies.py` — policy details, mandate analysis, country comparison, conflict detection
- `graph.py` — ecosystem analysis, relationship chains, hub detection, collection contents
- `graph_analysis.py` — PageRank, community detection, path finding, betweenness centrality
- `comparison.py` — multi-record comparison, DMP compliance, transitive impact
- `discovery.py` — tool recommendations, workflow templates, orphan detection, health check

If the new tool doesn't fit cleanly into any of these, create a new module file (`tools/mymodule.py`) and add an import line to `tools/__init__.py`. No other registration step exists.

### Step 2: Write the GraphQL query

Add a constant to `queries.py`. Keep it focused — only request fields the tool will actually use. Before you write the query, confirm which of the four response shapes you're using, because they are not interchangeable:

```python
# searchFairsharingRecords — paginated envelope
records = data["searchFairsharingRecords"]["records"]
total   = data["searchFairsharingRecords"]["totalCount"]

# multiTagFilter — flat list, no envelope
records = data["multiTagFilter"]

# advancedSearch — flat list, no envelope
records = data["advancedSearch"]

# fairsharingRecord — single dict
record = data["fairsharingRecord"]
```

Mixing these up produces silent empty results (you get an empty list where you expected a record) or `None`-dereference errors. This has caused multiple bugs; the details are in Section 7.

### Step 3: Write the tool function

Here is the complete skeleton. Every element is required.

```python
import json
import logging
from typing import Annotated

from mcp.types import ToolAnnotations
from pydantic import Field

from fairsharing_mcp import app
from fairsharing_mcp.client import FAIRsharingError
from fairsharing_mcp.queries import MY_QUERY

logger = logging.getLogger(__name__)


@app.mcp.tool(
    name="fairsharing_my_tool",          # must be fairsharing_ prefixed
    annotations=ToolAnnotations(
        readOnlyHint=True,               # this server never writes to FAIRsharing
        idempotentHint=True,
        openWorldHint=True,              # False only for static tools with no API call
    ),
)
async def my_tool(
    record_id: Annotated[
        int,
        Field(ge=1, description="FAIRsharing record ID"),
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
    """One-line summary suitable for display in the tool list.

    Longer explanation of what the tool does and when to use it.

    Args:
        record_id: The FAIRsharing numeric record ID.
        output_format: "markdown" (default) or "json".

    Returns:
        Formatted record information.
    """
    client = app.get_client()            # runtime, not import time
    try:
        data = await client.query(MY_QUERY, {"id": record_id})
        record = data.get("fairsharingRecord")
        if not record:
            return f"No record found with ID {record_id}."

        if output_format == "json":
            return json.dumps({"record_id": record_id, "data": record}, indent=2)

        # Build markdown output
        lines = [f"## {record.get('name', 'Unknown')}"]
        # ... format fields ...
        return "\n".join(lines)

    except FAIRsharingError as e:
        return f"Error retrieving record {record_id}: {e}"
```

### Step 4: Register the tool

Add the import to `tools/__init__.py`:

```python
from fairsharing_mcp.tools import (
    # ... existing imports ...
    mymodule,
)
```

That is the only registration step. The decorator does the rest.

### Step 5: Use the logger, never `print()`

Declare at module level, use everywhere:

```python
logger = logging.getLogger(__name__)
```

### Step 6: Put defaults in both places

FastMCP reads the JSON schema default from `Field(default=X)`. Python needs the `= X` in the function signature to make the parameter optional at the call site. If you omit either one, you get a mismatch:

```python
# Correct — both Field(default=...) and = in the signature
per_page: Annotated[int, Field(default=20, ge=1, le=50)] = 20

# Missing the = 20 in the signature — Python treats it as required
per_page: Annotated[int, Field(default=20, ge=1, le=50)]

# Missing Field(default=...) — JSON schema doesn't advertise a default
per_page: Annotated[int, Field(ge=1, le=50)] = 20
```

### Step 7: Write the test

See Section 6 for the full testing patterns. The minimum viable test for a new tool:

```python
from unittest.mock import AsyncMock, patch
from tests.conftest import make_record

@patch("fairsharing_mcp.app.get_client")
async def test_my_tool_returns_name(self, mock_get_client):
    mock_client = AsyncMock()
    mock_get_client.return_value = mock_client
    mock_client.query.return_value = {
        "fairsharingRecord": make_record(id="42", name="HDF5")
    }
    result = await my_tool(record_id=42)
    self.assertIn("HDF5", result)
```

---

## 5. The advancedSearch Timeout Problem

Three tools — `count_fair_records`, `advanced_filter_records`, and `assess_database_indicators` — use the `advancedSearch` GraphQL endpoint for server-side FAIR indicator filtering. This endpoint is powerful but slow: filtering across 50+ criteria on thousands of records can take tens of seconds.

The problem is arithmetic. MCP clients have a hard deadline of roughly 120 seconds per tool call. The default client configuration is `timeout=30s, max_retries=3`. In the worst case, three failed retry attempts consume 30 + 30 + 30 = 90 seconds. The fallback paginated query then has only 30 seconds remaining — often not enough to scan thousands of records.

The fix is a per-call override on `client.query()`:

```python
try:
    data = await client.query(
        ADVANCED_SEARCH_QUERY,
        variables,
        timeout=90,       # give advancedSearch most of the 120s budget
        max_retries=1,    # single attempt — no retries consuming the remaining time
    )
    records = data.get("advancedSearch", [])
except FAIRsharingAuthError:
    raise
except FAIRsharingError:
    logger.warning("advancedSearch failed, falling back to paginated search")
    # Fallback now has ~30s with default timeout/retries
    data = await client.query(SEARCH_RECORDS_QUERY, fallback_variables)
    records = data.get("searchFairsharingRecords", {}).get("records", [])
```

Why `max_retries=1` specifically: with `max_retries=3`, a timed-out advancedSearch would waste all 90 seconds on retries (30 × 3), leaving the fallback zero time. With `max_retries=1`, only one 90-second attempt is made. If the API is slow enough to time out, retrying won't help anyway.

If you add a new tool that uses `advancedSearch` with a fallback, use `timeout=90, max_retries=1`. The `client.query()` method accepts these as keyword overrides that apply only to that single call, leaving the global defaults unchanged.

---

## 6. Testing: Patterns and Anti-Patterns

The test suite has 282 tests, all in one file (`tests/test_server.py`), all using the same mock structure. Once you understand that structure, you can write a correct test in a few minutes without reading the full file.

### 6.1 The Single Mock Target

Every tool calls `app.get_client()` at runtime. By patching `fairsharing_mcp.app.get_client`, you intercept all tool calls through a single point. This works for all 96 tools without any per-module patching.

```python
import unittest
from unittest.mock import AsyncMock, patch

class TestMyTool(unittest.IsolatedAsyncioTestCase):
    @patch("fairsharing_mcp.app.get_client")
    async def test_my_tool(self, mock_get_client):
        mock_client = AsyncMock()
        mock_get_client.return_value = mock_client
        mock_client.query.return_value = {
            "fairsharingRecord": {"id": "1", "name": "FASTQ", "registry": "Standard"}
        }
        result = await my_tool(record_id=1)
        self.assertIn("FASTQ", result)
```

`IsolatedAsyncioTestCase` provides a fresh event loop per test method, avoiding shared asyncio state between tests.

There is also a pytest fixture in `conftest.py` if you prefer the pytest style:

```python
# conftest.py
@pytest.fixture
def patch_get_client(mock_client):
    with patch("fairsharing_mcp.app.get_client", return_value=mock_client):
        yield mock_client
```

### 6.2 The Fixture Factories

`tests/conftest.py` provides factory functions that produce properly shaped record dicts. Use them rather than writing raw dicts — the factories match the real API structure, including all the fields that formatters expect.

```python
from tests.conftest import make_record, make_standard, make_policy, make_search_result, make_advanced_search_result

# Single record
record = make_record(id="42", name="GenBank", registry="Database")

# Record with overrides
standard = make_standard(id="10", name="FASTQ", isMaintained=True)

# Policy with mandate fields (note: mandate fields are flat on the record dict)
policy = make_policy(id="200", name="Wellcome Trust DMP Policy",
                     mandatedDataSharing="required",
                     mandatedDmpCreation="suggested")

# Paginated search response envelope
response = make_search_result([record, standard], total_count=2, total_pages=1)

# advancedSearch flat list response
response = make_advanced_search_result([record, standard])
```

One important detail for policy tests: `extract_policy_mandates()` requires `record["metadata"]` to be a dict. The `make_policy()` factory populates mandate fields as flat keys on the record (matching what the function outputs after extraction), not as a nested `metadata` dict. This is correct for testing tools that consume already-extracted mandates. If you are testing the extraction itself, provide a `metadata` dict:

```python
policy_with_metadata = make_record(
    registry="Policy",
    metadata={
        "sharing_data": {"mandated_data_sharing": "required"},
        "dmp_development": {"mandated_dmp_creation": "suggested"},
    }
)
```

### 6.3 Multi-Call Sequences

When a tool makes multiple API calls — pagination, fallback queries, graph traversal, batch fetches — use `side_effect` with a list:

```python
mock_client.query.side_effect = [
    make_search_result([record1, record2], total_count=3, total_pages=2),  # page 1
    make_search_result([record3], total_count=3, total_pages=2),           # page 2
    make_search_result([], total_count=3, total_pages=2),                  # sentinel: no more
]
result = await filter_records_by_date(min_year=2020, max_year=2023)
self.assertEqual(mock_client.query.call_count, 3)
```

The list is consumed in order. If the tool makes more calls than the list has entries, the mock raises `StopIteration` and the test fails immediately — which is what you want. If it makes fewer calls, the remaining entries are silently unused.

### 6.4 Auth Error Propagation

Auth errors must propagate out of tools — they must never be silently converted into empty results or generic error messages. Test this explicitly:

```python
from fairsharing_mcp.client import FAIRsharingAuthError

mock_client.query.side_effect = FAIRsharingAuthError("Invalid API key")
with self.assertRaises(FAIRsharingAuthError):
    await my_tool(record_id=1)
```

If this test fails — if the auth error becomes a string return value instead of raising — the tool has a bare `except FAIRsharingError` that is swallowing the subclass. See Section 11 for how to fix that.

### 6.5 Assertion Style

Tests in this codebase use substring matching rather than exact output comparison:

```python
self.assertIn("GenBank", result)           # name appears
self.assertIn("Grade B", result)           # score grade rendered
self.assertIn("72", result)                # score value rendered
self.assertNotIn("Error", result)          # no error message
```

This tolerates layout changes while catching logic regressions. Avoid asserting the exact markdown structure unless the test is specifically about formatting.

---

## 7. API Quirks That Have Caused Bugs

These are behaviours of the FAIRsharing API that are not obvious from reading the code and have each produced at least one real bug. Know them before you write new query code.

### 1. Four query types, four response shapes

The API has four distinct response shapes, and there is no consistent pattern. Using the wrong accessor on any of them produces a silent empty result rather than an error:

```python
# searchFairsharingRecords — paginated, with envelope
records     = data["searchFairsharingRecords"]["records"]   # list
total_count = data["searchFairsharingRecords"]["totalCount"]
total_pages = data["searchFairsharingRecords"]["totalPages"]

# multiTagFilter — flat list, no envelope
records = data["multiTagFilter"]   # already a list

# advancedSearch — flat list, no envelope
records = data["advancedSearch"]   # already a list

# fairsharingRecord — single dict
record = data["fairsharingRecord"]  # dict or None
```

If you accidentally use `data.get("searchFairsharingRecords", {}).get("records", [])` on an `advancedSearch` response, you get an empty list and no error. This is exactly how two bugs manifested.

### 2. FAIR indicator fields are in the metadata blob

The FAIRsharing API stores FAIR indicator values (data access condition, curation type, etc.) inside a JSON string in the `metadata` field, not as top-level GraphQL fields. The `extract_fair_indicators()` function in `formatters.py` parses this blob. Do not try to read `record["dataAccessCondition"]` directly after a standard query — use the extractor or use a query that calls the extractor as part of its response handling (`fetch_database_quality_with_fallback()` does this automatically).

### 3. No server-side date filtering

The API has no `createdAfter` or `updatedBefore` filter. Date filtering is implemented by scanning pages of results and checking the `createdAt` / `updatedAt` fields client-side. The scan is capped at `FAIRSHARING_MAX_SCAN` (default: 2000 records). If your date-range query matches 50,000 records, you are scanning 40 pages out of a possible 1000. The output will include a truncation warning unless `FAIRSHARING_TRUNCATION_WARNING=false`.

### 4. Policy mandate fields are nested two levels deep

A policy record's mandate data is stored as a nested dict inside `metadata` (e.g., `metadata.sharing_data.mandated_data_sharing`). The `extract_policy_mandates()` function in `helpers.py` flattens these into the top-level record dict. It mutates the record in place and sets `record["_mandate_extraction_failed"] = True` if `metadata` is absent or is not a dict. If you see a policy tool returning `None` for all mandate fields, check that the query included the `metadata` field and that the test mock has it as a dict, not a JSON string.

### 5. `multiTagFilter` returns everything at once

Unlike `searchFairsharingRecords`, `multiTagFilter` has no server-side pagination. It returns every matching record in one response. For a broad query this can be thousands of records in a single API call. If you are writing a tool that uses `multiTagFilter` and expects pagination, you are misunderstanding the endpoint.

### 6. FAIRsharing URLs are not `https://fairsharing.org/{numeric_id}`

A record with numeric ID 12345 does not have the URL `https://fairsharing.org/12345`. The URL is derived from the DOI suffix, e.g. `10.25504/FAIRsharing.1943d4` becomes `https://fairsharing.org/FAIRsharing.1943d4`. The mapping between numeric ID and DOI suffix is not deterministic from the ID alone. Always use `build_fairsharing_url(record.get("doi"))` from `formatters.py` to construct URLs, and never construct them from the numeric ID.

---

## 8. The Quality Scoring System, in Plain English

The server computes quality scores for FAIRsharing records. Understanding what those scores measure — and what they do not — is necessary both for interpreting tool output and for maintaining the scoring code.

### The conceptual model

There is no single FAIRsharing quality metric. The server computes three separate scoring systems, one per registry type, and then normalizes them all to a common 0–100 scale for cross-registry comparison. All weights were chosen based on domain judgment, not empirical calibration. A high score means the record is well-described in FAIRsharing and follows open data practices. It does not mean the resource is scientifically excellent, widely used, or appropriate for any specific use case.

### Database scoring (raw 0–9)

A database record has nine FAIR indicator fields: `dataAccessCondition`, `dataCuration`, `dataDepositionCondition`, `citationToRelatedPublications`, `dataContactInformation`, `dataVersioning`, `dataPreservationPolicy`, `resourceSustainability`, and `usesPersistentIdentifier`. These fields contain string values like "open", "manual", "yes", or boolean `true`/`false`.

The scoring function `compute_fair_score_detailed()` in `formatters.py` maps each value to a point contribution:

- "open", "yes", "manual", "automated", `True` → 1.0 point (full credit)
- "partially open", "controlled", "embargoed" → 0.5 points (partial credit)
- "none", "not found", "no", `False` → 0 points
- `None` (field absent from API response) → 0 points, counted as missing
- Any other string → 0.5 points (imputation for unknown values)

The raw score is the sum of point values across all nine fields, giving a maximum of 9.0. The normalized score is `(raw / 9) × 100`. So a database that scores, say, 6.5 out of 9 will receive a normalized score of about 72 — a Grade B.

Confidence metadata reflects how much of the score is based on actual data versus missing fields: high (all 9 indicators present), medium (≤2 missing), low (>2 missing). A database with only 3 indicators populated in FAIRsharing will get a low-confidence score — the grade reflects data sparsity, not necessarily actual poor quality.

### Standard scoring (raw 0–10)

Standards are scored on three components:

- **Identity and access** (max 3): Has a homepage (1 point), has a DOI (1 point), has a description (1 point).
- **Status and maintenance** (max 3): Status is "ready" (2 points) or "uncertain" (0.5 points); `isMaintained` is true (1 point).
- **Usage and connectivity** (max 4): Counts the number of implementing databases and recommending policies, on a logarithmic-ish curve. A standard implemented by many databases and recommended by multiple policies scores close to 4.

The normalized score is `(raw / 10) × 100`.

### Policy scoring (raw 0–10)

Policies are scored on three components:

- **Mandate clarity** (max 4): Four core mandate fields — `mandatedDataSharing`, `mandatedDmpCreation`, `metadataSharing`, and `dataAvailabilityStatement` — each worth 1 point if defined and non-null.
- **Coverage breadth** (max 3): Six additional coverage and compliance fields, each worth 0.5 points.
- **Recommendations** (max 3): Recommends at least one standard (1.5 points) and/or at least one database (1.5 points).

The normalized score is `(raw / 10) × 100`.

### Unified normalization and grades

`normalize_quality_score()` in `formatters.py` takes a raw score and its maximum and maps it to 0–100. The grade thresholds from `constants.py` are: A+ ≥90, A ≥80, B ≥65, C ≥50, D ≥35, F <35.

### What the scores cannot tell you

The weights have not been validated against expert judgements or user outcomes. Changing the weight ratios significantly would re-rank records. The comprehensive scoring profiles (`get_comprehensive_quality_profile`) layer temporal health signals (how recently the record was updated) and community trust signals (how many policies recommend this database) on top of the basic score, but they are more sensitive to sparse data. The confidence level in the output is the most reliable indicator of whether a specific score deserves weight.

---

## 9. Graph Analysis: The Local Neighborhood Constraint

The graph tools — `compute_pagerank`, `detect_communities`, `find_semantic_path`, and the rest — operate on what the codebase calls the "local neighborhood graph." This is not a subgraph of the full FAIRsharing platform network. It is the set of records directly connected to the seed record, as returned by a single call to the `fairsharingGraph` API endpoint.

In concrete terms: if you call `compute_pagerank` with record ID 1 (a database), the graph contains that database, the standards it implements, the policies that recommend it, and the edges between them — roughly 1-hop from the seed. It does not follow those connections further. If Standard A is in the graph because DB1 implements it, and Standard A is also implemented by DB2 and DB3, DB2 and DB3 are not in the graph unless DB1 also has a direct relationship with them.

This means PageRank computed on this graph measures centrality within the seed record's immediate neighborhood. A record that ranks first in local PageRank may be structurally peripheral in the full FAIRsharing network. Two records from different neighborhoods might score identically in local PageRank while having entirely different platform-wide influence.

Every graph analysis tool appends this scope caveat to its output:

```python
# graph_analysis.py
_SCOPE_CAVEAT = (
    "_Scope: This analysis covers **only the local neighborhood graph** of "
    "the seed record (1 API call). Metrics like PageRank and betweenness "
    "reflect local structure, not platform-wide importance._"
)
```

Do not remove this. It prevents misuse of the output.

The multi-seed tools (`explore_expanded_graph` and `build_topic_graph`) partially mitigate this by fetching multiple neighborhood graphs and merging them with `merge_graphs()` from `graph_utils.py`. The merged graph is still not the platform graph, but it covers a larger slice of the network.

### Thread offloading

All graph algorithms (Dijkstra, PageRank, label propagation, betweenness centrality, Tarjan's SCC) are pure Python functions that operate on in-memory data structures. They are CPU-bound, not I/O-bound, so running them directly in an async function would block the event loop. They are all offloaded to a thread pool via `run_in_thread()`:

```python
# graph_utils.py
async def run_in_thread(fn, *args, **kwargs):
    """Run a CPU-bound function in a thread pool to avoid blocking the event loop."""
    return await asyncio.to_thread(fn, *args, **kwargs)
```

If you add a new graph algorithm, follow the same pattern:

```python
result = await run_in_thread(_my_graph_algorithm, graph, param1, param2)
```

This is also why the server has no networkx dependency: networkx is a heavy package for a server whose core has three declared dependencies (`mcp`, `httpx`, `python-dotenv`). The graph algorithms are implemented from scratch in pure Python with full control over weights and traversal order.

---

## 10. Configuration for Different Workloads

All configuration is via environment variables, read from `.env` in the project root (via `load_dotenv()` in `app.py`) or set directly in the environment. The `.env.example` file lists all supported variables.

### Bulk date-range analysis

If you are running `filter_records_by_date` or any date-filtered tool across the full FAIRsharing corpus, the default `FAIRSHARING_MAX_SCAN=2000` will stop after scanning roughly 40 pages at 50 records per page. FAIRsharing has over 8,000 records. To scan everything:

```bash
export FAIRSHARING_MAX_SCAN=50000
```

Be aware of what this implies: 50,000 records at 50 per page is 1,000 sequential API calls. At 5 requests per second, that is at minimum 200 seconds of wall-clock time. Use the per-call `max_scan` parameter on `filter_records_by_date` if you only need the high limit occasionally, rather than setting it globally.

### Verbose output for debugging or research

The default display limits truncate long lists: subjects are capped at 5, associations at 20, organisations at 10, description text at 300 characters. For detailed inspection:

```bash
export FAIRSHARING_DISPLAY_MAX_ASSOCIATIONS=0     # 0 = no limit
export FAIRSHARING_DISPLAY_MAX_SUBJECTS=0
export FAIRSHARING_DISPLAY_MAX_ORGANISATIONS=0
export FAIRSHARING_DISPLAY_MAX_DESCRIPTION_CHARS=0
```

The `FAIRSHARING_TRUNCATION_WARNING=false` env var suppresses "showing X of Y" banners if you are processing output programmatically and don't want those in your data.

### Programmatic tool chaining

When chaining tool output through downstream code or another tool, use `output_format="json"` on any tool that supports it. Every tool with JSON support returns a clean Python dict/list serialized as `json.dumps(..., indent=2)`. Error conditions return a plain string in both modes (never a JSON object with an error key wrapped in a string), so check for `result.startswith("{")` if you need to detect success programmatically.

---

## 11. Debugging Common Problems

### Empty results when the FAIRsharing website shows matching records

The most common cause is a response shape mismatch (see Section 7, Quirk 1). If the tool is using `multiTagFilter` or `advancedSearch` but extracting results with `data.get("searchFairsharingRecords", {}).get("records", [])`, it returns an empty list with no error. Check which query constant the tool uses and make sure the extraction matches.

### MCP client disconnects at startup with no clear error

Almost always a `print()` call in code that runs at import time — module-level code, class attribute initializations, or decorator bodies. Grep for `print(` in `src/fairsharing_mcp/`. Any match is a bug.

### `AttributeError: module 'fairsharing_mcp.app' has no attribute 'mcp'`

Circular import. Something new in `tools/` or `helpers.py` is importing a module that re-imports `app.py` before `app.py` has finished initializing. Trace the import chain from the offending module. The most common new occurrence is adding a helper function to `helpers.py` that calls something in a tool module.

### Tests pass but the tool fails when called from Claude Desktop

The mock data shape doesn't match the real API response shape. Tools that fail this way typically raise `KeyError` or `AttributeError` in production because a field the formatting code expects is missing from the mock. Check that your mock uses `make_record()` / `make_search_result()` from `conftest.py`. The most frequent missing field is `metadata` — if the tool calls `extract_fair_indicators()` or `extract_policy_mandates()`, the mock record must include a `metadata` dict.

### Quality score is 0 for a database you know has open data

The FAIR indicator fields come from the `metadata` blob in the API response. If the GraphQL query used to fetch the record didn't include the `metadata` field, the blob is absent and all indicators return 0. Use `GET_DATABASE_QUALITY_QUERY` (which includes `metadata`) or the `fetch_database_quality_with_fallback()` helper, which handles this automatically.

### Auth error silently becomes "no records found"

A `except FAIRsharingError` block is catching `FAIRsharingAuthError`, which is a subclass of `FAIRsharingError`. The fix:

```python
# WRONG — swallows the auth error
except FAIRsharingError as e:
    return f"Error: {e}"

# CORRECT — re-raise auth errors, handle everything else
except FAIRsharingAuthError:
    raise
except FAIRsharingError as e:
    return f"Error: {e}"
```

Model your error handling on `helpers.fetch_policy_with_fallback()`, which demonstrates the correct pattern explicitly.

### Policy mandate fields are all `None`

`extract_policy_mandates()` requires `record["metadata"]` to be a dict. If `metadata` is absent, a JSON string, or any other type, the function sets `record["_mandate_extraction_failed"] = True` and returns `False` as its second return value. The flat mandate keys are never populated. Three things to check: (1) the GraphQL query includes the `metadata` field, (2) the record is actually a Policy (not a Database or Standard), and (3) if testing, the mock includes `metadata` as a Python dict, not a JSON string.

---

## 12. Module Quick Reference

This section is a fast-scan reference for developers who know what they want to do but not where the relevant code lives.

**`server.py`** is the entry point. It is 26 lines. Its entire job is to import the tools package (triggering decorator registration) and call `mcp.run()`. You would modify this file only to change transport-level behaviour, which is rare.

**`app.py`** holds the two singletons: the `FastMCP` instance (`mcp`) and the client factory (`get_client()`). It also configures logging and registers the `atexit` shutdown hook that closes the persistent HTTP connection on process exit. You would modify this file to change the server's system prompt for the LLM (`FAIRSHARING_INSTRUCTIONS`), or to change how the client singleton is created.

**`client.py`** is where all API communication happens. It contains the `_TokenBucket` rate limiter, the LRU response cache, the retry logic, the persistent `httpx.AsyncClient`, and the opportunistic date index. You would modify this file to change request behaviour — timeout defaults, retry strategy, caching policy, rate limiting parameters.

**`queries.py`** is a collection of 27 GraphQL query string constants. You add a new constant here when you need a query shape that doesn't exist yet. The file contains no logic — just strings.

**`constants.py`** holds the validation sets (valid registry names, record types), scoring weights, FAIR indicator field lists, edge color-to-relationship mappings, and grade thresholds. If you find yourself hardcoding a list of valid values in a tool function, move it here.

**`formatters.py`** contains all output formatting functions and the scoring computations (`compute_fair_score_detailed`, `normalize_quality_score`). If a tool's markdown output needs to change, the formatter is usually where the change belongs rather than the tool function itself.

**`helpers.py`** has the fallback fetchers (`fetch_policy_with_fallback`, `fetch_database_quality_with_fallback`), the mandate extractor (`extract_policy_mandates`), the advancedSearch where-clause builder (`build_advanced_search_where`), and the date range matcher (`matches_date_range`). These are shared utilities for tools that need fallback behaviour or mandate data.

**`graph_utils.py`** holds the graph data structures (`ParsedGraph`, `NodeInfo`), the graph parser, the merge functions, and `run_in_thread()`. Anything shared between `graph.py` and `graph_analysis.py` lives here. New graph data structures or utilities belong here rather than in either tool module.

**`validation.py`** has three utilities: `validate_record_id` (raises ValueError if ≤0), `validate_page_params` (clamps to config limits), and `validate_query_length` (truncates and logs a warning). These are defense-in-depth utilities — Pydantic Field validation on tool parameters catches most issues at the MCP layer before the function runs, but these provide an additional check inside the function body.

**`config.py`** reads all environment variables and provides typed accessor functions. If you need to add a new configuration knob, add it here, not as a raw `os.getenv()` call inside a tool function.

**The 11 tool modules** (`tools/search.py`, `tools/records.py`, etc.) are organized by domain as described in Section 4. Each file registers its tools at import time via decorators. The Python function names are the internal names used in tests; the `name=` parameter in the decorator is the MCP name that the LLM sees. Both exist because tests import function names directly, and FastMCP needs its own naming scheme.

---

## Where to Go From Here

The 282 tests in `tests/test_server.py` are the most comprehensive collection of worked examples for every tool in the codebase. If you are unsure how a specific tool is supposed to behave, reading its test is faster than reading its implementation.

`CLAUDE.md` in the project root has the command-line quick reference: how to run tests, run the linter, and start the server.

`TECHNICAL_DOCUMENTATION.md` in this directory has the complete tool parameter listings, all GraphQL query constants, and the full configuration variable reference.
