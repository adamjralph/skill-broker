#!/usr/bin/env bash
# THROWAWAY prototype runner for wayfinder ticket #6.
#
# Runs the proof twice against an in-process mock provider (control: plugin
# disabled; treatment: plugin enabled) and compares the raw wire payloads.
#
#   ./run.sh
#
# Override the Hermes interpreter / source if yours differ:
#   HERMES_PYTHON=/path/to/python HERMES_AGENT_SRC=/path/to/hermes-agent ./run.sh
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
HERMES_SRC="${HERMES_AGENT_SRC:-/home/hermes/.hermes/hermes-agent}"
HERMES_PYTHON="${HERMES_PYTHON:-$HERMES_SRC/.venv/bin/python}"

if [[ ! -x "$HERMES_PYTHON" ]]; then
  echo "error: Hermes interpreter not found at $HERMES_PYTHON" >&2
  echo "       set HERMES_PYTHON (and HERMES_AGENT_SRC) and retry" >&2
  exit 2
fi

export HERMES_AGENT_SRC="$HERMES_SRC"
WORK="$(mktemp -d -t skill-broker-proof-XXXXXX)"
trap 'echo "artifacts: $WORK"' EXIT

echo "== control (plugin disabled) =="
"$HERMES_PYTHON" "$HERE/run_proof.py" --mode control --home "$WORK/hermes_home" --out "$WORK/control.json"
echo
echo "== treatment (plugin enabled) =="
"$HERMES_PYTHON" "$HERE/run_proof.py" --mode treatment --home "$WORK/hermes_home" --out "$WORK/treatment.json"
echo
echo "== comparison =="
"$HERMES_PYTHON" "$HERE/compare_proof.py" "$WORK/control.json" "$WORK/treatment.json"
