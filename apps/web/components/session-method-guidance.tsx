"use client";
import { isRawMovingSpeed, RAW_SPEED_NOTE, speedBasisLabel } from "../lib/speed-basis";
import { speedRangeText } from "../lib/speed-format";
import { useRunningSpeedUnit } from "./running-speed-unit";
import Link from "next/link";
import { durationHms, durationSeconds } from "../lib/duration-format";
import { methodAnchorLabel, methodHref, methodHrText, rangeText, type SessionMethod } from "../lib/method-library";
import type { DraftSession, EffortAnchor } from "../lib/training-management";

function FrozenAnchor({ anchor, sport }: { anchor: EffortAnchor; sport: string }) {
  const unit = useRunningSpeedUnit();
  if (anchor.parts?.length) return <>{anchor.parts.filter(p => !p.kind || p.kind === "WORK").map((part, i) => <span key={i}>{i > 0 ? "; " : ""}{part.zone}: <FrozenAnchor anchor={part.anchor} sport={sport}/></span>)}</>;
  if (anchor.anchor) return <FrozenAnchor anchor={anchor.anchor} sport={sport}/>;
  return <>{methodAnchorLabel(anchor)}{anchor.target_hr_bpm != null ? ` · ${anchor.zone} · ${anchor.target_hr_bpm.toLocaleString("bg-BG", { maximumFractionDigits: 1 })} уд/мин` : ""}{anchor.speed_kmh != null ? ` · ${speedRangeText([anchor.speed_kmh, anchor.speed_kmh], unit, sport)} · ${speedBasisLabel(anchor)}` : ""}</>;
}

function QAccounting({ session }: { session: DraftSession }) {
  const ledger = session.q_accounting;
  if (!ledger) return null;
  const groups = new Map<string, (typeof ledger.rows)[number]>();
  for (const row of ledger.rows) {
    const key = `${row.kind}:${row.zone}:${row.coefficient}:${row.source}`;
    const previous = groups.get(key);
    groups.set(key, previous ? { ...row, duration_min: previous.duration_min+row.duration_min, equivalent_min: previous.equivalent_min+row.equivalent_min } : row);
  }
  const kinds: Record<string, string> = { WORK: "Работа", WARMUP: "Загрявка", COOLDOWN: "Разпускане", RECOVERY: "Почивка", TRANSITION: "Преход", PREPARATION: "Подготовка" };
  return <details><summary>Как е изчислен Q?</summary><p>Прогнозен Q = време × вътрезонален коефициент. Активните почивки, загрявката и разпускането участват според своя компонент. При сила основната работа и вътрешните почивки се отчитат веднъж. Товарът E включва и разлива; той се проверява отделно за 7/40 и Recovery.</p><table><thead><tr><th>Част</th><th>Компонент</th><th>Време</th><th>Коефициент</th><th>Q</th></tr></thead><tbody>{[...groups.values()].map((row, i) => <tr key={i}><td>{kinds[row.kind] ?? row.kind}</td><td>{row.zone}</td><td>{durationHms(row.duration_min)}</td><td>{row.coefficient.toLocaleString("bg-BG", { maximumFractionDigits: 3 })}</td><td>{durationHms(row.equivalent_min)}</td></tr>)}</tbody></table>{(ledger.unavailable_minutes ?? 0) > 0 && <p>За {durationHms(ledger.unavailable_minutes!)} няма допустим разчет на Q. Това е неприравнено време, не нулев товар.</p>}{ledger.unknown_metabolic_minutes > 0 && <p>За {durationHms(ledger.unknown_metabolic_minutes)} техническа работа няма оценен метаболитен Q.</p>}<p className="management-muted">Разчетът е за предписаната тренировка. Реалният Q се изчислява от записаното изпълнение.</p></details>;
}

/** Include every component of a combined session once; old plans stay readable. */
export function sessionMethods(session: DraftSession): SessionMethod[] {
  const methods = [session.method_library, ...(session.method_library?.components ?? []), ...session.blocks.flatMap(block => [block.method_library, block.parent_method_card])].filter((m): m is SessionMethod => !!m && typeof m.id === "string");
  return methods.filter((method, index) => methods.findIndex(m => m.id === method.id) === index);
}

export function SessionMethodGuidance({ session }: { session: DraftSession }) {
  const unit = useRunningSpeedUnit();
  const methods = sessionMethods(session);
  const strength = session.dose_evidence?.strength_prescription;
  const anchor = session.dose_evidence?.effort_anchor;
  if (!methods.length && !strength && !anchor && !session.q_accounting) return null;
  return <section className="management-method-guidance" aria-label="Метод и инструкции">{anchor && <p><strong>Работна опора:</strong> <FrozenAnchor anchor={anchor} sport={session.sport}/></p>}<QAccounting session={session}/>{strength && <div className="management-strength-prescription"><h4>Силова програма · постоянен набор</h4><p>{strength.total_sets} работни серии · {(strength.dose_fraction * 100).toLocaleString("bg-BG", { maximumFractionDigits: 1 })}% от изграждащата доза за едно занимание</p><table><thead><tr><th>Упражнение</th><th>Серии × повторения</th><th>Основни мускулни групи</th></tr></thead><tbody>{strength.exercises.map(exercise => <tr key={exercise.id}><td>{exercise.name}</td><td>{exercise.sets} × {exercise.reps}</td><td>{exercise.muscle_groups}</td></tr>)}</tbody></table><p>{strength.reserve_definition}</p><p>{strength.continuity}</p><p>{strength.progression}</p><details><summary>Научна основа и адаптация</summary><p>Треньорска адаптация на onFlows; упражненията, резервът и комбинацията не възпроизвеждат дословно изследван протокол.</p><ul>{strength.sources.map(source => <li key={source.url}><a href={source.url} target="_blank" rel="noreferrer">{source.authors}</a></li>)}</ul></details></div>}{methods.map(method => {
    const resolution = method.resolution;
    return <div key={method.id}><h4>{method.id.startsWith("COMBO-") ? method.title : <Link href={methodHref(method.id)}>Метод {method.id}{method.title ? ` · ${method.title}` : ""} →</Link>}</h4>
      {method.guidance?.purpose && <p>{method.guidance.purpose}</p>}
      {resolution?.hr_reference_range_bpm && <p className="management-muted"><strong>Пулсов ориентир:</strong> {methodHrText(resolution)}</p>}
      {resolution?.anchor && <p className="management-muted"><strong>{anchor ? "Работен диапазон" : methodAnchorLabel(resolution.anchor)}</strong>{resolution.target_speed_range_kmh ? ` · ${speedRangeText(resolution.target_speed_range_kmh, unit, session.sport)} · ${speedBasisLabel(resolution)}` : ""}{resolution.work_duration_range_seconds ? ` · отсечка ${rangeText(resolution.work_duration_range_seconds, durationSeconds)}` : ""}</p>}
      {isRawMovingSpeed(resolution)&&<p className="management-muted">{RAW_SPEED_NOTE}</p>}
      <details><summary>Указания и критерии за изпълнение</summary>{(["execution", "recovery", "success", "errors", "adjustment", "progression"] as const).map(key => method.guidance?.[key] ? <p key={key}><strong>{{ execution: "Изпълнение", recovery: "Почивка", success: "Успех", errors: "Избягвай", adjustment: "Корекция", progression: "Прогресия" }[key]}:</strong> {method.guidance[key]}</p> : null)}<p className="management-muted">Дозата и личната скорост са запазени при съставянето на тази тренировка. Базата показва текущата крива и настройки.</p></details>
    </div>;
  })}</section>;
}
