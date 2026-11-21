"""Keep application composition out of reusable HTTP and service modules."""

import ast
from pathlib import Path
import subprocess
import sys

import pytest

from apps.api.application import app
from apps.api.main import ROUTE_MODULES
from apps.api.intervals_webhook import router as webhook_router


API_ROOT = Path(__file__).resolve().parents[2] / "apps" / "api"


def test_reusable_modules_do_not_import_application_entrypoints():
    modules = [
        *API_ROOT.joinpath("routes").glob("*.py"),
        API_ROOT / "dependencies.py",
        API_ROOT / "sync_service.py",
        API_ROOT / "intervals_webhook.py",
    ]
    for module in modules:
        for node in ast.walk(ast.parse(module.read_text())):
            if isinstance(node, ast.ImportFrom):
                imported = [node.module or "", *(item.name for item in node.names)]
            elif isinstance(node, ast.Import):
                imported = [item.name for item in node.names]
            else:
                continue
            assert not any(
                name.split(".")[-1] in {"main", "application"} for name in imported
            ), f"{module.name} must not depend on application composition"


def test_routes_are_registered_once():
    operations = [
        (method, route.path)
        for router in [*(module.router for module in ROUTE_MODULES), webhook_router]
        for route in router.routes
        for method in getattr(route, "methods", ())
    ]
    assert len(operations) == len(set(operations))


@pytest.mark.parametrize("first", ["intervals_webhook", "main"])
def test_entrypoint_import_order_keeps_complete_routes(first):
    completed = subprocess.run(
        [sys.executable, "-c", (
            f"import apps.api.{first}; "
            "from apps.api.application import app; "
            "paths = app.openapi()['paths']; "
            "assert '/health' in paths; "
            "assert '/api/v2/athlete/management/view' in paths; "
            "import os; os.environ.pop('INTERVALS_WEBHOOK_SECRET', None); "
            "from fastapi.testclient import TestClient; "
            "response = TestClient(app).post('/api/v2/integrations/intervals/webhook', json=[]); "
            "assert response.status_code == 503"
        )],
        cwd=API_ROOT.parents[1], capture_output=True, text=True, timeout=30,
    )
    assert completed.returncode == 0, completed.stderr
