"""Race-speed references and repeatable methods on the existing capacity curve."""
from copy import deepcopy
from . import speed_duration, speed_zones, training_guidance, adaptive_methods, preliminary_capacity, dosing_curve

VERSION = "race-specific-speed-methods-v2"
PERIODS = ("SPECIAL_PREPARATION", "PRECOMPETITION", "COMPETITION")


def reference(profile, view, event, today):
    result = {"version":VERSION,"status":"UNAVAILABLE","discipline":profile.get("discipline"),
              "sport":profile.get("sport"),"bands":[],"dose_from_maximum_time":False}
    if not view or view.get("sport") != profile.get("sport") or event.get("source") != "SPEED_DURATION":
        return result
    keys = set(view.get("active_test_keys",[]))
    tests = [e["payload"] for e in view.get("tests",[]) if e["entry_key"] in keys]
    if not tests:
        return result
    try:
        curve = speed_duration.calibrated(tests, prior=preliminary_capacity.curve_from_summary(view.get("preliminary_capacity")))
        seconds = curve.inverse(event["distance_m"],distance=True)
        blend = dosing_curve.from_view(view)
        bands = []
        dosing_bands = []
        # Versioned coaching references around the event. These durations
        # select maximal speeds; the method independently prescribes the dose.
        for name,label,factor in (("BELOW","Под състезателното темпо",1.25),
                                  ("RACE","Състезателно темпо",1.),
                                  ("ABOVE","Над състезателното темпо",.8)):
            duration = seconds*factor
            if not curve.times[0] <= duration <= curve.times[-1]:
                continue
            velocity = curve.speed(duration)*3.6
            zone = speed_zones.classify(view.get("speed_zones"),velocity)
            bands.append({"role":name,"label":label,"speed_kmh":velocity,
                "pace_seconds_km":3600/velocity,"maximum_duration_s":duration,"zone":zone,
                "evidence":"INTERPOLATED" if min(t["duration_s"] for t in tests) <= duration <= max(t["duration_s"] for t in tests) else "EXTRAPOLATED",
                "lactate_reference":training_guidance.lactate_at_speed(profile,profile["sport"],velocity,today)})
            if blend:
                working_speed = blend.speed(duration)*3.6
                dosing_bands.append({**bands[-1], "speed_kmh": working_speed,
                    "pace_seconds_km": 3600/working_speed,
                    "label": {"BELOW":"Под състезателния ориентир", "RACE":"Състезателен ориентир", "ABOVE":"Над състезателния ориентир"}[name]+" · 30/70",
                    "zone": speed_zones.classify(view["dosing_model"]["speed_zones"], working_speed),
                    "evidence": "COACH_BLEND_ESTIMATE", "test_curve_speed_kmh": velocity,
                    "dosing_model_version": dosing_curve.VERSION,
                    "lactate_reference": training_guidance.lactate_at_speed(profile,profile["sport"],working_speed,today)})
        return {**result,"status":"AVAILABLE","duration_s":seconds,
            "speed_kmh":curve.speed(seconds)*3.6,"bands":bands,"dosing_bands":dosing_bands,"accepted_test_count":len(tests),
            "active_test_keys":sorted(keys),"source_generation_id":view.get("source_generation_id"),
            "source_revision":view.get("source_revision"),"model_version":view.get("model_version"),
            "lactate_basis":"PERSONAL_TEST_PROTOCOL_ONLY"}
    except (ValueError,KeyError):
        return result


def methods(profile, reference, base_methods):
    # One accepted anchor already defines the available scaled individual
    # curve. Its estimated continuation can select a method; capacity and
    # whole-dose checks remain separate from the number of measured tests.
    if reference.get("status") != "AVAILABLE" or reference.get("accepted_test_count",0) < 1:
        return []
    if not (profile.get("planning_controls") or {}).get("automatic_intervals",True):
        return []
    template = next((m for m in base_methods if m["structure"] == "MODEL_INTERVALS" and not m.get("mixed_component")),None)
    if template is None:
        return []
    result = []
    for band in reference.get("dosing_bands") or reference["bands"]:
        zone = band["zone"]
        if zone not in {"Z2","Z3","Z4","Z5"}:
            continue
        # Explicit individual interval profiles retain ownership of their zone.
        if any(p["zone"] == zone for p in profile.get("interval_profiles",[])):
            continue
        m = deepcopy(template)
        work = min(180.,max(20.,band["maximum_duration_s"]*.1))
        if adaptive_methods.developmental(profile):
            work = min(work,30.)
        work = max(10.,round(work/5)*5)
        rest = work*(2 if adaptive_methods.developmental(profile) else 1)
        minimum, maximum = 3,12
        m.update(id=f"ONFLOWS-RACE-{band['role']}-{zone}-V1",title=band["label"]+" · "+profile["discipline"],
            zone=zone,sports=(profile["sport"],),periods=PERIODS,purpose="BUILDING",
            min_work_min=minimum*work/60,max_work_min=maximum*work/60,minimum_fraction=.1,
            warmup_min=15.,cooldown_min=10.,race_specific=band,
            instructions="Следвай зададеното темпо на подходящ равен участък с резерв за още две качествени отсечки. Запази почивките. Лактатът се сравнява само с личен съпоставим протокол.",
            implementation_profile=VERSION,source_id="INDIVIDUAL_RACE_CURVE",source_version="1",
            adaptation="Скоростта следва индивидуалната крива около основната дисциплина. Дозата следва метода, Q/E и Recovery; максималното време не е тренировъчен обем.")
        m["interval_template"] = {"zone":zone,"work_seconds":work,"recovery_seconds":rest,
            "min_repetitions":minimum,"max_repetitions":maximum,"reserve_repetitions":2,
            "total_capacity_ratio":1.,"rest_type":"ACTIVE_Z1","dose_status":"VERSIONED_COACH_RACE_METHOD"}
        result.append(m)
    return result
