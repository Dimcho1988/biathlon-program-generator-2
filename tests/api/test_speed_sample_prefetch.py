from contextvars import ContextVar
from threading import Barrier

import pytest

from apps.api.speed_load import activity_samples


def test_prefetch_overlaps_bounded_reads_and_preserves_context_and_order():
    scope = ContextVar("sample_test_scope", default=None)
    rendezvous = Barrier(2)
    batches = []
    class Repository:
        def activity_speed_exposure_samples(self, alias, keys):
            assert alias == scope.get() == "ath-test"
            batches.append(keys)
            if int(keys[0]) < 20:
                rendezvous.wait(timeout=5)
            return {k: {"key": k} for k in keys}
    activities = [{"latest_shadow_run_key": f"{i:02}"} for i in range(25)]
    token = scope.set("ath-test")
    try:
        results = list(activity_samples(Repository(), "ath-test", activities))
    finally:
        scope.reset(token)
    assert [r[1]["key"] for r in results] == [a["latest_shadow_run_key"] for a in activities]
    assert sorted(map(len, batches)) == [5, 10, 10]


def test_sample_read_failure_remains_an_error_instead_of_missing_data():
    class Repository:
        def activity_speed_exposure_samples(self, alias, keys):
            raise RuntimeError("Storage read failed")
    with pytest.raises(RuntimeError, match="Storage read failed"):
        list(activity_samples(Repository(), "ath-test", [{"latest_shadow_run_key": "one"}]))
