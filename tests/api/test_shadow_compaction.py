from copy import deepcopy

import pytest

from apps.api.compact_shadow_storage import logical_digest, run
from apps.api.oauth_store import PersistentStoreFailure
from apps.api.shadow_storage import decode_shadow_payload


def original_payload():
    return {"result_hash":"d"*64,"zone_summary":[{"zone":"Z3","seconds":900}],
            "timeseries":[{"elapsed":i,"speed":12.123456789,"flag":False} for i in range(3000)]}


class Store:
    def __init__(self):
        self.payload = original_payload()
        self.writes = 0
        self.conflict = False
        self.corrupt_read = False

    def _json(self, value):
        return value

    def _request(self, method, path, **kwargs):
        if path.endswith("read_onflows_shadow_compaction_batch"):
            if kwargs["json"]["p_after_run_key"]:
                return []
            return [{"run_key":"a"*64,"result_hash":"d"*64,
                     "storage_hash":"f"*32,"result_payload":deepcopy(self.payload)}]
        assert method == "GET"
        payload = deepcopy(self.payload)
        if self.corrupt_read:
            payload["zone_summary"] = []
        return [{"result_hash":"d"*64,"result_payload":payload}]

    def _rpc_row(self, name, args):
        assert name == "compact_onflows_shadow_run"
        assert args["p_expected_storage_hash"] == "f"*32
        if self.conflict:
            return {"outcome":"CONFLICT"}
        self.writes += 1
        self.payload = deepcopy(args["p_result_payload"])
        return {"outcome":"COMPACTED"}


def test_dry_run_measures_without_writing_and_preserves_exact_logical_values():
    store = Store()
    result = run(store, max_rows=3, progress=lambda _:None)
    assert store.writes == 0 and result["changed"] == 1
    assert result["after_json_bytes"] < result["before_json_bytes"]
    assert result["complete"]


def test_apply_verifies_stored_data_and_second_pass_is_idempotent():
    store = Store()
    expected = logical_digest(store.payload)
    result = run(store, apply=True, max_rows=3, progress=lambda _:None)
    assert store.writes == result["verified"] == 1
    assert logical_digest(decode_shadow_payload(store.payload)) == expected
    repeated = run(store, apply=True, max_rows=3, progress=lambda _:None)
    assert repeated["changed"] == 0 and store.writes == 1


@pytest.mark.parametrize("field", ["conflict","corrupt_read"])
def test_conflict_or_failed_readback_stops_maintenance(field):
    store = Store()
    setattr(store, field, True)
    with pytest.raises(PersistentStoreFailure):
        run(store, apply=True, max_rows=3, progress=lambda _:None)


def test_logical_fingerprint_distinguishes_types_and_signed_zero():
    assert logical_digest({"x":1}) != logical_digest({"x":1.0})
    assert logical_digest({"x":0.0}) != logical_digest({"x":-0.0})
    assert logical_digest({"x":False}) != logical_digest({"x":0})
