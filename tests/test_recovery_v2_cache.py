from copy import deepcopy
from datetime import date, timedelta

import pytest

from biathlon import recovery_v2 as recovery


def history():
    start=date(2026,1,1)
    return [{"date":(start+timedelta(days=n)).isoformat(),"zone":z,"effective_load":float((n*i)%70)}
            for n in range(60) if n%9!=0 for i,z in enumerate(recovery.ZONES,1)]


def test_request_cache_preserves_complete_state_after_load_date_and_config_changes():
    cache={};rows=history();configs=recovery.defaults()
    for day in [date(2026,3,1),date(2026,3,3),date(2026,2,15)]:
        for details in [False,True]:
            assert recovery.simulate(rows,configs,target=day,include_details=details,calculation_cache=cache)==recovery.simulate(rows,configs,target=day,include_details=details)
    changed=deepcopy(rows);changed[-3]["effective_load"]+=40
    configs["Z3"].update(shape=2.,duration_coefficient=1.5,initial_daily_min=15.)
    assert recovery.simulate(changed,configs,target=date(2026,3,2),calculation_cache=cache)==recovery.simulate(changed,configs,target=date(2026,3,2))


def test_cached_calculations_do_not_skip_invalid_input_validation():
    rows=history();cache={};configs=recovery.defaults()
    recovery.simulate(rows,configs,calculation_cache=cache,include_details=False)
    configs["Z3"]["shape"]=True
    with pytest.raises(ValueError):
        recovery.simulate(rows,configs,calculation_cache=cache,include_details=False)
    with pytest.raises(ValueError,match="Duplicate"):
        recovery.simulate(rows+[rows[0]],calculation_cache=cache,include_details=False)


@pytest.mark.parametrize("deadlines", [False, True])
def test_shared_prefix_keeps_revised_history_unknown_days_and_parameters_exact(deadlines):
    rows, configs, cache = history(), recovery.defaults(), {}
    before = deepcopy((rows, configs))
    cases = [(rows, configs, date(2026, 3, 1)),
             (rows, configs, date(2026, 2, 12)),
             (rows + [{"date": "2026-03-02", "zone": "Z3", "effective_load": 30.}], configs, date(2026, 3, 3))]
    revised = deepcopy(rows)
    revised[7]["effective_load"] += 40.
    revised[-3]["effective_load"] += 60.
    # An explicit rest day and an uncovered day have different causal windows.
    cases.extend((r, configs, date(2026, 3, 3)) for r in (revised, revised[:-6]))
    adjusted = deepcopy(configs)
    adjusted["Z3"].update(shape=2., duration_coefficient=1.5, initial_daily_min=15.)
    cases.append((revised, adjusted, date(2026, 3, 4)))
    for doses, policy, day in cases:
        expected = recovery.simulate(doses, policy, target=day,
            include_details=False, include_deadlines=deadlines)
        result = recovery.simulate(doses, policy, target=day,
            include_details=False, include_deadlines=deadlines, calculation_cache=cache)
        assert result == expected
        result["current"][0]["readiness_percent"] = -1.
        assert recovery.simulate(doses, policy, target=day, include_details=False,
            include_deadlines=deadlines, calculation_cache=cache) == expected
    assert (rows, configs) == before
