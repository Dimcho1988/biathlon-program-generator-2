"""Small immutable per-recording results shared by worker and API restarts."""
from .oauth_store import PersistentStoreFailure


def reuse(repository, alias, kind, keys, calculate):
    reader = getattr(repository, "read_activity_derivations", None)
    writer = getattr(repository, "write_activity_derivations", None)
    if not callable(reader):
        return calculate(keys)
    try:
        stored = reader(alias, kind, tuple(keys))
        if not isinstance(stored, dict) or not set(stored) <= set(keys) or not all(isinstance(value, dict) for value in stored.values()):
            raise PersistentStoreFailure("Invalid activity derivation results")
    except PersistentStoreFailure:
        stored = {}
    missing = [key for key in keys if key not in stored]
    if not missing:
        return stored
    fresh = calculate(missing)
    if set(fresh) != set(missing):
        raise ValueError("Missing activity derivation result")
    cacheable = {key:value for key,value in fresh.items()
        if isinstance(value, dict) and value.get("reason") != "SPEED_RECOMPUTATION_REQUIRED"}
    if cacheable and callable(writer):
        try:
            writer(alias, kind, cacheable)
        except PersistentStoreFailure:
            pass  # The worker can still publish its fully calculated view.
    return {**stored, **fresh}
