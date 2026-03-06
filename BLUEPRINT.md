# MCP Server Blueprint

A reusable architecture guide for building MCP (Model Context Protocol) servers that wrap domain APIs. Based on the FAIRsharing MCP server (95 tools, 12 modules, 256 tests).

This blueprint is **API-agnostic** — the patterns apply whether your upstream is GraphQL, REST, SOAP, or anything else. Swap the client layer; keep everything else.

---

## 1. Project Structure

```
your-mcp/
├── pyproject.toml
├── .env.example
├── src/your_mcp/
│   ├── __init__.py
│   ├── server.py           # Entry point (~15 lines)
│   ├── app.py              # Shared state (FastMCP + client singleton)
│   ├── client.py           # API client (rate limiting, cache, retry)
│   ├── config.py           # Environment-based configuration
│   ├── constants.py        # Validation sets, weights, thresholds
│   ├── formatters.py       # Output formatting utilities
│   ├── helpers.py          # Fallback fetchers, shared logic
│   ├── queries.py          # API query/endpoint constants
│   ├── validation.py       # Input validation utilities
│   └── tools/              # Domain tool modules
│       ├── __init__.py     # Imports all modules (triggers registration)
│       ├── search.py       # Search tools
│       ├── records.py      # Record detail tools
│       └── ...             # One file per domain
└── tests/
    ├── conftest.py         # Shared fixtures & mock factories
    └── test_server.py      # All tool tests
```

### pyproject.toml

```toml
[project]
name = "your-mcp"
version = "0.1.0"
description = "MCP server for Your API"
requires-python = ">=3.10"
dependencies = [
    "mcp[cli]>=1.2.0",       # FastMCP framework
    "httpx>=0.27.0",          # Async HTTP client
    "python-dotenv>=1.0.0",   # Environment loading
]

[project.scripts]
your-mcp = "your_mcp.server:main"

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[tool.hatch.build.targets.wheel]
packages = ["src/your_mcp"]

[tool.ruff]
target-version = "py310"
line-length = 100

[tool.ruff.lint]
select = ["E", "F", "I", "N", "W", "UP"]
ignore = ["E501"]

[tool.ruff.lint.isort]
known-first-party = ["your_mcp"]
```

Only three runtime dependencies. No heavy libraries needed — even graph algorithms can be pure Python.

---

## 2. Core Wiring — The 3-File Foundation

### server.py — Entry Point

```python
"""Your MCP Server entry point."""

import logging

import your_mcp.tools  # noqa: F401 – triggers tool registration
from your_mcp.app import mcp

logger = logging.getLogger(__name__)


def main():
    """Run the MCP server."""
    logger.info("Starting Your MCP server...")
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
```

Two jobs: import the tools package (which registers all tools via decorators), then run.

### app.py — Shared State

```python
"""Shared state: FastMCP instance and client singleton."""

import asyncio
import atexit
import logging
import os

from dotenv import load_dotenv
from mcp.server.fastmcp import FastMCP

from your_mcp.client import YourClient, YourError

# CRITICAL: Log to stderr only. stdout is reserved for MCP JSON-RPC.
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    handlers=[logging.StreamHandler()],  # stderr by default
)
logger = logging.getLogger(__name__)

load_dotenv()

mcp = FastMCP("your_mcp")

# Lazy singleton — created on first tool call
_client: YourClient | None = None


def get_client() -> YourClient:
    """Get or create the API client."""
    global _client
    if _client is None:
        api_key = os.getenv("YOUR_API_KEY")
        if not api_key:
            raise YourError("YOUR_API_KEY environment variable is not set.")
        _client = YourClient(api_key=api_key)
    return _client


def _shutdown_client() -> None:
    """Close HTTP connections on process exit."""
    global _client
    if _client is not None:
        try:
            loop = asyncio.get_event_loop()
            if loop.is_running():
                loop.create_task(_client.aclose())
            else:
                loop.run_until_complete(_client.aclose())
        except Exception:
            pass  # Best-effort cleanup
        _client = None


atexit.register(_shutdown_client)
```

**Critical rule:** `app.py` must **never** import from `tools/`. Tools import from `app`. This prevents circular imports.

### tools/\_\_init\_\_.py — Registration Trigger

```python
"""Importing this package registers all tools via decorators."""

from your_mcp.tools import (  # noqa: F401
    records,
    search,
    # ... add each domain module
)
```

Each import executes the `@app.mcp.tool()` decorators in that module, registering the tools with FastMCP. By the time `mcp.run()` is called in `server.py`, all tools are registered.

---

## 3. Client Layer

The client handles rate limiting, caching, retries, and connection pooling. This is the only layer that changes between GraphQL and REST.

### Error Hierarchy

```python
class YourError(Exception):
    """Base exception for API errors."""

class YourAuthError(YourError):
    """Authentication error (invalid/expired key)."""

class YourRateLimitError(YourError):
    """Rate limit exceeded (429)."""
```

Separate error classes let tools and helpers distinguish between:
- **Auth errors** — re-raise immediately (configuration problem, fallback won't help)
- **Rate limit errors** — retry with backoff
- **Transient errors** — retry, then optionally fall back to simpler queries

### Token Bucket Rate Limiter

```python
class _TokenBucket:
    """Allows brief bursts after idle, then throttles to sustained rate."""

    def __init__(self, rate: float = 5.0, burst: int = 3):
        self.rate = rate          # Tokens per second (sustained)
        self.burst = burst        # Max accumulated tokens
        self._tokens = float(burst)
        self._last_refill = 0.0
        self._lock = asyncio.Lock()

    async def acquire(self) -> None:
        """Acquire one token, waiting if necessary."""
        async with self._lock:
            now = asyncio.get_event_loop().time()
            elapsed = now - self._last_refill
            self._tokens = min(float(self.burst), self._tokens + elapsed * self.rate)
            self._last_refill = now

            if self._tokens >= 1.0:
                self._tokens -= 1.0
                return

            wait_time = (1.0 - self._tokens) / self.rate
            await asyncio.sleep(wait_time)
            self._tokens = 0.0
            self._last_refill = asyncio.get_event_loop().time()
```

Tune `rate` and `burst` to your API's limits.

### LRU Cache with TTL

```python
class _CacheEntry:
    __slots__ = ("data", "expires_at")

    def __init__(self, data: dict, ttl: float):
        self.data = data
        self.expires_at = time.monotonic() + ttl

    @property
    def is_expired(self) -> bool:
        return time.monotonic() > self.expires_at
```

Cache logic in the client:

```python
# On hit: promote to end (LRU)
entry = self._cache.get(cache_key)
if entry and not entry.is_expired:
    self._cache.pop(cache_key)
    self._cache[cache_key] = entry
    return entry.data

# On store: evict oldest if over capacity
self._cache[cache_key] = _CacheEntry(data, self.cache_ttl)
while len(self._cache) > self.cache_maxsize:
    oldest_key = next(iter(self._cache))
    del self._cache[oldest_key]
```

Uses Python dict ordering (insertion order) for FIFO eviction and LRU promotion.

### Connection Pooling

```python
def _get_http_client(self) -> httpx.AsyncClient:
    """Lazy-create persistent HTTP client for connection reuse."""
    if self._http_client is None or self._http_client.is_closed:
        self._http_client = httpx.AsyncClient(
            timeout=self.timeout,
            headers=self.headers,
        )
    return self._http_client

async def aclose(self) -> None:
    """Close persistent client on shutdown."""
    if self._http_client is not None and not self._http_client.is_closed:
        await self._http_client.aclose()
        self._http_client = None
```

Reuses TCP connections across requests. Set headers once at client creation.

### Retry with Exponential Backoff

```python
for attempt in range(self.max_retries):  # Default: 3
    try:
        response = await http_client.post(self.base_url, json=payload)

        if response.status_code == 401:
            raise YourAuthError("Invalid API key.")

        if response.status_code in (429,):
            if attempt < self.max_retries - 1:
                wait = int(response.headers.get("Retry-After", 2 ** (attempt + 1)))
                jitter = random.uniform(0.8, 1.2)
                await asyncio.sleep(max(0.5, wait * jitter))
                continue
            raise YourRateLimitError("Rate limit exceeded.")

        if response.status_code >= 500:
            if attempt < self.max_retries - 1:
                await asyncio.sleep(max(0.5, 2**attempt * random.uniform(0.8, 1.2)))
                continue
            raise YourError(f"Server error: {response.status_code}")

        response.raise_for_status()
        return response.json()

    except httpx.TimeoutException:
        if attempt < self.max_retries - 1:
            await asyncio.sleep(max(0.5, 2**attempt * random.uniform(0.8, 1.2)))
            continue
        raise YourError("Request timeout after retries.")
```

Auth errors (401) always fail immediately. Transient errors (429, 5xx, timeout) retry with jitter.

### GraphQL vs REST

For **GraphQL**, the client exposes:

```python
async def query(self, graphql_query: str, variables: dict | None = None) -> dict:
    payload = {"query": graphql_query}
    if variables:
        payload["variables"] = variables
    # ... rate limit, cache, retry, return data
```

For **REST**, swap to:

```python
async def get(self, endpoint: str, params: dict | None = None) -> dict:
    url = f"{self.base_url}/{endpoint}"
    response = await http_client.get(url, params=params)
    # ... rate limit, cache, retry, return data

async def post(self, endpoint: str, body: dict | None = None) -> dict:
    url = f"{self.base_url}/{endpoint}"
    response = await http_client.post(url, json=body)
    # ... same pattern
```

Everything else (rate limiter, cache, retry, connection pool) stays identical.

---

## 4. Tool Module Pattern

Every tool module follows the same structure.

### Imports

```python
"""Your MCP tools — [Domain description]."""

import json
import logging
from typing import Annotated

from mcp.types import ToolAnnotations
from pydantic import Field

from your_mcp import app, config
from your_mcp.client import YourError
from your_mcp.formatters import format_record_summary
from your_mcp.queries import SEARCH_QUERY

logger = logging.getLogger(__name__)
```

### Tool Definition

```python
@app.mcp.tool(
    name="yourdomain_search_items",           # Namespace prefix
    annotations=ToolAnnotations(
        readOnlyHint=True,                    # No mutations
        idempotentHint=True,                  # Same input = same output
        openWorldHint=True,                   # Calls external API
    ),
)
async def search_items(
    query: Annotated[
        str | None,
        Field(default=None, max_length=500, description="Search query"),
    ] = None,
    page: Annotated[
        int,
        Field(default=1, ge=1, description="Page number"),
    ] = 1,
    per_page: Annotated[
        int,
        Field(default=20, ge=1, le=50, description="Results per page"),
    ] = 20,
    output_format: Annotated[
        str,
        Field(
            default="markdown",
            pattern="^(markdown|json)$",
            description="Output format: 'markdown' or 'json'",
        ),
    ] = "markdown",
) -> str:
    """Search items in the catalog.

    Returns matching items with pagination.
    """
    client = app.get_client()

    # Defense-in-depth (Field constraints are primary)
    per_page = min(max(1, per_page), config.get_max_per_page())
    page = max(1, page)

    variables = {"q": query, "page": page, "perPage": per_page}

    try:
        data = await client.query(SEARCH_QUERY, variables)
        results = data.get("searchItems", {})
        items = results.get("items", [])

        # JSON output
        if output_format == "json":
            return json.dumps({
                "items": items,
                "total_count": results.get("totalCount", 0),
                "page": page,
            })

        # Markdown output (default)
        lines = [f"## Found {len(items)} items\n"]
        for item in items:
            lines.append(format_record_summary(item))
        return "\n".join(lines)

    except YourError as e:
        return f"Error: {e}"
```

### Key Patterns

1. **Namespace prefix**: All tools share a `yourdomain_` prefix via the `name=` parameter
2. **ToolAnnotations**: Every tool declares its hints. Use `openWorldHint=False` for static/offline tools
3. **Annotated + Field**: Pydantic constraints appear in the JSON tool schema that LLMs see
4. **Dual defaults**: Provide defaults in both `Field(default=X)` and `= X` (FastMCP requires both)
5. **Dual output**: Every tool returns markdown (human) or JSON (machine chaining)
6. **Error handling**: Catch domain errors and return user-friendly strings — never raw tracebacks

### Common Field Constraints

| Parameter type | Constraint pattern |
|---|---|
| Record/item ID | `Field(ge=1)` |
| Page number | `Field(default=1, ge=1)` |
| Per page | `Field(default=20, ge=1, le=50)` |
| Search query | `Field(default=None, max_length=500)` |
| Output format | `Field(default="markdown", pattern="^(markdown\|json)$")` |
| Year range | `Field(default=None, ge=1990, le=2030)` |
| ID list | `Field(min_length=2, max_length=50)` |
| Float weight | `Field(default=0.5, ge=0, le=1)` |
| Enum-like string | `Field(pattern="^(option_a\|option_b\|option_c)$")` |
| Depth/limit | `Field(default=2, ge=1, le=5)` |

---

## 5. Supporting Modules

### queries.py — API Query Constants

For **GraphQL**:

```python
SEARCH_QUERY = """
query SearchItems($q: String, $page: Int, $perPage: Int) {
    searchItems(q: $q, page: $page, perPage: $perPage) {
        items { id name description }
        totalCount
        totalPages
    }
}
"""

GET_ITEM_QUERY = """
query GetItem($id: ID!) {
    item(id: $id) {
        id name description
        categories { id label }
        relatedItems { id name }
    }
}
"""
```

For **REST**, store endpoint paths and schemas:

```python
ENDPOINTS = {
    "search": "/api/v1/items/search",
    "get_item": "/api/v1/items/{id}",
    "list_categories": "/api/v1/categories",
}
```

Never use string interpolation for user input in queries — always use variables/parameters.

### constants.py — Domain Knowledge

```python
# Validation sets
VALID_STATUSES = {"active", "deprecated", "draft"}
VALID_CATEGORIES = {"type_a", "type_b", "type_c"}

# Scoring weights
QUALITY_WEIGHTS = {
    "completeness": 0.3,
    "accuracy": 0.3,
    "timeliness": 0.2,
    "consistency": 0.2,
}

# Grade thresholds (0-100)
GRADE_THRESHOLDS = [
    ("A+", 90), ("A", 80), ("B", 65),
    ("C", 50), ("D", 35), ("F", 0),
]
```

### formatters.py — Output Formatting

```python
def escape_md_table(value: str) -> str:
    """Escape pipe and newline for markdown tables."""
    return value.replace("|", "\\|").replace("\n", " ").replace("\r", "")


def format_record_summary(record: dict) -> str:
    """Format a record as a markdown summary."""
    name = record.get("name", "Unknown")
    lines = [f"### {name}"]
    if desc := record.get("description"):
        lines.append(f"- **Description:** {desc[:300]}")
    return "\n".join(lines)
```

### helpers.py — Fallback Logic

```python
async def fetch_with_fallback(record_id: int) -> dict | None:
    """Fetch detailed record, fall back to basic query on transient error.

    Auth errors are re-raised immediately (configuration problem).
    """
    client = app.get_client()
    try:
        data = await client.query(GET_DETAILED_QUERY, {"id": record_id})
        return data.get("item")
    except YourAuthError:
        raise  # Never swallow auth errors
    except YourError:
        logger.warning("Detail query failed for %s, trying basic query", record_id)

    try:
        data = await client.query(GET_BASIC_QUERY, {"id": record_id})
        record = data.get("item")
        if record:
            record["_detail_unavailable"] = True  # Mark degraded data
            return record
    except YourAuthError:
        raise
    except YourError:
        logger.warning("Basic query also failed for %s", record_id)

    return None
```

The `_detail_unavailable` marker lets tools communicate that some fields are missing due to fallback.

### config.py — Environment Configuration

```python
"""Environment-based configuration with parse and clamp helpers."""

import os

def _parse_int(value: str | None, default: int, min_val: int, max_val: int) -> int:
    if value is None:
        return default
    try:
        return max(min_val, min(max_val, int(value.strip())))
    except ValueError:
        return default

def get_max_per_page() -> int:
    return _parse_int(os.getenv("YOUR_MAX_PER_PAGE"), 50, 1, 100)

def get_max_scan() -> int:
    return _parse_int(os.getenv("YOUR_MAX_SCAN"), 2000, 50, 50_000)
```

### validation.py — Input Validation

```python
"""Client-side validation for early, clear error messages."""

def validate_record_id(record_id: int) -> int:
    if record_id < 1:
        raise ValueError(f"Record ID must be positive, got {record_id}")
    return record_id

def validate_query_length(query: str | None, max_length: int = 500) -> tuple[str | None, bool]:
    """Returns (query, was_truncated)."""
    if not query:
        return None, False
    q = query.strip()
    if not q:
        return None, False
    if len(q) > max_length:
        return q[:max_length], True
    return q, False
```

---

## 6. Testing Infrastructure

### conftest.py — Shared Fixtures

```python
"""Shared test fixtures and mock factories."""

from unittest.mock import AsyncMock, patch

import pytest


@pytest.fixture
def mock_client():
    """Create a mock API client."""
    client = AsyncMock()
    client.query = AsyncMock()
    return client


@pytest.fixture
def patch_get_client(mock_client):
    """Patch app.get_client to return the mock."""
    with patch("your_mcp.app.get_client", return_value=mock_client):
        yield mock_client


# -- Mock data factories --

def make_record(id="1", name="Test Record", **extra):
    """Factory for mock record dicts."""
    rec = {
        "id": id,
        "name": name,
        "description": extra.get("description", "A test record."),
        "status": extra.get("status", "active"),
    }
    rec.update({k: v for k, v in extra.items() if k not in rec})
    return rec


def make_search_result(records, total_count=None):
    """Wrap records in a search response envelope."""
    return {
        "searchItems": {
            "items": records,
            "totalCount": total_count or len(records),
            "totalPages": 1,
        }
    }
```

### Test Pattern

```python
import unittest
from unittest.mock import AsyncMock, patch

from your_mcp.tools.search import search_items
from tests.conftest import make_record, make_search_result


class TestSearchTools(unittest.IsolatedAsyncioTestCase):

    async def test_search_items(self):
        mock_response = make_search_result([make_record()])

        with patch("your_mcp.app.get_client") as mock_get_client:
            mock_client = AsyncMock()
            mock_client.query = AsyncMock(return_value=mock_response)
            mock_get_client.return_value = mock_client

            result = await search_items(query="test")

            self.assertIn("Test Record", result)
            mock_client.query.assert_called_once()

    async def test_search_items_json_output(self):
        mock_response = make_search_result([make_record()])

        with patch("your_mcp.app.get_client") as mock_get_client:
            mock_client = AsyncMock()
            mock_client.query = AsyncMock(return_value=mock_response)
            mock_get_client.return_value = mock_client

            result = await search_items(query="test", output_format="json")

            import json
            data = json.loads(result)
            self.assertEqual(data["total_count"], 1)
```

**Single mock target:** Every test patches `your_mcp.app.get_client`. Because `app.py` never imports tools, and tools call `app.get_client()` at runtime (not import time), this single patch point works for all tools.

---

## 7. MCP Best Practices Checklist

### Tool Metadata

- [ ] **ToolAnnotations** on every tool — `readOnlyHint`, `idempotentHint`, `openWorldHint`
- [ ] **Namespace prefix** — all tools share `yourdomain_` prefix via `name=` parameter
- [ ] **Annotated + Field** — Pydantic constraints in JSON schema (LLMs see these)
- [ ] **Dual defaults** — `Field(default=X)` AND `= X` on the parameter

### Output

- [ ] **Dual format** — every tool supports `output_format="markdown"` and `"json"`
- [ ] **Graceful errors** — return user-friendly strings, never raw tracebacks
- [ ] **Truncation notes** — when output is capped, say "showing X of Y"

### Protocol

- [ ] **No print()** — all logging to stderr (`logging.StreamHandler()`)
- [ ] **STDIO transport** — `mcp.run(transport="stdio")`

### Resilience

- [ ] **Rate limiting** — token bucket with configurable rate and burst
- [ ] **Response cache** — LRU with TTL for reference data
- [ ] **Retry logic** — exponential backoff with jitter for transient errors
- [ ] **Auth error separation** — never swallow auth errors in fallback paths
- [ ] **Connection pooling** — persistent `httpx.AsyncClient` with atexit cleanup
- [ ] **Fallback queries** — degrade gracefully when detail queries fail

### Code Organization

- [ ] **Circular import avoidance** — `app.py` never imports from `tools/`
- [ ] **Lazy client** — created on first use, not at import time
- [ ] **CPU offloading** — `asyncio.to_thread()` for expensive computations
- [ ] **Single mock target** — all tests patch `app.get_client`

---

## 8. Adaptation Guide

Step-by-step for a new domain:

### Step 1: Scaffold

Copy the directory structure from Section 1. Replace `your_mcp` with your package name.

### Step 2: Client Layer

- If your API is **GraphQL**: keep the `query()` method pattern
- If your API is **REST**: implement `get()` and `post()` methods instead
- Configure rate limits, cache TTL, and retry counts for your API
- Define your error hierarchy (`YourError`, `YourAuthError`, `YourRateLimitError`)

### Step 3: Define Queries/Endpoints

Populate `queries.py` with your API's query strings or endpoint paths. Use parameterized queries — never string-interpolate user input.

### Step 4: Domain Constants

Fill `constants.py` with validation sets, scoring weights, and field definitions specific to your domain.

### Step 5: Implement Tools

Create one file per domain in `tools/`. Follow the tool template from Section 4. Start with search/list tools, then detail/get tools, then analysis tools.

For each tool:
1. Add `@app.mcp.tool()` with `name=`, `annotations=`
2. Use `Annotated[type, Field()]` on all parameters
3. Implement markdown and JSON output branches
4. Catch domain errors and return formatted strings

### Step 6: Register Tools

Add each new tool module to `tools/__init__.py`.

### Step 7: Write Tests

For each tool, write at least one test that:
1. Patches `app.get_client` with a mock
2. Configures the mock to return a known response
3. Calls the tool function directly
4. Asserts the output contains expected content

### Step 8: Configure Environment

Create `.env.example` with all environment variables. At minimum:

```env
YOUR_API_KEY=your-api-key-here
# YOUR_API_URL=https://api.example.com   # Optional override
# YOUR_MAX_PER_PAGE=50                    # Optional tuning
# YOUR_MAX_SCAN=2000                      # Optional tuning
```

### Step 9: Verify

```bash
# Run tests
python -m pytest tests/ -v

# Check lint
ruff check src/ tests/

# Format
ruff format src/ tests/

# Run the server
uv run your-mcp
```

---

## 9. Anti-Patterns to Avoid

| Anti-pattern | Why it fails | Do this instead |
|---|---|---|
| `print()` in tool code | Breaks STDIO JSON-RPC protocol | Use `logging` (goes to stderr) |
| Import tools in `app.py` | Circular imports | Tools import from `app`, never the reverse |
| Create client at import time | Fails if env var missing at import | Lazy creation in `get_client()` |
| Catch all exceptions in fallback | Hides auth errors (bad API key) | Re-raise `AuthError`, only catch transient |
| String-interpolate user input into queries | Injection risk | Use query variables/parameters |
| Return raw exception tracebacks | Confuses LLM users | Return `f"Error: {e}"` |
| New HTTP client per request | Connection overhead | Persistent `httpx.AsyncClient` |
| Blocking CPU work in async tools | Blocks event loop | `asyncio.to_thread()` for heavy computation |
| Guess at Field defaults | FastMCP needs both | Always set `Field(default=X)` AND `= X` |

---

## Reference Implementation

The FAIRsharing MCP server in this repository demonstrates all patterns at scale:

- **95 tools** across 12 domain modules
- **256 tests** with single mock target
- **Token bucket** rate limiter (5 RPS, burst 3)
- **LRU cache** (500 entries, 5-minute TTL)
- **Fallback queries** for degraded API responses
- **Pure Python** graph algorithms (no networkx)
- **Universal** `output_format` on every tool

See `src/fairsharing_mcp/` for the full implementation.
