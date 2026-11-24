"""Faster recipe copying retains recursive deepcopy isolation and aliases."""
from copy import deepcopy
from datetime import date

from biathlon.recipe_copy import copy_recipe


def test_nested_recipe_mutation_is_independent_and_shared_children_remain_shared():
    segment = {"zone": "Z4", "seconds": [180., 180.], "optional": None}
    source = {"parts": [segment, segment], "card": {"original": segment},
              "enabled": True, "count": 2, "title": "Тренировка", "raw": b"card"}
    original = deepcopy(source)
    copied = copy_recipe(source)
    assert copied == deepcopy(source)
    assert copied["parts"][0] is copied["parts"][1] is copied["card"]["original"]
    assert copied["parts"][0] is not segment
    copied["card"]["original"]["seconds"][0] = 0.
    copied["parts"].append({"zone": "Z1"})
    assert source == original


def test_dict_and_list_cycles_keep_their_copied_identity():
    source = {"items": []}
    source["self"] = source
    source["items"].extend([source, source["items"]])
    copied = copy_recipe(source)
    assert copied is not source
    assert copied["self"] is copied
    assert copied["items"][0] is copied
    assert copied["items"][1] is copied["items"]


def test_fallback_preserves_custom_types_mutable_tuple_children_and_cross_links():
    class Card(dict):
        pass

    class Note:
        def __init__(self, rows):
            self.rows = rows

    rows = [{"text": "source"}]
    card = Card(rows=rows)
    card.note = "custom attribute"
    source = {"rows": rows, "tuple": (rows,), "card": card,
              "object": Note(rows), "day": date(2026, 11, 24)}
    copied = copy_recipe(source)
    assert type(copied["card"]) is Card
    assert copied["card"].note == card.note
    assert type(copied["object"]) is Note
    assert copied["day"] == source["day"]
    assert copied["rows"] is copied["tuple"][0] is copied["card"]["rows"] is copied["object"].rows
    copied["object"].rows[0]["text"] = "changed"
    assert source["rows"][0]["text"] == "source"


def test_external_memo_and_mutable_hashable_keys_follow_deepcopy_contract():
    class Key:
        def __init__(self):
            self.labels = ["source"]

    key = Key()
    source = {key: key}
    memo = {}
    copied = copy_recipe(source, memo)
    copied_key = next(iter(copied))
    assert copied_key is copied[copied_key]
    assert copied_key is not key
    assert copy_recipe(source, memo) is copied
    assert any(retained is source for retained in memo[id(memo)])
    copied_key.labels.append("changed")
    assert key.labels == ["source"]
