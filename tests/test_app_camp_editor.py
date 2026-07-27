"""Regression contract for storing rows returned by the CAMP data editor."""

from __future__ import annotations

import ast
from pathlib import Path
from typing import Any, Callable

import pandas as pd

from biathlon.mesocycles import (
    CAMP_PRESCRIPTION_COLUMNS,
    normalize_camp_prescriptions,
)


def _load_app_helper(name: str) -> Callable[..., Any]:
    """Load one pure helper from app.py without executing the Streamlit app."""

    app_path = Path(__file__).resolve().parents[1] / "app.py"
    source = app_path.read_text(encoding="utf-8")
    tree = ast.parse(source, filename=str(app_path))
    function = next(
        (
            node
            for node in tree.body
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
            and node.name == name
        ),
        None,
    )
    assert function is not None, f"app.py must define the pure helper {name}()"

    future_annotations = ast.ImportFrom(
        module="__future__",
        names=[ast.alias(name="annotations")],
        level=0,
    )
    module = ast.fix_missing_locations(
        ast.Module(body=[future_annotations, function], type_ignores=[])
    )
    namespace: dict[str, Any] = {
        "Any": Any,
        "pd": pd,
        "CAMP_PRESCRIPTION_COLUMNS": CAMP_PRESCRIPTION_COLUMNS,
    }
    exec(compile(module, filename=str(app_path), mode="exec"), namespace)
    return namespace[name]


def test_camp_editor_rows_restore_storage_identity_and_drop_display_columns() -> None:
    helper = _load_app_helper("_camp_editor_rows_for_storage")
    edited = pd.DataFrame(
        [
            {
                "event_id": "A-EV-2",
                "camp_name": "Подготвителен лагер",
                "camp_start": pd.Timestamp("2026-08-01"),
                "camp_end": pd.Timestamp("2026-08-10"),
                # These two values must never be trusted from an editor row.
                "athlete_id": "OTHER",
                "schema_version": 999,
                "mesocycle_type": "BUILD",
                "accent_mode": "MANUAL",
                "accent_limit": 2,
                "accent_Z1": 1.0,
                "accent_Z3": 0.5,
                "volume_factor": 1.12,
                "stress_factor": 1.04,
                "maintenance_factor": 0.98,
                "post_camp_behavior": "AUTO",
                "post_camp_recovery_weeks": 1,
                "note": "Акцент върху Z1 и Z3.",
            }
        ]
    )

    rows = helper(edited, "A")

    assert len(rows) == 1
    assert set(rows[0]) == set(CAMP_PRESCRIPTION_COLUMNS)
    assert rows[0]["athlete_id"] == "A"
    assert rows[0]["schema_version"] == 1
    assert "camp_name" not in rows[0]
    assert "camp_start" not in rows[0]
    assert "camp_end" not in rows[0]

    normalized = normalize_camp_prescriptions(
        pd.DataFrame(rows, columns=CAMP_PRESCRIPTION_COLUMNS)
    )
    assert len(normalized) == 1
    assert normalized.iloc[0]["event_id"] == "A-EV-2"
    assert normalized.iloc[0]["athlete_id"] == "A"
    assert normalized.iloc[0]["mesocycle_type"] == "BUILD"
    assert normalized.iloc[0]["accent_Z1"] == 1.0
    assert normalized.iloc[0]["accent_Z3"] == 0.5


def test_camp_editor_rows_handle_an_empty_editor() -> None:
    helper = _load_app_helper("_camp_editor_rows_for_storage")

    assert helper(pd.DataFrame(), "A") == []
