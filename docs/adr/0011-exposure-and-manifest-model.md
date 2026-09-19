# Consumers reach the Skill Store only through generated Exposure Farms

No consumer loader is ever pointed at the Skill Store itself. Each consumer is wired to a
generated **Exposure Farm**: a directory of `<exposure-name>` symlinks into the store's
canonical `<owner>/<name>/` directories — one farm per wiring target, a Hermes profile or a
non-Hermes consumer root. Farms live under a machine-local root and are never committed,
because their links carry absolute paths. For Hermes the farm is added to that profile's
`skills.external_dirs`; the profile's own skills directory stays the writable native tier
where the agent and curator create skills, so generation never clobbers writes and migration
stays additive and reversible — a farm entry shadowed by a local copy simply keeps resolving
locally until migration repoints it.

Pointing a loader at the store directly was rejected: Hermes keys discovery on frontmatter
`name`, not ID, so same-name divergences (`nousresearch.pdf` vs `openai.pdf`) would resolve by
silent sorted-first-wins; every identity would become index-visible to every profile,
contradicting brokered-hiding (ADR-0002/0005); and there would be nowhere to place the
deliberate exposure alias ADR-0009 relies on.

A **Brokered Skill** appears in no farm and no index: the broker reads its canonical content
from the store by ID + version, and the Intervention (inline or ADR-0010's spill file) is its
only route to the agent.

Two manifests model this, because generated hashes and authored policy are different review
surfaces. The **Store Manifest** is generated and committed at the store root, canonically
serialized, one row per identity: `id`, `name`, `aliases`, `owner`, `path`, `package_sha256`,
`skill_md_sha256`, `provenance {repo, commit}`, `local_patch {bool, upstream_commit}`,
`dependencies`, `license`. The **Exposure Manifest** records a consumer's
`{exposure name → ID}` mapping: authored per consumer in the store repo so a store change and
its consumers' exposure review together, and derived from Profile Policy for Hermes profiles
rather than hand-maintained a second time. Exposure names must be injective per consumer;
generation fails closed on collision, naming both identities.

The generator and verifier are a CLI in the skill-broker repo. Store-repo CI regenerates the
Store Manifest and fails on drift, re-verifies every hash, asserts a bijection between store
identity directories and manifest rows, and validates every committed Exposure Manifest
against the manifest (ID exists; names injective). Farm generation and link verification are
local; `--prune` is explicit, never default, and generation is atomic per farm, so a failed run
leaves the previous farm intact.

_Considered options_: pointing `skills.external_dirs` at the store (or a store subtree) —
rejected, above; one combined manifest — rejected, it fuses a generated artifact with authored
policy in one review surface; a hand-authored Hermes exposure list — rejected, the Profile
Policy's Foundation Set is already the single source of truth (ADR-0002); copying content into
farms instead of symlinking — rejected, it re-creates the duplication ADR-0007 exists to
remove.
