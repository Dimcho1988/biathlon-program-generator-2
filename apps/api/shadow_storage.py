"""Lossless transport encoding for large immutable shadow time series.

Keep summaries and model/configuration hashes as JSON for SQL projections. Only
the repetitive per-sample array is compressed; every read path restores the
original logical payload before returning it to the application.
"""
from __future__ import annotations

import base64
import binascii
import gzip
import hashlib
import io
import json
import zlib
from typing import Any, Mapping

CODEC = "onflows-shadow-timeseries-gzip-json-v1"
MIN_COMPRESS_BYTES = 64 * 1024
MAX_DECODE_BYTES = 256 * 1024 * 1024


def _encode_main_series(payload: Mapping[str, Any]) -> dict[str, Any]:
    result = dict(payload)
    rows = result.get("timeseries")
    if not isinstance(rows, list) or not rows:
        return result
    raw = json.dumps(rows, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode("utf-8")
    if len(raw) < MIN_COMPRESS_BYTES:
        return result
    if len(raw) > MAX_DECODE_BYTES:
        raise ValueError("Shadow time series exceeds the storage decoding limit")
    packed = base64.b64encode(gzip.compress(raw, compresslevel=6, mtime=0)).decode("ascii")
    if len(packed) + 256 >= len(raw):
        return result
    result["timeseries"] = {
        "codec": CODEC, "json_bytes": len(raw), "row_count": len(rows),
        "sha256": hashlib.sha256(raw).hexdigest(), "data": packed,
    }
    return result


def _decode_main_series(payload: Mapping[str, Any]) -> dict[str, Any]:
    result = dict(payload)
    packed = result.get("timeseries")
    if packed is None or isinstance(packed, list):
        return result  # Legacy results and results without a time series.
    if not isinstance(packed, Mapping) or packed.get("codec") != CODEC:
        raise ValueError("Unsupported shadow time series encoding")
    size, count = packed.get("json_bytes"), packed.get("row_count")
    data, digest = packed.get("data"), packed.get("sha256")
    if (type(size) is not int or not 0 < size <= MAX_DECODE_BYTES
            or type(count) is not int or count < 0
            or not isinstance(data, str) or len(data) > MAX_DECODE_BYTES * 2
            or not isinstance(digest, str) or len(digest) != 64):
        raise ValueError("Invalid shadow time series encoding")
    try:
        compressed = base64.b64decode(data, validate=True)
        with gzip.GzipFile(fileobj=io.BytesIO(compressed), mode="rb") as stream:
            raw = stream.read(size + 1)
        if len(raw) != size or hashlib.sha256(raw).hexdigest() != digest:
            raise ValueError("Shadow time series integrity check failed")
        rows = json.loads(raw)
    except (binascii.Error, OSError, EOFError, UnicodeError, json.JSONDecodeError, zlib.error) as exc:
        raise ValueError("Invalid compressed shadow time series") from exc
    if not isinstance(rows, list) or len(rows) != count or not all(isinstance(row, dict) for row in rows):
        raise ValueError("Invalid decoded shadow time series")
    result["timeseries"] = rows
    return result


def encode_shadow_payload(payload: Mapping[str, Any]) -> dict[str, Any]:
    result = _encode_main_series(payload)
    if "speed_test_series" in payload:
        result["speed_test_series"] = _encode_main_series({"timeseries": payload["speed_test_series"]})["timeseries"]
    return result


def decode_shadow_payload(payload: Mapping[str, Any]) -> dict[str, Any]:
    result = _decode_main_series(payload)
    if "speed_test_series" in payload:
        result["speed_test_series"] = _decode_main_series({"timeseries": payload["speed_test_series"]})["timeseries"]
    return result
