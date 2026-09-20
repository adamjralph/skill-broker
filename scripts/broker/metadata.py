"""Reading a Candidate's retrieval metadata from the store — never its body (ADR-0013).

Name and Aliases come from the verified Store Manifest row; the description and the Hermes tags
come from the Skill's ``SKILL.md`` frontmatter. Frontmatter that is missing, malformed or
shaped unexpectedly yields empties rather than an error: retrieval metadata is an optimisation,
never a reason to fail a turn.
"""

from __future__ import annotations

from pathlib import Path

import yaml

from .retrieval import CandidateProfile


def candidate_profile(store: Path | str, row: dict) -> CandidateProfile:
    """The retrieval metadata for one manifest row, read from ``<store>/<path>/SKILL.md``."""
    description, tags = _frontmatter(Path(store) / row["path"] / "SKILL.md")
    return CandidateProfile(
        id=row["id"],
        name=row["name"],
        aliases=tuple(row.get("aliases", ())),
        description=description,
        tags=tags,
    )


def _frontmatter(skill_md: Path) -> tuple[str, tuple[str, ...]]:
    """``(description, hermes tags)`` from a YAML frontmatter block, or ``("", ())``."""
    try:
        text = skill_md.read_text(errors="replace")
    except OSError:
        return "", ()
    if not text.startswith("---"):
        return "", ()
    end = text.find("\n---", 3)
    if end == -1:
        return "", ()
    try:
        data = yaml.safe_load(text[3:end])
    except yaml.YAMLError:
        return "", ()
    if not isinstance(data, dict):
        return "", ()
    description = data.get("description")
    return (description.strip() if isinstance(description, str) else ""), _tags(data)


def _tags(data: dict) -> tuple[str, ...]:
    metadata = data.get("metadata")
    if not isinstance(metadata, dict):
        return ()
    hermes = metadata.get("hermes")
    if not isinstance(hermes, dict):
        return ()
    tags = hermes.get("tags")
    if not isinstance(tags, (list, tuple)):
        return ()
    return tuple(str(tag) for tag in tags if isinstance(tag, (str, int, float)))
