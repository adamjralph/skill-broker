# Brokered withholding is a Cutover edit to the Consumer's disabled-skill list

A **Brokered Skill** is withheld from a profile's automatic skills index by adding its Name to
that Consumer's `skills.disabled` list at **Cutover** — the same per-Consumer, reversible Cutover
that wires the Exposure Farm into `skills.external_dirs` (ADR-0011, ADR-0012). Withholding is
enforced by Hermes's own deterministic index filter, which hides any Skill whose frontmatter
`name` (or directory name) appears in `skills.disabled` when the skills index is built
(`agent/prompt_builder.py`, `agent/skill_commands.py`). It is not the Adapter's job: the Adapter
keeps registering only `pre_llm_call` and `pre_api_request`, writes no configuration, and touches
neither the system prompt nor the tool schema (ADR-0001, ADR-0019).

## Where the withholding gate is checked

Hard-gate item 5′ — "Brokered pilot Skills do not appear in the profile's automatic skills
index" — is a **Cutover gate**, checked **at Cutover, once per Consumer's Brokered Allowlist
(the batch that enabling covers)**, in `scripts/cutover.py`'s policy gate and re-runnable by
`cutover verify`. It is **not** one of the per-turn broker Hard Gates, which stay
unauthorised Grants, foundation-resolution regressions, broker/native hash disagreement,
Judgment validation, closure completeness and prompt/tool-schema mutation (ADR-0019). The
complementary failure of an over-broad disable — a Foundation Skill vanishing — is already
caught per turn by the `foundation-resolution regression` Hard Gate and at Cutover by item 4′.
The *enforcement* is continuous, because Hermes applies `skills.disabled` on every index build;
the *check* is at Cutover, so a profile's withheld set is reviewed once per batch rather than
re-derived every turn.

## Assessed against the live profile

`stillroom-signal-generator` has no `skills:` block, and its five Brokered Skills
(`local.adam-content-writing`, `nousresearch.humanizer`, `hermes_engineer.message-composer`,
`coreyhaines31.copywriting`, `coreyhaines31.cold-email`) sit in the profile's own `skills/`
directory — the writable native tier Hermes always scans first — so they are index-visible
today, and `get_disabled_skill_names()` for that `HERMES_HOME` returns the empty set. Migration
is additive (ADR-0012), so those copies stay on disk and cannot be removed; withholding must be
a visibility edit, not a move.

The Adapter option was assessed against the same profile. The Adapter is constructed with the
profile and reads `config.yaml` for the hook cap and route, but it registers only `pre_llm_call`
and `pre_api_request` and writes nothing. To withhold the pilot's five Brokered copies it would
have to hide Skill directories that Hermes scans from the profile's own `skills/` folder before
`pre_llm_call` runs, and it has no lever to do so: no plugin hook filters the skills index
(`VALID_HOOKS`), and mutating the assembled request at `pre_api_request` would change the
system-prompt hash its own Hard Gate checks.

## Names, not IDs

`skills.disabled` matches the frontmatter `name`, so Cutover maps each Brokered store ID to its
Name **and every alias it may be indexed under** (ADR-0009); a Brokered identity carrying an
alias would otherwise stay visible under that alias. A profile copy indexed under a Name that is
neither the identity's Name nor a recorded alias is the Duplicate/migration-defect case the
Stage 1 audit exists to surface, not a legitimate withholding target. A Brokered Name that is
also a Foundation Name — the Name Collision ADR-0018 already routes to the Brokered Allowlist —
is **not** disabled by name: the two twins cannot both be index-visible (Hermes keys on `name`
first-wins), disabling the shared name would hide the Foundation twin too, and the gate's
existing exemption already covers the pair.

## Reversibility

The withholding edit is a Consumer-config edit of the same class as `skills.external_dirs`:
Cutover records exactly the Names it added and, when it created the `skills:` block, removes
exactly what it created on rollback — leaving pre-existing `disabled` entries untouched and the
config byte-for-byte restored. No Skill content is created, moved or deleted, so the decision
reverses with no content loss; the only reversal cost is re-running one Consumer's config edit.
Rollback of the pilot was already rehearsed for the `external_dirs` edit (#42); the `disabled`
key joins the same rehearsal.

## Carried fog

The Adapter's packaging, distribution and enablement remain fog on the map (B16). This decision
removes only the withholding question from that fog: because withholding is a Cutover config
edit, the Adapter needs no index-filtering capability, no new hook and no config write, so the
remaining packaging question is not entangled with it and is carried unchanged.

_Considered options_: the Adapter withholding at runtime — rejected, it has no index lever:
Hermes builds the skills index from configuration before `pre_llm_call` runs, no plugin hook
filters that index (`VALID_HOOKS`), `pre_llm_call` only appends current-turn user context, and a
`pre_api_request` rewrite of the assembled system prompt would break the byte-identical
prompt/tool-schema invariants and trip the Hard Gate the Adapter exists to satisfy (ADR-0001,
ADR-0019); an Adapter that wrote `skills.disabled` itself would contradict "the Adapter never
writes the host's configuration" and would put enforcement outside the reviewed, reversible
Cutover. Removing or moving the profile's own Brokered copies at Cutover — rejected, migration
is additive and non-destructive (ADR-0012). A per-turn broker index filter — rejected, it would
reconstruct Hermes's index rules in the broker and re-litigate visibility every turn instead of
once per reviewed batch.
