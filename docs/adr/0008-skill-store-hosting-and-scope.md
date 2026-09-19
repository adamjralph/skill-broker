# Skill Store hosting and scope

The Skill Store is a **private**, git-backed repository at `~/skill-store` (remote
`adamjralph/skill-store`) holding one canonical named directory per Skill identity,
namespaced by owner/collection, with a generated manifest carrying package hashes. Scope is
**authored skills across every consumer** — Hermes runtime and agent tooling alike. Derived
runtime caches and snapshot archives are excluded, and **project-scoped skills stay
project-owned**, wired by a per-project setup script or skill.

_Considered options_: extending `~/Documents/skills-archive` — rejected, it is not a superset
(332 of 585 contents absent) and mixes an archive with embedded upstream checkouts; hosting
inside the Hermes workspace — rejected, it would make one agent the owner of a cross-agent
store; content-addressed storage — rejected for layout (it destroys browsability of
hand-authored text; `.bb` remains prior art for caches); public hosting — rejected because
collections include non-public workflow material.

All identified upstreams are MIT (Matt Pocock, Corey Haines, Lauren Tan, Nous Research), so
vendored collections must carry their copyright notices and license text.
