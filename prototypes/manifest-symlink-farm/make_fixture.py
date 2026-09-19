#!/usr/bin/env python3
"""THROWAWAY fixture builder — wayfinder ticket #7.

Creates a small content-addressed store of synthetic skill packages shaped like
the real same-name divergences audited in `docs/research/`:

* ``pdf``           — 2 distinct contents (real: openai codex-plugins / Hermes)
* ``skill-creator`` — 2 distinct contents (real: openai / bb)
* ``tdd``           — 2 distinct contents (real: mattpocock / cursor pstack)

**manor-ai is deliberately absent**: project-scoped trees are excluded from the
store (ADR-0008) and must stay excluded.

The bodies are synthetic on purpose — the prototype proves the *mechanism*, and
this avoids vendoring third-party skill content. The real audited hashes are
recorded in the README for provenance.

Store layout (the hub):

    store/objects/<package_sha256>/SKILL.md
    store/objects/<package_sha256>/scripts/...
    store/catalog.json      # id -> {namespace, name, package_sha, skill_md_sha}

The catalog is the store's inventory; the hand-authored ``manifest.json`` is the
per-consumer exposure policy. Both feed ``generate.py``.
"""

from __future__ import annotations

import hashlib
import json
import shutil
from pathlib import Path

HERE = Path(__file__).resolve().parent
STORE = HERE / "store"
OBJECTS = STORE / "objects"

# id -> (frontmatter name, {relative path: text})
# Namespaces are owner/provenance (see #9), not the consuming tool.
VARIANTS = {
    "openai.pdf": ("pdf", {"SKILL.md": "---\nname: pdf\ndescription: openai plugin pdf\n---\n\nOPENAI PDF VARIANT\nHandle PDFs with the codex plugin runtime toolchain.\n"}),
    "nousresearch.pdf": ("pdf", {"SKILL.md": "---\nname: pdf\ndescription: hermes productivity pdf\n---\n\nNOUS RESEARCH PDF VARIANT\nHandle PDFs with the Hermes productivity helpers.\n"}),
    "openai.skill-creator": ("skill-creator", {"SKILL.md": "---\nname: skill-creator\ndescription: openai skill creator\n---\n\nOPENAI SKILL-CREATOR\nAuthor skills for the codex harness.\n"}),
    "bb.skill-creator": ("skill-creator", {"SKILL.md": "---\nname: skill-creator\ndescription: bb skill creator\n---\n\nBB SKILL-CREATOR\nAuthor skills for the BB harness.\n"}),
    "mattpocock.tdd": ("tdd", {"SKILL.md": "---\nname: tdd\ndescription: tdd\n---\n\nMATTPOCOCK TDD\nTest-driven development, engineering-skills copy.\n"}),
    "cursor.tdd": ("tdd", {"SKILL.md": "---\nname: tdd\ndescription: tdd\n---\n\nCURSOR PSTACK TDD\nTest-driven development, pstack archive copy.\n"}),
}


def package_sha(files: dict[str, str]) -> str:
    """Canonical package hash: sorted relative paths + per-file bytes.

    Mirrors the recommendation in docs/research/identity-hash-depth.md.
    """
    entries = [
        f"{rel}\0{hashlib.sha256(text.encode()).hexdigest()}"
        for rel, text in sorted(files.items())
    ]
    return hashlib.sha256("\n".join(entries).encode()).hexdigest()


def main() -> int:
    shutil.rmtree(STORE, ignore_errors=True)
    OBJECTS.mkdir(parents=True)

    catalog: dict[str, dict] = {}
    for skill_id, (name, files) in sorted(VARIANTS.items()):
        pkg_sha = package_sha(files)
        md_sha = hashlib.sha256(files["SKILL.md"].encode()).hexdigest()
        obj = OBJECTS / pkg_sha
        for rel, text in files.items():
            dest = obj / rel
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_text(text)
        catalog[skill_id] = {
            "namespace": skill_id.split(".", 1)[0],
            "name": name,
            "package_sha": pkg_sha,
            "skill_md_sha": md_sha,
        }

    # No two ids may share a package hash: identical content is one identity.
    shas = [c["package_sha"] for c in catalog.values()]
    assert len(shas) == len(set(shas)), "two ids share content; aliases must be names, not ids"

    (STORE / "catalog.json").write_text(json.dumps({"skills": catalog}, indent=2) + "\n")

    distinct_pdf = {catalog[i]["package_sha"] for i in ("openai.pdf", "nousresearch.pdf")}
    distinct_sc = {catalog[i]["package_sha"] for i in ("openai.skill-creator", "bb.skill-creator")}
    distinct_tdd = {catalog[i]["package_sha"] for i in ("mattpocock.tdd", "cursor.tdd")}
    print(f"store: {OBJECTS} ({len(set(shas))} objects, {len(catalog)} ids)")
    print(f"pdf variants: {len(distinct_pdf)}; skill-creator: {len(distinct_sc)}; tdd: {len(distinct_tdd)} distinct objects")
    print("note: manor-ai excluded (project-scoped, ADR-0008)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
