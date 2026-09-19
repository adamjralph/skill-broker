# Skill Broker

Skill Broker is a deterministic skill-intervention layer for Hermes agents. Before an
agent acts, it decides which authorised skills that request warrants and supplies them to
the current turn. Authority, limits, and routing live in deterministic code; semantic
judgment never grants anything.

## Language

### The inventory

**Skill**:
A reusable instruction package identified by a stable ID, maintained at one canonical
source, with metadata and zero or more dependencies.
_Avoid_: capability, tool, plugin, prompt

**Resolved Skill Version**:
One Skill at a specific content hash. The unit a Grant authorises and delivery verifies.
_Avoid_: skill copy, snapshot, file

**Canonical Source**:
The single authoritative location from which a Skill is maintained and updated.
_Avoid_: home, master copy, origin, shelf

**Catalog**:
A read-only logical inventory of the Skills resolvable across canonical sources. It holds
references and metadata, never content copies.
_Avoid_: library, index, registry

**Alias**:
A second name for one Skill identity.
_Avoid_: duplicate, redirect

**Duplicate**:
Two distinct Skill identities that overlap in content or purpose. A migration defect, not
a feature of the model.
_Avoid_: alias, copy

### The consolidated store

**Skill Store**:
The single authoritative location holding the canonical copy of every Skill. Consolidation
into the Skill Store precedes brokering.
_Avoid_: archive, mega-repo, dump

**Collection**:
A named group of Skills in the Skill Store that a Profile Policy can allow or deny as a
unit.
_Avoid_: pack, bundle, folder

### Exposure and policy

**Exposure**:
A path by which a Skill is reachable by a profile. One Skill may have many Exposures and one
Canonical Source.
_Avoid_: copy, instance, location

**Profile Policy**:
The deterministic declaration of what one profile may receive: its Foundation Set,
Brokered Allowlist, Preferred Skills, Denied Skills, budgets, thresholds, dependency
policy, and fallback behaviour.
_Avoid_: config, permissions file

**Foundation Skill**:
A Skill exposed to a given profile through Hermes's native discovery because it is
essential or frequently required for that profile.
_Avoid_: core skill, builtin skill, always-on skill

**Brokered Skill**:
A Skill withheld from a given profile's automatic index but eligible to be supplied by the
broker.
_Avoid_: specialised skill, optional skill

**Preferred Skill**:
A Skill whose relevance threshold is relaxed for a profile. It receives no authority and
is never force-supplied: a Preferred Skill still requires a positive Judgment.
_Avoid_: default skill, mandatory skill, always-inject

**Authorised**:
Admitted by the Profile Policy. The Profile Policy is the only source of authority.
_Avoid_: permitted, allowed, enabled

**Authorised Closure**:
A Skill and its Dependency Closure, intersected with what the Profile Policy authorises.
_Avoid_: allowed set, candidate pool

### The routing pipeline

**Candidate**:
A Skill surfaced by deterministic candidate retrieval as potentially relevant. An input to
Judgment, never authority.
_Avoid_: selection, match, pick

**Judgment**:
A typed semantic result expressing relevance and confidence over a candidate set. It
grants nothing.
_Avoid_: decision, score, approval

**Grant**:
The deterministic authorisation of one Resolved Skill Version for delivery to one agent
turn. The only step that carries authority.
_Avoid_: selection, permission, allocation

**Primary Skill**:
The one Skill a Judgment identifies as most relevant to the request.
_Avoid_: best skill, top match

**Dependency**:
A Skill that a Skill declares it requires.
_Avoid_: sub-skill, linked skill

**Dependency Closure**:
The transitive set of a Skill's Dependencies.
_Avoid_: dependency tree, requirements

### Delivery

**Skill Pack**:
The bounded collection of granted Resolved Skill Versions and their granted dependencies,
supplied for one request.
_Avoid_: bundle, payload, context blob

**Intervention**:
The act of supplying a validated Skill Pack before the main agent begins work on the
request.
_Avoid_: brokering, skill loading, injection

**Intervention Result**:
The outcome of a turn's intervention: its grants, its Skill Pack, its reasons, and its
evidence handle. An explicit no-intervention outcome is still an Intervention Result.
_Avoid_: response, output

### Evidence

**Session Ledger**:
In-session, conversation-scoped state recording which content hashes have already been
supplied, used only to avoid duplicate intervention.
_Avoid_: dedupe cache, history

**Route Decision**:
The complete record of one routing outcome: request identity, profile, candidates,
Judgment, policy result, grants, hashes, limits, reasons, timing, and model usage.
_Avoid_: trace, selection event

**Evidence Log**:
The durable, append-only store of Route Decisions.
_Avoid_: ledger, telemetry

### The system

**Broker**:
Skill Broker itself. The system, not the act; the act is an Intervention.
_Avoid_: router, dispatcher, intermediary
