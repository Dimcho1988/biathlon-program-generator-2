"""Coach method budgets and Recovery are independent, once-only operations."""
from copy import deepcopy

import pytest
from pydantic import ValidationError

from apps.api import training_plan_engine as engine
from apps.api.management_schemas import IntervalDoseProfile
from biathlon import adaptive_methods, method_dosing
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


@pytest.mark.parametrize('purpose,role,work_seconds', [('BUILDING', .65, 156.), ('MAINTENANCE', .39, 93.6), ('SUPPORTING', .39, 93.6)])
def test_expert_interval_budget_uses_full_or_half_capacity_before_recovery(purpose, role, work_seconds):
    method = interval_method()
    evidence = {'capacity_minutes': 80/60, 'effort_profile': method['interval_profile'],
                'target_speed_kmh': 24., 'target_hr_bpm': None}
    fraction = engine._nominal_fraction(method, purpose, {'building_fraction': .65}, 'GENERAL_PREPARATION', evidence, None)
    assert evidence['capacity_minutes'] * fraction * 60 == pytest.approx(work_seconds)
    ceiling = engine._dose_ceiling(method, purpose, {}, 'GENERAL_PREPARATION', evidence, None, .8)
    assert ceiling == role  # The legacy generic .8 never recaps this method budget.
    blocks = engine._blocks(method, work_seconds/60, evidence, Repository().settings)
    assert sum(b['duration_s'] for b in blocks if b['kind'] == 'WORK') == pytest.approx(40 * int(work_seconds // 40))
    assert engine._dose_fits(blocks, evidence, 'Z5', ceiling)


def test_continuous_65_percent_at_80_recovery_uses_half_role_then_single_scale():
    method = {'zone': 'Z2', 'structure': 'CONTINUOUS'}
    evidence = {'capacity_minutes': 100., 'readiness_policy': adaptive_methods.readiness_policy(method, {}, {'Z1': 90., 'Z2': 80.})}
    nominal = engine._nominal_fraction(method, 'BUILDING', {'building_fraction': .65}, 'GENERAL_PREPARATION', evidence, None)
    recovery = adaptive_methods.readiness_policy(method, {}, {'Z1': 90., 'Z2': 80.})['dose_factor']
    assert evidence['capacity_minutes'] * nominal * recovery == 58.5


@pytest.mark.parametrize("building,maintenance_minutes", [(.6, 39.), (.65, 39.), (.7, 39.)])
@pytest.mark.parametrize("purpose,period", [("MAINTENANCE", "GENERAL_PREPARATION"),
    ("SUPPORTING", "GENERAL_PREPARATION"), ("RECOVERY", "GENERAL_PREPARATION"),
    ("BUILDING", "RE_ENTRY")])
def test_undosed_z2_roles_use_sixty_percent_of_fixed_sixty_five_percent_fallback(building, maintenance_minutes, purpose, period):
    profile = {"building_fraction": building, "maintenance_fraction": .4, "reentry_fraction": .5}
    before = deepcopy(profile)
    method = {"zone": "Z2", "structure": "CONTINUOUS"}
    evidence = {"capacity_minutes": 100.}
    nominal = engine._nominal_fraction(method, purpose, profile, period, evidence, None)
    assert nominal * evidence["capacity_minutes"] == pytest.approx(maintenance_minutes)
    assert evidence["method_budget_role_fraction"] == pytest.approx(.65*.6)
    assert evidence["maintenance_policy"] == "SIXTY_PERCENT_METHOD_BUDGET_NO_RECOVERY_SCALE"
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
        if policy["version"] == "continuous-role-budget-v3":
            assert policy["maintenance_fraction"] == .39
            assert policy["maintenance_fraction"] == policy["building_fraction"] * .6
            assert policy["recovery_adjustment"] == "BUILDING_HALF_DEFICIT_MAINTENANCE_FIXED"


def test_half_continuous_rule_does_not_replace_double_threshold_or_strength_budgets():
    profile = {"building_fraction": .65, "maintenance_fraction": .4}
    double = {"zone": "Z3", "structure": "THRESHOLD_LONG", "double_threshold": True}
    controls = {"double_threshold_fraction": .6}
    assert engine._nominal_fraction(double, "BUILDING", profile, "GENERAL_PREPARATION", {}, controls) == pytest.approx(.78)
    assert engine._dose_ceiling(double, "BUILDING", profile, "GENERAL_PREPARATION", {}, controls) == .39
    strength = {"zone": "STR", "structure": "CIRCUIT", "min_work_min": 12., "max_work_min": 30.}
    assert engine._nominal_fraction(strength, "BUILDING", profile, "GENERAL_PREPARATION", {}, None) == 1.
    assert engine._nominal_fraction(strength, "MAINTENANCE", profile, "GENERAL_PREPARATION", {}, None) == .6


def test_interval_recovery_scales_total_work_once_with_whole_repetitions():
    method = interval_method()
    evidence = {'capacity_minutes': 80/60, 'effort_profile': method['interval_profile'],
                'target_speed_kmh': 24., 'target_hr_bpm': None, 'readiness_dose_factor': .8}
    fraction = engine._nominal_fraction(method, 'MAINTENANCE', {}, 'GENERAL_PREPARATION', evidence, None)
    requested = evidence['capacity_minutes'] * fraction * evidence['readiness_dose_factor']
    assert requested * 60 == pytest.approx(93.6)
    blocks = engine._blocks(method, requested, evidence, Repository().settings)
    assert [b['duration_s'] for b in blocks if b['kind'] == 'WORK'] == [40., 40.]
    assert engine._dose_fits(blocks, evidence, 'Z5', .6)


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
    assert engine._nominal_fraction(continuous, purpose, profile, "RE_ENTRY", {}, None) == .39
    interval = interval_method()
    evidence = {}
    assert engine._nominal_fraction(interval, purpose, profile, "RE_ENTRY", evidence, None) == pytest.approx(1.17)
    assert engine._dose_ceiling(interval, purpose, profile, "RE_ENTRY", evidence, None) == .39


def test_short_variant_preserves_the_prescribed_curve_duration():
    method = interval_method()
    method['title'] = 'Example'
    short = adaptive_methods.short_variant(method)
    original, altered = method['interval_profile'], short['interval_profile']
    assert altered['work_seconds'] < original['work_seconds']
    assert altered['work_seconds'] * altered['speed_time_duration_ratio'] == original['work_seconds'] * original['speed_time_duration_ratio']


def test_short_structure_survives_capacity_recalculation_and_resolved_parent():
    method = {**interval_method(), 'title': 'Example', 'purpose': 'BUILDING'}
    method_dosing.prepare_native_budget(method, {'capacity_minutes': 100.}, {})
    short = adaptive_methods.short_variant(method)
    for capacity, expected_max in [(100., 12), (.5, 6), (100., 12)]:
        evidence = {'capacity_minutes': capacity}
        method_dosing.prepare_native_budget(short, evidence, {})
        assert short['interval_profile']['min_repetitions'] == 4
        assert short['interval_profile']['max_repetitions'] == expected_max
        assert short['min_work_min'] == 1.
        assert short['max_work_min'] == min(3., capacity*3)
        evidence.update(target_speed_kmh=24., target_hr_bpm=None)
        blocks = engine._blocks(short, short['max_work_min'], evidence, Repository().settings)
        assert len([b for b in blocks if b['kind'] == 'WORK']) == expected_max


def test_mixed_support_retains_its_own_work_bounds_without_capping_source_method():
    source = dict(id='Z2', title='Example', zone='Z2', purpose='BUILDING', structure='CONTINUOUS',
                  min_work_min=15., max_work_min=60., warmup_min=10., cooldown_min=5.,
                  periods=('GENERAL_PREPARATION',))
    mixed = adaptive_methods.mixed_variants([source])[0]
    for capacity in (100., 10., 100.):
        method_dosing.prepare_native_budget(mixed, {'capacity_minutes': capacity}, {})
        assert mixed['min_work_min'] == 2.
        assert mixed['max_work_min'] == min(20., capacity)
    method_dosing.prepare_native_budget(source, {'capacity_minutes': 100.}, {})
    assert source['max_work_min'] == 100.  # A source example is not an execution limit.


def joint_evidence(recovery=1.):
    return {'capacity_minutes': 100., 'target_hr_bpm': 160.,
            'secondary_capacity': {'capacity_minutes': 600., 'zone': 'Z2', 'target_hr_bpm': 148.},
            'secondary_max_fraction': .3, 'readiness_dose_factor': recovery,
            'zone_tmax_minutes': {z: 100. for z in ('Z1', 'Z2', 'Z3', 'Z4', 'Z5')},
            'dose_settings': {'zone_bounds_bpm': [80, 137, 148, 160, 170, 180], 'hrmax_bpm': 180.}}


def joint_blocks(primary, secondary):
    return [{'kind': 'WORK', 'zone': 'Z3', 'duration_min': primary, 'target_hr_bpm': 160.},
            {'kind': 'WORK', 'zone': 'Z2', 'duration_min': secondary, 'target_hr_bpm': 148.}]


def test_received_load_does_not_reduce_independent_method_budgets():
    evidence = joint_evidence()
    # Z3 E is 72, above its 65-minute method budget, but direct work fits.
    # E belongs to the plan's common constraints, not this local dose check.
    assert engine._dose_fits(joint_blocks(60., 60.), evidence, 'Z3', .65)
    assert not engine._dose_fits(joint_blocks(66., 60.), evidence, 'Z3', .65)


def test_mixed_method_recovery_applies_once_to_direct_work():
    evidence = joint_evidence(.8)
    assert engine._dose_fits(joint_blocks(52., 180.), evidence, 'Z3', .65*.8)
    assert not engine._dose_fits(joint_blocks(52.2, 180.), evidence, 'Z3', .65*.8)
    assert not engine._dose_fits(joint_blocks(52., 180.1), evidence, 'Z3', .65*.8)


def test_mixed_continuous_support_keeps_half_budget_and_single_recovery():
    evidence = joint_evidence(.8)
    evidence["secondary_capacity"]["capacity_minutes"] = 400.
    profile = {"building_fraction": .65, "maintenance_fraction": .4, "reentry_fraction": .5}
    method = {"zone": "Z3", "structure": "THRESHOLD_LONG"}
    ceiling = engine._dose_ceiling(method, "BUILDING", profile, "GENERAL_PREPARATION", evidence, None)
    assert evidence["secondary_max_fraction"] == .39
    assert evidence["session_dose_policy"] == "DIRECT_WORK_WITHIN_METHOD_ROLE_BUDGETS_E_FOR_PLAN_CONSTRAINTS"
    assert engine._dose_fits(joint_blocks(52., 156.), evidence, "Z3", ceiling * .8)
    assert not engine._dose_fits(joint_blocks(52., 156.1), evidence, "Z3", ceiling * .8)


def test_threshold_high_applies_building_fraction_to_both_independent_budgets(monkeypatch):
    method = interval_method()
    method.update(structure='THRESHOLD_HIGH', zone='Z3')
    method['interval_profile'].update(zone='Z4', total_capacity_ratio=1.,
        continuous_capacity_min=20., work_seconds=120, recovery_seconds=120)
    secondary = {'capacity_minutes': 20., 'effort_profile': method['interval_profile']}
    monkeypatch.setattr(engine, 'capacity_for', lambda *args, **kwargs: deepcopy(secondary))
    settings = Repository().settings
    capacities = {'Z1': 240., 'Z2': 120., 'Z3': 50., 'Z4': 20., 'Z5': 20.}
    evidence = {'capacity_minutes': 50., 'target_hr_bpm': 160., 'target_speed_kmh': None,
                'readiness_dose_factor': .8, 'zone_tmax_minutes': capacities,
                'dose_settings': {'zone_bounds_bpm': list(settings.zone_bounds_bpm), 'hrmax_bpm': settings.hrmax_bpm}}
    profile = {'building_fraction': .6}
    nominal = engine._nominal_fraction(method, 'BUILDING', profile, 'SPECIAL_PREPARATION', evidence, None)
    assert engine._prepare_threshold_high_budget(method, evidence, profile, Repository().settings,
        None, (None, [], []), TODAY, primary_fraction=nominal*.5, readiness_factor=.8)
    requested = evidence['primary_requested_work']
    assert requested == 12.
    assert evidence['combination_high_work_cap'] == pytest.approx(4.8)
    blocks = engine._blocks(method, requested, evidence, Repository().settings)
    ceiling = engine._dose_ceiling(method, 'BUILDING', profile, 'SPECIAL_PREPARATION', evidence, None)
    assert engine._dose_fits(blocks, evidence, 'Z3', ceiling*.8)
    work = [b for b in blocks if b['kind'] == 'WORK']
    assert [b['duration_min'] for b in work if b['zone'] == 'Z4'] == [2.]*2
    assert sum(b['duration_min'] for b in work if b['zone'] == 'Z3') == 12.
    # Received load includes both components after whole-repetition rounding.
    direct, effective, _ = engine._canonical_load(blocks, settings, [], TODAY, zone_tmax_minutes=capacities)
    assert direct['Z3'] == 12. and direct['Z4'] == 4.
    assert effective['Z3'] == pytest.approx(21.2)
    assert effective['Z3'] > direct['Z3']
    assert effective['Z2'] == 30.8
    assert effective['Z1'] == pytest.approx(direct['Z1'] + 30.8)


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
    assert requested == pytest.approx(2.6)
    work = [b for b in engine._blocks(method, requested, evidence, SETTINGS) if b['kind'] == 'WORK']
    assert len(work) == 3
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
    from tests.api.test_methodology_completion import proven_shortage
    proven_shortage(monkeypatch)
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
