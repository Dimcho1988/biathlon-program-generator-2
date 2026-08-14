"""Backward-compatible exports for the canonical effective-HR adapter."""

from __future__ import annotations

from biathlon.effective_hr import (
    EFFECTIVE_HR_ADAPTER_VERSION,
    EFFECTIVE_HR_SOURCE,
    effective_hr,
)


__all__ = [
    "EFFECTIVE_HR_ADAPTER_VERSION",
    "EFFECTIVE_HR_SOURCE",
    "effective_hr",
]
