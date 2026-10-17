import Link from "next/link";
import { durationSeconds } from "../lib/duration-format";
import { methodAnchorLabel, methodHref, methodHrText, rangeText, type SessionMethod } from "../lib/method-library";
import type { DraftSession } from "../lib/training-management";

/** Include every component of a combined session once; old plans stay readable. */
export function sessionMethods(session: DraftSession): SessionMethod[] {
  const methods = [session.method_library, ...(session.method_library?.components ?? []), ...session.blocks.flatMap(block => [block.method_library, block.parent_method_card])].filter((m): m is SessionMethod => !!m && typeof m.id === "string");
  return methods.filter((method, index) => methods.findIndex(m => m.id === method.id) === index);
}

export function SessionMethodGuidance({ session }: { session: DraftSession }) {
  const methods = sessionMethods(session);
  const strength = session.dose_evidence?.strength_prescription;
  if (!methods.length && !strength) return null;
  return <section className="management-method-guidance" aria-label="Метод и инструкции">{strength && <div className="management-strength-prescription"><h4>Силова програма · постоянен набор</h4><p>{strength.total_sets} работни серии · {(strength.dose_fraction * 100).toLocaleString("bg-BG", { maximumFractionDigits: 1 })}% от изграждащата доза за едно занимание</p><table><thead><tr><th>Упражнение</th><th>Серии × повторения</th><th>Основни мускулни групи</th></tr></thead><tbody>{strength.exercises.map(exercise => <tr key={exercise.id}><td>{exercise.name}</td><td>{exercise.sets} × {exercise.reps}</td><td>{exercise.muscle_groups}</td></tr>)}</tbody></table><p>{strength.reserve_definition}</p><p>{strength.continuity}</p><p>{strength.progression}</p><details><summary>Научна основа и адаптация</summary><p>Треньорска адаптация на onFlows; упражненията, резервът и комбинацията не възпроизвеждат дословно изследван протокол.</p><ul>{strength.sources.map(source => <li key={source.url}><a href={source.url} target="_blank" rel="noreferrer">{source.authors}</a></li>)}</ul></details></div>}{methods.map(method => {
    const resolution = method.resolution;
    return <div key={method.id}><h4>{method.id.startsWith("COMBO-") ? method.title : <Link href={methodHref(method.id)}>Метод {method.id}{method.title ? ` · ${method.title}` : ""} →</Link>}</h4>
      {method.guidance?.purpose && <p>{method.guidance.purpose}</p>}
      {resolution?.hr_reference_range_bpm && <p className="management-muted"><strong>Пулсов ориентир:</strong> {methodHrText(resolution)}</p>}
      {resolution?.anchor && <p className="management-muted"><strong>{methodAnchorLabel(resolution.anchor)}</strong>{resolution.target_speed_range_kmh ? ` · ${rangeText(resolution.target_speed_range_kmh)} km/h` : ""}{resolution.work_duration_range_seconds ? ` · отсечка ${rangeText(resolution.work_duration_range_seconds, durationSeconds)}` : ""}</p>}
      <details><summary>Указания и критерии за изпълнение</summary>{(["execution", "recovery", "success", "errors", "adjustment", "progression"] as const).map(key => method.guidance?.[key] ? <p key={key}><strong>{{ execution: "Изпълнение", recovery: "Почивка", success: "Успех", errors: "Избягвай", adjustment: "Корекция", progression: "Прогресия" }[key]}:</strong> {method.guidance[key]}</p> : null)}<p className="management-muted">Дозата и личната скорост са запазени при съставянето на тази тренировка. Базата показва текущата крива и настройки.</p></details>
    </div>;
  })}</section>;
}
