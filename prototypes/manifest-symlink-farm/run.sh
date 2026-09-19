#!/usr/bin/env bash
# THROWAWAY prototype runner for wayfinder ticket #7.
#
#   ./run.sh
#
# Builds a synthetic content-addressed store, generates one symlink farm per
# consumer from manifest.json, proves idempotency and pruning, then verifies
# every consumer resolves its intended variant.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PYTHON="${PYTHON:-python3}"
cd "$HERE"

WORK="$(mktemp -d -t skill-broker-farms-XXXXXX)"
trap 'rm -rf "$WORK"' EXIT

rm -rf farms

echo "== build fixture store =="
"$PYTHON" make_fixture.py

echo
echo "== generate farms (manifest-driven) =="
"$PYTHON" generate.py --prune

echo
echo "== idempotency: second run must change nothing =="
find farms -type l -printf '%p -> %l\n' | sort > "$WORK/farms1"
"$PYTHON" generate.py --prune > /dev/null
find farms -type l -printf '%p -> %l\n' | sort > "$WORK/farms2"
if diff -u "$WORK/farms1" "$WORK/farms2"; then
  echo "idempotent: identical link set on re-run"
else
  echo "NOT idempotent" >&2
  exit 1
fi

echo
echo "== prune: a stale link is removed =="
ln -sfn ../../store/objects/deadbeef farms/codex/skills/stale-skill
"$PYTHON" generate.py --prune > /dev/null
if [[ ! -L farms/codex/skills/stale-skill ]]; then
  echo "prune: stale link removed"
else
  echo "prune FAILED" >&2
  exit 1
fi

echo
echo "== verify =="
"$PYTHON" verify.py
