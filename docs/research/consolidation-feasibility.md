# Skill Consolidation Feasibility for Multi-Agent Systems

**Date:** 2026-09-19
**Context:** Based on findings from `docs/research/catalogue-topology.md`.
**Question:** Can the skills be consolidated into one archive/catalogue without breaking access to skills for coding agents (Codex, Pi, BB, etc.)?

## Executive Summary
**Yes, skills can be consolidated into a single authoritative catalogue without breaking downstream agents.** 

However, because different tools expect skills in specific local directories and sometimes use the same skill names for different underlying logic, the consolidation cannot be a simple "flat directory of named folders." It requires a **Hub-and-Spoke symlink model** backed by a **namespaced or content-addressed central store**.

## Key Findings and Evidence

### 1. Downstream Agents Already Tolerate Symlinks
The primary concern with a centralized store is whether hardcoded paths for agents (like `~/.codex/skills` or `~/.pi/agent/skills`) can be re-routed. The topology data proves this is already happening and working:
*   `~/.codex/skills` is currently **86% symlinks**.
*   `~/.agents/skills` is currently **91% symlinks**.
*   `~/services/honcho` is **83% symlinks**.

**Conclusion:** We can move the actual physical files to a single global catalogue and replace the tool-specific directories with symlink farms pointing to the global catalogue.

### 2. The Divergence Blocker (Name Collisions)
A major risk to consolidation is the **35 names with genuinely divergent content**.
For example, the skill named `pdf` has 3 completely different variants across `codex-plugins`, Hermes runtime, and `manor-ai`. Similarly, `skill-creator` has variants for `codex`, `bb`, and `manor-ai`.

If the central catalogue uses the skill's `name` as the folder name (e.g., `global-store/pdf/`), consolidating them will overwrite tool-specific variants, breaking Codex or BB when they try to load their specific version of `pdf`.

**Conclusion:** The central catalogue must use a two-level identity (Stable ID/Namespace + Content Hash) rather than relying on the frontmatter `name`.

### 3. The BB Content-Addressed Precedent
The `bb` agent currently utilizes a content-addressed hash store at `~/.bb/runtime/skill-store/` (where directories are named by the 64-hex SHA256 of the skill). This is a proven, working pattern on this machine.

## Required Architecture for Consolidation

To achieve a single archive without breaking Codex, Pi, BB, and Hermes, the following structure is recommended:

1. **The Core Catalogue (Single Source of Truth):**
   * A single Git-backed repository (e.g., `~/Documents/skills-archive` or a new dedicated repo).
   * Skills are stored either by a namespaced ID (e.g., `codex.pdf`, `hermes.pdf`) or entirely content-addressed (by hash, adopting the `.bb` model).
2. **Generated Symlink Farms (The Exposures):**
   * Instead of manual copying, a local generation script reads a manifest and generates symlinks for each specific consumer environment.
   * `~/.codex/skills/pdf` symlinks to `global-store/<codex-specific-hash>/`.
   * `~/.hermes/skills/pdf` symlinks to `global-store/<hermes-specific-hash>/`.
3. **Hermes Configuration:**
   * Hermes config (`~/.hermes/config.yaml`) can safely be updated to point its `external_dirs` to either the global store directly or a dedicated Hermes symlink farm.

## Final Verdict
Consolidation is entirely feasible and highly recommended to solve the current redundancy (364 redundant contents creating 1074 duplicate paths). By separating the **physical storage** (one central content-addressed repo) from the **exposure paths** (symlink farms generated for Codex, Pi, BB, and Hermes), all agents will retain seamless access to their specific skill variants.