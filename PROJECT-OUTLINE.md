# Skill Broker — Project Outline

## Status

Project-definition outline for conversion into a formal specification and implementation tickets.

This project is intended to become the first production deterministic workflow derived from the experiments in `~/Projects/agent-workflow-lab`. The lab proved useful patterns with Jev, Pydantic, and pydantic-graph; Skill Broker will apply those patterns to a real Hermes workflow without treating those libraries as permanent constraints.

## Working name

**Skill Broker**

Alternative descriptive phrase: **skill-intervention layer**.

The term “broker” means the programme selects and supplies authorised skills to an agent. It does not delegate the agent’s task or replace the agent.

## Problem

Hermes currently discovers skills from several locations and renders the names and descriptions of enabled skills into agent context. The live library is large, has duplicate names and mixed ownership, and contains both frequently needed foundation skills and specialised skills that are rarely relevant.

This causes several related problems:

- The skills index consumes context on every turn even when most skills are irrelevant.
- Agents must notice that a skill is needed and deliberately load it before acting.
- Profiles do not yet have an enforceable, explicit policy describing which specialised skills they may receive.
- The library topology is difficult to reason about: skills have several owners, sources, update paths, and resolution priorities.
- A routing system built directly on the current topology would encode existing duplication and ambiguity.

## Product proposition

**Intervene with the right authorised guidance before the agent acts.**

Skill Broker evaluates an incoming request before the main agent’s first model call, selects a small set of relevant skills from that profile’s authorised catalog, validates the selection deterministically, and supplies the selected skill content to the current agent turn.

Hermes retains a small native foundation skill set. The larger specialised library is removed from automatic prompt discovery only after the brokered path has been proven. Skills are migrated incrementally, with no destructive cutover.

## Goals

1. Reduce the always-present skill-index context without weakening Hermes’s foundation capabilities.
2. Feed relevant skills deliberately before an agent begins solving the request.
3. Give every profile an explicit skill policy.
4. Use Jev for narrow semantic judgment while keeping authority, limits, and routing decisions in deterministic code.
5. Preserve one canonical home for each skill and represent every other appearance as a link or catalog reference.
6. Produce replayable, auditable evidence for every routing decision.
7. Introduce the broker without breaking existing Hermes skill discovery or resolution.
8. Measure routing quality, context savings, latency, and model-call cost before cutover.

## Non-goals

- Replacing Hermes’s agent loop.
- Replacing all native Hermes skills.
- Allowing Jev or another model to grant permissions.
- Treating hidden skill names as a security boundary.
- Copying every skill into a new monolithic directory.
- Automatically deleting stale, duplicated, or unused skills.
- Building a general distributed workflow engine.
- Making model output deterministic.
- Changing Hermes’s system prompt or tool schema during an active conversation.

## Design principles

### Foundation remains native

A deliberately small set of foundation skills remains registered with Hermes and available through normal discovery and `skill_view`. Likely categories include:

- Hermes operation, configuration, troubleshooting, and extension
- Skill management and recovery
- Life OS
- Essential safety or system-recovery capabilities
- Genuinely frequent profile-specific capabilities
- Several skills that have been self-authored by hermes during the course of operation.

The exact foundation set is an audit output, not assumed in advance.
Remaining skills must include what is required by Hermes under normal run conditions. 

### Specialised skills are brokered

Specialised skills remain in their canonical homes but are omitted from the automatic Hermes index for brokered profiles. The broker catalog can resolve them without copying them.

### Jev judges relevance; code controls authority

Jev receives a narrow, typed decision over a small authorised candidate set. Deterministic code:

- Builds and filters the candidate set
- Enforces the profile policy
- Rejects unknown or unauthorised skill identifiers
- Applies confidence thresholds
- Applies skill-count and content-size budgets
- Resolves required dependencies
- Detects cycles or missing dependencies
- Determines fallback and failure behaviour
- Records the final decision

### Progressive, reversible migration

The existing Hermes skill system remains operational while the audit, catalog, router, and intervention path are introduced. No current source is removed until both native and brokered resolution have been verified.

### Prompt caching remains intact

The broker must not mutate the system prompt or tool list per turn. Selected skill content is supplied as current-turn context before the main agent call, using a cache-safe Hermes extension seam.

### One skill, one home

Each skill has one canonical source. Catalog entries, shelf entries, profile exposure, and compatibility locations point to that source rather than becoming independent copies.

## Domain model

### Skill

A reusable instruction package with a stable identifier, canonical source, content hash, metadata, and zero or more dependencies.

### Canonical Source

The one authoritative location from which a skill is maintained and updated.

### Catalog

A read-only logical inventory of resolvable skills across canonical sources. The catalog stores metadata and references; it is not another skill library copy.

### Foundation Skill

A skill deliberately exposed through Hermes’s native skill system for a profile because it is essential or frequently required.

### Brokered Skill

A skill omitted from the profile’s automatic index but eligible to be supplied by Skill Broker.

### Profile Policy

The deterministic policy defining the skills and skill groups a profile may receive, its preferred skills, explicit exclusions, routing thresholds, and per-turn limits.

### Candidate

An authorised skill selected by deterministic retrieval as potentially relevant to the current request. Candidates are inputs to Jev’s judgment, not grants.

### Judgment

A typed semantic result from Jev or an interchangeable recorded/stub source. It expresses relevance and confidence but grants no authority.

### Grant

The deterministic result authorising one resolved skill version for delivery to one agent turn.

### Skill Pack

The bounded collection of granted skill contents and dependency contents supplied to the agent for the current request.

### Intervention

The act of supplying a validated Skill Pack before the main agent begins working on the request.

### Session Ledger

A record of skill hashes already supplied during a conversation, used to prevent accidental duplicate injection and support auditability.

### Route Decision

The complete recorded outcome: request identity, profile, candidates, judgment, policy result, selected skills, content hashes, limits, reasons, timing, and model usage.

## Proposed system shape

```text
Incoming request
      |
      v
Typed intake and validation
      |
      v
Profile policy lookup
      |
      v
Deterministic candidate retrieval
      |
      v
Typed Jev judgment (only when needed)
      |
      v
Deterministic policy enforcement
      |
      v
Dependency and budget resolution
      |
      v
Skill Pack construction
      |
      v
Current-turn intervention
      |
      v
Normal Hermes agent execution
      |
      v
Append-only routing evidence
```

## Principal modules and seams

The preferred external interface is one deep seam:

```text
prepare_turn(request, profile, session_context) -> InterventionResult
```

Callers should not need to understand catalog layout, retrieval, Jev prompts, dependencies, thresholds, or content assembly.

Internally, the implementation will likely contain these modules:

1. **Catalog** — inventories canonical skills and resolves stable IDs to exact content hashes.
2. **Audit** — measures sources, duplicates, ownership, dependencies, usage, update paths, and live resolution.
3. **Policy** — parses and enforces per-profile skill policy.
4. **Candidate Retrieval** — creates a small authorised shortlist using deterministic metadata and request signals.
5. **Judgment Source** — interchangeable live Jev, recorded replay, and deterministic stub adapters.
6. **Router** — combines the validated judgment with deterministic thresholds and policy.
7. **Dependency Resolver** — expands required skills, rejects missing/cyclic dependencies, and respects limits.
8. **Pack Builder** — loads exact skill versions and creates bounded intervention content.
9. **Session Ledger** — records supplied hashes and avoids unnecessary reinjection.
10. **Evidence Log** — append-only record of route decisions, usage, timing, and outcomes.
11. **Hermes Adapter** — invokes `prepare_turn` at the cache-safe pre-agent seam and injects the resulting pack.

Internal seams should remain replaceable where there are already demonstrated alternatives: judgment source and Hermes adapter. Avoid speculative adapters elsewhere.

## Profile policy concept

A profile policy should be explicit and machine-validated. It will likely express:

- Foundation skill IDs
- Allowed brokered skill IDs or catalog groups
- Preferred skills with a lower relevance threshold
- Explicitly denied skills
- Maximum skills per intervention
- Maximum injected characters or tokens
- Confidence thresholds
- Allowed dependency expansion
- Platform, tool, or project constraints where genuinely required
- Behaviour when routing is uncertain or unavailable

“Preferred” must not mean “always inject.” Foundation exposure and broker preference are separate concepts.

## Candidate retrieval and Jev boundary

Jev must not inspect the entire library on every prompt.

Candidate retrieval first narrows the profile-authorised catalog using deterministic signals such as:

- Explicit user invocation
- Profile and project context
- Skill metadata and descriptions
- Required tools or platform conditions
- Exact identifiers and known aliases
- Lightweight lexical or semantic retrieval if justified by evaluation

Jev then receives only the small shortlist and the task context needed for one coherent judgment. Its output must be typed, validated, and replayable.

The initial judgment shape should favour one primary skill plus confidence and a no-skill outcome. Additional skills should normally enter through declared dependency expansion. True multi-capability selection can be added only if evaluation demonstrates that primary-plus-dependencies is insufficient.

## Intervention behaviour

- Intervention occurs before the main agent’s first model call for a new user request.
- Foundation skills continue to work through Hermes normally.
- Selected brokered skills are inserted into current-turn context, not the system prompt.
- The agent receives the selected instructions but does not receive the complete hidden catalog.
- Repeated content is not injected when the same content hash is already active in the session and still available in conversation context.
- If a request materially changes during execution, a single stable broker escape hatch may request another intervention without exposing the catalog.
- Router failure must never broaden access. The safe fallback is foundation-only operation or an explicit no-intervention result.

## Security boundary

Catalog hiding is context control, not filesystem security. A profile with unrestricted terminal or file access may still read a known path.

The first release will enforce routing authority at the broker interface: unauthorised skills cannot be granted or injected. Genuine filesystem-level isolation, if required, must be designed separately using process, sandbox, or operating-system controls.

## Audit and migration programme

The audit precedes the routing prototype because the catalog needs trustworthy identities and sources.

### Audit outputs

For every discovered skill:

- Stable ID and display name
- Canonical source and owner
- Current live resolution path
- Content hash
- Source type: first-party, bundled, OS, third-party, or project-scoped
- Update mechanism
- Duplicate or alias relationships
- Dependency references and closure status
- Usage evidence
- Current profile exposure
- Proposed classification: foundation, brokered, or retirement candidate
- Any conflict requiring Adam’s decision

### Known starting findings

The existing handoff records:

- Several active skill sources across profile, builtin, shared shelf, project, OS, and hub locations
- Sixty names present in more than one source
- Five intentional profile-versus-builtin divergences that must not be overwritten
- Ten shelf entries currently sourced from a non-git location without an update path
- Twenty-seven unmanaged skills
- Twenty-eight curator-stale skills
- Cross-skill references that can fail even when the parent skill resolves

These findings are the starting baseline; the project should not repeat the investigation from scratch.

### Migration rules

1. Do not delete skills automatically.
2. Do not overwrite intentional profile customisations with builtin copies.
3. Do not edit externally owned skills.
4. Repair update paths and dependency closure before changing visibility.
5. Introduce the catalog without moving content initially.
6. Repoint links incrementally and verify discovery and exact resolution after every batch.
7. Keep foundation skills available throughout.
8. Run the broker in shadow mode before injecting content.
9. Move or retire old paths only after all known consumers resolve through the intended source.
10. Record deletion candidates in the existing Deletion Review process for Adam’s approval.

## Delivery stages

### Stage 1 — Verified audit

Produce a machine-readable inventory and human review report from the existing measured state. Reconcile the stale library map and identify decisions without changing runtime behaviour.

### Stage 2 — Canonical catalog

Create the logical catalog, stable identifiers, dependency representation, and resolver. Prove that it resolves exact intended content across the existing canonical sources.

### Stage 3 — Non-breaking library repair

Repair no-update-path links, missing dependency links, aliases, and unintentional copies. Preserve all live Hermes behaviour and verify representative resolution through Hermes and the catalog.

### Stage 4 — Profile policies

Define and validate the first profile policy, including its native foundation set and broker-eligible catalog. The initial target should be one profile rather than a system-wide rollout.

### Stage 5 — Offline routing evaluation

Build deterministic candidate retrieval plus interchangeable stub, recorded, and live Jev judgment sources. Replay real historical prompts where permitted. Measure selection quality, no-skill decisions, false positives, false negatives, latency, and cost.

### Stage 6 — Shadow-mode Hermes integration

Invoke Skill Broker at the intended Hermes seam but do not inject content. Record what it would have selected and compare against actual skill use and human review.

### Stage 7 — Bounded intervention pilot

Enable content injection for one profile and a small brokered skill set. Keep foundation skills native and retain an immediate rollback path.

### Stage 8 — Measured expansion

Expand eligible skills and profiles only after acceptance thresholds are met. Reclassify skills from native to brokered incrementally, never in a single cutover.

### Stage 9 — Retirement review

Identify obsolete paths, copies, and stale skills only after the new resolution paths have remained verified. Submit deletion candidates for explicit approval.

## Evaluation and evidence

The project should maintain deterministic replay fixtures in the style proven by Agent Workflow Lab.

Measurements should include:

- Skill selection precision and recall against reviewed examples
- Correct no-skill rate
- Unauthorised-grant rate, which must remain zero
- Missing-dependency and cycle detection
- Average and percentile number of candidates sent to Jev
- Average selected skill count and content size
- Added routing latency
- Jev input/output usage and cost
- Prompt-index characters/tokens before and after migration
- Total current-turn context added by interventions
- Duplicate-injection rate
- Foundation-skill resolution regressions
- Broker and native resolution agreement for exact content hashes

Route evidence must distinguish deterministic retrieval, Jev judgment, and final code-enforced grant so responsibility remains clear.

## Failure behaviour

- Invalid intake: reject before any model call.
- Missing profile policy: foundation-only; record the reason.
- No relevant candidate: no intervention.
- Jev unavailable or invalid: deterministic fallback or foundation-only; never broaden access.
- Low confidence: no intervention or explicit review outcome according to policy.
- Unknown or unauthorised returned ID: reject the judgment and record a validation failure.
- Missing dependency or dependency cycle: fail closed for that pack.
- Skill budget exceeded: apply a deterministic reduction rule or return no intervention; never silently truncate instructional content.
- Catalog/source hash mismatch: reject delivery and flag the source for audit.
- Hermes adapter failure: normal foundation-only Hermes operation must remain recoverable.

## Testing strategy

Test through the highest stable seams.

### Primary behavioural seam

`prepare_turn(...) -> InterventionResult`

Tests should assert observable relationships:

- Every granted skill belongs to the profile’s authorised closure.
- The delivered hash matches the catalog-resolved canonical content.
- Foundation skills are not redundantly injected.
- The same recorded judgment produces the same route decision.
- An unauthorised Jev result cannot become a grant.
- Dependency expansion cannot escape policy or budget.
- Router failure does not impair foundation-only Hermes operation.

### Required test layers

1. Pure tests for policy, transitions, dependencies, budgets, and validation.
2. Recorded replay tests for Jev judgments.
3. Temporary-filesystem integration tests for catalog discovery and exact resolution.
4. Hermes adapter integration tests against an isolated profile/home.
5. Shadow-mode replay over a reviewed prompt corpus.
6. A bounded live Jev evaluation set, recorded for reproducibility.
7. End-to-end verification that native foundation skills still discover and resolve after each migration stage.

Tests should assert behaviour, not source shape, current catalog counts, or hardcoded model outputs.

## Initial acceptance criteria

The first production pilot is ready only when:

- Every pilot skill has one identified canonical source and stable ID.
- Every dependency in the pilot set resolves and remains within profile policy.
- The selected profile’s foundation skills still discover and resolve natively.
- Brokered pilot skills do not appear in that profile’s automatic skills index.
- The broker resolves brokered skills to the intended canonical content hashes.
- Jev receives only a bounded authorised candidate set.
- Unknown or unauthorised Jev outputs fail validation.
- Shadow-mode evidence has been reviewed before injection is enabled.
- Injection does not mutate the active conversation’s system prompt or tool schema.
- Repeated turns do not blindly duplicate the same skill content.
- Route decisions are replayable and auditable.
- Rollback restores the previous skill exposure without data loss.
- No skills or old paths have been deleted without Adam’s explicit approval.

## Decisions already made

- The project is named Skill Broker.
- It is the first production deterministic workflow applying lessons from Agent Workflow Lab.
- Jev is the semantic judgment model at narrow decision points.
- Pydantic and pydantic-graph are preferred based on successful experiments, but the project is not permanently constrained to them.
- Foundation skills remain available through Hermes.
- Specialised skills are delivered through deliberate pre-agent intervention.
- A skill audit and non-breaking reorganisation come before the routing prototype.
- The migration is additive and reversible; old paths are retired last.
- Skill hiding is not represented as a complete security boundary.

## Decisions still required

1. The exact first profile for the pilot.
2. The initial native foundation skill list for that profile.
3. The first small brokered skill set used for evaluation.
4. The canonical stable-ID scheme and alias policy.
5. Whether the catalog manifest is generated, checked in, or both.
6. The exact deterministic candidate-retrieval method for the first evaluation.
7. The Jev judgment schema after testing primary-only versus multi-skill cases.
8. Confidence and intervention-size thresholds based on evaluation data.
9. The precise Hermes plugin/middleware seam after a cache-safety proof.
10. Whether session skill availability needs explicit leases beyond content-hash deduplication.
11. Whether any profiles require real filesystem isolation in a later security phase.
12. The quantitative routing-quality threshold required to move from shadow mode to injection.

## Source material

- `~/Documents/life-os/Knowledge/Systems/Skill Library Rework Handoff.md`
- `~/Documents/life-os/Knowledge/Systems/Skill Library Map.md`
- `~/Documents/life-os/Knowledge/Systems/Skill Management Workflow.md`
- `~/Projects/agent-workflow-lab/README.md`
- `~/Projects/agent-workflow-lab/WHY.md`
- Hermes skill-system, middleware, plugin-LLM, and webhook documentation

## Next transformation

Run the `to-spec` workflow against this outline and the current conversation. The resulting specification should establish the external seam, user stories, implementation decisions, testing decisions, and explicit exclusions. After Adam confirms the seam, convert the accepted specification into implementation tickets rather than beginning an unbounded build.
