# Canonical sources of derived-only skills

Investigation date: 2026-09-20. No skill, cache, checkout, or live configuration was modified.

## Result

All three ticket cases have a locatable owning artifact/source. None remains at “no canonical source discoverable,” although the OpenAI plugin manifest and its README identify different levels of source detail (repository plus generator path, rather than a public file URL that was independently verified).

This applies ADR-0009: a runtime/plugin/archive copy is evidence, not itself a canonical source (`docs/adr/0009-skill-identity-and-divergence-policy.md:19-21`). The audit explicitly excludes BB runtime caches, the Codex plugin cache, and the archive root outside the three checkouts (`docs/audit/report.md:64-69`).

## Cases

### `openai.pdf`

**Canonical source:** OpenAI's `openai/openai` repository, source tree `lib/artifacts/artifacts_skills`; the generated plugin destination is `plugins/pdf`. Repository URL recorded by the installed plugin: <https://github.com/openai/openai>. The local plugin's own README says it is bundled into the Codex primary runtime and that the package is generated from `lib/artifacts/artifacts_skills` and copied into `plugins/pdf` through `lib/artifacts/artifacts_skills/mapping.json` (`/home/hermes/.codex/plugins/cache/openai-primary-runtime/pdf/26.905.11957/README.md:3-5,15-17`).

**Evidence and version:** The derived manifest identifies the plugin as `pdf`, version `26.905.11957`, author OpenAI, repository `https://github.com/openai/openai`, and skills directory `./skills/` (`/home/hermes/.codex/plugins/cache/openai-primary-runtime/pdf/26.905.11957/.codex-plugin/plugin.json:2-12,27`). The observed file is therefore the generated `plugins/pdf/skills/pdf/SKILL.md` artifact from that versioned package, not a canonical source. The local cache path and files were confirmed read-only with:

```text
find ~/.codex/plugins/cache/openai-primary-runtime/pdf -maxdepth 4 -type f -print
.../pdf/26.905.11957/.codex-plugin/plugin.json
.../pdf/26.905.11957/README.md
.../pdf/26.905.11957/skills/pdf/SKILL.md
```

The plugin README's source-and-copy statement is stronger provenance than the cache directory name (`README.md:15-17`). I did not claim a commit because this installed manifest supplies a package version but no source commit; the public repository tree/path is the canonical locator supplied by the artifact itself.

### `bb.skill-creator`

**Canonical source:** the BB source repository `get-bb/bb`, path `plugins/bb-guide/skills/skill-creator/SKILL.md`: <https://github.com/get-bb/bb/blob/main/plugins/bb-guide/skills/skill-creator/SKILL.md>.

**Evidence and version:** The installed `bb-app` package declares version `0.43.1` and its package metadata names the homepage and repository `https://github.com/get-bb/bb`, with package directory `packages/bb-app` (`/home/hermes/.local/share/mise/installs/node/26.7.0/lib/node_modules/bb-app/package.json:2-4,14-21`). The running BB runtime independently records version `0.43.1` (`/home/hermes/.bb/bb-app-runtime.json:2-7`). The installed BB distribution contains the source-shaped built-in path `server/dist/builtin-plugins/bb-guide/skills/skill-creator/SKILL.md`; the runtime copy has the same SHA-256 as that installed distribution, as established by the read-only command:

```text
sha256sum .../bb-app/server/dist/builtin-plugins/bb-guide/skills/skill-creator/SKILL.md .../.bb/runtime/skill-store/968.../content/SKILL.md
 d33bedeb1f3a75c877eea9ecbb9a804a97fd0a604dbfe3acbeebc286fa2a99ef  (both files)
```

The BB catalog labels `skill-creator` as `sourceType: "data-dir"` and gives it only an opaque `skill-tree:<hash>` source root (`/home/hermes/.bb/runtime/global-skills/fbd8d076.../catalog.json:2-3, entries at the `skill-creator` record`). The copied skill itself explicitly says built-in/plugin skills must be edited at their source, not generated runtime copies (`/home/hermes/.bb/runtime/skill-store/968eda9c.../content/SKILL.md:8-10`). Thus the runtime hash is not canonical; the BB package/repository source is.

The upstream file was also located in the first-party repository at the cited path. Its upstream commit history identifies commit `a3ac7a5025f1e7fac927313c928d5add5e90f800`, “feat: add controls for agent guidance and bundled skills (#3311)” (command: `curl https://api.github.com/repos/get-bb/bb/commits?path=plugins/bb-guide/skills/skill-creator/SKILL.md&per_page=1`).

### Cursor/pstack skills, including `tdd`

**Canonical source:** Cursor's `cursor/plugins` monorepo, subtree `pstack/skills/<skill>/SKILL.md`: <https://github.com/cursor/plugins/tree/main/pstack>. For example, `tdd` is <https://github.com/cursor/plugins/blob/main/pstack/skills/tdd/SKILL.md>.

**Evidence and version:** The archive manifest names plugin `pstack`, version `0.14.5`, author Lauren Tan, homepage `https://github.com/cursor/plugins/tree/main/pstack`, repository `https://github.com/cursor/plugins`, and skills root `./skills/` (`/home/hermes/Documents/skills-archive/pstack/.cursor-plugin/plugin.json:2-11,28-29`). The archived README documents `/add-plugin pstack` as the install mechanism (`/home/hermes/Documents/skills-archive/pstack/README.md:15-19`). The observed `tdd` file has the expected `name: tdd` frontmatter (`/home/hermes/Documents/skills-archive/pstack/skills/tdd/SKILL.md:1-4`).

The upstream repository's first-party API located the current pstack manifest and the `tdd` file, and its commit history returned `d7cde2b84eadbcd6fd890302c876f4436ccb6d82` for the `pstack/skills/tdd/SKILL.md` path (command: `curl https://api.github.com/repos/cursor/plugins/commits?path=pstack/skills/tdd/SKILL.md&per_page=1`). The upstream manifest currently reports `0.15.2` (command: `curl https://raw.githubusercontent.com/cursor/plugins/main/pstack/.cursor-plugin/plugin.json`), while the archive is `0.14.5`; therefore the archive is an older plugin snapshot, not the current canonical version. The audit's prior ownership decision likewise records pstack as `poteto` content hosted in `cursor/plugins` and counts 48 skills (`docs/audit/report.md:40,118,189-190`).

The archive itself has no `.git` directory and is explicitly a snapshot archive in the audit (`docs/audit/report.md:67`, plus the read-only command `git -C ~/Documents/skills-archive/pstack status --short --branch`, which returned “not a git repository”). This establishes why the archive path is not canonical, while the plugin manifest and upstream monorepo locate the owner. All pstack skill names in the snapshot map to the corresponding `cursor/plugins/tree/main/pstack/skills/` path; `tdd` is the checked example.

## Open questions / limits

- `openai.pdf`: the installed plugin records `openai/openai` and the generator path, but no commit. The public `openai/skills` repository also has a PDF skill, but it is not evidence that it owns this installed Codex artifact; the artifact's own manifest/README point to `openai/openai` and `lib/artifacts/artifacts_skills`, so no ownership was inferred from the similarly named repository.
- `bb.skill-creator`: the exact release tag for the locally installed `bb-app@0.43.1` was not recorded in the runtime metadata. The source repository and file path are confirmed, and the first-party commit history provides a source commit, but the installed package is not proven byte-for-byte identical to that commit beyond the local distribution/runtime copy match.
- `pstack`: the archive version is stale relative to upstream `main` (`0.14.5` versus `0.15.2`); per-skill historical commits for every one of the 48 snapshot files were not needed to answer ownership. The canonical owner/path is nevertheless explicit in the plugin manifest.
