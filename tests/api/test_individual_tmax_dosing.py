"""The source recipe must never determine an athlete's total work capacity."""
from copy import deepcopy

import pytest
from pydantic import ValidationError

from apps.api import training_plan_engine as engine
from apps.api.method_library_service import validate_preferences
from apps.api.management_schemas import MethodLibraryPreferences
from biathlon import method_dosing, method_library as lib
from tests.api.test_sourced_method_library import recipe, athlete, view, adapter


@pytest.mark.parametrize('capacity', [5., 10., 20.])
@pytest.mark.parametrize('building', [.6, .65, .7])
@pytest.mark.parametrize('role', ['BUILDING', 'MAINTENANCE', 'SUPPORTING'])
def test_interval_uses_individual_tmax_method_coefficient_role_and_half_deficit(capacity, building, role):
    method = dict(id='CHECK', zone='Z4', structure='MODEL_INTERVALS', min_work_min=3., max_work_min=3.,
        interval_template=dict(work_seconds=90, recovery_seconds=90, min_repetitions=2, max_repetitions=2, total_capacity_ratio=2.5))
    evidence = dict(capacity_minutes=capacity, readiness_policy=dict(observed_dose_factor=.8))
    f = engine._nominal_fraction(method, role, {'building_fraction':building}, 'GENERAL_PREPARATION', evidence, {})
    expected = capacity*2.5*building*(.9 if role == 'BUILDING' else .6)
    assert capacity*f*evidence['readiness_dose_factor'] == pytest.approx(expected)
    assert method['max_work_min'] == pytest.approx(capacity*2.5)
    ceiling = engine._dose_ceiling(method, role, {'building_fraction':building}, 'GENERAL_PREPARATION', evidence, {})
    assert ceiling == pytest.approx(building*(1 if role == 'BUILDING' else .6))
    blocks = [{'kind':'WORK','zone':'Z4','duration_min':expected}]
    assert engine._dose_fits(blocks, evidence, 'Z4', ceiling*evidence['readiness_dose_factor'])
    assert not engine._dose_fits([{**blocks[0], 'duration_min':expected+.1}], evidence, 'Z4', ceiling*evidence['readiness_dose_factor'])


def test_library_source_counts_do_not_change_capacity_or_role_dose(monkeypatch):
    source = recipe()
    m, cap, _ = adapter(monkeypatch, source)
    reference = cap['library_work_ceiling_minutes']
    assert lib.resolve(source, athlete(), view())['source_count_policy'] == 'INDIVIDUAL_TMAX_NOT_SOURCE_COUNT'
    for count in [2, 7, 50]:
        changed = deepcopy(source)
        changed['prescription']['repetitions'] = [count, count]
        other, other_cap, _ = adapter(monkeypatch, changed)
        assert other_cap['library_work_ceiling_minutes'] == pytest.approx(reference)
        fraction = engine._nominal_fraction(other, 'BUILDING', {'building_fraction':.65}, 'SPECIAL_PREPARATION', other_cap, {})
        assert fraction*other_cap['capacity_minutes'] == pytest.approx(reference*.65)


def test_recipe_mixed_components_each_use_half_independent_capacity():
    parts = [dict(kind='WORK',zone='Z5',work_duration_seconds=30.,continuous_capacity_seconds=300.),
             dict(kind='WORK',zone='Z3',work_duration_seconds=70.,continuous_capacity_seconds=1800.)]
    cap, allocation = method_dosing.recipe_capacity(parts, 2.5)
    assert allocation == .5
    assert cap == pytest.approx(1250.)
    assert cap*.3 == pytest.approx(300*2.5*.5)
    doubled = [{**s,'continuous_capacity_seconds':s['continuous_capacity_seconds']*2} for s in parts]
    assert method_dosing.recipe_capacity(doubled,2.5)[0] == pytest.approx(cap*2)


def test_explicit_source_range_is_preserved_and_point_tolerance_is_zone_clipped():
    zones = {'zones':[{'zone':'Z4','low_kmh':19.,'high_kmh':19.8}]}
    assert method_dosing.target_ranges(19.4,159,'Z4',speed_range=[19.,19.8],speed_zones=zones,hr_bounds=[0,100,120,156,162,190]) == {
        'target_speed_range_kmh':[19.,19.8], 'hr_reference_range_bpm':[156,162]}
    ranges = method_dosing.target_ranges(19.7,161,'Z4',speed_zones=zones,hr_bounds=[0,100,120,156,162,190])
    assert ranges['target_speed_range_kmh'] == pytest.approx([19.306,19.8])
    assert ranges['hr_reference_range_bpm'] == [158,162]


def test_native_and_source_controls_round_trip_and_reject_invalid_numbers():
    values = {'enabled':True,'overrides':{'END-Z2-PROG-01':{'total_capacity_ratio':2.5, 'speed_tolerance_fraction':.015, 'hr_tolerance_bpm':4.}}}
    assert validate_preferences(values) == values
    for key,value in [('total_capacity_ratio',0),('total_capacity_ratio',float('inf')),('speed_tolerance_fraction',-.02),('hr_tolerance_bpm',0)]:
        bad=deepcopy(values);bad['overrides']['END-Z2-PROG-01'][key]=value
        with pytest.raises(ValidationError):
            MethodLibraryPreferences.model_validate(bad)
