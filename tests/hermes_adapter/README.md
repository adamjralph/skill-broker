# The Hermes Adapter Shadow Mode proof (ticket #51)

Executed evidence for the Adapter's acceptance criteria. It is **not** part of the repo's stdlib
`unittest` discovery because it needs the Hermes virtualenv; the pure Adapter seam lives in
`tests/test_broker_adapter.py`.

```sh
~/.hermes/hermes-agent/.venv/bin/python tests/hermes_adapter/run_shadow_proof.py
```

Override the interpreter or Hermes source with `HERMES_PYTHON` / `HERMES_AGENT_SRC`.

One pass runs four real Hermes turns through the real plugin manager against an in-process
OpenAI-compatible mock provider (no model, no network, no cost), sharing one isolated
`HERMES_HOME` so the path cannot show up as a system-prompt difference:

| mode | what it proves |
|---|---|
| `control` | the plugin present but disabled: the baseline wire |
| `treatment` | the plugin enabled in Shadow Mode: byte-identical prompt and tools (control-vs-treatment and across turns), no Pack on the wire, evidence recorded |
| `multimodal` | an unsupported delivery path: no Intervention, the reason recorded, native exposure untouched |
| `codex` | the configured `codex_app_server` route: refused through Hermes's real hook dispatch without spawning codex |
| `failure` | a broken Store: the Adapter swallows the failure and the turn completes normally |

The script asserts the ticket's criteria and prints `VERDICT: PASS`/`FAIL`; it exits non-zero on
any failure. The Adapter itself is `scripts/broker/adapter.py`; the loadable plugin is
`scripts/broker/hermes_plugin/`.
