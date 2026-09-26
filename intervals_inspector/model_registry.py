"""Central, privacy-safe metadata registry for the shadow physiology model.

The registry is deliberately independent from Streamlit.  The same records can
therefore feed the pilot UI, diagnostic exports, tests and future generated
documentation without maintaining parallel explanations.
"""

from __future__ import annotations

from copy import deepcopy
from typing import Any, Iterable, Mapping


REGISTRY_VERSION = "shadow-model-registry-v4-bounded-tref"
VISIBLE_ROLES = ("athlete", "coach", "tester", "administrator")
EDITOR_ROLES = ("tester", "administrator")

_SENSITIVE_TERMS = frozenset(
    {
        "access_token",
        "authorization",
        "client_secret",
        "email",
        "oauth",
        "password",
        "refresh_token",
        "secret",
        "token",
    }
)


def _definition(
    item_id: str,
    short_name: str,
    full_name: str,
    description: str,
    formula: str,
    unit: str,
    *,
    interpretation: str,
    inputs: tuple[str, ...] = (),
    dependencies: tuple[str, ...] = (),
    limitations: tuple[str, ...] = (),
    allowed_range: tuple[float, float] | None = None,
    visible_roles: tuple[str, ...] = VISIBLE_ROLES,
    editable_roles: tuple[str, ...] = (),
) -> dict[str, Any]:
    return {
        "id": item_id,
        "short_name": short_name,
        "full_name": full_name,
        "description": description,
        "formula": formula,
        "unit": unit,
        "interpretation": interpretation,
        "inputs": list(inputs),
        "allowed_range": list(allowed_range) if allowed_range else None,
        "dependencies": list(dependencies),
        "limitations": list(limitations),
        "visible": True,
        "editable": bool(editable_roles),
        "visible_roles": list(visible_roles),
        "editable_roles": list(editable_roles),
        "sensitive": False,
        "registry_version": REGISTRY_VERSION,
        "initial_value": None,
        "current_value": None,
        "value_source": None,
        "version": None,
    }


PARAMETER_TEMPLATES: dict[str, dict[str, Any]] = {
    "equivalence_slope_pp_per_bpm": _definition(
        "parameter.{zone}.equivalence_slope_pp_per_bpm",
        "pp/bpm",
        "Линеен коефициент за приравняване",
        "Промяната в стойността на минутата за всеки 1 bpm от референтната граница.",
        "c=clamp(1-s×(HR_high-effective_hr),0,1); Z5: c=1+s×(effective_hr-HR_low)",
        "процентни пункта/удар/мин",
        interpretation="Началната стойност 3.0 означава линейна промяна от 3 процентни пункта за 1 bpm.",
        inputs=("effective_hr", "HR граници"),
        dependencies=("Приравнено време", "spillover", "E_z"),
        limitations=("Параметърът е начална експертна настройка.",),
        allowed_range=(0.0, 100.0),
        editable_roles=EDITOR_ROLES,
    ),
    "spill_threshold_fraction": _definition(
        "parameter.{zone}.spill_threshold_fraction",
        "spill threshold",
        "Праг за активиране на spillover",
        "Дял от Tref, над който приравненото време създава съседен spillover.",
        "X_z = max(0, T_eq,z - threshold_z × Tref_z)",
        "% от Tref",
        interpretation="По-нисък праг активира spillover по-рано; 50% означава праг при половината Tref.",
        inputs=("T_eq,z", "Tref"),
        dependencies=("spillover", "E_z"),
        limitations=("Прагът още не е индивидуално калибриран.",),
        allowed_range=(0.0, 1.0),
        editable_roles=EDITOR_ROLES,
    ),
    "spill_down_fraction": _definition(
        "parameter.{zone}.spill_down_fraction",
        "spill ↓",
        "Процент spillover към непосредствената по-ниска зона",
        "Частта от превишението, добавяна еднократно към по-ниската съседна зона.",
        "Spill(z→z-1) = down_z × X_z",
        "% от превишението",
        interpretation="0% изключва посоката; 20% добавя една пета от превишението.",
        inputs=("X_z",),
        dependencies=("spillover", "E_z"),
        limitations=("Z1 няма по-ниска зона; добавеното не поражда нова cascade или spillover.",),
        allowed_range=(0.0, 1.0),
        editable_roles=EDITOR_ROLES,
    ),
    "spill_up_fraction": _definition(
        "parameter.{zone}.spill_up_fraction",
        "spill ↑",
        "Процент spillover към непосредствената по-висока зона",
        "Частта от превишението, добавяна еднократно към по-високата съседна зона.",
        "Spill(z→z+1) = up_z × X_z",
        "% от превишението",
        interpretation="0% изключва посоката; 10% добавя една десета от превишението.",
        inputs=("X_z",),
        dependencies=("spillover", "E_z"),
        limitations=("Най-високата зона няма по-висока зона; няма вторичен spillover.",),
        allowed_range=(0.0, 1.0),
        editable_roles=EDITOR_ROLES,
    ),
    "tref_min": _definition(
        "parameter.{zone}.tref_min",
        "Tref min",
        "Долна експертна граница на Tref",
        "Минималната допустима стойност на индивидуалния 40-дневен Tref.",
        "Tref_z = clamp(7 × mean(E_z, previous up-to-40 days), min_z, max_z)",
        "приравнени минут",
        interpretation="Границата е фиксирана; Tref вътре в диапазона се определя от реалния E на спортиста.",
        inputs=("предходни до 40 завършени календарни дни с E_z",),
        dependencies=("Tref", "spillover", "recovery"),
        limitations=("Фиксиран научен параметър; само за четене.",),
    ),
    "tref_max": _definition(
        "parameter.{zone}.tref_max",
        "Tref max",
        "Горна експертна граница на Tref",
        "Максималната допустима стойност на индивидуалния 40-дневен Tref.",
        "Tref_z = clamp(7 × mean(E_z, previous up-to-40 days), min_z, max_z)",
        "приравнени минути",
        interpretation="При липса на история тази горна стойност е детерминираният cold-start Tref.",
        inputs=("предходни до 40 завършени календарни дни с E_z",),
        dependencies=("Tref", "spillover", "recovery"),
        limitations=("Фиксиран научен параметър; само за четене.",),
    ),
    "profile_version": _definition(
        "parameter.{zone}.profile_version",
        "profile version",
        "Версия на физиологичния профил",
        "Версията фиксира зоналния модел и HR границите.",
        "—",
        "версия",
        interpretation="Промяна на версията означава промяна на моделната дефиниция и изисква ново сравнение.",
        dependencies=("Приравнено време",),
        limitations=("Само за четене в пилотния интерфейс.",),
    ),
    "equivalence_version": _definition(
        "parameter.{zone}.equivalence_version",
        "equivalence version",
        "Версия на вътрешнозоновото приравняване",
        "Версията фиксира линейната формула и правилата за clamp.",
        "—",
        "версия",
        interpretation="Промяна на версията инвалидира старите кеширани резултати.",
        dependencies=("Приравнено време",),
        limitations=("Само за четене в пилотния интерфейс.",),
    ),
    "tref_profile_version": _definition(
        "parameter.{zone}.tref_profile_version",
        "Tref version",
        "Версия на ограничения 40-дневен Tref профил",
        "Версията фиксира експертните граници, 40-дневния прозорец и cold-start правилото.",
        "—",
        "версия",
        interpretation="Позволява възпроизводимост на историческото изчисление и clamp-а.",
        dependencies=("Tref",),
        limitations=("Само за четене в пилотния интерфейс.",),
    ),
}


RESULT_DEFINITIONS: dict[str, dict[str, Any]] = {
    "result.t": _definition(
        "result.t", "T_z", "Реално време в зона", "Класифицираното надеждно активно време в зоната.",
        "T_z = ∫ I(HR(t) ∈ z) dt", "минути",
        interpretation="Ниска/висока стойност означава по-малко/повече реално време, не непременно по-малък/по-голям физиологичен ефект.",
        inputs=("effective_hr", "HR граници"), dependencies=("T_eq,z",),
        limitations=("Липсващ или некачествен HR намалява покритието и T_z.",),
    ),
    "result.equivalent_time": _definition(
        "result.equivalent_time", "T_eq,z", "Приравнено време", "Единствената директна зонална доза преди cascade и spillover.",
        "T_eq,z = Σ Δt_i × c_z(effective_hr_i)", "приравнени минути",
        interpretation="За Z1–Z4 референтна е горната граница; за Z5 — долната, с clamp до HRmax.",
        inputs=("effective_hr", "линеен pp/bpm параметър"), dependencies=("Tref comparison", "cascade", "spillover", "E_z"),
        limitations=("Липсващ или неизползваем HR не се допълва.",),
    ),
    "result.direct_ratio": _definition(
        "result.direct_ratio", "% от Tref", "Директен дял от Tref", "Отношението на приравненото време към приложимия за деня Tref.",
        "direct_ratio_z = T_eq,z / Tref_z", "дял",
        interpretation="0.50 е директният праг за вторична зона; стойността не включва cascade или входящ spillover.",
        inputs=("T_eq,z", "Tref"), dependencies=("spillover", "диагностично планиране"),
        limitations=("Не е самостоятелна медицинска или readiness оценка.",),
    ),
    "result.cascade": _definition(
        "result.cascade", "cascade", "Каскаден принос от по-високи зони", "100% от приравненото време на всяка по-висока аеробна зона се добавя към по-ниския компонент.",
        "Cascade_z = Σ T_eq,j, j > z", "приравнени минути",
        interpretation="Високата стойност показва значим товар от работа над текущата зона.",
        inputs=("T_eq на по-високите зони",), dependencies=("E_z",),
        limitations=("Spillover не създава вторична cascade.",),
    ),
    "result.spillover": _definition(
        "result.spillover", "spillover", "Двупосочен съседен spillover", "Еднократният входящ принос от непосредствените съседни зони върху превишението над техния праг.",
        "X=max(0,T_eq-threshold×Tref); Spill=rate×X", "приравнени минути",
        interpretation="Нула означава, че няма съседно превишение; по-висока стойност означава по-силен надпрагов съседен стимул.",
        inputs=("T_eq,z", "Tref", "spill rates"), dependencies=("E_z",),
        limitations=("Не намалява T_eq и не поражда рекурсивен spillover.",),
    ),
    "result.e": _definition(
        "result.e", "E_z", "Краен компонентен ефект", "Сборът от приравненото време, каскадния принос и входящия spillover.",
        "E_z = T_eq,z + Cascade_z + Spill_received,z", "приравнени минути",
        interpretation="По-висок E означава по-голям моделиран компонентен стимул, а не медицинска оценка.",
        inputs=("T_eq,z", "cascade", "spillover"), dependencies=("7/40", "recovery"),
        limitations=("Не включва readiness, wellness или recovery модификатори в shadow пилота.",),
    ),
    "result.h40": _definition(
        "result.h40", "H40", "40-дневна база", "Седмичният еквивалент на средния дневен ефективен E за предходните до 40 завършени календарни дни.",
        "H_N,z = (7/N) × Σ E_day,z", "приравнени минути/седмица",
        interpretation="H40 е raw Tref преди прилагане на фиксираните експертни граници.",
        inputs=("предходен дневен E_z",), dependencies=("7/40", "Tref"),
        limitations=("Текущият и бъдещите дни са изключени; почивните дни са T_eq=0.",),
    ),
    "result.tref_effective": _definition(
        "result.tref_effective", "Tref", "Ограничен 40-дневен Tref", "Реалната персонална 40-дневна референция, ограничена в фиксираните експертни граници на зоната.",
        "Tref_z = clamp(7 × mean(E_z, previous up-to-40 days), min_z, max_z)", "приравнени минути",
        interpretation="При непълна история стойността е временна; без нито един ден се използва горната експертна стойност.",
        inputs=("дневен E_z", "Tref min/max"), dependencies=("spillover", "recovery"),
        limitations=("Текущият ден е изключен от собствения си causal Tref.",),
    ),
    "result.hr_coverage": _definition(
        "result.hr_coverage", "HR coverage", "Покритие на активното време с валиден HR", "Делът от активното време, който може надеждно да се класифицира.",
        "coverage = classified HR time / active time × 100", "%",
        interpretation="Под 80% е предупреждение; 80–95% е частично; над 95% е добро диагностично покритие.",
        inputs=("валидни raw HR интервали", "активно време"), dependencies=("T_z", "T_eq,z", "E_z"),
        limitations=("Високото покритие не гарантира правилни HR граници или точен сензор.",),
    ),
    "result.average_minute_value": _definition(
        "result.average_minute_value", "Средна стойност на минутата", "Средна стойност на минутата", "Съотношението между приравненото и реалното време в зоната.",
        "P_avg,z = 100 × T_eq,z / T_real,z", "%",
        interpretation="100% е референтната стойност на една минута; резултатът е null при липса на реално време.",
        inputs=("T_eq,z", "T_real,z"), dependencies=("зонална диагностика",),
        limitations=("Не се определя, когато T_real,z е нула.",),
    ),
    "result.mean_effective_hr": _definition(
        "result.mean_effective_hr", "Среден HR", "Времево претеглен среден effective HR", "Средният effective_hr според реалната продължителност на интервалите.",
        "mean_hr_z = Σ(Δt_i × mean(effective_hr_i))/T_real,z", "bpm",
        interpretation="Irregular и smart-recorded интервалите участват с действителната си продължителност.",
        inputs=("effective_hr", "Δt"), dependencies=("зонална диагностика",),
        limitations=("Не се определя при липса на реално време в зоната.",),
    ),
    "result.zone_share": _definition(
        "result.zone_share", "% от T", "Дял от класифицираното HR време", "Процентът от надеждно класифицираното време, прекаран в конкретната зона.",
        "share_z = T_z / ΣT × 100", "%",
        interpretation="Нисък/висок дял означава малка/голяма част от класифицираното време, но не измерва самостоятелно интензивността.",
        inputs=("T_z", "общо класифицирано HR време"), dependencies=("разпределение по зони",),
        limitations=("При ниско HR покритие процентът описва само наличната класифицирана част.",),
    ),
    "result.active_duration": _definition(
        "result.active_duration", "активно време", "Активно interval-aware време", "Общата продължителност на интервалите, приети като активни преди HR класификацията.",
        "active = Σ dt на активните интервали", "секунди",
        interpretation="Служи като знаменател за HR coverage; не всеки активен интервал задължително има валиден HR.",
        inputs=("нормализирани времеви интервали",), dependencies=("HR coverage",),
        limitations=("Зависи от правилата за stops, pauses и gaps.",),
    ),
    "result.classified_hr": _definition(
        "result.classified_hr", "класифицирано HR време", "Активно време с валиден и зонално класифициран HR", "Частта от активното време, която участва в T_z и T_eq,z.",
        "classified = Σ_z T_z", "секунди",
        interpretation="Колкото е по-близо до активното време, толкова по-пълно е покритието за зоналния модел.",
        inputs=("effective_hr", "профилни HR граници"), dependencies=("T_z", "T_eq,z", "HR coverage"),
        limitations=("Не включва HR извън профилните граници и невалидни точки.",),
    ),
    "result.unclassified_hr": _definition(
        "result.unclassified_hr", "неопределено HR време", "Активно време без надеждна HR класификация", "Активните секунди, които не могат да участват в зоналните резултати.",
        "unclassified = active - classified", "секунди",
        interpretation="Нула е желателно; висока стойност понижава надеждността и вероятно подценява товара.",
        inputs=("активно време", "класифицирано HR време"), dependencies=("HR coverage", "предупреждения"),
        limitations=("Не се допълва с измислени HR стойности.",),
    ),
    "result.excluded_duration": _definition(
        "result.excluded_duration", "изключено време", "Време, изключено от модела", "Продължителността на stops, pauses, несигурни gaps и други неприети класификации.",
        "excluded = Σ dt на неактивните/несигурните интервали", "секунди",
        interpretation="Високата стойност изисква проверка на recording качеството, но не се приема автоматично за тренировка или почивка.",
        inputs=("interval classifications",), dependencies=("активно време", "T_z", "T_eq,z"),
        limitations=("Причината трябва да се тълкува заедно с classification breakdown.",),
    ),
    "model.profile_fingerprint": _definition(
        "model.profile_fingerprint", "profile fingerprint", "Отпечатък на използвания физиологичен профил", "SHA-256 отпечатък на каноничните, нечувствителни профилни параметри за възпроизводимост.",
        "fingerprint = SHA-256(canonical profile metadata)", "hex SHA-256",
        interpretation="Еднакъв отпечатък означава еднаква канонична профилна конфигурация; различен означава промяна.",
        inputs=("HR граници", "pp/bpm", "HRmax"), dependencies=("stale-result проверка",),
        limitations=("Не е token, идентификатор на спортист или доказателство за научна валидност.",),
    ),
}


WARNING_DEFINITIONS: dict[str, dict[str, Any]] = {
    "warning.experimental": _definition(
        "warning.experimental", "Експериментална конфигурация", "Активна временна експериментална конфигурация",
        "Поне една разрешена настройка се различава от началната; резултатът е само shadow сравнение.", "—", "статус",
        interpretation="Не използвайте резултата като реален тренировъчен план.", limitations=("Не се записва и се губи с края на сесията.",),
    ),
    "warning.incomplete_history": _definition(
        "warning.incomplete_history", "Непълна история", "Непълен диагностичен H40 прозорец",
        "Налични са по-малко от 40 завършени календарни дни.", "history_days < 40", "дни",
        interpretation="Изчислява се временен bounded Tref от всички налични завършени календарни дни.", dependencies=("H40", "7/40", "Tref"),
        limitations=("Почивен ден е T_eq=0 само когато календарният прозорец е зареден успешно.",),
    ),
    "warning.low_hr_coverage": _definition(
        "warning.low_hr_coverage", "Ниско HR покритие", "Недостатъчно покритие с валиден HR",
        "Част от активното време не участва в реалното и приравненото време.", "coverage < 80%", "%",
        interpretation="Резултатите вероятно подценяват реалния зонален товар.", dependencies=("T_z", "T_eq,z", "E_z"),
        limitations=("Моделът не измисля липсващи HR точки.",),
    ),
}


def is_sensitive_identifier(value: str) -> bool:
    """Return True for identifiers that must never enter the registry/export."""

    lowered = str(value).lower().replace("-", "_")
    return any(term in lowered for term in _SENSITIVE_TERMS)


def parameter_definition(
    zone: str,
    field: str,
    *,
    initial_value: Any,
    current_value: Any,
    value_source: str,
    version: str,
) -> dict[str, Any]:
    if field not in PARAMETER_TEMPLATES:
        raise KeyError(f"unknown parameter field: {field}")
    definition = deepcopy(PARAMETER_TEMPLATES[field])
    definition["id"] = definition["id"].format(zone=zone)
    definition["initial_value"] = initial_value
    definition["current_value"] = current_value
    definition["value_source"] = value_source
    definition["version"] = version
    return definition


def explanation_text(definition: Mapping[str, Any]) -> str:
    """Render one complete, mobile-friendly explanation from registry data."""

    sections = [
        f"**{definition.get('full_name', definition.get('short_name', 'Показател'))}**",
        str(definition.get("description") or ""),
        f"**Формула:** `{definition.get('formula') or '—'}`",
        f"**Мерна единица:** {definition.get('unit') or '—'}",
        f"**Тълкуване:** {definition.get('interpretation') or '—'}",
    ]
    inputs = definition.get("inputs") or []
    if inputs:
        sections.append("**Входни данни:** " + ", ".join(map(str, inputs)))
    dependencies = definition.get("dependencies") or []
    if dependencies:
        sections.append("**Зависят от него:** " + ", ".join(map(str, dependencies)))
    limitations = definition.get("limitations") or []
    if limitations:
        sections.append("**Ограничения/липсващи данни:** " + " ".join(map(str, limitations)))
    if definition.get("version"):
        sections.append(f"**Версия:** {definition['version']}")
    return "\n\n".join(section for section in sections if section)


def validate_registry_items(
    item_ids: Iterable[str], registry: Mapping[str, Mapping[str, Any]]
) -> None:
    """Fail when a displayed item has no complete, non-sensitive definition."""

    for item_id in item_ids:
        if is_sensitive_identifier(item_id):
            raise ValueError(f"sensitive identifier is not displayable: {item_id}")
        definition = registry.get(item_id)
        if not definition:
            raise ValueError(f"missing registry definition: {item_id}")
        required = ("id", "short_name", "full_name", "description", "formula", "unit", "version")
        missing = [key for key in required if definition.get(key) in (None, "")]
        if missing:
            raise ValueError(f"incomplete registry definition {item_id}: {missing}")
        if definition.get("sensitive"):
            raise ValueError(f"sensitive definition is not displayable: {item_id}")


def safe_registry_rows(
    registry: Mapping[str, Mapping[str, Any]], *, role: str = "tester"
) -> list[dict[str, Any]]:
    """Return role-filtered metadata suitable for a diagnostic export."""

    rows: list[dict[str, Any]] = []
    for item_id, raw in registry.items():
        if is_sensitive_identifier(item_id) or raw.get("sensitive"):
            continue
        visible_roles = set(raw.get("visible_roles") or [])
        if not raw.get("visible", True) or role not in visible_roles:
            continue
        rows.append(deepcopy(dict(raw)))
    return sorted(rows, key=lambda row: str(row["id"]))
