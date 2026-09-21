#!/usr/bin/env bash
# Enable the bounded intervention pilot for one Consumer (ticket #57), Phases 0-3 of
# docs/runbooks/live-pilot.md. Dry-run by default: nothing live changes until `--apply`.
#
#   bash docs/runbooks/pilot-enable.sh --check      # read-only pre-flight (default)
#   bash docs/runbooks/pilot-enable.sh --apply      # manifest + plugin + config + Cutover
#   bash docs/runbooks/pilot-enable.sh --rollback   # disable the batch, roll the Cutover back
#
# Environment overrides: PILOT_PROFILE, PILOT_STORE, PILOT_MANIFEST, PILOT_BASELINE, PILOT_FARM,
# PILOT_EVIDENCE, HERMES_HOME_ROOT, HERMES_PYTHON.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"

PROFILE="${PILOT_PROFILE:-stillroom-signal-generator}"
HERMES_HOME_ROOT="${HERMES_HOME_ROOT:-$HOME/.hermes}"
CONFIG="$HERMES_HOME_ROOT/profiles/$PROFILE/config.yaml"
ROOTS="$HERMES_HOME_ROOT/profiles/$PROFILE/skills"
PLUGIN_DIR="$HERMES_HOME_ROOT/profiles/$PROFILE/plugins/skill-broker"
PLUGIN_SOURCE="$REPO_ROOT/scripts/broker/hermes_plugin"
STORE="${PILOT_STORE:-$HOME/skill-store}"
MANIFEST="${PILOT_MANIFEST:-$HOME/.config/skill-broker/manifests/$PROFILE.json}"
BASELINE="${PILOT_BASELINE:-$HOME/.config/skill-broker/baselines/$PROFILE.json}"
FARM="${PILOT_FARM:-$HOME/.local/share/skill-broker/farms/$PROFILE}"
EVIDENCE="${PILOT_EVIDENCE:-$HOME/.hermes/skill-broker}"
POLICY="$STORE/policies/$PROFILE.json"

MODE="check"
case "${1:-}" in
  --check|--dry-run|"") MODE="check" ;;
  --apply)              MODE="apply" ;;
  --rollback)           MODE="rollback" ;;
  --help|-h)            sed -n '2,10p' "$0"; exit 0 ;;
  *) echo "unknown option: $1 (use --check, --apply, --rollback, --help)" >&2; exit 2 ;;
esac

say()  { printf '%s\n' "$*"; }
run()  { if [[ "$MODE" == "check" ]]; then printf '  would run: %s\n' "$*"; else "$@"; fi; }
need() { [[ -e "$1" ]] || { echo "MISSING: $1" >&2; return 1; }; }

MANIFEST_JSON=$(cat <<EOF
{
  "consumer": "$PROFILE",
  "manifest_version": 1,
  "exposures": [
    {"id": "nousresearch.hermes-agent", "name": "hermes-agent"},
    {"id": "life-os.stillroom-writing-workflow", "name": "stillroom-writing-workflow"}
  ]
}
EOF
)

preflight() {
  say "Pre-flight (mode: $MODE)"
  say "  repo:    $REPO_ROOT"
  say "  profile: $PROFILE"
  say "  config:  $CONFIG"
  say "  store:   $STORE"
  local failed=0
  command -v python3 >/dev/null || { echo "python3 not found" >&2; failed=1; }
  python3 -c "import yaml" 2>/dev/null || { echo "PyYAML not available to python3" >&2; failed=1; }
  command -v hermes  >/dev/null || say "  WARN: 'hermes' not on PATH; Phase 4 needs it"
  need "$STORE/store-manifest.json" || failed=1
  need "$POLICY" || failed=1
  need "$CONFIG" || failed=1
  need "$ROOTS" || failed=1
  if [[ "$MODE" == "rollback" ]]; then
    need "$BASELINE" || failed=1
    return "$failed"
  fi
  python3 "$REPO_ROOT/scripts/store_manifest.py" verify --store "$STORE" >/dev/null \
    || { echo "store manifest does not verify" >&2; failed=1; }
  local manifest_for_check="$MANIFEST"
  if [[ ! -f "$MANIFEST" ]]; then
    manifest_for_check="$(mktemp)"
    printf '%s\n' "$MANIFEST_JSON" > "$manifest_for_check"
    say "  note: manifest absent; validating the one --apply would write"
  fi
  python3 "$REPO_ROOT/scripts/exposure_farm.py" validate \
    --store "$STORE" --manifest "$manifest_for_check" >/dev/null \
    || { echo "exposure manifest is invalid" >&2; failed=1; }
  [[ "$manifest_for_check" == "$MANIFEST" ]] || rm -f "$manifest_for_check"
  return "$failed"
}

write_manifest() {
  say "Phase 1 — Exposure Manifest"
  if [[ -f "$MANIFEST" ]]; then say "  exists, left unchanged: $MANIFEST"; return; fi
  run mkdir -p "$(dirname "$MANIFEST")"
  if [[ "$MODE" == "apply" ]]; then
    printf '%s\n' "$MANIFEST_JSON" > "$MANIFEST"
    say "  wrote $MANIFEST"
  else
    say "  would write $MANIFEST"
  fi
}

deploy_plugin() {
  say "Phase 2 — plugin deployment (packaging fog: see runbook)"
  if [[ -e "$PLUGIN_DIR" ]]; then say "  exists, left unchanged: $PLUGIN_DIR"; else
    run ln -s "$PLUGIN_SOURCE" "$PLUGIN_DIR"
    [[ "$MODE" == "apply" ]] || say "  would symlink $PLUGIN_DIR -> $PLUGIN_SOURCE"
  fi
  if grep -q 'skill-broker' "$CONFIG" 2>/dev/null; then
    say "  config already mentions skill-broker; left unchanged"
    return
  fi
  if [[ "$MODE" != "apply" ]]; then
    say "  would merge the 'skill-broker' plugin entry into $CONFIG (with a .bak backup)"
    return
  fi
  python3 - "$CONFIG" "$STORE" "$PROFILE" "$EVIDENCE" <<'PY'
import datetime, pathlib, shutil, sys
import yaml

config, store, profile, evidence = sys.argv[1:5]
path = pathlib.Path(config)
data = yaml.safe_load(path.read_text()) or {}
plugins = data.setdefault("plugins", {})
enabled = plugins.setdefault("enabled", [])
if not isinstance(enabled, list):
    raise SystemExit("plugins.enabled is not a list; edit the config by hand")
enabled.append("skill-broker")
plugins.setdefault("entries", {})["skill-broker"] = {"settings": {
    "store": store, "profile": profile, "evidence_dir": evidence,
    "judgment": "live", "inject": False}}
backup = path.with_name(path.name + ".bak-" + datetime.datetime.now().strftime("%Y%m%d-%H%M%S"))
shutil.copy2(path, backup)
path.write_text(yaml.safe_dump(data, sort_keys=False))
print(f"  merged skill-broker into {path} (backup {backup})")
PY
}

cutover_apply() {
  say "Phase 3 — Cutover (reversible)"
  run python3 "$REPO_ROOT/scripts/cutover.py" baseline \
    --consumer "$PROFILE" --config "$CONFIG" --roots "$ROOTS"
  run python3 "$REPO_ROOT/scripts/cutover.py" apply \
    --consumer "$PROFILE" --config "$CONFIG" --roots "$ROOTS" \
    --store "$STORE" --manifest "$MANIFEST" --farm "$FARM" --policy "$POLICY"
  run python3 "$REPO_ROOT/scripts/cutover.py" verify \
    --config "$CONFIG" --store "$STORE" --manifest "$MANIFEST" \
    --farm "$FARM" --policy "$POLICY" --roots "$ROOTS"
}

rollback() {
  say "Rollback"
  run python3 "$REPO_ROOT/scripts/gate.py" disable --profile "$PROFILE" --reason "pilot rollback"
  run python3 "$REPO_ROOT/scripts/cutover.py" rollback --consumer "$PROFILE" --config "$CONFIG"
  say "  left in place: $PLUGIN_DIR and the 'skill-broker' entry in $CONFIG"
  say "  remove them by hand only if you also want the Adapter gone"
}

next_steps() {
  cat <<EOF

Next (manual, see docs/runbooks/live-pilot.md):
  Phase 4  hermes -p $PROFILE                # accumulate Shadow Mode traffic
  Phase 5  scripts/corpus.py extract/review/record, scripts/evaluate.py register
  Phase 6  scripts/shadow_report.py build/show/review --outcome approve
  Phase 7  run a real turn; verify the index, the Pack and scripts/gate.py status
  Phase 8  bash docs/runbooks/pilot-enable.sh --rollback
EOF
}

preflight || { echo "pre-flight failed; fix the above first" >&2; exit 1; }
case "$MODE" in
  check)    write_manifest; deploy_plugin; cutover_apply
            say "Pre-flight OK. Re-run with --apply to make the changes." ;;
  apply)    write_manifest; deploy_plugin; cutover_apply; next_steps ;;
  rollback) rollback ;;
esac
