"""Smoke test: всички основни Streamlit страници се изпълняват без exception."""

from __future__ import annotations

from pathlib import Path

from streamlit.testing.v1 import AppTest


PAGES = [
    "team",
    "dashboard",
    "load",
    "recovery",
    "plan",
    "calendar",
    "history",
    "monitoring",
    "tests",
    "simulator",
    "profile",
    "models",
    "settings",
]


def test_all_pages_render() -> None:
    app_path = Path(__file__).resolve().parents[1] / "app.py"
    failures: list[str] = []
    for page in PAGES:
        app = AppTest.from_file(str(app_path), default_timeout=180)
        app.query_params["page"] = page
        app.query_params["athlete"] = "A"
        app.run()
        if app.exception:
            failures.extend(f"{page}: {item.value}" for item in app.exception)
    assert not failures, "\n".join(failures)
