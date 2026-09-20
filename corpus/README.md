# Replay Corpus artifacts

The **Replay Corpus** is the machine-local set of real request Cases used to evaluate routing
and to pre-register Soft Thresholds (ADR-0020). Raw request text is the one thing replay needs
in full, so it lives **outside the repository**, at `~/.config/skill-broker/corpus/`, redacted
of secrets and identifiers at extraction and deletable on request. The repository never carries
request text.

This directory holds only the two artifacts that are safe to commit:

- `labels/` — hand-corrected Reviewed Case ground truth, one `<profile>.json` per profile. Each
  review names a Case by content hash and gives either a Primary Skill ID or an explicit
  `no_skill` outcome. No request text.
- `recordings/` — frozen Jev outcomes, one `<profile>/<case_sha256>.json` per Case. A Recording
  is bound to its Case by content hash, so replay is deterministic and offline; a Recording
  whose Case has drifted is refused.

Both are written by the harness CLI. Extraction, review and purge all run offline:

```sh
# Extract a profile's opted-in turns into the machine-local corpus.
python3 scripts/corpus.py extract --profile stillroom-signal-generator

# Opt a held-back source in explicitly (telegram / desktop), or an unlisted one (kanban).
python3 scripts/corpus.py extract --profile stillroom-signal-generator --consent telegram

# Report reviewed supply against the Stage 5 minimum for every extracted profile.
python3 scripts/corpus.py status --minimum 20

# Hand-review one Case, or freeze a live Jev outcome against it.
python3 scripts/corpus.py review --profile stillroom-signal-generator \
    --case <case_sha256> --outcome local.adam-content-writing
python3 scripts/corpus.py record --profile stillroom-signal-generator \
    --case <case_sha256> --claim claim.json

# Delete raw text on request; --revoke also removes the source's consent.
python3 scripts/corpus.py purge --profile stillroom-signal-generator --source telegram --revoke
```

Per-source opt-in is enforced at extraction and again when a Case is stored: the agent-authored
channels (`cli`, `cron`, `subagent`, `tool`) are in by default, `telegram` and `desktop` need an
explicit `--consent`, and an unlisted source such as `kanban` does too. A profile below the
Stage 5 minimum is reported as staying in Shadow Mode rather than padded from another profile.
