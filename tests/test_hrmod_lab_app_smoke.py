from __future__ import annotations

from pathlib import Path

from streamlit.testing.v1 import AppTest


def test_standalone_hrmod_lab_renders_without_touching_production_navigation() -> None:
    repository_root = Path(__file__).resolve().parents[1]
    app = AppTest.from_file(
        str(repository_root / "hrmod_lab_app.py"), default_timeout=60
    )
    app.run()
    assert not app.exception
    assert (repository_root / "app.py").read_text(encoding="utf-8").find("hrmod_lab") == -1
