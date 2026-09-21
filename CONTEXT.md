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

**Name**:
The human label a Skill carries, taken from its frontmatter `name`. Not unique and never an
identity: two distinct Skills may share one Name.
_Avoid_: id, key, slug

**ID**:
The immutable, namespaced identifier of a Skill, `<owner>.<name>`. Assigned by the Skill Store;
never read from the Skill's own frontmatter.
_Avoid_: name, slug, hash

**Owner**:
The upstream publisher whose namespace a Skill's ID carries. A maintained tree with no
discoverable upstream supplies a Pseudo-owner in its place.
_Avoid_: consumer, tool, profile

**Pseudo-owner**:
The owner namespace for content with no discoverable upstream: the tree or profile that
maintains it (`work`, `agents`, `hermes_engineer`, `local`).
_Avoid_: local owner, fake owner

**Provenance**:
The recorded origin of a Skill: its Owner plus the upstream repository and commit.
_Avoid_: source (that is the Canonical Source)

**Version**:
The canonical package hash of one Skill's content over its normalized file set. The second
element of a Resolved Skill Version.
_Avoid_: release, tag, revision

**Resolved Skill Version**:
One Skill at a specific Version — its ID plus its package hash. The unit a Grant authorises
and delivery verifies.
_Avoid_: skill copy, snapshot, file

**Local Patch**:
The recorded fact that a Skill's canonical content deliberately diverges from its upstream
commit. A Local Patch produces a new Version of the same identity.
_Avoid_: fork, edit, mod

**Fork**:
A declared attribution of no-upstream content to a known upstream Owner, so its divergences are
Local Patches of that Owner's identity rather than new identities.
_Avoid_: copy, mirror

**Canonical Source**:
The single authoritative location from which a Skill is maintained and updated.
_Avoid_: home, master copy, origin, shelf

**Catalog**:
A read-only logical inventory of the Skills resolvable across canonical sources. It holds
references and metadata, never content copies.
_Avoid_: library, index, registry

**Alias**:
A second name for one Skill identity. An Alias is a Name, never an ID.
_Avoid_: duplicate, redirect

**Name Collision**:
Two distinct Skill identities that share a Name. Expected and legitimate.
_Avoid_: conflict, clash, duplicate

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

**Store Manifest**:
The generated, committed inventory of the Skill Store: one row per Skill identity carrying
its Version, Provenance, aliases, and dependencies. The committed form of the Catalog.
_Avoid_: catalog file, index file

**Cutover**:
The repointing of one Consumer's skill resolution from its existing exposures to its
generated Exposure Farm. Performed one Consumer at a time, behind a verification gate, and
reversible.
_Avoid_: switch, flip, replacement

**Retirement**:
The reversible withdrawal of a superseded path after every Consumer resolves through the Skill
Store: the path is moved to a dated, manifest-backed retired set that restores byte-for-byte.
Batched and reviewed; never a deletion.
_Avoid_: deletion, cleanup, prune

**Purge**:
The irreversible deletion of retired content after a retention window, approved explicitly and
separately from Retirement. The only irreversible migration act.
_Avoid_: retirement, cleanup, prune

### Exposure and policy

**Consumer**:
An environment that resolves Skills for itself: a Hermes profile or another agent's skill
root. Each Consumer has its own Exposure Farm.
_Avoid_: client, application

**Exposure**:
A path by which a Skill is reachable by a Consumer. One Skill may have many Exposures and one
Canonical Source.
_Avoid_: copy, instance, location

**Exposure Farm**:
The generated set of symlinks that gives one Consumer its Exposures, linking Exposure names
to the Skill Store's canonical directories. The Skill Store is never a Consumer path.
_Avoid_: symlink farm, mirror, spoke

**Exposure Manifest**:
The record of which Exposure names one Consumer resolves to which IDs. Authored for
non-Hermes Consumers; derived from the Profile Policy for Hermes profiles.
_Avoid_: farm manifest, mapping file

**Exposure Alias**:
The Exposure name a Profile Policy gives a Foundation Skill when it differs from the Skill's
Name. A Name, never an ID.
_Avoid_: rename, override

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

**Foundation Set**:
The Foundation Skills a Profile Policy exposes to one profile, each a store ID or a
project-wired entry.
_Avoid_: core set, always-on list

**Brokered Allowlist**:
The Brokered Skills a Profile Policy permits the broker to supply to one profile, by ID.
_Avoid_: hidden list, withheld set

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

**No-Skill Outcome**:
The explicit Judgment that no authorised Candidate warrants intervention for a request. A
valid, recorded outcome, not a failure.
_Avoid_: no-match, miss, empty result

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

**Pack Delivery**:
Whether a Skill Pack's content is supplied within the Intervention itself or by reference to
a location holding it in full. Independent of which Skills the Pack contains.
_Avoid_: truncation, spill

**Delivery Path**:
The channel one turn's content can reach the model through the Hermes seam: ordinary text,
which carries the per-turn user-message sidecar, versus multimodal content and the app-server
route, which cannot and so receive no Intervention and fall back to native foundation exposure.
_Avoid_: transport, provider route

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

**Lease**:
A session-scoped permission to keep supplying a Skill without a fresh Judgment. Not used:
authority is per-turn and the Session Ledger is evidence, not permission.
_Avoid_: session grant, sticky grant

**Route Decision**:
The complete record of one routing outcome: request identity, profile, candidates,
Judgment, policy result, grants, hashes, limits, reasons, timing, and model usage. Carries the
turn's correlation ids and a deterministic `route_decision_id` locator.
_Avoid_: trace, selection event

**Evidence Handle**:
The record appended for one provider request carrying the system-prompt hash, the tool count
and the correlation ids, so the Route Decision that request belongs to is locatable.
_Avoid_: trace, log line

**Evidence Log**:
The durable, append-only store of Route Decisions, holding metadata and content hashes rather
than request text.
_Avoid_: ledger, telemetry

### Evaluation and rollout

**Replay Corpus**:
The reviewed set of real request Cases used to evaluate routing and pre-register Soft
Thresholds. The sole home for full request text (ADR-0015), held machine-local.
_Avoid_: dataset, test set

**Case**:
One request turn extracted for replay, with its provenance and the profile's Authorised Closure
at extraction.
_Avoid_: prompt, sample

**Reviewed Case**:
A Case whose ground-truth outcome — a Primary Skill or a No-Skill Outcome — has been corrected
by hand. Only Reviewed Cases count for evaluation and threshold setting.
_Avoid_: label, example

**Recording**:
A Jev outcome frozen and bound to its Case by content hash, so replay is deterministic and
offline.
_Avoid_: snapshot, fixture

**Hard Gate**:
A routing-quality condition that must hold regardless of evaluation data, checked on every turn.
A breach fails the profile closed; never negotiated by a corpus or baseline.
_Avoid_: must-have, blocker

**Injection Batch**:
A named group of Brokered Skills one recorded review authorises the broker to inject for one
profile. Injection is off until a batch is enabled, and enabling names the profile and the batch.
_Avoid_: rollout, release

**Expansion**:
The reviewed act of enabling one further Injection Batch for a profile, additive to the running
batch, on fresh evidence and after a rollback rehearsal. One batch at a time; the Soft Thresholds
do not move to admit it.
_Avoid_: rollout, scale-up, broadening

**Batch Review**:
The recorded review that authorises enabling one Injection Batch, naming the profile and the
batch. After a Hard-Gate breach, only a review recorded after the breach may re-enable it.
_Avoid_: sign-off, ticket

**Injection Gate**:
The durable injection switch and its enforcement: it keeps injection off until a batch is enabled,
checks the Hard Gates on every turn, fails a profile closed on a breach, and holds expansion on a
Soft-Threshold regression. Its state is observable.
_Avoid_: feature flag, kill switch

**Incident**:
The durable record of one Hard-Gate breach: the gate, the turn's correlation ids and reason
markers, and when it happened. An Incident fails the profile closed and is never rewritten.
_Avoid_: alert, warning

**Open Incident**:
An Incident no review recorded after it has closed. While one is open a profile cannot Expand:
growth is not the recovery path from a breach.
_Avoid_: unresolved bug, pending review

**Failed Closed**:
The state a profile enters on a Hard-Gate breach: foundation-only with injection off, until a
review recorded after the breach re-enables a batch. The turn that breached cannot flip the
switch back.
_Avoid_: disabled, degraded

**Soft Threshold**:
A routing-quality threshold derived from the measured Stage 5 baseline over the reviewed replay
corpus and pre-registered before shadow review. Moves only by re-derivation against a larger
corpus, never to admit a batch.
_Avoid_: target, cutoff

**Shadow Mode**:
The integration stage in which the broker runs at its Hermes seam but injects no content,
recording what it would have selected.
_Avoid_: dry run, test mode

**Shadow Report**:
The reviewed record of what the broker would have selected in Shadow Mode, compared against
actual skill use and human review. Precision and recall are measured against actual skill use;
the review supplies the ground truth for a disagreement's attribution, not the metrics, so a
Grant the agent never loaded is a false intervention (ADR-0024). Its review authorises enabling
injection for a batch.
_Avoid_: metrics dump, eval report

### The system

**Broker**:
Skill Broker itself. The system, not the act; the act is an Intervention.
_Avoid_: router, dispatcher, intermediary
