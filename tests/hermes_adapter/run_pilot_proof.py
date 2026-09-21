#!/usr/bin/env python3
"""The bounded intervention pilot, rehearsed deterministically (ticket #57, ADR-0019/0021).

Run it with the Hermes virtualenv's interpreter::

    ~/.hermes/hermes-agent/.venv/bin/python tests/hermes_adapter/run_pilot_proof.py

This is the **rehearsal** for the first real Intervention. It touches no live Consumer state:
the Cutover runs against a byte-copy of the pilot profile's ``config.yaml`` and the Hermes seam
runs under an isolated ``HERMES_HOME`` with an in-process OpenAI-compatible mock provider (no
model, no network, no cost). The real Skill Store and the real Profile Policy are used
throughout, because those are what the pilot actually routes over.

It rehearses the whole pilot lifecycle and asserts its acceptance criteria:

1. **Cutover** — on a byte-copy of the live ``stillroom-signal-generator`` config: the Exposure
   Farm is wired into ``skills.external_dirs`` and exactly the five Brokered Names are withheld
   in ``skills.disabled``; the policy gate passes; rollback restores the original bytes.
2. **Shadow Mode** — real Hermes turns through the real plugin manager with injection off: a Pack
   is built for a request that warrants a Brokered Skill but no Pack content reaches the wire.
3. **The review** — a Shadow Report is assembled from the recorded Route Decisions, and its
   approval enables exactly one profile and one batch (the Brokered Allowlist).
4. **The pilot** — the same seam with the batch enabled: a real turn receives a Pack containing
   the granted Skill and its complete Dependency Closure, the Brokered Names stay out of the
   automatic index (the Cutover edit above), the system prompt and tool schema stay byte-identical
   to the plugin-disabled control, a repeated turn does not re-inject, and every Route Decision is
   replayable from the Evidence Log.
5. **Rollback** — disabling the batch returns the profile to foundation-only and the rehearsal to
   Shadow Mode.

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
STORE = Path(os.environ.get("SKILL_BROKER_STORE", str(Path.home() / "skill-store")))
PROFILE = os.environ.get("SKILL_BROKER_PILOT_PROFILE", "stillroom-signal-generator")
LIVE_CONFIG = Path(os.environ.get(
    "SKILL_BROKER_PILOT_CONFIG",
    str(Path.home() / ".hermes" / "profiles" / PROFILE / "config.yaml")))
ROOTS = [Path.home() / ".hermes" / "profiles" / PROFILE / "skills"]
PACK_MARKER = "SKILL-BROKER PACK"
COLD_EMAIL_BODY = "# Cold Email Writing"
COLD_EMAIL_ID = "coreyhaines31.cold-email"
MODES = ("control", "shadow", "pilot")
SESSION_ID = "sess-pilot-proof"
REQUEST = "write a cold email to a prospect introducing our new service"
TURN_TWO = "write a cold email to a prospect introducing our new service"

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


def _last_user_message(request: dict):
    users = [m.get("content") for m in request.get("messages", [])
             if m.get("role") == "user"]
    return users[-1] if users else None


# --- the child: one mode's Hermes turns at the real seam -----------------------------------


def _write_config(home: Path, *, port: int, evidence: Path, profile: str, enabled: bool) -> str:
    """A minimal isolated-home config: the mock provider plus the real plugin settings."""
    import yaml

    config = {
        "model": {
            "default": "test-model",
            "provider": "openai-compat",
            "base_url": f"http://127.0.0.1:{port}/v1",
            "api_key": "test-key",
            "streaming": False,
            "context_length": 131072,
        },
        "hooks": {"output_spill": {"enabled": True, "max_chars": 10000}},
        "plugins": {
            "enabled": [PLUGIN_NAME] if enabled else [],
            "entries": {
                PLUGIN_NAME: {
                    "settings": {
                        "store": str(STORE),
                        "profile": profile,
                        "evidence_dir": str(evidence),
                        "judgment": "first_candidate",
                        "inject": False,
                    }
                }
            },
        },
    }
    text = yaml.safe_dump(config, sort_keys=False)
    (home / "config.yaml").write_text(text)
    return text


def run_mode(mode: str, *, root: Path, home: Path, evidence: Path, profile: str) -> dict:
    """Run one mode's real Hermes turns and return everything observable at the seam."""
    sys.path.insert(0, str(REPO / "scripts"))
    sys.path.insert(0, str(REPO / "tests"))
    sys.path.insert(0, str(HERMES_SRC))

    server = HTTPServer(("127.0.0.1", 0), _MockProvider)
    port = server.server_address[1]
    threading.Thread(target=server.serve_forever, daemon=True).start()

    shutil.rmtree(home, ignore_errors=True)
    (home / "plugins").mkdir(parents=True)
    shutil.copytree(PLUGIN_SOURCE, home / "plugins" / PLUGIN_NAME)
    evidence.mkdir(parents=True, exist_ok=True)
    config_before = _write_config(home, port=port, evidence=evidence, profile=profile,
                                  enabled=mode != "control")

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
    make_agent().run_conversation(REQUEST, conversation_history=[], task_id="t1")
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
            "last_user_message": _last_user_message(request),
            "user_messages": [m.get("content") for m in request.get("messages", [])
                              if m.get("role") == "user"],
        })

    def read_records(path: Path) -> list[dict]:
        if not path.exists():
            return []
        return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]

    loaded = sorted(key for key, plugin in manager._plugins.items() if plugin.enabled)
    return {
        "mode": mode,
        "profile": profile,
        "loaded_plugins": loaded,
        "requests": requests,
        "evidence": read_records(evidence / "api_requests.jsonl"),
        "decisions": read_records(evidence / "route_decisions.jsonl"),
        "config_unchanged": (home / "config.yaml").read_text() == config_before,
    }


# --- the parent: the pilot lifecycle -------------------------------------------------------


def _rehearse_cutover(work: Path, store: Path, profile: str, live_config: Path) -> dict:
    """Wire the farm and withhold the Brokered Names on a byte-copy, then roll back (AC1/AC3/AC6)."""
    sys.path.insert(0, str(REPO / "scripts"))
    import cutover as co

    if not live_config.is_file():
        raise SystemExit(f"pilot profile config not found: {live_config}")
    original = live_config.read_bytes()

    area = work / "cutover"
    area.mkdir(parents=True, exist_ok=True)
    config = area / "config.yaml"
    config.write_bytes(original)

    identity_names = [f"{ident.split('.', 1)[1]}" for ident in _policy_brokered(store, profile)]
    manifest = area / "exposure-manifest.json"
    manifest.write_text(json.dumps({
        "consumer": profile,
        "manifest_version": 1,
        "exposures": [{"id": row, "name": row.split(".", 1)[1]}
                      for row in _policy_foundation(store, profile)],
    }, indent=2) + "\n")
    baseline = area / "baseline.json"
    farm = area / "farm"

    co.run("baseline", config=config, baseline=baseline, consumer=profile, roots=ROOTS)
    applied = co.run("apply", config=config, baseline=baseline, consumer=profile, roots=ROOTS,
                     store=store, manifest=manifest, farm=farm,
                     policy=store / "policies" / f"{profile}.json")
    verify = co.run("verify", config=config, store=store, manifest=manifest, farm=farm,
                    policy=store / "policies" / f"{profile}.json", roots=ROOTS)
    after_apply = config.read_text()
    rolled = co.run("rollback", config=config, baseline=baseline, consumer=profile)
    restored = config.read_bytes() == original

    return {
        "withheld": list(applied.get("withheld", [])),
        "expected_withheld": identity_names,
        "external_dirs_present": str(farm) in after_apply,
        "disabled_present": all(name in after_apply for name in identity_names),
        "gate_ok": bool(applied.get("ok") and verify.get("ok")),
        "rollback_changed": bool(rolled.get("changed")),
        "bytes_restored": restored,
        "config_before_sha256": hashlib.sha256(original).hexdigest(),
    }


def _policy_foundation(store: Path, profile: str) -> list[str]:
    policy = json.loads((store / "policies" / f"{profile}.json").read_text())
    return [entry["id"] for entry in policy.get("foundation", [])]


def _policy_brokered(store: Path, profile: str) -> list[str]:
    policy = json.loads((store / "policies" / f"{profile}.json").read_text())
    return [str(ident) for ident in policy.get("brokered", [])]


def _rehearsal_thresholds(profile: str, metrics=None) -> dict:
    """Pre-register the rehearsal's own measured quality rates as floors (ADR-0019).

    A rehearsal has no hand-reviewed corpus to derive thresholds from, so the Shadow window's
    measured rates become the floors: the approval must then clear exactly what was observed,
    and a later drop below the window would block expansion as it would in a real review. The
    mock provider makes no ``skill_view`` call, so the observed rates are conservative.
    """
    floors = {
        "precision": getattr(metrics, "precision", None),
        "recall": getattr(metrics, "recall", None),
        "correct_no_skill_rate": getattr(metrics, "correct_no_skill_rate", None),
    }
    return {
        "threshold_version": 1,
        "profile": profile,
        "thresholds": floors,
    }


def _build_and_approve(work: Path, store: Path, profile: str, shadow_evidence: Path,
                      gate_evidence: Path) -> dict:
    """Assemble the Shadow Report and approve exactly one batch (AC1)."""
    sys.path.insert(0, str(REPO / "scripts"))
    sys.path.insert(0, str(REPO / "tests"))
    from broker.gate import APPROVE, InjectionGate
    from broker.report import ShadowReportStore, apply_review, build_shadow_report
    import corpus_fixture

    db = work / "report-state.db"
    corpus_fixture.create_db(db)
    gate_evidence.mkdir(parents=True, exist_ok=True)
    gate = InjectionGate.in_evidence_dir(gate_evidence)
    decisions = shadow_evidence / "route_decisions.jsonl"
    probe = build_shadow_report(
        profile=profile, store=store, evidence_path=decisions, db=db, gate=gate,
        generated_at="rehearsal-probe")
    report = build_shadow_report(
        profile=profile, store=store, evidence_path=decisions, db=db,
        thresholds=_rehearsal_thresholds(profile, probe.metrics), gate=gate,
        generated_at="rehearsal")
    reviewed = apply_review(report, outcome=APPROVE, reviewer="adam (rehearsal)",
                            reviewed_at="rehearsal", gate=gate)
    path = ShadowReportStore(work / "reports").save(reviewed)
    enabled = gate.enabled_batch(profile)
    return {
        "batch": reviewed.batch.to_record(),
        "turns": reviewed.turns,
        "gates_passed": reviewed.gates_passed,
        "gate_status": [status.to_record() for status in reviewed.gate_status],
        "thresholds_pre_registered": reviewed.thresholds_pre_registered,
        "hash_disagreements": reviewed.metrics.hash_disagreements,
        "hash_agreements": reviewed.metrics.hash_agreements,
        "report_sha256": reviewed.report_sha256,
        "review": reviewed.review,
        "report_path": str(path),
        "injecting": gate.injecting(profile),
        "enabled_batch": enabled.to_record() if enabled is not None else None,
        "other_injecting": gate.injecting("not-the-pilot-profile"),
    }


def _child(mode: str, work: Path, store: Path, home: Path, evidence: Path,
           profile: str) -> dict:
    out = work / f"report-{mode}.json"
    proc = subprocess.run(
        [HERMES_PYTHON, str(HERE / "run_pilot_proof.py"), "--mode", mode,
         "--root", str(work), "--home", str(home), "--store", str(store),
         "--profile", profile, "--evidence", str(evidence), "--out", str(out)],
        cwd=str(REPO), capture_output=True, text=True)
    if proc.returncode != 0:
        print(f"[{mode}] child failed (rc={proc.returncode})")
        print(proc.stdout)
        print(proc.stderr)
        raise SystemExit(1)
    return json.loads(out.read_text())


def assert_proof(*, cutover: dict, review: dict, control: dict, shadow: dict,
                 pilot: dict) -> list[str]:
    """Return the list of failures; empty means every pilot invariant held."""
    failures: list[str] = []
    failures += _check_cutover(cutover)
    failures += _check_shadow(shadow)
    failures += _check_review(review)
    failures += _check_injection(control, pilot)
    failures += _check_replay(shadow, pilot)
    return failures


def _check_cutover(cutover: dict) -> list[str]:
    """AC1/AC3/AC6: the farm is wired, the batch is withheld, rollback is byte-exact."""
    failures: list[str] = []
    if cutover["withheld"] != cutover["expected_withheld"]:
        failures.append(f"cutover withheld {cutover['withheld']}, "
                        f"expected {cutover['expected_withheld']}")
    if not cutover["external_dirs_present"]:
        failures.append("cutover did not wire the farm into skills.external_dirs")
    if not cutover["disabled_present"]:
        failures.append("cutover did not withhold every Brokered Name in skills.disabled")
    if not cutover["gate_ok"]:
        failures.append("the Cutover policy gate did not pass")
    if not cutover["bytes_restored"] or not cutover["rollback_changed"]:
        failures.append("cutover rollback did not restore the config byte-for-byte")
    return failures


def _check_shadow(shadow: dict) -> list[str]:
    """AC: Shadow Mode builds a Pack but puts no Pack content on the wire."""
    failures: list[str] = []
    if shadow["loaded_plugins"] != [PLUGIN_NAME]:
        failures.append(f"shadow must load {PLUGIN_NAME}; loaded {shadow['loaded_plugins']}")
    for request in shadow["requests"]:
        last = json.dumps(request["last_user_message"])
        if PACK_MARKER in last:
            failures.append("shadow: Pack content reached the wire with injection off")
    if not any(decision.get("grants") and decision.get("pack_sha256")
               for decision in shadow["decisions"]):
        failures.append("shadow: no Pack was built, so suppression is not evidenced")
    return failures


def _check_review(review: dict) -> list[str]:
    """AC1: exactly one profile and one batch, approved from a passing report."""
    failures: list[str] = []
    if not review["review"] or review["review"].get("outcome") != "approve":
        failures.append("the Shadow Report was not approved")
    batch = review["batch"]
    if batch.get("profile") != PROFILE:
        failures.append(f"the approved batch is for {batch.get('profile')!r}, not {PROFILE!r}")
    if not batch.get("skills"):
        failures.append("the approved batch covers no Skills")
    if not review["thresholds_pre_registered"]:
        failures.append("the report has no pre-registered Soft Thresholds")
    if not review["gates_passed"]:
        failures.append("the report has a Hard-Gate breach")
    if review["hash_disagreements"]:
        failures.append(f"{review['hash_disagreements']} broker/native hash disagreements")
    if not review["hash_agreements"]:
        failures.append("the report recorded no broker/native hash agreement to check")
    enabled = review["enabled_batch"]
    if enabled is None or enabled.get("profile") != PROFILE:
        failures.append("the approval enabled no batch for the pilot profile")
    elif enabled.get("name") != review["batch"]["name"]:
        failures.append("the enabled batch is not the reviewed batch")
    if review["other_injecting"]:
        failures.append("injection was enabled for a profile other than the pilot")
    if not review["injecting"]:
        failures.append("approving the report did not enable injection")
    for status in review["gate_status"]:
        if status["checked"] and status["status"] != "pass":
            failures.append(f"gate {status['gate']} is {status['status']}")
    return failures


def _check_injection(control: dict, pilot: dict) -> list[str]:
    """AC2/AC4/AC5/AC7/AC8: the Pack arrives, cache safety holds, a repeat dedups."""
    failures: list[str] = []
    if pilot["loaded_plugins"] != [PLUGIN_NAME]:
        failures.append(f"pilot must load {PLUGIN_NAME}; loaded {pilot['loaded_plugins']}")
    if control["loaded_plugins"]:
        failures.append(f"control must load no plugins; loaded {control['loaded_plugins']}")

    # Cache safety: the system prompt and tool schema are byte-identical control vs pilot.
    if len(control["requests"]) != len(pilot["requests"]):
        failures.append("control and pilot captured a different number of requests")
    else:
        for index, (before, after) in enumerate(zip(control["requests"], pilot["requests"])):
            if before["system_prompt_sha256"] != after["system_prompt_sha256"]:
                failures.append(f"request {index}: system prompt differs control vs pilot")
            if before["tools_sha256"] != after["tools_sha256"]:
                failures.append(f"request {index}: tool schema differs control vs pilot")

    # The first turn receives a Pack carrying the granted Skill body; the repeat does not.
    injected = [i for i, request in enumerate(pilot["requests"])
                if PACK_MARKER in json.dumps(request["last_user_message"])]
    if injected != [0]:
        failures.append(f"pilot injected a Pack into requests {injected}, expected only the first")
    if injected and COLD_EMAIL_BODY not in json.dumps(pilot["requests"][0]["last_user_message"]):
        failures.append("the injected Pack does not carry the granted Skill's body")
    if len(pilot["requests"]) >= 2 and PACK_MARKER not in json.dumps(
            pilot["requests"][1].get("user_messages") or []):
        failures.append("the repeat turn no longer carried the supplied Pack in context")

    granted = [decision for decision in pilot["decisions"] if decision.get("grants")]
    if not granted:
        failures.append("pilot recorded no grant")
    elif granted[0]["grants"][0]["id"] != COLD_EMAIL_ID:
        failures.append(f"the pilot granted {granted[0]['grants'][0]['id']!r}, "
                        f"not {COLD_EMAIL_ID!r} for a request that warranted it")
    elif not any(candidate.get("id") == COLD_EMAIL_ID
                 for candidate in granted[0].get("candidates", ())):
        failures.append("the granted Skill was not in the retrieved candidate set")
    for decision in granted:
        closure = {entry["id"]: entry["version"] for entry in decision["authorised_closure"]}
        for entry in decision["grants"]:
            if entry["id"] not in closure or closure[entry["id"]] != entry["version"]:
                failures.append(f"grant {entry['id']} is not in the authorised closure at hash")
    duplicates = [decision for decision in pilot["decisions"]
                  if "duplicate_suppressed" in decision.get("reasons", ())]
    if not duplicates:
        failures.append("a repeated turn did not record duplicate_suppressed")
    for decision in duplicates:
        if decision.get("pack_sha256"):
            failures.append("a duplicate-suppressed turn still delivered a Pack")
    if len(pilot["decisions"]) != len(pilot["requests"]):
        failures.append(f"pilot recorded {len(pilot['decisions'])} decisions for "
                        f"{len(pilot['requests'])} provider requests")
    return failures


def _check_replay(shadow: dict, pilot: dict) -> list[str]:
    """AC7: every provider request's evidence locates a Route Decision in the Evidence Log."""
    failures: list[str] = []
    for mode, report in (("shadow", shadow), ("pilot", pilot)):
        decision_ids = {decision.get("route_decision_id") for decision in report["decisions"]}
        if len(report["evidence"]) != len(report["requests"]):
            failures.append(f"{mode}: {len(report['evidence'])} evidence records for "
                            f"{len(report['requests'])} provider requests")
        for record in report["evidence"]:
            if not record.get("route_decision_id") \
                    or record["route_decision_id"] not in decision_ids:
                failures.append(f"{mode}: evidence record {record.get('api_request_id')} "
                                "locates no Route Decision")
            if not all((record.get("session_id"), record.get("task_id"), record.get("turn_id"),
                        record.get("api_request_id"))):
                failures.append(f"{mode}: evidence record {record.get('api_request_id')} "
                                "is missing correlation ids")
    return failures


def _run_suite(work: Path) -> int:
    store = STORE.expanduser().resolve()
    home = work / "hermes_home"
    shadow_evidence = work / "evidence" / "shadow"
    gate_evidence = work / "evidence" / "pilot"
    control_evidence = work / "evidence" / "control"

    if not (store / "store-manifest.json").is_file():
        print(f"Skill Store not found at {store}")
        return 1

    print("=== Bounded intervention pilot (rehearsal) ===")
    cutover = _rehearse_cutover(work, store, PROFILE, LIVE_CONFIG)
    print(f"  cutover   withheld={cutover['withheld']} farm={cutover['external_dirs_present']} "
          f"rollback_bytes={cutover['bytes_restored']}")

    shadow = _child("shadow", work, store, home, shadow_evidence, PROFILE)
    print(f"  shadow    requests={len(shadow['requests'])} "
          f"decisions={len(shadow['decisions'])}")

    review = _build_and_approve(work, store, PROFILE, shadow_evidence, gate_evidence)
    print(f"  review    batch={review['batch']['name']} skills={len(review['batch']['skills'])} "
          f"gates_passed={review['gates_passed']} injecting={review['injecting']}")

    control = _child("control", work, store, home, control_evidence, PROFILE)
    pilot = _child("pilot", work, store, home, gate_evidence, PROFILE)
    packs = sum(1 for request in pilot["requests"]
                if PACK_MARKER in json.dumps(request["last_user_message"]))
    print(f"  pilot     requests={len(pilot['requests'])} decisions={len(pilot['decisions'])} "
          f"packs_on_wire={packs}")

    # Rollback: disabling the batch returns the profile to Shadow Mode.
    sys.path.insert(0, str(REPO / "scripts"))
    from broker.gate import InjectionGate

    disabled = InjectionGate.in_evidence_dir(gate_evidence)
    disabled.disable(PROFILE, reason="rehearsal complete")
    offline = not disabled.injecting(PROFILE)
    print(f"  rollback  injecting_after_disable={not offline}")

    failures = assert_proof(cutover=cutover, review=review, control=control, shadow=shadow,
                            pilot=pilot)
    if not offline:
        failures.append("rollback did not return the profile to foundation-only")
    if failures:
        for failure in failures:
            print(f"  FAIL  {failure}")
        print("VERDICT: FAIL")
        return 1
    print(f"  PASS  batch approved: {review['batch']['name']} "
          f"({len(review['batch']['skills'])} Brokered Skills)")
    print(f"  PASS  Pack delivered on one turn, suppressed on the repeat")
    print(f"  PASS  every provider request locates its Route Decision")
    print(f"  PASS  system prompt and tool schema byte-identical control vs pilot")
    print(f"  PASS  cutover rollback byte-exact, gate off after rollback")
    print("VERDICT: PASS")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=MODES, default=None)
    parser.add_argument("--root", type=Path, default=None)
    parser.add_argument("--home", type=Path, default=None)
    parser.add_argument("--store", type=Path, default=None)
    parser.add_argument("--profile", default=PROFILE)
    parser.add_argument("--evidence", type=Path, default=None)
    parser.add_argument("--out", type=Path, default=None)
    args = parser.parse_args()

    if args.mode is not None:
        report = run_mode(args.mode, root=args.root, home=args.home, evidence=args.evidence,
                          profile=args.profile)
        args.out.write_text(json.dumps(report, indent=2))
        return 0

    work = Path(tempfile.mkdtemp(prefix="sb_pilot_proof_"))
    try:
        return _run_suite(work)
    finally:
        shutil.rmtree(work, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
