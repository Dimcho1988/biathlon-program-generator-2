from biathlon.combined_methods import opportunities


def test_free_slots_do_not_disable_combinations_of_unfinished_components():
    goals = {"Z2": 60., "Z3": 10., "Z4": 5., "Z5": 5.}
    nominal = {"Z2": 120., "Z3": 30., "Z4": 20., "Z5": 20.}
    for slots in (1, 2, 7):
        result = opportunities(goals, nominal, slots)
        assert result["consider_combinations"]
        assert result["sessions_by_component"] == dict.fromkeys(goals, 1)
        assert result["available_sessions"] == slots


def test_missing_capacity_or_completed_components_do_not_create_combination_parts():
    result = opportunities({"Z2": 30., "Z3": 0., "Z5": 10.}, {"Z2": 60., "Z3": 10.}, 2)
    assert result["sessions_by_component"] == {"Z2": 1}
    assert not result["consider_combinations"]


def test_strength_is_not_an_automatic_endurance_combination():
    result = opportunities({"Z2": 30., "STR": 20.}, {"Z2": 60., "STR": 30.}, 1)
    assert result["sessions_by_component"] == {"Z2": 1, "STR": 1}
    assert not result["consider_combinations"]
