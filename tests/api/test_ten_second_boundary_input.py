"""Boundary evidence is a timed mean and never an implicit curve extension."""
import pytest

from apps.api.model_schemas import ManualSpeedTestInput, SpeedTestInput


def payload(**changes):
    return dict(test_id="11111111-1111-4111-8111-111111111111", name="10 s track",
                day="2026-10-20", sport="Run", duration_s=10., distance_m=70.,
                maximal=True, comparable=True, flat_terrain=True,
                conditions="Measured distance over exactly ten seconds", **changes)


def test_boundary_allows_exact_timed_mean_and_retains_start_type():
    for start_type in ("STANDING", "FLYING", "UNKNOWN"):
        value = ManualSpeedTestInput(**payload(start_type=start_type))
        assert value.duration_s == 10
        assert value.model_dump()["start_type"] == start_type


@pytest.mark.parametrize("duration,sport", [(9.9, "Run"), (10.1, "Run"), (10.7, "Run"),
                                           (10., "Ride"), (10., "NordicSki")])
def test_boundary_does_not_expand_other_sports_or_curve_domain(duration, sport):
    data = payload()
    data.update(duration_s=duration, sport=sport)
    with pytest.raises(ValueError):
        ManualSpeedTestInput(**data)


def test_imported_smoothed_samples_cannot_be_saved_as_ten_second_evidence():
    with pytest.raises(ValueError):
        SpeedTestInput(activity_ref="act_"+"a"*32, start_s=0, duration_s=10,
                       maximal=True, comparable=True, conditions="Imported Vflat")


def test_missing_start_type_stays_unknown_and_invalid_type_is_rejected():
    assert ManualSpeedTestInput(**payload()).start_type == "UNKNOWN"
    with pytest.raises(ValueError):
        ManualSpeedTestInput(**payload(start_type="GPS_PEAK"))
