#!/usr/bin/env python3
"""Run one pass of the cache-safe intervention proof (wayfinder ticket #6).

This is a THROWAWAY prototype harness. It:

1. spins up an in-process OpenAI-compatible mock provider that captures the
   *raw* request bodies Hermes sends (no model, no network, no cost);
2. creates an isolated ``HERMES_HOME`` and drops in the prototype plugin;
3. loads the plugin through Hermes's real plugin manager;
4. runs two turns of a real ``AIAgent`` against the mock — turn 2 on a fresh
   agent with history reloaded from the session DB, modelling a resumed
   process;
5. writes a JSON report of exactly what went on the wire.

Run it twice (``--mode control`` and ``--mode treatment``) and let
``compare_proof.py`` assert the invariant. ``run.sh`` does both.

Requires the Hermes virtualenv's interpreter, e.g.::

    ~/.hermes/hermes-agent/.venv/bin/python run_proof.py --mode treatment --out /tmp/t.json
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sys
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

HERE = Path(__file__).resolve().parent
HERMES_SRC = Path(os.environ.get("HERMES_AGENT_SRC", "/home/hermes/.hermes/hermes-agent"))
SESSION_ID = "sess-proof"

_CAPTURED: list[dict] = []


class _MockProvider(BaseHTTPRequestHandler):
    """OpenAI-compatible endpoint that records raw request bodies."""

    def do_POST(self):  # noqa: N802 (http.server API)
        length = int(self.headers.get("Content-Length", 0))
        request = json.loads(self.rfile.read(length).decode())
        _CAPTURED.append(request)

        response = {
            "id": "mock",
            "choices": [
                {
                    "index": 0,
                    "finish_reason": "stop",
                    "message": {"role": "assistant", "content": "MOCK_OK"},
                }
            ],
            "usage": {"prompt_tokens": 10, "completion_tokens": 1, "total_tokens": 11},
        }

        if request.get("stream") is True:
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.end_headers()
            for chunk in (
                {"id": "mock", "choices": [{"index": 0, "delta": {"role": "assistant", "content": "MOCK_OK"}, "finish_reason": None}]},
                {"id": "mock", "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}]},
            ):
                self.wfile.write(f"data: {json.dumps(chunk)}\n\n".encode())
            self.wfile.write(b"data: [DONE]\n\n")
            self.wfile.flush()
        else:
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


def _sha(value) -> str:
    """Structured-value hash (JSON-canonical)."""
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()[:16]


def _sha_text(value: str) -> str:
    """Raw-string hash, matching the plugin's evidence handle."""
    return hashlib.sha256(value.encode()).hexdigest()[:16]


def _system_prompt(request: dict):
    for message in request.get("messages", []):
        if message.get("role") == "system":
            return message.get("content")
    return None


def run_mode(mode: str, home: Path | None = None) -> dict:
    """Run one pass and return a report of the wire payloads.

    ``home`` must be the *same* path for control and treatment: Hermes embeds
    HERMES_HOME in the system prompt, so a per-mode path would show up as a
    spurious system-prompt difference.
    """
    if mode not in {"control", "treatment"}:
        raise SystemExit(f"unknown mode: {mode}")

    server = HTTPServer(("127.0.0.1", 0), _MockProvider)
    port = server.server_address[1]
    threading.Thread(target=server.serve_forever, daemon=True).start()

    work = Path(tempfile.mkdtemp(prefix=f"sb_proof_{mode}_work_"))
    hermes_home = home if home is not None else work / "hermes_home"
    shutil.rmtree(hermes_home, ignore_errors=True)
    (hermes_home / "plugins").mkdir(parents=True)

    os.environ["HERMES_HOME"] = str(hermes_home)
    os.environ["HERMES_BUNDLED_PLUGINS"] = str(work / "empty_bundled")
    os.environ.pop("HERMES_ENABLE_PROJECT_PLUGINS", None)
    os.environ["SKILL_BROKER_PROTO_EVIDENCE_DIR"] = str(work / "evidence")

    enabled = "[skill-broker-prototype]" if mode == "treatment" else "[]"
    (hermes_home / "config.yaml").write_text(
        "model:\n"
        "  default: test-model\n"
        "  provider: openai-compat\n"
        f"  base_url: http://127.0.0.1:{port}/v1\n"
        "  api_key: test-key\n"
        "  streaming: false\n"
        "  context_length: 131072\n"
        f"plugins:\n  enabled: {enabled}\n"
    )

    # The plugin is always present; only its enablement differs between modes.
    shutil.copytree(
        HERE / "plugin" / "skill-broker-prototype",
        hermes_home / "plugins" / "skill-broker-prototype",
    )

    from hermes_cli.plugins import get_plugin_manager
    from hermes_state import SessionDB
    from run_agent import AIAgent

    manager = get_plugin_manager()
    manager.discover_and_load()
    loaded = sorted(k for k, plugin in manager._plugins.items() if plugin.enabled)

    session_db = SessionDB(db_path=work / "state.db")
    session_db.create_session(session_id=SESSION_ID, source="cli")

    def make_agent():
        agent = AIAgent(
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
        return agent

    _CAPTURED.clear()
    make_agent().run_conversation(
        "please extract text from this pdf", conversation_history=[], task_id="t1"
    )
    history = session_db.get_messages_as_conversation(SESSION_ID)
    # Fresh agent, history reloaded from the store: models a resumed process.
    make_agent().run_conversation("now do a tdd cycle", conversation_history=history, task_id="t2")

    server.shutdown()
    session_db.close()

    requests = []
    for request in _CAPTURED:
        if "messages" not in request:
            continue  # skip the context-length probe
        system_prompt = _system_prompt(request)
        tools = request.get("tools") or []
        messages = [
            {"role": m.get("role"), "content": m.get("content")}
            for m in request.get("messages", [])
        ]
        requests.append(
            {
                "system_prompt": system_prompt,
                "system_prompt_sha256": _sha_text(system_prompt or ""),
                "tools": tools,
                "tools_sha256": _sha(tools),
                "messages": messages,
                "user_messages": [m["content"] for m in messages if m["role"] == "user"],
            }
        )

    evidence_path = Path(os.environ["SKILL_BROKER_PROTO_EVIDENCE_DIR"]) / "hook_outputs" / "skill-broker-prototype" / "evidence.jsonl"
    evidence = []
    if evidence_path.exists():
        evidence = [json.loads(line) for line in evidence_path.read_text().splitlines() if line.strip()]

    report = {
        "mode": mode,
        "loaded_plugins": loaded,
        "session_id": SESSION_ID,
        "requests": requests,
        "evidence": evidence,
    }
    shutil.rmtree(work, ignore_errors=True)
    if home is not None:
        shutil.rmtree(home, ignore_errors=True)
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=["control", "treatment"], required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument(
        "--home",
        type=Path,
        default=None,
        help="shared HERMES_HOME path (identical across modes so it cannot "
        "show up as a system-prompt difference)",
    )
    args = parser.parse_args()

    sys.path.insert(0, str(HERMES_SRC))
    report = run_mode(args.mode, home=args.home)
    args.out.write_text(json.dumps(report, indent=2))
    print(
        f"[{args.mode}] loaded={report['loaded_plugins']} "
        f"requests={len(report['requests'])} evidence={len(report['evidence'])} "
        f"-> {args.out}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
