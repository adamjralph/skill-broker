# Skill-name collision report — Stage 9 leftovers

**Status:** investigation artifact. Read-only — this document made **no filesystem change**.
**Date:** 2026-09-26 15:39 AEST.
**Scope:** every Hermes profile on this machine, measured with the real resolver.

## Why this report exists

Ticket #64 cut `hermes_engineer` over to its Exposure Farm and recorded the cutover as
**zero-delta**, which it verified correctly: the farm yields the same 139 names, name-for-name and
hash-for-hash, and the config was restored byte-for-byte.

The zero-delta check measured **names and hashes**. It did not measure **resolution**, and those
diverge once two configured roots carry the same name. ADR-0012 deliberately left the old copies
in place ("redundant exposures and out-of-scope copies stay in place until a separately-approved
Stage 9 retirement"), so every consumer now resolves its names against **two roots at once** — its
own profile tier and its farm.

The result is not a wrong skill being loaded. It is a skill that **cannot be loaded at all**.

## The failure

Hermes resolves a skill by frontmatter `name`. With two or more configured roots, a name found in
two of them makes `skill_view` refuse rather than guess — correct upstream behaviour, since silent
shadowing is a real bug class. One exemption exists: all candidates in a single search root **and**
provably identical bytes.

The exemption cannot apply here, because the copies sit in **different configured roots**. The
root test fails before content is compared, so **byte-identical content collides exactly as hard
as divergent content.** That is the part that surprises: "the copies are the same, so nothing is
wrong" is false.

Measured on `hermes_engineer` with `tools/skills_tool._collect_skill_candidates` and the real
ambiguity predicate:

```
CURRENT            : 147 names, 114 ambiguous
```

### Observable symptoms

- `skill_view("<name>")` → `Ambiguous skill name '<name>': N skills match across your local skills
  dir and external_dirs. Refusing to guess — load one explicitly by its categorized path.`
- **120** "Skill name collision" warnings in
  `~/.hermes/profiles/hermes_engineer/logs/agent.log`, live since **2026-09-24** (the #64 cutover
  date).
- Silent in normal use: the agent recovers by calling `skill_view("<category>/<name>")`, which
  resolves. Degradation is real but invisible without measuring resolution by bare name.

### Affected names on `hermes_engineer` (114)

```
adam-content-writing, agent-skill-library-management, ai-memory-systems, ai-model-routing,
airtable, apple-notes, apple-reminders, architecture-diagram, architecture-research, arxiv,
ascii-art, ascii-video, astra-executor-orchestration, baoyu-infographic, blocked-page-recovery,
blogwatcher, box, business-opportunity-research, claude-code, claude-design, cloudflare-pages,
codebase-inspection, codex, comfyui, competitor-news-monitor, computer-use, design-md,
diagnose-crash, document-to-action-items, docx, dogfood, email-inbox-triage,
evaluating-llms-harness, evidence-based-capability-development, excalidraw, file-collection,
findmy, gif-search, github, github-auth, github-code-review, github-issue-to-pr, github-issues,
github-pr-workflow, github-repo-management, google-workspace, grounded-citations, handoff,
hermes-agent, hermes-agent-skill-authoring, hermes-efficiency-audits, himalaya,
huggingface-hub, humanizer, imessage, inspecting-hermes-desktop-dom, job-search, life-os,
linkedin-post-writing, llama-cpp, llm-wiki, local-llm-deployment, manim-video, maps,
meeting-action-items, merge-reconciler, message-composer, model-identity-audit,
multi-agent-profile-workflows, nano-pdf, node-inspect-debugger, notion, obsidian,
ocr-and-documents, omarchy, opencode, openhue, p5js, pdf, personal-brand-strategy,
pi-coding-agent, plan, popular-web-designs, powerpoint, pretext, product-price-monitor,
pydantic-graph-workflows, python-debugpy, requesting-code-review, research-paper-writing,
scale-leaderboard-research, sdlc-review, serving-llms-vllm, session-librarian, signal-guardian,
simplify-code, sketch, sol-terra-orchestration, songsee, songwriting-and-ai-music, spike,
studio-producer, systematic-debugging, teams-meeting-pipeline, terminal-email-workflows,
test-driven-development, touchdesigner-mcp, typesafe-ai, weekly-review-planning,
weights-and-biases, xlsx, xurl, youtube-content, youtube-video-production
```

### Per-profile collisions (profile tier vs its own farm)

| Profile | Colliding | Content differs | Real dirs | Symlinks |
| --- | ---: | ---: | ---: | ---: |
| `hermes_engineer` | 110 | 19 | 108 | 2 |
| `stillroom-chief-of-staff` | 4 | 2 | 1 | 3 |
| `stillroom-research-assistant` | 4 | 1 | 1 | 3 |
| `stillroom-signal-guardian` | 4 | 1 | 1 | 3 |
| `stillroom-content-scout` | 3 | 1 | 1 | 2 |
| `stillroom-media-analyst` | 3 | 1 | 1 | 2 |
| `stillroom-signal-generator` | 2 | 1 | 1 | 1 |
| `stillroom-studio-producer` | 2 | 1 | 1 | 1 |
| `astra-pinned` | 1 | 1 | 1 | 0 |
| `life-agent` | 1 | 1 | 1 | 0 |
| **Total** | **134** | **29** | | |

The nine small profiles collide on one or two names each despite their farms being small, because
`diagnose-crash` / `omarchy` symlinks and a handful of shared skills exist in their tiers too.

## The exit is already designed — Stage 9 retirement

This is the predicted end state of ADR-0012, and `docs/retirement/README.md` already defines the
exit. #64 states the requirement directly: the two stale profile copies "stay for Stage 9".
Nothing new needs designing.

Simulated on a **copy** of the `hermes_engineer` tier (`cache/scratch/sim/profile`); nothing live
was touched:

```
CURRENT             : 147 names, 114 ambiguous
AFTER RETIREMENT     : 146 names,   6 ambiguous
```

One name is genuinely lost (`typesafe-ai`) and the residual 6 are fully explained and benign.

## Traps an executor must know

These were each found by simulation, not reasoning. All three would cause silent loss or a
mis-verified "done".

**1. A colliding parent takes a non-colliding child.** `typesafe-ai-patterns` is nested inside
`typesafe-ai`. The colliding name is the parent, so removing "the colliding folder" removes the
child — which exists nowhere else. Store `hermes_engineer/typesafe-ai/` holds only `SKILL.md` and
`LICENSE`. This is why the simulation loses one name. **Retire the exact skill directory the name
resolves at — never a parent, never a glob — then re-enumerate.**

**2. Depth-3 skills vanish from a two-level scan.** Four skills sit at `mlops/<topic>/<skill>`:

```
mlops/evaluation/evaluating-llms-harness   mlops/inference/llama-cpp
mlops/evaluation/weights-and-biases        mlops/inference/serving-llms-vllm
```

Present in **both** the profile tier and the shared shelf (two copies in the *same* root, hence the
depth-tie-break ambiguity), absent from the farm (the store nests `owner/name/`, two levels). A
scan over `*/skill` misses them entirely. `scripts/retire.py propose` enumerates per root — verify
its scan depth rather than assuming it is exhaustive.

**3. Three skills are profile-only and unbacked.** Not in the store under any owner:

| Skill | Created | Location |
| --- | --- | --- |
| `status-reconciliation` | 2026-09-26 | `skills/software-development/status-reconciliation` |
| `retained-python-runtime` | 2026-09-25 | `skills/software-development/retained-python-runtime` |
| `typesafe-ai-patterns` | 2026-09-24 | `skills/typesafe-ai/typesafe-ai-patterns` |

Retirement is archive-first, so a retirement batch would capture them — but they would be archived
rather than preserved as live skills. **Vendor them before retiring their tier.**

Related: a follow-on already in `docs/HANDOFF.md` covers three un-vendored shelf skills
(`caveman`, `explain-diff-html`, `explain-diff-notion`); a fourth, `instruct`, is additional.

**4. Do not delete-and-reinstall.** `hermes skills install` writes into the **active profile's**
directory (`HERMES_HOME/skills`, `tools/skills_hub.py::_skills_dir`). On the default profile that
is `~/.hermes/skills` — the shared shelf, whose `.archive/` and `.curator_backups/` hold the
curator's protected history. Wiping it would destroy that archive and still not fix anything, since
the second colliding root is the farm, not the shelf. The third-party sets are already vendored:
`mattpocock` 38, `coreyhaines31` 50 — reinstalling would **add** 88 copies and more collisions.

## Residual 6 after retirement — both causes benign

```
diagnose-crash   omarchy                     ← symlinks, identical live OS target
evaluating-llms-harness   llama-cpp          ← the depth-3 group
serving-llms-vllm         weights-and-biases
```

- `diagnose-crash` and `omarchy`: the profile tier's symlink and `~/.agents/skills`'s symlink both
  resolve to `/usr/share/omarchy/default/agents/skills/<name>`. Identical live targets — removing
  either is byte-neutral and loses nothing. Tidy-up, not risk.
- The depth-3 four: see Trap 2. Placement decision, not deletion.

## Suggested follow-ups for the tool

These are observations about Skill Broker, not requests:

1. **The cutover gate measures names and hashes but not resolution by bare name.** A resolution
   read-back would have caught this on 2026-09-24. If a future ADR-0025 revision is opened, this is
   the natural addition — the collision is invisible to name/hash equality.
2. **ADR-0012's "stay in place until Stage 9" is the designed cause.** That is correct for safety
   and it means collisions are expected between cutover and retirement, not exceptional. Worth
   stating plainly in `docs/HANDOFF.md` so the next session does not treat it as a regression.
3. **Consider a `scripts/retire.py` scan-depth assertion.** Trap 2 is exactly the class of silent
   error the retirement process exists to prevent, and it is currently one glob away.

## Not done

- No file was deleted, moved, retired or purged.
- Nothing was promoted into the store.
- The divergence list (29 names) was inspected, not adjudicated — Adam decides each.
- The nine small profiles were measured but not simulated individually.
