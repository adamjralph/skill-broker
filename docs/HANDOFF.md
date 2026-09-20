# Skill Broker handoff

Updated: 2026-09-21 07:28 AEST (verified against the working tree, Git, GitHub, and the store)
Next objective: the bounded intervention pilot **[#57](https://github.com/adamjralph/skill-broker/issues/57)**. The withholding decision **[#53](https://github.com/adamjralph/skill-broker/issues/53)** (ADR-0021) is made and implemented; **[#52](https://github.com/adamjralph/skill-broker/issues/52)** (Hard Gates, fail-closed, the per-batch switch) and **[#56](https://github.com/adamjralph/skill-broker/issues/56)** (the Shadow Report and batch review) are built.

## Start here

Suggested skills, in order: `implement` → `tdd` (at the Hermes seam) → `code-review`.
Consult `domain-modeling` before touching `CONTEXT.md`, and the repo's agent docs:

- [`AGENTS.md`](../AGENTS.md) — project rules, graft usage, issue-tracker/triage/domain doc pointers
- [`CONTEXT.md`](../CONTEXT.md) — the domain language; names in code must match it
- [`docs/agents/issue-tracker.md`](agents/issue-tracker.md) — `GH_CONFIG_DIR=~/.config/gh-personal` for every `gh` call; claim/close conventions
- [`PROJECT-OUTLINE.md`](../PROJECT-OUTLINE.md) — the outline of record

Authoritative spec and decisions:

- issue **#44** — the accepted spec (store half built; broker half in progress). It carries the stage map, the `B1`–`B17` implementation decisions, and the testing seams.
- ADRs in [`docs/adr/`](adr/) — the ones that bind the broker: [`0001`](adr/0001-one-external-seam-two-internal-seams.md) (one external seam, two internal), [`0004`](adr/0004-jev-judges-relevance-code-grants.md), [`0006`](adr/0006-v1-judgment-is-single-primary-plus-dependencies.md), [`0010`](adr/0010-pack-delivery-under-the-hook-cap.md), [`0013`](adr/0013-candidate-retrieval-is-lexical-and-jev-returns-one-choice.md), [`0014`](adr/0014-authority-is-per-turn-and-the-session-ledger-is-not-a-lease.md), [`0015`](adr/0015-the-evidence-log-records-metadata-and-hashes-not-request-text.md), [`0018`](adr/0018-profile-policy-is-authored-in-the-store-and-manifests-are-derived.md), [`0019`](adr/0019-injection-gate-is-precision-first-and-fails-closed.md), [`0020`](adr/0020-the-replay-corpus-is-opted-in-local-and-hand-reviewed.md), [`0021`](adr/0021-brokered-withholding-is-a-cutover-disabled-list-edit.md).
- For code context, use the graph first: `graft map`, `graft ask "<question>" --source`, `graft callers <symbol>`. Refresh with `graft build` after changes.

## Verified current state

**Git.** Branch `main`, HEAD is the #54 Replay Corpus commit on top of `b3b2ab9` (the #51 Adapter). Recent broker commits:
`47e6e00` #48 Judgment/Grant, `e19deea` #49 Pack, `46c436c` #50 live Jev, `b3b2ab9` #51 Adapter, `21e3638` #54 Replay Corpus, then #55 Offline evaluation.

**Tests.** `python3 -m unittest discover -s tests` from the repo root → **368 tests, all passing**. No packaging, no third-party deps beyond PyYAML for the store tooling. The Adapter's Hermes-seam proof is separate (it needs the Hermes venv): `~/.hermes/hermes-agent/.venv/bin/python tests/hermes_adapter/run_shadow_proof.py` → `VERDICT: PASS`.

**Broker modules** (`scripts/broker/`): `broker.py` (`Broker.prepare_turn`, the only external seam), `closure.py` (`authorised_closure`, `dependency_closure`), `retrieval.py` (BM25 candidate ranking), `metadata.py` (frontmatter retrieval metadata), `judgment.py` (`JudgmentCall`, sources, `validate`, `grant`, `FallbackJudgmentSource`, `FirstCandidateJudgmentSource`, SHA-bound `Recording`), `jev.py` (`JevJudgmentSource`, `live_judgment_source`), `pack.py` (`HookConfig`, `Budget`, `build_pack`, Pack Delivery), `evidence.py` (`EvidenceLog`, `JsonlEvidenceLog`, `SessionLedger`, `append_jsonl`), `types.py` (`RouteDecision`, `InterventionResult`, `PackDelivery`, …), `adapter.py` (the Hermes Adapter and its API-request evidence handle), `corpus.py` (Replay Corpus extraction, the machine-local Case store, the committed reviewed-label and Recording stores), `evaluation.py` (offline replay over Reviewed Cases and Recordings, per-split metrics, retrieval/Judgment/Grant attribution, the committed pre-registration of Soft Thresholds, and `soft_threshold_regression`), `gate.py` (the Hard Gates, fail-closed Incidents, the per-batch switch, and the prompt/tool-schema baseline), `report.py` (the Shadow Report and its batch review), `hermes_plugin/` (the loadable `skill-broker` plugin). The CLIs are `scripts/corpus.py`, `scripts/evaluate.py`, `scripts/gate.py` and `scripts/shadow_report.py`.

**Closed tickets.** #45–#55 (module home, seam, retrieval, Judgment/Grant, Pack, live Jev, Hermes Adapter in Shadow Mode, Replay Corpus and Reviewed Cases, offline routing evaluation and pre-registered Soft Thresholds) #52 (Hard Gates, fail-closed, the per-batch injection switch), #53 (Brokered withholding decided and implemented, ADR-0021) and #56 (the Shadow Report and batch review).

**Skill Store.** `~/skill-store` (repo `adamjralph/skill-store`): **415 identities**, manifest verifies clean; two policies `life-agent.json` and `stillroom-signal-generator.json`. Verified this session:

```
python3 scripts/store_manifest.py verify --store ~/skill-store      # ok
python3 scripts/profile_policy.py validate --store ~/skill-store    # 2 policies
```

**Cutover.** `life-agent` is live, zero-delta, rollback rehearsed; farm at `~/.local/share/skill-broker/farms/life-agent`. Brokered withholding is a Cutover `skills.disabled` edit (ADR-0021): a pilot rehearsal applied the five Brokered Names to a byte-copy of `stillroom-signal-generator`'s config, verified the gate, and rolled the config back byte-for-byte.

**Open frontier** (open, no open blocker, unassigned): #59. Gated: #57←#52+#53+#56 (all closed), #58←#57. #53 (B16) is decided and implemented, so the pilot is unblocked.

**Evidence from #51.** The Adapter is built (`scripts/broker/adapter.py`) and loadable as the `skill-broker` Hermes plugin (`scripts/broker/hermes_plugin/`). The executed Hermes-seam proof runs five modes against a mock provider under one isolated `HERMES_HOME` and passes: byte-identical system prompt and tool schema control-vs-treatment and across turns, no Pack on the wire in Shadow Mode while a Pack is built, one evidence record per provider request locating its Route Decision, multimodal and `codex_app_server` refused with the reason recorded, an Adapter failure swallowed, and the effective hook cap read from config (which is unchanged). Injection remains **off** by default.

**Evidence from #54.** `scripts/broker/corpus.py` implements extraction from a Hermes `state.db` (`read_turns`), redaction of secrets/identifiers at extraction, the machine-local `CorpusStore` (refusing a root inside the repo, and refusing any source not opted in), the committed `LabelStore` (hand-corrected `no_skill` or closure-authorised Primary only), the SHA-bound `RecordingStore` bound to the Case by content hash, the disjoint threshold/held-out `split_for`, `profile_status` against the provisional Stage 5 minimum, and `retention_report` for the Stage 5/6 review. `scripts/corpus.py` exposes `extract`/`status`/`retention`/`review`/`prelabel`/`record`/`purge`. A real-machine smoke run extracted the pilot `stillroom-signal-generator` into a throwaway corpus: 4 Cases (cli/tool), 4 redactions, refused `desktop` (9) and `kanban` (14); the store/root DBs are never pooled and an untagged turn belongs only to the default profile. No real corpus content or labels were created.

**Evidence from #52.** `scripts/broker/gate.py` implements the injection gate. `check_hard_gates` is the data-independent check over a Route Decision — an unauthorised Grant, a foundation-resolution regression, broker/native hash disagreement, a Judgment validation failure and an incomplete Dependency Closure — and `InjectionGate` is the durable per-batch switch: `enable(batch, review)` refuses a review that does not name the batch or does not post-date a breach, `fail_closed` records an append-only Incident, turns injection off and marks the profile review-required, and `check_soft_thresholds`/`note_expansion_regression` block expansion while leaving a running injection on. `Broker` consults the gate on every turn: a breach reverts the turn to foundation-only and records the Incident; a failed-closed profile short-circuits to `injection_failed_closed`. The Adapter consults `gate.injecting` before delivering (the per-batch switch is authoritative over the legacy `inject` flag) and checks the system-prompt hash, the tool count and the tool-schema hash against the conversation baseline on every provider request, failing the profile closed on a mutation. `scripts/gate.py` exposes `status`/`enable`/`disable`/`regression`/`clear-regression`/`incidents`. `soft_threshold_regression` (quality floors only) lives in `gate.py` and is re-exported from `evaluation.py`. 41 tests in `tests/test_broker_gate.py`.

**Evidence from #56.** `scripts/broker/report.py` implements the Shadow Report. `build_shadow_report` joins the recorded Route Decisions from the Evidence Log with the `skill_view` use read from the profile's `state.db` (`corpus.read_skill_use`), windows on the session turn's timestamp, and assembles a `ShadowReport`: the profile, the exact Brokered Allowlist as the `InjectionBatch` that enabling would cover, each Hard Gate's status via `check_hard_gates` plus the Incident log, movement against the pre-registered Soft Thresholds via `soft_threshold_regression`, and every turn where the broker differed from actual use or human review with the retrieval/Judgment/Grant attribution. `apply_review` is the only path to `InjectionGate`: approve enables the report's batch, `approve_narrower` enables a proper subset of it, and reject records the review and enables nothing, leaving Shadow Mode running. An approval is refused unless the Soft Thresholds are pre-registered, every checked Hard Gate passed, and no quality floor regressed (a regression also records the expansion block). The review is bound to the report by `report_sha256`. `scripts/shadow_report.py` exposes `build`/`show`/`review`. 27 tests in `tests/test_broker_report.py`.

**Evidence from #55.** `scripts/broker/evaluation.py` replays every Reviewed Case through the real `Broker` against its bound `Recording` with no network and no model, reporting per profile and per split: precision, recall, correct no-skill rate, unauthorised-grant rate, closure/dependency/cycle failures, average Candidate count, average Pack size, duplicate-injection rate and hash agreement, plus retrieval/Judgment/Grant attribution so a wrong outcome is explained. The held-out set is reported separately and `derive_thresholds` reads only the threshold set. `ThresholdRegistry` pre-registers the Soft Thresholds in `corpus/thresholds/<profile>.json`, bound to a digest of the reviewed corpus + Recording claims and the measured baseline, refusing a move unless re-derived against a strictly larger reviewed set. `below_minimum` requires the threshold-setting set itself to reach the Stage 5 minimum, so a profile with enough total cases but too few tuning cases stays in Shadow Mode. `scripts/evaluate.py` exposes `evaluate`/`register`. A real-machine smoke run against the empty pilot corpus wrote nothing and reported `stage: shadow`.

## In progress and pending

Nothing is half-written. #56 and #53 are complete and committed; the next builds are:

- **#57 The bounded intervention pilot** — populate the corpus, run Shadow Mode, review a report, enable one batch.
- **#59 Fix the project-setup skills block requirement** (independent, small).

The Adapter's settings are `store`, `profile`, `evidence_dir`, `judgment` (`live` | `recorded` | `first_candidate` | `no_skill`) and `inject` (deprecated; the per-batch gate state under `evidence_dir` is authoritative). Route Decisions are appended to `<evidence_dir>/route_decisions.jsonl`; the API-request evidence handle to `<evidence_dir>/api_requests.jsonl`; the gate state to `<evidence_dir>/gate.json`; Incidents to `<evidence_dir>/gate_incidents.jsonl`.

## Decisions and boundaries

- **Injection is off by default.** Shadow Mode runs the whole pipeline at the real seam and returns no context. Enabling a batch needs a reviewed Shadow Report and Adam's explicit approval (ADR-0019).
- **Migration is additive and reversible.** No Skill, source path, or Consumer path is deleted without explicit approval; Stage 9 Retirement is not authorised.
- **Main stays docs/spec/tooling.** Prototypes live on throwaway branches. `prototype/hermes-intervention-seam` is out of main.
- **Authority.** Only deterministic code mints a Grant; Jev never grants. The Session Ledger is dedup evidence, never a lease. The Evidence Log holds metadata and hashes — no request text, no Skill bodies (ADR-0015).
- **Jev prompt/criteria engineering is carried fog** (B5, spec "Out of Scope"). #50 delivered a working, untuned source; do not tune it here.
- **B16 is resolved (ADR-0021):** Brokered withholding is a Cutover edit to the Consumer's `skills.disabled`; hard-gate item 5′ is checked per batch at Cutover, not per turn. The Adapter keeps its no-write, no-index hook surface. The Adapter's packaging/distribution/enablement stays carried fog, now unentangled with withholding.
- **Falsification condition.** If Reviewed Cases require two independent primaries, ADR-0006's single-primary deferral is lifted by a new ticket, not a silent change.
- **Uncommitted local state — leave it:** `.gitignore` and `AGENTS.md` carry graft additions; `.ignore`, `opencode.json` and `prototypes/` are untracked. Not part of any ticket. Do not commit or clean without Adam.

## Blockers and troubleshooting

- **Live Jev cannot be called from the broker repo's Python.** `typesafe_sdk` is absent and no key is exported. The agent-workflow-lab venv `/home/hermes/Projects/agent-workflow-lab/.venv` (Python 3.11) has the SDK and a `.env` pointing at a key file, but it lacks `yaml`, so broker modules cannot import there. Do not `pip install` into another project's venv. Test through `client_factory`; a real live call is optional evidence, not an AC.
- **Jev retry trap (fixed in #50, keep it fixed).** The SDK's default `RetryPolicy` retries twice inside a 30s budget — at Hermes's 30s `plugins.hook_callback_timeout`. `jev.py::_typesafe_client` passes `RetryPolicy(max_retries=0, timeout=…)` with `DEFAULT_TIMEOUT = 8.0s`. If you change the client construction, keep the single attempt and keep `tests/test_broker_jev.py::test_the_default_client_disables_retries_and_bounds_the_call` green.
- **Hook timeout / cap facts.** `plugins.hook_callback_timeout` default 30s, max 600, `0` disables bounding. `hooks.output_spill.max_chars` default 10,000, `preview_head`/`preview_tail` 500 each, `enabled: false` disables. Hermes abandons a timed-out `pre_llm_call` worker and continues (fail-open).
- **Unsupported delivery paths** (research: [`docs/research/hermes-delivery-paths.md`](research/hermes-delivery-paths.md)): ordinary text is supported end to end; multimodal user content and `codex_app_server` drop the injected sidecar; MoA carries it but participant recovery is unclaimed; a context engine that replaces the request is unknown. Unsupported paths must emit no Intervention.
- **Store smoke test without the full suite:** build a `Broker(store=~/"skill-store", evidence_log=RecordingEvidenceLog(), judgment_source=StubJudgmentSource(claim))` and call `prepare_turn("…", "life-agent", {"session_id": "smoke"})`. A real `life-agent` grant produced an 8,257-char inline Pack.

## Next actions

1. Populate the corpus for the pilot profile (extraction + Adam's hand review), freeze Recordings, then run `scripts/evaluate.py evaluate` and `register` so the Soft Thresholds are pre-registered.
2. Run Shadow Mode over real traffic, build a Shadow Report with `scripts/shadow_report.py build`, review it, and only then enable the pilot batch with `review` (#57).
3. Run the full suite plus `tests/hermes_adapter/run_shadow_proof.py`, then `code-review` (Standards + Spec), then commit on `main` and close the issue — the pattern used for #45–#56.

## Definition of done

#53 is done: spec B16's open decision is recorded as ADR-0021 — both options assessed against
the live `stillroom-signal-generator` configuration, Cutover `skills.disabled` chosen, hard-gate
item 5′ placed at Cutover and checked per batch, the Adapter's packaging carried as fog, and
reversibility recorded — and implemented in `scripts/cutover.py` (withholding applied and
gate-checked against the withheld index, byte-exact rollback), five new tests in
`tests/test_cutover.py`, and a real byte-copy of the pilot's config applied, verified and rolled
back byte-for-byte.

#56 is done: all six acceptance criteria are covered by `tests/test_broker_report.py` (27 tests)
— the report assembled from recorded Route Decisions plus observed skill use over a bounded
window, the profile and exact Brokered Allowlist batch named, each Hard Gate's status and
Soft-Threshold movement reported, attributed disagreements against actual use and human review,
the review (approve / reject / approve-narrower) as the only path to the injection switch and
bound to the report digest, and a rejection enabling nothing while Shadow Mode runs. The full
368-test suite passes, and the change is reviewed on both axes before committing on `main`.
