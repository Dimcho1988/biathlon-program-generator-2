from __future__ import annotations

import json
import math
import os
import subprocess
import sys

import pytest
from fastapi.testclient import TestClient

from apps.api.main import app
from apps.api.training_status import DEMO_AS_OF, DEMO_ATHLETE_ID
from biathlon.demo_data import DEMO_SEED, generate_demo_bundle
from biathlon.effective_hr import EFFECTIVE_HR_ADAPTER_VERSION, EFFECTIVE_HR_SOURCE
from biathlon.service import analyze_athlete

client = TestClient(app)
EXPECTED_ZONE_FIELDS = {
    "zone",
    "raw_time_min",
    "equivalent_time_min",
    "tref_min",
    "status_7_40",
    "recovery_readiness_percent",
    "recovery_days_to_full",
}


def test_health() -> None:
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_browser_wake_redirect_is_fixed_to_the_configured_web_service(monkeypatch) -> None:
    monkeypatch.setenv("ONFLOWS_WEB_BASE_URL", "https://web.example.test")
    response = client.get(
        "/api/v2/wake?intervals=refresh-error&settings=saved",
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert response.headers["location"] == (
        "https://web.example.test/?wake=ready&intervals=refresh-error&settings=saved"
    )
    assert client.get(
        "/api/v2/wake?intervals=https://attacker.example",
        follow_redirects=False,
    ).status_code == 400


def test_browser_wake_can_resume_only_the_oauth_start(monkeypatch) -> None:
    monkeypatch.setenv("ONFLOWS_WEB_BASE_URL", "https://web.example.test")
    response = client.get("/api/v2/wake?resume=connect", follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"] == (
        "https://web.example.test/api/integrations/intervals/connect?wake=ready"
    )
    assert client.get("/api/v2/wake?resume=refresh", follow_redirects=False).status_code == 400


def test_training_status_exact_schema_and_zone_order() -> None:
    response = client.get("/api/v1/demo/training-status")
    assert response.status_code == 200
    payload = response.json()
    assert set(payload) == {"schema_version", "as_of", "athlete_id", "model", "data_quality", "zones"}
    assert payload["schema_version"] == "training-status-v1"
    assert set(payload["model"]) == {
        "algorithm_version", "effective_hr_version", "effective_hr_source", "parameter_version"
    }
    assert set(payload["data_quality"]) == {
        "history_reliability", "latest_activity_quality_score", "warnings"
    }
    assert [row["zone"] for row in payload["zones"]] == ["Z1", "Z2", "Z3", "Z4", "Z5"]
    assert all(set(row) == EXPECTED_ZONE_FIELDS for row in payload["zones"])


def test_training_status_is_deterministic_and_strict_json() -> None:
    first = client.get("/api/v1/demo/training-status").json()
    second = client.get("/api/v1/demo/training-status").json()
    assert first == second
    rendered = json.dumps(first, allow_nan=False)
    assert "NaN" not in rendered and "Infinity" not in rendered
    assert all(
        math.isfinite(value)
        for row in first["zones"]
        for key, value in row.items()
        if key != "zone"
    )


def test_model_metadata() -> None:
    model = client.get("/api/v1/demo/training-status").json()["model"]
    assert model == {
        "algorithm_version": "streamlit-demo-0.6.0",
        "effective_hr_version": EFFECTIVE_HR_ADAPTER_VERSION,
        "effective_hr_source": EFFECTIVE_HR_SOURCE,
        "parameter_version": 1,
    }


def test_numerical_parity_with_canonical_service_outputs() -> None:
    payload = client.get("/api/v1/demo/training-status").json()
    bundle = generate_demo_bundle(seed=DEMO_SEED, reference_date=DEMO_AS_OF)
    canonical = analyze_athlete(bundle, DEMO_ATHLETE_ID, as_of=DEMO_AS_OF, generate_plan=False)
    latest = canonical["latest_activity"]
    for row in payload["zones"]:
        zone = row["zone"]
        assert row["raw_time_min"] == pytest.approx(latest[f"real_{zone}"])
        assert row["equivalent_time_min"] == pytest.approx(latest[f"q_{zone}"])
        assert row["tref_min"] == pytest.approx(canonical["load_stats"].loc[zone, "Tref"])
        assert row["status_7_40"] == pytest.approx(canonical["load_stats"].loc[zone, "index_7_40"])
        assert row["recovery_readiness_percent"] == pytest.approx(canonical["load_readiness"].loc[zone, "readiness"])
        assert row["recovery_days_to_full"] == pytest.approx(canonical["load_readiness"].loc[zone, "days_to_full"])


def test_api_import_needs_no_external_credentials_or_streamlit_modules() -> None:
    environment = os.environ.copy()
    for key in list(environment):
        if any(token in key.upper() for token in ("SUPABASE", "INTERVALS", "STREAMLIT")):
            environment.pop(key)
    script = (
        "import sys; import apps.api.main; "
        "assert 'streamlit' not in sys.modules; "
        "assert not any(name.startswith('intervals_inspector') for name in sys.modules); "
        "print('import-ok')"
    )
    result = subprocess.run(
        [sys.executable, "-c", script], capture_output=True, text=True, env=environment, check=False
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "import-ok"
