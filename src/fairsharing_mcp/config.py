"""Priority 1 / limitation-related configuration from environment.

All values are read via os.getenv(); ensure load_dotenv() has been called
(e.g. in app.py) before first use. Used by search, records, and tools
that need truncation visibility or env-driven caps.
"""

import os

# Defaults aligned with API and current behaviour
_DEFAULT_MAX_PER_PAGE = 50
_DEFAULT_MAX_SCAN = 2000


def _parse_bool(value: str | None) -> bool:
    if value is None:
        return False
    return value.strip().lower() in ("1", "true", "yes", "on")


def _parse_int(value: str | None, default: int, min_val: int, max_val: int) -> int:
    if value is None:
        return default
    try:
        n = int(value.strip())
        return max(min_val, min(max_val, n))
    except ValueError:
        return default


def get_max_per_page() -> int:
    """Max results per page for search and scan-based tools (API cap 50)."""
    return _parse_int(
        os.getenv("FAIRSHARING_MAX_PER_PAGE"),
        _DEFAULT_MAX_PER_PAGE,
        1,
        50,
    )


def get_max_scan() -> int:
    """Max records to scan for date filtering and scan-based counts (e.g. 2000).

    Can be raised via FAIRSHARING_MAX_SCAN up to 50_000. Very large scans
    mean many sequential API calls (rate-limited) and may hit timeouts.
    """
    return _parse_int(
        os.getenv("FAIRSHARING_MAX_SCAN"),
        _DEFAULT_MAX_SCAN,
        50,
        50_000,
    )


def get_display_limit(key: str) -> int:
    """Return display limit for a list (associations, organisations, etc.).

    Read from env FAIRSHARING_DISPLAY_MAX_<KEY> (e.g. FAIRSHARING_DISPLAY_MAX_ASSOCIATIONS).
    Defaults: associations=20, organisations=10, publications=10, taxonomies=20,
    subjects=5, domains=5, children=30, recommended=30, description_chars=300.
    Use 0 for no limit (show all).
    """
    defaults: dict[str, int] = {
        "associations": 20,
        "organisations": 10,
        "publications": 10,
        "taxonomies": 20,
        "subjects": 5,
        "domains": 5,
        "children": 30,
        "recommended": 30,
        "description_chars": 300,
        # Cap on association entries embedded in JSON output. Machine-readable
        # consumers tolerate more than a rendered list, but a record can carry
        # 1,000+ associations, so this stays bounded. Use
        # fairsharing_list_associations to page through the complete set.
        "json_associations": 100,
        # Per (label, registry) group cap in analyze_record_ecosystem. Was a
        # hardcoded 15, which no environment variable could reach.
        "ecosystem_group": 15,
    }
    default = defaults.get(key, 20)
    max_val = 100_000 if key == "description_chars" else 1000
    return _parse_int(
        os.getenv(f"FAIRSHARING_DISPLAY_MAX_{key.upper()}"),
        default,
        0,
        max_val,
    )


def get_truncation_warning() -> bool:
    """If True, tool responses include truncation / 'showing X of Y' messaging.

    Default is True (opt-out). Set FAIRSHARING_TRUNCATION_WARNING=0 or false to disable.
    """
    val = os.getenv("FAIRSHARING_TRUNCATION_WARNING")
    if val is None:
        return True  # Default to showing warnings (opt-out instead of opt-in)
    return val.strip().lower() in ("1", "true", "yes", "on")
