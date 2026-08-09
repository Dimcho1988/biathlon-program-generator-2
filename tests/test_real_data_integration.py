from __future__ import annotations

from pathlib import Path
from typing import Any

from intervals_inspector.intervals_client import IntervalsResponse
from intervals_inspector.pipeline import (
    CANONICAL_INPUT_VERSION,
    process_activity_payloads,
    run_activity_pipeline,
)


def _detail(activity_id: str, activity_date: str) -> dict[str, Any]:
    return {
        "id": activity_id,
        "name": "Private activity name",
        "start_date_local": f"{activity_date}T08:00:00",
        "type": "Run",
        "elapsed_time": 60,
        "moving_time": 60,
        "icu_recording_time": 60,
        "recording_stops": [],
        "icu_hr_zones": [119, 140, 160, 180],
        "latlng": [[42.0, 23.0]],
    }


def _streams(hr: list[Any] | None = None) -> list[dict[str, Any]]:
    values = hr if hr is not None else [145.0] * 61
    return [
        {"type": "time", "data": list(range(61))},
        {"type": "heartrate", "data": values},
        {"type": "latlng", "data": [[42.0, 23.0]] * 61},
    ]


class FakeHistoryClient:
    def __init__(self) -> None:
        self.history_range: tuple[str, str] | None = None
        self.details = {
            "selected": _detail("selected", "2026-08-08"),
            "history-a": _detail("history-a", "2026-08-07"),
            "history-b": _detail("history-b", "2026-08-07"),
        }

    def get_activities_result(self, oldest: str, newest: str) -> IntervalsResponse:
        self.history_range = (oldest, newest)
        return IntervalsResponse(
            200,
            [
                {"id": "history-a", "start_date_local": "2026-08-07T08:00:00"},
                {"id": "history-b", "start_date_local": "2026-08-07T18:00:00"},
                {"id": "selected", "start_date_local": "2026-08-08T08:00:00"},
            ],
        )

    def get_activity_result(
        self, activity_id: str, *, include_intervals: bool = False
    ) -> IntervalsResponse:
        assert include_intervals is False
        return IntervalsResponse(200, self.details[activity_id])

    def get_streams_result(self, activity_id: str) -> IntervalsResponse:
        return IntervalsResponse(200, _streams())


def test_complete_pipeline_uses_real_prior_days_and_one_canonical_input() -> None:
    client = FakeHistoryClient()

    summary = run_activity_pipeline(client, "selected")

    assert client.history_range == ("2026-06-29", "2026-08-07")
    assert summary["canonical_model_input"] == {
        "schema_version": CANONICAL_INPUT_VERSION,
        "normalization_version": "conservative-interval-aware-v1",
        "activity_date": "2026-08-08",
        "normalized_once": True,
        "shared_by_baseline_and_experimental": True,
    }
    assert summary["history"]["available_days"] == 40
    assert summary["history"]["available_activities"] == 2
    assert summary["history"]["period_start"] == "2026-06-29"
    assert summary["history"]["period_end"] == "2026-08-07"
    assert summary["history"]["current_day_excluded"] is True
    comparison = summary["shadow_model_comparison"]
    assert comparison["baseline"]["history_days"] == 40
    assert comparison["baseline"]["history_period_end"] == "2026-08-07"
    assert comparison["experimental"]["history_days"] == 40
    assert summary["model_status"]["status"] == "valid"


def test_pipeline_is_idempotent_for_model_outputs() -> None:
    first = run_activity_pipeline(FakeHistoryClient(), "selected")
    second = run_activity_pipeline(FakeHistoryClient(), "selected")

    assert first["shadow_model_comparison"] == second["shadow_model_comparison"]
    assert first["canonical_model_input"] == second["canonical_model_input"]
    assert first["history"] == second["history"]


def test_missing_hr_does_not_publish_model_results() -> None:
    summary = process_activity_payloads(
        _detail("selected", "2026-08-08"),
        [{"type": "time", "data": list(range(61))}],
    )

    assert summary["model_status"]["status"] == "not_run"
    assert summary["shadow_model_comparison"] is None


def test_partial_hr_is_explicitly_limited() -> None:
    summary = process_activity_payloads(
        _detail("selected", "2026-08-08"),
        _streams([145.0] * 31 + [None] * 30),
    )

    assert summary["model_status"]["status"] == "limited"
    assert summary["shadow_model_comparison"] is not None
    assert summary["onflows_load_analysis"]["hr_coverage_percent"] < 80.0


def test_aggregate_output_excludes_identity_location_and_credentials() -> None:
    summary = process_activity_payloads(
        _detail("selected", "2026-08-08"),
        _streams(),
    )
    rendered = repr(summary).lower()

    for forbidden in (
        "private activity name",
        "42.0",
        "latlng",
        "access_token",
        "refresh_token",
        "client_secret",
        "email",
    ):
        assert forbidden not in rendered


def test_pipeline_layer_has_no_streamlit_dependency() -> None:
    pipeline_source = (
        Path(__file__).resolve().parents[1]
        / "intervals_inspector"
        / "pipeline.py"
    ).read_text(encoding="utf-8")

    assert "import streamlit" not in pipeline_source
    assert "st.session_state" not in pipeline_source
