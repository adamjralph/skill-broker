#!/usr/bin/env python3
"""THROWAWAY fixture builder — wayfinder ticket #7.

Creates a small content-addressed store of synthetic skill packages shaped like
the real same-name divergences audited in `docs/research/`:

* ``pdf``           — 3 distinct contents (real: codex-plugins / hermes / manor-ai)
* ``skill-creator`` — 2 distinct contents (real: codex / bb)
* ``tdd``           — 2 distinct contents plus one alias
                      (real: pstack / Work-vs-archive, with Work and archive identical)

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
VARIANTS = {
    "codex.pdf": ("pdf", {"SKILL.md": "---\nname: pdf\ndescription: codex plugin pdf\n---\n\nCODEX PDF VARIANT\nHandle PDFs with the codex plugin runtime toolchain.\n"}),
    "hermes.pdf": ("pdf", {"SKILL.md": "---\nname: pdf\ndescription: hermes productivity pdf\n---\n\nHERMES PDF VARIANT\nHandle PDFs with the Hermes productivity helpers.\n"}),
    "manor-ai.pdf": ("pdf", {"SKILL.md": "---\nname: pdf\ndescription: manor-ai project pdf\n---\n\nMANOR-AI PDF VARIANT\nProject-local PDF handling.\n", "scripts/extract.py": "print('manor-ai extract')\n"}),
    "codex.skill-creator": ("skill-creator", {"SKILL.md": "---\nname: skill-creator\ndescription: codex skill creator\n---\n\nCODEX SKILL-CREATOR\nAuthor skills for the codex harness.\n"}),
    "bb.skill-creator": ("skill-creator", {"SKILL.md": "---\nname: skill-creator\ndescription: bb skill creator\n---\n\nBB SKILL-CREATOR\nAuthor skills for the BB harness.\n"}),
    "work.tdd": ("tdd", {"SKILL.md": "---\nname: tdd\ndescription: tdd\n---\n\nWORK TDD\nTest-driven development, engineering-work copy.\n"}),
    # Byte-identical to work.tdd: the alias case (one object, two ids).
    "archive.tdd": ("tdd", {"SKILL.md": "---\nname: tdd\ndescription: tdd\n---\n\nWORK TDD\nTest-driven development, engineering-work copy.\n"}),
    "pstack.tdd": ("tdd", {"SKILL.md": "---\nname: tdd\ndescription: tdd\n---\n\nPSTACK TDD\nTest-driven development, pstack archive copy.\n"}),
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

    # Alias check: archive.tdd must share work.tdd's object.
    assert catalog["work.tdd"]["package_sha"] == catalog["archive.tdd"]["package_sha"], "alias fixture drifted"

    (STORE / "catalog.json").write_text(json.dumps({"skills": catalog}, indent=2) + "\n")

    distinct_pdf = {catalog[i]["package_sha"] for i in ("codex.pdf", "hermes.pdf", "manor-ai.pdf")}
    distinct_tdd = {catalog[i]["package_sha"] for i in ("work.tdd", "pstack.tdd")}
    print(f"store: {OBJECTS} ({len({c['package_sha'] for c in catalog.values()})} objects, {len(catalog)} ids)")
    print(f"pdf variants: {len(distinct_pdf)} distinct objects")
    print(f"tdd variants: {len(distinct_tdd)} distinct objects; work.tdd == archive.tdd (alias)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
