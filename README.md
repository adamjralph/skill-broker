# Skill Broker

**Intervene with the right authorised guidance before the agent acts.**

Skill Broker is a deterministic skill-intervention layer for Hermes agents. Before the
main agent's first model call, it evaluates an incoming request, selects a small set of
relevant skills from that profile's authorised catalog, validates the selection in code,
and supplies the selected skill content to the current agent turn.

Authority, limits, and routing decisions live in deterministic code. Jev — a narrow
semantic judgment model — only expresses relevance and confidence, and never grants
access.

## Why

Hermes currently renders every enabled skill's name and description into agent context on
every turn. The live library is large, contains duplicate names from mixed owners, and
mixes frequent foundation skills with rarely relevant specialised ones. This costs context
on every turn and forces agents to notice and load skills deliberately.

Skill Broker keeps a small foundation set native to Hermes, brokers the larger specialised
library, and produces replayable evidence for every routing decision.

## Status

Stages 1–8 are built; ten Consumers with skills are cut over to their Exposure Farms (`default-2` has no policy). The broker includes the deterministic routing pipeline, Hermes Adapter, replay/evaluation, review-gated injection and measured expansion. This is not a claim that injection is currently healthy for every Consumer; check the live gate state before enabling a batch.

**Stage 9, 2026-09-26:** With Adam's one-time backup-first exception to ADR-0023's timing/paperwork gates, 123 overlapping skill paths across ten profile tiers were moved into a labelled reversible archive, not purged. Original profile trees were backed up and verified first (1,295 files and 25 symlinks); native Hermes resolution was checked after each profile (127 name checks, no missing or ambiguous names). The store remains canonical and unchanged profile-only skills stay in place. See [`docs/retirement/2026-09-26-preflight.md`](./docs/retirement/2026-09-26-preflight.md) and the per-profile journals in `/home/hermes/Documents/skill-backups/2026-09-26-profile-skills-before-stage9/`. The standard `scripts/retire.py` still enforces ADR-0023 for future batches; this one-time operation does not loosen it.

Canonical documents:

- [`PROJECT-OUTLINE.md`](./PROJECT-OUTLINE.md) — domain model, system shape, delivery stages
- [`CONTEXT.md`](./CONTEXT.md) — the domain language, plus [`docs/adr/`](./docs/adr/)
- issue #44 — the accepted specification for the broker half
- [`docs/HANDOFF.md`](./docs/HANDOFF.md) — the live continuation state and next action
- [`docs/runbooks/expansion.md`](./docs/runbooks/expansion.md) — the measured-expansion routine
- [`docs/retirement/`](./docs/retirement/README.md) — the Stage 9 retirement process

## Delivery stages

1. Verified audit
2. Canonical catalog
3. Non-breaking library repair
4. Profile policies
5. Offline routing evaluation
6. Shadow-mode Hermes integration
7. Bounded intervention pilot
8. Measured expansion
9. Retirement review

The migration is additive and reversible. No skill or old path is deleted without
explicit approval.
