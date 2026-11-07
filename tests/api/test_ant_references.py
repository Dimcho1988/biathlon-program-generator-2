"""Explicit threshold references survive profile validation unchanged."""
import pytest

from apps.api.management_schemas import AnTReference, ManagementProfile
from tests.api.test_method_library_api import api, PROFILE, HEADERS


def profile(**changes):
    return ManagementProfile(**dict(sport="Run", actual_sport="Run", discipline="1500 m",
        program_start="2026-10-20", program_end="2027-05-01", available_minutes=[60]*7,
        **changes))


def test_explicit_manual_and_measured_references_survive_roundtrip():
    refs = [dict(sport="Run", speed_kmh=17.25, source="MEASURED", assessed_on="2026-10-18",
                 notes="Measured threshold protocol"),
            dict(sport="NordicSki", speed_kmh=18.5, source="MANUAL", notes="Coach setting")]
    saved = profile(ant_references=refs).model_dump(mode="json")
    reread = ManagementProfile(**saved).model_dump(mode="json")
    assert reread["ant_references"] == saved["ant_references"]
    assert reread["ant_references"][0]["speed_kmh"] == 17.25
    assert profile().model_dump()["ant_references"] == []


def test_duplicate_sport_reference_is_rejected():
    row = dict(sport="Run", speed_kmh=17, source="MANUAL")
    with pytest.raises(ValueError):
        profile(ant_references=[row, row])


@pytest.mark.parametrize("changes", [dict(speed_kmh=0), dict(speed_kmh=float("nan")),
                                    dict(source="EXPERT_0_95_CS_ESTIMATE")])
def test_estimate_cannot_be_saved_as_an_explicit_reference(changes):
    row = dict(sport="Run", speed_kmh=17, source="MANUAL")
    row.update(changes)
    with pytest.raises(ValueError):
        AnTReference(**row)


def test_older_profile_editor_cannot_erase_explicit_reference(api):
    client, store, _, _ = api
    saved = [dict(sport="Run", speed_kmh=17.25, source="MEASURED", notes="Retained result")]
    store.current["profile"]["ant_references"] = saved
    response = client.put("/api/v2/athlete/management/profile", headers=HEADERS,
                          json={"expected_revision": 4, "profile": PROFILE})
    assert response.status_code == 200
    assert store.saved[-1][1]["ant_references"] == saved


def test_explicit_reference_removal_is_distinct_from_omission(api):
    client, store, _, _ = api
    store.current["profile"]["ant_references"] = [dict(sport="Run", speed_kmh=17, source="MANUAL")]
    response = client.put("/api/v2/athlete/management/profile", headers=HEADERS,
        json={"expected_revision": 4, "profile": {**PROFILE, "ant_references": []}})
    assert response.status_code == 200
    assert store.saved[-1][1]["ant_references"] == []
