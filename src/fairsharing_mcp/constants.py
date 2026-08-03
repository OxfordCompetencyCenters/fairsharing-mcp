"""FAIRsharing MCP Server - Constants for validation and documentation.

Scoring weights (RELATIONSHIP_WEIGHTS, RELATIONSHIP_INFLUENCE_WEIGHTS):
- RELATIONSHIP_WEIGHTS: Used for semantic pathfinding (Dijkstra). Lower value =
  stronger/closer relationship. "implements" (1.0) and "recommends" (1.5) are
  treated as strongest; "related_to" (4.0) and "other" (5.0) as weakest. Tuning:
  based on FAIRsharing relationship semantics (direct adoption vs. generic link).
- RELATIONSHIP_INFLUENCE_WEIGHTS: Used for PageRank and community detection.
  Higher value = more influence transferred along that edge. "implements" (1.0)
  and "recommends" (0.8) transfer most influence; "deprecates" (0.2) least.
  These are not empirically calibrated; adjust if ranking behaviour is refined.
"""

RECORD_STATUS_DESCRIPTIONS = {
    "ready": "Fully curated and publicly visible",
    "in_development": "Being actively curated, not yet approved",
    "uncertain": "Resource may no longer be available; under review",
    "deprecated": "Resource is discontinued; record kept for historical reference",
}

RECORD_TYPE_DESCRIPTIONS = {
    # Database registry subtypes
    "repository": "A data repository — stores and provides access to datasets",
    "knowledgebase": "A curated knowledge base with expert-annotated data",
    "biobank": "A biological sample and associated data collection",
    "catalogue": "An index or catalogue of other resources",
    "ontology": "A formal representation of a domain's concepts and relationships",
    "controlled vocabulary": "A standardised set of terms for annotation",
    # Standard registry subtypes
    "model/format": "A data model, schema, or file format",
    "reporting guideline": "Minimum information checklist or reporting standard",
    "identifier schema": "A system for assigning persistent identifiers",
    "terminology artefact": "Terminologies and vocabularies for annotation",
    # Policy registry subtypes
    "journal": "A journal data sharing policy",
    "journal publisher": "A journal publisher data sharing policy",
    "funder": "A funder data management policy",
    "institution": "An institutional data policy",
    "project": "A project-level data policy",
    "society": "A professional society data policy",
}

DATABASE_REGISTRY_SUBTYPES = {
    "repository": "Data repository — stores and provides access to research datasets",
    "knowledgebase": "Expert-curated annotation resource",
    "biobank": "Biological sample collection and associated data",
    "catalogue": "Index or catalogue of other data resources",
    "ontology": "Formal concept hierarchy (also: controlled vocabulary)",
}

POLICY_MANDATE_LEVELS = {"required", "suggested", "not covered", "other"}
POLICY_TYPES = {"journal", "journal_publisher", "funder", "institution", "project", "society"}
DATA_ACCESS_VALUES = {"open", "partially open", "controlled", "not found"}
DATA_CURATION_VALUES = {"manual", "automated", "manual/automated", "none", "not found"}
DATA_DEPOSITION_VALUES = {"open", "controlled", "not applicable", "not found"}

# Policy mandate fields grouped by category
POLICY_MANDATE_FIELDS = [
    "mandatedDataSharing",
    "sharingResearchSoftware",
    "mandatedDmpCreation",
    "metadataSharing",
]

POLICY_BOOLEAN_FIELDS = [
    "exceptionsToDataSharing",
    "dataProtection",
    "dataAvailabilityStatement",
    "licencesForOutputs",
    "dataCitation",
    "dataPreservation",
    "supportedCosts",
    "guidanceToHelpEnableCompliance",
    "monitoringOfCompliance",
    "updatingOfDmp",
]

POLICY_DMP_FIELDS = [
    "timingOfDmp",
    "updatingOfDmp",
]

DATA_ACCESS_CONDITION_VALUES = ["open", "partially open", "controlled", "not found"]
OBJECT_TYPE_VALUES = ["dataset", "image", "model", "publication", "object type not found"]

# Mapping from camelCase API names to metadata snake_case keys (for reference)
FAIR_INDICATOR_METADATA_KEYS = {
    "dataAccessCondition": "data_access_condition",
    "dataCuration": "data_curation",
    "dataDepositionCondition": "data_deposition_condition",
    "citationToRelatedPublications": "citation_to_related_publications",
    "dataContactInformation": "data_contact_information",
    "dataVersioning": "data_versioning",
    "dataPreservationPolicy": "data_preservation_policy",
    "resourceSustainability": "resource_sustainability",
    "usesPersistentIdentifier": "uses_persistent_identifier",
}

DATABASE_FAIR_INDICATOR_FIELDS = [
    "dataAccessCondition",
    "dataCuration",
    "dataDepositionCondition",
    "citationToRelatedPublications",
    "dataContactInformation",
    "dataVersioning",
    "dataPreservationPolicy",
    "resourceSustainability",
    "usesPersistentIdentifier",
]

# The 14 relationship labels the API actually defines, from `recordAssociationLabels`.
# This is the authoritative vocabulary; anything outside it is a derivation artefact.
RECORD_ASSOCIATION_LABELS = [
    "implements",
    "accepts",
    "outputs",
    "related_to",
    "shares_code_with",
    "shares_data_with",
    "profiles",
    "extends",
    "deprecates",
    "collects",
    "recommends",
    "part_of",
    "measures_principle",
    "has_associated_metric",
]

# Edge color → relationship type mapping for the `fairsharingGraph` payload.
#
# PROVENANCE: derived empirically, not guessed. Graph edge colors were joined against
# the authoritative `recordAssocLabel` from `recordAssociations` over ~950 sampled
# records, restricted to record pairs with exactly one association and one graph edge
# so the join is unambiguous. Sample sizes per color are noted below.
#
# Prefer `recordAssocLabel` whenever the calling code has it (see
# graph_utils.build_label_overrides). Color inference is a fallback for graph-only
# nodes, and it CANNOT be exact — see the "brown" note below.
EDGE_COLOR_TO_RELATIONSHIP = {
    "#e6e600": "collects",  # n=797
    "grey": "related_to",  # n=104
    "#7ae827": "has_associated_metric",  # n=72
    "pink": "implements",  # n=50
    "orange": "recommends",  # n=366 (separate sample)
    "green": "profiles",  # n=18
    "black": "extends",  # n=17
    "red": "deprecates",  # n=10
    "brown": "shares_data_with",  # n=10 — AMBIGUOUS, see below
    "indigo": "outputs",  # n=8
    "#e827a4": "measures_principle",  # n=6
    "blue": "accepts",  # n=2 — low sample
    "violet": "outputs",  # never observed in sampling; retained unverified
}

# Colors that carry more than one true label and therefore cannot be resolved by
# color alone. "brown" was observed as shares_data_with (n=10) AND part_of (n=4);
# it is mapped to the more frequent one, so part_of edges are mislabelled whenever
# only the graph payload is available. Callers needing exactness must use
# `recordAssocLabel` via build_label_overrides().
AMBIGUOUS_EDGE_COLORS = {"brown": ["shares_data_with", "part_of"]}

# Labels that no observed color maps to, so they are unreachable from graph data alone.
# "part_of" collides with brown (above); "shares_code_with" appeared once in ~950
# records and never in a joinable position.
COLOR_UNREACHABLE_LABELS = ["part_of", "shares_code_with"]

# Semantic distance weights for Dijkstra path finding (lower = stronger/closer relationship).
# Covers all 14 labels in RECORD_ASSOCIATION_LABELS plus the "other" fallback; a label
# missing from this table silently degrades to the 5.0 worst case, which is what used to
# happen to part_of / measures_principle / has_associated_metric / accepts / shares_code_with.
RELATIONSHIP_WEIGHTS = {
    "implements": 1.0,
    "part_of": 1.2,
    "has_associated_metric": 1.5,
    "measures_principle": 1.5,
    "recommends": 1.5,
    "extends": 1.8,
    "profiles": 2.0,
    "outputs": 2.0,
    "accepts": 2.2,
    "collects": 2.5,
    "shares_code_with": 2.5,
    "shares_data_with": 2.5,
    "deprecates": 3.0,
    "related_to": 4.0,
    "other": 5.0,
}

# Influence transfer weights for PageRank (higher = more influence transferred)
# Unified quality grade thresholds (normalized 0-100 scale)
UNIFIED_GRADE_THRESHOLDS = [
    ("A+", 90),
    ("A", 80),
    ("B", 65),
    ("C", 50),
    ("D", 35),
    ("F", 0),
]

# Comprehensive quality indicator weights per registry type.
# Each dict maps indicator name → max points. Total max is the sum of all values.
# These are heuristic weights, not empirically calibrated.
STANDARD_COMPREHENSIVE_WEIGHTS = {
    "temporal_health": 2.0,  # recency of updates
    "community_engagement": 2.0,  # publications, subject breadth
    "adoption_breadth": 4.0,  # implementers + recommenders (from basic scorer)
    "identity_access": 3.0,  # homepage, DOI, description (from basic scorer)
    "maintenance": 3.0,  # status, isMaintained (from basic scorer)
}  # Max comprehensive: 14.0

POLICY_COMPREHENSIVE_WEIGHTS = {
    "geographic_coverage": 1.5,  # countries covered
    "mandate_specificity": 4.0,  # mandate fields defined (from basic scorer)
    "compliance_infrastructure": 2.0,  # monitoring, guidance, timing
    "recommendation_coverage": 3.0,  # standards + databases recommended (from basic scorer)
    "temporal_health": 1.5,  # recency of updates
}  # Max comprehensive: 12.0

DATABASE_COMPREHENSIVE_WEIGHTS = {
    "fair_indicators": 9.0,  # 9 FAIR indicator fields (from basic scorer)
    "temporal_health": 1.5,  # update recency
    "community_trust": 2.0,  # policies recommending + standards implemented
    "metadata_completeness": 1.5,  # publications, description, DOI, licences
}  # Max comprehensive: 14.0

# Influence transfer weights for PageRank (higher = more influence transferred).
# Covers all 14 labels; a missing label degrades to the 0.2 floor.
RELATIONSHIP_INFLUENCE_WEIGHTS = {
    "implements": 1.0,
    "part_of": 0.8,
    "recommends": 0.8,
    "extends": 0.7,
    "has_associated_metric": 0.7,
    "measures_principle": 0.7,
    "profiles": 0.6,
    "accepts": 0.6,
    "outputs": 0.5,
    "shares_data_with": 0.5,
    "collects": 0.4,
    "shares_code_with": 0.4,
    "related_to": 0.3,
    "deprecates": 0.2,
    "other": 0.2,
}
