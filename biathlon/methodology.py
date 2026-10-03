"""Versioned planning-methodology identity and public contract.

The methodology describes planning rules only.  It does not own athlete
settings and it never changes the physiology, load, 7/40, or recovery models.
"""

from __future__ import annotations

from typing import Any

from .constants import COMPONENTS, DEFAULT_PARAMETERS
from .mesocycles import CAMP_ACCENT_MODES


METHODOLOGY_SCHEMA_VERSION = "planning-methodology-v1"
CANONICAL_METHODOLOGY_ID = "onflows-canonical"
CANONICAL_METHODOLOGY_VERSION = "onflows-canonical-v1"
CANONICAL_METHODOLOGY_SOURCE = "BUILT_IN"


def canonical_methodology() -> dict[str, Any]:
    """Return a fresh, serializable description of the active methodology."""

    pattern = [float(value) for value in DEFAULT_PARAMETERS["mesocycle_pattern"]]
    return {
        "schema_version": METHODOLOGY_SCHEMA_VERSION,
        "methodology_id": CANONICAL_METHODOLOGY_ID,
        "methodology_version": CANONICAL_METHODOLOGY_VERSION,
        "source_scope": CANONICAL_METHODOLOGY_SOURCE,
        "mesocycle_pattern": pattern,
        "supported_accent_modes": list(CAMP_ACCENT_MODES),
        "accent_components": list(COMPONENTS),
        "default_accent_limit": 2,
        "maximum_accent_limit": len(COMPONENTS),
        "hybrid_rule": "manual-first-auto-fill",
        "stress_mesocycle": {
            "status": "DESIGNED_NOT_ACTIVE",
            "automatic_enabled": False,
            "manual_dose_required": True,
            "selected_accents_only": True,
            "mandatory_recovery": True,
            "affects_canonical_result": False,
        },
    }


def methodology_snapshot_metadata() -> dict[str, str]:
    """Return the stable identity that every generated plan snapshot records."""

    return {
        "schema_version": METHODOLOGY_SCHEMA_VERSION,
        "methodology_id": CANONICAL_METHODOLOGY_ID,
        "methodology_version": CANONICAL_METHODOLOGY_VERSION,
        "source_scope": CANONICAL_METHODOLOGY_SOURCE,
    }


__all__ = [
    "CANONICAL_METHODOLOGY_ID",
    "CANONICAL_METHODOLOGY_SOURCE",
    "CANONICAL_METHODOLOGY_VERSION",
    "METHODOLOGY_SCHEMA_VERSION",
    "canonical_methodology",
    "methodology_snapshot_metadata",
]
