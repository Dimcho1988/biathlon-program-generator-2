"""20 October contract: source dose, single Recovery and explicit adaptation."""
from copy import deepcopy
from types import SimpleNamespace
import pytest
from biathlon import method_library as lib, adaptive_methods, combined_methods
from apps.api import training_plan_engine as engine
from tests.api.test_sourced_method_library import recipe, athlete, view, adapter


def source_record(**changes):
    record = recipe()
    record['prescription'].update(anchor={'type':'V5'}, speed_multiplier=[.95,.95],
        work={'unit':'seconds','range':[60,60]}, repetitions=[10,10], source_repetitions_range=[10,10],
        allow_fewer_repetitions=True, minimum_repetitions=1,
        standard_variant={'repetitions':10,'sets':1,'work':{'unit':'seconds','range':[60,60]},'speed_multiplier':[.95,.95]})
    record['prescription'].update(changes)
    return record


def prepare(monkeypatch, record=None, recovery=100):
    m,e,s = adapter(monkeypatch, record or source_record())
    e['readiness_policy'] = adaptive_methods.readiness_policy(m, athlete(), {f'Z{i}':recovery for i in range(1,6)})
    return m,e,s


def work_minutes(blocks):
    return sum(b['duration_min'] for b in blocks if b['kind']=='WORK')


def test_source_standard_not_tmax_coefficient_or_maximum_of_range(monkeypatch):
    record=source_record(repetitions=[10,14],source_repetitions_range=[10,14])
    m,e,s=prepare(monkeypatch,record)
    fraction=engine._nominal_fraction(m,'BUILDING',athlete(),'SPECIAL_PREPARATION',e,{})
    assert e['capacity_minutes']*fraction == pytest.approx(10)
    assert m['method_library']['resolution']['standard_work_seconds']==600
    assert m['max_work_min']==14
    assert e['method_budget_role_fraction']==1
    assert work_minutes(engine._blocks(m,10,e,s))==10


def test_maintenance_sixty_percent_selected_standard_once(monkeypatch):
    m,e,s=prepare(monkeypatch,recovery=80)
    fraction=engine._nominal_fraction(m,'MAINTENANCE',athlete(),'SPECIAL_PREPARATION',e,{})
    assert e['capacity_minutes']*fraction==pytest.approx(6)
    assert e['readiness_dose_factor']==1  # Preserved current role-specific policy.
    assert work_minutes(engine._blocks(m,6,e,s))==6
    assert e['method_dose_factors'][1:] == [{'stage':'ROLE','factor':.6},{'stage':'RECOVERY','factor':1.}]


def test_recovery_eighty_is_ten_percent_volume_reduction_no_speed_change(monkeypatch):
    m,e,s=prepare(monkeypatch,recovery=80)
    fraction=engine._nominal_fraction(m,'BUILDING',athlete(),'SPECIAL_PREPARATION',e,{})
    requested=e['capacity_minutes']*fraction*e['readiness_dose_factor']
    assert requested==pytest.approx(9)
    blocks=engine._blocks(m,requested,e,s)
    work=[b for b in blocks if b['kind']=='WORK']
    assert len(work)==9
    assert all(b['target_speed_range_kmh'][0]==b['target_speed_range_kmh'][1] for b in work)
    assert all(b['target_speed_kmh']==work[0]['target_speed_kmh'] for b in work)
    assert len([b for b in blocks if b['kind']=='RECOVERY'])==8


def test_combination_uses_each_standard_then_recovery_once(monkeypatch):
    m,e,s=prepare(monkeypatch,recovery=80)
    second=deepcopy(m); second['id']='SECOND'; second['zone']='Z3'
    caps=[deepcopy(e),deepcopy(e)]
    combo={**deepcopy(m),'id':'COMBO','method_library':None,'structure':'COMBINED_METHODS',
           'combined_methods':[m,second],'combined_accents':[m['zone'],'Z3'],
           'combination_shortage':{'shortage':True}}
    evidence={**deepcopy(e),'method_library':None,'combined_capacities':caps}
    fraction=engine._nominal_fraction(combo,'BUILDING',athlete(),'SPECIAL_PREPARATION',evidence,{})
    assert [b['nominal_work_minutes'] for b in evidence['combined_budgets']]==pytest.approx([10,10])
    requested=evidence['capacity_minutes']*fraction*evidence['readiness_dose_factor']
    assert requested==pytest.approx(9.)
    blocks=engine._blocks(combo,requested,evidence,s)
    assert [sum(b['duration_min'] for b in blocks if b['kind']=='WORK' and b['combination_part']==i) for i in (1,2)]==[9,9]
    assert all(b['readiness_factor']==.9 for b in evidence['combined_budgets'])
    minimum=engine._minimum_work(combo,evidence,s)
    assert minimum==pytest.approx(1)
    minimum_blocks=engine._blocks(combo,minimum,evidence,s)
    assert [sum(b['duration_min'] for b in minimum_blocks if b['kind']=='WORK' and b['combination_part']==i) for i in (1,2)]==[1,1]
    assert engine._minimum_dose_usage(minimum_blocks,evidence,combo['zone'])==pytest.approx(.1)
    ceiling=engine._dose_ceiling(combo,'BUILDING',athlete(),'SPECIAL_PREPARATION',evidence,{})*evidence['readiness_dose_factor']
    assert engine._dose_fits(blocks,evidence,combo['zone'],ceiling)
    # Ten minutes exceed the nine-minute adjusted budget. The builder clips
    # to that budget; the validator also rejects an independently altered dose.
    excess=deepcopy(blocks)
    excess.append(deepcopy(next(b for b in blocks if b['kind']=='WORK' and b['combination_part']==1)))
    assert not engine._dose_fits(excess,evidence,combo['zone'],ceiling)


def test_coupled_variants_do_not_fill_gaps_or_mix_set_and_repeat_maxima(monkeypatch):
    r=source_record(repetitions=[4,6],source_repetitions_range=[4,6],allow_fewer_repetitions=False,
        standard_variant={'repetitions':4,'sets':3,'work':{'unit':'seconds','range':[60,60]},'speed_multiplier':[.95,.95]},
        allowed_variants=[{'repetitions':4,'sets':3,'work':{'unit':'seconds','range':[60,60]},'speed_multiplier':[.95,.95]},
                          {'repetitions':6,'sets':2,'work':{'unit':'seconds','range':[60,60]},'speed_multiplier':[.95,.95]}])
    m,e,s=prepare(monkeypatch,r)
    resolved=m['method_library']['resolution']
    assert resolved['standard_work_seconds']==720
    assert resolved['maximum_work_seconds']==720  # Never 6×3.
    assert engine._blocks(m,11,e,s)==[]
    assert work_minutes(engine._blocks(m,18,e,s))==12


def test_mas_v6_and_v5_remain_distinct_and_marked_estimates():
    mas=source_record(anchor={'type':'MAS'}); mas['prescription'].pop('standard_variant')
    v5=source_record(); v5['prescription'].pop('standard_variant')
    a,b=(lib.resolve(r,athlete(),view(),phase='SPECIAL_PREPARATION') for r in (mas,v5))
    assert a['status']==b['status']=='READY'
    assert a['anchor']['duration_seconds']==360
    assert b['anchor']['duration_seconds']==300
    assert a['anchor']['is_laboratory_measurement'] is False
    assert a['anchor']['speed_kmh'] != b['anchor']['speed_kmh']


def test_ant_explicit_sport_reference_preserved_otherwise_cs_estimate():
    r=source_record(anchor={'type':'AnT'}); r['prescription'].pop('standard_variant')
    manual=athlete(ant_references=[{'sport':'Run','speed_kmh':16,'source':'MANUAL'}])
    a=lib.resolve(r,manual,view(),phase='SPECIAL_PREPARATION')
    assert a['anchor']['speed_kmh']==16 and a['anchor']['source']=='MANUAL'
    b=lib.resolve(r,athlete(),view(),phase='SPECIAL_PREPARATION')
    assert b['anchor']['speed_kmh']==pytest.approx(.95*b['anchor']['evidence']['speed_kmh'])
    assert b['anchor']['is_measured_lactate_threshold'] is False
    assert manual['ant_references'][0]['speed_kmh']==16


def test_unknown_level_does_not_receive_elite_protocol_or_arbitrary_discount():
    r=source_record(); r['athlete_levels']=['HIGHLY_TRAINED']
    assert lib.resolve(r,athlete(load_progression={'training_level':'AUTO'}),view())['reason']=='ATHLETE_LEVEL_REQUIRED'
    result=lib.resolve(r,athlete(load_progression={'training_level':'HIGH'}),view(),phase='SPECIAL_PREPARATION')
    assert result['status']=='READY' and result['standard_work_seconds']==600


def test_no_source_count_does_not_manufacture_tmax_repeat_count():
    r=source_record(repetitions=None,source_repetitions_range=None,repetitions_from_budget=True)
    r['prescription'].pop('standard_variant')
    assert lib.resolve(r,athlete(),view(),phase='SPECIAL_PREPARATION')['reason']=='STANDARD_DOSE_REQUIRED_NO_SOURCE_COUNT'


def test_continuous_source_volume_is_not_multiplied_by_sixty_five_percent(monkeypatch):
    r=source_record(structure='CONTINUOUS',anchor={'type':'CV'},speed_multiplier=[.75,.75],
        work={'unit':'seconds','range':[1200,1800]},repetitions=[1,1],source_repetitions_range=[1,1],minimum_scale=.3)
    r['prescription'].pop('standard_variant')
    m,e,s=prepare(monkeypatch,r)
    fraction=engine._nominal_fraction(m,'BUILDING',athlete(),'SPECIAL_PREPARATION',e,{})
    assert e['capacity_minutes']*fraction==pytest.approx(20)
    assert work_minutes(engine._blocks(m,20,e,s))==20


def test_undosed_continuous_z1_method_uses_one_sixty_five_percent_tmax_fallback():
    r=source_record(structure='CONTINUOUS',anchor={'type':'CV'},speed_multiplier=[.7,.7],
        work=None,repetitions=[1,1],source_repetitions_range=[1,1])
    r['prescription'].pop('standard_variant')
    result=lib.resolve(r,athlete(),view(),phase='SPECIAL_PREPARATION')
    assert result['status']=='READY',result
    assert result['zone'] in {'Z1','Z2'}
    assert result['standard_work_seconds']==pytest.approx(.65*result['continuous_capacity_seconds'])
    assert result['dose_basis']=='INDIVIDUAL_TMAX_FALLBACK_65_PERCENT'


@pytest.mark.parametrize('speeds,times,position,expected_products', [
    ([10.,20.], [10.,20.], .5, [200.,225.]),
    ([10.,20.], [10.,30.], .4, [200.,312.5]),
    ([10.,20.], [10.,11.], .5, [110.,200.]),
    ([20.,20.], [10.,20.], .5, [200.,400.]),
    ([10.,20.], [10.,10.], .5, [100.,200.]),
])
def test_time_dosed_distance_range_covers_paired_endpoints_and_interior_peak(
        monkeypatch, speeds, times, position, expected_products):
    monkeypatch.setattr(lib, '_anchor', lambda *args: {'speed_kmh':20.})
    monkeypatch.setattr(lib.speed_zones, 'classify', lambda *args: 'Z4')
    segment = lib._segment({}, {'speed_multiplier':[v/20 for v in speeds],
        'work':{'unit':'seconds','range':times}}, {}, {},
        SimpleNamespace(inverse=lambda *_: 3600.), [], position, {})
    low, high = segment['work_distance_range_m']
    assert [low,high] == pytest.approx([v/3.6 for v in expected_products])
    assert low <= segment['work_distance_m'] <= high
    assert segment['target_speed_range_kmh'] == speeds
    assert segment['work_duration_range_seconds'] == times
    if speeds[0] != speeds[1] and times[0] != times[1]:
        assert high < speeds[1] * times[1] / 3.6  # Never the Cartesian maximum.
