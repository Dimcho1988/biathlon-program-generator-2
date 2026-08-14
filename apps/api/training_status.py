"""Application adapter from canonical analysis results to the v1 API contract."""

from __future__ import annotations

import math
from datetime import date
from typing import Any

from biathlon.demo_data import DEMO_SEED, generate_demo_bundle
from biathlon.effective_hr import EFFECTIVE_HR_ADAPTER_VERSION, EFFECTIVE_HR_SOURCE
from biathlon.service import analyze_athlete

from .schemas import DataQuality, ModelMetadata, TrainingStatusResponse, ZoneTrainingStatus

DEMO_AS_OF = date(2026, 6, 20)
DEMO_ATHLETE_ID = "A"
ZONES = ("Z1", "Z2", "Z3", "Z4", "Z5")


def _finite(value: Any, field: str) -> float:
    rendered = float(value)
    if not math.isfinite(rendered):
        raise ValueError(f"Canonical output {field} is not finite")
    return rendered


def build_demo_training_status() -> TrainingStatusResponse:
    """Run fixed local data through the existing canonical service pipeline."""

    bundle = generate_demo_bundle(seed=DEMO_SEED, reference_date=DEMO_AS_OF)
    analysis = analyze_athlete(bundle, DEMO_ATHLETE_ID, as_of=DEMO_AS_OF, generate_plan=False)
    latest = analysis["latest_activity"]
    stats = analysis["load_stats"]
    recovery = analysis["load_readiness"]

    zones = [
        ZoneTrainingStatus(
            zone=zone,
            raw_time_min=_finite(latest[f"real_{zone}"], f"real_{zone}"),
            equivalent_time_min=_finite(latest[f"q_{zone}"], f"q_{zone}"),
            tref_min=_finite(stats.loc[zone, "Tref"], f"Tref[{zone}]"),
            status_7_40=_finite(stats.loc[zone, "index_7_40"], f"index_7_40[{zone}]"),
            recovery_readiness_percent=_finite(recovery.loc[zone, "readiness"], f"readiness[{zone}]"),
            recovery_days_to_full=_finite(recovery.loc[zone, "days_to_full"], f"days_to_full[{zone}]"),
        )
        for zone in ZONES
    ]
    reliability = min(_finite(stats.loc[zone, "reliability"], f"reliability[{zone}]") for zone in ZONES)
    quality = latest.get("quality_score")
    quality_score = None if quality is None else _finite(quality, "quality_score")
    warnings = [] if reliability >= 1.0 else ["History is shorter than the canonical 40-day load window."]

    snapshot = analysis["decision_snapshot"]
    return TrainingStatusResponse(
        schema_version="training-status-v1",
        as_of=DEMO_AS_OF.isoformat(),
        athlete_id=DEMO_ATHLETE_ID,
        model=ModelMetadata(
            algorithm_version=str(snapshot["algorithm_version"]),
            effective_hr_version=EFFECTIVE_HR_ADAPTER_VERSION,
            effective_hr_source=EFFECTIVE_HR_SOURCE,
            parameter_version=int(snapshot["parameter_version"]),
        ),
        data_quality=DataQuality(
            history_reliability=reliability,
            latest_activity_quality_score=quality_score,
            warnings=warnings,
        ),
        zones=zones,
    )
