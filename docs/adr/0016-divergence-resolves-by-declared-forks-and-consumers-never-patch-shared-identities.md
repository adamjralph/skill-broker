# Divergence resolves by declared Forks; Consumers never patch shared identities

A tree maintained as an edited whole of a known upstream is declared `fork_of` that
upstream, so its same-named content is a **Local Patch** — a new Version of the upstream
identity, never a new identity. `Work/.agents/skills` is a Fork of `mattpocock/skills`: its
13 divergent engineering skills become patched `mattpocock.*` Versions.

A Hermes profile is a **Consumer**, not a Canonical Source, and a Consumer never patches a
shared identity — a shared patch would leak that profile's customisation into every other
profile resolving the same identity. A profile's deliberately-maintained divergent copy is
therefore a distinct identity under the profile's Pseudo-owner; a stale copy is not an
identity at all, only an older Version of the canonical identity. Applied:
`hermes_engineer.agent-skill-library-management`, `.multi-agent-profile-workflows`,
`.life-os`, `.hermes-agent`, and `.handoff` stay distinct;
`hermes_engineer.adam-content-writing` and `.linkedin-post-writing` are stale and collapse
to `local.*`. This narrows ADR-0009's patch rule to edits made in a Canonical Source or a
declared Fork.

_Considered options_: patching every same-name divergence — rejected, it globalises
profile-scoped customisations; keeping the Work tree as 13 distinct `work.*` identities —
rejected, it mints overlapping Duplicates and severs the upstream link that keeps pulls
useful.
