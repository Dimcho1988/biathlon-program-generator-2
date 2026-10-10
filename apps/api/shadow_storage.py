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
import lzma
import zlib
from typing import Any, Mapping

LEGACY_CODEC = "onflows-shadow-timeseries-gzip-json-v1"
CODEC = "onflows-shadow-series-columnar-lzma-v2"
MIN_COMPRESS_BYTES = 64 * 1024
MAX_DECODE_BYTES = 256 * 1024 * 1024
MAX_LZMA_MEMORY = 32 * 1024 * 1024
SERIES_FIELDS = ("timeseries", "speed_test_series", "segments_15s", "hrmod_waves")


def _column_document(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Group like values without rounding, resampling or losing absent keys."""
    keys = list(rows[0])
    if (len(keys) <= 512 and len(rows) * max(1, len(keys)) <= 10_000_000
            and all(set(row) == set(keys) for row in rows)):
        return {"layout": "columns", "keys": keys,
                "columns": [[row[key] for row in rows] for key in keys]}
    # Irregular diagnostic rows must preserve missing versus explicit null.
    return {"layout": "rows", "rows": rows}


def _restore_rows(document: Any, count: int) -> list[dict[str, Any]]:
    if not isinstance(document, dict):
        raise ValueError("Invalid shadow column document")
    if document.get("layout") == "rows":
        rows = document.get("rows")
    elif document.get("layout") == "columns":
        keys, columns = document.get("keys"), document.get("columns")
        if (not isinstance(keys, list) or len(keys) > 512
                or not all(isinstance(key, str) for key in keys)
                or len(set(keys)) != len(keys)
                or not isinstance(columns, list) or len(columns) != len(keys)
                or not all(isinstance(column, list) and len(column) == count for column in columns)
                or count * max(1, len(keys)) > 10_000_000):
            raise ValueError("Invalid shadow column dimensions")
        rows = [dict(zip(keys, values, strict=True)) for values in zip(*columns, strict=True)] if keys else [{} for _ in range(count)]
    else:
        raise ValueError("Unsupported shadow column layout")
    if not isinstance(rows, list) or len(rows) != count or not all(isinstance(row, dict) for row in rows):
        raise ValueError("Invalid decoded shadow time series")
    return rows


def _encode_main_series(payload: Mapping[str, Any]) -> dict[str, Any]:
    result = dict(payload)
    rows = result.get("timeseries")
    if not isinstance(rows, list) or not rows:
        return result
    if not all(isinstance(row, dict) for row in rows):
        raise ValueError("Shadow time series must contain objects")
    logical = json.dumps(rows, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode("utf-8")
    if len(logical) < MIN_COMPRESS_BYTES:
        return result
    if len(logical) > MAX_DECODE_BYTES or len(rows) > 1_000_000:
        raise ValueError("Shadow time series exceeds the storage decoding limit")
    raw = json.dumps(_column_document(rows), ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode("utf-8")
    if len(raw) > MAX_DECODE_BYTES:
        raise ValueError("Shadow time series exceeds the storage decoding limit")
    packed = base64.b64encode(lzma.compress(raw, preset=3)).decode("ascii")
    if len(packed) + 256 >= len(logical):
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
    if not isinstance(packed, Mapping) or packed.get("codec") not in (CODEC, LEGACY_CODEC):
        raise ValueError("Unsupported shadow time series encoding")
    size, count = packed.get("json_bytes"), packed.get("row_count")
    data, digest = packed.get("data"), packed.get("sha256")
    if (type(size) is not int or not 0 < size <= MAX_DECODE_BYTES
            or type(count) is not int or not 0 <= count <= 1_000_000
            or not isinstance(data, str) or len(data) > MAX_DECODE_BYTES * 2
            or not isinstance(digest, str) or len(digest) != 64):
        raise ValueError("Invalid shadow time series encoding")
    try:
        compressed = base64.b64decode(data, validate=True)
        if packed["codec"] == LEGACY_CODEC:
            with gzip.GzipFile(fileobj=io.BytesIO(compressed), mode="rb") as stream:
                raw = stream.read(size + 1)
        else:
            decoder = lzma.LZMADecompressor(format=lzma.FORMAT_XZ, memlimit=MAX_LZMA_MEMORY)
            raw = decoder.decompress(compressed, max_length=size + 1)
            if not decoder.eof or decoder.unused_data:
                raise ValueError("Invalid compressed shadow time series")
        if len(raw) != size or hashlib.sha256(raw).hexdigest() != digest:
            raise ValueError("Shadow time series integrity check failed")
        document = json.loads(raw)
        rows = document if packed["codec"] == LEGACY_CODEC else _restore_rows(document, count)
    except (binascii.Error, OSError, EOFError, UnicodeError, json.JSONDecodeError, zlib.error, lzma.LZMAError) as exc:
        raise ValueError("Invalid compressed shadow time series") from exc
    if not isinstance(rows, list) or len(rows) != count or not all(isinstance(row, dict) for row in rows):
        raise ValueError("Invalid decoded shadow time series")
    result["timeseries"] = rows
    return result


def encode_shadow_payload(payload: Mapping[str, Any]) -> dict[str, Any]:
    result = dict(payload)
    for field in SERIES_FIELDS:
        if field in payload:
            result[field] = _encode_main_series({"timeseries": payload[field]})["timeseries"]
    return result


def decode_shadow_payload(payload: Mapping[str, Any]) -> dict[str, Any]:
    result = dict(payload)
    for field in SERIES_FIELDS:
        if field in payload:
            result[field] = _decode_main_series({"timeseries": payload[field]})["timeseries"]
    return result
