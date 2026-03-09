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

# Edge color → relationship type mapping from FAIRsharing graph data
EDGE_COLOR_TO_RELATIONSHIP = {
    "pink": "implements",
    "grey": "related_to",
    "#e6e600": "collects",
    "orange": "recommends",
    "green": "extends",
    "red": "deprecates",
    "black": "related_to",
    "blue": "shares_data_with",
    "brown": "other",
    "violet": "outputs",
    "indigo": "profiles",
}

# Semantic distance weights for Dijkstra path finding (lower = stronger/closer relationship)
RELATIONSHIP_WEIGHTS = {
    "implements": 1.0,
    "recommends": 1.5,
    "extends": 1.8,
    "profiles": 2.0,
    "outputs": 2.0,
    "collects": 2.5,
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

RELATIONSHIP_INFLUENCE_WEIGHTS = {
    "implements": 1.0,
    "recommends": 0.8,
    "extends": 0.7,
    "profiles": 0.6,
    "outputs": 0.5,
    "shares_data_with": 0.5,
    "collects": 0.4,
    "related_to": 0.3,
    "deprecates": 0.2,
    "other": 0.2,
}
