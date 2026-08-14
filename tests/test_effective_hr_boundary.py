"""Regression coverage for the effective-HR package boundary."""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from biathlon.effective_hr import (
    EFFECTIVE_HR_ADAPTER_VERSION,
    EFFECTIVE_HR_SOURCE,
    effective_hr,
)
from intervals_inspector.effective_hr import (
    EFFECTIVE_HR_ADAPTER_VERSION as compatibility_version,
)
from intervals_inspector.effective_hr import EFFECTIVE_HR_SOURCE as compatibility_source
from intervals_inspector.effective_hr import effective_hr as compatibility_effective_hr


@pytest.mark.parametrize(
    ("raw_hr", "expected"),
    [
        (None, None),
        (True, None),
        ("150", None),
        (float("nan"), None),
        (float("inf"), None),
        (150, 150.0),
        (150.5, 150.5),
    ],
)
def test_effective_hr_preserves_edge_case_behavior(raw_hr: object, expected: float | None) -> None:
    assert effective_hr(raw_hr) == expected


def test_intervals_inspector_reexports_canonical_adapter() -> None:
    assert compatibility_effective_hr is effective_hr
    assert compatibility_version == EFFECTIVE_HR_ADAPTER_VERSION
    assert compatibility_source == EFFECTIVE_HR_SOURCE


def test_biathlon_modules_do_not_import_intervals_inspector() -> None:
    package_root = Path(__file__).parents[1] / "biathlon"
    violations: list[str] = []

    for module_path in package_root.rglob("*.py"):
        tree = ast.parse(module_path.read_text(encoding="utf-8"), filename=str(module_path))
        for node in ast.walk(tree):
            imported_modules: list[str] = []
            if isinstance(node, ast.Import):
                imported_modules = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported_modules = [node.module]
            if any(name == "intervals_inspector" or name.startswith("intervals_inspector.") for name in imported_modules):
                violations.append(str(module_path.relative_to(package_root.parent)))

    assert violations == []
