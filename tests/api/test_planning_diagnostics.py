from collections import Counter
from copy import deepcopy
import json

from apps.api.planning_diagnostics import compact_rejections


def expanded_records(rejections):
    return Counter(json.dumps({**{key: value for key, value in rejection.items()
                                  if key not in {"method_id", "method_ids"}},
                               "method_id": method_id}, sort_keys=True)
                   for rejection in rejections
                   for method_id in rejection.get("method_ids", [rejection["method_id"]]))


def test_grouped_rejections_preserve_each_method_occurrence_and_distinct_evidence():
    diagnostic = {"code": "COMPONENT_BUDGET_EXHAUSTED", "reason": "Оставащият товар не допуска цялата структура.",
                  "blocking_components": ["Z1"], "manual_target_components": ["Z1"],
                  "source_ids": ["observed-load-17", "coach-target-2"],
                  "budget": {"required": 18.25, "remaining": 16.5}}
    rejections = [{"method_id": method, **deepcopy(diagnostic)}
                  for _ in range(3) for method in ["CONTINUOUS", "SPLIT", "VARIABLE"]]
    rejections += [{"method_id": "SPLIT", **deepcopy(diagnostic), "budget": {"required": 18.25, "remaining": 12.}},
                   {"method_id": "VARIABLE", **deepcopy(diagnostic), "source_ids": ["observed-load-18"]}]
    original = deepcopy(rejections)
    grouped = compact_rejections(rejections)

    assert len(grouped) == 3
    assert expanded_records(grouped) == expanded_records(original)
    assert grouped[0]["method_ids"].count("SPLIT") == 3
    assert json.loads(json.dumps(grouped)) == grouped
    assert compact_rejections(grouped) == grouped
    assert rejections == original
    assert len(json.dumps(grouped)) < len(json.dumps(original)) / 2
