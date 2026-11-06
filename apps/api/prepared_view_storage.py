"""Lossless transfer of large, already prepared management pages."""
import base64
from copy import deepcopy
import gzip
import io
import json
import zlib


CODEC = "gzip-json-v1"
TAG = "_onflows_prepared_page"
MAX_BYTES = 8 * 1024 * 1024  # The existing prepared-page publication limit.
MIN_BYTES = 128 * 1024


def pack(kind, payload):
    if kind not in {"management-view", "management-active"}:
        return payload
    raw = json.dumps(payload, ensure_ascii=False, allow_nan=False).encode("utf-8")
    if not MIN_BYTES <= len(raw) <= MAX_BYTES:
        return payload
    data = base64.b64encode(gzip.compress(raw, compresslevel=3, mtime=0)).decode("ascii")
    if len(data) + 128 >= len(raw):
        return payload
    return {TAG: CODEC, "raw_bytes": len(raw), "data": data}


def unpack(payload):
    if TAG not in payload:
        return deepcopy(payload)
    size, data = payload.get("raw_bytes"), payload.get("data")
    if (payload[TAG] != CODEC or type(size) is not int or not 0 < size <= MAX_BYTES
            or not isinstance(data, str) or len(data) > MAX_BYTES):
        raise ValueError("Invalid prepared page encoding")
    try:
        packed = base64.b64decode(data, validate=True)
        with gzip.GzipFile(fileobj=io.BytesIO(packed)) as stream:
            raw = stream.read(size + 1)
    except (OSError, EOFError, zlib.error, ValueError) as exc:
        raise ValueError("Invalid compressed prepared page") from exc
    if len(raw) != size:
        raise ValueError("Invalid prepared page size")
    value = json.loads(raw)
    if not isinstance(value, dict):
        raise ValueError("Invalid prepared page object")
    return value
