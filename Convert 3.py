catalog:
  name: ontology-studio
  version: "1.0.0"
  description: Central catalog of all ontologies

ontologies:
  - id: healthcare-insurance
    name: Healthcare Insurance Ontology
    domain: healthcare
    path: domains/healthcare/healthcare-insurance
    version: "1.0.0"
    status: active
    owner: healthcare-team
    dependencies:
      - shared-core
      - common-relationships

  - id: provider-ontology
    name: Provider Ontology
    domain: healthcare
    path: domains/healthcare/provider-ontology
    version: "1.0.0"
    status: draft
    owner: healthcare-team




catalog:
  name: domain-catalog
  version: "1.0.0"

domains:
  - id: healthcare
    name: Healthcare
    description: Healthcare-related ontologies
    path: domains/healthcare
    status: active

    ontologies:
      - healthcare-insurance
      - provider-ontology

  - id: finance
    name: Finance
    description: Financial services and transactions
    path: domains/finance
    status: active

    ontologies:
      - payments
      - risk
      - fraud

  - id: customer
    name: Customer
    description: Customer and interaction models
    path: domains/customer
    status: active

  - id: supply-chain
    name: Supply Chain
    description: Supply chain domain
    path: domains/supply-chain
    status: planned


catalog:
  name: entity-catalog
  version: "1.0.0"

entities:

  - id: person
    name: Person
    type: core
    namespace: core
    path: shared/core/person

  - id: organization
    name: Organization
    type: core
    namespace: core
    path: shared/core/organization

  - id: location
    name: Location
    type: core
    namespace: core
    path: shared/core/location

  - id: product
    name: Product
    type: core
    namespace: core
    path: shared/core/product

  - id: event
    name: Event
    type: core
    namespace: core
    path: shared/core/event

  - id: member
    name: Member
    type: domain
    domain: healthcare
    ontology: healthcare-insurance
    path: domains/healthcare/healthcare-insurance/entities/member.yaml

  - id: policy
    name: Policy
    type: domain
    domain: healthcare
    ontology: healthcare-insurance
    path: domains/healthcare/healthcare-insurance/entities/policy.yaml

  - id: claim
    name: Claim
    type: domain
    domain: healthcare
    ontology: healthcare-insurance
    path: domains/healthcare/healthcare-insurance/entities/claim.yaml

  - id: provider
    name: Provider
    type: domain
    domain: healthcare
    ontology: healthcare-insurance
    path: domains/healthcare/healthcare-insurance/entities/provider.yaml

  - id: coverage
    name: Coverage
    type: domain
    domain: healthcare
    ontology: healthcare-insurance
    path: domains/healthcare/healthcare-insurance/entities/coverage.yaml




catalog:
  name: relationship-catalog
  version: "1.0.0"

relationships:

  - id: owns
    name: Owns
    type: common
    path: shared/common-relationships/owns.yaml

  - id: belongs-to
    name: Belongs To
    type: common
    path: shared/common-relationships/belongs-to.yaml

  - id: located-at
    name: Located At
    type: common
    path: shared/common-relationships/located-at.yaml

  - id: related-to
    name: Related To
    type: common
    path: shared/common-relationships/related-to.yaml

  - id: member-policy
    name: Member Policy
    type: domain
    domain: healthcare
    ontology: healthcare-insurance
    path: domains/healthcare/healthcare-insurance/relationships/member-policy.yaml

  - id: policy-claim
    name: Policy Claim
    type: domain
    domain: healthcare
    ontology: healthcare-insurance
    path: domains/healthcare/healthcare-insurance/relationships/policy-claim.yaml

  - id: provider-claim
    name: Provider Claim
    type: domain
    ontology: healthcare
    path: domains/healthcare/healthcare-insurance/relationships/provider-claim.yaml
