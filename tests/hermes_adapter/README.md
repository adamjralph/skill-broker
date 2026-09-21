# The Hermes-seam proofs (tickets #51 and #57)

Executed evidence that needs the Hermes virtualenv, so they are **not** part of the repo's stdlib
`unittest` discovery. The pure Adapter seam lives in `tests/test_broker_adapter.py`; the pilot
proof's acceptance logic lives in `tests/test_pilot_proof.py`.

```sh
# The Adapter in Shadow Mode (#51, ADR-0001/B9)
~/.hermes/hermes-agent/.venv/bin/python tests/hermes_adapter/run_shadow_proof.py

# The bounded intervention pilot, rehearsed (#57, ADR-0019/0021)
~/.hermes/hermes-agent/.venv/bin/python tests/hermes_adapter/run_pilot_proof.py
```

Override the interpreter or Hermes source with `HERMES_PYTHON` / `HERMES_AGENT_SRC`. The pilot
proof also reads `SKILL_BROKER_STORE`, `SKILL_BROKER_PILOT_PROFILE` and
`SKILL_BROKER_PILOT_CONFIG` for the real store, profile and profile config it rehearses over.

Both harnesses drive real `AIAgent` turns through the real plugin manager against an in-process
OpenAI-compatible mock provider (no model, no network, no cost), sharing one isolated
`HERMES_HOME` so the path cannot show up as a system-prompt difference. They print
`VERDICT: PASS`/`FAIL` and exit non-zero on any failure.

## `run_shadow_proof.py` — the Adapter (#51)

Five modes under one mock provider:

| mode | what it proves |
|---|---|
| `control` | the plugin present but disabled: the baseline wire |
| `treatment` | the plugin enabled in Shadow Mode: byte-identical prompt and tools (control-vs-treatment and across turns), no Pack on the wire, evidence recorded |
| `multimodal` | an unsupported delivery path: no Intervention, the reason recorded, native exposure untouched |
| `codex` | the configured `codex_app_server` route: refused through Hermes's real hook dispatch without spawning codex |
| `failure` | a broken Store: the Adapter swallows the failure and the turn completes normally |

## `run_pilot_proof.py` — the bounded pilot, rehearsed (#57)

A deterministic rehearsal of the whole pilot lifecycle against the **real** Skill Store and the
real Profile Policy; no live Consumer state is touched. The Cutover runs on a byte-copy of the
pilot profile's `config.yaml`, and the Hermes seam runs under an isolated `HERMES_HOME`:

1. **Cutover** — the Exposure Farm is wired into `skills.external_dirs`, exactly the Brokered
   Names are withheld in `skills.disabled`, the policy gate passes, and rollback restores the
   original bytes.
2. **Shadow Mode** — a Pack is built for a request that warrants a Brokered Skill but no Pack
   content reaches the wire.
3. **The review** — a Shadow Report assembled from the recorded Route Decisions is approved,
   enabling exactly one profile and one batch.
4. **The pilot** — the same seam with the batch enabled: a real turn receives a Pack carrying the
   granted Skill and its Dependency Closure, the system prompt and tool schema stay byte-identical
   to the plugin-disabled control, the repeat turn suppresses a second injection while the
   supplied content stays in context, and every provider request locates its Route Decision.
5. **Rollback** — disabling the batch returns the profile to foundation-only.

The Adapter is `scripts/broker/adapter.py`; the loadable plugin is
`scripts/broker/hermes_plugin/`. Live enablement for a Consumer remains an operator step (ADR-0019).
