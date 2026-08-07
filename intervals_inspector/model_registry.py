"""Central, privacy-safe metadata registry for the shadow physiology model.

The registry is deliberately independent from Streamlit.  The same records can
therefore feed the pilot UI, diagnostic exports, tests and future generated
documentation without maintaining parallel explanations.
"""

from __future__ import annotations

from copy import deepcopy
from typing import Any, Iterable, Mapping


REGISTRY_VERSION = "shadow-model-registry-v1"
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
    "weight_low": _definition(
        "parameter.{zone}.weight_low",
        "W_low",
        "Долна вътрешнозонова тежест",
        "Тежестта в долната граница на зоната и знаменателят на k.",
        "W = W_low + (W_high - W_low) × u^p; k = W / W_low",
        "относителна тежест",
        interpretation="По-високата стойност сама по себе си не означава по-голям Q; важна е пропорцията W_high/W_low.",
        inputs=("W_high", "u", "p"),
        dependencies=("Q_z",),
        limitations=("Не е физиологично калибрирана абсолютна мощност.",),
        allowed_range=(1.0, 1000.0),
        editable_roles=EDITOR_ROLES,
    ),
    "weight_high": _definition(
        "parameter.{zone}.weight_high",
        "W_high",
        "Горна вътрешнозонова тежест",
        "Тежестта в горната граница на зоната.",
        "W = W_low + (W_high - W_low) × u^p",
        "относителна тежест",
        interpretation="По-висока стойност увеличава Q за време, прекарано във високата част на зоната.",
        inputs=("W_low", "u", "p"),
        dependencies=("Q_z", "cascade", "spillover", "E_z"),
        limitations=("Профилът е моделна хипотеза, а не директно физиологично измерване.",),
        allowed_range=(1.0, 2000.0),
        editable_roles=EDITOR_ROLES,
    ),
    "power": _definition(
        "parameter.{zone}.power",
        "p",
        "Степенен коефициент на вътрешнозоновата крива",
        "Определя формата на нарастване от W_low към W_high.",
        "W = W_low + (W_high - W_low) × u^p",
        "без единица",
        interpretation="p < 1 усилва по-рано; p = 1 е линейно; p > 1 концентрира усилването към горната част на зоната.",
        inputs=("HR", "HR_low", "HR_high"),
        dependencies=("Q_z",),
        limitations=("Чувствителен е към неточни HR граници и артефакти в пулса.",),
        allowed_range=(0.2, 4.0),
        editable_roles=EDITOR_ROLES,
    ),
    "spill_threshold_fraction": _definition(
        "parameter.{zone}.spill_threshold_fraction",
        "spill threshold",
        "Праг за активиране на spillover",
        "Дял от ефективния Tref, над който директният Q създава съседен spillover.",
        "X_z = max(0, Q_z - threshold_z × Tref_effective,z)",
        "% от Tref",
        interpretation="По-нисък праг активира spillover по-рано; 50% означава праг при половината Tref.",
        inputs=("Q_z", "tref_effective"),
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
        "tref_min",
        "Долна граница на Tref",
        "Минималната допустима седмична референция след прилагане на коефициента на границите.",
        "min_effective,z = bounds_factor_z × tref_min,z",
        "еквивалентни минути/седмица",
        interpretation="Стойност под суровия Tref повдига използваната референция до долната граница.",
        dependencies=("tref_effective", "spillover"),
        limitations=("Пилотният профил е защитна, не окончателна физиологична калибрация.",),
        allowed_range=(0.0, 10080.0),
        editable_roles=EDITOR_ROLES,
    ),
    "tref_max": _definition(
        "parameter.{zone}.tref_max",
        "tref_max",
        "Горна граница на Tref",
        "Максималната допустима седмична референция след прилагане на коефициента на границите.",
        "max_effective,z = bounds_factor_z × tref_max,z",
        "еквивалентни минути/седмица",
        interpretation="Стойност над суровия Tref ограничава използваната референция до горната граница.",
        dependencies=("tref_effective", "spillover"),
        limitations=("Пилотният профил е защитна, не окончателна физиологична калибрация.",),
        allowed_range=(0.0, 10080.0),
        editable_roles=EDITOR_ROLES,
    ),
    "bounds_factor": _definition(
        "parameter.{zone}.bounds_factor",
        "c_z",
        "Коефициент на Tref границите по зона",
        "Умножава само физиологичните граници, без да променя записаната история.",
        "bounds_effective,z = c_z × bounds_base,z",
        "без единица",
        interpretation="1.0 запазва профилните граници; под 1 ги свива, над 1 ги повишава.",
        dependencies=("tref_effective",),
        limitations=("Не трябва да се тълкува като промяна на реалния 40-дневен товар.",),
        allowed_range=(0.5, 1.5),
        editable_roles=EDITOR_ROLES,
    ),
    "profile_version": _definition(
        "parameter.{zone}.profile_version",
        "profile version",
        "Версия на физиологичния профил",
        "Версията фиксира набора W_low, W_high, p и HR граници.",
        "—",
        "версия",
        interpretation="Промяна на версията означава промяна на моделната дефиниция и изисква ново сравнение.",
        dependencies=("Q_z",),
        limitations=("Само за четене в пилотния интерфейс.",),
    ),
    "tref_bounds_profile_version": _definition(
        "parameter.{zone}.tref_bounds_profile_version",
        "Tref bounds version",
        "Версия на TrefBoundsProfile",
        "Версията фиксира базовите долни и горни граници.",
        "—",
        "версия",
        interpretation="Позволява резултатът да бъде възпроизведен с точния профил на границите.",
        dependencies=("tref_effective",),
        limitations=("Само за четене в пилотния интерфейс.",),
    ),
}


RESULT_DEFINITIONS: dict[str, dict[str, Any]] = {
    "result.t": _definition(
        "result.t", "T_z", "Реално време в зона", "Класифицираното надеждно активно време в зоната.",
        "T_z = ∫ I(HR(t) ∈ z) dt", "минути",
        interpretation="Ниска/висока стойност означава по-малко/повече реално време, не непременно по-малък/по-голям физиологичен ефект.",
        inputs=("нормализиран HR поток", "HR граници"), dependencies=("Q_z",),
        limitations=("Липсващ или некачествен HR намалява покритието и T_z.",),
    ),
    "result.q": _definition(
        "result.q", "Q_z", "Директно физиологично еквивалентно време", "Вътрешнозоново претегленото време преди cascade и spillover.",
        "Q_z = ∫ k(HR(t)) dt", "еквивалентни минути",
        interpretation="Q_z ≥ T_z при профил с W_high ≥ W_low; висок Q показва повече време и/или работа във високата част на зоната.",
        inputs=("T_z", "W_low", "W_high", "p"), dependencies=("cascade", "spillover", "E_z", "Tref"),
        limitations=("Зависи от качеството на HR и калибрацията на профила.",),
    ),
    "result.cascade": _definition(
        "result.cascade", "cascade", "Каскаден принос от по-високи зони", "100% от директния Q на всяка по-висока аеробна зона се добавя към по-ниския компонент.",
        "Cascade_z = Σ Q_j, j > z", "еквивалентни минути",
        interpretation="Високата стойност показва значим товар от работа над текущата зона.",
        inputs=("Q на по-високите зони",), dependencies=("E_z",),
        limitations=("Spillover не създава вторична cascade.",),
    ),
    "result.spillover": _definition(
        "result.spillover", "spillover", "Двупосочен съседен spillover", "Еднократният входящ принос от непосредствените съседни зони върху превишението над техния праг.",
        "X=max(0,Q-threshold×Tref); Spill=rate×X", "еквивалентни минути",
        interpretation="Нула означава, че няма съседно превишение; по-висока стойност означава по-силен надпрагов съседен стимул.",
        inputs=("Q_z", "tref_effective", "spill rates"), dependencies=("E_z",),
        limitations=("Не намалява Q и не поражда рекурсивен spillover.",),
    ),
    "result.e": _definition(
        "result.e", "E_z", "Краен компонентен ефект", "Сборът от директния Q, каскадния принос и входящия spillover.",
        "E_z = Q_z + Cascade_z + Spill_received,z", "еквивалентни минути",
        interpretation="По-висок E означава по-голям моделиран компонентен стимул, а не медицинска оценка.",
        inputs=("Q_z", "cascade", "spillover"), dependencies=("бъдещ Tref",),
        limitations=("Не включва readiness, wellness или recovery модификатори в shadow пилота.",),
    ),
    "result.tref_raw": _definition(
        "result.tref_raw", "tref_raw", "Сурова историческа седмична референция", "Седем пъти средния дневен E от наличната предходна история до 40 дни; при липса се показва системният fallback.",
        "tref_raw,z = 7 × mean(E_z, previous N days), N≤40", "еквивалентни минути/седмица",
        interpretation="Ниско/високо означава по-ниска/по-висока наблюдавана адаптационна база; стойността остава видима и когато е извън границите.",
        inputs=("предходен E_z",), dependencies=("tref_effective", "spillover"),
        limitations=("Текущият ден е изключен; непълната история намалява надеждността и липсващ ден не се приема автоматично за почивен.",),
    ),
    "result.tref_effective": _definition(
        "result.tref_effective", "tref_effective", "Използвана ограничена Tref референция", "Суровият Tref, ограничен между ефективните профилни граници.",
        "tref_effective = clip(tref_raw, c×tref_min, c×tref_max)", "еквивалентни минути/седмица",
        interpretation="Разлика спрямо tref_raw означава активирана долна или горна граница.",
        inputs=("tref_raw", "tref_min", "tref_max", "c_z"), dependencies=("spillover",),
        limitations=("Ограничението не променя историческите E стойности.",),
    ),
    "result.hr_coverage": _definition(
        "result.hr_coverage", "HR coverage", "Покритие на активното време с валиден HR", "Делът от активното време, който може надеждно да се класифицира.",
        "coverage = classified HR time / active time × 100", "%",
        interpretation="Под 80% е предупреждение; 80–95% е частично; над 95% е добро диагностично покритие.",
        inputs=("валидни HR интервали", "активно време"), dependencies=("T_z", "Q_z", "E_z"),
        limitations=("Високото покритие не гарантира правилни HR граници или точен сензор.",),
    ),
    "result.average_k": _definition(
        "result.average_k", "среден k_z", "Среден вътрешнозонов коефициент", "Съотношението между претегленото и реалното време в зоната.",
        "average_k_z = Q_z / T_z", "без единица",
        interpretation="1.0 означава долната профилна тежест; по-висока стойност показва повече време във високата част на зоната.",
        inputs=("Q_z", "T_z"), dependencies=("диагностично тълкуване на Q_z",),
        limitations=("Не се определя, когато T_z е нула.",),
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
        "result.classified_hr", "класифицирано HR време", "Активно време с валиден и зонално класифициран HR", "Частта от активното време, която участва в T_z и Q_z.",
        "classified = Σ_z T_z", "секунди",
        interpretation="Колкото е по-близо до активното време, толкова по-пълно е покритието за зоналния модел.",
        inputs=("валиден HR", "профилни HR граници"), dependencies=("T_z", "Q_z", "HR coverage"),
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
        inputs=("interval classifications",), dependencies=("активно време", "T_z", "Q_z"),
        limitations=("Причината трябва да се тълкува заедно с classification breakdown.",),
    ),
    "model.profile_fingerprint": _definition(
        "model.profile_fingerprint", "profile fingerprint", "Отпечатък на използвания физиологичен профил", "SHA-256 отпечатък на каноничните, нечувствителни профилни параметри за възпроизводимост.",
        "fingerprint = SHA-256(canonical profile metadata)", "hex SHA-256",
        interpretation="Еднакъв отпечатък означава еднаква канонична профилна конфигурация; различен означава промяна.",
        inputs=("HR граници", "W_low", "W_high", "p"), dependencies=("stale-result проверка",),
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
        "warning.incomplete_history", "Непълна история", "Недостатъчна предходна история за Tref",
        "Налични са по-малко от 40 надеждно представени предходни дни.", "history_days < 40", "дни",
        interpretation="Tref е с по-ниска надеждност; при нула дни се използва видим системен fallback.", dependencies=("tref_raw",),
        limitations=("Липсващ синхронизиран ден не се приема автоматично за почивка.",),
    ),
    "warning.low_hr_coverage": _definition(
        "warning.low_hr_coverage", "Ниско HR покритие", "Недостатъчно покритие с валиден HR",
        "Част от активното време не участва в T_z и Q_z.", "coverage < 80%", "%",
        interpretation="Резултатите вероятно подценяват реалния зонален товар.", dependencies=("T_z", "Q_z", "E_z"),
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
