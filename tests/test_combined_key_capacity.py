from biathlon.combined_methods import shortage


def test_three_separate_qualities_cannot_use_two_keys_despite_seven_free_sessions():
    goals={"Z2":60.,"Z3":10.,"Z4":5.,"Z5":5.}
    nominal={"Z2":120.,"Z3":30.,"Z4":20.,"Z5":20.}
    vectors=[{z:value} for z,value in nominal.items()]
    result=shortage(goals,nominal,7,vectors=vectors,key_costs=[0,1,1,1],available_key_slots=2)
    assert result["required_separate_sessions"] <= 7
    assert result["shortage"]
    assert result["required_key_sessions"] == 3
    assert result["reason"] == "KEY_SESSION_SHORTAGE_PROVEN_BY_LOWER_BOUND"


def test_multi_component_whole_recipe_can_cover_three_qualities_in_two_keys():
    goals={"Z3":10.,"Z4":5.,"Z5":5.}
    vectors=[{"Z3":15.,"Z4":6.},{"Z5":10.}]
    result=shortage(goals,{"Z3":15.,"Z4":6.,"Z5":10.},7,
        vectors=vectors,key_costs=[1,1],available_key_slots=2)
    assert result["required_key_sessions"] == 2
    assert not result["shortage"]


def test_non_key_methods_keep_their_actual_role_in_the_capacity_check():
    result=shortage({"Z2":30.,"Z3":5.},{"Z2":60.,"Z3":10.},2,
        vectors=[{"Z2":60.},{"Z3":10.}],key_costs=[0,0],available_key_slots=0)
    assert not result["shortage"]
    assert result["required_key_sessions"] == 0
