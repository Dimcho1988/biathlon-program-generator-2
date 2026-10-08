"""Lossless grouping of repeated method rejections in persisted plan evidence."""
from copy import deepcopy
import json


def compact_rejections(rejections):
    """Store an identical diagnostic once, retaining every evaluated method ID.

    ``method_id`` remains the first method for older readers. ``method_ids``
    contains all occurrences, including repeated evaluations in multiple slots.
    Different budgets, sources, or any other evidence never share a group.
    """
    groups = {}
    for rejection in rejections:
        evidence = {key: value for key, value in rejection.items()
                    if key not in {"method_id", "method_ids"}}
        key = json.dumps(evidence, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
        identifiers = rejection.get("method_ids") or [rejection["method_id"]]
        if key not in groups:
            groups[key] = (deepcopy(evidence), [])
        groups[key][1].extend(identifiers)
    return [{"method_id": identifiers[0], **evidence,
             **({"method_ids": list(identifiers)} if len(identifiers) > 1 else {})}
            for evidence, identifiers in groups.values()]
