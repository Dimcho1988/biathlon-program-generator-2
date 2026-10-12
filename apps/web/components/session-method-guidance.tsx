import Link from "next/link";
import { durationSeconds } from "../lib/duration-format";
import { methodAnchorLabel, methodHref, rangeText, type SessionMethod } from "../lib/method-library";
import type { DraftSession } from "../lib/training-management";

/** Include every component of a combined session once; old plans stay readable. */
export function sessionMethods(session: DraftSession): SessionMethod[] {
  const methods = [session.method_library, ...session.blocks.map(block => block.method_library)].filter((m): m is SessionMethod => !!m && typeof m.id === "string");
  return methods.filter((method, index) => methods.findIndex(m => m.id === method.id) === index);
}

export function SessionMethodGuidance({ session }: { session: DraftSession }) {
  const methods = sessionMethods(session);
  if (!methods.length) return null;
  return <section className="management-method-guidance" aria-label="Метод и инструкции">{methods.map(method => {
    const resolution = method.resolution;
    return <div key={method.id}><h4><Link href={methodHref(method.id)}>Метод {method.id}{method.title ? ` · ${method.title}` : ""} →</Link></h4>
      {method.guidance?.purpose && <p>{method.guidance.purpose}</p>}
      {resolution?.anchor && <p className="management-muted"><strong>{methodAnchorLabel(resolution.anchor)}</strong>{resolution.target_speed_range_kmh ? ` · ${rangeText(resolution.target_speed_range_kmh)} km/h` : ""}{resolution.work_duration_range_seconds ? ` · отсечка ${rangeText(resolution.work_duration_range_seconds, durationSeconds)}` : ""}</p>}
      <details><summary>Указания и критерии за изпълнение</summary>{(["execution", "recovery", "success", "errors", "adjustment", "progression"] as const).map(key => method.guidance?.[key] ? <p key={key}><strong>{{ execution: "Изпълнение", recovery: "Почивка", success: "Успех", errors: "Избягвай", adjustment: "Корекция", progression: "Прогресия" }[key]}:</strong> {method.guidance[key]}</p> : null)}<p className="management-muted">Дозата и личната скорост са запазени при съставянето на тази тренировка. Базата показва текущата крива и настройки.</p></details>
    </div>;
  })}</section>;
}
