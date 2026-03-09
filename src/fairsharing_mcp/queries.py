"""FAIRsharing MCP Server - GraphQL query constants."""

SEARCH_RECORDS_QUERY = """
query SearchRecords(
    $q: String, $searchAnd: Boolean,
    $registry: [String!], $recordType: [String!], $status: [String!],
    $subjects: [String!], $domains: [String!], $taxonomies: [String!],
    $countries: [String!], $organisations: [String!], $userDefinedTags: [String!],
    $licences: [String!], $journals: [String!],
    $isRecommended: Boolean, $isApproved: Boolean, $isMaintained: Boolean,
    $hasPublication: Boolean, $isImplemented: Boolean,
    $page: Int, $perPage: Int
) {
    searchFairsharingRecords(
        q: $q, searchAnd: $searchAnd,
        fairsharingRegistry: $registry, recordType: $recordType, status: $status,
        subjects: $subjects, domains: $domains, taxonomies: $taxonomies,
        countries: $countries, organisations: $organisations, userDefinedTags: $userDefinedTags,
        licences: $licences, journals: $journals,
        isRecommended: $isRecommended, isApproved: $isApproved, isMaintained: $isMaintained,
        hasPublication: $hasPublication, isImplemented: $isImplemented,
        page: $page, perPage: $perPage
    ) {
        records {
            id
            name
            abbreviation
            description
            registry
            type
            status
            doi
            subjects { label }
            domains { label }
            createdAt
        }
        totalCount
        totalPages
    }
}
"""

GET_RECORD_QUERY = """
query GetRecord($id: ID!) {
    fairsharingRecord(id: $id) {
        id
        name
        abbreviation
        description
        doi
        homepage
        status
        registry
        type
        createdAt
        updatedAt
        isApproved
        isMaintained
        isRecommended
        subjects { id label iri }
        domains { id label iri }
        taxonomies { id label iri }
        countries { id name code }
        organisations { id name homepage }
        publications { id title doi year journal }
        licenceLinks { licence { id name url } relation }
        userDefinedTags { id label }
        recordAssociations {
            linkedRecord { id name registry type }
            recordAssocLabel
        }
        reverseRecordAssociations {
            fairsharingRecord { id name registry type }
            recordAssocLabel
        }
    }
}
"""

GET_GRAPH_QUERY = """
query GetGraph($id: Int!) {
    fairsharingGraph(id: $id) {
        data
    }
}
"""

LIST_SUBJECTS_QUERY = """
query ListSubjects($page: Int, $perPage: Int) {
    subjects(page: $page, perPage: $perPage) {
        records { id label iri }
        totalCount
        totalPages
    }
}
"""

SEARCH_SUBJECTS_QUERY = """
query SearchSubjects($q: String!) {
    searchSubjects(q: $q) {
        id label iri
    }
}
"""

GET_SUBJECT_QUERY = """
query GetSubject($id: Int!) {
    subject(id: $id) {
        id
        label
        iri
        description
        parents { id label }
        children { id label }
        ancestors { id label }
    }
}
"""

LIST_DOMAINS_QUERY = """
query ListDomains($page: Int, $perPage: Int) {
    domains(page: $page, perPage: $perPage) {
        records { id label iri }
        totalCount
        totalPages
    }
}
"""

SEARCH_DOMAINS_QUERY = """
query SearchDomains($q: String!) {
    searchDomains(q: $q) {
        id label iri
    }
}
"""

GET_DOMAIN_QUERY = """
query GetDomain($id: Int!) {
    domain(id: $id) {
        id
        label
        iri
        description
        parents { id label }
        children { id label }
        ancestors { id label }
    }
}
"""

LIST_TAXONOMIES_QUERY = """
query ListTaxonomies($page: Int, $perPage: Int) {
    taxonomies(page: $page, perPage: $perPage) {
        records { id label iri }
        totalCount
        totalPages
    }
}
"""

SEARCH_TAXONOMIES_QUERY = """
query SearchTaxonomies($q: String!) {
    searchTaxonomies(q: $q) {
        id label iri
    }
}
"""

LIST_ORGANISATIONS_QUERY = """
query ListOrganisations($page: Int, $perPage: Int) {
    organisations(page: $page, perPage: $perPage) {
        records { id name homepage countries { name } }
        totalCount
        totalPages
    }
}
"""

SEARCH_ORGANISATIONS_QUERY = """
query SearchOrganisations($q: String!) {
    searchOrganisations(q: $q) {
        id name homepage countries { name }
    }
}
"""

LIST_COUNTRIES_QUERY = """
query ListCountries($page: Int, $perPage: Int) {
    countries(page: $page, perPage: $perPage) {
        records { id name code }
        totalCount
        totalPages
    }
}
"""

LIST_LICENCES_QUERY = """
query ListLicences($page: Int, $perPage: Int) {
    licences(page: $page, perPage: $perPage) {
        records { id name url }
        totalCount
        totalPages
    }
}
"""

GET_REGISTRIES_QUERY = """
query GetRegistries {
    fairsharingRegistries {
        records {
            id
            name
            description
        }
        totalCount
    }
}
"""

GET_RECORD_TYPES_QUERY = """
query GetRecordTypes {
    recordTypes(perPage: 100) {
        records {
            id
            name
            description
            fairsharingRegistry { name }
        }
        totalCount
    }
}
"""

# Note: fairsharingStats doesn't exist on this API version. We use latestStats instead.

GET_LATEST_STATS_QUERY = """
query GetLatestStats {
    latestStats {
        id
        createdAt
        data
    }
}
"""

BROWSE_SUBJECTS_QUERY = """
query BrowseSubjects {
    browseSubjects {
        data
    }
}
"""

# --- Analytical queries ---

GET_RECORD_WITH_ASSOCIATIONS_QUERY = """
query GetRecordAssociations($id: ID!) {
    fairsharingRecord(id: $id) {
        id name abbreviation description registry type status
        doi homepage
        createdAt updatedAt
        subjects { id label }
        domains { id label }
        taxonomies { id label }
        organisations { id name }
        publications { id title }
        licenceLinks { licence { id name } }
        recordAssociations {
            linkedRecord { id name abbreviation registry type status doi }
            recordAssocLabel
        }
        reverseRecordAssociations {
            fairsharingRecord { id name abbreviation registry type status doi }
            recordAssocLabel
        }
    }
}
"""

GET_RELATIONSHIP_LABELS_QUERY = """
query { recordAssociationLabels { id name } }
"""

SEARCH_PUBLICATIONS_QUERY = """
query SearchPublications($q: String!) {
    searchPublications(q: $q) {
        id title doi year journal
    }
}
"""

SEARCH_RECORDS_COMPACT_QUERY = """
query SearchCompact(
    $q: String, $searchAnd: Boolean,
    $registry: [String!], $recordType: [String!], $status: [String!],
    $subjects: [String!], $domains: [String!], $taxonomies: [String!],
    $countries: [String!], $organisations: [String!],
    $isRecommended: Boolean, $isMaintained: Boolean,
    $hasPublication: Boolean, $isImplemented: Boolean,
    $page: Int, $perPage: Int
) {
    searchFairsharingRecords(
        q: $q, searchAnd: $searchAnd,
        fairsharingRegistry: $registry, recordType: $recordType, status: $status,
        subjects: $subjects, domains: $domains, taxonomies: $taxonomies,
        countries: $countries, organisations: $organisations,
        isRecommended: $isRecommended, isMaintained: $isMaintained,
        hasPublication: $hasPublication, isImplemented: $isImplemented,
        page: $page, perPage: $perPage
    ) {
        records { id name abbreviation registry type status createdAt }
        totalCount totalPages
    }
}
"""

MULTI_TAG_FILTER_QUERY = """
query MultiTagFilter(
    $q: String, $registry: [String!], $status: [String!], $recordType: [String!],
    $subjects: [String!], $domains: [String!], $taxonomies: [String!],
    $userDefinedTags: [String!],
    $isRecommended: Boolean, $isApproved: Boolean, $isMaintained: Boolean,
    $hasPublication: Boolean, $isImplemented: Boolean,
    $usesPersistentIdentifier: Boolean,
    $dataPreservationPolicy: Boolean, $resourceSustainability: Boolean,
    $dataAccessCondition: [String!], $dataCuration: [String!],
    $dataDepositionCondition: [String!],
    $citationToRelatedPublications: [String!],
    $dataContactInformation: [String!], $dataVersioning: [String!],
    $recommendsDatabase: Boolean, $recommendsStandard: Boolean,
    $load: Boolean
) {
    multiTagFilter(
        q: $q, fairsharingRegistry: $registry, status: $status, recordType: $recordType,
        subjects: $subjects, domains: $domains, taxonomies: $taxonomies,
        userDefinedTags: $userDefinedTags,
        isRecommended: $isRecommended, isApproved: $isApproved, isMaintained: $isMaintained,
        hasPublication: $hasPublication, isImplemented: $isImplemented,
        usesPersistentIdentifier: $usesPersistentIdentifier,
        dataPreservationPolicy: $dataPreservationPolicy,
        resourceSustainability: $resourceSustainability,
        dataAccessCondition: $dataAccessCondition, dataCuration: $dataCuration,
        dataDepositionCondition: $dataDepositionCondition,
        citationToRelatedPublications: $citationToRelatedPublications,
        dataContactInformation: $dataContactInformation,
        dataVersioning: $dataVersioning,
        recommendsDatabase: $recommendsDatabase, recommendsStandard: $recommendsStandard,
        load: $load
    ) {
        id name abbreviation registry type status doi createdAt
        subjects { label }
        domains { label }
    }
}
"""

GET_POLICY_DETAIL_QUERY = """
query GetPolicyDetail($id: ID!) {
    fairsharingRecord(id: $id) {
        id
        name
        abbreviation
        description
        doi
        homepage
        status
        registry
        type
        createdAt
        updatedAt
        isApproved
        isMaintained
        isRecommended
        metadata
        subjects { id label }
        domains { id label }
        taxonomies { id label }
        countries { id name code }
        organisations { id name homepage }
        publications { id title doi year journal }
        licenceLinks { licence { id name url } relation }
        userDefinedTags { id label }
        recordAssociations {
            linkedRecord { id name abbreviation registry type status doi }
            recordAssocLabel
        }
        reverseRecordAssociations {
            fairsharingRecord { id name abbreviation registry type status doi }
            recordAssocLabel
        }
    }
}
"""

GET_DATABASE_QUALITY_QUERY = """
query GetDatabaseQuality($id: ID!) {
    fairsharingRecord(id: $id) {
        id
        name
        abbreviation
        description
        doi
        homepage
        status
        registry
        type
        createdAt
        updatedAt
        subjects { id label }
        domains { id label }
        countries { id name code }
        organisations { id name }
        dataAccessCondition
        dataCuration
        dataDepositionCondition
        citationToRelatedPublications
        dataContactInformation
        dataVersioning
        dataPreservationPolicy
        resourceSustainability
        usesPersistentIdentifier
    }
}
"""
