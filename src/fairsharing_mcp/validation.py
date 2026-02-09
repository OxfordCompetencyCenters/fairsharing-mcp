"""Client-side input validation for FAIRsharing MCP tools.

Validates record IDs, pagination parameters, and string lengths before
calling the API, so users get clear error messages instead of vague "not found".
"""

import logging

from fairsharing_mcp import config

logger = logging.getLogger(__name__)


def validate_record_id(record_id: int) -> int:
    """Validate a FAIRsharing record ID (must be positive).

    Args:
        record_id: The record ID to validate.

    Returns:
        The same record_id if valid.

    Raises:
        ValueError: If record_id is less than 1.
    """
    if record_id < 1:
        raise ValueError(f"Record ID must be positive, got {record_id}")
    return record_id


def validate_page_params(page: int = 1, per_page: int = 20) -> tuple[int, int]:
    """Validate and clamp pagination parameters.

    Args:
        page: Page number (1-based).
        per_page: Results per page.

    Returns:
        (page, per_page) clamped to valid ranges. per_page is capped by
        config.get_max_per_page() (API cap 50).
    """
    page = max(1, page)
    max_per_page = config.get_max_per_page()
    per_page = min(max(1, per_page), max_per_page)
    return page, per_page


def validate_query_length(query: str | None, max_length: int = 500) -> tuple[str | None, bool]:
    """Validate search query length to avoid oversized API payloads.

    Args:
        query: The search query string (may be None).
        max_length: Maximum allowed length (default 500).

    Returns:
        Tuple of (query, was_truncated). query is trimmed to max_length if
        provided, else None. was_truncated is True if the query was shortened.
        When query is None or empty string, returns (None, False).
    """
    if query is None:
        return None, False
    q = query.strip()
    if len(q) == 0:
        return None, False
    if len(q) > max_length:
        logger.warning(f"Query truncated from {len(q)} to {max_length} characters")
        return q[:max_length], True
    return q, False
