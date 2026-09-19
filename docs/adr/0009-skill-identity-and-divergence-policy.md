# Skill identity and divergence policy

A Skill's identity is a stable, immutable, store-assigned ID of the form
`<owner>.<name>`, where **owner** is the upstream publisher — or a maintained tree's
**pseudo-owner** (`work`, `agents`, `hermes_engineer`, `local`) when no upstream is
discoverable — and **name** is the Skill's human name. The **version** is the canonical
package hash over the normalized package file set; the `SKILL.md` hash is kept separately
as an index field. Identity is namespaced by provenance, never by the consuming tool.

Same-name divergences resolve by provenance, not by name:

- identical content is **one identity**; further names are aliases;
- a different owner is a **distinct identity**;
- a local edit of an admitted upstream, or of a tree declared `fork_of` that upstream, is a
  **patched version of the same identity** (new package hash, `local_patch` marker);
- content with no discoverable upstream and no declared fork is a **distinct identity** under
  the maintaining tree's pseudo-owner.

A **derived copy** (runtime cache, plugin cache, snapshot archive) is evidence of a Skill,
never a Canonical Source: admission still requires a canonical source (ADR-0008). A Skill seen
only as a derived copy is known but not admitted. Profile Policy references IDs; Grants, the
Session Ledger, and Route Decisions reference ID + version.

_Considered options_: naming by the consuming tool (`codex.pdf`) — rejected, it makes identity
depend on exposure; pure content-addressing as identity — rejected, it discards the logical
Skill and cannot carry provenance or a stable policy reference; `SKILL.md`-only hashing —
rejected, 0.55% of duplicate groups differ underneath (identity-hash-depth); treating a local
edit as a distinct identity — rejected, it severs the upstream link that keeps pulls useful.
