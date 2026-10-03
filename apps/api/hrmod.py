"""Deterministic HR-only experimental/shadow model boundary."""
from __future__ import annotations
import hashlib
import math
from typing import Sequence

VERSION = "hrmod-hr-only-v2"

def calculate_hrmod(hr: Sequence[float | None]) -> dict:
    raw = [None if x is None else float(x) for x in hr]
    valid = [x is not None and math.isfinite(x) and x > 0 for x in raw]
    clean = [x if ok else None for x, ok in zip(raw, valid)]
    # Non-overlapping 5-sample waves; no future wave contributes to another.
    modeled = clean.copy(); waves = 0
    for start in range(0, len(clean), 5):
        block = [x for x in clean[start:start + 5] if x is not None]
        if len(block) >= 3:
            waves += 1; mean = sum(block) / len(block)
            for i in range(start, min(start + 5, len(modeled))):
                if modeled[i] is not None: modeled[i] = round((modeled[i] + mean) / 2, 6)
    digest = hashlib.sha256(repr(clean).encode()).hexdigest()
    return {"model_version": VERSION, "status": "experimental/shadow", "affects_final_decision": False,
            "raw_hr": clean, "hrmod": modeled, "coverage": sum(valid) / len(valid) if valid else 0.0,
            "diagnostics": {"non_overlapping_waves": waves, "input_fingerprint": digest}}
