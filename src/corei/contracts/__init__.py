"""Canonical contracts — the single source for every event and command.

Python models here are the source of truth; ``scripts/export_schemas.py``
writes language-neutral JSON Schemas to ``contracts/schemas/`` and CI fails if
they drift. Other languages generate bindings from those schemas (C07).
"""
