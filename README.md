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

Project-definition stage. The authoritative description lives in
[`PROJECT-OUTLINE.md`](./PROJECT-OUTLINE.md), which covers the domain model, system shape,
module seams, delivery stages, evaluation criteria, and open decisions.

Next step: a bounded catalogue reconnaissance spike to inform the specification, followed
by the `to-spec` workflow.

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
