from copy import deepcopy
from urllib.parse import parse_qs, urlsplit

import pytest

from apps.api.activity_catalog import extract_activity_metadata
from apps.api.activity_sport_rules import apply_sport_rules, validate_rules
from apps.api.oauth_store import SupabasePilotRepository

RULE = {"id":"approved-rule","source_sport":"Walk","name_contains":"биатлон","target_sport":"NordicSki"}


@pytest.mark.parametrize("source,name,expected", [
    ("Walk","Якоруда Биатлон","NordicSki"),
    ("Walk","БИАТЛОН със стрелба","NordicSki"),
    ("Walk","Якоруда ходене","Walk"),
    ("Run","Якоруда Биатлон","Run"),
    ("Ride","Биатлон","Ride"),
])
def test_only_approved_source_and_title_match(source,name,expected):
    detail = {"type":source,"name":name}
    original = deepcopy(detail)
    result = apply_sport_rules(detail,validate_rules([RULE]))
    assert result["type"] == expected
    assert detail == original
    assert apply_sport_rules(detail,())["type"] == source
    if expected != source:
        catalog = extract_activity_metadata("act_"+"a"*32,result)
        assert catalog["sport"] == "NordicSki"
        assert catalog["sport_classification"]["provider_sport"] == "Walk"
        assert catalog["sport_classification"]["rule_id"] == "approved-rule"


def test_rule_read_is_scoped_and_never_fetches_other_athletes():
    repository = object.__new__(SupabasePilotRepository)
    paths = []
    def request(method,path):
        assert method == "GET"
        paths.append(path)
        query = parse_qs(urlsplit(path).query)
        assert query["enabled"] == ["eq.true"]
        return [RULE] if query["athlete_alias"] == ["eq.athlete-a"] else []
    repository._request = request
    repository._json = lambda result:result
    assert repository.activity_sport_rules("athlete-a") == (RULE,)
    assert repository.activity_sport_rules("athlete-b") == ()
    assert len(paths) == 2


def test_conflicting_rules_fail_instead_of_silently_changing_sport():
    with pytest.raises(ValueError, match="Conflicting"):
        apply_sport_rules({"type":"Walk","name":"Биатлон"},[RULE,{**RULE,"target_sport":"Run"}])
    with pytest.raises(ValueError):
        validate_rules([{**RULE,"name_contains":""}])
