import Link from "next/link";
import { durationHms } from "../lib/duration-format";
import { isRecord } from "../lib/training-status";

const minutes = (v: unknown): number | null => typeof v === "number" && Number.isFinite(v) && v >= 0 ? v : null;

export function timeLimits(context: Record<string, unknown>) {
  const controls = isRecord(context.planning_controls) ? context.planning_controls : {};
  const hours = minutes(controls.weekly_target_hours);
  const weekly = minutes(context.weekly_time_limit_minutes) ?? (hours === null ? null : hours * 60);
  const daily = context.availability_mode === "MANUAL" ? minutes(context.available_weekly_minutes) : null;
  return { weekly, daily, effective: weekly === null ? daily : daily === null ? weekly : Math.min(weekly, daily) };
}

export function TimeAvailability({ context }: { context: Record<string, unknown> }) {
  const { effective } = timeLimits(context);
  return <div><small>Лимит за време</small><strong>{effective === null ? "Автоматично" : durationHms(effective)}</strong>
    <span>{effective === null ? "без ръчно зададен лимит" : "максимум общо за 7 дни"}</span></div>;
}

export function TimeLimitNotice({ context, forecast = false }: { context: Record<string, unknown>; forecast?: boolean }) {
  const { weekly, daily } = timeLimits(context);
  const budget = isRecord(context.time_budget) ? context.time_budget : null;
  if (weekly === null && daily === null && !budget) return null;
  return <aside className="management-notice" role="status" aria-label="Ограничение за тренировъчно време">
    {weekly !== null && <p><strong>Записан седмичен таван: {durationHms(weekly)} общо.</strong> Това е максимум за всички тренировки, включително силовите; не добавя часове към програмата.</p>}
    {daily !== null && <p>Наличните минути по тренировъчни дни позволяват до {durationHms(daily)} за 7 дни. Прилагат се и дневните ограничения.</p>}
    {budget && <p>За периода {String(budget.start_date)} – {String(budget.end_date)}: лимит {durationHms(minutes(budget.period_limit_minutes) ?? 0)}; изпълнено {durationHms(minutes(budget.actual_minutes) ?? 0)}; предложено {durationHms(minutes(budget.planned_minutes) ?? 0)}; оставащо време {durationHms(minutes(budget.remaining_minutes) ?? 0)}.
      {(minutes(budget.actual_excess_minutes) ?? 0) > 0 && " Изпълненото вече надхвърля лимита. Нови сесии не се добавят в този период."}
      {budget.remaining_minutes === 0 && budget.planned_minutes === 0 && budget.actual_excess_minutes === 0 && (minutes(budget.actual_minutes) ?? 0) > 0 && " Изпълненото вече изчерпва лимита за периода."}</p>}
    {forecast && <p>Прогнозното време по-долу е еквивалент на компонентните цели преди ограниченията за време. Ако ги надхвърля, съставените сесии ще покрият само част от целите.</p>}
    <Link href="/planning">Провери „Дни и обем“ →</Link>
  </aside>;
}
