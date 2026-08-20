"""Standalone Streamlit entry point for empirical Vflat calibration.

Run from the repository root with::

    python -m streamlit run vflat_lab_app.py

The laboratory is intentionally isolated from the production application.
"""

from __future__ import annotations

from dataclasses import fields
import json
from typing import Any

import numpy as np
import pandas as pd
import streamlit as st

from vflat_lab import (
    VFlatConfig,
    activity_summary,
    apply_vflat_model,
    parse_tcx,
    prepare_activity,
    recommended_work_laps,
    segment_timeseries,
)
from vflat_lab.plotting import activity_figure, grade_scatter_figure


APP_TITLE = "Vflat Lab · емпирична калибрация"
MODEL_VERSION = "vflat-empirical-lab-v1"
DEFAULTS = VFlatConfig()


HELP = {
    "altitude_smoothing_m": "Пространственият прозорец, с който се изглажда надморската височина преди изчисляване на наклона. По-голяма стойност намалява барометричния шум, но заглажда късите баири.",
    "speed_smoothing_s": "Времевият прозорец за скоростта и ускорението. При Garmin Smart Recording 15 s е началният безопасен компромис.",
    "output_smoothing_s": "Центрирана медиана върху крайния Vflat. Потиска единични пикове, но прекалено голяма стойност може да скрие истински ускорявания.",
    "segment_s": "Продължителност на изходните сегменти. Моделът се изчислява на 1 Hz, след което се обобщава по този интервал.",
    "max_gap_s": "Прекъсвания над този праг разделят активността на отделни блокове и никога не се интерполират.",
    "flat_band_pct": "Диапазонът от − тази стойност до + тази стойност се приема за равен терен и не получава наклонова корекция.",
    "uphill_amplitude": "Максималната сила на стационарната корекция. По-висока стойност увеличава Vflat при стръмни изкачвания. Насищащата функция не позволява безкраен ръст.",
    "uphill_scale_pct": "Определя при какъв наклон кривата започва видимо да се насища. По-голяма стойност измества насищането към по-стръмни изкачвания.",
    "uphill_shape": "Формата на кривата между равния терен и насищането. Стойност над 1 прави началото по-плавно и средните наклони по-различими.",
    "negative_grade_gain": "Мека корекция само за наклони между равния диапазон и долната валидна граница. По-стръмните спускания се изключват.",
    "acceleration_gain": "Колко km/h Vflat се добавят за 1 m/s² положително ускорение. Представя усилието, вложено в набиране на скорост.",
    "deceleration_gain": "Колко km/h Vflat се отнемат за 1 m/s² отрицателно ускорение. Намалява инерционно завишената скорост.",
    "descent_threshold_pct": "Под този наклон се натрупва памет за спускане. Самото спускане обичайно е извън валидния Vflat диапазон.",
    "descent_full_effect_pct": "При този или по-стръмен наклон паметта получава максимален вход. Междинните наклони се мащабират плавно.",
    "descent_memory_s": "Времева константа на затихване след спускане. По-голяма стойност запазва корекцията по-дълго след превала.",
    "descent_memory_strength_kmh": "Максималната скорост, която пълната памет след спускане може да отнеме от Vflat.",
    "climb_threshold_pct": "Над този наклон се натрупва памет за продължително изкачване.",
    "climb_full_effect_pct": "При този наклон паметта от изкачване получава максимален вход.",
    "climb_memory_s": "Времева константа на затихване след изкачване. Компенсира временно ниската входна скорост след превала.",
    "climb_memory_strength_kmh": "Максималната скорост, която пълната памет след изкачване може да добави към Vflat.",
    "transition_reference_s": "Прозорец за устойчивата локална референтна скорост около прехода. Използва и минали, и следващи данни, защото активността се обработва след завършване.",
    "transition_anchor_strength": "Каква част от преходното отклонение да се приближи към локалната референция. Нула изключва слоя; единица го прилага изцяло само когато преходното тегло е 1.",
    "transition_accel_scale_mps2": "Ускорението, при което преходното тегло достига 1. По-малка стойност активира локалната стабилизация при по-слаби промени на скоростта.",
    "min_grade_pct": "Долната граница за валидни сегменти. По-стръмните спускания остават видими, но не участват в диагностиката.",
    "max_grade_pct": "Горната оперативна граница на модела. Стойности над нея са видими, но се изключват от диагностиката.",
    "min_speed_kmh": "Минимална скорост за валиден Vflat. Предпазва от стартове, спирания и GPS шум.",
    "turn_threshold_deg": "Промяна на посоката за приблизително 6 s, над която участъкът се маркира като остър завой.",
    "min_segment_coverage": "Минималният дял валидни секунди, необходим, за да участва целият сегмент в метриките.",
}


st.set_page_config(
    page_title=APP_TITLE,
    page_icon="⛷️",
    layout="wide",
    initial_sidebar_state="expanded",
)


def _state_key(name: str) -> str:
    return f"vflat_{name}"


def _initialise_parameters() -> None:
    for item in fields(DEFAULTS):
        st.session_state.setdefault(_state_key(item.name), getattr(DEFAULTS, item.name))


def _reset_parameters() -> None:
    for item in fields(DEFAULTS):
        st.session_state[_state_key(item.name)] = getattr(DEFAULTS, item.name)


def _number(
    label: str,
    name: str,
    minimum: float | int,
    maximum: float | int,
    step: float | int,
    *,
    integer: bool = False,
) -> None:
    kwargs: dict[str, Any] = {
        "label": label,
        "min_value": minimum,
        "max_value": maximum,
        "step": step,
        "key": _state_key(name),
        "help": HELP[name],
    }
    if not integer:
        kwargs["format"] = "%.3f" if step < 0.1 else "%.2f"
    st.number_input(**kwargs)


def parameter_form() -> VFlatConfig:
    with st.sidebar.form("vflat_parameters"):
        st.subheader("Параметри на модела")
        st.caption("Промените се прилагат за всички качени активности след натискане на бутона.")
        with st.expander("1 · Стационарна функция", expanded=True):
            _number("Равен диапазон ± (%)", "flat_band_pct", 0.5, 1.5, 0.1)
            _number("Максимална амплитуда A", "uphill_amplitude", 0.10, 1.80, 0.01)
            _number("Мащаб на наклона B (%)", "uphill_scale_pct", 1.0, 20.0, 0.1)
            _number("Форма p", "uphill_shape", 0.40, 3.00, 0.05)
            _number("Корекция −3% до равния диапазон", "negative_grade_gain", 0.000, 0.250, 0.005)

        with st.expander("2 · Ускорение и забавяне", expanded=True):
            _number("Коефициент при ускоряване", "acceleration_gain", 0.0, 25.0, 0.5)
            _number("Коефициент при забавяне", "deceleration_gain", 0.0, 40.0, 0.5)

        with st.expander("3 · Памет след спускане", expanded=True):
            _number("Праг на спускане (%)", "descent_threshold_pct", -6.0, -1.5, 0.1)
            _number("Пълен ефект при (%)", "descent_full_effect_pct", -15.0, -4.0, 0.5)
            _number("Затихване след спускане (s)", "descent_memory_s", 2.0, 60.0, 1.0)
            _number("Максимално отнемане (km/h)", "descent_memory_strength_kmh", 0.0, 35.0, 0.5)

        with st.expander("4 · Памет след изкачване", expanded=False):
            _number("Праг на изкачване (%)", "climb_threshold_pct", 2.0, 10.0, 0.5)
            _number("Пълен ефект при (%)", "climb_full_effect_pct", 5.0, 18.0, 0.5)
            _number("Затихване след изкачване (s)", "climb_memory_s", 2.0, 60.0, 1.0)
            _number("Максимално добавяне (km/h)", "climb_memory_strength_kmh", 0.0, 15.0, 0.5)

        with st.expander("5 · Локална стабилизация на преходите", expanded=False):
            _number("Локален референтен прозорец (s)", "transition_reference_s", 21, 181, 2, integer=True)
            _number("Сила на привличане към референцията", "transition_anchor_strength", 0.00, 1.00, 0.05)
            _number("Пълно тегло при ускорение (m/s²)", "transition_accel_scale_mps2", 0.05, 0.80, 0.05)

        with st.expander("6 · Сигнали и валидност", expanded=False):
            _number("Изглаждане на височината (m)", "altitude_smoothing_m", 25, 151, 2, integer=True)
            _number("Изглаждане на скоростта (s)", "speed_smoothing_s", 5, 61, 2, integer=True)
            _number("Финално медианно изглаждане (s)", "output_smoothing_s", 1, 61, 2, integer=True)
            _number("Дължина на сегмента (s)", "segment_s", 5, 60, 1, integer=True)
            _number("Максимална пауза за интерполация (s)", "max_gap_s", 2, 20, 1, integer=True)
            _number("Минимален наклон (%)", "min_grade_pct", -5.0, -1.0, 0.1)
            _number("Максимален наклон (%)", "max_grade_pct", 6.0, 20.0, 0.5)
            _number("Минимална скорост (km/h)", "min_speed_kmh", 0.0, 15.0, 0.5)
            _number("Праг за остър завой (°)", "turn_threshold_deg", 25.0, 120.0, 5.0)
            _number("Минимално покритие на сегмент", "min_segment_coverage", 0.20, 1.00, 0.05)
        st.form_submit_button("Приложи параметрите", type="primary", width="stretch")

    values = {item.name: st.session_state[_state_key(item.name)] for item in fields(DEFAULTS)}
    integer_names = {"altitude_smoothing_m", "speed_smoothing_s", "output_smoothing_s", "segment_s", "max_gap_s", "transition_reference_s"}
    for name in integer_names:
        values[name] = int(values[name])
    return VFlatConfig(**values)


@st.cache_data(show_spinner=False)
def process_upload(data: bytes, filename: str, config_values: dict[str, Any]) -> tuple[dict[str, object], pd.DataFrame, pd.DataFrame, dict[str, object]]:
    config = VFlatConfig(**config_values)
    parsed = parse_tcx(data, filename=filename)
    prepared = prepare_activity(parsed, config)
    modelled = apply_vflat_model(prepared, config)
    segments = segment_timeseries(modelled, config)
    suggested_laps = recommended_work_laps(modelled)
    calibration_segments = segments[segments.lap.isin(suggested_laps)] if suggested_laps else segments
    summary = activity_summary(calibration_segments)
    summary["recommended_laps"] = ", ".join(str(value + 1) for value in suggested_laps)
    return parsed.metadata, modelled, segments, summary


def _json_bytes(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False).encode("utf-8")


def _clean_json(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): _clean_json(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_clean_json(item) for item in value]
    if isinstance(value, (np.floating, float)):
        return float(value) if np.isfinite(value) else None
    if isinstance(value, np.integer):
        return int(value)
    if isinstance(value, pd.Timestamp):
        return value.isoformat()
    return value


_initialise_parameters()
st.sidebar.title("Vflat Lab")
st.sidebar.caption(f"Модел: `{MODEL_VERSION}`")
if st.sidebar.button("Върни началните параметри", width="stretch"):
    _reset_parameters()
    st.rerun()
config = parameter_form()

st.title("Емпирична лаборатория за Vflat")
st.caption("Изолирана тестова среда. Не променя основното приложение и не записва TCX файловете.")
st.info(
    "Целта е практически стабилна еквивалентна скорост на равно. Настройваме параметрите върху контролирани и повторяеми активности, а отделни записи оставяме за проверка. Не ограничаваме изкуствено Vflat до фиксиран диапазон."
)

uploads = st.file_uploader(
    "Качи един или повече TCX файла",
    type=["tcx"],
    accept_multiple_files=True,
    help="Файловете се обработват в текущата сесия. Garmin Smart Recording се ресемплира до 1 Hz само вътре в непрекъснати блокове; големите паузи не се запълват.",
)

with st.expander("Уравнение и логика на модела", expanded=False):
    st.latex(r"R(g)=\exp\{-A[1-\exp(-((g-g_0)/B)^p)]\}")
    st.latex(r"V_{stat}=v/R(g)")
    st.latex(r"V_{raw}=V_{stat}+K_+\max(a,0)-K_-\max(-a,0)-C_dD_t+C_uU_t")
    st.latex(r"V_{flat}=V_{raw}+k_Tw_T(V_{local}-V_{raw})")
    st.markdown(
        "`Dₜ` и `Uₜ` са ограничени между 0 и 1 състояния с експоненциално затихване. "
        "Първото се активира след спускане и отнема инерционно завишена скорост; второто се активира след изкачване и компенсира временно ниска входна скорост. "
        "Последният слой се включва пропорционално само около преходи и приближава кандидата към устойчива локална медиана."
    )

if not uploads:
    st.subheader("Препоръчана последователност")
    st.markdown(
        "1. Качи първо равномерни Z3 тренировки с повторни обиколки.\n"
        "2. Настрой стационарните параметри при минимални ускорителни и memory коефициенти.\n"
        "3. Фиксирай стационарната функция и настрой преходите.\n"
        "4. Провери готовите параметри върху контролно или състезание, което не е използвано при настройването."
    )
    st.stop()

results: dict[str, tuple[dict[str, object], pd.DataFrame, pd.DataFrame, dict[str, object]]] = {}
errors: list[str] = []
with st.spinner("Изчисляване на Vflat…"):
    for upload in uploads:
        try:
            results[upload.name] = process_upload(upload.getvalue(), upload.name, config.to_dict())
        except Exception as error:  # individual malformed uploads must not hide valid files
            errors.append(f"{upload.name}: {error}")

for error in errors:
    st.error(error)
if not results:
    st.stop()

summary_frame = pd.DataFrame([item[3] for item in results.values()])
display_summary = summary_frame.rename(
    columns={
        "filename": "Активност",
        "segments": "Валидни сегменти",
        "stationary_median_kmh": "Медиана стационарен",
        "stationary_central90_width_kmh": "90% диапазон стационарен",
        "final_median_kmh": "Медиана финален",
        "final_central90_width_kmh": "90% диапазон финален",
        "final_grade_corr": "Корелация с наклон",
        "final_accel_corr": "Корелация с ускорение",
        "target_5kmh_met": "Цел ≤5 km/h",
    }
)
summary_columns = [
    "Активност",
    "Валидни сегменти",
    "Медиана стационарен",
    "90% диапазон стационарен",
    "Медиана финален",
    "90% диапазон финален",
    "Корелация с наклон",
    "Корелация с ускорение",
    "Цел ≤5 km/h",
]
st.subheader("Сравнение на активностите")
st.dataframe(display_summary.loc[:, summary_columns], hide_index=True, width="stretch")

selected = st.selectbox(
    "Активност за детайлен преглед",
    list(results),
    help="Параметрите остават общи за всички файлове. Изборът променя само визуализираната активност.",
)
metadata, timeseries, segments, summary = results[selected]
available_laps = sorted(segments.lap.dropna().astype(int).unique().tolist())
suggested_laps = recommended_work_laps(timeseries)
selected_laps = st.multiselect(
    "Обиколки, включени в диагностиката",
    available_laps,
    default=[lap for lap in suggested_laps if lap in available_laps],
    format_func=lambda lap: f"Обиколка {lap + 1}",
    help="Автоматичното предложение търси повторни работни обиколки с продължителност 3–15 min и медианна скорост поне 10 km/h. Изборът е само диагностичен — не променя формулата.",
    key=f"vflat_selected_laps_{selected}",
)
if not selected_laps:
    st.warning("Избери поне една обиколка за диагностиката.")
    st.stop()
selected_segments = segments[segments.lap.isin(selected_laps)].copy() if selected_laps else segments.iloc[0:0].copy()
summary = activity_summary(selected_segments)

metric_columns = st.columns(5)
metric_columns[0].metric("Валидни сегменти", int(summary["segments"]), help="Сегменти, покрили всички филтри и минималното изискване за валидни секунди.")
metric_columns[1].metric("Медиана Vflat", f"{summary['final_median_kmh']:.1f} km/h", help="Медианата е устойчива централна стойност и не се влияе силно от единични пикове.")
metric_columns[2].metric("Централен 90% диапазон", f"{summary['final_central90_width_kmh']:.1f} km/h", help="Разлика между 95-и и 5-и персентил. За обозначена равномерна Z3 работа работната цел е около 5 km/h.")
metric_columns[3].metric("Vflat ↔ наклон", f"{summary['final_grade_corr']:.2f}", help="Остатъчната линейна връзка с наклона. При добър модел и равномерно усилие трябва да е близо до нула.")
metric_columns[4].metric("Vflat ↔ ускорение", f"{summary['final_accel_corr']:.2f}", help="Остатъчната линейна връзка с ускорението. Използва се за настройване на динамичните коефициенти.")

if float(summary["final_central90_width_kmh"]) <= 5.0:
    st.success("Работната цел ≤5 km/h за централните 90% е изпълнена. Провери дали истинските ускорявания остават видими и валидирай върху отделен файл.")
else:
    st.warning("Централният 90% диапазон остава над 5 km/h. Това е диагностичен сигнал за допълнителна настройка, а не основание за механично ограничаване на резултата.")

tab_activity, tab_scatter, tab_segments, tab_export, tab_method = st.tabs(
    ["Времева динамика", "Наклон и остатък", "Сегменти", "Експорт", "Методика"]
)
with tab_activity:
    st.plotly_chart(activity_figure(timeseries, selected_segments), width="stretch", config={"displaylogo": False})
    st.caption("Прекъсванията в линиите са изключени сегменти, а не интерполирани стойности.")
with tab_scatter:
    st.plotly_chart(grade_scatter_figure(selected_segments), width="stretch", config={"displaylogo": False})
    st.caption("При равномерно усилие финалните оранжеви точки трябва да загубят системната си зависимост от наклона, без да се свиват изкуствено в една линия.")
with tab_segments:
    segment_display = selected_segments.copy()
    segment_display["start_time"] = pd.to_datetime(segment_display.start_time).dt.strftime("%H:%M:%S")
    st.dataframe(segment_display, hide_index=True, width="stretch", height=520)
with tab_export:
    valid_export = selected_segments[selected_segments.segment_valid].copy()
    st.download_button(
        "Изтегли сегментите CSV",
        valid_export.to_csv(index=False).encode("utf-8-sig"),
        file_name=f"{selected.rsplit('.', 1)[0]}_vflat_segments.csv",
        mime="text/csv",
        width="stretch",
    )
    run_manifest = _clean_json(
        {
            "model_version": MODEL_VERSION,
            "activity": metadata,
            "config": config.to_dict(),
            "summary": summary,
        }
    )
    st.download_button(
        "Изтегли параметрите и метриките JSON",
        _json_bytes(run_manifest),
        file_name=f"{selected.rsplit('.', 1)[0]}_vflat_run.json",
        mime="application/json",
        width="stretch",
    )
with tab_method:
    st.markdown(
        "### Какво приемаме за успех\n"
        "- настройваме върху предварително обозначени равномерни тренировки;\n"
        "- използваме централния 90% диапазон, а не абсолютния минимум и максимум;\n"
        "- следим остатъчната зависимост от наклон, ускорение и позиция по трасето;\n"
        "- не използваме пулса като директна цел заради закъснението и дрейфа му;\n"
        "- запазваме отделни активности и спортисти за проверка извън настройването;\n"
        "- не прехвърляме модела в onFlows, докато общ preset не работи върху независими файлове."
    )
    st.json(_clean_json({"model_version": MODEL_VERSION, "config": config.to_dict(), "metadata": metadata}))
