# Live pilot enablement runbook

How to enable the bounded intervention pilot (ticket #57) for one Consumer — by default
`stillroom-signal-generator`. This is the operator path; the deterministic rehearsal that proves
the same lifecycle without touching live state is
`tests/hermes_adapter/run_pilot_proof.py` (`VERDICT: PASS`).

Read this whole page before starting. Everything in Phases 0–3 is set up by
[`pilot-enable.sh`](pilot-enable.sh), which is **dry-run by default**. Injection stays **off**
until Phase 6, and every config edit is reversible in Phase 8.

## What you need

- The Skill Store at `~/skill-store` with a valid manifest and the Consumer's Profile Policy.
- The Consumer's Hermes profile (`~/.hermes/profiles/<profile>/`) with its own `skills/`.
- The Hermes CLI on `PATH` (`~/.local/bin/hermes`).
- A real Jev judgment source for the Shadow Mode window (Phase 4) and the corpus recordings
  (Phase 5).
- Patience for Phase 5: Soft Thresholds cannot be pre-registered below 20 reviewed cases, and
  the Report review in Phase 6 refuses without them.

Paths used below (override with the environment variables in the script's header):

| Thing | Path |
|---|---|
| Profile config | `~/.hermes/profiles/<profile>/config.yaml` |
| Profile skills (native tier) | `~/.hermes/profiles/<profile>/skills/` |
| Exposure Manifest (input) | `~/.config/skill-broker/manifests/<profile>.json` |
| Cutover baseline | `~/.config/skill-broker/baselines/<profile>.json` |
| Exposure Farm (output) | `~/.local/share/skill-broker/farms/<profile>` |
| Evidence dir | `~/.hermes/skill-broker/` |
| Machine-local corpus | `~/.config/skill-broker/corpus/` |

## Phase 0 — pre-flight (read-only)

```sh
bash docs/runbooks/pilot-enable.sh --check
```

It verifies the store manifest, the policy, the profile config/skills, the Hermes CLI, and that
today's Exposure Manifest is valid. Fix anything it reports before continuing. You can also run
the rehearsal proof for confidence:

```sh
~/.hermes/hermes-agent/.venv/bin/python tests/hermes_adapter/run_pilot_proof.py
```

## Phase 1 — author the Exposure Manifest

The Farm exposes the **Foundation Set**, never the Brokered Skills. `--apply` writes this
automatically (and `--check` validates it from a temp file):

```json
{
  "consumer": "stillroom-signal-generator",
  "manifest_version": 1,
  "exposures": [
    {"id": "nousresearch.hermes-agent", "name": "hermes-agent"},
    {"id": "life-os.stillroom-writing-workflow", "name": "stillroom-writing-workflow"}
  ]
}
```

To do it by hand instead: `mkdir -p ~/.config/skill-broker/manifests` and write the JSON above to
`~/.config/skill-broker/manifests/<profile>.json`, then validate with
`python3 scripts/exposure_farm.py validate --store ~/skill-store --manifest <manifest>`.

## Phase 2 — deploy the plugin (the one open packaging decision)

`import broker` does **not** resolve in the Hermes venv, so the plugin needs this repo's
`scripts/` on its path. `--apply` creates a symlink, which works because the plugin resolves
`parents[2]` of its real path:

```sh
ln -s /home/hermes/Projects/skill-broker/scripts/broker/hermes_plugin \
      ~/.hermes/profiles/<profile>/plugins/skill-broker
```

If Hermes's directory-plugin discovery ignores symlinks, copy the directory instead **and** put
`/home/hermes/Projects/skill-broker/scripts` on the Hermes process's `PYTHONPATH` (a `.pth` in
the venv or the service environment). This is the packaging question carried as fog (B16); it is
the only step here without a settled answer.

Then the profile config needs the plugin enabled and configured. `--apply` merges this in and
writes a timestamped backup first; to do it by hand, add to `~/.hermes/profiles/<profile>/config.yaml`:

```yaml
plugins:
  enabled:
    - stillroom-acquisition-ops     # keep whatever is already there
    - skill-broker
  entries:
    skill-broker:
      settings:
        store: /home/hermes/skill-store
        profile: stillroom-signal-generator
        evidence_dir: /home/hermes/.hermes/skill-broker
        judgment: live
        inject: false               # the per-batch gate is authoritative; leave false
```

`inject: false` is correct: Shadow Mode runs the pipeline and records decisions, and delivers
nothing.

## Phase 3 — Cutover the live profile (reversible)

`--apply` runs the three commands below. They create the `skills:` block, wire the Farm into
`external_dirs`, and withhold the five Brokered Names in `disabled`:

```sh
python3 scripts/cutover.py baseline --consumer <profile> --config <config> --roots <skills>

python3 scripts/cutover.py apply --consumer <profile> --config <config> --roots <skills> \
  --store ~/skill-store \
  --manifest ~/.config/skill-broker/manifests/<profile>.json \
  --farm ~/.local/share/skill-broker/farms/<profile> \
  --policy ~/skill-store/policies/<profile>.json

python3 scripts/cutover.py verify --config <config> --store ~/skill-store \
  --manifest ~/.config/skill-broker/manifests/<profile>.json \
  --farm ~/.local/share/skill-broker/farms/<profile> \
  --policy ~/skill-store/policies/<profile>.json --roots <skills>
```

Expected: `apply` prints `"withheld"` with the five Brokered Names; `verify` exits 0.

## Phase 4 — run Shadow Mode over real traffic

Use the profile normally so real turns accumulate:

```sh
hermes -p <profile>
```

Wait for a window that gives the Report enough turns. Watch it with:

```sh
python3 scripts/gate.py status    --profile <profile>
python3 scripts/gate.py incidents --profile <profile>
```

## Phase 5 — reviewed corpus and pre-registered thresholds (the long pole)

The Report review in Phase 6 refuses unless the Soft Thresholds are pre-registered, and
`evaluate.py register` refuses below 20 reviewed cases. The full command reference is
[`corpus/README.md`](../../corpus/README.md); the sequence is:

```sh
python3 scripts/corpus.py extract --profile <profile>
python3 scripts/corpus.py status  --profile <profile> --minimum 20

# for each Case: hand-review the outcome, and freeze a Jev claim against it
python3 scripts/corpus.py review --profile <profile> --case <sha> --outcome <skill-id|no_skill>
# A turn the live broker judged: freeze the recorded Judgment (joined by request hash).
python3 scripts/corpus.py record --profile <profile> --case <sha> \
    --from-evidence ~/.hermes/skill-broker/route_decisions.jsonl
# A pre-broker turn it never judged: ask live Jev now and freeze that Choice.
python3 scripts/corpus.py record --profile <profile> --case <sha> --live

python3 scripts/evaluate.py evaluate --profile <profile> --minimum 20
python3 scripts/evaluate.py register --profile <profile> --minimum 20
```

Notes:

- Raw request text lives machine-locally at `~/.config/skill-broker/corpus/` and is deletable;
  the repo only ever receives `corpus/labels/`, `corpus/recordings/` and `corpus/thresholds/`.
- `--from-evidence` freezes the validated `judgment` the live broker already recorded in the
  Evidence Log, joined to the Case by the request's content hash. It refuses a Judgment not
  answered by live Jev unless `--any-source` is passed, so a `first_candidate` fallback cannot be
  frozen as if it were a live call. `--live` is for a Case the broker never judged (a turn from
  before the plugin was live): it asks live Jev for the Case now, freezes that Choice the same
  way, and refuses a fallback answer. `--claim <claim.json>` remains for a hand-supplied Choice.
- Below the minimum the profile correctly stays in Shadow Mode; do not pad from another profile.

## Phase 6 — build and review the Shadow Report

Pick epoch-second bounds covering the Phase 4 window:

```sh
python3 scripts/shadow_report.py build --profile <profile> --since <epoch> --until <epoch>
python3 scripts/shadow_report.py show  --report <path printed by build>
python3 scripts/shadow_report.py review --report <path> --outcome approve --reviewer adam
```

`show` prints the batch, every Hard Gate's status, threshold movement, and disagreements. Read it
before approving. The review is the only thing that enables injection, and it enables exactly the
one `<profile>-brokered` batch.

## Phase 7 — the live turn, and what to verify

Run a real turn whose request warrants a Brokered Skill, e.g. *"write a cold email to a prospect
introducing our new service"*. Then confirm:

- The profile's `/skills` index does **not** list any of the five Brokered Names.
- The Pack arrives (guidance appears without the agent loading the Skill itself).
- `python3 scripts/gate.py status --profile <profile>` shows the batch enabled and the profile not
  closed; `incidents` is empty.
- `~/.hermes/skill-broker/api_requests.jsonl` has one record per provider request, each with a
  `route_decision_id`.

## Phase 8 — rollback

```sh
bash docs/runbooks/pilot-enable.sh --rollback
```

That disables the batch (back to foundation-only) and rolls the Cutover back byte-for-byte. It
leaves the plugin symlink and the `plugins:` entry in place; remove those by hand only if you
also want the Adapter gone.

## Usability notes

- The helper is **dry-run by default**; nothing live changes until you pass `--apply`.
- It uses absolute `$HOME`-based paths, so run it from anywhere; no Python REPL and no manual
  JSON paste required.
- The one live edit it makes itself (`plugins:` in the profile config) is backed up to
  `config.yaml.bak-<timestamp>` before writing. Prefer a hand edit if you want the file's exact
  byte layout preserved.
- `--rollback` never touches the store, the corpus, or any Skill content.
