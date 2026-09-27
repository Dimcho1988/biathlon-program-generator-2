from copy import deepcopy
from datetime import timedelta
from math import log

import pytest

from apps.api import learning_evidence as evidence
from apps.api.stress_model import CHANNELS
from apps.api.trainability import MODEL_VERSION, SCHEMA_VERSION
from biathlon.constants import COMPONENTS
from biathlon.individual_learning import dose_changes


START = evidence.ANCHOR+timedelta(days=14*170)
TODAY = START+timedelta(days=14)
FIELDS = ("fatigue", "sleep_quality", "stress", "soreness", "motivation")


def entry(kind, key, payload, revision=1, **extra):
    return {"kind": kind, "entry_key": key, "payload": payload, "revision": revision, **extra}


def ti(day, value, *, band="GENERAL", sport="Run", comparison="same"):
    measured = {"name": band, "valid": True, "index": value}
    return {"activity_ref": f"ti-{day}-{band}", "local_date": str(day), "sport": sport,
            "index": {"model_version": MODEL_VERSION, "schema_version": SCHEMA_VERSION,
                      "admission": {"status": "ACCEPTED"}, "comparison_key": comparison,
                      "general": measured if band == "GENERAL" else {}, "zones": [measured] if band != "GENERAL" else []}}


def fixture(*, gained=True, recovered=True):
    rows, activities, strength, entries, days = [], [], [], [], []
    for n in range(-14, 14):
        day = str(START+timedelta(days=n))
        # Independent subjective measurements; functional scores deliberately
        # contradict them and cannot manufacture a recovery outcome.
        value = 3 if 0 <= n <= 6 or (n > 6 and not recovered) else 2
        payload = {"day": day, **dict.fromkeys(FIELDS, value), "pain_or_illness": False}
        entries.append(entry("DAILY", day, payload))
        channels = [{"key": k, "score": (value-1)*25, "group": CHANNELS[k][0], "weight": CHANNELS[k][1]/100} for k in FIELDS]
        channels.append({"key": "performance", "score": 90 if n < 0 else 0, "group": "functional", "weight": .12})
        days.append({"day": day, "daily_report": payload, "channels": channels, "daily_revision": 1})
        zones = []
        for z in COMPONENTS:
            # E changes everywhere due to physiological spill; only direct Z3
            # exposure changed, so attribution must use Q.
            rows.append({"date": day, "zone": z, "effective_load": 1.2 if 0 <= n <= 6 else 1.})
            if z != "STR":
                q = 12. if z == "Z3" and 0 <= n <= 6 else 10.
                zones.append({"zone": z, "equivalent_time_min": q, "raw_time_min": 10.})
        activities.append({"date": day, "activity_ref": f"session-{day}", "sport": "Run", "zones": zones})
        strength.append({"date": day, "real_time_min": 0., "equivalent_time_min": 0.})
    trainability = [ti(START+timedelta(days=n), 10 if n < 0 else 9 if gained else 11) for n in (-5, -2, 10, 12)]
    return {"source": {"period_start": str(START-timedelta(days=14)), "period_end": str(TODAY-timedelta(days=1)),
                       "quality": {}, "activities": activities, "strength": {"daily": strength}},
            "rows": rows, "entries": entries, "history": {"days": days, "sessions": [], "generation_id": "pinned", "revision": 1},
            "trainability": trainability, "today": TODAY, "context_key": "athlete-config"}


def episode(data):
    result = evidence.build_evidence(**data)
    assert len(result["episodes"]) == 1
    return result["episodes"][0]


def test_automatic_episode_needs_no_manual_block_or_test_and_preserves_q_e_distinction():
    data = fixture()
    result = episode(data)
    assert result["dose_change"]["Z3"] == pytest.approx(log(85/71))
    assert result["dose_change"]["Z2"] == 0
    assert result["intensity_change"]["Z3"] == pytest.approx(log(85/71))
    assert result["dose"]["Z2"]["actual_e"] > result["dose"]["Z2"]["baseline_e"]
    assert result["response"]["recovered"]
    assert result["response"]["recovery_days"] == 2
    assert result["response"]["burden_delta"] == pytest.approx(12.5)
    assert "performance" not in result["response"]["channels"]
    assert [o["scope"] for o in result["outcomes"]] == ["GLOBAL"]
    assert result["outcomes"][0]["change_percent"] == 10
    assert result["observed_on"] == str(TODAY)
    assert result == episode(data)


def test_current_day_not_completed_episode_but_usable_for_current_state():
    data = fixture()
    data["today"] -= timedelta(days=1)
    result = evidence.build_evidence(**data)
    assert result["episodes"] == []
    assert result["current"]["latest_day"] == str(data["today"])


def test_missing_observations_or_functional_improvement_do_not_prove_recovery():
    data = fixture(recovered=False)
    result = episode(data)
    assert not result["response"]["recovered"]
    assert result["outcomes"] == []
    assert result["response"]["burden_delta"] > 0
    # Only three actual followup reports do not meet the response gate.
    missing = {str(START+timedelta(days=n)) for n in (7, 9, 11, 13)}
    data["entries"] = [e for e in data["entries"] if e["entry_key"] not in missing]
    assert evidence.build_evidence(**data)["episodes"] == []


def test_missing_channel_cannot_simulate_return_and_sparse_lab_does_not_poison_common_panel():
    data = fixture()
    for day in data["history"]["days"]:
        day["channels"].append({"key": "CK", "score": 100 if day["day"] < str(START) else None})
    result = episode(data)
    assert "CK" not in result["response"]["channels"]
    assert result["response"]["baseline_score"] == 25
    assert result["response"]["burden_delta"] == 12.5


def test_distinct_ti_days_and_matching_model_sport_configuration_required():
    data = fixture()
    data["trainability"][3]["local_date"] = data["trainability"][2]["local_date"]
    assert episode(data)["outcomes"] == []
    for mismatch in ("sport", "comparison", "model", "admission"):
        data = fixture()
        after = data["trainability"][-1]
        if mismatch == "sport":
            after["sport"] = "Ride"
        elif mismatch == "comparison":
            after["index"]["comparison_key"] = "new-zones"
        elif mismatch == "model":
            after["index"]["model_version"] = "old"
        else:
            after["index"]["admission"]["status"] = "EXCLUDED"
        assert episode(data)["outcomes"] == []


def test_specific_ti_keeps_band_scope_and_never_infers_strength():
    data = fixture()
    data["trainability"] += [ti(START+timedelta(days=n), 10 if n < 0 else 9, band="Z3") for n in (-5, -2, 10, 12)]
    outcomes = episode(data)["outcomes"]
    assert {o["scope"] for o in outcomes} == {"GLOBAL", "Z3"}
    assert all(o["scope"] != "STR" for o in outcomes)


def test_test_protocols_direction_and_scope_are_independent_post_recovery():
    data = fixture()
    data["trainability"] = []
    protocol = {"protocol": "strength jump", "protocol_version": "1", "unit": "cm", "direction": "HIGHER",
                "conditions": "same protocol", "components": ["STR"], "comparable": True}
    for offset, value in ((-1, 30), (11, 33)):
        day = str(START+timedelta(days=offset))
        data["entries"].append(entry("TEST", f"jump-{day}", {**protocol, "day": day, "value": value}))
    outcome = episode(data)["outcomes"][0]
    assert outcome["scope"] == "STR" and outcome["change_percent"] == pytest.approx(10.)
    data["entries"][-1]["payload"]["conditions"] = "different conditions"
    assert episode(data)["outcomes"] == []


@pytest.mark.parametrize("confound", ["illness", "lab", "time", "taper", "camp"])
def test_confounds_are_visible_and_prevent_positive_credit(confound):
    data = fixture()
    if confound == "illness":
        data["entries"][-5]["payload"]["pain_or_illness"] = True
    elif confound == "lab":
        data["history"]["days"][-5]["body_observations"] = {"context_status": "REVIEW_LAB_REFERENCE"}
    elif confound == "time":
        day = str(START)
        data["history"]["sessions"] = [{"day": day, "activity_ref": "session"}]
        data["entries"].append(entry("SESSION", "session", {"execution_reason": "TIME"}))
    elif confound == "taper":
        data["periodization"] = {"taper_windows": [{"start_date": str(START), "end_date": str(START)}]}
    else:
        data["events"] = [{"event_type": "CAMP", "start_date": str(START), "end_date": str(START)}]
    result = evidence.build_evidence(**data)
    assert result["episodes"][0]["confounded"]
    assert any(e["reason"] == "CONFOUNDED_EPISODE" for e in result["exclusions"])


def test_reduced_actual_dose_is_supported_not_only_increases():
    data = fixture()
    for activity in data["source"]["activities"]:
        if str(START) <= activity["date"] <= str(START+timedelta(days=6)):
            activity["zones"][2]["equivalent_time_min"] = 8
    assert episode(data)["dose_change"]["Z3"] == pytest.approx(log(57/71))


@pytest.mark.parametrize("change", ["missing", "unknown_q", "extreme", "duplicate", "limited"])
def test_missing_or_invalid_load_cannot_enter_learning(change):
    data = fixture()
    if change == "missing":
        data["rows"] = [r for r in data["rows"] if not (r["date"] == str(START) and r["zone"] == "Z2")]
    elif change == "unknown_q":
        data["source"]["activities"][14]["zones"][0]["equivalent_time_min"] = None
    elif change == "extreme":
        for a in data["source"]["activities"]:
            if str(START) <= a["date"] <= str(START+timedelta(days=6)):
                for zone in a["zones"]:
                    zone["equivalent_time_min"] *= 5
    elif change == "duplicate":
        data["rows"].append(data["rows"][0])
    else:
        data["source"]["quality"]["limited_activities"] = 1
    assert evidence.build_evidence(**data)["episodes"] == []


@pytest.mark.parametrize("before,after", [(0., 2.), (2., 0.), (.001, 2.), (2., .001), (0., 0.)])
def test_intermittent_zone_is_retained_as_an_observed_covariate(before, after):
    data = fixture()
    for a in data["source"]["activities"]:
        q = before if a["date"] < str(START) else after
        a["zones"][4].update(equivalent_time_min=q, raw_time_min=q/2)
    result = episode(data)
    assert result["dose"]["Z5"]["baseline_q"] == pytest.approx(before*7)
    assert result["dose"]["Z5"]["actual_q"] == pytest.approx(after*7)
    expected = dose_changes(before*7, after*7, before*3.5, after*3.5)
    assert (result["dose_change"]["Z5"], result["intensity_change"]["Z5"]) == pytest.approx(expected)
    assert result["outcomes"]  # Independent result survives, including Z5 covariates.


def test_unknown_sparse_minutes_are_not_replaced_with_zero():
    data = fixture()
    data["source"]["activities"][14]["zones"][4]["raw_time_min"] = None
    assert evidence.build_evidence(**data)["episodes"] == []


def test_asof_future_outcomes_and_future_manual_revision_do_not_leak():
    data = fixture()
    future = ti(TODAY+timedelta(days=1), 1)
    assert episode({**data, "trainability": data["trainability"]+[future]}) == episode(data)
    old = deepcopy(data["entries"][-1])
    newer = deepcopy(old)
    newer.update(revision=2, recorded_at=str(TODAY+timedelta(days=2))+"T12:00:00Z")
    newer["payload"].update(dict.fromkeys(FIELDS, 5), pain_or_illness=True)
    data["entries"].append(newer)
    data["history"]["days"][-1]["daily_report"] = newer["payload"]
    data["history"]["days"][-1]["daily_revision"] = 2
    result = episode(data)
    assert not result["confounded"]
    assert result["response"]["recovered"]


def test_retention_dedup_revision_invalidation_and_availability_dates():
    data = fixture()
    first = evidence.build_evidence(**data)
    later = evidence.build_evidence(**{**data, "today": TODAY+timedelta(days=1), "retained": first})
    assert len(later["episodes"]) == 1
    assert later["episodes"][0]["observed_on"] == str(TODAY)
    # Reanalysis IDs alone do not change the first knowledge date.
    data["history"]["generation_id"] = "new-import"
    assert episode({**data, "today": TODAY+timedelta(days=1), "retained": first})["observed_on"] == str(TODAY)
    # Corrected recent outcome replaces evidence and has a new availability.
    data["trainability"][-1]["index"]["general"]["index"] = 8
    assert episode({**data, "today": TODAY+timedelta(days=1), "retained": first})["observed_on"] == str(TODAY+timedelta(days=1))
    # A removed recent source day invalidates old credit.
    data["rows"] = [r for r in data["rows"] if r["date"] != str(START)]
    assert evidence.build_evidence(**{**data, "retained": first})["episodes"] == []
    # Sliding import loss preserves prior evidence, no synthetic reconstruction.
    data["rows"] = []
    data["source"]["period_start"] = str(TODAY+timedelta(days=30))
    retained = evidence.build_evidence(**{**data, "today": TODAY+timedelta(days=40), "retained": first})
    assert retained["episodes"] == first["episodes"]
    assert not retained["current"]["history_usable"]
    assert evidence.build_evidence(**{**data, "context_key": "changed", "retained": first})["episodes"] == []


def test_future_exposure_rows_never_become_observed_evidence():
    data = fixture()
    future = [{"date": str(TODAY+timedelta(days=1)), "zone": z, "effective_load": 1000} for z in COMPONENTS]
    assert evidence.build_evidence(**{**data, "rows": data["rows"]+future}) == evidence.build_evidence(**data)


def test_conflicting_independent_protocols_do_not_select_only_the_favorable_outcome():
    data = fixture()
    protocol = {"protocol": "global test", "protocol_version": "1", "unit": "W", "direction": "HIGHER",
                "conditions": "same protocol", "components": [], "comparable": True}
    for offset, value in ((-1, 100), (11, 80)):
        day = str(START+timedelta(days=offset))
        data["entries"].append(entry("TEST", f"power-{day}", {**protocol, "day": day, "value": value}))
    result = episode(data)
    assert result["outcomes"] == []
    assert result["confounded"]
    assert "CONFLICTING_OUTCOMES" in result["provenance"]["confounds"]


def test_fixed_episodes_have_disjoint_exposure_followup_and_outcome_windows():
    data = fixture()
    second = fixture()
    for key in ("rows", "entries"):
        for item in second[key]:
            if key == "rows":
                item["date"] = str(evidence.date.fromisoformat(item["date"])+timedelta(days=14))
            else:
                day = str(evidence.date.fromisoformat(item["entry_key"])+timedelta(days=14))
                item["entry_key"] = item["payload"]["day"] = day
    for row in second["history"]["days"]:
        row["day"] = str(evidence.date.fromisoformat(row["day"])+timedelta(days=14))
    for collection in (second["source"]["activities"], second["source"]["strength"]["daily"]):
        for item in collection:
            item["date"] = str(evidence.date.fromisoformat(item["date"])+timedelta(days=14))
    for row in second["trainability"]:
        row["local_date"] = str(evidence.date.fromisoformat(row["local_date"])+timedelta(days=14))
    # Append the second exposure/followup only, retaining actual previous days.
    for key in ("rows", "entries"):
        data[key] += [r for r in second[key] if (r["date"] if key == "rows" else r["entry_key"]) >= str(TODAY)]
    data["history"]["days"] += [r for r in second["history"]["days"] if r["day"] >= str(TODAY)]
    data["source"]["activities"] += [r for r in second["source"]["activities"] if r["date"] >= str(TODAY)]
    data["source"]["strength"]["daily"] += [r for r in second["source"]["strength"]["daily"] if r["date"] >= str(TODAY)]
    data["trainability"] += [r for r in second["trainability"] if r["local_date"] >= str(TODAY)]
    data["today"] += timedelta(days=14)
    data["source"]["period_end"] = str(data["today"]-timedelta(days=1))
    result = evidence.build_evidence(**data)
    assert len(result["episodes"]) == 2
    one, two = result["episodes"]
    assert one["end"] < two["start"]
    assert all(one["load_end"] < o["outcome_day"] <= one["end"] for o in one["outcomes"])
    assert all(two["load_end"] < o["outcome_day"] <= two["end"] for o in two["outcomes"])


def test_current_flags_and_stale_actual_history_are_separate_from_learned_capacity():
    data = fixture()
    last = data["entries"][-1]
    last["payload"]["pain_or_illness"] = True
    data["history"]["days"][-1]["body_observations"] = {"context_status": "REVIEW_LAB_REFERENCE"}
    result = evidence.build_evidence(**data)
    assert result["current"]["illness_hold"] and result["current"]["lab_review"]
    assert isinstance(result["current"]["families"], int)
    data["today"] += timedelta(days=3)
    current = evidence.build_evidence(**data)["current"]
    assert current["illness_hold"]  # Silence does not clear an explicit report.
    assert not current["history_usable"]
    assert current["lab_review"]  # Missing raw followup cannot resolve a dated finding.


def test_a_past_return_does_not_prove_current_recovery_when_recent_same_channels_rise():
    data = fixture()
    assert evidence.build_evidence(**data)["current"]["recovered"] is True
    today = str(TODAY)
    payload = {"day": today, **dict.fromkeys(FIELDS, 3)}
    data["entries"].append(entry("DAILY", today, payload))
    data["history"]["days"].append({"day": today, "daily_report": payload,
                                     "channels": [{"key": k, "score": 50} for k in FIELDS]})
    result = evidence.build_evidence(**data)
    assert result["episodes"][0]["response"]["recovered"]
    assert result["current"]["recovered"] is False


def test_physiological_baseline_drift_cannot_masquerade_as_an_observed_return():
    data = fixture()
    for row in data["history"]["days"]:
        later = row["day"] >= str(START)
        # A rolling reference has already absorbed the higher raw resting HR.
        # The episode must still compare it with the pre-exposure reference.
        row["channels"].append({"key": "resting_hr", "score": 50, "raw": 75 if later else 60,
                                 "baseline": {"median": 75 if later else 60, "spread": 3}})
    result = episode(data)
    assert result["response"]["references"]["resting_hr"]["median"] == 60
    assert result["response"]["recovered"] is False
    assert result["outcomes"] == []


def laboratory(day, key, value, *, analyte="CK", qualifier="EQ", comparable=True, **changes):
    result = {"analyte": analyte, "value": value, "unit": "U/L", "sample": "SERUM", "reference_high": 180., "qualifier": qualifier}
    result.update(changes.pop("result", {}))
    report = {"day": str(day), "sample_id": key, "collection_time": "08:00", "laboratory": "same laboratory",
              "protocol": "same protocol", "fasting": "YES", "comparable": comparable, "results": [result], **changes}
    return entry("LAB", key, report)


def test_sparse_abnormal_laboratory_remains_review_until_new_comparable_exact_result():
    data = fixture()
    data["entries"].append(laboratory(TODAY-timedelta(days=5), "a"*32, 500.))
    current = evidence.build_evidence(**data)["current"]
    assert current["lab_review"]
    assert current["lab_review_findings"] == [{"analyte": "CK", "observed_on": str(TODAY-timedelta(days=5)), "status": "HIGH", "age_days": 5}]
    # Review context is separate: no laboratory score is invented for today.
    assert current["stress_score"] == evidence.build_evidence(**fixture())["current"]["stress_score"]
    data["entries"].append(laboratory(TODAY-timedelta(days=1), "b"*32, 100.))
    current = evidence.build_evidence(**data)["current"]
    assert not current["lab_review"]
    assert current["lab_review_findings"] == []


@pytest.mark.parametrize("change", ["censored", "no_reference", "wrong_limit", "different_protocol", "different_sample", "not_comparable", "unrelated", "future_recorded"])
def test_missing_or_noncomparable_new_laboratory_does_not_silently_clear_abnormal(change):
    data = fixture()
    data["entries"].append(laboratory(TODAY-timedelta(days=5), "a"*32, 500.))
    followup = laboratory(TODAY-timedelta(days=1), "b"*32, 100.)
    if change == "censored":
        followup["payload"]["results"][0]["qualifier"] = "LT"
    elif change == "no_reference":
        followup["payload"]["results"][0]["reference_high"] = None
    elif change == "wrong_limit":
        followup["payload"]["results"][0].update(reference_high=None, reference_low=20.)
    elif change == "different_protocol":
        followup["payload"]["protocol"] = "after training"
    elif change == "different_sample":
        followup["payload"]["results"][0]["sample"] = "PLASMA"
    elif change == "not_comparable":
        followup["payload"]["comparable"] = False
    elif change == "unrelated":
        followup["payload"]["results"][0].update(analyte="CRP", value=2., unit="mg/L", reference_high=5.)
    else:
        followup["recorded_at"] = str(TODAY+timedelta(days=1))+"T08:00:00Z"
    data["entries"].append(followup)
    assert evidence.build_evidence(**data)["current"]["lab_review"]


def test_future_recorded_lab_cannot_create_an_asof_review_from_precomputed_history():
    data = fixture()
    abnormal = laboratory(TODAY-timedelta(days=5), "a"*32, 500.)
    abnormal["recorded_at"] = str(TODAY+timedelta(days=1))+"T08:00:00Z"
    data["entries"].append(abnormal)
    day = next(row for row in data["history"]["days"] if row["day"] == abnormal["payload"]["day"])
    day["body_observations"] = {"context_status": "REVIEW_LAB_REFERENCE"}
    assert not evidence.build_evidence(**data)["current"]["lab_review"]
