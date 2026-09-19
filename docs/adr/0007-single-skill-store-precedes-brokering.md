# A single authoritative Skill Store precedes brokering

Skill discovery in practice is scattered: the same skills appear across agent tooling
directories, Hermes builtins, profiles, project trees, archives, and backups, and many
profiles can read them wherever they sit. That scatter is itself the problem Skill Broker
exists to solve, and it cannot be routed over: a broker built on ambiguous identities
would encode the duplication.

The destination is therefore a **single source of truth for skills first** — one
authoritative Skill Store holding the canonical copy of every Skill — and only then a
broker over it. Consolidation is audited across the whole system, not just Hermes's
runtime library; a "hermes" collection living inside the Hermes workspace is the leading
candidate for *hosting* that store, adopted only if testing supports it.

_Considered options_: routing directly over the existing per-profile, per-agent sources.
Rejected because it would make the broker's stable identities depend on the very topology
it is supposed to repair.
