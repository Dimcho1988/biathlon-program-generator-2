"""Coach method budgets and Recovery are independent, once-only operations."""
from copy import deepcopy

import pytest
from pydantic import ValidationError

from apps.api import training_plan_engine as engine
from apps.api.management_schemas import IntervalDoseProfile
from biathlon import adaptive_methods
from tests.api.test_training_plan_engine import Repository, TODAY


def interval_method():
    profile = {
        'zone': 'Z5', 'sport': 'Run', 'continuous_capacity_min': 80/60,
        'assessed_on': TODAY.isoformat(), 'effort': 'Контролирано повторяемо усилие',
        'work_seconds': 40, 'recovery_seconds': 40, 'min_repetitions': 2,
        'max_repetitions': 20, 'total_capacity_ratio': 3., 'reserve_repetitions': 2,
        'speed_time_duration_ratio': 2.,
    }
    return {'id': 'EXPERT-EXAMPLE', 'zone': 'Z5', 'structure': 'METABOLIC_INTERVALS',
            'interval_profile': profile, 'min_work_min': 80/60, 'max_work_min': 800/60,
            'warmup_min': 15., 'cooldown_min': 10., 'instructions': profile['effort']}


@pytest.mark.parametrize('purpose,role,work_seconds', [('BUILDING', 1., 240.), ('MAINTENANCE', .5, 120.), ('SUPPORTING', .5, 120.)])
def test_expert_interval_budget_uses_full_or_half_capacity_before_recovery(purpose, role, work_seconds):
    method = interval_method()
    evidence = {'capacity_minutes': 80/60, 'effort_profile': method['interval_profile'],
                'target_speed_kmh': 24., 'target_hr_bpm': None}
    fraction = engine._nominal_fraction(method, purpose, {'building_fraction': .65}, 'GENERAL_PREPARATION', evidence, None)
    assert evidence['capacity_minutes'] * fraction * 60 == pytest.approx(work_seconds)
    ceiling = engine._dose_ceiling(method, purpose, {}, 'GENERAL_PREPARATION', evidence, None, .8)
    assert ceiling == role  # The legacy generic .8 never recaps this method budget.
    blocks = engine._blocks(method, work_seconds/60, evidence, Repository().settings)
    assert sum(b['duration_s'] for b in blocks if b['kind'] == 'WORK') == pytest.approx(work_seconds)
    assert engine._dose_fits(blocks, evidence, 'Z5', ceiling)


def test_continuous_65_percent_at_80_recovery_is_52_minutes():
    method = {'zone': 'Z2', 'structure': 'CONTINUOUS'}
    evidence = {'capacity_minutes': 100.}
    nominal = engine._nominal_fraction(method, 'BUILDING', {'building_fraction': .65}, 'GENERAL_PREPARATION', evidence, None)
    recovery = adaptive_methods.readiness_policy(method, {}, {'Z1': 90., 'Z2': 80.})['dose_factor']
    assert evidence['capacity_minutes'] * nominal * recovery == 52.


@pytest.mark.parametrize("building,maintenance_minutes", [(.6, 30.), (.65, 32.5), (.7, 35.)])
@pytest.mark.parametrize("purpose,period", [("MAINTENANCE", "GENERAL_PREPARATION"),
    ("SUPPORTING", "GENERAL_PREPARATION"), ("RECOVERY", "GENERAL_PREPARATION"),
    ("BUILDING", "RE_ENTRY")])
def test_continuous_roles_use_half_building_budget_despite_stale_saved_settings(building, maintenance_minutes, purpose, period):
    profile = {"building_fraction": building, "maintenance_fraction": .4, "reentry_fraction": .5}
    before = deepcopy(profile)
    method = {"zone": "Z2", "structure": "CONTINUOUS"}
    evidence = {"capacity_minutes": 100.}
    nominal = engine._nominal_fraction(method, purpose, profile, period, evidence, None)
    assert nominal * evidence["capacity_minutes"] == pytest.approx(maintenance_minutes)
    assert evidence["method_budget_role_fraction"] == .5
    assert evidence["maintenance_policy"] == "HALF_DEVELOPING_CONTINUOUS_BUDGET_BEFORE_READINESS_SCALE"
    ceiling = engine._dose_ceiling(method, purpose, profile, period, evidence, None)
    assert ceiling == nominal
    recovery = adaptive_methods.readiness_policy(method, profile, {"Z1": 100., "Z2": 80.})["dose_factor"]
    work = maintenance_minutes * recovery
    assert engine._dose_fits([{"kind": "WORK", "zone": "Z2", "duration_min": work}], evidence, "Z2", ceiling * recovery)
    assert not engine._dose_fits([{"kind": "WORK", "zone": "Z2", "duration_min": work + .2}], evidence, "Z2", ceiling * recovery)
    assert profile == before


def test_continuous_catalogue_reports_the_same_derived_roles_as_engine():
    from biathlon.training_methods import resolved_methods

    profile = {"building_fraction": .65, "maintenance_fraction": .4, "reentry_fraction": .5}
    for method in resolved_methods(profile):
        policy = method["dosing_policy"]
        if policy["version"] == "continuous-role-budget-v2":
            assert policy["maintenance_fraction"] == .325
            assert policy["maintenance_fraction"] == policy["building_fraction"] / 2
            assert policy["recovery_adjustment"] == "MULTIPLY_VOLUME_ONCE"


def test_half_continuous_rule_does_not_replace_double_threshold_or_strength_budgets():
    profile = {"building_fraction": .65, "maintenance_fraction": .4}
    double = {"zone": "Z3", "structure": "THRESHOLD_LONG", "double_threshold": True}
    controls = {"double_threshold_fraction": .6}
    assert engine._nominal_fraction(double, "BUILDING", profile, "GENERAL_PREPARATION", {}, controls) == 1.2
    assert engine._dose_ceiling(double, "BUILDING", profile, "GENERAL_PREPARATION", {}, controls) == .6
    strength = {"zone": "STR", "structure": "CIRCUIT", "min_work_min": 12., "max_work_min": 30.}
    assert engine._nominal_fraction(strength, "BUILDING", profile, "GENERAL_PREPARATION", {}, None) == 1.
    assert engine._nominal_fraction(strength, "MAINTENANCE", profile, "GENERAL_PREPARATION", {}, None) == .5


def test_interval_recovery_scales_total_work_once_with_whole_repetitions():
    method = interval_method()
    evidence = {'capacity_minutes': 80/60, 'effort_profile': method['interval_profile'],
                'target_speed_kmh': 24., 'target_hr_bpm': None, 'readiness_dose_factor': .8}
    fraction = engine._nominal_fraction(method, 'MAINTENANCE', {}, 'GENERAL_PREPARATION', evidence, None)
    requested = evidence['capacity_minutes'] * fraction * .8
    assert requested * 60 == pytest.approx(96.)
    blocks = engine._blocks(method, requested, evidence, Repository().settings)
    assert [b['duration_s'] for b in blocks if b['kind'] == 'WORK'] == [40., 40.]
    assert engine._dose_fits(blocks, evidence, 'Z5', .5 * .8)


def test_mixed_components_have_own_budgets_without_sum_of_fractions_cap():
    evidence = {'capacity_minutes': 100., 'secondary_capacity': {'capacity_minutes': 100.},
                'secondary_max_fraction': .3, 'readiness_dose_factor': 1.}
    blocks = [{'kind': 'WORK', 'zone': 'Z3', 'duration_min': 40.},
              {'kind': 'WORK', 'zone': 'Z1', 'duration_min': 30.}]
    assert engine._dose_usage(blocks, evidence, 'Z3') == .4
    assert engine._dose_fits(blocks, evidence, 'Z3', .65)
    # No .5 sum ceiling; each component's budget is still enforced.
    blocks[-1]['duration_min'] = 31.
    assert not engine._dose_fits(blocks, evidence, 'Z3', .65)


def test_explicit_expert_schema_accepts_450_percent_and_does_not_infer_speed_coefficient():
    method = interval_method()
    data = deepcopy(method['interval_profile'])
    data['total_capacity_ratio'] = 4.5
    validated = IntervalDoseProfile.model_validate(data)
    assert validated.total_capacity_ratio == 4.5
    assert validated.speed_time_duration_ratio == 2.
    with pytest.raises(ValueError, match="derived total capacity budget must be finite"):
        IntervalDoseProfile.model_validate({**data, 'continuous_capacity_min': 4., 'total_capacity_ratio': 1e308})
    data.pop('speed_time_duration_ratio')
    assert IntervalDoseProfile.model_validate(data).speed_time_duration_ratio is None
    with pytest.raises(ValidationError):
        IntervalDoseProfile.model_validate({**data, 'speed_time_duration_ratio': 1.})


@pytest.mark.parametrize("purpose", ["BUILDING", "MAINTENANCE"])
def test_reentry_uses_the_maintenance_role_not_a_separate_legacy_fraction(purpose):
    profile = {"building_fraction": .65, "maintenance_fraction": .35, "reentry_fraction": .5}
    continuous = {"zone": "Z2", "structure": "CONTINUOUS"}
    assert engine._nominal_fraction(continuous, purpose, profile, "RE_ENTRY", {}, None) == .325
    interval = interval_method()
    evidence = {}
    assert engine._nominal_fraction(interval, purpose, profile, "RE_ENTRY", evidence, None) == 1.5
    assert engine._dose_ceiling(interval, purpose, profile, "RE_ENTRY", evidence, None) == .5


def test_short_variant_preserves_the_prescribed_curve_duration():
    method = interval_method()
    method['title'] = 'Example'
    short = adaptive_methods.short_variant(method)
    original, altered = method['interval_profile'], short['interval_profile']
    assert altered['work_seconds'] < original['work_seconds']
    assert altered['work_seconds'] * altered['speed_time_duration_ratio'] == original['work_seconds'] * original['speed_time_duration_ratio']


def joint_evidence(recovery=1.):
    return {'capacity_minutes': 100., 'target_hr_bpm': 160.,
            'secondary_capacity': {'capacity_minutes': 200., 'zone': 'Z2', 'target_hr_bpm': 148.},
            'secondary_max_fraction': .3, 'readiness_dose_factor': recovery,
            'zone_tmax_minutes': {z: 100. for z in ('Z1', 'Z2', 'Z3', 'Z4', 'Z5')},
            'dose_settings': {'zone_bounds_bpm': [80, 137, 148, 160, 170, 180], 'hrmax_bpm': 180.}}


def joint_blocks(primary, secondary):
    return [{'kind': 'WORK', 'zone': 'Z3', 'duration_min': primary, 'target_hr_bpm': 160.},
            {'kind': 'WORK', 'zone': 'Z2', 'duration_min': secondary, 'target_hr_bpm': 148.}]


def test_mixed_session_received_load_reduces_a_combination_that_fits_direct_budgets():
    evidence = joint_evidence()
    blocks = joint_blocks(60., 60.)
    assert engine._dose_usage(blocks, evidence, 'Z3') == .6
    # Z2 donates 20% of its whole 60 Q: Z3 receives 72, above its 65 Q budget.
    assert not engine._dose_fits(blocks, evidence, 'Z3', .65)
    # Both components still reach 50%, but received Z3=60 and Z2=55 now fit.
    assert engine._dose_fits(joint_blocks(50., 50.), evidence, 'Z3', .65)


def test_mixed_session_received_load_budget_does_not_apply_recovery_twice():
    evidence = joint_evidence(.8)
    assert engine._dose_fits(joint_blocks(47., 40.), evidence, 'Z3', .65*.8)


def test_mixed_continuous_support_uses_derived_half_budget_and_single_recovery():
    evidence = joint_evidence(.8)
    evidence["secondary_capacity"]["capacity_minutes"] = 100.
    profile = {"building_fraction": .65, "maintenance_fraction": .4, "reentry_fraction": .5}
    method = {"zone": "Z3", "structure": "THRESHOLD_LONG"}
    ceiling = engine._dose_ceiling(method, "BUILDING", profile, "GENERAL_PREPARATION", evidence, None)
    assert evidence["secondary_max_fraction"] == .325
    # Z3 crosses the spill gate: Z2 receives 20.8 own Q + 5.2 from Z3,
    # exactly 100 × .325 × .8 = 26. The direct work by itself is below 26.
    assert engine._dose_fits(joint_blocks(52., 20.8), evidence, "Z3", ceiling * .8)
    assert not engine._dose_fits(joint_blocks(52., 21.8), evidence, "Z3", ceiling * .8)


def test_expert_effort_coefficient_prescribes_curve_speed_and_separate_total_budget():
    from tests.api.test_method_curve_capacity import single_anchor_context, SETTINGS

    method = interval_method()
    speed, context = single_anchor_context()
    speed.update(active_test_keys=['anchor'], tests=[{'entry_key': 'anchor', 'payload': context[1][0]}],
                 hr_model=context[0].summary())
    assert engine.capacity_for(method, SETTINGS, speed, context, TODAY) is None
    evidence = engine.capacity_for(method, SETTINGS, speed, context, TODAY, use_model_prior=True)
    assert evidence['capacity_minutes'] == pytest.approx(80/60)
    assert evidence['target_speed_kmh'] == pytest.approx(context[0].curve.speed(80)*3.6)
    fraction = engine._nominal_fraction(method, 'BUILDING', {}, 'GENERAL_PREPARATION', evidence, None)
    requested = evidence['capacity_minutes'] * fraction
    assert requested == pytest.approx(4.)
    work = [b for b in engine._blocks(method, requested, evidence, SETTINGS) if b['kind'] == 'WORK']
    assert len(work) == 6
    assert all(b['target_speed_kmh'] == pytest.approx(evidence['target_speed_kmh'], abs=.001) for b in work)


def test_expert_effort_profile_falls_back_to_explicit_tmax_without_a_curve():
    from tests.api.test_method_curve_capacity import SETTINGS

    method = interval_method()
    method['interval_profile']['continuous_capacity_min'] = 2.
    evidence = engine.capacity_for(method, SETTINGS, None, (None, [], []), TODAY)
    assert evidence['capacity_minutes'] == 2.
    assert evidence['capacity_source'] == 'COACH_EFFORT_CAPACITY'
    assert evidence['target_speed_kmh'] is None


def test_threshold_high_uses_resolved_secondary_curve_speed():
    method = interval_method()
    method.update(structure='THRESHOLD_HIGH', zone='Z3')
    method['interval_profile']['target_speed_kmh'] = 18.
    resolved = {**method['interval_profile'], 'target_speed_kmh': 25., 'continuous_capacity_min': 80/60}
    evidence = {'capacity_minutes': 30., 'target_hr_bpm': 155., 'target_speed_kmh': 15.,
                'secondary_capacity': {'capacity_minutes': 80/60, 'effort_profile': resolved},
                'primary_requested_work': 6., 'combination_high_work_cap': 4.}
    blocks = engine._blocks(method, 6., evidence, Repository().settings)
    high = [b for b in blocks if b['kind'] == 'WORK' and b['zone'] == 'Z5']
    assert high and all(b['target_speed_kmh'] == 25. for b in high)
    assert method['interval_profile']['target_speed_kmh'] == 18.


@pytest.mark.parametrize('method_id,primary_share', [('END-ALT-Z1Z2-01', .5), ('END-ALT-10-05-01', 2/3)])
def test_alternating_evidence_reports_actual_primary_share_separately(monkeypatch, method_id, primary_share):
    from tests.api.test_training_plan_engine import NOW, profile
    from tests.api.test_readiness_adaptive_plan_v2 import fixed_readiness

    fixed_readiness(monkeypatch, 100.)
    body = profile(age_years=30, training_experience_years=5, reentry_days=0,
                   available_minutes=[180]*7, building_fraction=.65)
    method = next(m for m in engine.resolved_methods(body) if m['id'] == method_id)
    # This expert profile admits the existing method's 60-minute structural
    # maximum at the ordinary relative minimum, without changing that gate.
    from tests.api.test_training_plan_engine import reference_speed
    def shorter_capacity(repo, alias, sport):
        return {**reference_speed(repo, alias, sport), 'preliminary_capacity': {'anchors': [
            {'zone': 'Z1', 'kind': 'ESTIMATE', 'duration_s': 180*60},
            {'zone': 'Z2', 'kind': 'ESTIMATE', 'duration_s': 100*60}]}}
    monkeypatch.setattr(engine.model_service, 'speed_view', shorter_capacity)
    monkeypatch.setattr(engine, 'resolved_methods', lambda _: [deepcopy(method)])
    repo = Repository()
    for activity in repo.envelope['snapshot_payload']['load_history']['activities']:
        activity['duration_min'] = 180.
    repo.accents.update(accent_mode='MANUAL', manual_components=['Z2'])
    plan = engine.generate_plan(repo, 'athlete', body, start_date=TODAY, now=NOW)
    sessions = [session for day in plan['days'] for session in day['sessions']]
    assert sessions
    evidence = sessions[0]['dose_evidence']
    assert evidence['requested_primary_work_minutes'] == pytest.approx(evidence['structure_requested_work_minutes']*primary_share, abs=.001)
    assert evidence['primary_work_budget_minutes'] == pytest.approx(evidence['structure_work_budget_minutes']*primary_share, abs=.001)
    primary = sum(b['duration_min'] for b in sessions[0]['blocks'] if b['kind'] == 'WORK' and b['zone'] == 'Z2')
    assert evidence['applied_fraction'] == pytest.approx(primary/evidence['capacity_minutes'], abs=.001)
    assert evidence['base_fraction'] == pytest.approx(evidence['structure_base_fraction']*primary_share)
