"""The standalone Vflat Streamlit entry point renders without uploads."""

from __future__ import annotations

from pathlib import Path

from streamlit.testing.v1 import AppTest


def test_vflat_lab_renders_without_upload() -> None:
    app_path = Path(__file__).resolve().parents[1] / "vflat_lab_app.py"
    app = AppTest.from_file(str(app_path), default_timeout=90)
    app.run()
    assert not app.exception
    assert any("Емпирична лаборатория" in title.value for title in app.title)
