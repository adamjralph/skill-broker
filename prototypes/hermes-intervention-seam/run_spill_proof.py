#!/usr/bin/env python3
"""THROWAWAY prototype harness for wayfinder ticket #21.

Question: when an oversize Skill Pack is delivered by Hermes's native spill
(head/tail preview + path to the full text), does the *agent* read the spill
path and apply the Pack's instructions — or stall/ignore them?

This harness is the #6 harness with one change: the prototype plugin builds an
**oversize** Pack whose operative instruction sits in the MIDDLE of the Pack,
so it survives only inside the spilled file, never in the head/tail preview.
A per-run random token marks it, so it cannot be guessed or hallucinated.

Two modes:

* ``--mode mock``  — in-process mock provider. FREE: proves the oversize Pack
  really spills, the preview+path reach the wire, and the token is absent from
  the preview. Establishes the setup; says nothing about agent behaviour.
* ``--mode live``  — a real provider/model turn. Observes whether the agent
  reads the spill path and returns the token.

The verdict is behavioural: ``RECOVERED`` requires that a tool call actually
read the spilled file **and** the final answer carries the token. Never claim
recovery from the mock pass, or from source alone.

Requires the Hermes virtualenv's interpreter::

    ~/.hermes/hermes-agent/.venv/bin/python run_spill_proof.py --mode live --out /tmp/spill.json
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import sys
import tempfile
import threading
import uuid
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

HERE = Path(__file__).resolve().parent
HERMES_SRC = Path(os.environ.get("HERMES_AGENT_SRC", "/home/hermes/.hermes/hermes-agent"))
SESSION_ID = "sess-spill"
USER_MESSAGE = "Please extract the text from this PDF. Follow the PDF skill exactly."
MARKER_PREFIX = "SB-RECOVERED-"


class _MockProvider(BaseHTTPRequestHandler):
    """OpenAI-compatible endpoint that records raw request bodies (mock mode)."""

    captured: list[dict] = []

    def do_POST(self):  # noqa: N802 (http.server API)
        length = int(self.headers.get("Content-Length", 0))
        try:
            request = json.loads(self.rfile.read(length).decode())
        except Exception:
            request = {}
        self.captured.append(request)
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


def _read_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def _config_yaml(*, model: str, provider: str, base_url: str, api_key: str, max_chars: int) -> str:
    return (
        "model:\n"
        f"  default: {model}\n"
        f"  provider: {provider}\n"
        f"  base_url: {base_url}\n"
        f"  api_key: {api_key}\n"
        "  streaming: false\n"
        "  context_length: 131072\n"
        "plugins:\n"
        "  enabled: [skill-broker-prototype]\n"
        "hooks:\n"
        "  output_spill:\n"
        "    enabled: true\n"
        f"    max_chars: {max_chars}\n"
        "    preview_head: 500\n"
        "    preview_tail: 500\n"
    )


def run(args) -> dict:
    marker = MARKER_PREFIX + uuid.uuid4().hex[:8]
    work = Path(tempfile.mkdtemp(prefix="sb_spill_"))
    home = work / "hermes_home"
    (home / "plugins").mkdir(parents=True)
    evidence_dir = work / "evidence"

    os.environ["HERMES_HOME"] = str(home)
    os.environ["HERMES_BUNDLED_PLUGINS"] = str(work / "empty_bundled")
    os.environ.pop("HERMES_ENABLE_PROJECT_PLUGINS", None)
    os.environ["SKILL_BROKER_PROTO_EVIDENCE_DIR"] = str(evidence_dir)
    os.environ["SKILL_BROKER_PROTO_PACK_MODE"] = "oversize"
    os.environ["SKILL_BROKER_PROTO_PACK_CHARS"] = str(args.pack_chars)
    os.environ["SKILL_BROKER_PROTO_PACK_MARKER"] = marker
    os.environ["SKILL_BROKER_PROTO_HEAD_NUDGE"] = "1" if args.nudge else "0"
    os.environ["SKILL_BROKER_PROTO_DUMP"] = "1"

    server = None
    if args.mode == "mock":
        server = HTTPServer(("127.0.0.1", 0), _MockProvider)
        _MockProvider.captured = []
        threading.Thread(target=server.serve_forever, daemon=True).start()
        provider, base_url, api_key = (
            "openai-compat",
            f"http://127.0.0.1:{server.server_address[1]}/v1",
            "test-key",
        )
    else:
        provider, base_url, api_key = args.provider, args.base_url, args.api_key

    (home / "config.yaml").write_text(
        _config_yaml(
            model=args.model,
            provider=provider,
            base_url=base_url,
            api_key=api_key,
            max_chars=args.max_chars,
        )
    )
    shutil.copytree(
        HERE / "plugin" / "skill-broker-prototype",
        home / "plugins" / "skill-broker-prototype",
    )

    from hermes_state import SessionDB
    from run_agent import AIAgent

    session_db = SessionDB(db_path=work / "state.db")
    session_db.create_session(session_id=SESSION_ID, source="cli")

    agent = AIAgent(
        api_key=api_key,
        base_url=base_url,
        provider=provider,
        model=args.model,
        max_iterations=args.max_iterations,
        enabled_toolsets=["file"],
        quiet_mode=True,
        skip_context_files=True,
        skip_memory=True,
        save_trajectories=False,
        platform="cli",
        session_db=session_db,
        session_id=SESSION_ID,
    )
    result = agent.run_conversation(USER_MESSAGE, conversation_history=[], task_id="spill")
    final_response = result.get("final_response") or ""
    if server is not None:
        server.shutdown()
    session_db.close()

    ev_dir = evidence_dir / "hook_outputs" / "skill-broker-prototype"
    evidence = _read_jsonl(ev_dir / "evidence.jsonl")
    wire = _read_jsonl(ev_dir / "wire.jsonl")
    tool_calls = _read_jsonl(ev_dir / "tool_calls.jsonl")

    spill_files = sorted(
        path for directory in (home / "hook_outputs").glob("*") for path in directory.glob("*.txt")
    )
    wire_user_text = "\n".join(
        text
        for record in wire
        for text in record.get("user_messages", [])
        if isinstance(text, str)
    )
    tool_args_text = json.dumps(tool_calls)

    # Cross-check the plugin's dump against the mock's raw captured bodies.
    mock_user_text = "\n".join(
        message.get("content", "")
        for request in _MockProvider.captured
        for message in request.get("messages", [])
        if message.get("role") == "user" and isinstance(message.get("content"), str)
    )

    flags = {
        "spill_file_written": bool(spill_files),
        "spill_file_chars": [
            {"path": str(path), "chars": len(path.read_text())} for path in spill_files
        ],
        "preview_on_wire": "output truncat" in wire_user_text,
        "spill_path_on_wire": any(str(path) in wire_user_text for path in spill_files),
        "marker_absent_from_wire": marker not in wire_user_text,
        "marker_present_in_spill_file": any(marker in path.read_text() for path in spill_files),
        "preview_on_raw_mock_wire": (
            ("output truncat" in mock_user_text) if args.mode == "mock" else None
        ),
        "tool_call_read_spill": any(
            str(path) in tool_args_text or "hook_outputs" in tool_args_text for path in spill_files
        ),
        "first_tool_call_reads_spill": bool(
            tool_calls
            and any(
                str(path) in json.dumps(tool_calls[0].get("args"))
                or "hook_outputs" in json.dumps(tool_calls[0].get("args"))
                for path in spill_files
            )
        ),
        "marker_in_final_response": marker in final_response,
        "agent_mentioned_truncation": bool(
            re.search(r"truncat|preview|full content", final_response, re.I)
        ),
    }

    if not flags["spill_file_written"] or not flags["preview_on_wire"]:
        verdict = "VOID — the oversize Pack did not spill; the observation is meaningless"
    elif not flags["marker_absent_from_wire"]:
        verdict = "VOID — the operative token leaked into the preview; not a recovery test"
    elif args.mode == "mock":
        # A mock provider cannot decide to read a file: setup is proven, behaviour is not.
        verdict = "UNOBSERVED — mock pass only; the oversize Pack spilled and reached the wire"
    elif flags["tool_call_read_spill"] and flags["marker_in_final_response"]:
        # Recovery only: whether the agent OBEYED the instruction is judged by hand
        # (a returned token can sit inside a refusal).
        verdict = "RECOVERED — the agent read the spill path and returned the buried token"
    elif flags["tool_call_read_spill"]:
        verdict = "PARTIAL — the agent read the spill path but never returned the buried token"
    elif flags["marker_in_final_response"]:
        verdict = "SUSPECT — token returned without any read of the spill path"
    else:
        verdict = "FAILED — the agent neither read the spill path nor applied the Pack"

    report = {
        "mode": args.mode,
        "ticket": 21,
        "pack_chars_target": args.pack_chars,
        "max_chars": args.max_chars,
        "head_nudge": args.nudge,
        "marker": marker,
        "model": args.model,
        "provider": provider,
        "base_url": base_url,
        "session_id": SESSION_ID,
        "user_message": USER_MESSAGE,
        "final_response": final_response,
        "flags": flags,
        "verdict": verdict,
        "tool_calls": tool_calls,
        "evidence": evidence,
        "wire": wire,
        "work_dir": str(work),
    }
    args.out.write_text(json.dumps(report, indent=2))

    print(f"mode={args.mode} model={args.model} provider={provider} nudge={args.nudge}")
    print(f"marker={marker} pack_chars={args.pack_chars} max_chars={args.max_chars}")
    for key, value in flags.items():
        print(f"  {key}: {value}")
    print(f"final_response: {final_response[:400]!r}")
    print(f"\nVERDICT: {verdict}")
    print(f"artifacts: {work}\nreport: {args.out}")
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=["mock", "live"], default="live")
    parser.add_argument("--model", default="deepseek-v4.1-flash:cloud")
    parser.add_argument("--provider", default="custom")
    parser.add_argument("--base-url", default="http://127.0.0.1:11434/v1")
    parser.add_argument("--api-key", default="ollama-local")
    parser.add_argument("--pack-chars", type=int, default=12000)
    parser.add_argument("--max-chars", type=int, default=10000)
    parser.add_argument("--nudge", action="store_true", help="broker-authored pointer nudge in the Pack head")
    parser.add_argument("--max-iterations", type=int, default=6)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()

    sys.path.insert(0, str(HERMES_SRC))
    run(args)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
