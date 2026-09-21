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

Stages 1–4 are built and one Consumer is live. The Skill Broker's deterministic pipeline
is implemented through ticket #55: the `prepare_turn` seam, Authorised Closure, Candidate
Retrieval, Judgment validation and the Grant, Skill Pack assembly with Pack Delivery and
duplicate suppression, the live Jev Judgment Source with its recorded/No-Skill fallback, the
Hermes Adapter at the cache-safe seam in Shadow Mode, the machine-local Replay Corpus with
Reviewed Cases and SHA-bound Recordings, the offline routing evaluation with its pre-registered
Soft Thresholds, and the injection gate: the data-independent Hard Gates checked on every turn,
fail-closed with a recorded Incident, and the per-batch injection switch. The Shadow Report and
its batch review assemble recorded Route Decisions and observed skill use into the reviewed
artifact whose review is the only thing that enables a batch. Injection stays off by default.
The bounded pilot (#57) is rehearsed deterministically end to end — Cutover withholding and
byte-exact rollback, a reviewed Shadow Report enabling exactly one batch, a real Hermes turn
receiving a Pack, and duplicate suppression — without touching live Consumer state; the live
enablement awaits the operator's reviewed shadow traffic. Measured expansion (#58) is built as a
routine: one additive batch at a time, on fresh evidence with its own review, with the Soft
Thresholds held (or re-derived only against a strictly larger corpus), the rollback rehearsed
before enabling, the post-batch foundation-resolution and hash checks reported, and an open
Incident failing expansion closed. The second batch is rehearsed end to end against the real
store (`tests/hermes_adapter/run_expansion_proof.py`). Stage 9 retirement is defined (#23,
ADR-0023): retirement is a reversible, batched archive gated on explicit approval, deletion is a
separate purge, and execution stays blocked until every Consumer resolves through the store.

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
