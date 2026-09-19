# Migration is additive, copy-then-cutover, and reversible

The Skill Store is assembled and manifest-verified **in full** before any Consumer changes.
Each Consumer is then cut over **alone**, through its generated Exposure Farm, behind a gate,
and reversibly to a recorded baseline. No path is deleted or overwritten; redundant exposures
and out-of-scope copies stay in place until a separately-approved Stage 9 retirement.
Upstream-managed trees are vendored, their checkouts retained as read-only update origins.

_Considered options_: per-collection cutover — rejected, it interleaves content and wiring
failures and a partial store cannot be bijection-verified; referencing upstream trees
externally — rejected, it breaks the single canonical copy (ADR-0007) and the into-the-store
farm model (ADR-0011); symlinking or pruning redundant copies during migration — rejected, it
mutates live paths before all Consumers are verified and destroys the rollback baseline.
