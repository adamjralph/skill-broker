#!/usr/bin/env bash
# THROWAWAY prototype runner for wayfinder ticket #21 (oversize Pack spill recovery).
#
#   ./run_spill.sh mock          # FREE: proves the Pack spills + preview/path hit the wire
#   ./run_spill.sh live          # real provider turn: does the agent read + apply it?
#   ./run_spill.sh live --nudge  # variant: broker-authored pointer nudge in the Pack head
#
# Override Hermes paths if yours differ:
#   HERMES_PYTHON=/path/to/python HERMES_AGENT_SRC=/path/to/hermes-agent ./run_spill.sh mock
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
HERMES_SRC="${HERMES_AGENT_SRC:-/home/hermes/.hermes/hermes-agent}"
HERMES_PYTHON="${HERMES_PYTHON:-$HERMES_SRC/.venv/bin/python}"

MODE="${1:-mock}"
shift || true

if [[ ! -x "$HERMES_PYTHON" ]]; then
  echo "error: Hermes interpreter not found at $HERMES_PYTHON" >&2
  exit 2
fi

export HERMES_AGENT_SRC="$HERMES_SRC"
OUT="${OUT:-/tmp/skill-broker-spill-${MODE}-$(date +%H%M%S).json}"

"$HERMES_PYTHON" "$HERE/run_spill_proof.py" --mode "$MODE" --out "$OUT" "$@"
