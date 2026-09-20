# The Profile Policy is authored in the store; Hermes Exposure Manifests are derived from it

A Hermes profile's **Profile Policy** is an authored JSON document committed in the Skill Store
at `policies/<profile>.json`, one file per profile; the filename stem is the profile name and
the document's `profile` field must equal it. It is the single authored declaration of what one
profile may receive, and its **Exposure Manifest** is derived from it deterministically rather
than authored a second time (ADR-0011). Derivation emits one exposure per store-backed
Foundation Set entry — named by the entry's explicit `exposure` alias, else the Skill's Name —
and emits nothing for project-wired foundation entries (wired by the project setup path, not the
farm) or for Brokered, Preferred, and Denied Skills. A collision of exposure names fails closed.
For a genuine Name Collision — two identities sharing a frontmatter `name` — the twin belongs in
the Brokered Allowlist rather than the farm, because Hermes keys its skill index on frontmatter
`name` first-wins and would otherwise silently hide one of the pair; an exposure alias renames a
path, never the indexed Name. The policy lives in the store repo because ADR-0011 already keeps
authored exposure review beside the store content it references; the broker reads it at runtime,
and the derived manifest is never committed.

_Considered options_: a hand-authored Hermes Exposure Manifest — rejected, a second source of
truth that drifts from the policy; a machine-local or broker-repo policy — rejected, it
separates the authorising declaration from the store IDs it references, defeating
review-together (ADR-0011); committing the derived manifest — rejected, the policy is the
source of truth and a committed derivative can drift; auto-minting an owner-qualified exposure
alias on collision — rejected, it presents two same-`name:` Skills to Hermes and silently hides
one, the sorted-first-wins failure ADR-0011 exists to prevent.
