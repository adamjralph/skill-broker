# Cutover models the Consumer's effective exposure, not one filesystem walk

A Consumer's Cutover baseline is the **effective exposure** Hermes builds its automatic index
from, not the profile's own `skills/` directory alone. It is the command-line Cutover roots plus
the config's pre-existing `skills.external_dirs` (that Consumer's other native skill roots), minus
every Name in `skills.disabled`. All three parts are recorded in the baseline
(`roots`, `config.external_dirs`, `config.disabled`) and used by `baseline`, `apply` and
`verify`, so the gate, the policy and the index agree.

This closes the three gaps wayfinder #64 found in `hermes_engineer`, the last Consumer:

- **Several real roots.** `hermes_engineer` wires `~/.agents/skills` and
  `~/Documents/stillroom-wiki/skills` in `skills.external_dirs` alongside its profile `skills/`.
  A single-root baseline could not see `ask-matt` (which resolves in `~/.agents/skills`) and
  reported `agent-skill-library-management -> ask-matt` as an unresolved reference. Folding the
  wired external directories into the baseline makes the reference resolve and makes the zero-regression check
  cover the real exposure.
- **A pre-existing `skills.disabled`.** The baseline previously walked the filesystem, so a
  policy authored from it listed disabled Names as foundation and the gate then failed with
  `foundation not resolving`. The baseline now honours `disabled`: the withheld Names are not in
  `resolution`, exactly as they are not in the index.
- **A genuinely dangling reference.** `research-paper-writing -> subagent-driven-development`
  (`nousresearch.subagent-driven-development`) is dangling *natively* —
  `subagent-driven-development` is in none of the Consumer's roots. The reference check now fails
  closed only on references the Cutover **leaves** unresolved among Skills it newly exposes; a
  reference already dangling in the recorded baseline is a pre-existing native defect, recorded
  by the baseline and carried to a future dependency ticket, not a Cutover regression. A Cutover
  is additive (ADR-0012), so it cannot have broken a reference that was already broken, and
  making the gate police native defects would block every zero-delta Cutover that inherits one.

The extra directories stay native. The farm and the Profile Policy foundation are the profile's own
writable tier; an external directory is not wired or brokered by the Cutover and may be listed in the
foundation or omitted, as the operator decides (as `astra-pinned` already leaves its
`engineering-skills` external directory untouched). Folding a directory into the baseline resolution does not
vendor, move or disable anything in it.

Two carried items, recorded rather than fixed here:

- The two stale profile copies (`adam-content-writing`, `linkedin-post-writing`) stay in place.
  The foundation names the canonical `local.*` identities, the farm exposes the canonical
  Versions, the native profile copy keeps winning first-wins, so the zero-delta check holds; the
  copies are retired by Stage 9 (ADR-0012/0023) after every Consumer resolves through the store.
- Three Skills in `~/.agents/skills` (`caveman`, `explain-diff-html`, `explain-diff-notion`)
  postdate the Stage 1 audit and have no Store identity. They stay native and are neither farmed
  nor required by the policy; vendoring them is a separate store change.

_Considered options_: requiring the operator to pass every root by hand — rejected, the config
already declares them and a hand-maintained second list drifts; adding the extra directories to the
farm/foundation — rejected, it would vendor shared directories into one profile's exposure and pull
three un-vendored Skills into a policy that can only reference store IDs; adding
`subagent-driven-development` to the foundation to close the dangling reference — rejected, it is
a real exposure change that defeats the zero-delta Cutover and grows the always-present index the
broker exists to shrink; editing `research-paper-writing` to drop the reference — rejected, it
patches a profile-scoped identity's content for a defect that predates the Cutover; keeping the
filesystem-walk baseline and instead making the policy gate ignore `disabled` — rejected, it
leaves policy and index disagreeing.
