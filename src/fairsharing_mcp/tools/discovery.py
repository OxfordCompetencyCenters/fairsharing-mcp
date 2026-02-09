"""FAIRsharing MCP tools — Discovery and exploration."""

import json
import logging
from collections import Counter

from fairsharing_mcp import app
from fairsharing_mcp.client import FAIRsharingAuthError, FAIRsharingError
from fairsharing_mcp.queries import (
    GET_GRAPH_QUERY,
    GET_LATEST_STATS_QUERY,
    GET_RECORD_WITH_ASSOCIATIONS_QUERY,
    GET_REGISTRIES_QUERY,
    LIST_COUNTRIES_QUERY,
    LIST_DOMAINS_QUERY,
    LIST_LICENCES_QUERY,
    LIST_SUBJECTS_QUERY,
    MULTI_TAG_FILTER_QUERY,
    SEARCH_PUBLICATIONS_QUERY,
    SEARCH_RECORDS_COMPACT_QUERY,
    SEARCH_RECORDS_QUERY,
)

logger = logging.getLogger(__name__)

# Tool name → short description for keyword-based recommendation (from TOOLS.md).
TOOL_CATALOG: list[tuple[str, str]] = [
    ("search_records", "Search and filter records"),
    ("count_records", "Search and filter records"),
    ("advanced_filter_records", "Search and filter records"),
    ("count_fair_records", "Search and filter records"),
    ("get_record", "Get one record"),
    ("get_record_types", "Get one record"),
    ("get_record_graph", "Record graph structure, hubs, neighbors"),
    ("find_graph_hubs", "Record graph structure, hubs, neighbors"),
    ("analyze_record_ecosystem", "Record graph associations by relationship and registry"),
    ("find_record_connections", "Path within one graph"),
    ("find_semantic_path", "Weighted path in one graph"),
    ("find_cross_graph_path", "Path between two records by merging their graphs"),
    ("find_path_across_graphs", "Path across multiple merged neighborhood graphs"),
    ("find_multiple_paths", "Paths and connections"),
    ("compute_pagerank", "Graph centrality and communities"),
    ("compute_betweenness_centrality", "Graph centrality and communities"),
    ("detect_communities", "Graph centrality and communities"),
    ("analyze_graph_comprehensive", "Full graph analysis: PageRank, communities, betweenness"),
    ("find_dependency_clusters", "Graph analysis"),
    ("analyze_path_criticality", "Graph analysis"),
    ("find_similar_records", "Similar or related records"),
    ("suggest_related_resources", "Similar or related records"),
    ("get_policy_details", "Policies"),
    ("get_policy_quality_profile", "Policies"),
    ("compare_policies_by_country", "Policies"),
    ("analyze_policy_mandates", "Policies"),
    ("trace_policy_impact", "Policies"),
    ("find_policy_gaps", "Policies"),
    ("detect_policy_conflicts", "Policies"),
    ("find_standards_for_database", "Standards and databases"),
    ("find_databases_by_standard", "Databases implementing a standard"),
    ("find_databases_for_standard", "Standards and databases"),
    ("analyze_standard_adoption", "Standards and databases"),
    ("compute_maturity_index", "Standards and databases"),
    ("find_emerging_standards", "Standards and databases"),
    ("find_endorsed_but_unadopted", "Standards and databases"),
    ("compare_records", "Compare records or landscapes"),
    ("compare_multiple_records", "Compare records or landscapes"),
    ("compare_subject_landscapes", "Compare records or landscapes"),
    ("compare_collections", "Compare records or landscapes"),
    ("check_policy_database_compliance", "Compare and compliance"),
    ("find_compliant_standards", "Compare and compliance"),
    ("compare_databases_quality", "Compare databases"),
    ("analyze_deprecation_impact", "Deprecation"),
    ("find_deprecated_resources", "Deprecation"),
    ("assess_database_indicators", "FAIR quality for databases"),
    ("get_database_quality_profile", "FAIR quality for databases"),
    ("rank_databases_by_quality", "FAIR quality for databases"),
    ("find_orphan_records", "Discovery: records missing connections"),
    ("suggest_graph_starting_points", "Discovery: best records for graph analysis"),
    ("search_publications", "Discovery: search publications"),
    ("get_registries", "Discovery: registry types"),
    ("list_licences", "Discovery: list licences"),
    ("get_statistics", "Discovery: platform statistics"),
    ("list_subjects", "Taxonomy: subjects"),
    ("search_subjects", "Taxonomy: subjects"),
    ("get_subject", "Taxonomy: subjects"),
    ("list_domains", "Taxonomy: domains"),
    ("search_domains", "Taxonomy: domains"),
    ("get_domain", "Taxonomy: domains"),
    ("list_taxonomies", "Taxonomy"),
    ("search_taxonomies", "Taxonomy"),
    ("browse_subject_hierarchy", "Taxonomy"),
    ("analyze_subject_landscape", "Taxonomy"),
    ("analyze_taxonomy_landscape", "Taxonomy"),
    ("list_organisations", "Organisations and countries"),
    ("search_organisations", "Organisations and countries"),
    ("list_countries", "Organisations and countries"),
    ("analyze_country_landscape", "Organisations and countries"),
    ("analyze_regional_distribution", "Organisations and countries"),
    ("audit_metadata_completeness", "Curator: metadata audit"),
    ("batch_audit_metadata", "Curator: batch metadata audit"),
    ("get_relationship_types", "Relationship types"),
    ("get_collection_contents", "Collection contents"),
    ("trace_influence_chain", "Influence and dependencies"),
    ("detect_circular_dependencies", "Circular dependencies"),
    ("filter_records_by_date", "Filter by date"),
    ("get_standard_quality_profile", "Standard quality profile"),
    ("search_by_doi", "Look up record by DOI or FAIRsharing URL"),
    ("check_api_health", "API connectivity, auth, and health check"),
    ("explain_fairsharing", "Reference docs: overview, indicators, workflows, scoring"),
    ("get_unified_quality_score", "Normalized 0-100 quality score for any record type"),
    ("compare_unified_quality", "Compare records of any type on a common quality scale"),
    ("assess_dmp_compliance", "Single-call DMP compliance: policy + databases -> report"),
    ("analyze_transitive_impact", "Multi-hop deprecation impact through the graph"),
    ("suggest_workflow", "Step-by-step tool workflow for common analytical tasks"),
    ("get_records_batch", "Fetch multiple records by ID list in one call"),
    ("find_referencing_records", "Reverse lookup: who implements/recommends/collects this record?"),
    ("explore_expanded_graph", "Multi-hop expanded graph analysis from a seed record"),
    ("build_topic_graph", "Topic-level graph by searching records and merging neighborhoods"),
    (
        "get_comprehensive_quality_profile",
        "Detailed quality scoring with domain-specific indicators",
    ),
]

# Synonym expansion for tool recommendation
TOOL_SYNONYMS: dict[str, list[str]] = {
    "search": ["find", "look up", "discover", "query", "list", "browse"],
    "compare": ["contrast", "diff", "vs", "versus", "side by side"],
    "quality": ["fair", "score", "grade", "rating", "indicator", "assess"],
    "policy": ["mandate", "dmp", "compliance", "funder", "journal"],
    "graph": ["network", "path", "connection", "relationship", "link"],
    "standard": ["format", "ontology", "terminology", "schema", "guideline"],
    "database": ["repository", "knowledgebase", "biobank", "resource"],
    "deprecat": ["obsolete", "retired", "replaced", "sunset"],
    "impact": ["effect", "reach", "influence", "downstream"],
    "taxonomy": ["species", "organism", "subject", "domain"],
    "adoption": ["implementation", "usage", "uptake", "maturity"],
    "recommend": ["suggest", "workflow", "next step", "what tool"],
}

# Workflow templates for suggest_workflow tool
WORKFLOW_TEMPLATES: dict[str, dict] = {
    "dmp_compliance": {
        "title": "DMP Compliance Assessment",
        "description": "Assess whether databases comply with a funder/journal policy",
        "keywords": ["dmp", "compliance", "policy", "funder", "mandate", "data management plan"],
        "steps": [
            {
                "tool": "assess_dmp_compliance",
                "note": "Single-call comprehensive assessment (preferred)",
            },
            {"tool": "get_policy_details", "note": "Or start here for detailed policy mandates"},
            {
                "tool": "find_compliant_standards",
                "note": "Find standards satisfying multiple policies",
            },
            {"tool": "check_policy_database_compliance", "note": "Check DB compliance per-policy"},
            {"tool": "get_database_quality_profile", "note": "Assess FAIR quality of each DB"},
        ],
    },
    "standard_ecosystem": {
        "title": "Standard Ecosystem Analysis",
        "description": "Understand adoption and reach of a data standard",
        "keywords": ["standard", "adoption", "ecosystem", "implementation", "maturity"],
        "steps": [
            {"tool": "search_records", "note": "Find the standard by name"},
            {"tool": "get_record", "note": "Get full standard details"},
            {"tool": "analyze_standard_adoption", "note": "See who implements and recommends it"},
            {"tool": "find_databases_for_standard", "note": "List implementing databases"},
            {"tool": "compute_maturity_index", "note": "Compare maturity across standards"},
        ],
    },
    "database_selection": {
        "title": "Database Quality Comparison",
        "description": "Find and compare the best databases for a subject area",
        "keywords": ["database", "quality", "fair", "compare", "rank", "best", "select"],
        "steps": [
            {"tool": "rank_databases_by_quality", "note": "Rank databases by FAIR score"},
            {"tool": "compare_databases_quality", "note": "Side-by-side FAIR indicator comparison"},
            {"tool": "get_database_quality_profile", "note": "Deep-dive on the top candidate"},
            {"tool": "find_standards_for_database", "note": "Check what standards it implements"},
        ],
    },
    "policy_landscape": {
        "title": "Policy Landscape Analysis",
        "description": "Compare data policies across countries or institutions",
        "keywords": ["policy", "country", "landscape", "compare", "mandate", "funder", "journal"],
        "steps": [
            {"tool": "search_records", "note": "Find policies by country/subject"},
            {"tool": "compare_policies_by_country", "note": "Cross-country mandate comparison"},
            {
                "tool": "detect_policy_conflicts",
                "note": "Find conflicts between overlapping policies",
            },
            {
                "tool": "trace_policy_impact",
                "note": "See downstream impact (standards -> databases)",
            },
            {"tool": "find_policy_gaps", "note": "Identify uncovered standards/databases"},
        ],
    },
    "deprecation_assessment": {
        "title": "Deprecation Impact Assessment",
        "description": "Assess the ripple effects of a deprecated resource",
        "keywords": ["deprecat", "impact", "retired", "obsolete", "replace", "transitive"],
        "steps": [
            {"tool": "find_deprecated_resources", "note": "Find deprecated records in a subject"},
            {"tool": "analyze_transitive_impact", "note": "Multi-hop impact analysis (preferred)"},
            {
                "tool": "analyze_deprecation_impact",
                "note": "Or single-hop analysis for quick check",
            },
        ],
    },
    "graph_exploration": {
        "title": "Knowledge Graph Exploration",
        "description": "Explore the FAIRsharing knowledge graph for a record",
        "keywords": ["graph", "network", "path", "community", "pagerank", "centrality", "explore"],
        "steps": [
            {
                "tool": "suggest_graph_starting_points",
                "note": "Find records with the richest graphs",
            },
            {"tool": "get_record_graph", "note": "Get graph structure overview"},
            {
                "tool": "analyze_graph_comprehensive",
                "note": "Full analysis: PageRank + communities + centrality",
            },
            {"tool": "find_semantic_path", "note": "Find weighted path between two nodes"},
        ],
    },
    "cross_registry_quality": {
        "title": "Cross-Registry Quality Comparison",
        "description": "Compare quality scores across databases, standards, and policies",
        "keywords": ["unified", "cross", "quality", "compare", "mixed", "score"],
        "steps": [
            {
                "tool": "compare_unified_quality",
                "note": "Compare any mix of records on 0-100 scale",
            },
            {"tool": "get_unified_quality_score", "note": "Individual record normalized score"},
        ],
    },
}


def _expand_synonyms(tokens: list[str]) -> set[str]:
    """Expand a list of query tokens using TOOL_SYNONYMS."""
    expanded = set(tokens)
    for token in tokens:
        for key, synonyms in TOOL_SYNONYMS.items():
            if token == key or token in synonyms:
                expanded.add(key)
                expanded.update(synonyms)
    return expanded


@app.mcp.tool()
async def recommend_tools(
    query: str, max_suggestions: int = 10, output_format: str = "markdown"
) -> str:
    """Recommend MCP tools based on a natural-language or keyword query.

    Uses synonym expansion and keyword matching over tool names and descriptions.
    For example, "find standards" also matches tools with "search" or "discover".
    Includes related workflow suggestions when the query matches a known workflow.

    Args:
        query: Natural-language or keyword description of the task.
        max_suggestions: Maximum number of tools to return (default: 10, max: 20).
        output_format: Output format: "markdown" (default) or "json" for structured data.

    Returns:
        A short list of suggested tool names with descriptions, plus workflow hints.
    """
    if not query or not query.strip():
        return "Provide a short query (e.g. 'search records', 'policy', 'graph path')."
    q = query.strip().lower()
    tokens = q.split()
    expanded = _expand_synonyms(tokens)
    max_suggestions = min(max(1, max_suggestions), 20)

    scored: list[tuple[int, str, str]] = []
    for name, desc in TOOL_CATALOG:
        name_lower = name.lower()
        desc_lower = desc.lower()
        score = 0

        # Original query as substring (backward compat)
        if q in name_lower:
            score += 3
        if q in desc_lower:
            score += 1

        # Per-token matching
        for token in tokens:
            if token in name_lower:
                score += 2
            if token in desc_lower:
                score += 1

        # Expanded synonym matching
        for syn in expanded - set(tokens):
            if syn in name_lower:
                score += 1
            if syn in desc_lower:
                score += 1

        if score > 0:
            scored.append((score, name, desc))

    if not scored:
        return (
            f"No tools matched '{query}'. Try broader keywords: e.g. 'search', "
            "'policy', 'graph', 'compare', 'taxonomy', 'curator', 'FAIR'.\n\n"
            "Or use `suggest_workflow(intent)` for step-by-step task guidance."
        )

    scored.sort(key=lambda x: (-x[0], x[1]))
    shown = scored[:max_suggestions]

    if output_format == "json":
        matching_workflows = []
        for wf_key, wf in WORKFLOW_TEMPLATES.items():
            wf_keywords = set(wf["keywords"])
            if expanded & wf_keywords:
                matching_workflows.append({"key": wf_key, "title": wf["title"]})
        return json.dumps(
            {
                "query": query,
                "matches": [{"tool": name, "description": desc} for _score, name, desc in shown],
                "workflows": matching_workflows[:3],
            },
            indent=2,
        )

    lines = [
        f'# Tool recommendations for "{query}"',
        f"Found {len(scored)} match(es). Showing top {min(len(shown), max_suggestions)}.",
        "",
    ]
    for _score, name, desc in shown:
        lines.append(f"- **{name}** — {desc}")

    # Check for matching workflows
    matching_workflows = []
    for wf_key, wf in WORKFLOW_TEMPLATES.items():
        wf_keywords = set(wf["keywords"])
        if expanded & wf_keywords:
            matching_workflows.append((wf_key, wf["title"]))

    if matching_workflows:
        lines.append("")
        lines.append("## Related Workflows")
        for wf_key, wf_title in matching_workflows[:3]:
            lines.append(f"- **{wf_title}** — use `suggest_workflow('{wf_key}')`")

    return "\n".join(lines)


@app.mcp.tool()
async def suggest_workflow(intent: str, output_format: str = "markdown") -> str:
    """Get a recommended multi-tool workflow for a specific task.

    Returns a step-by-step tool sequence for common analytical tasks like
    DMP compliance, standard ecosystem analysis, or database selection.
    No API call is made — this returns static workflow guidance.

    Available intents: dmp_compliance, standard_ecosystem, database_selection,
    policy_landscape, deprecation_assessment, graph_exploration,
    cross_registry_quality

    Args:
        intent: The task intent (keyword or short phrase).
            Examples: "dmp compliance", "compare databases", "deprecation impact"
        output_format: Output format: "markdown" (default) or "json" for structured data.

    Returns:
        Step-by-step workflow with tool names and descriptions
    """
    if not intent or not intent.strip():
        available = ", ".join(sorted(WORKFLOW_TEMPLATES.keys()))
        return f"Please provide an intent. Available workflows: {available}"

    normalized = intent.strip().lower().replace(" ", "_")

    # Exact match
    if normalized in WORKFLOW_TEMPLATES:
        wf = WORKFLOW_TEMPLATES[normalized]
        if output_format == "json":
            return json.dumps(
                {
                    "intent": intent,
                    "workflow": {
                        "title": wf["title"],
                        "description": wf["description"],
                        "steps": wf["steps"],
                    },
                },
                indent=2,
            )
        return _format_workflow(wf)

    # Fuzzy match via keyword overlap with synonym expansion
    tokens = intent.strip().lower().split()
    expanded = _expand_synonyms(tokens)

    best_key = None
    best_score = 0
    for wf_key, wf in WORKFLOW_TEMPLATES.items():
        wf_keywords = set(wf["keywords"])
        # Also include words from the key itself
        wf_keywords.update(wf_key.split("_"))
        overlap = len(expanded & wf_keywords)
        if overlap > best_score:
            best_score = overlap
            best_key = wf_key

    if best_key and best_score > 0:
        wf = WORKFLOW_TEMPLATES[best_key]
        if output_format == "json":
            return json.dumps(
                {
                    "intent": intent,
                    "workflow": {
                        "title": wf["title"],
                        "description": wf["description"],
                        "steps": wf["steps"],
                    },
                },
                indent=2,
            )
        return _format_workflow(wf)

    # No match
    available = "\n".join(
        f"- **{key}** — {wf['title']}: {wf['description']}"
        for key, wf in sorted(WORKFLOW_TEMPLATES.items())
    )
    return (
        f"No workflow matched '{intent}'. Available workflows:\n\n"
        f"{available}\n\n"
        "Use any of the workflow names above as the intent parameter."
    )


def _format_workflow(wf: dict) -> str:
    """Format a workflow template as markdown."""
    lines = [
        f"# Workflow: {wf['title']}",
        f"_{wf['description']}_",
        "",
        "## Steps",
    ]
    for i, step in enumerate(wf["steps"], 1):
        lines.append(f"{i}. **{step['tool']}** — {step['note']}")
    lines.append("")
    lines.append(
        "_Step 1 is usually the best starting point. Later steps provide deeper analysis._"
    )
    return "\n".join(lines)


@app.mcp.tool()
async def find_orphan_records(
    registry: str,
    orphan_type: str,
    subjects: list[str] | None = None,
    countries: list[str] | None = None,
    min_year: int | None = None,
    max_year: int | None = None,
    max_results: int = 25,
    output_format: str = "markdown",
) -> str:
    """Find records that LACK a specific type of connection (inverse queries).

    Finds records missing expected relationships, such as standards with no
    implementing database, or policies that recommend nothing. Uses server-side
    boolean filters to efficiently identify gaps.
    When min_year/max_year are set, the scan is best-effort (up to 20 pages of
    results) to find date-matching records.

    Args:
        registry: Registry to search: "Standard", "Database", "Policy"
        orphan_type: Type of missing connection:
            - "not_implemented": Standards NOT implemented by any database
            - "no_publication": Records with no associated publications
            - "no_policy_coverage": Standards/databases not recommended by any policy (is_recommended=False)
            - "recommends_no_database": Policies that recommend no databases
            - "recommends_no_standard": Policies that recommend no standards
        subjects: Optional subject filter
        countries: Optional country filter
        min_year: Minimum creation year (inclusive). Client-side filter.
        max_year: Maximum creation year (inclusive). Client-side filter.
        max_results: Maximum results to return (default: 25, max: 50)
        output_format: Output format: "markdown" (default) or "json" for structured data.

    Returns:
        List of orphan records matching the criteria
    """
    client = app.get_client()

    max_results = min(max(1, max_results), 50)

    orphan_descriptions = {
        "not_implemented": "not implemented by any database",
        "no_publication": "with no associated publications",
        "no_policy_coverage": "not recommended by any policy",
        "recommends_no_database": "that recommend no databases",
        "recommends_no_standard": "that recommend no standards",
    }

    valid_types = set(orphan_descriptions.keys())
    if orphan_type not in valid_types:
        return (
            f"Invalid orphan_type '{orphan_type}'. Valid options: {', '.join(sorted(valid_types))}"
        )

    if min_year and max_year and min_year > max_year:
        return f"Error: min_year ({min_year}) cannot be greater than max_year ({max_year})."

    def _matches_year(record: dict) -> bool:
        """Check if a record's createdAt falls within min_year..max_year."""
        date_str = record.get("createdAt")
        if not date_str:
            return False
        try:
            year = int(date_str[:4])
            if min_year and year < min_year:
                return False
            if max_year and year > max_year:
                return False
            return True
        except (ValueError, IndexError):
            return False

    has_date_filter = min_year is not None or max_year is not None

    try:
        desc = orphan_descriptions[orphan_type]

        # Determine which query and variables to use
        if orphan_type in ("recommends_no_database", "recommends_no_standard"):
            # Use MULTI_TAG_FILTER_QUERY for recommends* filters
            variables: dict = {"load": True}
            if registry:
                variables["registry"] = [registry]
            variables["status"] = ["ready"]
            if subjects:
                variables["subjects"] = subjects
            if orphan_type == "recommends_no_database":
                variables["recommendsDatabase"] = False
            else:
                variables["recommendsStandard"] = False

            data = await client.query(MULTI_TAG_FILTER_QUERY, variables)
            records = data.get("multiTagFilter", [])
            if has_date_filter:
                records = [r for r in records if _matches_year(r)]
            total_count = len(records)
            page_records = records[:max_results]
        else:
            # Use SEARCH_RECORDS_COMPACT_QUERY for boolean filters
            # When date filtering, scan multiple pages to collect enough matches
            if has_date_filter:
                variables = {
                    "registry": [registry],
                    "status": ["ready"],
                    "page": 1,
                    "perPage": 50,
                }
            else:
                variables = {
                    "registry": [registry],
                    "status": ["ready"],
                    "page": 1,
                    "perPage": max_results,
                }
            if subjects:
                variables["subjects"] = subjects
            if countries:
                variables["countries"] = countries

            if orphan_type == "not_implemented":
                variables["isImplemented"] = False
            elif orphan_type == "no_publication":
                variables["hasPublication"] = False
            elif orphan_type == "no_policy_coverage":
                variables["isRecommended"] = False

            if has_date_filter:
                # Scan multiple pages to find date-matching records
                all_matches = []
                max_scan_pages = 20
                page = 1
                while len(all_matches) < max_results and page <= max_scan_pages:
                    variables["page"] = page
                    data = await client.query(SEARCH_RECORDS_COMPACT_QUERY, variables)
                    result = data.get("searchFairsharingRecords", {})
                    records = result.get("records", [])
                    if not records:
                        break
                    for r in records:
                        if _matches_year(r):
                            all_matches.append(r)
                            if len(all_matches) >= max_results:
                                break
                    page += 1
                page_records = all_matches
                total_count = len(all_matches)
            else:
                data = await client.query(SEARCH_RECORDS_COMPACT_QUERY, variables)
                result = data.get("searchFairsharingRecords", {})
                page_records = result.get("records", [])
                total_count = result.get("totalCount", 0)

        if not page_records:
            return f"No {registry} records found {desc}."

        if output_format == "json":
            return json.dumps(
                {
                    "query": orphan_type,
                    "orphans": [
                        {
                            "id": r.get("id", ""),
                            "name": r.get("name", "Unknown"),
                            "registry": r.get("registry", registry),
                        }
                        for r in page_records
                    ],
                },
                indent=2,
            )

        lines = [
            f"## Orphan Records: {registry} records {desc}",
            f"**Total found: {total_count:,}** (showing up to {max_results})",
            "",
        ]

        if has_date_filter:
            year_range = f"{min_year or '...'}-{max_year or '...'}"
            lines.insert(2, f"**Year filter:** {year_range}")
        if subjects:
            lines.insert(2, f"**Subject filter:** {', '.join(subjects)}")
        if countries:
            lines.insert(2, f"**Country filter:** {', '.join(countries)}")

        for i, record in enumerate(page_records, 1):
            name = record.get("name", "Unknown")
            abbrev = record.get("abbreviation", "")
            rec_type = record.get("type", "")
            rec_id = record.get("id", "")

            entry = f"{i}. **{name}**"
            if abbrev:
                entry += f" ({abbrev})"
            if rec_type:
                entry += f" [{rec_type}]"
            entry += f" (ID: {rec_id})"
            lines.append(entry)

        if total_count > max_results:
            lines.append(
                f"\n_Showing {max_results} of {total_count:,} total. Increase max_results to see more._"
            )

        return "\n".join(lines)

    except FAIRsharingError as e:
        return f"Error finding orphan records: {e}"


@app.mcp.tool()
async def search_publications(query: str, output_format: str = "markdown") -> str:
    """Search publications referenced by FAIRsharing records.

    Args:
        query: Search text (title, author, DOI, etc.)
        output_format: Output format: "markdown" (default) or "json" for structured data.

    Returns:
        List of matching publications
    """
    client = app.get_client()

    if not query or not query.strip():
        return "Please provide a search query."

    try:
        data = await client.query(SEARCH_PUBLICATIONS_QUERY, {"q": query})
        records = data.get("searchPublications", [])

        if not records:
            return f"No publications found matching '{query}'."

        if output_format == "json":
            return json.dumps(
                {
                    "query": query,
                    "publications": records,
                },
                indent=2,
            )

        lines = [
            f"## Publication Search Results for '{query}' ({len(records)} found)",
            "",
        ]

        for pub in records:
            title = pub.get("title", "Unknown")
            year = pub.get("year", "")
            doi = pub.get("doi", "")
            journal = pub.get("journal", "")
            pid = pub.get("id", "N/A")

            line = f"- **{title}**"
            if year:
                line += f" ({year})"
            if journal:
                line += f" - _{journal}_"
            if doi:
                line += f" [DOI: {doi}]"
            line += f" (ID: {pid})"
            lines.append(line)

        return "\n".join(lines)

    except FAIRsharingError as e:
        return f"Error searching publications: {e}"


@app.mcp.tool()
async def get_registries(output_format: str = "markdown") -> str:
    """Get available registry types in FAIRsharing.

    Returns the different types of registries: Database, Standard, Policy, Collection.

    Args:
        output_format: Output format: "markdown" (default) or "json" for structured data.

    Returns:
        List of registry types with descriptions
    """
    client = app.get_client()

    try:
        data = await client.query(GET_REGISTRIES_QUERY, cache=True)
        result = data.get("fairsharingRegistries", {})
        registries = result.get("records", [])

        if not registries:
            return "No registry information available."

        if output_format == "json":
            return json.dumps(
                {"registries": registries},
                indent=2,
            )

        lines = [
            "## FAIRsharing Registries",
            "",
        ]

        for r in registries:
            name = r.get("name", "Unknown")
            desc = r.get("description", "No description")
            lines.append(f"### {name}")
            lines.append(desc)
            lines.append("")

        return "\n".join(lines)

    except FAIRsharingError as e:
        return f"Error fetching registries: {e}"


@app.mcp.tool()
async def aggregate_by_field(
    field: str = "registry",
    status: list[str] | None = None,
    max_values: int = 30,
    output_format: str = "markdown",
) -> str:
    """Aggregate record counts by a dimension (registry, subject, domain, or country).

    Returns counts of records grouped by the chosen field. Use for "how many
    databases vs standards?" or "records per subject/domain/country". When status
    is not provided, all statuses are included. For subject/domain/country, only
    the first max_values values are aggregated (to limit API calls).

    Args:
        field: Dimension to aggregate by: "registry", "subject", "domain", "country".
        status: Optional status filter, e.g. ["ready"] for ready-only counts.
        max_values: For subject/domain/country, max dimension values to count
            (default: 30, max: 100). Ignored for registry.
        output_format: Output format: "markdown" (default) or "json" for structured data.

    Returns:
        Table of field value -> count and optional total.
    """
    client = app.get_client()
    supported = ("registry", "subject", "domain", "country")
    if field not in supported:
        return (
            f"field must be one of: {', '.join(supported)}. "
            "Use 'registry' for Database/Standard/Policy/Collection counts."
        )

    max_values = min(max(1, max_values), 100)
    counts: list[tuple[str, int]] = []

    try:
        if field == "registry":
            registries = ["Database", "Standard", "Policy", "Collection"]
            for reg in registries:
                variables: dict = {"registry": [reg], "page": 1, "perPage": 1}
                if status:
                    variables["status"] = status
                data = await client.query(SEARCH_RECORDS_COMPACT_QUERY, variables)
                result = data.get("searchFairsharingRecords", {})
                total = result.get("totalCount", 0)
                counts.append((reg, total))
            title = "Record counts by registry"
            col_name = "Registry"
        else:
            if field == "subject":
                list_query = LIST_SUBJECTS_QUERY
                list_key = "subjects"
                filter_key = "subjects"
                label_key = "label"
            elif field == "domain":
                list_query = LIST_DOMAINS_QUERY
                list_key = "domains"
                filter_key = "domains"
                label_key = "label"
            else:
                list_query = LIST_COUNTRIES_QUERY
                list_key = "countries"
                filter_key = "countries"
                label_key = "name"

            data = await client.query(
                list_query,
                {"page": 1, "perPage": max_values},
                cache=True,
            )
            container = data.get(list_key, {})
            records = container.get("records", [])
            for rec in records:
                label = rec.get(label_key, "Unknown")
                if not label:
                    continue
                variables = {
                    filter_key: [label],
                    "page": 1,
                    "perPage": 1,
                }
                if status:
                    variables["status"] = status
                search_data = await client.query(SEARCH_RECORDS_COMPACT_QUERY, variables)
                total = search_data.get("searchFairsharingRecords", {}).get("totalCount", 0)
                counts.append((label, total))
            title = f"Record counts by {field} (first {len(counts)} values)"
            col_name = field.capitalize()

        total_all = sum(c for _, c in counts)

        if output_format == "json":
            return json.dumps(
                {
                    "field": field,
                    "counts": [{"value": n, "count": c} for n, c in counts],
                    "total": total_all,
                    "status_filter": status,
                },
                indent=2,
            )

        lines = [
            f"## {title}",
            "",
            f"| {col_name:<20} | Count  |",
            "|----------------------|--------|",
        ]
        for name, count in counts:
            display = (name[:17] + "...") if len(name) > 20 else name
            lines.append(f"| {display:<20} | {count:,} |")
        lines.append("")
        lines.append(f"**Total (shown):** {total_all:,} records")
        if status:
            lines.append(f"_(Status filter: {status})_")
        return "\n".join(lines)

    except FAIRsharingError as e:
        return f"Error aggregating by {field}: {e}"


@app.mcp.tool()
async def list_licences(page: int = 1, per_page: int = 50, output_format: str = "markdown") -> str:
    """List all licences used in FAIRsharing.

    Args:
        page: Page number (default: 1)
        per_page: Results per page (default: 50, max: 100)
        output_format: Output format: "markdown" (default) or "json" for structured data.

    Returns:
        List of licences with URLs
    """
    client = app.get_client()
    per_page = min(max(1, per_page), 100)
    page = max(1, page)

    try:
        data = await client.query(
            LIST_LICENCES_QUERY, {"page": page, "perPage": per_page}, cache=True
        )
        result = data.get("licences", {})
        records = result.get("records", [])
        total_count = result.get("totalCount", 0)
        total_pages = result.get("totalPages", 0)

        if not records:
            return "No licences found."

        if output_format == "json":
            return json.dumps(
                {"licences": records},
                indent=2,
            )

        lines = [
            f"## Licences (Page {page} of {total_pages}, Total: {total_count})",
            "",
        ]

        for lic in records:
            name = lic.get("name", "Unknown")
            lid = lic.get("id", "N/A")
            url = lic.get("url", "")
            lines.append(f"- **{name}** (ID: {lid})" + (f" - {url}" if url else ""))

        if page < total_pages:
            lines.append("")
            lines.append(f"_Use page={page + 1} to see more licences._")

        return "\n".join(lines)

    except FAIRsharingError as e:
        return f"Error listing licences: {e}"


@app.mcp.tool()
async def get_statistics(detailed: bool = False, output_format: str = "markdown") -> str:
    """Get platform-wide statistics for FAIRsharing.

    Args:
        detailed: If True, include rich aggregations (top databases implementing
                  standards, standards recommended by policies, FAIR indicator coverage, etc.)
        output_format: Output format: "markdown" (default) or "json" for structured data.

    Returns:
        Summary statistics; if detailed=True, includes aggregation breakdowns
    """
    client = app.get_client()

    try:
        latest_data = await client.query(GET_LATEST_STATS_QUERY, cache=True)
        latest = latest_data.get("latestStats", {})
        rich = latest.get("data")
        if isinstance(rich, str):
            rich = json.loads(rich)

        if not rich or not isinstance(rich, dict):
            return "No statistics available."

        if output_format == "json":
            return json.dumps(
                {
                    "last_updated": latest.get("createdAt"),
                    "data": rich,
                },
                indent=2,
            )

        lines = [
            "## FAIRsharing Statistics",
            f"_Last updated: {latest.get('createdAt', 'unknown')}_",
            "",
        ]

        # Database/Standard publication coverage (derive basic counts)
        dbs_pubs = rich.get("dbs_to_pubs", {})
        stds_pubs = rich.get("stds_to_pubs", {})
        db_total = sum(dbs_pubs.values()) if dbs_pubs else 0
        std_total = sum(stds_pubs.values()) if stds_pubs else 0

        if db_total or std_total:
            lines.append(f"- **Databases:** {db_total:,}")
            lines.append(f"- **Standards:** {std_total:,}")

        if detailed:
            # Databases with publications
            if dbs_pubs:
                lines.append("")
                lines.append("## Database Publication Coverage")
                lines.append(
                    f"- With publications: {dbs_pubs.get('databases_with_publication', 0):,}"
                )
                lines.append(
                    f"- Without publications: {dbs_pubs.get('databases_without_publication', 0):,}"
                )

            # Standards with publications
            if stds_pubs:
                lines.append("")
                lines.append("## Standard Publication Coverage")
                lines.append(
                    f"- With publications: {stds_pubs.get('standards_with_publication', 0):,}"
                )
                lines.append(
                    f"- Without publications: {stds_pubs.get('standards_without_publication', 0):,}"
                )

            # Databases linked to standards
            dbs_stds = rich.get("dbs_linked_to_stds", {})
            if dbs_stds:
                lines.append("")
                lines.append("## Databases Linked to Standards")
                for bucket, count in sorted(dbs_stds.items(), key=lambda x: x[0]):
                    lines.append(f"- **{bucket} standards:** {count:,} databases")

            # Standards implemented by databases
            stds_impl = rich.get("stds_implemented_by_a_db", {})
            if stds_impl:
                lines.append("")
                lines.append("## Standards Implemented by Databases")
                for bucket, count in sorted(stds_impl.items(), key=lambda x: x[0]):
                    lines.append(f"- **{bucket} databases:** {count:,} standards")

            # Policies linked to standards or databases
            pols_linked = rich.get("pols_linked_to_std_or_db", {})
            if pols_linked:
                lines.append("")
                lines.append("## Policies Linked to Standards/Databases")
                for bucket, count in sorted(pols_linked.items(), key=lambda x: x[0]):
                    lines.append(f"- **{bucket} links:** {count:,} policies")

            # Top databases implementing most standards
            top_dbs = rich.get("dbs_implementing_most_stds", [])
            if top_dbs:
                lines.append("")
                lines.append("## Top Databases (Most Standards Implemented)")
                for db in top_dbs[:10]:
                    lines.append(
                        f"- **{db.get('name', '?')}** (ID: {db.get('id', '?')}): {db.get('standards_implemented', 0)} standards"
                    )

            # Top standards recommended by policies
            top_stds = rich.get("top_10_stds_recommended_by_pols", {})
            if top_stds:
                lines.append("")
                lines.append("## Top Standards (Most Recommended by Policies)")
                for name, info in sorted(
                    top_stds.items(), key=lambda x: x[1].get("count", 0), reverse=True
                ):
                    lines.append(
                        f"- **{name}** (ID: {info.get('id', '?')}): recommended by {info.get('count', 0)} policies"
                    )

            # Top databases/standards in journals
            top_db_j = rich.get("top_10_db_recs_journal", {})
            if top_db_j:
                lines.append("")
                lines.append("## Top Databases (Most Journal Citations)")
                for name, info in sorted(
                    top_db_j.items(), key=lambda x: x[1].get("count", 0), reverse=True
                ):
                    lines.append(
                        f"- **{name}** (ID: {info.get('id', '?')}): {info.get('count', 0)} journal citations"
                    )

        return "\n".join(lines)

    except FAIRsharingError as e:
        return f"Error fetching statistics: {e}"


@app.mcp.tool()
async def suggest_related_resources(record_id: int, output_format: str = "markdown") -> str:
    """Suggest related Standards or Databases based on community usage.

    Uses a collaborative filtering approach:
    - For a Database: Recommends other Databases that implement the same Standards.
    - For a Standard: Recommends other Standards that are implemented by the same Databases.

    Args:
        record_id: The ID of the record to find suggestions for.
        output_format: Output format: "markdown" (default) or "json" for structured data.

    Returns:
        List of suggested records ranked by relevance (overlap count).
    """
    client = app.get_client()

    try:
        data = await client.query(GET_RECORD_WITH_ASSOCIATIONS_QUERY, {"id": record_id})
        record = data.get("fairsharingRecord")

        if not record:
            return f"No record found with ID {record_id}."

        name = record.get("name", "Unknown")
        registry = record.get("registry", "")

        lines = [
            f"# Suggestions for: {name} ({registry})",
            "",
        ]

        if registry == "Database":
            # 1. Find implemented standards
            implemented_standards = []
            for a in record.get("recordAssociations", []):
                if (
                    a.get("recordAssocLabel") == "implements"
                    and a.get("linkedRecord", {}).get("registry") == "Standard"
                ):
                    implemented_standards.append(a["linkedRecord"])

            if not implemented_standards:
                return f"No suggestions available. '{name}' does not implement any standards to base recommendations on."

            lines.append(f"Based on {len(implemented_standards)} implemented standards:")
            for s in implemented_standards:
                lines.append(f"- {s.get('name')} (Standard)")
            lines.append("")

            # 2. Find other databases implementing these standards
            candidate_dbs = Counter()

            for std in implemented_standards:
                # We need to fetch the standard to see who implements it (reverse associations)
                try:
                    s_data = await client.query(
                        GET_RECORD_WITH_ASSOCIATIONS_QUERY, {"id": std["id"]}
                    )
                    s_rec = s_data.get("fairsharingRecord", {})
                    for ra in s_rec.get("reverseRecordAssociations", []):
                        if ra.get("recordAssocLabel") == "implements":
                            candidate = ra.get("fairsharingRecord", {})
                            if candidate.get("registry") == "Database" and candidate.get(
                                "id"
                            ) != str(record_id):
                                candidate_dbs[candidate.get("name")] += 1
                except FAIRsharingError as e:
                    logger.warning(f"Error fetching standard details for {std['id']}: {e}")
                    continue

            if not candidate_dbs:
                return "No sufficient data to make recommendations."

            if output_format == "json":
                return json.dumps(
                    {
                        "record_id": record_id,
                        "resources": [
                            {"name": db_name, "shared_standards": score}
                            for db_name, score in candidate_dbs.most_common(10)
                        ],
                    },
                    indent=2,
                )

            lines.append("## Recommended Databases")
            for db_name, score in candidate_dbs.most_common(10):
                lines.append(f"- **{db_name}** (Implements {score} shared standards)")

        elif registry == "Standard":
            # 1. Find implementing databases
            implementing_dbs = []
            for a in record.get("reverseRecordAssociations", []):
                if (
                    a.get("recordAssocLabel") == "implements"
                    and a.get("fairsharingRecord", {}).get("registry") == "Database"
                ):
                    implementing_dbs.append(a["fairsharingRecord"])

            if not implementing_dbs:
                return f"No suggestions available. '{name}' is not implemented by any databases to base recommendations on."

            lines.append(f"Based on usage by {len(implementing_dbs)} databases.")
            lines.append("")

            # 2. Find other standards implemented by these databases
            candidate_stds = Counter()

            # Limit to top 20 databases to avoid too many queries
            for db in implementing_dbs[:20]:
                try:
                    d_data = await client.query(
                        GET_RECORD_WITH_ASSOCIATIONS_QUERY, {"id": db["id"]}
                    )
                    d_rec = d_data.get("fairsharingRecord", {})
                    for ra in d_rec.get("recordAssociations", []):
                        if ra.get("recordAssocLabel") == "implements":
                            candidate = ra.get("linkedRecord", {})
                            if candidate.get("registry") == "Standard" and candidate.get(
                                "id"
                            ) != str(record_id):
                                candidate_stds[candidate.get("name")] += 1
                except FAIRsharingError as e:
                    logger.warning(f"Error fetching database details for {db['id']}: {e}")
                    continue

            if not candidate_stds:
                return "No sufficient data to make recommendations."

            if output_format == "json":
                return json.dumps(
                    {
                        "record_id": record_id,
                        "resources": [
                            {"name": std_name, "co_implemented_by": score}
                            for std_name, score in candidate_stds.most_common(10)
                        ],
                    },
                    indent=2,
                )

            lines.append("## Recommended Standards")
            for std_name, score in candidate_stds.most_common(10):
                lines.append(f"- **{std_name}** (Co-implemented by {score} databases)")

        else:
            return f"Suggestions are currently only supported for Databases and Standards, not {registry}."

        return "\n".join(lines)

    except FAIRsharingError as e:
        return f"Error generating suggestions: {e}"


@app.mcp.tool()
async def find_databases_by_standard(
    standard_id: int | None = None,
    standard_name: str | None = None,
    max_results: int = 50,
    output_format: str = "markdown",
) -> str:
    """Find databases that implement or are linked to a given standard (reverse lookup).

    Given a standard's ID or name, returns databases that implement that standard
    (or are otherwise associated with it). Use this for "which databases use
    standard X?" or "who implements this standard?".

    Args:
        standard_id: FAIRsharing record ID of the standard (use if you know the ID).
        standard_name: Name or partial name of the standard to look up (searches then uses first match).
        max_results: Maximum number of databases to return (default: 50, max: 100).
        output_format: Output format: "markdown" (default) or "json" for structured data.

    Returns:
        List of implementing/linked databases with id, name, registry, and type.
    """
    if standard_id is None and not (standard_name and standard_name.strip()):
        return "Provide either standard_id or standard_name."
    if standard_id is not None and standard_name and standard_name.strip():
        return "Provide either standard_id or standard_name, not both."

    client = app.get_client()
    max_results = min(max(1, max_results), 100)

    try:
        rid = standard_id
        if rid is None:
            # Resolve name to ID via search
            data = await client.query(
                SEARCH_RECORDS_QUERY,
                {
                    "q": standard_name.strip(),
                    "registry": ["Standard"],
                    "status": ["ready"],
                    "page": 1,
                    "perPage": 5,
                },
            )
            result = data.get("searchFairsharingRecords", {})
            records = result.get("records", [])
            if not records:
                return f"No standard found matching '{standard_name.strip()}'."
            rid = int(records[0].get("id", 0))
            if not rid:
                return "Could not resolve standard ID from search."

        data = await client.query(GET_RECORD_WITH_ASSOCIATIONS_QUERY, {"id": rid})
        record = data.get("fairsharingRecord")
        if not record:
            return f"No record found with ID {rid}."
        if record.get("registry") != "Standard":
            return f"Record {rid} is a {record.get('registry', '?')}, not a Standard. Use a standard record ID or name."

        name = record.get("name", "Unknown")
        reverse = record.get("reverseRecordAssociations", [])
        # Implementing databases: "implements" from DB -> Standard, so reverse has fairsharingRecord = DB
        databases = []
        seen_ids = set()
        for a in reverse:
            if a.get("recordAssocLabel") != "implements":
                continue
            db = a.get("fairsharingRecord") or {}
            if db.get("registry") != "Database":
                continue
            db_id = db.get("id")
            if db_id and db_id not in seen_ids:
                seen_ids.add(db_id)
                databases.append(db)
                if len(databases) >= max_results:
                    break

        if not databases:
            return f"No databases found that implement the standard '{name}' (ID: {rid})."

        if output_format == "json":
            return json.dumps(
                {
                    "standard_id": rid,
                    "standard_name": name,
                    "databases": [
                        {
                            "id": db.get("id"),
                            "name": db.get("name", "Unknown"),
                            "abbreviation": db.get("abbreviation"),
                            "type": db.get("type"),
                        }
                        for db in databases
                    ],
                },
                indent=2,
            )

        lines = [
            f"# Databases implementing: {name} (Standard ID: {rid})",
            f"Found {len(databases)} database(s).",
            "",
        ]
        for i, db in enumerate(databases, 1):
            db_name = db.get("name", "Unknown")
            abbrev = db.get("abbreviation", "")
            db_id = db.get("id", "N/A")
            rec_type = db.get("type", "")
            line = f"{i}. **{db_name}**"
            if abbrev:
                line += f" ({abbrev})"
            line += f" — ID: {db_id} | Type: {rec_type}"
            lines.append(line)

        return "\n".join(lines)

    except FAIRsharingError as e:
        return f"Error finding databases by standard: {e}"


@app.mcp.tool()
async def suggest_graph_starting_points(
    query: str,
    registry: list[str] | None = None,
    subjects: list[str] | None = None,
    max_candidates: int = 5,
    output_format: str = "markdown",
) -> str:
    """Find records with the largest, richest knowledge graphs for a search topic.

    Graph analysis tools (find_semantic_path, compute_pagerank, detect_communities,
    etc.) operate on a single record's local knowledge graph. Records vary
    enormously in graph size — from 2 nodes to 4,000+. This tool searches for
    records matching your criteria, fetches each candidate's graph, and ranks
    them by graph size so you can pick the best starting point.

    Use this BEFORE calling any graph analysis tool to avoid picking records
    with tiny, disconnected graphs.

    API cost: 1 search call + 1 graph call per candidate (max_candidates + 1 total).

    Args:
        query: Search text to find candidate records.
        registry: Optional registry filter: ["Database"], ["Standard"], ["Policy"].
        subjects: Optional subject filter (e.g., ["Genomics"]).
        max_candidates: Number of search results to evaluate (default: 5, max: 10).
            Each candidate costs one additional API call.
        output_format: Output format: "markdown" (default) or "json" for structured data.

    Returns:
        Ranked table of candidates by graph size with node/edge counts.
    """
    client = app.get_client()
    max_candidates = min(max(1, max_candidates), 10)

    if not query or not query.strip():
        return "Please provide a search query."

    try:
        # Step 1: search for candidates
        variables: dict = {
            "q": query.strip(),
            "status": ["ready"],
            "page": 1,
            "perPage": max_candidates,
        }
        if registry:
            variables["registry"] = registry
        if subjects:
            variables["subjects"] = subjects

        data = await client.query(SEARCH_RECORDS_COMPACT_QUERY, variables)
        result = data.get("searchFairsharingRecords", {})
        records = result.get("records", [])

        if not records:
            return f"No records found matching '{query}'. Try a broader search."

        # Step 2: fetch graph size for each candidate
        candidates: list[dict] = []
        for rec in records:
            rec_id = int(rec.get("id", 0))
            name = rec.get("name", "Unknown")
            abbrev = rec.get("abbreviation", "")
            rec_registry = rec.get("registry", "?")

            node_count = 0
            edge_count = 0
            try:
                graph_data = await client.query(GET_GRAPH_QUERY, {"id": rec_id})
                raw = graph_data.get("fairsharingGraph", {}).get("data")
                if raw:
                    if isinstance(raw, str):
                        raw = json.loads(raw)
                    node_count = len(raw.get("nodes", []))
                    edge_count = len(raw.get("edges", []))
            except FAIRsharingError as e:
                logger.warning(f"Graph fetch failed for record {rec_id}: {e}")

            display_name = f"{name} ({abbrev})" if abbrev else name
            candidates.append(
                {
                    "id": rec_id,
                    "name": display_name,
                    "registry": rec_registry,
                    "nodes": node_count,
                    "edges": edge_count,
                    "score": node_count + edge_count,
                }
            )

        # Step 3: rank by graph size
        candidates.sort(key=lambda c: c["score"], reverse=True)

        if output_format == "json":
            return json.dumps(
                {
                    "starting_points": [
                        {
                            "id": c["id"],
                            "name": c["name"],
                            "nodes": c["nodes"],
                            "edges": c["edges"],
                        }
                        for c in candidates
                    ],
                },
                indent=2,
            )

        lines = [
            f"# Graph Starting Points for '{query}'",
            f"**Candidates evaluated:** {len(candidates)}",
            "",
            "| Rank | Name | ID | Registry | Nodes | Edges |",
            "|------|------|----|----------|-------|-------|",
        ]

        for rank, c in enumerate(candidates, 1):
            lines.append(
                f"| {rank} | {c['name']} | {c['id']} | {c['registry']} "
                f"| {c['nodes']:,} | {c['edges']:,} |"
            )

        lines.append("")

        # Recommendation
        best = candidates[0]
        if best["score"] > 0:
            lines.append(
                f"**Recommendation:** Use record ID **{best['id']}** ({best['name']}) "
                f"with {best['nodes']:,} nodes and {best['edges']:,} edges."
            )
        else:
            lines.append(
                "_None of the candidates have graph data. "
                "Try a different search query or registry filter._"
            )

        return "\n".join(lines)

    except FAIRsharingError as e:
        return f"Error suggesting starting points: {e}"


@app.mcp.tool()
async def find_deprecated_resources(
    subjects: list[str] | None = None,
    query: str | None = None,
    registry: list[str] | None = None,
    max_results: int = 20,
    output_format: str = "markdown",
) -> str:
    """Find deprecated records with progressive fallback for records that lost tags.

    Deprecated records may lose their subject tags, making subject-filtered searches
    return empty. This tool automatically falls back through progressively broader
    search strategies:
    1. Search with subjects + status=deprecated
    2. If empty, search by query text + status=deprecated (without subject filter)
    3. If still empty, search by registry + status=deprecated only

    For each result, includes a brief impact note (count of active dependents).

    Args:
        subjects: Subject filter (e.g., ["Genomics"]). May return empty if
            deprecated records lost their tags.
        query: Text search query. Used as fallback if subject search is empty.
        registry: Registry filter: ["Database"], ["Standard"], ["Policy"]
        max_results: Maximum results to return (default: 20, max: 50)
        output_format: Output format: "markdown" (default) or "json" for structured data.

    Returns:
        List of deprecated records with search strategy used and impact notes
    """
    client = app.get_client()
    max_results = min(max(1, max_results), 50)

    if not subjects and not query and not registry:
        return (
            "Please provide at least one of: subjects, query, or registry "
            "to search for deprecated resources."
        )

    try:
        records = []
        strategy_used = ""

        # Strategy 1: subjects + status=deprecated
        if subjects:
            variables: dict = {
                "status": ["deprecated"],
                "subjects": subjects,
                "page": 1,
                "perPage": max_results,
            }
            if registry:
                variables["registry"] = registry
            if query:
                variables["q"] = query

            data = await client.query(SEARCH_RECORDS_QUERY, variables)
            result = data.get("searchFairsharingRecords", {})
            records = result.get("records", [])
            if records:
                strategy_used = "subjects + status=deprecated"

        # Strategy 2: query text + status=deprecated (drop subjects)
        if not records and query:
            variables = {
                "q": query,
                "status": ["deprecated"],
                "page": 1,
                "perPage": max_results,
            }
            if registry:
                variables["registry"] = registry

            data = await client.query(SEARCH_RECORDS_QUERY, variables)
            result = data.get("searchFairsharingRecords", {})
            records = result.get("records", [])
            if records:
                strategy_used = "query text + status=deprecated (subjects filter removed)"

        # Strategy 3: registry + status=deprecated only (broadest)
        if not records:
            variables = {
                "status": ["deprecated"],
                "page": 1,
                "perPage": max_results,
            }
            if registry:
                variables["registry"] = registry
            if query:
                variables["q"] = query

            data = await client.query(SEARCH_RECORDS_QUERY, variables)
            result = data.get("searchFairsharingRecords", {})
            records = result.get("records", [])
            if records:
                strategy_used = "status=deprecated only (all filters relaxed)"

        if not records:
            filter_parts = []
            if subjects:
                filter_parts.append(f"subjects={subjects}")
            if query:
                filter_parts.append(f"query='{query}'")
            if registry:
                filter_parts.append(f"registry={registry}")
            return (
                "No deprecated records found"
                + (f" for: {', '.join(filter_parts)}" if filter_parts else "")
                + ". The registry may not have deprecated records matching these criteria."
            )

        if output_format == "json":
            return json.dumps(
                {
                    "subject": subjects[0] if subjects else None,
                    "deprecated": [
                        {
                            "id": r.get("id", ""),
                            "name": r.get("name", "Unknown"),
                            "registry": r.get("registry", ""),
                            "type": r.get("type", ""),
                        }
                        for r in records[:max_results]
                    ],
                },
                indent=2,
            )

        # Build output
        lines = [
            f"# Deprecated Resources ({len(records)} found)",
            f"**Search strategy:** {strategy_used}",
            "",
        ]

        if subjects and "subjects filter removed" in strategy_used:
            lines.append(
                f"_Warning: No deprecated records found with subjects={subjects}. "
                f"These records may have lost their subject tags upon deprecation._"
            )
            lines.append("")

        # For each result, fetch a brief impact note (count of active dependents)
        for i, rec in enumerate(records[:max_results], 1):
            name = rec.get("name", "Unknown")
            abbrev = rec.get("abbreviation", "")
            rec_id = rec.get("id", "")
            rec_registry = rec.get("registry", "")
            rec_type = rec.get("type", "")

            entry = f"### {i}. {name}"
            if abbrev:
                entry += f" ({abbrev})"
            lines.append(entry)
            lines.append(
                f"- **Registry:** {rec_registry} | **Type:** {rec_type} | **ID:** {rec_id}"
            )

            # Fetch active dependents count
            try:
                detail_data = await client.query(GET_RECORD_WITH_ASSOCIATIONS_QUERY, {"id": rec_id})
                detail = detail_data.get("fairsharingRecord", {})
                incoming = detail.get("reverseRecordAssociations", [])
                active_deps = sum(
                    1
                    for a in incoming
                    if a.get("fairsharingRecord", {}).get("status", "").lower() == "ready"
                )
                if active_deps > 0:
                    lines.append(
                        f"- **Impact:** {active_deps} active record(s) still depend on this"
                    )
                else:
                    lines.append("- **Impact:** No active dependents")
            except FAIRsharingError:
                lines.append("- **Impact:** Could not determine (API error)")

            lines.append("")

        return "\n".join(lines)

    except FAIRsharingError as e:
        return f"Error finding deprecated resources: {e}"


@app.mcp.tool()
async def check_api_health(output_format: str = "markdown") -> str:
    """Check FAIRsharing API connectivity, authentication, and response time.

    Makes a lightweight query (fetch registries) to verify that:
    - The API is reachable
    - The API key is valid
    - Response times are acceptable

    Use this tool to diagnose connection issues or verify setup before
    running expensive multi-step analyses.

    Args:
        output_format: Output format: "markdown" (default) or "json" for structured data.

    Returns:
        API health status with response time and diagnostics
    """
    import time as _time

    health: dict = {}
    lines = ["# FAIRsharing API Health Check", ""]

    try:
        client = app.get_client()
    except FAIRsharingError as e:
        health = {"status": "FAIL", "error": str(e)}
        if output_format == "json":
            return json.dumps(health, indent=2)
        lines.append("**Status:** FAIL")
        lines.append(f"**Error:** {e}")
        lines.append("")
        lines.append("Check that FAIRSHARING_API_KEY is set in your environment.")
        return "\n".join(lines)

    # Test 1: Basic connectivity + auth
    start = _time.monotonic()
    try:
        data = await client.query(GET_REGISTRIES_QUERY, cache=False)
        elapsed_ms = (_time.monotonic() - start) * 1000
        registries = data.get("registries", [])

        cache_size = len(client._cache)
        active_cache = sum(1 for e in client._cache.values() if not e.is_expired)

        health = {
            "status": "OK",
            "response_time_ms": round(elapsed_ms),
            "registries_found": len(registries),
            "api_url": client.base_url,
            "cache_active": active_cache,
            "cache_total": cache_size,
        }

        lines.append("**Status:** OK")
        lines.append(f"**Response time:** {elapsed_ms:.0f}ms")
        lines.append(f"**Registries found:** {len(registries)}")
        lines.append(f"**API URL:** {client.base_url}")
        lines.append("")

        if elapsed_ms > 5000:
            lines.append(
                "_Warning: Response time > 5s. Multi-step tools may be slow or hit timeouts._"
            )
        elif elapsed_ms > 2000:
            lines.append("_Note: Response time > 2s. Consider this when running batch operations._")

        # Test 2: Check cache status
        lines.append(f"**Cache entries:** {active_cache} active / {cache_size} total")

    except FAIRsharingAuthError as e:
        elapsed_ms = (_time.monotonic() - start) * 1000
        health = {
            "status": "AUTH_FAILURE",
            "response_time_ms": round(elapsed_ms),
            "error": str(e),
        }
        lines.append("**Status:** AUTH FAILURE")
        lines.append(f"**Response time:** {elapsed_ms:.0f}ms")
        lines.append(f"**Error:** {e}")
        lines.append("")
        lines.append(
            "Your API key is invalid or expired. "
            "Get a new key from your FAIRsharing profile page and update FAIRSHARING_API_KEY."
        )

    except FAIRsharingError as e:
        elapsed_ms = (_time.monotonic() - start) * 1000
        health = {
            "status": "ERROR",
            "response_time_ms": round(elapsed_ms),
            "error": str(e),
        }
        lines.append("**Status:** ERROR")
        lines.append(f"**Response time:** {elapsed_ms:.0f}ms")
        lines.append(f"**Error:** {e}")
        lines.append("")
        error_str = str(e).lower()
        if "timeout" in error_str:
            lines.append("The API appears to be slow or unreachable. Try again later.")
        elif "network" in error_str:
            lines.append("Network connectivity issue. Check your internet connection.")
        else:
            lines.append("The API returned an error. It may be temporarily unavailable.")

    if output_format == "json":
        return json.dumps(health, indent=2)

    return "\n".join(lines)


@app.mcp.tool()
async def explain_fairsharing(topic: str = "overview", output_format: str = "markdown") -> str:
    """Get reference documentation about FAIRsharing concepts, registries, and this tool suite.

    No API call is made. Returns static reference material to help understand
    FAIRsharing structure, FAIR indicators, relationship types, and how to use
    the available tools effectively.

    Args:
        topic: Topic to explain. Options:
            - "overview": What FAIRsharing is and its registry types
            - "fair_indicators": The 9 FAIR quality indicator fields for databases
            - "relationships": Edge/association types between records
            - "registries": Detailed description of Database, Standard, Policy, Collection
            - "workflows": Common multi-tool workflows for typical tasks
            - "scoring": How quality scores are computed (DB, Standard, Policy)
        output_format: Output format: "markdown" (default) or "json" for structured data.

    Returns:
        Reference documentation for the requested topic
    """
    topic = topic.strip().lower()

    docs = {
        "overview": (
            "# FAIRsharing Overview\n\n"
            "FAIRsharing (https://fairsharing.org) is a curated, cross-discipline registry\n"
            "of data standards, databases, and data policies. It covers:\n\n"
            "- **Standards** — Terminologies, models/formats, reporting guidelines, identifier schemas\n"
            "- **Databases** — Knowledgebases, repositories, biobanks\n"
            "- **Policies** — Funder, journal, institution, and society data policies\n"
            "- **Collections** — Curated groups of related records\n\n"
            "Records are linked by typed relationships (implements, recommends, collects, etc.)\n"
            "forming a knowledge graph. Each record may have subjects, domains, taxonomies,\n"
            "countries, organisations, publications, and (for databases) 9 FAIR quality indicators.\n\n"
            "## Key IDs\n"
            "- Each record has a numeric ID (e.g., 25) and optionally a DOI (e.g., 10.25504/FAIRsharing.2abjs5)\n"
            "- Use `search_records` to find records by text/filters, `get_record` by ID, `search_by_doi` by DOI\n\n"
            "## Getting Started\n"
            "1. `check_api_health()` — verify your API key works\n"
            "2. `get_statistics()` — see platform-wide counts\n"
            "3. `search_records(query='...', registry=['Database'])` — find specific records\n"
            "4. `get_record(record_id=25)` — get full details for a record\n"
        ),
        "fair_indicators": (
            "# FAIR Quality Indicators (Database Records Only)\n\n"
            "The FAIRsharing API exposes 9 FAIR quality indicators for database records:\n\n"
            "| # | Field | Values | FAIR Aspect |\n"
            "|---|-------|--------|-------------|\n"
            "| 1 | dataAccessCondition | open, partially open, controlled, not found | Accessible |\n"
            "| 2 | dataCuration | manual, automated, manual/automated, none, not found | Reusable |\n"
            "| 3 | dataDepositionCondition | open, controlled, not applicable, not found | Accessible |\n"
            "| 4 | citationToRelatedPublications | yes, no, not found | Findable |\n"
            "| 5 | dataContactInformation | yes, no, not found | Accessible |\n"
            "| 6 | dataVersioning | yes, no, not found | Reusable |\n"
            "| 7 | dataPreservationPolicy | yes/no (boolean) | Reusable |\n"
            "| 8 | resourceSustainability | yes/no (boolean) | Reusable |\n"
            "| 9 | usesPersistentIdentifier | yes/no (boolean) | Findable |\n\n"
            "## Scoring\n"
            "- Best values (open, manual, yes, True) = 1.0 point\n"
            "- Partial values (partially open, automated, controlled) = 0.5 points\n"
            "- Unknown strings not in the known-bad list = 0.5 (imputed, flagged in confidence)\n"
            "- Bad values (none, not found, no, False) = 0 points\n"
            "- Missing (None/null) = not counted\n\n"
            "Score range: 0-9. Grade: Excellent (>=80%), Good (>=60%), Fair (>=40%), Needs Improvement (<40%).\n"
            "Confidence: high (all 9 present, none imputed), medium (<=2 missing), low (>2 missing).\n\n"
            "## Tools\n"
            "- `get_database_quality_profile(record_id)` — single DB profile\n"
            "- `rank_databases_by_quality(subjects=[...])` — ranked list\n"
            "- `assess_database_indicators(data_access='open')` — filter by indicators\n"
            "- `compare_databases_quality(record_ids=[...])` — side-by-side comparison\n"
        ),
        "relationships": (
            "# Relationship Types in FAIRsharing\n\n"
            "Records are connected by typed associations (edges in the knowledge graph):\n\n"
            "| Relationship | Meaning | Typical Direction |\n"
            "|-------------|---------|------------------|\n"
            "| implements | Database uses/implements a Standard | DB -> Standard |\n"
            "| recommends | Policy recommends a Standard or Database | Policy -> Standard/DB |\n"
            "| collects | Collection contains a record | Collection -> any |\n"
            "| related_to | General cross-type relationship | any <-> any |\n"
            "| extends | One standard extends another | Standard -> Standard |\n"
            "| deprecates | Record replaces a deprecated record | new -> deprecated |\n"
            "| profiles | Standard profiles another standard | Standard -> Standard |\n"
            "| outputs | Record produces output used by another | any -> any |\n\n"
            "## Graph Tools\n"
            "- `get_record_graph(record_id)` — structural overview of the local graph\n"
            "- `find_semantic_path(record_id, target_id)` — shortest weighted path\n"
            "- `compute_pagerank(record_id)` — influence ranking in the local graph\n"
            "- `detect_communities(record_id)` — cluster detection\n"
            "- `analyze_graph_comprehensive(record_id)` — combined analysis\n\n"
            "NOTE: All graph tools operate on a single record's local neighborhood.\n"
            "Use `suggest_graph_starting_points` to find records with the largest graphs.\n"
        ),
        "registries": (
            "# FAIRsharing Registries\n\n"
            "## Database\n"
            "Repositories, knowledgebases, and biobanks that store and serve data.\n"
            "These are the only records with FAIR quality indicator fields.\n"
            "Record types: knowledgebase, repository, biobank.\n\n"
            "## Standard\n"
            "Data standards including terminologies/ontologies, models/formats,\n"
            "reporting guidelines, and identifier schemas.\n"
            "Record types: terminology_artefact, model_and_format, reporting_guideline, identifier_schema.\n\n"
            "## Policy\n"
            "Data management policies from funders, journals, institutions, and societies.\n"
            "Contain mandate fields (data sharing, DMP creation, etc.) in a metadata JSON blob.\n"
            "Record types: funder, journal, institution, society, project.\n\n"
            "## Collection\n"
            "Curated groups of related records, often assembled around a theme or project.\n"
        ),
        "workflows": (
            "# Common Multi-Tool Workflows\n\n"
            "Use `suggest_workflow(intent)` for interactive workflow guidance.\n\n"
            "## 1. DMP Compliance Assessment\n"
            "**Preferred:** `assess_dmp_compliance(policy_id, database_ids)` — single call\n"
            "**Manual alternative:**\n"
            "```\n"
            "get_policy_details(record_id=POLICY_ID)\n"
            "  -> find_compliant_standards(policy_ids=[POLICY_ID], database_ids=[DB_IDS])\n"
            "  -> get_database_quality_profile(record_id=DB_ID)\n"
            "  -> check_policy_database_compliance(policy_id=POLICY_ID, database_id=DB_ID)\n"
            "```\n\n"
            "## 2. Standard Ecosystem Analysis\n"
            "```\n"
            "search_records(query='STANDARD_NAME', registry=['Standard'])\n"
            "  -> get_record(record_id=STD_ID)\n"
            "  -> find_databases_for_standard(record_id=STD_ID)\n"
            "  -> analyze_standard_adoption(record_id=STD_ID)\n"
            "  -> compute_maturity_index(subjects=['SUBJECT'])\n"
            "```\n\n"
            "## 3. Database Quality Comparison\n"
            "```\n"
            "rank_databases_by_quality(subjects=['Genomics'])\n"
            "  -> compare_databases_quality(record_ids=[ID1, ID2, ID3])\n"
            "  -> get_database_quality_profile(record_id=BEST_ID)\n"
            "```\n\n"
            "## 4. Policy Landscape Analysis\n"
            "```\n"
            "search_records(registry=['Policy'], countries=['United Kingdom'])\n"
            "  -> compare_policies_by_country(country_a='United Kingdom', country_b='Germany')\n"
            "  -> detect_policy_conflicts(policy_ids=[P1, P2])\n"
            "  -> trace_policy_impact(record_id=P1)\n"
            "```\n\n"
            "## 5. Deprecation Impact\n"
            "**Preferred:** `analyze_transitive_impact(record_id, max_depth=3)` — multi-hop\n"
            "**Quick check:** `analyze_deprecation_impact(record_id)` — single-hop only\n"
            "```\n"
            "find_deprecated_resources(subjects=['Genomics'])\n"
            "  -> analyze_transitive_impact(record_id=DEPRECATED_ID, max_depth=3)\n"
            "```\n\n"
            "## 6. Cross-Registry Quality\n"
            "```\n"
            "compare_unified_quality(record_ids=[DB_ID, STD_ID, POL_ID])\n"
            "  -> get_unified_quality_score(record_id=SPECIFIC_ID)\n"
            "```\n\n"
            "## Tips\n"
            "- Use `output_format='json'` on search_records, get_record, count_records,\n"
            "  get_database_quality_profile, get_statistics, get_unified_quality_score,\n"
            "  assess_dmp_compliance, and analyze_transitive_impact for machine-readable output.\n"
            "- Use `check_api_health()` before batch operations.\n"
            "- Use `suggest_graph_starting_points(query)` before graph analysis.\n"
            "- Use `recommend_tools(query)` to discover tools by keyword.\n"
        ),
        "scoring": (
            "# Quality Scoring Systems\n\n"
            "This server has three independent quality scoring systems:\n\n"
            "## Database FAIR Score (0-9 scale)\n"
            "Based on 9 FAIR indicator fields. See `explain_fairsharing('fair_indicators')` for details.\n"
            "Tool: `get_database_quality_profile`, `rank_databases_by_quality`\n\n"
            "## Standard Quality Profile (0-10 scale)\n"
            "Scores standards on:\n"
            "- Identity (3 pts): homepage, DOI, description presence\n"
            "- Maintenance (2 pts): isMaintained status, not deprecated\n"
            "- Adoption (3 pts): count of implementing databases (normalized)\n"
            "- Policy endorsement (2 pts): count of recommending policies (normalized)\n"
            "Tool: `get_standard_quality_profile`\n\n"
            "## Policy Quality Profile (0-10 scale)\n"
            "Scores policies on:\n"
            "- Mandate coverage (4 pts): data sharing, DMP creation, software sharing, preservation\n"
            "- Coverage breadth (4 pts): data protection, availability statement, licences, citation\n"
            "- Recommendations (2 pts): links to standards and databases\n"
            "Tool: `get_policy_quality_profile`\n\n"
            "## Standards Maturity Index (SMI, 0-100)\n"
            "Composite index combining: adoption (DB count), policy endorsement, stability.\n"
            "Normalized against the sample (not the full registry). Configurable weights.\n"
            "Tool: `compute_maturity_index`\n\n"
            "## Unified Quality Score (0-100 scale)\n"
            "Normalizes any registry's score to a common 0-100 scale for cross-registry comparison.\n"
            "Database: raw/9 * 100, Standard: raw/10 * 100, Policy: raw/10 * 100.\n"
            "Unified grades: A+ (>=90), A (>=80), B (>=65), C (>=50), D (>=35), F (<35).\n"
            "Tools: `get_unified_quality_score`, `compare_unified_quality`\n\n"
            "## Important: Cross-Registry Comparison\n"
            "Database, Standard, and Policy scores measure different aspects of quality.\n"
            "Cross-registry comparisons via unified scoring are approximate — a DB score of\n"
            "75/100 and a Standard score of 75/100 mean different things.\n"
        ),
    }

    if topic not in docs:
        available = ", ".join(sorted(docs.keys()))
        return (
            f"Unknown topic '{topic}'. Available topics: {available}\n\n"
            "Example: `explain_fairsharing('workflows')` or `explain_fairsharing('fair_indicators')`"
        )

    if output_format == "json":
        return json.dumps(
            {"topic": topic, "content": docs[topic]},
            indent=2,
        )

    return docs[topic]
