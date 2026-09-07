from copy import deepcopy
import hashlib
import json

import pytest

from apps.api import shadow_storage
from apps.api.shadow_storage import CODEC, decode_shadow_payload, encode_shadow_payload


def large_payload():
    return {
        "schema_version": "activity-shadow-derived-v3", "result_hash": "a" * 64,
        "configuration_fingerprint": "b" * 64,
        "zone_summary": [{"zone_name": "Z1", "hrmod_final_seconds": 120}],
        "trainability_index": {"general": {"index": 7.123456789012345}},
        "timeseries": [{
            "elapsed_s": i, "hrmod_final_bpm": 140.123456789 + i % 15,
            "vflat_b65_kmh": 12.3456789 + i % 8, "quality_flags": [],
            "optional": None, "receiver_flag": bool(i % 2), "signed_zero": -0.0,
            "hrmod_model_version": "hrmod_mirror_area_shift_v7",
        } for i in range(2000)],
    }


def canonical(payload):
    return json.dumps(payload, sort_keys=True, ensure_ascii=False, allow_nan=False)


def test_encoding_is_lossless_deterministic_and_leaves_sql_projections_visible():
    original = large_payload()
    before = deepcopy(original)
    packed = encode_shadow_payload(original)
    assert packed["timeseries"]["codec"] == CODEC
    assert len(canonical(packed)) < len(canonical(original)) / 5
    assert encode_shadow_payload(original) == packed
    decoded = decode_shadow_payload(packed)
    assert canonical(decoded) == canonical(original)
    assert hashlib.sha256(canonical(decoded).encode()).digest() == hashlib.sha256(canonical(original).encode()).digest()
    for key in ("zone_summary", "trainability_index", "configuration_fingerprint", "result_hash"):
        assert packed[key] == original[key]
    assert original == before
    assert isinstance(packed["timeseries"], dict)


@pytest.mark.parametrize("payload", [{}, {"timeseries": None}, {"timeseries": []}, {"timeseries": [{"hrmod_final_bpm": 141}]}])
def test_existing_and_small_results_remain_readable(payload):
    assert decode_shadow_payload(encode_shadow_payload(payload)) == payload


@pytest.mark.parametrize("field,value", [
    ("codec", "unknown"), ("json_bytes", True), ("json_bytes", 2),
    ("json_bytes", shadow_storage.MAX_DECODE_BYTES + 1),
    ("row_count", 1), ("sha256", "0" * 64), ("data", "broken-base64"),
    ("data", "bm90IGd6aXA="),
])
def test_damaged_or_unbounded_payloads_fail_closed(field, value):
    packed = encode_shadow_payload(large_payload())
    packed["timeseries"][field] = value
    with pytest.raises(ValueError):
        decode_shadow_payload(packed)
