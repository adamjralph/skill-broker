# Service-repo skills are excluded, and the store records a licence per identity

Skills authored inside a project or service repository and consumed only by agents working in
that repository are **excluded** from the Skill Store, exactly as project-scoped skills are;
the six `plastic-labs/honcho` skills stay repo-owned. This narrows ADR-0008's "authored
skills across every consumer": a repository's internal skills are not consumers' skills,
even when the repository carries its own `.claude`/`.agents` farm. `plastic-labs/honcho` is
also **AGPL-3.0**, not MIT, so ADR-0008's "all identified upstreams are MIT" holds only for
the vendored upstreams (NousResearch/Hermes, mattpocock, coreyhaines31, `cursor/plugins`).

Licence is recorded per identity: Adam-authored content uses `LicenseRef-Proprietary`;
vendored upstream content carries its SPDX id and upstream notice; `agents` and `omarchy`
are resolved per source when vendored; the store carries a top-level `LICENSE` and a
`NOTICE` aggregating vendored notices.

_Considered options_: admitting the honcho skills with AGPL notices — rejected, no Hermes
profile consumes them and it pulls copyleft into a private store for nothing; recording no
licence for authored content — rejected, a manifest row must state its licence explicitly.
