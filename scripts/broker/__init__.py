"""The Skill Broker: the deterministic intervention layer over the Skill Store.

This package is the broker's home in the broker repository (ticket #45), beside the store
tooling in ``scripts/``. It is deliberately importable by the existing stdlib-``unittest``
runner without a packaging change: the suites already put ``scripts/`` on ``sys.path``, so
``import broker`` works, and broker modules import the store tooling (``store_manifest``,
``profile_policy``) from the same path with no shim.

The single external seam is ``prepare_turn(request, profile, session_context)`` (ADR-0001),
built by ticket #46; the internal replaceable seams are the Judgment Source and the Hermes
Adapter. See the accepted spec (issue #44) and ``PROJECT-OUTLINE.md``.
"""

__all__: list[str] = []
