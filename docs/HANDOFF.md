# Skill Broker handoff

Updated: 2026-09-21 04:56 AEST (verified against the working tree, Git, GitHub, and the store)
Next objective: implement **[#51 — The Hermes Adapter in Shadow Mode](https://github.com/adamjralph/skill-broker/issues/51)**, then the Stage 5 evaluation tickets.

## Start here

Suggested skills, in order: `implement` → `tdd` (at the Hermes seam) → `code-review`.
Consult `domain-modeling` before touching `CONTEXT.md`, and the repo's agent docs:

- [`AGENTS.md`](../AGENTS.md) — project rules, graft usage, issue-tracker/triage/domain doc pointers
- [`CONTEXT.md`](../CONTEXT.md) — the domain language; names in code must match it
- [`docs/agents/issue-tracker.md`](agents/issue-tracker.md) — `GH_CONFIG_DIR=~/.config/gh-personal` for every `gh` call; claim/close conventions
- [`PROJECT-OUTLINE.md`](../PROJECT-OUTLINE.md) — the outline of record

Authoritative spec and decisions:

- issue **#44** — the accepted spec (store half built; broker half in progress). It carries the stage map, the `B1`–`B17` implementation decisions, and the testing seams.
- ADRs in [`docs/adr/`](adr/) — the ones that bind the broker: [`0001`](adr/0001-one-external-seam-two-internal-seams.md) (one external seam, two internal), [`0004`](adr/0004-jev-judges-relevance-code-grants.md), [`0006`](adr/0006-v1-judgment-is-single-primary-plus-dependencies.md), [`0010`](adr/0010-pack-delivery-under-the-hook-cap.md), [`0013`](adr/0013-candidate-retrieval-is-lexical-and-jev-returns-one-choice.md), [`0014`](adr/0014-authority-is-per-turn-and-the-session-ledger-is-not-a-lease.md), [`0015`](adr/0015-the-evidence-log-records-metadata-and-hashes-not-request-text.md), [`0018`](adr/0018-profile-policy-is-authored-in-the-store-and-manifests-are-derived.md), [`0019`](adr/0019-injection-gate-is-precision-first-and-fails-closed.md), [`0020`](adr/0020-the-replay-corpus-is-opted-in-local-and-hand-reviewed.md).
- For code context, use the graph first: `graft map`, `graft ask "<question>" --source`, `graft callers <symbol>`. Refresh with `graft build` after changes.

## Verified current state

**Git.** Branch `main`, HEAD `46c436c`. `origin/main` is level. Recent broker commits:
`4718e6a` #46 seam, `14f69c0` #47 retrieval, `47e6e00` #48 Judgment/Grant, `e19deea` #49 Pack, `46c436c` #50 live Jev.

**Tests.** `python3 -m unittest discover -s tests` from the repo root → **197 tests, all passing**. No packaging, no third-party deps beyond PyYAML for the store tooling.

**Broker modules** (`scripts/broker/`): `broker.py` (`Broker.prepare_turn`, the only external seam), `closure.py` (`authorised_closure`, `dependency_closure`), `retrieval.py` (BM25 candidate ranking), `metadata.py` (frontmatter retrieval metadata), `judgment.py` (`JudgmentCall`, sources, `validate`, `grant`, `FallbackJudgmentSource`), `jev.py` (`JevJudgmentSource`, `live_judgment_source`), `pack.py` (`HookConfig`, `Budget`, `build_pack`, Pack Delivery), `evidence.py` (`EvidenceLog`, `JsonlEvidenceLog`, `SessionLedger`), `types.py` (`RouteDecision`, `InterventionResult`, `PackDelivery`, …).

**Closed tickets.** #45–#50 (module home, seam, retrieval, Judgment/Grant, Pack, live Jev).

**Skill Store.** `~/skill-store` (repo `adamjralph/skill-store`): **415 identities**, manifest verifies clean; two policies `life-agent.json` and `stillroom-signal-generator.json`. Verified this session:

```
python3 scripts/store_manifest.py verify --store ~/skill-store      # ok
python3 scripts/profile_policy.py validate --store ~/skill-store    # 2 policies
```

**Cutover.** `life-agent` is live, zero-delta, rollback rehearsed; farm at `~/.local/share/skill-broker/farms/life-agent`.

**Open frontier** (open, no open blocker, unassigned): **#51**, #53, #54, #59. Gated: #52←#51, #55←#54, #56←#51+#55, #57←#52+#53+#56, #58←#57. Spec order favours #51 next.

**Evidence from #50.** Live Jev call **not executed**: `typesafe_sdk` is not installed in the broker's Python and `TYPESAFE_API_KEY` is not exported. The SDK contract was verified against the installed SDK source and the live TypeSafe docs; the source is driven in tests through an injectable `client_factory`.

## In progress and pending

Nothing is half-written. The next build is wholly unattempted:

- **#51 The Hermes Adapter in Shadow Mode** — the only remaining internal seam (ADR-0001/B9). It composes a `Broker` and registers exactly two hooks (`pre_llm_call`, `pre_api_request`), injecting nothing while shadowing.
- **#54 Replay Corpus extraction and Reviewed Cases** (Stage 5).
- **#53 Decide where Brokered withholding is enforced** (spec B16 open decision; gates the pilot).
- **#59 Fix the project-setup skills block requirement** (independent, small).

Acceptance criteria for #51 are in the issue; the six that matter: byte-identical system prompt and tool schema in shadow (and across turns); no Pack content on the wire; one evidence record per request carrying the system-prompt hash, tool count and correlation ids, locatable from its Route Decision; unsupported paths emit no Intervention and record why; an Adapter failure leaves Hermes intact; the effective hook cap is **read** at runtime, not written to config.

Wire-up available to #51: `HookConfig(hook_cap=…, reserve=…, spill=…)` (from #49), `live_judgment_source(recording=…)` (from #50), `JsonlEvidenceLog`, `SessionLedger`, and the prototype on the throwaway branch `prototype/hermes-intervention-seam` (the payload contract and the `run_proof.py` / `compare_proof.py` / `run_spill_proof.py` harnesses).

## Decisions and boundaries

- **Injection is off by default.** Shadow Mode runs the whole pipeline at the real seam and returns no context. Enabling a batch needs a reviewed Shadow Report and Adam's explicit approval (ADR-0019).
- **Migration is additive and reversible.** No Skill, source path, or Consumer path is deleted without explicit approval; Stage 9 Retirement is not authorised.
- **Main stays docs/spec/tooling.** Prototypes live on throwaway branches. `prototype/hermes-intervention-seam` is out of main.
- **Authority.** Only deterministic code mints a Grant; Jev never grants. The Session Ledger is dedup evidence, never a lease. The Evidence Log holds metadata and hashes — no request text, no Skill bodies (ADR-0015).
- **Jev prompt/criteria engineering is carried fog** (B5, spec "Out of Scope"). #50 delivered a working, untuned source; do not tune it here.
- **B16 is unresolved:** whether Brokered withholding happens at Cutover (`skills.disabled`) or in the Adapter decides where hard-gate item 5′ is checked. That is #53 — do not silently choose it while building #51.
- **Falsification condition.** If Reviewed Cases require two independent primaries, ADR-0006's single-primary deferral is lifted by a new ticket, not a silent change.
- **Uncommitted local state — leave it:** `.gitignore` and `AGENTS.md` carry graft additions; `.ignore`, `opencode.json` and `prototypes/` are untracked. Not part of any ticket. Do not commit or clean without Adam.

## Blockers and troubleshooting

- **Live Jev cannot be called from the broker repo's Python.** `typesafe_sdk` is absent and no key is exported. The agent-workflow-lab venv `/home/hermes/Projects/agent-workflow-lab/.venv` (Python 3.11) has the SDK and a `.env` pointing at a key file, but it lacks `yaml`, so broker modules cannot import there. Do not `pip install` into another project's venv. Test through `client_factory`; a real live call is optional evidence, not an AC.
- **Jev retry trap (fixed in #50, keep it fixed).** The SDK's default `RetryPolicy` retries twice inside a 30s budget — at Hermes's 30s `plugins.hook_callback_timeout`. `jev.py::_typesafe_client` passes `RetryPolicy(max_retries=0, timeout=…)` with `DEFAULT_TIMEOUT = 8.0s`. If you change the client construction, keep the single attempt and keep `tests/test_broker_jev.py::test_the_default_client_disables_retries_and_bounds_the_call` green.
- **Hook timeout / cap facts.** `plugins.hook_callback_timeout` default 30s, max 600, `0` disables bounding. `hooks.output_spill.max_chars` default 10,000, `preview_head`/`preview_tail` 500 each, `enabled: false` disables. Hermes abandons a timed-out `pre_llm_call` worker and continues (fail-open).
- **Unsupported delivery paths** (research: [`docs/research/hermes-delivery-paths.md`](research/hermes-delivery-paths.md)): ordinary text is supported end to end; multimodal user content and `codex_app_server` drop the injected sidecar; MoA carries it but participant recovery is unclaimed; a context engine that replaces the request is unknown. Unsupported paths must emit no Intervention.
- **Store smoke test without the full suite:** build a `Broker(store=~/"skill-store", evidence_log=RecordingEvidenceLog(), judgment_source=StubJudgmentSource(claim))` and call `prepare_turn("…", "life-agent", {"session_id": "smoke"})`. A real `life-agent` grant produced an 8,257-char inline Pack.

## Next actions

1. Claim #51: `GH_CONFIG_DIR=~/.config/gh-personal gh issue edit 51 --add-assignee @me`, then read the issue, ADR-0001/B9, and the prototype branch's `plugin/skill-broker-prototype/__init__.py`.
2. Build the Adapter as a Hermes plugin registering exactly `pre_llm_call` (returns `{"context": result.pack}` only when injection is enabled; `None` in shadow) and `pre_api_request` (system-prompt hash, tool count, correlation ids). No tools, no capabilities, no system-prompt section. Read the effective hook cap and spill setting from Hermes config into `HookConfig`.
3. Test at the Hermes seam under an isolated `HERMES_HOME`, control-versus-treatment, byte-comparing the system prompt and tool schema across turns and asserting no Pack content crosses the wire in shadow.
4. Run the full suite, then `code-review` (Standards + Spec), then commit on `main` and close #51 — the pattern used for #45–#50.

## Definition of done

#51 is done when all six acceptance criteria are evidenced by tests that drive the real Hermes seam under an isolated `HERMES_HOME`, the full suite passes, the change is reviewed on both axes, and the commit and issue closure are on `origin/main`. Report any criterion that is only source-verified (not executed) explicitly, as #50 did for its live call.
