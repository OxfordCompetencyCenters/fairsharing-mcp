"""FAIRsharing GraphQL client with rate limiting, caching, and error handling."""

import asyncio
import logging
import random
import time
from typing import Any

import httpx

logger = logging.getLogger(__name__)


def sanitize_graphql_string(value: str) -> str:
    """Sanitize a string value for safe use in GraphQL variables.

    Escapes characters that could cause issues in GraphQL JSON payloads
    and removes problematic Unicode line/paragraph separators.
    """
    # Replace Unicode line/paragraph separators (problematic in JSON strings)
    value = value.replace("\u2028", " ").replace("\u2029", " ")
    # Strip leading/trailing whitespace
    value = value.strip()
    return value


def sanitize_variables(variables: dict[str, Any] | None) -> dict[str, Any] | None:
    """Recursively sanitize string values in GraphQL variables."""
    if variables is None:
        return None
    result = {}
    for key, value in variables.items():
        if isinstance(value, str):
            result[key] = sanitize_graphql_string(value)
        elif isinstance(value, list):
            result[key] = [sanitize_graphql_string(v) if isinstance(v, str) else v for v in value]
        elif isinstance(value, dict):
            result[key] = sanitize_variables(value)
        else:
            result[key] = value
    return result


class FAIRsharingError(Exception):
    """Base exception for FAIRsharing API errors."""

    pass


class FAIRsharingAuthError(FAIRsharingError):
    """Authentication error."""

    pass


class FAIRsharingRateLimitError(FAIRsharingError):
    """Rate limit exceeded."""

    pass


class _CacheEntry:
    """A cached query result with TTL."""

    __slots__ = ("data", "expires_at")

    def __init__(self, data: dict[str, Any], ttl: float):
        self.data = data
        self.expires_at = time.monotonic() + ttl

    @property
    def is_expired(self) -> bool:
        return time.monotonic() > self.expires_at


class _TokenBucket:
    """Token bucket rate limiter for controlling API request rate.

    Allows brief bursts when tokens have accumulated during idle periods,
    then throttles to the configured rate. More robust than a simple delay
    when multiple concurrent requests are issued via asyncio.gather.
    """

    def __init__(self, rate: float = 5.0, burst: int = 1):
        """Initialize token bucket.

        Args:
            rate: Tokens added per second (= max sustained requests/sec).
            burst: Maximum accumulated tokens (= max instant burst size).
        """
        self.rate = rate
        self.burst = burst
        self._tokens = float(burst)
        self._last_refill = 0.0
        self._lock = asyncio.Lock()

    async def acquire(self) -> None:
        """Acquire one token, waiting if necessary."""
        async with self._lock:
            now = asyncio.get_event_loop().time()
            # Refill tokens based on elapsed time
            elapsed = now - self._last_refill
            self._tokens = min(float(self.burst), self._tokens + elapsed * self.rate)
            self._last_refill = now

            if self._tokens >= 1.0:
                self._tokens -= 1.0
                return

            # Wait for a token to accumulate
            wait_time = (1.0 - self._tokens) / self.rate
            await asyncio.sleep(wait_time)
            self._tokens = 0.0
            self._last_refill = asyncio.get_event_loop().time()


class FAIRsharingClient:
    """Async GraphQL client for FAIRsharing API."""

    DEFAULT_URL = "https://api.fairsharing.org/graphql/"
    DEFAULT_RATE_RPS = 5.0  # 5 requests per second (200ms interval)
    DEFAULT_RATE_BURST = 3  # Allow short bursts after idle
    DEFAULT_CACHE_TTL = 300.0  # 5 minutes for reference data
    DEFAULT_CACHE_MAXSIZE = 500  # Maximum cache entries before eviction

    def __init__(
        self,
        api_key: str,
        base_url: str | None = None,
        timeout: float = 30.0,
        max_retries: int = 3,
        cache_ttl: float | None = None,
        cache_maxsize: int | None = None,
        rate_limit_rps: float | None = None,
        rate_limit_burst: int | None = None,
    ):
        """Initialize the FAIRsharing client.

        Args:
            api_key: FAIRsharing API key (X-GraphQL-Key header)
            base_url: Optional custom API URL
            timeout: Request timeout in seconds
            max_retries: Maximum retry attempts for transient errors
            cache_ttl: TTL for cached responses in seconds (default: 300s)
            cache_maxsize: Maximum cache entries before LRU eviction (default: 500)
            rate_limit_rps: Sustained request rate in requests/sec (default: 5.0)
            rate_limit_burst: Max burst size after idle periods (default: 3)
        """
        if not api_key:
            raise FAIRsharingAuthError("API key is required")

        self.api_key = api_key
        self.base_url = base_url or self.DEFAULT_URL
        self.timeout = timeout
        self.max_retries = max_retries
        self.cache_ttl = cache_ttl if cache_ttl is not None else self.DEFAULT_CACHE_TTL
        self.cache_maxsize = (
            cache_maxsize if cache_maxsize is not None else self.DEFAULT_CACHE_MAXSIZE
        )
        self._rate_limiter = _TokenBucket(
            rate=rate_limit_rps if rate_limit_rps is not None else self.DEFAULT_RATE_RPS,
            burst=rate_limit_burst if rate_limit_burst is not None else self.DEFAULT_RATE_BURST,
        )
        self._cache: dict[str, _CacheEntry] = {}

        self.headers = {
            "Accept": "application/json",
            "Content-Type": "application/json",
            "X-GraphQL-Key": api_key,
        }
        self._http_client: httpx.AsyncClient | None = None
        # Opportunistic date index: record_id -> {"createdAt": ..., "updatedAt": ...}
        self._date_index: dict[int, dict[str, str | None]] = {}

    def _get_http_client(self) -> httpx.AsyncClient:
        """Get or create a persistent HTTP client for connection reuse."""
        if self._http_client is None or self._http_client.is_closed:
            self._http_client = httpx.AsyncClient(
                timeout=self.timeout,
                headers=self.headers,
                http2=False,
            )
        return self._http_client

    async def aclose(self) -> None:
        """Close the persistent HTTP client and release connections."""
        if self._http_client is not None and not self._http_client.is_closed:
            await self._http_client.aclose()
            self._http_client = None

    def _index_dates_from_response(self, data: dict[str, Any]) -> None:
        """Extract createdAt/updatedAt from API responses into the date index.

        Opportunistically indexes dates from every API response so that
        date-filtered queries can skip already-known records without re-fetching.
        """

        def _index_record(rec: dict) -> None:
            rec_id = rec.get("id")
            if rec_id is None:
                return
            try:
                rec_id = int(rec_id)
            except (ValueError, TypeError):
                return
            created = rec.get("createdAt")
            updated = rec.get("updatedAt")
            if created or updated:
                existing = self._date_index.get(rec_id)
                if existing:
                    # Merge: prefer non-None values
                    if created:
                        existing["createdAt"] = created
                    if updated:
                        existing["updatedAt"] = updated
                else:
                    self._date_index[rec_id] = {
                        "createdAt": created,
                        "updatedAt": updated,
                    }

        # searchFairsharingRecords -> {records: [...]}
        search = data.get("searchFairsharingRecords")
        if isinstance(search, dict):
            for rec in search.get("records", []):
                if isinstance(rec, dict):
                    _index_record(rec)

        # multiTagFilter -> flat list
        mtf = data.get("multiTagFilter")
        if isinstance(mtf, list):
            for rec in mtf:
                if isinstance(rec, dict):
                    _index_record(rec)

        # advancedSearch -> flat list
        adv = data.get("advancedSearch")
        if isinstance(adv, list):
            for rec in adv:
                if isinstance(rec, dict):
                    _index_record(rec)

        # fairsharingRecord -> single record
        single = data.get("fairsharingRecord")
        if isinstance(single, dict):
            _index_record(single)

    def get_date_for_record(self, record_id: int) -> dict[str, str | None] | None:
        """Get cached date info for a record, or None if not indexed."""
        return self._date_index.get(record_id)

    def get_date_index_size(self) -> int:
        """Return the number of records in the date index."""
        return len(self._date_index)

    def _cache_key(self, graphql_query: str, variables: dict[str, Any] | None) -> str:
        """Generate a cache key from query and variables."""
        import hashlib
        import json as _json

        key_str = graphql_query.strip() + "|" + _json.dumps(variables, sort_keys=True, default=str)
        return hashlib.md5(key_str.encode()).hexdigest()

    async def query(
        self,
        graphql_query: str,
        variables: dict[str, Any] | None = None,
        cache: bool = False,
    ) -> dict[str, Any]:
        """Execute a GraphQL query.

        Args:
            graphql_query: The GraphQL query string
            variables: Optional query variables
            cache: If True, cache the result for cache_ttl seconds.
                   Useful for reference data (subjects, domains, registries, etc.)

        Returns:
            The data portion of the GraphQL response

        Raises:
            FAIRsharingError: On API errors
            FAIRsharingAuthError: On authentication errors
            FAIRsharingRateLimitError: On rate limit errors
        """
        # Sanitize string inputs
        variables = sanitize_variables(variables)

        # Check cache
        if cache:
            ck = self._cache_key(graphql_query, variables)
            entry = self._cache.get(ck)
            if entry and not entry.is_expired:
                logger.debug("Cache hit for query")
                # Move to end for LRU ordering (most recently used = last)
                self._cache.pop(ck)
                self._cache[ck] = entry
                return entry.data

        await self._rate_limiter.acquire()

        payload = {"query": graphql_query}
        if variables:
            payload["variables"] = variables

        logger.debug(f"Executing GraphQL query with variables: {variables}")

        last_error: Exception | None = None

        for attempt in range(self.max_retries):
            try:
                http_client = self._get_http_client()
                response = await http_client.post(
                    self.base_url,
                    json=payload,
                )

                # Handle HTTP errors
                if response.status_code == 401:
                    raise FAIRsharingAuthError(
                        "Invalid API key. Please check your FAIRSHARING_API_KEY."
                    )
                elif response.status_code in (402, 429):
                    if attempt < self.max_retries - 1:
                        wait_time = 2 ** (attempt + 1)
                        retry_after = response.headers.get("Retry-After")
                        if retry_after is not None:
                            try:
                                wait_time = int(retry_after)
                            except ValueError:
                                pass
                        jitter = random.uniform(0.8, 1.2)
                        wait_time = max(0.5, wait_time * jitter)
                        logger.warning(
                            f"Rate limited ({response.status_code}), waiting {wait_time:.1f}s before retry"
                        )
                        await asyncio.sleep(wait_time)
                        continue
                    raise FAIRsharingRateLimitError("Rate limit exceeded. Please try again later.")
                elif response.status_code >= 500:
                    if attempt < self.max_retries - 1:
                        wait_time = 2**attempt
                        jitter = random.uniform(0.8, 1.2)
                        wait_time = max(0.5, wait_time * jitter)
                        logger.warning(
                            f"Server error {response.status_code}, retrying in {wait_time:.1f}s"
                        )
                        await asyncio.sleep(wait_time)
                        continue
                    raise FAIRsharingError(f"Server error: {response.status_code}")

                response.raise_for_status()
                result = response.json()

                # Handle GraphQL errors
                if "errors" in result:
                    errors = result["errors"]
                    error_messages = [e.get("message", str(e)) for e in errors]
                    raise FAIRsharingError(f"GraphQL errors: {'; '.join(error_messages)}")

                logger.debug("Query successful")
                data = result.get("data", {})

                # Opportunistically index dates from every response
                self._index_dates_from_response(data)

                # Store in cache if requested
                if cache:
                    self._cache[ck] = _CacheEntry(data, self.cache_ttl)
                    # Evict oldest entries if cache exceeds maxsize
                    while len(self._cache) > self.cache_maxsize:
                        oldest_key = next(iter(self._cache))
                        del self._cache[oldest_key]

                return data

            except httpx.TimeoutException as e:
                last_error = e
                if attempt < self.max_retries - 1:
                    wait_time = 2**attempt
                    jitter = random.uniform(0.8, 1.2)
                    wait_time = max(0.5, wait_time * jitter)
                    logger.warning(f"Request timeout, retrying in {wait_time:.1f}s")
                    await asyncio.sleep(wait_time)
                    continue
                raise FAIRsharingError(f"Request timeout after {self.max_retries} attempts") from e

            except httpx.RequestError as e:
                last_error = e
                if attempt < self.max_retries - 1:
                    wait_time = 2**attempt
                    jitter = random.uniform(0.8, 1.2)
                    wait_time = max(0.5, wait_time * jitter)
                    logger.warning(f"Network error: {e}, retrying in {wait_time:.1f}s")
                    await asyncio.sleep(wait_time)
                    continue
                raise FAIRsharingError(f"Network error: {e}") from e

        # Should not reach here, but just in case
        raise FAIRsharingError(f"Query failed after {self.max_retries} attempts") from last_error
