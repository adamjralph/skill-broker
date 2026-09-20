"""The Skill Broker Hermes plugin (ADR-0001, B9, ticket #51).

A thin shim: :mod:`broker.adapter` is the real Adapter, and this module only wires it to Hermes's
plugin system. It registers exactly the two hooks the Adapter owns and nothing else. A failure to
load registers no hooks at all, so a broken Adapter can never make the agent unusable.
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path

logger = logging.getLogger(__name__)


def _ensure_broker_importable() -> None:
    """Make the broker package importable when the plugin is deployed from this repo.

    A pip-installed or ``PYTHONPATH``-provided broker already resolves; a plugin copied (or
    symlinked) out of ``<repo>/scripts/broker/hermes_plugin`` falls back to the repo's ``scripts``.
    """
    try:
        import broker  # noqa: F401
        return
    except Exception:  # noqa: BLE001 — fall through to the repo-relative path
        scripts = Path(__file__).resolve().parents[2]
        if (scripts / "broker").is_dir():
            sys.path.insert(0, str(scripts))


def register(ctx) -> None:
    """Register the Adapter's two hooks. Any failure registers nothing."""
    _ensure_broker_importable()
    try:
        from broker.adapter import Adapter

        Adapter.from_context(ctx).register(ctx)
    except Exception:  # noqa: BLE001 — loading must never break Hermes
        logger.warning("Skill Broker adapter failed to load; no hooks registered", exc_info=True)
