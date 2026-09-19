# Curatorial staleness reconciliation

**Snapshot:** 2026-09-19 06:20:27 UTC. **Scope:** the two Hermes skill roots and their curator snapshots, not the 585-content all-machine catalogue.

## Answer

The outline's **28** is reproducible as the number of records currently marked `state: stale` in `/home/hermes/.hermes/skills/.usage.json`, and it is also the last curator run's summary (`28 marked stale, 1 archived`) in `.curator_state`. It is **not** 28 stale skills in the whole machine-wide catalogue: the curator does not manage that catalogue.

Using the curator's actual eligibility rules, the default root currently has **76 managed skills**: 58 bundled skills (the bundled manifest, with `prune_builtins` defaulting on) plus 18 local skills explicitly marked `created_by: agent`. Of these, **28 are stale and 48 active**; none of the 76 is currently archived. The one archived skill is `punchy-copywriting`, outside the current managed set. The profile root has 115 current skill directories and the same 28 stale names in its copied usage evidence; its latest curator backup also reports 113 skill files. The default and profile backup archives contain the same three dated snapshots; the default latest is 2026-09-17T08:19:39Z, profile latest 2026-09-17T07:36:30Z.

Thus the baseline count is a valid **last-run/status count**, not a fresh measurement of all skills. It is also not evidence that 28 skills have zero use: most stale records have zero use, but `claude-code` has one use. Per-skill evidence is below.

## Curator definition and calculation

- Eligibility is `created_by: agent` (or legacy `agent_created: true`) plus bundled skills when `curator.prune_builtins` is enabled; hub/external skills are excluded ([`skill_usage.py:213-218,271-278`](file:///home/hermes/.hermes/hermes-agent/tools/skill_usage.py)). The current config has no `curator` override, so the defaults apply.
- Staleness is determined from the newest of `last_used_at`, `last_viewed_at`, and `last_patched_at`; default threshold is 14 days and archive threshold 30 days ([`skill_usage.py:342-345`](file:///home/hermes/.hermes/hermes-agent/tools/skill_usage.py), [`curator.py:28-31,191-200`](file:///home/hermes/.hermes/hermes-agent/agent/curator.py)). A never-active skill uses `created_at`; zero use is not by itself stale while younger than the threshold ([`curator.py:219-229`](file:///home/hermes/.hermes/hermes-agent/agent/curator.py)). Pinned and cron-referenced skills are skipped ([`curator.py:200-213`](file:///home/hermes/.hermes/hermes-agent/agent/curator.py)).
- Counts below are `use_count / view_count / patch_count`; “last activity” is the newest of those three timestamps. This is the telemetry exposed by `usage_report`/`curated_report` ([`skill_usage.py:655-679`](file:///home/hermes/.hermes/hermes-agent/tools/skill_usage.py)). Ages are calculated at the snapshot time above, not inferred from directory mtime.

## All currently curator-managed skills

| Skill | Provenance | Persisted state | Usage: use / view / patch | Last activity (UTC) | Age (days) | Pinned |
|---|---|---:|---:|---|---:|---:|
| `agent-skill-library-management` | agent | active | 19 / 19 / 11 | 2026-09-16T20:53:54.519708Z | 2.4 | no |
| `ai-content-kit-system` | agent | stale | 1 / 1 / 1 | 2026-08-25T22:20:17.084940Z | 24.3 | no |
| `ai-memory-systems` | agent | active | 9 / 9 / 5 | 2026-09-14T08:04:00.711069Z | 4.9 | no |
| `ai-model-routing` | agent | active | 40 / 40 / 26 | 2026-09-16T20:53:54.526277Z | 2.4 | no |
| `airtable` | bundled | stale | 0 / 0 / 0 | 2026-09-03T07:12:08.680711Z | 16.0 | no |
| `apple-notes` | bundled | stale | 0 / 0 / 0 | 2026-09-03T07:12:08.688131Z | 16.0 | no |
| `apple-reminders` | bundled | stale | 0 / 0 / 0 | 2026-09-03T07:12:08.693862Z | 16.0 | no |
| `architecture-diagram` | bundled | active | 1 / 1 / 0 | 2026-09-04T07:56:27.527193Z | 14.9 | no |
| `architecture-research` | agent | active | 4 / 4 / 4 | 2026-09-16T20:16:44.884055Z | 2.4 | no |
| `arxiv` | bundled | stale | 0 / 0 / 0 | 2026-09-03T07:12:08.704546Z | 16.0 | no |
| `ascii-video` | bundled | active | 1 / 1 / 0 | 2026-09-15T04:32:14.811779Z | 4.1 | no |
| `baoyu-infographic` | bundled | stale | 0 / 0 / 0 | 2026-09-03T07:12:08.715452Z | 16.0 | no |
| `blocked-page-recovery` | bundled | active | 7 / 7 / 2 | 2026-09-17T02:16:47.742245Z | 2.2 | no |
| `box` | bundled | stale | 0 / 0 / 0 | 2026-09-03T07:12:08.726486Z | 16.0 | no |
| `business-opportunity-research` | agent | active | 25 / 25 / 26 | 2026-09-15T22:31:17.294203Z | 3.3 | no |
| `claude-code` | bundled | stale | 1 / 1 / 0 | 2026-08-31T00:17:28.256642Z | 19.3 | no |
| `claude-design` | bundled | active | 1 / 1 / 0 | 2026-09-12T00:31:18.365754Z | 7.2 | no |
| `cloudflare-pages` | agent | active | 17 / 17 / 11 | 2026-09-11T07:56:13.559200Z | 7.9 | no |
| `codebase-inspection` | bundled | active | 7 / 7 / 0 | 2026-09-18T00:28:25.104262Z | 1.2 | no |
| `codex` | bundled | active | 7 / 7 / 1 | 2026-09-14T04:36:32.461665Z | 5.1 | no |
| `competitor-news-monitor` | bundled | stale | 0 / 0 / 0 | 2026-09-03T07:12:08.742741Z | 16.0 | no |
| `computer-use` | bundled | active | 17 / 17 / 0 | 2026-09-16T22:27:53.893927Z | 2.3 | no |
| `design-md` | bundled | active | 1 / 1 / 0 | 2026-09-12T00:30:57.404564Z | 7.2 | no |
| `document-to-action-items` | bundled | stale | 0 / 0 / 0 | 2026-09-03T07:12:08.759565Z | 16.0 | no |
| `docx` | bundled | active | 3 / 3 / 0 | 2026-09-14T17:48:39.865225Z | 4.5 | no |
| `dogfood` | bundled | active | 3 / 3 / 0 | 2026-09-13T02:22:52.704710Z | 6.2 | no |
| `email-inbox-triage` | bundled | active | 4 / 4 / 0 | 2026-09-12T00:25:00.966908Z | 7.2 | no |
| `evidence-based-capability-development` | agent | active | 4 / 4 / 14 | 2026-09-15T20:45:26.857738Z | 3.4 | no |
| `file-collection` | agent | active | 2 / 2 / 9 | 2026-09-14T22:06:50.691011Z | 4.3 | no |
| `findmy` | bundled | stale | 0 / 0 / 0 | 2026-09-03T07:12:08.776951Z | 16.0 | no |
| `gif-search` | bundled | stale | 0 / 0 / 0 | 2026-09-03T07:12:08.782953Z | 16.0 | no |
| `github` | bundled | active | 2 / 2 / 0 | 2026-09-08T21:01:02.497199Z | 10.4 | no |
| `google-workspace` | bundled | active | 2 / 2 / 0 | 2026-09-11T03:34:41.511916Z | 8.1 | no |
| `grounded-citations` | bundled | active | 30 / 30 / 0 | 2026-09-15T01:18:52.404312Z | 4.2 | no |
| `hermes-agent` | bundled | active | 226 / 226 / 1 | 2026-09-17T04:41:26.634481Z | 2.1 | no |
| `hermes-agent-skill-authoring` | bundled | active | 18 / 18 / 2 | 2026-09-13T07:01:41.622477Z | 6.0 | no |
| `hermes-efficiency-audits` | agent | active | 5 / 5 / 13 | 2026-09-07T22:35:08.792894Z | 11.3 | no |
| `himalaya` | bundled | active | 18 / 18 / 3 | 2026-09-12T00:24:52.865952Z | 7.2 | no |
| `humanizer` | bundled | active | 4 / 4 / 0 | 2026-09-15T01:52:06.584745Z | 4.2 | no |
| `imessage` | bundled | stale | 0 / 0 / 0 | 2026-09-03T07:12:08.799669Z | 16.0 | no |
| `inspecting-hermes-desktop-dom` | bundled | active | 2 / 2 / 0 | 2026-09-13T05:48:44.263160Z | 6.0 | no |
| `job-search` | agent | active | 2 / 2 / 1 | 2026-09-15T02:51:31.510364Z | 4.1 | no |
| `life-os` | agent | active | 503 / 418 / 181 | 2026-09-19T03:25:20.061030Z | 0.1 | no |
| `llm-wiki` | bundled | stale | 0 / 0 / 0 | 2026-09-03T07:12:08.804917Z | 16.0 | no |
| `local-llm-deployment` | agent | active | 10 / 10 / 8 | 2026-09-08T00:28:32.696932Z | 11.2 | no |
| `manim-video` | bundled | stale | 0 / 0 / 0 | 2026-09-03T07:12:08.810673Z | 16.0 | no |
| `maps` | bundled | stale | 0 / 0 / 0 | 2026-09-03T07:12:08.815987Z | 16.0 | no |
| `meeting-action-items` | bundled | stale | 0 / 0 / 0 | 2026-09-03T07:12:08.821768Z | 16.0 | no |
| `multi-agent-profile-workflows` | agent | active | 43 / 43 / 28 | 2026-09-16T22:37:29.487538Z | 2.3 | no |
| `node-inspect-debugger` | bundled | stale | 0 / 0 / 0 | 2026-09-03T07:12:08.827032Z | 16.0 | no |
| `notion` | bundled | stale | 0 / 0 / 0 | 2026-09-03T07:12:08.832726Z | 16.0 | no |
| `obsidian` | bundled | active | 39 / 39 / 0 | 2026-09-18T01:54:22.392330Z | 1.2 | no |
| `opencode` | bundled | active | 1 / 1 / 0 | 2026-09-14T04:36:32.476831Z | 5.1 | no |
| `p5js` | bundled | stale | 0 / 0 / 0 | 2026-09-03T07:12:08.843907Z | 16.0 | no |
| `pdf` | bundled | active | 2 / 2 / 0 | 2026-09-17T18:55:27.139832Z | 1.5 | no |
| `personal-brand-strategy` | agent | active | 43 / 43 / 24 | 2026-09-15T06:35:38.215078Z | 4.0 | no |
| `popular-web-designs` | bundled | stale | 0 / 0 / 0 | 2026-09-03T07:12:08.855164Z | 16.0 | no |
| `powerpoint` | bundled | active | 1 / 1 / 0 | 2026-09-06T18:53:05.506466Z | 12.5 | no |
| `product-price-monitor` | bundled | stale | 0 / 0 / 0 | 2026-09-03T07:12:08.866212Z | 16.0 | no |
| `python-debugpy` | bundled | stale | 0 / 0 / 0 | 2026-09-03T07:12:08.871762Z | 16.0 | no |
| `requesting-code-review` | bundled | active | 16 / 16 / 0 | 2026-09-13T02:22:52.710973Z | 6.2 | no |
| `sdlc-review` | bundled | stale | 0 / 0 / 0 | 2026-09-03T07:12:08.877672Z | 16.0 | no |
| `simplify-code` | bundled | stale | 0 / 0 / 0 | 2026-09-03T07:12:08.883047Z | 16.0 | no |
| `sol-terra-orchestration` | agent | active | 9 / 9 / 2 | 2026-09-06T08:51:48.896600Z | 12.9 | no |
| `songsee` | bundled | stale | 0 / 0 / 0 | 2026-09-03T07:12:08.888836Z | 16.0 | no |
| `songwriting-and-ai-music` | bundled | stale | 0 / 0 / 0 | 2026-09-03T07:12:08.894171Z | 16.0 | no |
| `spike` | bundled | active | 1 / 1 / 0 | 2026-09-12T07:55:02.022200Z | 6.9 | no |
| `systematic-debugging` | bundled | active | 16 / 16 / 0 | 2026-09-16T20:57:12.523745Z | 2.4 | no |
| `teams-meeting-pipeline` | bundled | active | 1 / 1 / 0 | 2026-09-07T06:03:51.039491Z | 12.0 | no |
| `terminal-email-workflows` | agent | active | 5 / 5 / 4 | 2026-09-12T00:25:31.626306Z | 7.2 | no |
| `test-driven-development` | bundled | active | 25 / 25 / 0 | 2026-09-16T20:57:12.513585Z | 2.4 | no |
| `weekly-review-planning` | bundled | active | 3 / 3 / 0 | 2026-09-13T05:08:51.370031Z | 6.0 | no |
| `xlsx` | bundled | active | 2 / 2 / 0 | 2026-09-08T22:24:42.023325Z | 10.3 | no |
| `xurl` | bundled | stale | 0 / 0 / 0 | 2026-09-03T07:12:08.917092Z | 16.0 | no |
| `youtube-content` | bundled | active | 18 / 18 / 6 | 2026-09-15T07:14:27.965343Z | 4.0 | no |
| `youtube-video-production` | agent | active | 0 / 0 / 0 | 2026-09-15T07:15:17.861434Z | 4.0 | no |

## The 28 stale records

`ai-content-kit-system`, `airtable`, `apple-notes`, `apple-reminders`, `arxiv`, `baoyu-infographic`, `box`, `claude-code`, `competitor-news-monitor`, `document-to-action-items`, `findmy`, `gif-search`, `imessage`, `llm-wiki`, `manim-video`, `maps`, `meeting-action-items`, `node-inspect-debugger`, `notion`, `p5js`, `popular-web-designs`, `product-price-monitor`, `python-debugpy`, `sdlc-review`, `simplify-code`, `songsee`, `songwriting-and-ai-music`, and `xurl`.

All 28 are present in the current default-root managed inventory above. Twenty-six have zero recorded uses; `ai-content-kit-system` and `claude-code` each have `use_count=1` (and one view). `claude-code`'s last activity is 2026-08-31. The zero-use rows are not necessarily unused forever: they are bundled records seeded by the curator and can represent no observed use in this telemetry window.

## Curator backups and topology reconciliation

The manifests at:

- `~/.hermes/skills/.curator_backups/`
- `~/.hermes/profiles/hermes_engineer/skills/.curator_backups/`

show `skill_files` of 95 (Sep 3), 105 (Sep 10), and 113 (Sep 17) in both roots. They are rollback snapshots, not an independent definition of staleness; they include the usage sidecar and skill tree, while the backup directory itself is excluded from discovery. The catalogue reconnaissance explicitly left curator usage evidence unmeasured ([`catalogue-topology.md:205-215`](docs/research/catalogue-topology.md)). It also counts 585 distinct `SKILL.md` contents across many consumers, whereas the curator's `curated_report()` is only the managed subset.

## Limitations

Telemetry is keyed by skill name, not content hash or realpath. It therefore cannot distinguish divergent same-name copies described by the reconnaissance. A missing record is backfilled with a fresh `created_at`; it must not be treated as ancient. The table reports persisted records only for skills currently found under the default root; it does not claim usage for every exposure in the machine-wide catalogue.
