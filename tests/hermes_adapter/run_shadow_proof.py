#!/usr/bin/env python3
"""The Hermes Adapter in Shadow Mode, driven at the real Hermes seam (ticket #51, ADR-0001/B9).

Run it with the Hermes virtualenv's interpreter::

    ~/.hermes/hermes-agent/.venv/bin/python tests/hermes_adapter/run_shadow_proof.py

It spins up an in-process OpenAI-compatible mock provider (no model, no network, no cost),
creates one isolated ``HERMES_HOME`` shared by every mode so the path cannot show up as a
system-prompt difference, and runs real ``AIAgent`` turns through Hermes's real plugin manager:

* ``control``    — the plugin present but disabled.
* ``treatment``  — the plugin enabled, Shadow Mode (injection off).
* ``multimodal`` — the plugin enabled, a multimodal user turn (an unsupported delivery path).
* ``codex``      — the plugin enabled, the ``codex_app_server`` route configured (also unsupported).
* ``failure``    — the plugin enabled over a broken Store (an Adapter failure).

It then asserts the acceptance criteria that are visible at the seam: the system prompt and tool
schema are byte-identical control-versus-treatment and across turns, no Pack content reaches the
wire in Shadow Mode, one evidence record per provider request carries the system-prompt hash, the
tool count and the correlation ids and locates its Route Decision, an unsupported path emits no
Intervention and records why, the effective hook cap is read from the host configuration rather
than written, and the Adapter registers exactly two hooks and nothing else.

The harness is not part of the repo's stdlib ``unittest`` discovery because it needs the Hermes
interpreter; it is the executed evidence for this ticket.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
HERMES_SRC = Path(os.environ.get("HERMES_AGENT_SRC", "/home/hermes/.hermes/hermes-agent"))
HERMES_PYTHON = os.environ.get(
    "HERMES_PYTHON", str(HERMES_SRC / ".venv" / "bin" / "python"))
PLUGIN_SOURCE = REPO / "scripts" / "broker" / "hermes_plugin"
PLUGIN_NAME = "skill-broker"
SESSION_ID = "sess-adapter-proof"
PACK_MARKER = "SKILL-BROKER PACK"
HOOK_CAP = 12345
MODES = ("control", "treatment", "multimodal", "codex", "failure")
TURN_ONE = "please extract text from this pdf"
TURN_TWO = "now do a tdd cycle"

_CAPTURED: list[dict] = []


class _MockProvider(BaseHTTPRequestHandler):
    """OpenAI-compatible endpoint that records every raw request body."""

    def do_POST(self):  # noqa: N802 (http.server API)
        length = int(self.headers.get("Content-Length", 0))
        request = json.loads(self.rfile.read(length).decode())
        _CAPTURED.append(request)
        response = {
            "id": "mock",
            "choices": [{"index": 0, "finish_reason": "stop",
                         "message": {"role": "assistant", "content": "MOCK_OK"}}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 1, "total_tokens": 11},
        }
        body = json.dumps(response).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):  # noqa: N802
        body = json.dumps(
            {"object": "list", "data": [{"id": "test-model", "object": "model"}]}
        ).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args, **kwargs):
        pass


def _sha_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _sha_structured(value) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode("utf-8")).hexdigest()


def _system_prompt(request: dict):
    for message in request.get("messages", []):
        if message.get("role") == "system":
            return message.get("content")
    return None


def _write_config(home: Path, *, port: int, store: Path, evidence: Path, enabled: bool,
                  provider: str = "openai-compat", openai_runtime: str = "") -> str:
    plugin_entry = {
        "settings": {
            "store": str(store),
            "profile": "broker-test",
            "evidence_dir": str(evidence),
            "judgment": "first_candidate",
            "inject": False,
        }
    }
    model = {
        "default": "test-model",
        "provider": provider,
        "base_url": f"http://127.0.0.1:{port}/v1",
        "api_key": "test-key",
        "streaming": False,
        "context_length": 131072,
    }
    if openai_runtime:
        model["openai_runtime"] = openai_runtime
    config = {
        "model": model,
        "hooks": {"output_spill": {"enabled": True, "max_chars": HOOK_CAP}},
        "plugins": {
            "enabled": [PLUGIN_NAME] if enabled else [],
            "entries": {PLUGIN_NAME: plugin_entry},
        },
    }
    # Hand-render a small, deterministic YAML so the harness has no yaml dependency of its own.
    import yaml

    text = yaml.safe_dump(config, sort_keys=False)
    (home / "config.yaml").write_text(text)
    return text


def run_mode(mode: str, *, root: Path, store: Path, home: Path) -> dict:
    """Run one mode's Hermes turns and return everything observable at the seam."""
    sys.path.insert(0, str(REPO / "scripts"))
    sys.path.insert(0, str(REPO / "tests"))
    sys.path.insert(0, str(HERMES_SRC))

    server = HTTPServer(("127.0.0.1", 0), _MockProvider)
    port = server.server_address[1]
    threading.Thread(target=server.serve_forever, daemon=True).start()

    shutil.rmtree(home, ignore_errors=True)
    (home / "plugins").mkdir(parents=True)
    shutil.copytree(PLUGIN_SOURCE, home / "plugins" / PLUGIN_NAME)
    evidence = root / "evidence" / mode
    evidence.mkdir(parents=True, exist_ok=True)
    codex = mode == "codex"
    config_before = _write_config(
        home, port=port, store=store, evidence=evidence, enabled=mode != "control",
        provider="openai" if codex else "openai-compat",
        openai_runtime="codex_app_server" if codex else "")

    os.environ["HERMES_HOME"] = str(home)
    os.environ["HERMES_BUNDLED_PLUGINS"] = str(root / "empty_bundled")
    os.environ.pop("HERMES_ENABLE_PROJECT_PLUGINS", None)

    from hermes_cli.plugins import get_plugin_manager
    from hermes_state import SessionDB
    from run_agent import AIAgent

    manager = get_plugin_manager()
    manager.discover_and_load()

    session_db = SessionDB(db_path=root / f"state-{mode}.db")
    session_db.create_session(session_id=SESSION_ID, source="cli")

    def make_agent():
        return AIAgent(
            api_key="test-key",
            base_url=f"http://127.0.0.1:{port}/v1",
            provider="openai-compat",
            model="test-model",
            max_iterations=10,
            enabled_toolsets=["file", "web"],
            quiet_mode=True,
            skip_context_files=True,
            skip_memory=True,
            save_trajectories=False,
            platform="cli",
            session_db=session_db,
            session_id=SESSION_ID,
        )

    _CAPTURED.clear()
    hook_returns: list = []
    if codex:
        # The app-server route hands the turn to the codex subprocess, which would spend real
        # credentials; drive the registered seam directly through Hermes's real hook dispatch
        # instead, so the Adapter's classification is exercised without spawning codex.
        from hermes_cli.lifecycle import invoke_hook

        hook_returns = list(invoke_hook("pre_llm_call", session_id=SESSION_ID, task_id="t1",
                                        turn_id="turn-codex-1", user_message=TURN_ONE))
    elif mode == "multimodal":
        make_agent().run_conversation(
            [{"type": "text", "text": TURN_ONE}], conversation_history=[], task_id="t1")
    else:
        make_agent().run_conversation(TURN_ONE, conversation_history=[], task_id="t1")
        history = session_db.get_messages_as_conversation(SESSION_ID)
        # A fresh agent with history reloaded from the store models a resumed process.
        make_agent().run_conversation(TURN_TWO, conversation_history=history, task_id="t2")

    server.shutdown()
    session_db.close()

    requests = []
    for request in _CAPTURED:
        if "messages" not in request:
            continue  # skip the context-length probe
        system_prompt = _system_prompt(request)
        tools = request.get("tools") or []
        requests.append({
            "system_prompt": system_prompt,
            "system_prompt_sha256": _sha_text(system_prompt or ""),
            "tools_sha256": _sha_structured(tools),
            "tool_count": len(tools),
            "user_messages": [m.get("content") for m in request.get("messages", [])
                              if m.get("role") == "user"],
        })

    def read_records(path: Path) -> list[dict]:
        if not path.exists():
            return []
        return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]

    loaded = sorted(key for key, plugin in manager._plugins.items() if plugin.enabled)
    report = {
        "mode": mode,
        "loaded_plugins": loaded,
        "requests": requests,
        "evidence": read_records(evidence / "api_requests.jsonl"),
        "decisions": read_records(evidence / "route_decisions.jsonl"),
        "registered": {
            "hooks": sorted(manager._hooks),
            "hook_counts": {name: len(cbs) for name, cbs in manager._hooks.items()},
            "tool_names": sorted(manager._plugin_tool_names),
            "system_prompt_sections": sorted(manager._system_prompt_sections),
        },
        "hook_cap": HOOK_CAP,
        "config_unchanged": (home / "config.yaml").read_text() == config_before,
        "hook_returns": hook_returns,
    }
    return report


def _decision_lookup(decisions: list[dict]) -> dict[str, dict]:
    return {decision["route_decision_id"]: decision for decision in decisions}


def assert_proof(reports: dict[str, dict]) -> list[str]:
    """Return the list of failures; empty means every invariant held."""
    failures: list[str] = []
    failures += _check_cache_safety(reports["control"], reports["treatment"])
    failures += _check_shadow_suppression(reports)
    failures += _check_evidence(reports)
    failures += _check_unsupported_paths(reports)
    failures += _check_failure_tolerance(reports)
    failures += _check_registration(reports)
    failures += _check_runtime_config(reports)
    return failures


def _check_cache_safety(control: dict, treatment: dict) -> list[str]:
    """Criterion 1: the system prompt and tool schema are byte-identical, and stable."""
    failures: list[str] = []
    if not control["requests"] or not treatment["requests"]:
        return ["both control and treatment must capture chat requests"]
    if control["loaded_plugins"]:
        failures.append(f"control must load no plugins; loaded {control['loaded_plugins']}")
    if treatment["loaded_plugins"] != [PLUGIN_NAME]:
        failures.append(f"treatment must load {PLUGIN_NAME}; loaded {treatment['loaded_plugins']}")
    if len(control["requests"]) != len(treatment["requests"]):
        failures.append("control and treatment captured a different number of requests")
    for index, (before, after) in enumerate(zip(control["requests"], treatment["requests"])):
        if before["system_prompt"] != after["system_prompt"]:
            failures.append(f"request {index}: system prompt differs control vs treatment")
        if before["system_prompt_sha256"] != after["system_prompt_sha256"]:
            failures.append(f"request {index}: system-prompt hash differs control vs treatment")
        if before["tools_sha256"] != after["tools_sha256"]:
            failures.append(f"request {index}: tool schema differs control vs treatment")
    for field, label in (("system_prompt_sha256", "system prompt"), ("tools_sha256", "tool schema")):
        if len({request[field] for request in treatment["requests"]}) != 1:
            failures.append(f"treatment: the {label} is not byte-identical across turns")
    return failures


def _check_shadow_suppression(reports: dict[str, dict]) -> list[str]:
    """Criterion 2: no Pack content on the wire in Shadow Mode, though a Pack was built."""
    failures: list[str] = []
    for mode in ("control", "treatment", "multimodal"):
        for request in reports[mode]["requests"]:
            for message in request["user_messages"]:
                if message is not None and PACK_MARKER in json.dumps(message):
                    failures.append(f"{mode}: Pack content reached the wire")
    if not any(decision["grants"] and decision["pack_sha256"]
               for decision in reports["treatment"]["decisions"]):
        failures.append("treatment: no Pack was built, so suppression is not evidenced")
    return failures


def _check_evidence(reports: dict[str, dict]) -> list[str]:
    """Criterion 3: one evidence record per request, locating its Route Decision."""
    failures: list[str] = []
    treatment = reports["treatment"]
    if len(treatment["evidence"]) != len(treatment["requests"]):
        failures.append(f"treatment: {len(treatment['evidence'])} evidence records for "
                        f"{len(treatment['requests'])} provider requests")
    decisions = _decision_lookup(treatment["decisions"])
    for record in treatment["evidence"]:
        if not record["route_decision_id"] or record["route_decision_id"] not in decisions:
            failures.append(f"evidence record {record['api_request_id']} locates no Route Decision")
        if not all((record["session_id"], record["task_id"], record["turn_id"],
                    record["api_request_id"])):
            failures.append(f"evidence record {record['api_request_id']} is missing correlation ids")
    for request, record in zip(treatment["requests"], treatment["evidence"]):
        if record["system_prompt_sha256"] != request["system_prompt_sha256"]:
            failures.append("an evidence record's system-prompt hash does not match the wire")
        if record["tool_count"] != request["tool_count"]:
            failures.append("an evidence record's tool count does not match the wire")
    multimodal_decisions = _decision_lookup(reports["multimodal"]["decisions"])
    for record in reports["multimodal"]["evidence"]:
        if not record["route_decision_id"] or record["route_decision_id"] not in multimodal_decisions:
            failures.append("a multimodal evidence record locates no Route Decision")
    return failures


def _check_unsupported_paths(reports: dict[str, dict]) -> list[str]:
    """Criterion 4 / B10: multimodal and the app-server route refuse and record why."""
    failures: list[str] = []
    control_prompts = {request["system_prompt_sha256"] for request in reports["control"]["requests"]}
    multimodal = reports["multimodal"]
    if {request["system_prompt_sha256"] for request in multimodal["requests"]} != control_prompts:
        failures.append("multimodal: the system prompt moved on an unsupported path")
    unsupported = [decision for decision in multimodal["decisions"]
                   if "delivery_path_unsupported" in decision["reasons"]]
    if not unsupported:
        failures.append("multimodal: no Route Decision recorded the unsupported path")
    elif unsupported[-1]["delivery_path"] != "multimodal":
        failures.append("multimodal: the recorded delivery path is not multimodal")
    elif unsupported[-1]["grants"] or unsupported[-1]["pack_sha256"]:
        failures.append("multimodal: an unsupported path still granted or built a Pack")

    codex = reports["codex"]
    if any(result for result in codex["hook_returns"]):
        failures.append("codex: the app-server route emitted an Intervention")
    codex_unsupported = [decision for decision in codex["decisions"]
                         if "delivery_path_unsupported" in decision["reasons"]]
    if not codex_unsupported:
        failures.append("codex: no Route Decision recorded the app-server route")
    elif codex_unsupported[-1]["delivery_path"] != "codex_app_server":
        failures.append("codex: the recorded delivery path is not codex_app_server")
    elif codex_unsupported[-1]["grants"] or codex_unsupported[-1]["pack_sha256"]:
        failures.append("codex: an unsupported path still granted or built a Pack")
    return failures


def _check_failure_tolerance(reports: dict[str, dict]) -> list[str]:
    """Criterion 5: an Adapter failure leaves ordinary Hermes operation intact."""
    failures: list[str] = []
    control, failure = reports["control"], reports["failure"]
    control_prompts = {request["system_prompt_sha256"] for request in control["requests"]}
    if len(failure["requests"]) != len(control["requests"]):
        failures.append("failure: the turn did not complete as it does with the plugin disabled")
    elif {request["system_prompt_sha256"] for request in failure["requests"]} != control_prompts:
        failures.append("failure: the system prompt moved when the Adapter failed")
    for request in failure["requests"]:
        for message in request["user_messages"]:
            if message is not None and PACK_MARKER in json.dumps(message):
                failures.append("failure: Pack content reached the wire after an Adapter failure")
    if failure["decisions"]:
        failures.append("failure: a failed Adapter still recorded a Route Decision")
    return failures


def _check_registration(reports: dict[str, dict]) -> list[str]:
    """Criterion 6 / user story 46: exactly two hooks, no tools, no prompt section."""
    failures: list[str] = []
    for mode in ("control", "treatment", "multimodal", "codex", "failure"):
        registered = reports[mode]["registered"]
        if mode == "control":
            if registered["hooks"] or registered["tool_names"] or registered["system_prompt_sections"]:
                failures.append("control: a disabled plugin still registered something")
            continue
        if registered["hooks"] != ["pre_api_request", "pre_llm_call"]:
            failures.append(f"{mode}: registered hooks are {registered['hooks']}")
        if any(count != 1 for count in registered["hook_counts"].values()):
            failures.append(f"{mode}: a hook has more than one callback")
        if registered["tool_names"]:
            failures.append(f"{mode}: the Adapter registered tools {registered['tool_names']}")
        if registered["system_prompt_sections"]:
            failures.append(f"{mode}: the Adapter registered a system-prompt section")
    return failures


def _check_runtime_config(reports: dict[str, dict]) -> list[str]:
    """Criterion 6: the effective hook cap is read at runtime, not written to configuration."""
    failures: list[str] = []
    for mode in ("treatment", "multimodal", "codex", "failure"):
        if not reports[mode]["config_unchanged"]:
            failures.append(f"{mode}: the Adapter rewrote config.yaml")
    for mode in ("treatment", "multimodal", "codex"):
        caps = {decision["budget"]["hook_cap"] for decision in reports[mode]["decisions"]
                if decision.get("budget")}
        if caps != {HOOK_CAP}:
            failures.append(f"{mode}: recorded hook caps are {caps}, expected {{{HOOK_CAP}}}")
    return failures


def _run_suite(work: Path) -> int:
    store_root = work / "store"
    store_meta = json.dumps({
        "local/writing-method": {"name": "writing-method", "aliases": [], "dependencies": []},
        "nousresearch/hermes-agent": {"name": "hermes-agent", "aliases": [],
                                      "dependencies": []},
    }, indent=2, sort_keys=True) + "\n"
    (store_root / "local" / "writing-method").mkdir(parents=True)
    (store_root / "nousresearch" / "hermes-agent").mkdir(parents=True)
    (store_root / "local" / "writing-method" / "SKILL.md").write_text(
        "---\nname: writing-method\ndescription: Use when writing or editing prose.\n"
        "metadata:\n  hermes:\n    tags: [editing, prose]\n---\n\n# Writing method\n\n"
        "A deterministic writing procedure.\n")
    (store_root / "nousresearch" / "hermes-agent" / "SKILL.md").write_text(
        "---\nname: hermes-agent\ndescription: Use when operating the Hermes agent loop.\n"
        "metadata:\n  hermes:\n    tags: [agent, configuration]\n---\n\n# Hermes agent\n\n"
        "Operating the Hermes agent loop.\n")
    (store_root / "store-meta.json").write_text(store_meta)
    policy = {
        "policy_version": 1,
        "profile": "broker-test",
        "foundation": [{"id": "nousresearch.hermes-agent"}],
        "brokered": ["local.writing-method"],
        "preferred": [],
        "denied": [],
    }
    (store_root / "policies").mkdir()
    (store_root / "policies" / "broker-test.json").write_text(
        json.dumps(policy, indent=2, sort_keys=True) + "\n")

    sys.path.insert(0, str(REPO / "scripts"))
    import store_manifest as sm

    sm.generate(store_root)

    home = work / "hermes_home"
    broken_store = work / "broken_store"
    (broken_store / "store-manifest.json").mkdir(parents=True)
    reports: dict[str, dict] = {}
    for mode in MODES:
        out = work / f"report-{mode}.json"
        store = broken_store if mode == "failure" else store_root
        proc = subprocess.run(
            [HERMES_PYTHON, str(HERE / "run_shadow_proof.py"), "--mode", mode,
             "--root", str(work), "--store", str(store), "--home", str(home),
             "--out", str(out)],
            cwd=str(REPO), capture_output=True, text=True)
        if proc.returncode != 0:
            print(f"[{mode}] child failed (rc={proc.returncode})")
            print(proc.stdout)
            print(proc.stderr)
            return 1
        reports[mode] = json.loads(out.read_text())

    failures = assert_proof(reports)
    print("=== Hermes Adapter Shadow Mode proof ===")
    for mode in MODES:
        report = reports[mode]
        print(f"  {mode:10s} plugins={report['loaded_plugins']} "
              f"requests={len(report['requests'])} evidence={len(report['evidence'])} "
              f"decisions={len(report['decisions'])} hooks={report['registered']['hooks']}")
    for index, control_request in enumerate(reports["control"]["requests"]):
        treatment_request = reports["treatment"]["requests"][index]
        print(f"  turn {index}: control sha={control_request['system_prompt_sha256'][:16]} "
              f"tools={control_request['tool_count']} | "
              f"treatment sha={treatment_request['system_prompt_sha256'][:16]} "
              f"tools={treatment_request['tool_count']}")
    if failures:
        for failure in failures:
            print(f"  FAIL  {failure}")
        print("VERDICT: FAIL")
        return 1
    print(f"  PASS  {len(reports['treatment']['evidence'])} evidence records, each locating its "
          f"Route Decision")
    print(f"  PASS  hook cap {HOOK_CAP} read from the host config, which is unchanged")
    print("VERDICT: PASS")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=MODES, default=None)
    parser.add_argument("--root", type=Path, default=None)
    parser.add_argument("--store", type=Path, default=None)
    parser.add_argument("--home", type=Path, default=None)
    parser.add_argument("--out", type=Path, default=None)
    args = parser.parse_args()

    if args.mode is not None:
        # A child run: drive Hermes and write the report. The parent removes the work tree.
        report = run_mode(args.mode, root=args.root, store=args.store, home=args.home)
        args.out.write_text(json.dumps(report, indent=2))
        return 0

    work = Path(tempfile.mkdtemp(prefix="sb_adapter_proof_"))
    try:
        return _run_suite(work)
    finally:
        shutil.rmtree(work, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
