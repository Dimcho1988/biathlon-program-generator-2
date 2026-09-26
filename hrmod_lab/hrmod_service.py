"""Two-phase orchestration for the standalone HRmod Lab.

Phase one parses TCX and calls the strict HR-only core with *only*
``parsed.hr_input_samples``.  Phase two is a separate operation over the
already-computed result and the physically separate reference container.
"""

from __future__ import annotations

from dataclasses import dataclass
import os
from typing import Any, BinaryIO, Mapping, Sequence, TextIO

from .hrmod_core import compute_hrmod_hr_only
from .reference_validation import (
    ReferenceValidationConfig,
    ReferenceValidationResult,
    evaluate_against_reference,
)
from .schemas import AthleteHRProfile, HRmodConfig, HRmodResult
from .tcx_adapter import (
    ReferenceChannels,
    TCXParseDiagnostics,
    TCXParserConfig,
    parse_tcx,
)


@dataclass(frozen=True, slots=True)
class HRmodCoreRun:
    """Immutable phase-one artifacts with core and references kept separate."""

    hrmod_result: HRmodResult
    reference_channels: ReferenceChannels
    tcx_diagnostics: TCXParseDiagnostics

    @property
    def hr_input_hash(self) -> str:
        return self.hrmod_result.hr_input_hash

    @property
    def model_version(self) -> str:
        return self.hrmod_result.model_version


@dataclass(frozen=True, slots=True)
class HRmodValidatedRun:
    """Phase-two result retaining the unchanged phase-one artifacts."""

    core_run: HRmodCoreRun
    validation_result: ReferenceValidationResult

    @property
    def hrmod_result(self) -> HRmodResult:
        return self.core_run.hrmod_result

    @property
    def reference_channels(self) -> ReferenceChannels:
        return self.core_run.reference_channels


def run_hr_only_phase(
    *,
    tcx_source: bytes
    | bytearray
    | memoryview
    | str
    | os.PathLike[str]
    | BinaryIO
    | TextIO,
    athlete_profile: AthleteHRProfile,
    hrmod_config: HRmodConfig | None = None,
    parser_config: TCXParserConfig | None = None,
) -> HRmodCoreRun:
    """Parse TCX and compute HRmod without exposing references to core."""

    effective_hrmod_config = hrmod_config or HRmodConfig()
    effective_parser_config = parser_config or TCXParserConfig(
        long_gap_threshold_s=effective_hrmod_config.long_gap_threshold_s,
        regularity_tolerance_s=effective_hrmod_config.sampling_regularity_tolerance_s,
    )
    parsed = parse_tcx(tcx_source, config=effective_parser_config)
    # This explicit attribute access is the architectural boundary: no parsed
    # result, generic TCX row, lap, or reference object is accepted by core.
    result = compute_hrmod_hr_only(
        hr_samples=parsed.hr_input_samples,
        athlete_profile=athlete_profile,
        config=effective_hrmod_config,
    )
    return HRmodCoreRun(
        hrmod_result=result,
        reference_channels=parsed.reference_channels,
        tcx_diagnostics=parsed.diagnostics,
    )


def run_reference_phase(
    *,
    core_run: HRmodCoreRun,
    reference_config: ReferenceValidationConfig | Mapping[str, Any] | None = None,
    optional_annotations: Sequence[Any] | None = None,
) -> HRmodValidatedRun:
    """Evaluate phase-one output post hoc; never call or refit the core."""

    if not isinstance(core_run, HRmodCoreRun):
        raise TypeError("core_run must be an HRmodCoreRun from run_hr_only_phase")
    core_snapshot = core_run.hrmod_result.to_dict()
    validation = evaluate_against_reference(
        hrmod_result=core_run.hrmod_result,
        reference_channels=core_run.reference_channels,
        reference_config=reference_config,
        optional_annotations=optional_annotations,
    )
    if core_run.hrmod_result.to_dict() != core_snapshot:
        raise RuntimeError("Reference phase mutated the completed HR-only result")
    if validation.hr_input_hash != core_run.hr_input_hash:
        raise RuntimeError("Reference phase returned a different HR input hash")
    if validation.model_version != core_run.model_version:
        raise RuntimeError("Reference phase returned a different model version")
    return HRmodValidatedRun(core_run=core_run, validation_result=validation)


# Verbose aliases make the boundary obvious in integration code.
compute_hrmod_from_tcx_hr_only = run_hr_only_phase
evaluate_core_run_against_reference = run_reference_phase


__all__ = [
    "HRmodCoreRun",
    "HRmodValidatedRun",
    "compute_hrmod_from_tcx_hr_only",
    "evaluate_core_run_against_reference",
    "run_hr_only_phase",
    "run_reference_phase",
]

