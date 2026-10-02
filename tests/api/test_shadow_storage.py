from copy import deepcopy
import hashlib
import json
import base64
import gzip
import lzma

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
            "hrmod_model_version": "hrmod_mirror_area_shift_v9",
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


def test_independent_speed_series_round_trips_and_checks_integrity():
    original = large_payload()
    original["speed_test_series"] = deepcopy(original["timeseries"])
    packed = encode_shadow_payload(original)
    assert packed["speed_test_series"]["codec"] == CODEC
    assert decode_shadow_payload(packed) == original
    packed["speed_test_series"]["sha256"] = "0"*64
    with pytest.raises(ValueError): decode_shadow_payload(packed)


def envelope(document, *, count, codec=CODEC):
    raw = json.dumps(document, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode()
    packed = gzip.compress(raw, mtime=0) if codec == shadow_storage.LEGACY_CODEC else lzma.compress(raw, preset=3)
    return {"codec": codec, "json_bytes": len(raw), "row_count": count,
            "sha256": hashlib.sha256(raw).hexdigest(), "data": base64.b64encode(packed).decode()}


def test_reads_existing_gzip_rows_alongside_new_columnar_rows():
    original = large_payload()
    mixed = encode_shadow_payload(original)
    mixed["speed_test_series"] = envelope(original["timeseries"], count=2000,
        codec=shadow_storage.LEGACY_CODEC)
    expected = {**original, "speed_test_series": original["timeseries"]}
    assert canonical(decode_shadow_payload(mixed)) == canonical(expected)


def test_irregular_rows_preserve_missing_keys_null_types_unicode_and_signed_zero():
    rows = [{"x": 1, "v": -0.0, "label": "зона", "f": False},
            {"x": 1.0, "v": None, "optional": None, "f": True}] * 2000
    packed = encode_shadow_payload({"timeseries": rows})
    assert packed["timeseries"]["codec"] == CODEC
    restored = decode_shadow_payload(packed)["timeseries"]
    assert canonical(restored) == canonical(rows)
    assert "optional" not in restored[0]
    assert "label" not in restored[1]


def test_segments_and_wave_evidence_are_lossless_too():
    original = large_payload()
    original["segments_15s"] = deepcopy(original["timeseries"])
    original["hrmod_waves"] = deepcopy(original["timeseries"])
    packed = encode_shadow_payload(original)
    for field in ("timeseries", "segments_15s", "hrmod_waves"):
        assert packed[field]["codec"] == CODEC
    assert canonical(decode_shadow_payload(packed)) == canonical(original)


@pytest.mark.parametrize("document,count", [
    ({"layout":"columns","keys":["x","x"],"columns":[[1],[2]]},1),
    ({"layout":"columns","keys":["x"],"columns":[[1,2]]},1),
    ({"layout":"columns","keys":["x"],"columns":[]},1),
    ({"layout":"rows","rows":[1]},1),
    ({"layout":"unknown"},0),
])
def test_invalid_column_documents_fail_closed(document, count):
    with pytest.raises(ValueError):
        decode_shadow_payload({"timeseries": envelope(document, count=count)})


def test_trailing_lzma_data_is_rejected():
    packed = encode_shadow_payload(large_payload())
    series = packed["timeseries"]
    series["data"] = base64.b64encode(base64.b64decode(series["data"])+b"trailing").decode()
    with pytest.raises(ValueError):
        decode_shadow_payload(packed)


def test_lzma_dictionary_limit_is_enforced():
    document = {"layout":"rows","rows":[{"x":1}]}
    packed = envelope(document, count=1)
    raw = json.dumps(document, separators=(",", ":")).encode()
    packed["data"] = base64.b64encode(lzma.compress(raw, preset=9)).decode()
    with pytest.raises(ValueError):
        decode_shadow_payload({"timeseries": packed})
