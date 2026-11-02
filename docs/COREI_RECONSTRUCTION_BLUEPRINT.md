# COREI OS — Reconstruction Blueprint and Capability Preservation Plan

Date: 3 November 2026. Status: owner-directed scope; implementation design and delivery gates proposed unless separately approved and evidenced.

## 1. Purpose and controlling intent

Build a clean, corrected successor **similar to the original COREI in mission, institutional breadth, operating flow and operator capabilities**. The objective is not a smaller forex application or an imitation of Westernpips. Preserve useful COREI concepts, repair disconnected loops and unsafe boundaries, and prove the rebuilt behavior.

COREI is an Institutional Intelligence Operating System for discovering, understanding and operating the underlying structures, relationships, dependencies, state, intent, risk, control and continuity of finance and technology. Trading is the first implemented domain; security, technology and strategic-continuity extensions remain within the mission, but are not claimed as implemented.

The mandatory lifecycle is:

**Observe → Discover → Understand → Decide → Act → Monitor → Learn → Improve.**

Every domain, workflow and workspace must help an operator establish what is happening, why, what changed, what requires action and the evidence supporting the next decision. A dashboard alone is not this operating system.

This document records the owner's clarification in the reconstruction conversation. It does not approve every proposed technology, migration, performance target or production deployment. It must be read with the findings register and existing ADRs; conflicting older decisions must receive an explicit superseding ADR before their replacement is implemented. The scope correction is not proof that B.3 or later services exist.

## 2. Source authority and provenance

Legacy source pinned for this document: [`coreeyegroup/corei@a08758dc30a04919195ea59bc795a42ff3e1244d`](https://github.com/coreeyegroup/corei/tree/a08758dc30a04919195ea59bc795a42ff3e1244d).

Reconstruction implementation baseline reviewed: `coreeyegroup/corei-os@d360a2f83b1dca47ba06e1c8e7f892e44928cab5`.

| Source | What it establishes |
|---|---|
| [Legacy Anchor Law](https://github.com/coreeyegroup/corei/blob/a08758dc30a04919195ea59bc795a42ff3e1244d/docs/COREI%20Anchor%20Law) | Mission, full lifecycle, institutional scale, operator understanding, state ownership, evolutionary improvement and source documentation |
| [Trading blueprint 3.1](https://github.com/coreeyegroup/corei/blob/a08758dc30a04919195ea59bc795a42ff3e1244d/docs/blueprints/BLUEPRINT_TRADING_PLATFORM_3.1) | Broad trading architecture, screening, strategies, constraints, execution, portfolio state, multi-account operations and governance |
| [Infrastructure blueprint 3.1](https://github.com/coreeyegroup/corei/blob/a08758dc30a04919195ea59bc795a42ff3e1244d/docs/blueprints/BLUEPRINT_INFRASTRUCTURE_3.1) | Reproducible environments, security, streaming, storage, observability, release and recovery capabilities |
| [Legacy ownership tree](https://github.com/coreeyegroup/corei/blob/a08758dc30a04919195ea59bc795a42ff3e1244d/docs/architecture/MASTER_TREE_v2.md) | Intentional homes and ownership boundaries for infrastructure, platform, services, schemas, governance, operations, replay, research and UI |
| `docs/R0-source-disposition.md` and `docs/findings/register.yaml` | Retain/port/rebuild/retire decisions and the original F01–F50 audit register |
| Owner-supplied master phase, phase A–D, stage/governance and continuity documents | Additional requirements to import into traceability; not all have been incorporated into this repository |
| Westernpips consolidated technical research supplied by owner | Comparative requirements for provider roles, account connections, contract mapping, strategy bindings, execution slots and failure investigation |
| Reconstruction conversation and subsequent reviews | Universal scope, model corrections, safety gaps, phased implementation and preservation of the company website |

No legacy PASS file, stage heading, directory name or vendor marketing statement certifies present capability. Westernpips internal architecture and recovery guarantees are not established by the public-source research. External documents not yet present in this repository must be imported with exact provenance rather than silently assumed.

Interpretation order: mission and owner scope → governing requirements → domain/phase responsibilities → reviewed architecture decisions → implementation → measured evidence. Where sources conflict, record a decision and its consequences; do not combine incompatible models silently.

## 3. Preserve capabilities, correct mechanisms

| Legacy capability | Successor requirement | Treatment |
|---|---|---|
| Institutional operating system | Full intelligence-to-action lifecycle, state, lineage, continuity | Preserve mission and breadth |
| Screening and instrument monitoring | Discover instruments/opportunities, monitor state transitions and eligibility | Rebuild with provenance, freshness and measured results |
| Market ingestion/normalization/features | Multi-provider observations, historical retrieval, bars and versioned features | Port only verified parsing logic; repair isolation, reconnects and data-quality gaps |
| Strategy and decision layers | Versioned strategies, hypothesis/evidence records, explainable decisions | Rebuild around deterministic domain logic |
| Constraints and policy | Risk precedence, policy, final sizing, allocation and reservations | Enforce at authoritative boundaries |
| OMS/EMS/routing/broker adapters | Durable order lifecycle and qualified provider capabilities | Replace stubs; handle uncertainty, partial fills and recovery |
| Portfolio and accounting | Independent external accounts, internal books, exact product accounting | Correct the ranked-broker and margin-accounting models |
| Research and replay | Reuse production decision logic against isolated recorded inputs | No broker effects; prove parity |
| Orbit and operational terminals | Information-dense, contextual workspaces for research, trading, risk, infrastructure, governance and executives | Retain useful navigation and interaction concepts; refactor audited code rather than import mocks |
| Workflow automation/n8n | Governed operational workflows, alerts, approvals and incident coordination | Preserve capability; qualify n8n or equivalent integration; never bypass trading controls |
| Security and governance | Identity, roles, credential references, audit, policy versions, approval and release evidence | Introduce before credential-bearing integrations |
| Infrastructure/GitOps/operations | Reproducible deployment, telemetry, drift control, backup, restore and rollback | Repair legacy topology and certify operation through drills |
| Copy/remote/multi-engine operations | Explicit ownership, attribution, sizing, cycle prevention and regional recovery | Add qualified modules after complete paper execution |

Similarity means functional and operational continuity, not copying broken service directories, secrets, dependencies, generated backups or historical certification claims. Preserve useful UI and workflow behavior without presenting fixture state as measured reality.

## 4. Architecture boundaries

### Platform core

Tenant identity and access; versioned contracts; provenance and lineage; explicit state ownership; recorded decision inputs and time; durable commands and event processing; scoped authorization; audit; capability discovery; operational telemetry; configuration lifecycle and recovery mechanisms.

Keep this core small. A future domain must have its own semantics and qualification. Do not implement a generic workflow framework merely to claim universality.

### Trading domain

Provider/account/instrument registries; market observation and quality; screening and feature state; research and qualification; strategy runtime; intent validation including CORE and EXTERNAL provenance; risk/policy/sizing/allocation; OMS/EMS/routing; connectors; ledger/valuation; reconciliation; copying; execution controls and financial reporting.

Financial measures, margin rules, order types and flattening belong to the trading domain, not every future platform domain. Each domain has one authoritative owner per state; databases, Kafka projections and UI views must not become competing owners.

### Deployment profiles

Entities, jurisdictions, providers, account classifications, currencies, instruments, enabled modules, operating calendars, regions, limits, engine assignments and environment configuration. US/UK/AE, EURUSD/USDJPY and a particular MT5 broker are examples, not core restrictions.

New configurations use existing qualified capabilities. New protocols, product types or policy semantics require implementation and qualification; configuration cannot manufacture support.

### Logical phases versus delivery stages

The original Phase 0/A/B/C/D model expresses responsibilities: governance, trading logic, constraints, infrastructure and execution. The reconstruction deliveries A–I express build order. These are different axes; preserve phase boundaries while delivering vertical increments. A service may share a process with another until measurement justifies separation.

## 5. Domain model and financial corrections — B.3

| Concept | Required boundary |
|---|---|
| Tenant / organization | Explicit persistence, authorization and event isolation |
| Legal entity | Contracting party with recorded policy context; unknown status remains unknown |
| Provider / venue | Data, execution, account-reporting and history capabilities, each qualified separately |
| External account | Provider-scoped identity, balances, margin, orders and positions; a funded trading login is one subtype |
| Portfolio / internal book | Allocation, attribution, limits and reporting; no independent copy of external cash |
| Allocation | Controlled capacity from a specific external account with engine authority and lifecycle |
| Connection / session | Endpoint, authentication reference, capabilities, epoch and recovery state |
| Strategy instance | Algorithm/version, parameters, evidence, bindings and runtime state |
| Trade group / slot | Related legs or concurrent trade state; never mistaken for another account |
| Instrument / provider contract | Economic identity plus effective-dated symbol, unit, session and product specifications |
| Accounting/policy version | Named rules and evidence for the applicable product/account combination |

Retain qualified cash-funded accounting and add margin FX/CFD policy alongside it. Future product policies must fail explicitly until implemented. Cash, settled/unsettled funds, reservations, used margin, equity, exposure, realized/unrealized P&L, fees and funding must have distinct definitions and authoritative inputs.

Do not treat a margin fill as a full-notional cash purchase. Do not assume all pair products settle identically. Define provider-specific contract size, tick/volume rules, margin, conversions, rounding, commissions and swaps where applicable.

Tenant keys and permissions must constrain every relevant read/write and consumer. Portfolios may overlap for reporting, but capacity must not be duplicated. Revalue allocations when losses, fees or external actions reduce capacity; specify deficit handling, revocation, expiry and reconciliation.

Migration must preserve historical meaning. Verify whether existing rows represent legitimate cash trades or incorrectly modelled margin trades. No silent relabelling, deletion or reinterpretation of financial history. Existing migrations remain immutable; use new migrations and tested event-version readers. Synthetic fixtures may be reset only through an explicitly scoped migration/test procedure.

Broker statements qualify the corresponding broker profile. Their absence does not block generic contracts, simulator fixtures or B.3 development.

## 6. Full operational loops

| Step | Persisted input/output and completion evidence |
|---|---|
| Observe | Source observations, session/sequence identity, source and receive times, raw-evidence reference; bad/stale data quarantined |
| Discover | Screening results and candidate hypotheses tied to a dataset/version, coverage and limitations |
| Understand | Features, assumptions, causal/diagnostic explanations and research experiments with reproducible inputs |
| Decide | Versioned signal/intent, risk/policy result, final sizing and allocation decision, reasons and state versions |
| Act | Authorized durable request, route/account binding, broker outcomes and residual exposure |
| Monitor | Independent reconciliation, scoped health, freshness, execution quality and incident evidence |
| Learn | Expected-versus-observed comparisons, drift and attribution; findings are proposals, not automatic live changes |
| Improve | New version, qualification, recorded approval/promotion and rollback plan; no retrospective rewriting |

The execution chain is: observations → screening/features → strategy signal → account-specific intent → risk → policy → final sizing validation → allocation/reservation and fence → OMS/EMS → authorized engine/connector → broker outcomes → ledger → reconciliation → control/alerts. Policy cannot enlarge a risk-approved exposure without renewed validation.

Every edge requires contract/version, identity, units, time semantics, owner, durable state, deduplication, ordering, timeout, rejection, recovery and evidence. This boundary matrix is the mechanism for closing legacy loop gaps.

## 7. Safety on all execution paths

- Exact financial arithmetic across the full supported range; reject unsupported scales and overflow.
- Tenant, account, portfolio, strategy/version, origin, capital partition and execution mode survive every applicable boundary. Persist request/order/deal/position mappings.
- Record an external-effect request durably before sending. A timeout remains SUBMISSION_UNKNOWN until reconciliation resolves it; no blind retry or cross-broker failover.
- Durable idempotency compares immutable content. Event identity includes the semantic namespace, and supplied IDs are validated according to the identity policy. A checksum is not authentication.
- Control commands and reservations are concurrency-safe, including first scope creation. Authorize at reservation and final dispatch; explicitly define the linearization boundary and already-submitted-order behavior.
- Halt, cancellation and flattening are separate operations. Risk reduction must be verified against actual positions and pending exposure; it cannot depend solely on a caller flag.
- Expiry alone cannot release capacity for an unresolved submitted order. Partial fills and unknown outcomes retain appropriate exposure accounting.
- Order reducers handle fills before separate acknowledgements, duplicate/out-of-order reports, late fills and conflicting executions.
- Valuation requires admissible, timestamped marks and FX rates. Future/stale/missing inputs withhold complete valuation. Snapshots have consistent state/version cutoffs.
- Multi-leg orders are not cross-broker atomic. Persist trade-group state, identify imbalance and apply a bounded, qualified recovery policy.
- Halts and financial/reconciliation breaks survive restarts. Resume only after verified recovery of reservations, requests, allocations, slots, legs, controls and authority.
- Separate simulation, replay, paper, broker demo and live in data and effect boundaries. Live activation remains separately authorized.

## 8. Engines and external integrations

COREI standard, performance-sensitive and external engines are distinct implementations behind declared capabilities. WP7 is one possible external engine, not the platform architecture.

Classify control as enforced, cooperative or observation-only. COREI cannot prevent new orders from an observation-only engine, infer that it is paused from silence, or guarantee its compliance with allocations. Dedicated external accounts are a proposed isolation default, not a hardcoded schema limit. Shared management needs an explicit recorded ownership procedure and acknowledged limitations.

Attribute externally observed activity as COREI, a positively identified external engine, manual or unknown. Unknown financial effects remain recorded and trigger reconciliation and the appropriate scoped control response. Tags help correlation; netting accounts still aggregate broker positions.

The proposed performance path uses an exclusive versioned capacity allocation, expiring authority, local dispatch checks and a durable pre-send journal. Kafka publication can be asynchronous. Define control propagation, lease/revocation races, clock assumptions, journal/disk/host failure, bounded offline operation and recovery before qualification. Central checks must not pretend to enforce fresh aggregate risk against invisible external actions during a partition.

Select FIX/API, MT4/MQL4, MT5/MQL5, cTrader, exchange and read-only connectors according to actual capabilities and qualification. Data-only protocols never acquire order-entry capability by implication. Preserve support for Indian-market and other qualified adapters identified in legacy source disposition; do not narrow the provider catalogue to forex.

## 9. Operator experience and automation

Reconstruct the useful Orbit operating model: contextual navigation, multiple workspaces, live state, evidence, investigation and authorized operations. The initial frontend should be selected explicitly; multiple old UI generations are not simultaneous authoritative designs.

| Workspace / terminal | Required operational purpose |
|---|---|
| Market intelligence | Coverage, screening, sessions, feed quality, structure, features and evidence |
| Research | Hypotheses, datasets, experiments, replay, qualification and strategy comparisons |
| Trading / execution | Account/strategy bindings, orders, fills, residual exposure and execution quality |
| Risk / portfolio | Cash, margin, P&L, allocations, limits, concentration, controls and reconciliation |
| Infrastructure | Services, dependencies, storage, connectivity, deployments, logs and incident state |
| Governance / compliance | Access, approvals, policy/configuration versions, audit and evidence |
| Executive / continuity | Aggregated operational state, material risks, continuity and decisions |

Screens must show measured/derived/simulated/unavailable status, source, freshness, scope and qualifications. Unsupported actions are gated; fake healthy state and fixture P&L are prohibited. Information density must remain readable, accessible and responsive. The API documentation currently exposed by corei-os is not the completed operator terminal.

Workflow automation may coordinate research jobs, notifications, approvals, housekeeping and incidents. Whether implemented through n8n or another qualified adapter, trading effects must pass the same authoritative command/risk/control boundaries. Workflow retries must not duplicate financial effects.

## 10. Infrastructure capability preservation

Preserve the original operational capabilities, without automatically reinstalling its entire tool list:

| Capability | Legacy examples | Reconstruction obligation |
|---|---|---|
| Streaming and schema governance | Kafka, schema registry, Kafka UI/connectors | Ordered ownership streams, bounded backlog, evolution and retention/replay evidence |
| Transactional/time-series/cache storage | PostgreSQL, TimescaleDB, Redis | Declared authority, integrity, freshness, retention, restore; caches remain derived |
| Identity, secrets and network security | Keycloak, Vault, Istio, cert-manager, Falco | Least privilege, credentials by reference, authenticated boundaries, network and runtime controls |
| Observability | Prometheus, Grafana, Loki/Promtail, Jaeger/OpenTelemetry, Alertmanager | Metrics/logs/traces linked to scope and lineage; measured alert delivery |
| Release and artifact governance | GitHub Actions, Harbor, Trivy, ArgoCD, Helm | Source-revision gates, immutable artifacts, scanning, promotion and tested rollback |
| Compute, networking and storage | Docker/containerd, Kubernetes, Calico, MetalLB, Longhorn, Terraform, Velero | Reproducibility, isolation, capacity, recovery and qualified scaling |
| Operational automation | n8n and governed scripts | Audited, bounded, idempotent operational workflows |

These are capability references, not blanket approvals of versions, licences, topology or implementation claims. Ubuntu packages/systemd are a current delivery mechanism, not a complete replacement for production HA, backup, release control and continuity design. Preserve environment contract parity even where deployment mechanisms differ. Use DNS/configuration rather than embedding a particular VPS IP in domain logic.

The retained `/home/coreeye/core-eye-group-web` and its services are separate from COREI runtime. Future deployment changes must preserve that website. Removing legacy tools did not remove their required operational capabilities from the reconstruction scope.

## 11. Delivery and acceptance

| Delivery | Required outcome |
|---|---|
| B.3 | Correct tenants/providers/external accounts/portfolios/allocations, product accounting, profile handling and safe migration; identity, precision and concurrency blockers corrected |
| C | Trustworthy observation, recording, screening inputs, historical/replay foundations, one qualified data connector, credentials/auth/audit/basic observability |
| D | Complete paper strategy-to-ledger/control loop; operator views for that loop; measured performance-path prototype only after standard correctness |
| E | Expanded adversarial qualification, replay parity, cost sensitivity, failure/recovery and evidence gates |
| F | Further qualified providers, live-data paper operation, external-engine observation and broker-demo qualification |
| G | Production deployment, capacity/HA, security hardening, recovery objectives/drills, complete operational terminals and release governance |
| H | Separately authorized limited live activation per qualified account/product/strategy; no implicit live approval |
| I | Copying, additional strategy/product/domain modules and further regional scale |

Discovery/research artifacts start in C, strategy qualification in D/E, monitoring and expected-versus-observed learning in D/F, and controlled improvement at every version promotion. They are deliverables with evidence, not unspecified work deferred beyond execution.

Fault tests begin in the stage introducing a boundary. E expands them; it does not postpone basic safety. Restore is tested when persistence is introduced. A small acceptance fixture is not an architecture limit.

First full paper acceptance: two instruments, two independent external accounts and at least one strategy, through real production domain logic, with risk rejection, duplicate events, conflicting duplicate content, partial fills, fill-before-ACK, unknown submission, restart, scoped durable halts, valuation freshness, reconciliation and origin attribution. Include concurrency, tenant rejection and overlapping allocation tests. Add multi-leg imbalance scenarios when paired execution is introduced.

Performance criteria, jurisdictions and provider facts block only their affected qualification. Generic contracts, simulator tests and model corrections can proceed without fabricated live credentials or regulatory data.

## 12. Evidence and change governance

Maintain a requirement → source → design decision → state owner → implementation → acceptance test → source revision/result matrix. Every retained legacy capability needs a disposition: preserved, ported, rebuilt, replaced by a qualified equivalent, deferred with reason, or retired by explicit decision. Do not silently shrink scope.

Distinguish planned, implemented, locally tested, integration-tested, deployment-tested and production-qualified. Tests that only validate directory presence or mark open findings xfailed do not certify capabilities. Record skipped infrastructure tests and unverified vendor behavior explicitly.

Before widening the build: inspect the remaining owner-supplied phase/stage documents, complete the requirements matrix and identify contradictions. Before changing accepted mechanics: record the alternative, improvement, affected invariants, migration, qualification and rollback. Routine work within an authorized scope should continue without repeatedly requesting the same approval; genuinely unresolved choices must identify exactly what they block.

Every production source file must explain its purpose, responsibilities, prohibited behavior, dependencies, consumers, architectural position and lifecycle where applicable. Preserve operator understanding, state ownership, auditability, continuity and maintainability through every change.

## 13. Current implementation boundary

At baseline d360a2f, corei-os contains contracts, pure rules, reference stores, a cash-notional ledger/reservations, control API, inbox/outbox/Kafka transport and Ubuntu packaging. B.3 and the complete trading/research/terminal loops are not implemented. Existing audit findings remain open unless closed by revision-specific behavioral evidence.

This blueprint restores the intended scope. Publishing it does not certify implementation, resolve financial defects or authorize real-money trading. The immediate next deliverable is a concrete B.3 schema/contract diff, migration plan and acceptance matrix consistent with this document.
