"""Independent copies of JSON-shaped source recipes and their resolved cards.

Exact dictionaries/lists/scalars dominate these records. Their copy avoids
generic object dispatch while retaining deepcopy's aliases, cycles and fallback
for tuples, subclasses and other objects. This is not a shared mutable view.
"""
from copy import deepcopy


_ATOMIC_TYPES = frozenset({str, int, float, bool, type(None), bytes})
_MISSING = object()


def copy_recipe(value, memo=None):
    kind = type(value)
    if kind in _ATOMIC_TYPES:
        return value
    if memo is None:
        memo = {}
    identity = id(value)
    existing = memo.get(identity, _MISSING)
    if existing is not _MISSING:
        return existing
    if kind is dict:
        result = {}
        memo[identity] = result
        for key, child in value.items():
            result[key if type(key) in _ATOMIC_TYPES else copy_recipe(key, memo)] = copy_recipe(child, memo)
        # Match deepcopy when a caller retains and reuses an external memo:
        # source IDs cannot be recycled while their copied values remain cached.
        memo.setdefault(id(memo), []).append(value)
        return result
    if kind is list:
        result = []
        memo[identity] = result
        result.extend(copy_recipe(child, memo) for child in value)
        memo.setdefault(id(memo), []).append(value)
        return result
    return deepcopy(value, memo)
