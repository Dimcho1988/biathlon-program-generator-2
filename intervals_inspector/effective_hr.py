"""Canonical heart-rate input for the zonal physiology pipeline.

The adapter is deliberately small: the current model uses the validated raw
heart rate unchanged.  A future HR-modulation model can replace the body of
``effective_hr`` without changing zone classification, equivalence, or any
downstream aggregate.
"""

from __future__ import annotations

import math
from numbers import Real
from typing import Any


EFFECTIVE_HR_ADAPTER_VERSION = "effective-hr-raw-pass-through-v1"
EFFECTIVE_HR_SOURCE = "raw_hr"


def effective_hr(raw_hr: Any) -> float | None:
    """Return the canonical model HR, currently identical to valid raw HR.

    Stream-level quality control remains responsible for the physiological
    validity limits.  This adapter only rejects values that cannot represent a
    finite numeric HR, while preserving the raw value for diagnostics.
    """

    if raw_hr is None or isinstance(raw_hr, bool) or not isinstance(raw_hr, Real):
        return None
    rendered = float(raw_hr)
    return rendered if math.isfinite(rendered) else None


__all__ = [
    "EFFECTIVE_HR_ADAPTER_VERSION",
    "EFFECTIVE_HR_SOURCE",
    "effective_hr",
]
