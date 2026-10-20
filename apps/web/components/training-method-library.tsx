"use client";
import { speedRangeText, speedText } from "../lib/speed-format";
import { RunningSpeedUnitControl, useRunningSpeedUnit } from "./running-speed-unit";
import { useManagementFetch } from "./management-scope";

import Link from "next/link";
import { useRef, useState, type FormEvent } from "react";
import { durationSeconds } from "../lib/duration-format";
import { isRecord } from "../lib/training-status";
import { methodHref, methodHrText } from "../lib/method-library";
import { METHOD_PHASES, METHOD_SPORTS, isMethodRange, methodAnchorLabel, methodSourceText, methodSourceGroup, methodReasonText, methodStatusLabel, methodWorkText, parseMethodLibrary, parseMethodPreferences, rangeText, type LibraryMethod, type MethodLibraryResponse, type MethodOverride, type MethodPreferences, type MethodRange } from "../lib/method-library";

const guidanceLabels = { purpose: "Какво развиваме", execution: "Как да изпълниш", recovery: "Защо почиваме така", success: "Признаци за добро изпълнение", errors: "Типични грешки", adjustment: "Кога да коригираш", progression: "Как надграждаме" } as const;
const unique = (values: string[]) => [...new Set(values)].filter(Boolean).sort((a, b) => a.localeCompare(b, "bg"));

function RangeEditor({ label, value, onChange, percent = false, integer = false }: { label: string; value: MethodRange; onChange: (range: MethodRange) => void; percent?: boolean; integer?: boolean }) {
  const factor = percent ? 100 : 1;
  return <fieldset className="method-range-editor"><legend>{label}</legend><div>{value.map((v, index) => <label key={index}><span>{index === 0 ? "От" : "До"}</span><input type="number" min={integer ? 1 : .01} step={integer ? 1 : "any"} aria-label={`${label} ${index === 0 ? "от" : "до"}`} value={Number.isFinite(v) ? Number((v * factor).toFixed(6)) : ""} onChange={event => onChange(value.map((old, i) => i === index ? event.target.value === "" ? NaN : Number(event.target.value) / factor : old) as MethodRange)} /></label>)}</div></fieldset>;
}

function SourceDetail({ label, value }: { label: string; value: unknown }) {
  if (typeof value === "string") return value ? <div><h4>{label}</h4><p>{value}</p></div> : null;
  if (!isRecord(value)) return null;
  const fieldLabels: Record<string, string> = { authors: "Автори", reference: "Публикация", locator: "Страница / раздел", evidence: "Данни", result_or_intent: "Цел / резултат", population_conditions: "Участници и условия", anchor: "Опора", work: "Работа", recovery: "Почивка", repetitions: "Повторения", notes: "Уточнение", classification: "Вид адаптация", anchor_label: "Скоростна опора", recipe_text: "Структура", zone_note: "Отчитане на товара" };
  const fieldValue = (key: string) => key === "evidence" && value[key] === "EXPLICIT_ONFLOWS_COACH_ADAPTATION_NOT_SOURCE_EQUIVALENT" ? "Треньорска адаптация на onFlows. Числовите настройки са наши работни правила; не е установен еднакъв ефект с научния оригинал." : value[key] as string;
  const links = Array.isArray(value.links) ? value.links.filter((url): url is string => typeof url === "string" && /^https?:\/\//.test(url)) : [];
  return <div><h4>{label}</h4>{Object.entries(fieldLabels).map(([key, name]) => typeof value[key] === "string" && value[key] ? <p key={key}><strong>{name}:</strong> {fieldValue(key)}</p> : null)}{links.length > 0 && <p className="method-source-links">{links.map((url, i) => <a key={url} href={url} target="_blank" rel="noopener noreferrer">{url.includes("doi.org") ? "DOI · публикация" : `Източник ${i + 1}`} ↗</a>)}</p>}</div>;
}

export function MethodCard({ method, override, canEdit, selected, onChange, onReset }: { method: LibraryMethod; override?: MethodOverride; canEdit: boolean; selected: boolean; onChange: (patch: MethodOverride) => void; onReset: () => void }) {
  const rx = method.prescription;
  const resolution = method.resolution;
  const dosing = rx && isRecord(resolution?.dosing) ? resolution.dosing : isRecord(method.dosing) ? method.dosing : null;
  const sourceDose = dosing?.basis === "SOURCE_STANDARD_NO_CAPACITY_MULTIPLIER" || resolution?.budget_policy === "SELECTED_SOURCE_STANDARD_TIMES_ROLE_TIMES_RECOVERY_ONCE";
  const progression = isRecord(resolution?.progression) ? resolution.progression : null;
  const csEvidence = isRecord(resolution?.anchor) && isRecord(resolution.anchor.evidence) ? resolution.anchor.evidence : null;
  const speed = resolution?.target_speed_range_kmh;
  const speedUnit = useRunningSpeedUnit();
  const speedSport = method.sports.includes("Run") ? "Run" : method.sports[0];
  const status = override?.enabled === false ? "DISABLED" : resolution?.status;
  const ready = status === "READY";
  const allowed = override?.enabled ?? (resolution?.reason === "SOURCE_FAMILY_DISABLED_BY_PROFILE" ? false : method.automatic_selection ?? false);
  const [editing, setEditing] = useState(false);
  const phaseValue = rx?.phase_ranges?.[resolution?.phase ?? ""];
  const variant = isRecord(phaseValue) ? phaseValue : {};
  const multiplier = override?.speed_multiplier ?? resolution?.speed_multiplier_range ?? (isMethodRange(variant.speed_multiplier) ? variant.speed_multiplier : rx?.speed_multiplier);
  const intensityRange = multiplier ?? (isMethodRange(resolution?.hrmax_fraction_range) ? resolution.hrmax_fraction_range : rx?.hrmax_fraction);
  const work = rx?.work ? { unit: resolution?.work_unit ?? rx.work.unit, range: override?.work_range ?? resolution?.work_range ?? (isMethodRange(variant.work_range) ? variant.work_range : rx.work.range) } : method.registry_kind === "COACH_PROFILE" && resolution?.work_unit && resolution.work_range ? {unit: resolution.work_unit, range: resolution.work_range} : undefined;
  const repetitions = override?.repetitions ?? rx?.repetitions;
  const noAdditionalRecovery = rx?.recovery?.seconds?.every(seconds => seconds === 0) ?? false;
  const related = method.related_method_ids ?? [];
  const workLabel = work ? methodWorkText(work) : resolution?.segments?.length ? resolution.segments.slice(0, 3).map(s => `${durationSeconds(s.work_duration_seconds)} ${s.kind === "RECOVERY" ? "леко" : "работа"}`).join(" → ") + (resolution.segments.length > 3 ? " → …" : "") : rx?.pattern ? "Свързани части · виж структурата" : "Оригиналът е описан по-долу";
  const originalAnchor = isRecord(method.original) && typeof method.original.anchor === "string" ? method.original.anchor : "Опората е описана в оригинала";
  const reasons = [resolution?.reason, resolution?.message, ...(resolution?.reasons ?? [])].filter((v): v is string => typeof v === "string" && !!v).map(methodReasonText);
  return <article id={`method-${method.id}`} className={`method-card${selected ? " is-selected" : ""}`}>
    <header><div><p className="method-id">{method.id} · {method.sports.map(s => METHOD_SPORTS[s] ?? s).join(" / ")}</p><h2>{method.title}</h2></div><span className={`method-status${ready && allowed ? " ready" : ""}`}>{ready && !allowed ? "Лична оценка · включване от треньор" : methodStatusLabel(status, resolution?.reason)}</span></header>
    {related.length > 0 && <p className="methods-notice"><strong>Има работна рецепта за програмата:</strong> {related.map(id => <Link key={id} href={methodHref(id)}>{id} → </Link>)} Оригиналът остава за сравнение с избраната адаптация.</p>}
    {method.derived_from?.length ? <p className="method-note">Адаптация по {method.derived_from.map(id => <Link key={id} href={methodHref(id)}>{id} </Link>)} · промените са описани по-долу.</p> : null}
    {Array.isArray(method.athlete_levels) && <p className="method-note">Подходящо ниво: {method.athlete_levels.map(level => ({BEGINNER:"начинаещ",ADVANCED:"напреднал",HIGHLY_TRAINED:"високотрениран"})[String(level)] ?? "индивидуална преценка").join(" / ")}.</p>}
    {method.guidance.purpose && <p className="method-purpose">{method.guidance.purpose}</p>}
    <dl className="method-facts"><div><dt>Опора за интензивността</dt><dd>{resolution?.anchor ? methodAnchorLabel(resolution.anchor) : rx ? methodAnchorLabel(rx.anchor) : originalAnchor}</dd><small>{intensityRange ? `${rangeText(intensityRange, n => (n * 100).toLocaleString("bg-BG", { maximumFractionDigits: 1 }))}%` : "Не е зададен числов диапазон"}</small></div><div><dt>Работна отсечка</dt><dd>{workLabel}</dd><small>{repetitions ? `${rangeText(repetitions)} повторения · ${override?.repetitions ? "треньорско ограничение" : "допустим диапазон на метода"}` : "Според структурата"}{rx?.sets ? ` · ${rx.sets.count} серии` : ""}</small></div><div><dt>Лична скорост · {METHOD_PHASES[resolution?.phase ?? ""] ?? "текущ период"}</dt><dd>{ready && speed ? speedRangeText(speed, speedUnit, speedSport) : resolution?.zone === "STR" ? "Време и качество на упражненията" : resolution?.hr_role === "PRIMARY_SOURCE_PRESCRIPTION" ? "По публикувания %HRmax" : "Няма приложима оценка"}</dd><small>{ready && speed ? "Процентът се прилага към скоростта." : reasons[0] ?? "Използвай обяснението на метода и провери индивидуалните опори."}</small></div></dl>
    <details open={selected || undefined} className="method-details"><summary>Обяснение, почивка и прогресия</summary><div className="method-guidance-grid">{Object.entries(guidanceLabels).filter(([key]) => key !== "purpose").map(([key, label]) => {
      const text = method.guidance[key as keyof typeof guidanceLabels];
      return text ? <section key={key}><h3>{label}</h3><p>{text}</p></section> : null;
    })}</div>
      {rx?.recovery?.seconds && <p className="method-recovery"><strong>{noAdditionalRecovery ? "Допълнителна почивка:" : "Почивка:"}</strong> {noAdditionalRecovery ? "Няма; следвай описаните леки части и паузи в структурата." : <>{rangeText(rx.recovery.seconds, durationSeconds)}{rx.recovery.type ? ` · ${{ ACTIVE: "активна", ACTIVE_Z1: "активна, зона 1", PASSIVE: "пасивна", WALK_JOG: "ходене / леко движение", UNSPECIFIED: "видът не е уточнен в източника" }[rx.recovery.type] ?? rx.recovery.type}` : ""}</>}{rx.sets?.recovery_seconds ? ` · между сериите ${Array.isArray(rx.sets.recovery_seconds) ? rangeText(rx.sets.recovery_seconds, durationSeconds) : durationSeconds(rx.sets.recovery_seconds)}` : ""}</p>}
      {ready && resolution?.work_duration_range_seconds && <p><strong>Време на личната работна отсечка:</strong> {rangeText(resolution.work_duration_range_seconds, durationSeconds)}{resolution.work_distance_range_m ? ` · ${rangeText(resolution.work_distance_range_m)} m` : ""}.</p>}
      {ready && resolution?.zone !== "STR" && <p><strong>Пулсов ориентир:</strong> {methodHrText(resolution)}</p>}
      {ready && typeof resolution?.standard_work_seconds === "number" && <p><strong>Избрана стандартна работа:</strong> {durationSeconds(resolution.standard_work_seconds)}. Тази доза в допустимите рамки е основата за ролевите и възстановителните корекции; максималната граница не се избира автоматично.</p>}
      {ready && typeof resolution?.minimum_work_seconds === "number" && typeof resolution.maximum_work_seconds === "number" && <p><strong>Работен обем преди ограниченията на седмицата:</strong> {rangeText([resolution.minimum_work_seconds, resolution.maximum_work_seconds], durationSeconds)}. Крайната доза отчита възстановяването и наличния бюджет.</p>}
      {rx?.pace_offset && <p><strong>Корекция на темпото:</strong> {rangeText(rx.pace_offset.seconds)} сек на {rx.pace_offset.distance_m} m след изчисляване на скоростната опора.</p>}
      {resolution?.reason === "SHORT_SPEED_TEST_REQUIRED" && <p className="methods-notice">Добави реален максимален тест до {Number(resolution.required_test_max_seconds)} сек за същото средство в <Link href="/speed">Скорост–време</Link>. След приемането му диапазоните се изчисляват автоматично.</p>}
      {ready && resolution?.anchor?.speed_kmh && <p><strong>Лична основа: {methodAnchorLabel(resolution.anchor)}</strong> = {speedText(resolution.anchor.speed_kmh, speedUnit, speedSport)}{resolution.anchor.duration_seconds ? ` за ${durationSeconds(resolution.anchor.duration_seconds)}` : ""}. {resolution.anchor.source === "MANUAL" ? "От ръчно зададената индивидуална опора; моделната оценка не я презаписва." : resolution.anchor.source === "MEASURED" ? "От въведената измерена индивидуална опора; моделната оценка не я презаписва." : resolution.curve_source === "BLENDED_DOSING_CURVE" ? "От актуалната индивидуална крива за дозиране, която съчетава приетите тестове и моделната форма." : resolution.curve_source === "INDIVIDUAL_TEST_CURVE" ? "От кривата, калибрирана с индивидуалните максимални тестове." : resolution.curve_source === "INDIVIDUAL_PRIOR_CURVE" ? "От индивидуална предварителна моделна оценка." : "От актуалната индивидуална крива."} Работната скорост следва процента и изрично посочената времева корекция, когато рецептата има такава.</p>}
      {rx?.repetitions_from_budget && <p className="method-note">Броят повторения се избира от разрешения работен бюджет. Горната му граница е експертно правило на onFlows, което предстои да се потвърждава с реалното изпълнение.</p>}
      {ready && resolution?.segments && resolution.segments.length > 1 && <div className="method-segments"><h3>Свързани части на едно повторение</h3><ol>{resolution.segments.map((segment, i) => <li key={i}><strong>{segment.kind === "RECOVERY" ? "Леко движение" : "Работа"}: {durationSeconds(segment.work_duration_seconds)}</strong> · {segment.work_distance_m ? `${Math.round(segment.work_distance_m)} m · ` : ""}{segment.target_speed_range_kmh ? speedRangeText(segment.target_speed_range_kmh, speedUnit, speedSport) : ""} · {methodAnchorLabel(segment.anchor)}{segment.hr_reference_range_bpm ? ` · ${rangeText(segment.hr_reference_range_bpm)} уд./мин (${segment.hr_role === "PRIMARY_SOURCE_PRESCRIPTION" ? "основен ориентир по %HRmax" : "вторичен ориентир"})` : ""}{typeof segment.recovery_after_seconds === "number" && segment.recovery_after_seconds > 0 ? ` · след това ${durationSeconds(segment.recovery_after_seconds)} почивка` : ""}</li>)}</ol></div>}
      {resolution?.speed_role === "FLAT_EQUIVALENT_REFERENCE_NOT_TERRAIN_PACE" && <p className="method-note">Скоростта е еквивалент за сравнение при съпоставими условия. По пресечен терен контролирай усилието и техниката; числото не е постоянна GPS скорост за всяко изкачване и спускане.</p>}
      {reasons.length > 0 && <p className="method-note">{unique(reasons).join(" ")}</p>}
      {csEvidence && <p className="method-note"><strong>Основа за КС:</strong> {csEvidence.source === "DIRECT_ACCEPTED_TEST_CS" ? "Оценка от приети максимални тестове." : "Моделна оценка от V3 и V12 на текущата крива; не е измерен тест."} Процентът от КС не се превръща автоматично в същия процент от максималния пулс.</p>}
      {progression && <p className="method-note"><strong>Прогресия · стъпка {Number(progression.step) + 1} от {Number(progression.steps)}:</strong> {String(progression.explanation)} {progression.eligible === false ? "Тази стъпка още не е отключена." : "Стъпката е допустима; седмичният план проверява Recovery и общия бюджет."}</p>}
      <div className="method-source"><SourceDetail label="Публикувано предписание" value={method.original}/><SourceDetail label="Адаптация за onFlows" value={method.adaptation}/><SourceDetail label="Източник" value={method.source}/></div>
      <p className="method-note">Диапазонът описва свързани варианти: по-къса отсечка с по-висок процент или по-дълга с по-нисък. Горните граници на скорост и продължителност не се събират автоматично. Ролята следва тренировъчната цел. Поддържащата и допълващата работа използват 60% от базовата доза на метода, без допълнителен множител по Recovery. Развиващата доза се намалява с половината от дефицита на възстановяване. Под 50% Recovery не се допуска целева работа над Z1. Планът проверява целите повторения, времето и оставащия товар.</p>
    </details>
    {canEdit && <div className="method-edit"><button className="method-text-button" type="button" aria-expanded={editing} onClick={() => setEditing(!editing)}>Настройки за спортиста{override ? " · има корекция" : ""}</button>{editing && <div>
      <label className="method-checkbox"><input type="checkbox" disabled={!rx && method.registry_kind !== "COACH_PROFILE"} checked={allowed} onChange={e => onChange({ enabled: e.target.checked })}/>Разрешен за избор в седмичната програма</label>
      {!rx && method.registry_kind !== "COACH_PROFILE" && <p className="method-note">За седмичната програма отвори свързаната работна адаптация. Научният оригинал остава непроменен.</p>}
      {method.registry_kind === "COACH_PROFILE" && <p className="method-note">Работен метод от общата база. Настройката за включване важи за седмичния план; индивидуалният числов профил се задава в <Link href="/management">Управление</Link>.</p>}
      {rx?.pattern?.length ? <p className="method-note">Комбинираният метод пази свързаните отсечки, скорости и почивки. Те не се заменят с един общ диапазон.</p> : null}
      {sourceDose && <p>Дозата следва избрания вариант на публикувания метод или изрично описаната адаптация. Не се добавя множител от Tmax върху вече дозиран метод. Поддържането използва 60% от избраната стандартна работа; целите повторения се проверяват след приложимата еднократна корекция.</p>}
      {dosing && !sourceDose && <><p>Общият работен капацитет е коефициентът × личния Tmax при избраната интензивност. За непрекъснатите Z1/Z2 без публикувана доза резервният вариант е фиксирани 65% от личния Tmax, без допълнителен капацитетен множител. Настройките по-долу важат за останалите треньорски профили. Развиващата доза отчита половината дефицит на Recovery; поддържащата — 60% от развиващата без допълнително намаляване. При комбинация всеки компонент получава своя дял. Стойностите са начални треньорски настройки.</p><div className="method-override-grid">{([
        ["total_capacity_ratio", "Общ работен капацитет, % от Tmax", 100, 2],
        ["speed_tolerance_fraction", "Допуск за скорост, ±% (при точкова опора)", 100, .02],
        ["hr_tolerance_bpm", "Допуск за пулс, ±уд./мин (при точкова опора)", 1, 3],
      ] as const).map(([key, label, factor, fallback]) => <label key={key}>{label}<input type="number" step="any" min={key === "hr_tolerance_bpm" ? 1 : .01} max={key === "total_capacity_ratio" ? 2000 : key === "speed_tolerance_fraction" ? 10 : 15} value={Number.isFinite(override?.[key] ?? dosing[key] ?? fallback) ? Number(((override?.[key] ?? Number(dosing[key] ?? fallback))*factor).toFixed(4)) : ""} onChange={e => onChange({[key]: e.target.value === "" ? NaN : Number(e.target.value)/factor})}/></label>)}</div><p>Записаните диапазони на метода се запазват. Допуските разширяват само точковите опори в границите на индивидуалната зона.</p></>}
      <div className="method-override-grid">{multiplier && !rx?.pattern?.some(s => s.speed_multiplier) && <RangeEditor label="Интензивност, % от опората" value={multiplier} percent onChange={speed_multiplier => onChange({ speed_multiplier })}/>}{rx?.work && work && work.unit !== "capacity_fraction" && !rx?.pattern?.length && <RangeEditor label={`Работна отсечка, ${work.unit === "seconds" ? "секунди" : "метри"}`} value={work.range} onChange={work_range => onChange({ work_range })}/>} {repetitions && !["SEQUENCE", "SCALED_SEQUENCE", "PROGRESSIVE_CONTINUOUS"].includes(rx?.structure ?? "") && <div><label><input type="checkbox" checked={override?.repetitions !== undefined} onChange={e => onChange({repetitions: e.target.checked ? repetitions : undefined})}/>Изрично ограничи повторенията</label>{override?.repetitions ? <RangeEditor label="Изрично ограничение на повторенията" value={override.repetitions} integer onChange={repetitions => onChange({ repetitions })}/> : <p>Броят остава в допустимите варианти на метода. Намалената доза изисква цели повторения и изрично допустим вариант.</p>}</div>}</div>
      {override && <button type="button" className="method-text-button" onClick={onReset}>Върни настройките на метода</button>}
    </div>}</div>}
  </article>;
}

export function TrainingMethodLibrary({ initial, athleteName, canEdit, selectedId = "" }: { initial: MethodLibraryResponse; athleteName: string; canEdit: boolean; selectedId?: string }) {
  const scopedFetch = useManagementFetch();
  const [data, setData] = useState(initial);
  const [preferences, setPreferences] = useState<MethodPreferences>(initial.preferences);
  const [query, setQuery] = useState("");
  const [sport, setSport] = useState("");
  const [discipline, setDiscipline] = useState("");
  const [anchor, setAnchor] = useState("");
  const [source, setSource] = useState("");
  const [phase, setPhase] = useState("");
  const [onlyReady, setOnlyReady] = useState(false);
  const [showReferences, setShowReferences] = useState(false);
  const [visibleCount, setVisibleCount] = useState(24);
  const [selected, setSelected] = useState(selectedId);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [conflict, setConflict] = useState(false);
  const requestActive = useRef(false);
  const dirty = JSON.stringify(preferences) !== JSON.stringify(data.preferences);

  async function load(nextPhase = phase) {
    const response = await scopedFetch(`/api/athlete/management/methods${nextPhase ? `?phase=${encodeURIComponent(nextPhase)}` : ""}`, { cache: "no-store" });
    const value: unknown = await response.json();
    if (!response.ok) throw new Error(isRecord(value) && typeof value.error === "string" ? value.error : "Методите не можаха да се обновят.");
    return parseMethodLibrary(value);
  }
  async function reload(nextPhase = phase) {
    if (requestActive.current) return;
    requestActive.current = true; setBusy(true); setError(""); setNotice("");
    try { const result = await load(nextPhase); setData(result); setPreferences(result.preferences); setPhase(nextPhase); setConflict(false); }
    catch (caught) { setError(caught instanceof Error ? caught.message : "Неуспешно обновяване."); }
    finally { requestActive.current = false; setBusy(false); }
  }
  async function save(event: FormEvent) {
    event.preventDefault(); if (requestActive.current) return;
    requestActive.current = true; setBusy(true); setError(""); setNotice(""); setConflict(false);
    try {
      const checked = parseMethodPreferences(preferences);
      const response = await scopedFetch("/api/athlete/management/methods/preferences", { method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ expected_revision: data.revision, preferences: checked }) });
      const value: unknown = await response.json();
      if (!response.ok) { setConflict(response.status === 409); throw new Error(isRecord(value) && typeof value.error === "string" ? value.error : "Настройките не бяха записани."); }
      if (!isRecord(value) || !Number.isSafeInteger(value.revision)) throw new Error("Записът е изпратен, но потвърждението липсва. Зареди последната версия преди нов запис.");
      const saved = parseMethodPreferences(value.preferences);
      setData(current => ({ ...current, revision: Number(value.revision), preferences: saved })); setPreferences(saved);
      setNotice("Настройките са записани. Обнови седмичната програма, за да ги приложиш към новите тренировки.");
      try { const refreshed = await load(); setData(refreshed); setPreferences(refreshed.preferences); }
      catch { setError("Настройките са записани, но личните диапазони не можаха да се обновят. Зареди ги отново."); }
    } catch (caught) { setError(caught instanceof Error ? caught.message : "Настройките не бяха записани."); }
    finally { requestActive.current = false; setBusy(false); }
  }
  const patch = (id: string, update: MethodOverride) => { setPreferences(p => ({ ...p, overrides: { ...p.overrides, [id]: { ...p.overrides[id], ...update } } })); setNotice(""); };
  const reset = (id: string) => setPreferences(p => ({ ...p, overrides: Object.fromEntries(Object.entries(p.overrides).filter(([key]) => key !== id)) }));
  const matches = data.methods.filter(m => (!selected || m.id === selected) && (selected || query.trim() || showReferences || m.registry_kind === "COACH_PROFILE" || (m.prescription && m.automatic_selection !== false)) && (!sport || m.sports.includes(sport)) && (!discipline || m.disciplines.map(String).includes(discipline)) && (!anchor || m.prescription?.anchor.type === anchor)
    && (!source || methodSourceGroup(m.source) === source) && (!onlyReady || (m.resolution?.status === "READY" && preferences.overrides[m.id]?.enabled !== false))
    && (!query.trim() || `${m.id} ${m.title} ${Object.values(m.guidance).join(" ")} ${methodSourceText(m.source)}`.toLocaleLowerCase("bg").includes(query.trim().toLocaleLowerCase("bg"))));
  const readyCount = data.methods.filter(m => m.resolution?.status === "READY" && preferences.overrides[m.id]?.enabled !== false).length;
  return <main className="activities-page methods-page"><header className="methods-hero"><div><p className="eyebrow">{athleteName} · подготовка с ясна цел</p><h1>База от тренировъчни методи</h1><p>Избери метода според качеството, което развиваш. Виж как се изпълнява и как актуалната крива определя личната скорост.</p></div><Link className="text-action" href="/management">Седмична програма →</Link></header>
    <RunningSpeedUnitControl/>
    <section className="methods-context" aria-label="Индивидуална основа"><div><small>Основна дисциплина</small><strong>{data.profile?.discipline || "Не е зададена"}</strong><Link href="/planning">Профил за планиране →</Link></div><div><small>Период на персонализацията</small><strong>{METHOD_PHASES[data.phase] ?? data.phase}</strong><span>{phase ? "Преглед на избран период" : "От текущия план"}</span></div><div><small>Каталог</small><strong>{data.methods.length} метода</strong><span>{readyCount} с приложима лична дозировка</span></div></section>
    <p className="methods-intro">КС задава критичната скорост. MAS се оценява чрез V(6 мин), когато липсва измерена опора; методите по V(5 мин) запазват петминутната опора. Състезателната скорост следва целевата продължителност и актуалната крива. Късите и дългите отсечки имат собствени диапазони. Напредъкът се отчита чрез обновената крива.</p>
    {!data.configured && <p className="methods-notice">Попълни <Link href="/planning">профила за планиране</Link>, за да получиш индивидуални диапазони и настройки.</p>}
    {!canEdit && <p className="methods-notice">Разполагаш с достъп за преглед. Настройките се променят от спортиста или треньор с права за редактиране.</p>}
    <section className="methods-filter-panel" aria-label="Филтри на методите"><label className="methods-search">Търси метод, цел или автор<input type="search" value={query} onChange={e => { setQuery(e.target.value); setSelected(""); }} placeholder="Например: 30/15, 5000, техника…" /></label><div className="methods-filters">
      <label>Спорт<select value={sport} onChange={e => { setSport(e.target.value); setSelected(""); }}><option value="">Всички спортове</option>{unique(data.methods.flatMap(m => m.sports)).map(s => <option key={s} value={s}>{METHOD_SPORTS[s] ?? s}</option>)}</select></label>
      <label>Дисциплина<select value={discipline} onChange={e => { setDiscipline(e.target.value); setSelected(""); }}><option value="">Всички дисциплини</option>{unique(data.methods.flatMap(m => m.disciplines.map(String))).map(d => <option key={d} value={d}>{Number.isFinite(Number(d)) ? `${d} m` : d}</option>)}</select></label>
      <label>Период за преглед<select value={phase} disabled={busy || dirty} onChange={e => void reload(e.target.value)}><option value="">Текущият период</option>{Object.entries(METHOD_PHASES).map(([key, label]) => <option key={key} value={key}>{label}</option>)}</select></label>
      <label>Опора<select value={anchor} onChange={e => { setAnchor(e.target.value); setSelected(""); }}><option value="">Всички опори</option>{unique(data.methods.map(m => m.prescription?.anchor.type ?? "")).map(a => <option key={a} value={a}>{methodAnchorLabel({ type: a })}</option>)}</select></label>
      <label>Източник<select value={source} onChange={e => { setSource(e.target.value); setSelected(""); }}><option value="">Всички източници</option>{unique(data.methods.map(m => methodSourceGroup(m.source))).map(s => <option key={s} value={s}>{s}</option>)}</select></label>
    </div><label className="method-checkbox"><input type="checkbox" checked={onlyReady} onChange={e => setOnlyReady(e.target.checked)}/>Само с приложима индивидуална дозировка</label></section>
    {selected && <p className="methods-selection">Избран метод: <strong>{selected}</strong> <button className="method-text-button" type="button" onClick={() => setSelected("")}>Покажи всички методи</button></p>}
    <form onSubmit={save}>
      {canEdit && data.configured && <div className="methods-save-bar"><label className="method-checkbox"><input type="checkbox" disabled={busy} checked={preferences.enabled} onChange={e => { setPreferences(p => ({ ...p, enabled: e.target.checked })); setNotice(""); }}/>Използвай базата в седмичната програма</label><div><span>{dirty ? "Има незаписани промени" : "Използват се записаните настройки"}</span><button className="action-button" disabled={!dirty || busy || conflict} type="submit">{busy ? "Обновяване…" : "Запази настройките"}</button></div></div>}
      {error && <div className="methods-error" role="alert"><p>{error}</p>{conflict && <p>Локалните промени са запазени във формата. Зареждането на последната версия ще ги замени.</p>}<button className="method-text-button" type="button" disabled={busy} onClick={() => void reload()}>{conflict ? "Зареди последната версия и замени местните промени" : "Зареди отново"}</button></div>}
      {notice && <p className="methods-notice" role="status">{notice} <Link href="/management">Към програмата →</Link></p>}
      {dirty && <p className="method-note">Показаните лични скорости са от последните записани настройки. След запазване се преизчисляват и коригираните диапазони.</p>}
      <p className="methods-result-count" role="status">{matches.length} от {data.methods.length} метода</p>
      <label className="method-checkbox"><input type="checkbox" checked={showReferences} onChange={e => { setShowReferences(e.target.checked); setSelected(""); }}/>Покажи и научните оригинали и справочните варианти</label>
      <fieldset className="methods-cards" disabled={busy}>{matches.slice(0, visibleCount).map(method => <MethodCard key={method.id} method={method} override={preferences.overrides[method.id]} canEdit={canEdit && data.configured} selected={method.id === selected} onChange={patchValue => patch(method.id, patchValue)} onReset={() => reset(method.id)}/>)}</fieldset>
      {matches.length > visibleCount && <button type="button" className="action-button secondary" onClick={() => setVisibleCount(n => n + 24)}>Покажи още 24 метода</button>}
    </form>
    {!matches.length && <div className="methods-empty"><h2>Няма методи за избраните условия</h2><p>Промени търсенето или филтрите, за да видиш останалите методи.</p><button className="action-button secondary" onClick={() => { setQuery(""); setSport(""); setDiscipline(""); setAnchor(""); setSource(""); setOnlyReady(false); setSelected(""); }}>Изчисти филтрите</button></div>}
  </main>;
}
