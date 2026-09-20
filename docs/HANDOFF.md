# Skill Broker handoff

Updated: 2026-09-21 08:10 AEST (verified against the working tree, Git, GitHub, and the store)
Next objective: build **[#56 — The Shadow Report and batch review](https://github.com/adamjralph/skill-broker/issues/56)** (Stage 6) on top of #55, with the pilot-gating tickets [#53](https://github.com/adamjralph/skill-broker/issues/53) and [#52](https://github.com/adamjralph/skill-broker/issues/52).

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

**Git.** Branch `main`, HEAD is the #54 Replay Corpus commit on top of `b3b2ab9` (the #51 Adapter). Recent broker commits:
`47e6e00` #48 Judgment/Grant, `e19deea` #49 Pack, `46c436c` #50 live Jev, `b3b2ab9` #51 Adapter, `21e3638` #54 Replay Corpus, then #55 Offline evaluation.

**Tests.** `python3 -m unittest discover -s tests` from the repo root → **295 tests, all passing**. No packaging, no third-party deps beyond PyYAML for the store tooling. The Adapter's Hermes-seam proof is separate (it needs the Hermes venv): `~/.hermes/hermes-agent/.venv/bin/python tests/hermes_adapter/run_shadow_proof.py` → `VERDICT: PASS`.

**Broker modules** (`scripts/broker/`): `broker.py` (`Broker.prepare_turn`, the only external seam), `closure.py` (`authorised_closure`, `dependency_closure`), `retrieval.py` (BM25 candidate ranking), `metadata.py` (frontmatter retrieval metadata), `judgment.py` (`JudgmentCall`, sources, `validate`, `grant`, `FallbackJudgmentSource`, `FirstCandidateJudgmentSource`, SHA-bound `Recording`), `jev.py` (`JevJudgmentSource`, `live_judgment_source`), `pack.py` (`HookConfig`, `Budget`, `build_pack`, Pack Delivery), `evidence.py` (`EvidenceLog`, `JsonlEvidenceLog`, `SessionLedger`, `append_jsonl`), `types.py` (`RouteDecision`, `InterventionResult`, `PackDelivery`, …), `adapter.py` (the Hermes Adapter and its API-request evidence handle), `corpus.py` (Replay Corpus extraction, the machine-local Case store, the committed reviewed-label and Recording stores), `evaluation.py` (offline replay over Reviewed Cases and Recordings, per-split metrics, retrieval/Judgment/Grant attribution, and the committed pre-registration of Soft Thresholds), `hermes_plugin/` (the loadable `skill-broker` plugin). The CLIs are `scripts/corpus.py` and `scripts/evaluate.py`.

**Closed tickets.** #45–#55 (module home, seam, retrieval, Judgment/Grant, Pack, live Jev, Hermes Adapter in Shadow Mode, Replay Corpus and Reviewed Cases, offline routing evaluation and pre-registered Soft Thresholds).

**Skill Store.** `~/skill-store` (repo `adamjralph/skill-store`): **415 identities**, manifest verifies clean; two policies `life-agent.json` and `stillroom-signal-generator.json`. Verified this session:

```
python3 scripts/store_manifest.py verify --store ~/skill-store      # ok
python3 scripts/profile_policy.py validate --store ~/skill-store    # 2 policies
```

**Cutover.** `life-agent` is live, zero-delta, rollback rehearsed; farm at `~/.local/share/skill-broker/farms/life-agent`.

**Open frontier** (open, no open blocker, unassigned): **#52**, #53, #56, #59. Gated: #57←#52+#53+#56, #58←#57. Spec order favours #56 next (#55, its Stage 5 blocker, just landed).

**Evidence from #51.** The Adapter is built (`scripts/broker/adapter.py`) and loadable as the `skill-broker` Hermes plugin (`scripts/broker/hermes_plugin/`). The executed Hermes-seam proof runs five modes against a mock provider under one isolated `HERMES_HOME` and passes: byte-identical system prompt and tool schema control-vs-treatment and across turns, no Pack on the wire in Shadow Mode while a Pack is built, one evidence record per provider request locating its Route Decision, multimodal and `codex_app_server` refused with the reason recorded, an Adapter failure swallowed, and the effective hook cap read from config (which is unchanged). Injection remains **off** by default.

**Evidence from #54.** `scripts/broker/corpus.py` implements extraction from a Hermes `state.db` (`read_turns`), redaction of secrets/identifiers at extraction, the machine-local `CorpusStore` (refusing a root inside the repo, and refusing any source not opted in), the committed `LabelStore` (hand-corrected `no_skill` or closure-authorised Primary only), the SHA-bound `RecordingStore` bound to the Case by content hash, the disjoint threshold/held-out `split_for`, `profile_status` against the provisional Stage 5 minimum, and `retention_report` for the Stage 5/6 review. `scripts/corpus.py` exposes `extract`/`status`/`retention`/`review`/`prelabel`/`record`/`purge`. A real-machine smoke run extracted the pilot `stillroom-signal-generator` into a throwaway corpus: 4 Cases (cli/tool), 4 redactions, refused `desktop` (9) and `kanban` (14); the store/root DBs are never pooled and an untagged turn belongs only to the default profile. No real corpus content or labels were created.

**Evidence from #55.** `scripts/broker/evaluation.py` replays every Reviewed Case through the real `Broker` against its bound `Recording` with no network and no model, reporting per profile and per split: precision, recall, correct no-skill rate, unauthorised-grant rate, closure/dependency/cycle failures, average Candidate count, average Pack size, duplicate-injection rate and hash agreement, plus retrieval/Judgment/Grant attribution so a wrong outcome is explained. The held-out set is reported separately and `derive_thresholds` reads only the threshold set. `ThresholdRegistry` pre-registers the Soft Thresholds in `corpus/thresholds/<profile>.json`, bound to a digest of the reviewed corpus + Recording claims and the measured baseline, refusing a move unless re-derived against a strictly larger reviewed set. `below_minimum` requires the threshold-setting set itself to reach the Stage 5 minimum, so a profile with enough total cases but too few tuning cases stays in Shadow Mode. `scripts/evaluate.py` exposes `evaluate`/`register`. A real-machine smoke run against the empty pilot corpus wrote nothing and reported `stage: shadow`.

## In progress and pending

Nothing is half-written. #55 is complete and committed; the next build is unattempted:

- **#56 The Shadow Report and batch review** (Stage 6) — consumes the evaluation and pre-registration #55 produces.
- **#53 Decide where Brokered withholding is enforced** (spec B16 open decision; gates the pilot).
- **#52 Hard gates, fail-closed, and the per-batch injection switch** — unblocked by #51.
- **#59 Fix the project-setup skills block requirement** (independent, small).

The Adapter's settings are `store`, `profile`, `evidence_dir`, `judgment` (`live` | `recorded` | `first_candidate` | `no_skill`) and `inject` (default false). Route Decisions are appended to `<evidence_dir>/route_decisions.jsonl`; the API-request evidence handle to `<evidence_dir>/api_requests.jsonl`.

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

1. Claim #56: `GH_CONFIG_DIR=~/.config/gh-personal gh issue edit 56 --add-assignee @me`, then read the issue, ADR-0019, and the evaluation #55 delivered.
2. Populate the corpus for the pilot profile (extraction + Adam's hand review), freeze Recordings, then run `scripts/evaluate.py evaluate` and `register` so the Soft Thresholds are pre-registered before any Shadow Report review.
3. Before the pilot: #53 decides where Brokered withholding is enforced (B16) and #52 builds the hard gates and the injection switch on top of #51.
4. Run the full suite plus `tests/hermes_adapter/run_shadow_proof.py`, then `code-review` (Standards + Spec), then commit on `main` and close the issue — the pattern used for #45–#55.

## Definition of done

#55 is done: all six acceptance criteria are covered by `tests/test_broker_evaluation.py`
(25 tests) — model-free replay from Reviewed Cases and Recordings, the per-profile metrics, the
held-out split reported separately and never read by `derive_thresholds`, the pre-registration
bound to the corpus digest and baseline (and movable only by a larger re-derivation), the
retrieval/Judgment/Grant attribution, and the thin-tuning-profile Shadow Mode report. The full
295-test suite passes, the change is reviewed on both axes, and the commit and issue closure are
on `main`. The registration artifact is the pre-registration record; wiring the values into the
store's Profile Policy is the reviewed Stage 5/6 change (ADR-0018/0019), and the pilot corpus
still needs population and Adam's hand review before real thresholds can be set.
