"""Standalone Streamlit entry point for the experimental HR-only HRmod Lab.

Run from the repository root with::

    python -m streamlit run hrmod_lab_app.py

This file is intentionally not imported by the production application and does
not add an entry to its navigation.  The only value passed to the model core is
``TCXParseResult.hr_input_samples``; every reference channel remains post-hoc.
"""

from __future__ import annotations

from dataclasses import asdict, fields, is_dataclass
from datetime import date, datetime
import hashlib
from io import BytesIO
import json
from math import isfinite
from typing import Any, Iterable, Mapping, Sequence
import zipfile

import numpy as np
import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots
import streamlit as st

import hrmod_lab.terrain_gate as terrain_gate

from hrmod_lab import (
    AthleteHRProfile,
    HRmodConfig,
    HRmodResult,
    HRZone,
    compute_hrmod_hr_only,
)
from hrmod_lab.plotting import build_hr_only_figure as _hr_only_figure
from hrmod_lab.exports import (
    export_terrain_result_json,
    export_terrain_timeseries_csv,
    export_terrain_wave_summary_csv,
    export_terrain_zone_summary_csv,
)
from hrmod_lab.reference_validation import (
    ReferenceValidationConfig,
    ReferenceZone,
    evaluate_against_reference,
)
from hrmod_lab.tcx_adapter import (
    ReferenceChannels,
    TCXParseResult,
    TCXParserConfig,
    parse_tcx,
)


APP_TITLE = "HRmod Lab v4 · experimental HR-only mirror shift"
SCIENTIFIC_LIMITATION = (
    "HRmod v4 експериментално оглежда към възхода част от HR площта над локалния baseline "
    "след пика. По подразбиране се разглеждат завършени HR вълни до 180 s с пик поне "
    "80% от зададения HRmax; това са регулируеми HR-only критерии, а не доказателство за "
    "реалната продължителност или мощност на усилието. "
    "Ако кратко усилие не остави различим HR отговор, HR-only моделът не може да го възстанови. "
    "Теренният филтър е отделен post-core слой: той изключва от donor само пробите в устойчиво "
    "спускане и преизчислява същата площ. Моделът не може да знае реалното механично "
    "натоварване без независим reference канал. Резултатът е HR-еквивалентна хипотеза, а не измерена мощност."
)
CORE_STATE_KEY = "hrmod_lab_core_run"
CORE_CACHE_KEY = "hrmod_lab_core_cache"
REFERENCE_STATE_KEY = "hrmod_lab_reference_result"
TERRAIN_STATE_KEY = "hrmod_lab_terrain_result"
WAVE_TABLE_COLUMNS = (
    "wave_id",
    "status",
    "morphology",
    "morphology_reason",
    "correction_strategy",
    "terrain_status",
    "terrain_rejection_reason",
    "downhill_overlap_s",
    "downhill_overlap_fraction",
    "min_smoothed_grade_pct",
    "moved_area_candidate_bpm_s",
    "moved_area_final_bpm_s",
    "rise_start_timestamp",
    "peak_timestamp",
    "tail_end_timestamp",
    "end_reason",
    "baseline_hr_bpm",
    "rise_bpm",
    "fall_bpm",
    "receiver_duration_s",
    "donor_duration_s",
    "donor_available_area_bpm_s",
    "requested_area_bpm_s",
    "receiver_capacity_bpm_s",
    "moved_area_bpm_s",
    "moved_fraction_of_donor",
    "area_balance_error_bpm_s",
    "capacity_limited",
    "skip_reason",
    "raw_zone_seconds",
    "clean_zone_seconds",
    "hrmod_zone_seconds",
    "hrmod_minus_raw_zone_seconds",
    "hrmod_minus_clean_zone_seconds",
)


st.set_page_config(
    page_title=APP_TITLE,
    page_icon="🫀",
    layout="wide",
    initial_sidebar_state="expanded",
)


def _plain(value: Any) -> Any:
    """Return JSON-ready values without relying on private model internals."""

    if value is None or isinstance(value, (str, int, bool)):
        return value
    if isinstance(value, float):
        return value if isfinite(value) else None
    if isinstance(value, (np.integer, np.bool_)):
        return value.item()
    if isinstance(value, np.floating):
        number = float(value)
        return number if isfinite(number) else None
    if isinstance(value, (datetime, date, pd.Timestamp)):
        return value.isoformat()
    if isinstance(value, pd.DataFrame):
        return [_plain(row) for row in value.to_dict(orient="records")]
    if isinstance(value, pd.Series):
        return [_plain(item) for item in value.tolist()]
    if is_dataclass(value):
        return {
            item.name: _plain(getattr(value, item.name))
            for item in fields(value)
        }
    if hasattr(value, "to_dict") and callable(value.to_dict):
        return _plain(value.to_dict())
    if isinstance(value, Mapping):
        return {str(key): _plain(item) for key, item in value.items()}
    if isinstance(value, (tuple, list, set, frozenset)):
        return [_plain(item) for item in value]
    return str(value)


def _records_frame(value: Any) -> pd.DataFrame:
    """Convert public result rows/dataclasses/mappings to a display frame."""

    if value is None:
        return pd.DataFrame()
    if isinstance(value, pd.DataFrame):
        return value.copy()
    if isinstance(value, Mapping):
        for candidate in (
            "aligned_timeseries",
            "timeseries",
            "rows",
            "samples",
            "records",
        ):
            rows = value.get(candidate)
            if isinstance(rows, (list, tuple)):
                return _records_frame(rows)
        return pd.DataFrame([_plain(value)])
    if hasattr(value, "to_dict") and not isinstance(value, (list, tuple)):
        plain = _plain(value)
        if isinstance(plain, list):
            return pd.DataFrame(plain)
        if isinstance(plain, Mapping):
            return pd.DataFrame([plain])
    if isinstance(value, Iterable) and not isinstance(value, (str, bytes, bytearray)):
        return pd.DataFrame([_plain(item) for item in value])
    return pd.DataFrame()


def _member(value: Any, *names: str, default: Any = None) -> Any:
    for name in names:
        if isinstance(value, Mapping) and name in value:
            return value[name]
        if hasattr(value, name):
            return getattr(value, name)
    return default


def _ordered_wave_frame(frame: pd.DataFrame) -> pd.DataFrame:
    """Keep the required scientific columns first without hiding diagnostics."""

    leading = [column for column in WAVE_TABLE_COLUMNS if column in frame.columns]
    trailing = [column for column in frame.columns if column not in leading]
    return frame.loc[:, [*leading, *trailing]]


def _json_bytes(value: Any) -> bytes:
    return json.dumps(
        _plain(value),
        ensure_ascii=False,
        indent=2,
        sort_keys=True,
        allow_nan=False,
    ).encode("utf-8")


def _csv_bytes(frame: pd.DataFrame) -> bytes:
    return frame.to_csv(index=False).encode("utf-8-sig")


def _first_column(frame: pd.DataFrame, names: Sequence[str]) -> str | None:
    return next((name for name in names if name in frame.columns), None)


def _format_number(value: Any, *, percent: bool = False) -> str:
    if value is None:
        return "—"
    try:
        number = float(value)
    except (TypeError, ValueError):
        return str(value)
    if not np.isfinite(number):
        return "—"
    if percent:
        return f"{100.0 * number:.1f}%"
    return f"{number:.3g}"


def _clear_run_state() -> None:
    st.session_state.pop(CORE_STATE_KEY, None)
    st.session_state.pop(REFERENCE_STATE_KEY, None)
    st.session_state.pop(TERRAIN_STATE_KEY, None)
    st.session_state.pop("hrmod_lab_annotations", None)
    st.session_state.pop("hrmod_lab_annotation_import_hash", None)


def _terrain_display_frames(
    result: HRmodResult,
    terrain_result: Any | None,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Combine immutable core rows with display-only terrain output."""

    core_timeseries = _records_frame(result.timeseries)
    core_waves = _records_frame(result.wave_summary)
    if terrain_result is None:
        return core_timeseries, core_waves

    terrain_timeseries = _records_frame(
        _member(terrain_result, "timeseries", "terrain_timeseries")
    )
    terrain_waves = _records_frame(
        _member(terrain_result, "wave_summary", "terrain_wave_summary")
    )
    if not terrain_timeseries.empty:
        join_column = _first_column(
            core_timeseries,
            tuple(
                name
                for name in ("timestamp", "elapsed_s")
                if name in terrain_timeseries.columns
            ),
        )
        terrain_columns = [
            column
            for column in terrain_timeseries.columns
            if column not in core_timeseries.columns or column == join_column
        ]
        if join_column is not None:
            core_timeseries = core_timeseries.merge(
                terrain_timeseries.loc[:, terrain_columns],
                on=join_column,
                how="left",
                validate="one_to_one",
            )
        elif len(core_timeseries) == len(terrain_timeseries):
            for column in terrain_columns:
                core_timeseries[column] = terrain_timeseries[column].to_numpy()
    if not terrain_waves.empty:
        terrain_columns = [
            column
            for column in terrain_waves.columns
            if column not in core_waves.columns or column == "wave_id"
        ]
        if "wave_id" in core_waves.columns and "wave_id" in terrain_waves.columns:
            core_waves = core_waves.merge(
                terrain_waves.loc[:, terrain_columns],
                on="wave_id",
                how="left",
                validate="one_to_one",
            )
    return core_timeseries, core_waves


def _terrain_zone_display_frame(
    result: HRmodResult,
    terrain_result: Any | None,
) -> pd.DataFrame:
    """Build an explicitly labelled core/candidate/final zone comparison."""

    core_zones = _records_frame(result.zone_summary)
    terrain_zones = _records_frame(
        _member(terrain_result, "zone_summary", "zones")
        if terrain_result is not None
        else None
    )
    if terrain_zones.empty:
        terrain_zones = core_zones.rename(
            columns={
                "hrmod_seconds": "hrmod_candidate_seconds",
                "hrmod_percent": "hrmod_candidate_percent",
            }
        ).copy()
        terrain_zones["hrmod_final_seconds"] = terrain_zones.get(
            "hrmod_candidate_seconds"
        )
        terrain_zones["hrmod_final_percent"] = terrain_zones.get(
            "hrmod_candidate_percent"
        )
        terrain_zones["final_minus_candidate_seconds"] = 0.0

    clean_value_columns = [
        column
        for column in ("clean_seconds", "clean_percent")
        if column in core_zones.columns and column not in terrain_zones.columns
    ]
    if "zone_name" in terrain_zones.columns and clean_value_columns:
        terrain_zones = terrain_zones.merge(
            core_zones.loc[:, ["zone_name", *clean_value_columns]],
            on="zone_name",
            how="left",
            validate="one_to_one",
        )

    display_order = (
        "zone_name",
        "lower_bpm",
        "upper_bpm",
        "raw_seconds",
        "raw_percent",
        "clean_seconds",
        "clean_percent",
        "hrmod_candidate_seconds",
        "hrmod_candidate_percent",
        "hrmod_final_seconds",
        "hrmod_final_percent",
        "final_minus_candidate_seconds",
        "final_minus_raw_seconds",
    )
    return terrain_zones.loc[
        :, [column for column in display_order if column in terrain_zones.columns]
    ]


def _terrain_summary_panel(terrain_result: Any, wave_frame: pd.DataFrame) -> None:
    """Show terrain decisions while tolerating additive diagnostics fields."""

    diagnostics = _plain(_member(terrain_result, "diagnostics", default={}))
    if not isinstance(diagnostics, Mapping):
        diagnostics = {}
    candidate = int(
        diagnostics.get("candidate_wave_count", diagnostics.get("total_wave_count", len(wave_frame)))
        or 0
    )
    status = (
        wave_frame.get("terrain_status", pd.Series(dtype=str))
        .astype(str)
        .str.casefold()
    )
    rejected = int(
        diagnostics.get(
            "terrain_rejected_wave_count",
            diagnostics.get(
                "rejected_wave_count",
                status.isin(
                    ("terrain_confounded", "terrain_rejected", "rejected")
                ).sum(),
            ),
        )
        or 0
    )
    accepted = int(
        diagnostics.get(
            "accepted_wave_count",
            max(0, candidate - rejected),
        )
        or 0
    )
    rejected_fraction = diagnostics.get("terrain_rejected_fraction")
    if rejected_fraction is None:
        rejected_fraction = rejected / candidate if candidate else 0.0
    adjusted = int(diagnostics.get("terrain_adjusted_wave_count", 0) or 0)
    adjusted_fraction = diagnostics.get("terrain_adjusted_fraction")
    if adjusted_fraction is None:
        adjusted_fraction = adjusted / candidate if candidate else 0.0
    metrics = st.columns(4)
    metrics[0].metric("Candidate waves", candidate)
    metrics[1].metric("Accepted waves", accepted)
    metrics[2].metric("Terrain-adjusted waves", adjusted or rejected)
    metrics[3].metric(
        "Terrain-adjusted share",
        _format_number(adjusted_fraction if adjusted else rejected_fraction, percent=True),
    )
    area = st.columns(4)
    area[0].metric(
        "Candidate moved area",
        _format_number(
            diagnostics.get(
                "total_candidate_moved_area_bpm_s",
                diagnostics.get("candidate_moved_area_bpm_s"),
            )
        )
        + " bpm·s",
    )
    area[1].metric(
        "Final moved area",
        _format_number(
            diagnostics.get(
                "total_final_moved_area_bpm_s",
                diagnostics.get("final_moved_area_bpm_s"),
            )
        )
        + " bpm·s",
    )
    area[2].metric(
        "Grade coverage",
        _format_number(diagnostics.get("grade_coverage_fraction"), percent=True),
    )
    area[3].metric(
        "Grade source", diagnostics.get("grade_source") or "unavailable"
    )


def _core_fingerprint(result: HRmodResult) -> str:
    """Hash the complete public core result around reference evaluation."""

    canonical = json.dumps(
        _plain(result),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def _parser_signature(payload_hash: str, config: TCXParserConfig) -> str:
    encoded = json.dumps(
        {"payload_hash": payload_hash, "parser_config": _plain(config)},
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _core_run_signature(
    hr_samples: Sequence[Any],
    profile: AthleteHRProfile,
    model_config: HRmodConfig,
) -> str:
    """Identify one HR-only run without including any reference channel."""

    encoded = json.dumps(
        {
            "hr_input_samples": _plain(hr_samples),
            "athlete_profile": _plain(profile),
            "model_config": _plain(model_config),
        },
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _preview_parse(
    payload: bytes,
    payload_hash: str,
    parser_config: TCXParserConfig,
) -> TCXParseResult:
    """Parse once per file/parser configuration across Streamlit reruns."""

    signature = _parser_signature(payload_hash, parser_config)
    if st.session_state.get("hrmod_lab_preview_signature") != signature:
        parsed = parse_tcx(payload, config=parser_config)
        st.session_state["hrmod_lab_preview_parse"] = parsed
        st.session_state["hrmod_lab_preview_signature"] = signature
        _clear_run_state()
    return st.session_state["hrmod_lab_preview_parse"]


def _quality_report(parsed: TCXParseResult) -> None:
    diagnostics = parsed.diagnostics
    values = diagnostics.to_dict()
    samples = _records_frame(parsed.hr_input_samples)
    hr_column = _first_column(samples, ("heart_rate_bpm", "raw_hr_bpm", "raw_hr"))
    observed_max = None
    if hr_column:
        observed = pd.to_numeric(samples[hr_column], errors="coerce")
        observed_max = observed.max() if observed.notna().any() else None

    st.subheader("2 · Data-quality отчет")
    metric_columns = st.columns(6)
    metric_columns[0].metric("Trackpoints", int(values.get("trackpoint_count", 0)))
    metric_columns[1].metric(
        "HR coverage",
        _format_number(values.get("hr_coverage_fraction"), percent=True),
    )
    metric_columns[2].metric(
        "Median dt",
        f"{_format_number(values.get('median_dt_s'))} s",
    )
    metric_columns[3].metric(
        "≈1 Hz regularity",
        _format_number(values.get("sampling_regularity_fraction"), percent=True),
    )
    metric_columns[4].metric("Long gaps", int(values.get("long_gap_count", 0)))
    metric_columns[5].metric(
        "Observed max",
        "—" if observed_max is None else f"{float(observed_max):.1f} bpm",
        help="Само информативно; не се използва автоматично като HRmax.",
    )

    detail_columns = st.columns(4)
    detail_columns[0].metric(
        "Missing HR", int(values.get("missing_hr_count", 0))
    )
    detail_columns[1].metric(
        "Duplicate timestamps", int(values.get("duplicate_timestamp_count", 0))
    )
    detail_columns[2].metric(
        "Timezone assumed", int(values.get("timezone_assumed_count", 0))
    )
    detail_columns[3].metric(
        "Продължителност",
        f"{_format_number(values.get('duration_s'))} s",
    )

    flags = list(values.get("flags", []) or [])
    warnings = list(values.get("warnings", []) or [])
    if flags:
        st.warning("TCX quality flags: " + ", ".join(map(str, flags)))
    if warnings:
        st.info(" · ".join(map(str, warnings)))
    with st.expander("Пълни TCX diagnostics"):
        st.json(_plain(diagnostics))


def _simple_model_config_widgets(defaults: HRmodConfig) -> dict[str, Any]:
    """Compact controls for the single v4 model."""

    values = asdict(defaults)
    st.info(
        "Единствен активен модел: v4 mirror — следпиковата площ над локалния "
        "baseline се оглежда към целия възход."
    )
    main = st.columns(4)
    values["alpha"] = main[0].slider(
        "Сила на модулацията",
        0.0,
        1.0,
        float(defaults.alpha),
        0.05,
        help="Каква част от допустимата следпикова площ да бъде преместена. 1.0 означава цялата площ.",
    )
    peak_percent = main[1].slider(
        "Минимален пик (% HRmax)",
        50,
        100,
        int(round(defaults.mirror_min_peak_fraction_hrmax * 100)),
        1,
        help="Вълната се моделира само ако нейният HR пик достига този процент от индивидуалния HRmax.",
    )
    values["mirror_min_peak_fraction_hrmax"] = peak_percent / 100.0
    values["mirror_max_wave_duration_s"] = float(
        main[2].number_input(
            "Максимална вълна (s)",
            min_value=10.0,
            value=float(defaults.mirror_max_wave_duration_s),
            step=10.0,
            help="Максималното време от начало на HR възхода до края на връщането; по-дългите вълни се пропускат.",
        )
    )
    values["smoothing_window_s"] = float(
        main[3].number_input(
            "Изглаждане (s)",
            min_value=1.0,
            value=float(defaults.smoothing_window_s),
            step=1.0,
            help="Прозорец само за откриване на формата; измереният HR не се заменя от изгладения сигнал.",
        )
    )
    with st.expander("Разширени HR настройки", expanded=False):
        row = st.columns(4)
        values["rise_threshold_bpm_s"] = float(row[0].number_input(
            "Праг за възход (bpm/s)", min_value=0.001, value=float(defaults.rise_threshold_bpm_s), step=0.01,
            help="Минималният устойчив положителен наклон, с който започва HR вълна."
        ))
        values["min_rise_bpm"] = float(row[1].number_input(
            "Минимално покачване (bpm)", min_value=0.1, value=float(defaults.min_rise_bpm), step=0.5,
            help="Минималната обща разлика в HR, необходима за валиден възход."
        ))
        values["min_sustained_rise_s"] = float(row[2].number_input(
            "Устойчив възход (s)", min_value=0.1, value=float(defaults.min_sustained_rise_s), step=0.5,
            help="Колко време наклонът трябва да остане положителен, преди да потвърдим възход."
        ))
        values["fall_threshold_bpm_s"] = float(row[3].number_input(
            "Праг за спад (bpm/s)", min_value=0.001, value=float(defaults.fall_threshold_bpm_s), step=0.01,
            help="Минималният отрицателен наклон, използван за потвърждаване на следпиковия спад."
        ))
        row2 = st.columns(4)
        values["min_fall_bpm"] = float(row2[0].number_input(
            "Минимален спад (bpm)", min_value=0.1, value=float(defaults.min_fall_bpm), step=0.5,
            help="Минималното общо понижение след пика за потвърдена вълна."
        ))
        values["min_sustained_fall_s"] = float(row2[1].number_input(
            "Устойчив спад (s)", min_value=0.1, value=float(defaults.min_sustained_fall_s), step=0.5,
            help="Минималното време на потвърдения отрицателен HR наклон."
        ))
        values["baseline_lookback_s"] = float(row2[2].number_input(
            "Baseline история (s)", min_value=1.0, value=float(defaults.baseline_lookback_s), step=1.0,
            help="Времето преди възхода, от което се оценява локалният HR baseline."
        ))
        values["return_tolerance_bpm"] = float(row2[3].number_input(
            "Толеранс до baseline (bpm)", min_value=0.0, value=float(defaults.return_tolerance_bpm), step=0.5,
            help="Колко близо до локалния baseline трябва да стигне HR, за да приключи вълната."
        ))
        row3 = st.columns(4)
        values["long_gap_threshold_s"] = float(row3[0].number_input(
            "Дълга липса (s)", min_value=1.0, value=float(defaults.long_gap_threshold_s), step=1.0,
            help="По-голяма липса разделя сигнала; площ никога не се мести през нея."
        ))
        values["max_interpolation_gap_s"] = float(row3[1].number_input(
            "Макс. интерполация (s)", min_value=0.0, value=float(defaults.max_interpolation_gap_s), step=0.5,
            help="Само по-кратки HR липси могат да бъдат прозрачно интерполирани."
        ))
        addition = row3[2].checkbox(
            "Лимит на добавяне", value=defaults.max_addition_bpm is not None,
            help="Включва максимална добавка към една HR проба; по подразбиране е изключено."
        )
        addition_value = row3[2].number_input(
            "Макс. добавяне (bpm)", min_value=0.1, value=float(defaults.max_addition_bpm or 30.0), step=1.0,
            disabled=not addition, help="Горна граница на добавката към една проба."
        )
        removal = row3[3].checkbox(
            "Лимит на отнемане", value=defaults.max_removal_bpm is not None,
            help="Включва максимално отнемане от една donor проба; по подразбиране е изключено."
        )
        removal_value = row3[3].number_input(
            "Макс. отнемане (bpm)", min_value=0.1, value=float(defaults.max_removal_bpm or 30.0), step=1.0,
            disabled=not removal, help="Горна граница на отнемането от една проба, без преминаване под baseline."
        )
        values["max_addition_bpm"] = float(addition_value) if addition else None
        values["max_removal_bpm"] = float(removal_value) if removal else None
    return values


def _simple_terrain_config_widgets(defaults: Any) -> Any:
    """Compact post-core downhill donor filter controls."""

    values = asdict(defaults)
    row = st.columns(3)
    values["terrain_gate_enabled"] = row[0].checkbox(
        "Теренен филтър",
        value=bool(defaults.terrain_gate_enabled),
        help="Работи само след готовия HR-only candidate и не променя HR input hash или избора на вълни.",
    )
    values["downhill_threshold_pct"] = float(row[1].number_input(
        "Спускане (%)", max_value=-0.1, value=float(defaults.downhill_threshold_pct), step=0.5,
        help="Donor проба се изключва, когато изгладеният наклон е равен или по-нисък от този праг."
    ))
    values["min_sustained_downhill_s"] = float(row[2].number_input(
        "Устойчиво спускане (s)", min_value=1.0, value=float(defaults.min_sustained_downhill_s), step=1.0,
        help="Минималната продължителност под прага, преди участъкът да се приеме за реално спускане."
    ))
    with st.expander("Разширени теренни настройки", expanded=False):
        advanced = st.columns(4)
        values["use_transition_buffer"] = advanced[0].checkbox(
            "Използвай terrain buffer", value=bool(defaults.use_transition_buffer),
            help="Ако е включено, изключва donor проби и около границите на спускането; по-консервативно е."
        )
        values["terrain_transition_buffer_s"] = float(advanced[1].number_input(
            "Terrain buffer (s)", min_value=0.0, value=float(defaults.terrain_transition_buffer_s), step=1.0,
            help="Секунди преди и след устойчивото спускане, използвани само когато buffer е включен."
        ))
        values["grade_smoothing_window_s"] = float(advanced[2].number_input(
            "Изглаждане на наклона (s)", min_value=1.0, value=float(defaults.grade_smoothing_window_s), step=1.0,
            help="Медианен времеви прозорец за потискане на единични GPS/височинни пикове."
        ))
        values["derived_grade_window_m"] = float(advanced[3].number_input(
            "Прозорец за наклон (m)", min_value=5.0, value=float(defaults.derived_grade_window_m), step=5.0,
            help="Дистанционният прозорец за извеждане на наклон от надморска височина и разстояние."
        ))
        advanced2 = st.columns(3)
        values["derived_min_distance_span_m"] = float(advanced2[0].number_input(
            "Минимален distance span (m)", min_value=1.0, value=float(defaults.derived_min_distance_span_m), step=1.0,
            help="Минималната реална дистанция за надеждно изчисляване на локален наклон."
        ))
        values["min_grade_coverage_fraction"] = float(advanced2[1].slider(
            "Минимално grade покритие", 0.1, 1.0, float(defaults.min_grade_coverage_fraction), 0.05,
            help="Под това покритие terrain филтърът fail-closed остава неприложен и final=candidate."
        ))
        values["max_terrain_sample_gap_s"] = float(advanced2[2].number_input(
            "Макс. terrain gap (s)", min_value=1.0, value=float(defaults.max_terrain_sample_gap_s), step=1.0,
            help="Прекъсва устойчивото спускане през по-голяма липса в теренните данни."
        ))
    return type(defaults)(**values)


def _terrain_run_signature(run: Mapping[str, Any], config: Any) -> str:
    payload = {
        "core_fingerprint": run["core_fingerprint"],
        "input_file_sha256": run["file_sha256"],
        "terrain_config": _plain(config),
    }
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def _athlete_profile(
    hr_floor_bpm: float,
    hrmax_bpm: float,
    internal_boundaries: Sequence[float],
) -> AthleteHRProfile:
    boundaries = [float(hr_floor_bpm), *map(float, internal_boundaries), float(hrmax_bpm)]
    if any(left >= right for left, right in zip(boundaries, boundaries[1:])):
        raise ValueError(
            "HR_floor, четирите вътрешни граници и HRmax трябва да са строго "
            "нарастващи."
        )
    zones = tuple(
        HRZone(name=f"Z{index + 1}", lower_bpm=lower, upper_bpm=upper)
        for index, (lower, upper) in enumerate(zip(boundaries, boundaries[1:]))
    )
    return AthleteHRProfile(
        hrmax_bpm=float(hrmax_bpm),
        hr_floor_bpm=float(hr_floor_bpm),
        zones=zones,
    )


def _reference_samples_frame(reference_channels: ReferenceChannels) -> pd.DataFrame:
    frame = _records_frame(reference_channels.samples)
    if "timestamp" in frame.columns:
        frame["timestamp"] = pd.to_datetime(frame["timestamp"], errors="coerce", utc=True)
    return frame


def _reference_overlay_figure(
    core_timeseries: pd.DataFrame,
    reference_samples: pd.DataFrame,
    selected_channels: Sequence[str],
) -> go.Figure:
    figure = make_subplots(specs=[[{"secondary_y": True}]])
    core_x_column = _first_column(core_timeseries, ("timestamp", "elapsed_s"))
    if core_x_column:
        core_x = core_timeseries[core_x_column]
        if core_x_column == "timestamp":
            core_x = pd.to_datetime(core_x, errors="coerce", utc=True)
        for candidates, label, color in (
            (("clean_hr_bpm", "clean_hr"), "clean_hr", "#3b82f6"),
            (("hrmod_bpm", "hrmod"), "HRmod", "#dc2626"),
        ):
            column = _first_column(core_timeseries, candidates)
            if column:
                figure.add_trace(
                    go.Scatter(
                        x=core_x,
                        y=pd.to_numeric(core_timeseries[column], errors="coerce"),
                        name=label,
                        mode="lines",
                        line={"color": color, "width": 2},
                    ),
                    secondary_y=False,
                )

    reference_x_column = _first_column(reference_samples, ("timestamp", "elapsed_s"))
    reference_x = (
        reference_samples[reference_x_column]
        if reference_x_column
        else pd.Series(dtype=float)
    )
    reference_colors = ("#16a34a", "#7c3aed", "#ea580c", "#0891b2", "#64748b")
    for channel, color in zip(selected_channels, reference_colors):
        if channel not in reference_samples.columns:
            continue
        figure.add_trace(
            go.Scatter(
                x=reference_x,
                y=pd.to_numeric(reference_samples[channel], errors="coerce"),
                name=f"reference · {channel}",
                mode="lines",
                line={"color": color, "width": 1.2, "dash": "dot"},
                connectgaps=False,
            ),
            secondary_y=True,
        )
    figure.update_yaxes(title_text="HR (bpm)", secondary_y=False)
    figure.update_yaxes(title_text="Reference — оригинални единици", secondary_y=True)
    figure.update_layout(
        height=560,
        hovermode="x unified",
        legend={"orientation": "h", "y": 1.04, "x": 0},
        margin={"l": 45, "r": 45, "t": 65, "b": 40},
    )
    return figure


def _annotation_frame(reference_channels: ReferenceChannels) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    for lap in reference_channels.laps:
        item = lap.to_dict()
        rows.append(
            {
                "annotation_id": item.get("annotation_id"),
                "start_time": item.get("start_time"),
                "end_time": item.get("end_time"),
                "label": item.get("annotation_id"),
                "external_zone": None,
                "source": item.get("source", "tcx_lap"),
            }
        )
    return pd.DataFrame(
        rows,
        columns=(
            "annotation_id",
            "start_time",
            "end_time",
            "label",
            "external_zone",
            "source",
        ),
    )


def _reference_zones(frame: pd.DataFrame, label: str) -> tuple[ReferenceZone, ...]:
    zones: list[ReferenceZone] = []
    for index, row in frame.iterrows():
        zone_label = str(row.get("label", "")).strip()
        lower = pd.to_numeric(pd.Series([row.get("lower")]), errors="coerce").iloc[0]
        upper = pd.to_numeric(pd.Series([row.get("upper")]), errors="coerce").iloc[0]
        if not zone_label and pd.isna(lower) and pd.isna(upper):
            continue
        if not zone_label or pd.isna(lower):
            raise ValueError(f"{label}, row {index + 1}: label и lower са задължителни")
        zones.append(
            ReferenceZone(
                label=zone_label,
                lower=float(lower),
                upper=None if pd.isna(upper) else float(upper),
            )
        )
    return tuple(zones)


def _read_annotation_upload(uploaded: Any) -> pd.DataFrame:
    payload = uploaded.getvalue()
    suffix = uploaded.name.lower().rsplit(".", 1)[-1]
    if suffix == "json":
        data = json.loads(payload.decode("utf-8-sig"))
        if isinstance(data, Mapping):
            data = data.get("annotations", data.get("rows", [data]))
        return pd.DataFrame(data)
    return pd.read_csv(BytesIO(payload))


def _validation_aligned_frame(validation: Any) -> pd.DataFrame:
    for name in ("aligned_timeseries", "aligned_comparison", "timeseries", "rows"):
        if hasattr(validation, name):
            return _records_frame(getattr(validation, name))
    if isinstance(validation, Mapping):
        return _records_frame(validation)
    plain = _plain(validation)
    if isinstance(plain, Mapping):
        return _records_frame(plain)
    return pd.DataFrame()


def _zip_bundle(files: Mapping[str, bytes]) -> bytes:
    buffer = BytesIO()
    with zipfile.ZipFile(buffer, mode="w", compression=zipfile.ZIP_DEFLATED) as archive:
        for filename, payload in files.items():
            archive.writestr(filename, payload)
    return buffer.getvalue()


def _download_panel(
    run: Mapping[str, Any],
    annotations: pd.DataFrame | None,
    validation: Any | None,
    terrain_result: Any | None,
) -> None:
    result: HRmodResult = run["result"]
    timeseries = _records_frame(result.timeseries)
    waves = _records_frame(result.wave_summary)
    zones = _records_frame(result.zone_summary)
    configuration = {
        "model_version": result.model_version,
        "hr_input_hash": result.hr_input_hash,
        "input_filename": run["filename"],
        "input_file_sha256": run["file_sha256"],
        "parser_config": _plain(run["parser_config"]),
        "athlete_profile": _plain(run["athlete_profile"]),
        "hrmod_config": _plain(result.config),
    }
    files: dict[str, bytes] = {
        "processed_hr_only_timeseries.csv": _csv_bytes(timeseries),
        "wave_summary.csv": _csv_bytes(waves),
        "zone_summary.csv": _csv_bytes(zones),
        "run_configuration.json": _json_bytes(configuration),
        "diagnostics.json": _json_bytes(result.diagnostics),
    }
    if annotations is not None and not annotations.empty:
        files["annotations.csv"] = _csv_bytes(annotations)
        files["annotations.json"] = _json_bytes(
            {"annotations": annotations.to_dict(orient="records")}
        )
    if validation is not None:
        aligned = _validation_aligned_frame(validation)
        if not aligned.empty:
            files["reference_aligned_comparison.csv"] = _csv_bytes(aligned)
        files["reference_validation.json"] = _json_bytes(validation)
    if terrain_result is not None:
        files["terrain_gated_timeseries.csv"] = export_terrain_timeseries_csv(
            terrain_result
        )
        files["terrain_wave_summary.csv"] = export_terrain_wave_summary_csv(
            terrain_result
        )
        files["terrain_zone_summary.csv"] = export_terrain_zone_summary_csv(
            terrain_result
        )
        files["terrain_result.json"] = export_terrain_result_json(terrain_result)

    st.caption(
        "HR-only core CSV/JSON файловете остават без terrain/speed/power/lap колони. "
        "Terrain raw/candidate/final/grade/status сравнението е отделен post-hoc артефакт."
    )
    buttons = st.columns(4)
    buttons[0].download_button(
        "Timeseries CSV",
        files["processed_hr_only_timeseries.csv"],
        "processed_hr_only_timeseries.csv",
        "text/csv",
        use_container_width=True,
    )
    buttons[1].download_button(
        "Waves CSV",
        files["wave_summary.csv"],
        "wave_summary.csv",
        "text/csv",
        use_container_width=True,
    )
    buttons[2].download_button(
        "Zones CSV",
        files["zone_summary.csv"],
        "zone_summary.csv",
        "text/csv",
        use_container_width=True,
    )
    buttons[3].download_button(
        "Всички резултати · ZIP",
        _zip_bundle(files),
        "hrmod_lab_results.zip",
        "application/zip",
        use_container_width=True,
    )
    json_buttons = st.columns(3)
    json_buttons[0].download_button(
        "Run config JSON",
        files["run_configuration.json"],
        "run_configuration.json",
        "application/json",
        use_container_width=True,
    )
    json_buttons[1].download_button(
        "Diagnostics JSON",
        files["diagnostics.json"],
        "diagnostics.json",
        "application/json",
        use_container_width=True,
    )
    if "reference_aligned_comparison.csv" in files:
        json_buttons[2].download_button(
            "Reference CSV",
            files["reference_aligned_comparison.csv"],
            "reference_aligned_comparison.csv",
            "text/csv",
            use_container_width=True,
        )
    if "terrain_gated_timeseries.csv" in files:
        terrain_buttons = st.columns(4)
        terrain_buttons[0].download_button(
            "Terrain timeseries CSV",
            files["terrain_gated_timeseries.csv"],
            "terrain_gated_timeseries.csv",
            "text/csv",
            use_container_width=True,
        )
        terrain_buttons[1].download_button(
            "Terrain waves CSV",
            files["terrain_wave_summary.csv"],
            "terrain_wave_summary.csv",
            "text/csv",
            use_container_width=True,
        )
        terrain_buttons[2].download_button(
            "Terrain zones CSV",
            files["terrain_zone_summary.csv"],
            "terrain_zone_summary.csv",
            "text/csv",
            use_container_width=True,
        )
        terrain_buttons[3].download_button(
            "Terrain result JSON",
            files["terrain_result.json"],
            "terrain_result.json",
            "application/json",
            use_container_width=True,
        )


def _diagnostic_panel(result: HRmodResult) -> None:
    diagnostics = result.diagnostics.to_dict()
    coverage = st.columns(4)
    coverage[0].metric(
        "HR coverage",
        _format_number(diagnostics.get("hr_coverage_fraction"), percent=True),
    )
    coverage[1].metric(
        "Sampling regularity",
        _format_number(diagnostics.get("regular_sampling_fraction"), percent=True),
    )
    coverage[2].metric(
        "Interpolated",
        _format_number(diagnostics.get("interpolated_fraction"), percent=True),
    )
    coverage[3].metric(
        "Artifacts",
        _format_number(diagnostics.get("artifact_fraction"), percent=True),
    )
    support = st.columns(4)
    support[0].metric(
        "Detection support",
        _format_number(diagnostics.get("detection_support_fraction"), percent=True),
    )
    support[1].metric(
        "Detected / corrected",
        f"{diagnostics.get('detected_wave_count', 0)} / "
        f"{diagnostics.get('corrected_wave_count', 0)}",
    )
    support[2].metric(
        "Complete / incomplete",
        f"{diagnostics.get('complete_wave_count', 0)} / "
        f"{diagnostics.get('incomplete_wave_count', 0)}",
    )
    support[3].metric(
        "Area conservation",
        "PASS" if diagnostics.get("area_conservation_passed") else "FAIL",
    )
    areas = pd.DataFrame(
        [
            {
                "donor_available_bpm_s": diagnostics.get(
                    "total_donor_available_area_bpm_s"
                ),
                "requested_bpm_s": diagnostics.get("total_requested_area_bpm_s"),
                "receiver_capacity_bpm_s": diagnostics.get(
                    "total_receiver_capacity_bpm_s"
                ),
                "moved_bpm_s": diagnostics.get("total_moved_area_bpm_s"),
                "added_bpm_s": diagnostics.get("total_added_area_bpm_s"),
                "removed_bpm_s": diagnostics.get("total_removed_area_bpm_s"),
                "balance_error_bpm_s": diagnostics.get(
                    "total_area_balance_error_bpm_s"
                ),
                "capacity_limited_bpm_s": diagnostics.get(
                    "total_capacity_limited_area_bpm_s"
                ),
                "moved_fraction_of_donor": diagnostics.get(
                    "moved_fraction_of_donor"
                ),
                "capacity_limited_waves": diagnostics.get(
                    "capacity_limited_wave_count"
                ),
                "edge_affected_samples": diagnostics.get("edge_affected_samples"),
                "gap_affected_samples": diagnostics.get("gap_affected_samples"),
            }
        ]
    )
    st.dataframe(areas, use_container_width=True, hide_index=True)
    flags = diagnostics.get("flags", []) or []
    if flags:
        st.warning("Model flags: " + ", ".join(map(str, flags)))
    skip_reasons = diagnostics.get("skip_reason_counts", {}) or {}
    if skip_reasons:
        st.markdown("**Skip reasons**")
        st.dataframe(
            pd.DataFrame(
                {"skip_reason": list(skip_reasons), "wave_count": list(skip_reasons.values())}
            ),
            use_container_width=True,
            hide_index=True,
        )
    st.markdown("**Parameter sensitivity**")
    st.json(diagnostics.get("parameter_sensitivity", {}))
    with st.expander("Пълни core diagnostics"):
        st.json(_plain(diagnostics))


st.title(APP_TITLE)
st.caption(
    "Самостоятелна лабораторна програма · не е част от продукционния onFlows flow · "
    "офлайн преразпределение на наблюдавана HR площ"
)
st.warning(SCIENTIFIC_LIMITATION, icon="⚠️")
st.info(
    "Core изчислението приема само timestamp, HR/HR-quality, индивидуалния HR "
    "профил и HR-only параметрите. Speed, power, grade, distance, cadence, laps и "
    "manual markers са изолирани в отделни post-hoc terrain/reference слоеве."
)

with st.sidebar:
    st.header("HRmod Lab")
    st.markdown("**Единствен модел:** `hrmod_mirror_area_shift_v4` · experimental")
    st.markdown("**Режим:** offline / completed activity")
    st.markdown("[Документация](./docs/HRMOD_LAB.md)")
    st.divider()
    st.caption(
        "Няма production DB/Intervals.icu достъп. Reference overlays не стартират core."
    )

st.subheader("1 · TCX upload")
uploaded_tcx = st.file_uploader(
    "TCX файл с HR записи",
    type=("tcx",),
    accept_multiple_files=False,
    help="Файлът се обработва в текущата сесия и не се commit-ва в repository.",
)

if uploaded_tcx is None:
    st.info("Качете TCX файл, за да започне HR-only quality проверката.")
    st.stop()

tcx_payload = uploaded_tcx.getvalue()
tcx_sha256 = hashlib.sha256(tcx_payload).hexdigest()
default_config = HRmodConfig()

with st.expander("TCX parser/quality настройки"):
    parser_columns = st.columns(3)
    parser_regularity_target_s = parser_columns[0].number_input(
        "Очаквана sampling стъпка (s)", min_value=0.01, value=1.0, step=0.1,
        help="Очакваният интервал между TCX пробите; използва се само за quality diagnostics."
    )
    parser_regularity_tolerance_s = parser_columns[1].number_input(
        "Sampling tolerance (s)", min_value=0.0, value=0.25, step=0.05,
        help="Допустимото отклонение около очакваната sampling стъпка."
    )
    assume_naive_utc = parser_columns[2].checkbox(
        "Приеми naive timestamp като UTC", value=True,
        help="Как да се тълкуват TCX timestamps без изрично зададена часова зона."
    )
    st.caption(
        "Preview long-gap границата следва текущия default на HRmodConfig. При core "
        "run parser-ът се изпълнява отново с избраната model long-gap стойност."
    )

preview_parser_config = TCXParserConfig(
    long_gap_threshold_s=float(default_config.long_gap_threshold_s),
    regularity_target_s=float(parser_regularity_target_s),
    regularity_tolerance_s=float(parser_regularity_tolerance_s),
    assume_naive_timestamps_utc=bool(assume_naive_utc),
)
try:
    preview_parse = _preview_parse(tcx_payload, tcx_sha256, preview_parser_config)
except Exception as exc:  # Streamlit boundary: parser errors are user-facing.
    st.error(f"TCX файлът не може да бъде обработен: {exc}")
    st.stop()

_quality_report(preview_parse)

st.subheader("3 · Индивидуален HR профил и 5 зони")
st.caption(
    "HRmax не се извежда по възраст или от observed maximum. Зоните се "
    "класифицират по незакръглените стойности."
)

with st.form("hrmod_core_configuration", clear_on_submit=False):
    profile_columns = st.columns(6)
    hr_floor_bpm = profile_columns[0].number_input(
        "HR_floor (bpm)", min_value=1.0, value=40.0, step=1.0,
        help="Физиологичната долна граница за модела; HR никога не се отнема под нея."
    )
    zone_2_lower = profile_columns[1].number_input(
        "Z2 starts", min_value=1.0, value=120.0, step=1.0,
        help="Долната, включена граница на индивидуална зона Z2."
    )
    zone_3_lower = profile_columns[2].number_input(
        "Z3 starts", min_value=1.0, value=140.0, step=1.0,
        help="Долната, включена граница на индивидуална зона Z3."
    )
    zone_4_lower = profile_columns[3].number_input(
        "Z4 starts", min_value=1.0, value=160.0, step=1.0,
        help="Долната, включена граница на индивидуална зона Z4."
    )
    zone_5_lower = profile_columns[4].number_input(
        "Z5 starts", min_value=1.0, value=180.0, step=1.0,
        help="Долната, включена граница на индивидуална зона Z5."
    )
    hrmax_bpm = profile_columns[5].number_input(
        "HRmax (bpm)", min_value=1.0, value=200.0, step=1.0,
        help="Индивидуалният максимален пулс и твърд таван за модулирания HR."
    )

    st.subheader("4 · HR-only mirror модел и основни настройки")
    st.caption(
        f"{default_config.config_version} · единствен активен модел е v4 mirror. Defaults са "
        "exploratory и не са физиологично калибрирани."
    )
    config_values = _simple_model_config_widgets(default_config)
    compute_clicked = st.form_submit_button(
        "Изчисли HRmod (само HR)",
        type="primary",
        use_container_width=True,
    )

if compute_clicked:
    try:
        athlete_profile = _athlete_profile(
            hr_floor_bpm,
            hrmax_bpm,
            (zone_2_lower, zone_3_lower, zone_4_lower, zone_5_lower),
        )
        hrmod_config = HRmodConfig(**config_values)
        run_parser_config = TCXParserConfig(
            long_gap_threshold_s=hrmod_config.long_gap_threshold_s,
            regularity_target_s=float(parser_regularity_target_s),
            regularity_tolerance_s=float(parser_regularity_tolerance_s),
            assume_naive_timestamps_utc=bool(assume_naive_utc),
        )
        # Parsing stays current for terrain/reference channels.  The core cache
        # key below is built only from the parsed HR-only input and HR config.
        run_parse = parse_tcx(tcx_payload, config=run_parser_config)
        core_signature = _core_run_signature(
            run_parse.hr_input_samples, athlete_profile, hrmod_config
        )
        core_cache = st.session_state.setdefault(CORE_CACHE_KEY, {})
        cached_run = core_cache.get(core_signature)
        if isinstance(cached_run, Mapping):
            result = cached_run["result"]
        else:
            with st.spinner("HR-only preprocessing, wave detection и exact mirror area shift…"):
                # Anti-leakage by construction: no reference object is in this call.
                result = compute_hrmod_hr_only(
                    hr_samples=run_parse.hr_input_samples,
                    athlete_profile=athlete_profile,
                    config=hrmod_config,
                )
        run_timeseries = _records_frame(result.timeseries)
        clean_column = _first_column(run_timeseries, ("clean_hr_bpm", "clean_hr"))
        profile_warning = None
        if clean_column:
            clean_values = pd.to_numeric(run_timeseries[clean_column], errors="coerce")
            out_of_profile = clean_values.lt(athlete_profile.hr_floor_bpm) | clean_values.gt(
                athlete_profile.hrmax_bpm
            )
            if out_of_profile.any():
                profile_warning = (
                    "Приетият clean_hr съдържа стойности извън зададените "
                    "HR_floor/HRmax. Проверете профила и артефактите; UI не прилага "
                    "тихо clipping."
                )
        completed_run = {
            "result": result,
            "parsed": run_parse,
            "athlete_profile": athlete_profile,
            "parser_config": run_parser_config,
            "filename": uploaded_tcx.name,
            "file_sha256": tcx_sha256,
            "core_fingerprint": _core_fingerprint(result),
            "profile_warning": profile_warning,
            "core_signature": core_signature,
        }
        core_cache[core_signature] = {
            "result": result,
            "core_fingerprint": completed_run["core_fingerprint"],
        }
        st.session_state[CORE_STATE_KEY] = completed_run
        st.session_state.pop(TERRAIN_STATE_KEY, None)
        st.session_state.pop(REFERENCE_STATE_KEY, None)
        st.session_state.pop("hrmod_lab_annotations", None)
        if isinstance(cached_run, Mapping):
            st.success("HRmod HR-only резултатът е възстановен от session cache.")
        else:
            st.success("HRmod е изчислен само от parser.hr_input_samples.")
    except Exception as exc:  # Config/core validation must remain visible in UI.
        st.error(f"HRmod run-ът не завърши: {exc}")

run = st.session_state.get(CORE_STATE_KEY)
if run is None:
    st.info(
        "Reference validation ще се появи едва след успешно HR-only изчисление."
    )
    st.stop()

result = run["result"]
run_parse = run["parsed"]
athlete_profile = run["athlete_profile"]
timeseries_frame = _records_frame(result.timeseries)
wave_frame = _records_frame(result.wave_summary)
wave_table_frame = _ordered_wave_frame(wave_frame)
base_annotations = _annotation_frame(run_parse.reference_channels)
if "hrmod_lab_annotations" not in st.session_state:
    st.session_state["hrmod_lab_annotations"] = base_annotations

st.subheader("5 · Готов експериментален HR-only core резултат")
summary_columns = st.columns(5)
summary_columns[0].metric("Model version", result.model_version)
summary_columns[1].metric("HR samples", len(timeseries_frame))
summary_columns[2].metric("Detected waves", len(wave_frame))
summary_columns[3].metric(
    "Candidate corrected waves",
    int(pd.to_numeric(wave_frame.get("corrected"), errors="coerce").fillna(0).sum())
    if "corrected" in wave_frame.columns else 0,
)
summary_columns[4].metric(
    "Core input hash", result.hr_input_hash[:16] + "…", help=result.hr_input_hash
)
st.caption(
    "Core fingerprint: `"
    + run["core_fingerprint"][:20]
    + "…` · reference join все още не е част от този резултат."
)
if "morphology" in wave_frame.columns:
    morphology_counts = (
        wave_frame["morphology"].fillna("unknown").astype(str).value_counts()
    )
    strategy_counts = (
        wave_frame.get("correction_strategy", pd.Series(dtype=str))
        .fillna("none")
        .astype(str)
        .value_counts()
    )
    st.caption(
        "V4 mirror · morphology: "
        + ", ".join(f"{name}={count}" for name, count in morphology_counts.items())
        + " · correction: "
        + ", ".join(f"{name}={count}" for name, count in strategy_counts.items())
    )
if run.get("profile_warning"):
    st.warning(run["profile_warning"], icon="⚠️")

st.subheader("6 · Теренен donor филтър (post-processing)")
terrain_config = _simple_terrain_config_widgets(terrain_gate.TerrainGateConfig())
terrain_signature = _terrain_run_signature(run, terrain_config)
terrain_state = st.session_state.get(TERRAIN_STATE_KEY)
cached_terrain_result = (
    terrain_state.get("result") if isinstance(terrain_state, Mapping) else None
)
cached_zone_summary = _member(
    cached_terrain_result, "zone_summary", "zones", default=None
)
if (
    not isinstance(terrain_state, Mapping)
    or terrain_state.get("signature") != terrain_signature
    or cached_zone_summary is None
):
    try:
        with st.spinner("Подготовка на наклона и terrain donor filter…"):
            prepared_terrain = terrain_gate.prepare_terrain(
                run_parse.reference_channels,
                config=terrain_config,
            )
            terrain_result = terrain_gate.apply_terrain_gate(
                result,
                config=terrain_config,
                prepared_terrain=prepared_terrain,
            )
        terrain_state = {
            "signature": terrain_signature,
            "prepared": prepared_terrain,
            "result": terrain_result,
        }
        st.session_state[TERRAIN_STATE_KEY] = terrain_state
    except Exception as exc:
        st.error(f"Terrain gate не завърши: {exc}")
        terrain_state = None

terrain_result = (
    terrain_state.get("result") if isinstance(terrain_state, Mapping) else None
)
if terrain_result is not None:
    terrain_diagnostics = _plain(terrain_result.diagnostics)
    terrain_flags = set(terrain_diagnostics.get("flags", ()) or ())
    if "TERRAIN_GATE_UNAVAILABLE" in terrain_flags:
        st.warning(
            "Terrain gate не е приложен: липсва достатъчно надежден grade или "
            "altitude+distance канал. HRmod candidate е запазен и final не "
            "измисля terrain стойности.",
            icon="⚠️",
        )
    elif "TERRAIN_GATE_DISABLED" in terrain_flags:
        st.info(
            "Terrain gate е изключен. HRmod final е идентичен с HRmod candidate."
        )
    else:
        st.success(
            "Теренният donor филтър е приложен само след непроменения HR-only candidate."
        )
    timeseries_frame, wave_frame = _terrain_display_frames(result, terrain_result)
    wave_table_frame = _ordered_wave_frame(wave_frame)
    _terrain_summary_panel(terrain_result, wave_frame)
    st.caption(
        "Terrain input hash: `"
        + terrain_result.terrain_input_hash[:20]
        + "…` · final result hash: `"
        + terrain_result.final_result_hash[:20]
        + "…` · HR input hash остава `"
        + result.hr_input_hash[:20]
        + "…`."
    )
st.warning(SCIENTIFIC_LIMITATION, icon="⚠️")

zone_display_frame = _terrain_zone_display_frame(result, terrain_result)

result_view = st.radio(
    "Резултатен изглед",
    options=(
        "HR-only сигнали",
        "HR вълни",
        "HR зони",
        "Diagnostics",
        "Reference validation",
        "Downloads",
    ),
    horizontal=True,
    key="hrmod_lab_result_view",
    help="Изгражда се само избраният изглед; готовият HR-only резултат се запазва.",
)

use_webgl = False
if result_view in ("HR-only сигнали", "HR вълни"):
    use_webgl = st.checkbox(
        "WebGL ускорение (само за съвместим браузър)",
        value=False,
        key="hrmod_lab_use_webgl",
        help=(
            "По подразбиране графиката използва съвместим SVG режим. Включете "
            "WebGL само ако браузърът го поддържа; при съобщение 'WebGL is not "
            "supported' оставете тази опция изключена."
        ),
    )

if result_view == "HR-only сигнали":
    show_h_detect_overview = st.checkbox(
        "Покажи h_detect (тънка диагностична линия, не измерен HR)",
        value=False,
        key="hrmod_lab_show_h_detect_overview",
    )
    st.plotly_chart(
        _hr_only_figure(
            timeseries_frame,
            wave_frame,
            athlete_profile,
            show_h_detect=show_h_detect_overview,
            use_webgl=use_webgl,
        ),
        use_container_width=True,
        config={"displaylogo": False},
    )
    with st.expander("Processed HR-only timeseries"):
        st.dataframe(timeseries_frame, use_container_width=True, hide_index=True)

if result_view == "HR вълни":
    st.caption(
        "Вълните са открити само от h_detect. Receiver е целият възход s→p, "
        "а donor е спадът p→e. Следпиковата площ се оглежда към възхода; "
        "маркерите не са механичен work interval."
    )
    if wave_frame.empty:
        st.info("Не са открити HR вълни при текущите параметри.")
    else:
        selector_options = [
            int(value)
            for value in pd.to_numeric(wave_frame["wave_id"], errors="coerce").dropna()
        ]
        selected_wave_id = st.selectbox(
            "Wave selector",
            options=selector_options,
            format_func=lambda value: (
                f"Wave {value} · "
                + " · ".join(
                    str(item)
                    for item in wave_frame.loc[
                        pd.to_numeric(wave_frame["wave_id"], errors="coerce").eq(value),
                        [
                            column
                            for column in (
                                "morphology",
                                "morphology_reason",
                                "correction_strategy",
                                "status",
                            )
                            if column in wave_frame.columns
                        ],
                    ].iloc[0]
                    if pd.notna(item) and str(item).strip()
                )
            ),
        )
        show_h_detect_zoom = st.checkbox(
            "Покажи h_detect в wave zoom",
            value=True,
            key="hrmod_lab_show_h_detect_zoom",
        )
        st.plotly_chart(
            _hr_only_figure(
                timeseries_frame,
                wave_frame,
                athlete_profile,
                show_h_detect=show_h_detect_zoom,
                use_webgl=use_webgl,
                selected_wave_id=int(selected_wave_id),
            ),
            use_container_width=True,
            config={"displaylogo": False},
        )
        selected_wave = wave_table_frame.loc[
            pd.to_numeric(wave_frame["wave_id"], errors="coerce").eq(selected_wave_id)
        ]
        st.markdown("**Избрана вълна — площи и зонова промяна**")
        st.dataframe(selected_wave, use_container_width=True, hide_index=True)
        st.markdown("**Всички вълни**")
        st.dataframe(wave_table_frame, use_container_width=True, hide_index=True)

if result_view == "HR зони":
    st.caption(
        "Raw HR, clean HR, HRmod candidate и HRmod final са класифицирани "
        "преди визуално закръгляване."
    )
    st.caption(
        "Candidate е непромененият HR-only core резултат; final е резултатът "
        "след terrain gate. При изключен или недостъпен gate final = candidate."
    )
    st.dataframe(zone_display_frame, use_container_width=True, hide_index=True)
    if not zone_display_frame.empty and {
        "zone_name",
        "raw_seconds",
        "clean_seconds",
        "hrmod_candidate_seconds",
        "hrmod_final_seconds",
    }.issubset(zone_display_frame.columns):
        zone_plot = go.Figure()
        zone_plot.add_bar(
            x=zone_display_frame["zone_name"],
            y=zone_display_frame["raw_seconds"],
            name="Raw HR",
        )
        zone_plot.add_bar(
            x=zone_display_frame["zone_name"],
            y=zone_display_frame["clean_seconds"],
            name="Clean HR",
        )
        zone_plot.add_bar(
            x=zone_display_frame["zone_name"],
            y=zone_display_frame["hrmod_candidate_seconds"],
            name="HRmod candidate (v4 mirror)",
        )
        zone_plot.add_bar(
            x=zone_display_frame["zone_name"],
            y=zone_display_frame["hrmod_final_seconds"],
            name="HRmod final (downhill donor filter)",
        )
        zone_plot.update_layout(
            barmode="group", yaxis_title="Seconds", height=390
        )
        st.plotly_chart(zone_plot, use_container_width=True)

if result_view == "Diagnostics":
    _diagnostic_panel(result)
    with st.expander("TCX diagnostics за точно този run"):
        st.json(_plain(run_parse.diagnostics))

if result_view == "Reference validation":
    st.warning(
        "Строга граница: този tab работи върху вече готовия core result. "
        "Нито overlay, нито annotation извиква compute_hrmod_hr_only.",
        icon="🔒",
    )
    reference_channels = run_parse.reference_channels
    reference_frame = _reference_samples_frame(reference_channels)
    available = [
        name
        for name in reference_channels.available_channels
        if name in reference_frame.columns
    ]
    st.write(
        "**TCX sport metadata:**",
        reference_channels.sport or "не е зададено",
        " · **налични reference канали:**",
        ", ".join(available) if available else "няма",
    )
    sport_text = (reference_channels.sport or "").lower()
    if "ski" in sport_text:
        st.warning(
            "RAW_SKI_SPEED_CONTEXT_ONLY — raw ski speed е само контекст и не е "
            "автоматична оценка за интензивност или мощност."
        )

    selected_overlays = st.multiselect(
        "Post-hoc overlays (оригинални единици)",
        options=available,
        default=[],
        help="Промяната на този избор само прерисува reference графиката.",
    )
    if selected_overlays:
        st.plotly_chart(
            _reference_overlay_figure(
                timeseries_frame, reference_frame, selected_overlays
            ),
            use_container_width=True,
            config={"displaylogo": False},
        )
    elif available:
        st.info("Изберете канал за post-hoc overlay. Core резултатът остава същият.")
    else:
        st.info(
            "Файлът няма speed/power/grade/cadence reference проби. HRmod е "
            "напълно използваем и без тях."
        )

    st.markdown("#### Laps и ръчни markers — само annotations")
    annotation_upload = st.file_uploader(
        "Optional annotations CSV/JSON",
        type=("csv", "json"),
        key="hrmod_lab_annotation_upload",
    )
    if annotation_upload is not None:
        annotation_hash = hashlib.sha256(annotation_upload.getvalue()).hexdigest()
        if st.session_state.get("hrmod_lab_annotation_import_hash") != annotation_hash:
            try:
                imported_annotations = _read_annotation_upload(annotation_upload)
                st.session_state["hrmod_lab_annotations"] = pd.concat(
                    (base_annotations, imported_annotations), ignore_index=True
                )
                st.session_state["hrmod_lab_annotation_import_hash"] = annotation_hash
            except Exception as exc:
                st.error(f"Annotations import error: {exc}")
    if "hrmod_lab_annotations" not in st.session_state:
        st.session_state["hrmod_lab_annotations"] = base_annotations
    edited_annotations = st.data_editor(
        st.session_state["hrmod_lab_annotations"],
        num_rows="dynamic",
        use_container_width=True,
        hide_index=True,
        key="hrmod_lab_annotation_editor",
    )
    st.session_state["hrmod_lab_annotations"] = edited_annotations

    st.markdown("#### Reference evaluation настройки")
    st.caption(
        "Quantitative режими са explicit opt-in и изискват външен source/зони. "
        "Тези настройки никога не се подават към HRmod core."
    )
    reference_base = st.columns(4)
    reference_sport = reference_base[0].text_input(
        "Sport/context",
        value=reference_channels.sport or "",
        help="Използва се само за допустимата post-hoc интерпретация.",
    )
    join_tolerance_s = reference_base[1].number_input(
        "Timestamp join tolerance (s)", min_value=0.0, value=0.51, step=0.05
    )
    max_lag_s = reference_base[2].number_input(
        "Max lag diagnostic (s)", min_value=0, value=120, step=5
    )
    lag_step_s = reference_base[3].number_input(
        "Lag step (s)", min_value=1, value=1, step=1
    )

    power_col, treadmill_col = st.columns(2)
    with power_col:
        enable_quantitative_power = st.checkbox(
            "Enable quantitative power reference",
            value=False,
            disabled="power_w" not in available,
            help="Изисква известен произход на power и индивидуални power зони.",
        )
        power_source = st.text_input(
            "Power source/provenance",
            value="",
            disabled=not enable_quantitative_power,
        )
        st.caption(
            "Power zones (W) — попълват се изрично; последният upper може да е празен"
        )
        power_zone_frame = st.data_editor(
            pd.DataFrame(
                {
                    "label": ["Z1", "Z2", "Z3", "Z4", "Z5"],
                    "lower": [None, None, None, None, None],
                    "upper": [None, None, None, None, None],
                }
            ),
            num_rows="dynamic",
            use_container_width=True,
            hide_index=True,
            disabled=not enable_quantitative_power,
            key="hrmod_lab_power_zones",
        )
    with treadmill_col:
        enable_controlled_treadmill_speed = st.checkbox(
            "Enable controlled treadmill speed",
            value=False,
            disabled="speed_mps" not in available,
            help="Не е допустимо за raw ski/outdoor speed без контролиран протокол.",
        )
        treadmill_grade_verified = st.checkbox(
            "Treadmill grade е проверен",
            value=False,
            disabled=not enable_controlled_treadmill_speed,
        )
        st.caption(
            "Protocol speed zones (m/s) — попълват се изрично; последният upper може да е празен"
        )
        speed_zone_frame = st.data_editor(
            pd.DataFrame(
                {
                    "label": ["Z1", "Z2", "Z3", "Z4", "Z5"],
                    "lower": [None, None, None, None, None],
                    "upper": [None, None, None, None, None],
                }
            ),
            num_rows="dynamic",
            use_container_width=True,
            hide_index=True,
            disabled=not enable_controlled_treadmill_speed,
            key="hrmod_lab_speed_zones",
        )

    external_columns = st.columns(3)
    external_zone_field_text = external_columns[0].text_input(
        "External zone field (optional)",
        value="",
        help="Име на вече налична reference колона със zone label.",
    )
    use_annotation_zones = external_columns[1].checkbox(
        "Използвай annotation external_zone",
        value=False,
        help="Annotations остават post-hoc и променят само reference summaries.",
    )
    high_zone_labels_text = external_columns[2].text_input(
        "High zone labels", value="Z4,Z5"
    )
    evaluate_clicked = st.button(
        "Изпълни отделна post-hoc reference оценка",
        disabled=reference_frame.empty and edited_annotations.empty,
        use_container_width=True,
    )
    if evaluate_clicked:
        before = _core_fingerprint(result)
        try:
            power_zones = (
                _reference_zones(power_zone_frame, "Power zones")
                if enable_quantitative_power
                else ()
            )
            speed_zones = (
                _reference_zones(speed_zone_frame, "Speed zones")
                if enable_controlled_treadmill_speed
                else ()
            )
            reference_config = ReferenceValidationConfig(
                join_tolerance_s=float(join_tolerance_s),
                sport=reference_sport.strip() or None,
                enable_quantitative_power=bool(enable_quantitative_power),
                power_source=power_source.strip() or None,
                power_zones=power_zones,
                enable_controlled_treadmill_speed=bool(
                    enable_controlled_treadmill_speed
                ),
                treadmill_grade_verified=bool(treadmill_grade_verified),
                speed_zones=speed_zones,
                external_zone_field=external_zone_field_text.strip() or None,
                use_annotation_zones=bool(use_annotation_zones),
                high_zone_labels=tuple(
                    label.strip()
                    for label in high_zone_labels_text.split(",")
                    if label.strip()
                ),
                max_lag_s=int(max_lag_s),
                lag_step_s=int(lag_step_s),
            )
            with st.spinner("Reference timestamp alignment и експертни метрики…"):
                validation = evaluate_against_reference(
                    hrmod_result=result,
                    reference_channels=reference_channels,
                    reference_config=reference_config,
                    optional_annotations=edited_annotations.to_dict(orient="records"),
                )
            after = _core_fingerprint(result)
            if before != after or before != run["core_fingerprint"]:
                raise RuntimeError(
                    "Reference evaluator attempted to mutate the immutable core result."
                )
            st.session_state[REFERENCE_STATE_KEY] = validation
            st.success(
                "Reference evaluation е завършена; core fingerprint-ът е непроменен."
            )
        except Exception as exc:
            st.error(f"Reference evaluation error: {exc}")

    validation = st.session_state.get(REFERENCE_STATE_KEY)
    if validation is not None:
        st.caption(
            "Предварителна експертна оценка — correlation сама по себе си не "
            "валидира модела."
        )
        validation_plain = _plain(validation)
        if isinstance(validation_plain, Mapping):
            metrics = validation_plain.get("metrics")
            flags = validation_plain.get("flags")
            confusion = validation_plain.get("confusion_matrices")
            if metrics:
                st.json(metrics)
            if flags:
                st.warning("Reference flags: " + ", ".join(map(str, flags)))
            if confusion:
                st.dataframe(_records_frame(confusion), use_container_width=True)
        aligned = _validation_aligned_frame(validation)
        if not aligned.empty:
            with st.expander("Reference-aligned comparison"):
                st.dataframe(aligned, use_container_width=True, hide_index=True)
        with st.expander("Пълен reference validation резултат"):
            st.json(validation_plain)

if result_view == "Downloads":
    _download_panel(
        run,
        st.session_state.get("hrmod_lab_annotations"),
        st.session_state.get(REFERENCE_STATE_KEY),
        terrain_result,
    )
