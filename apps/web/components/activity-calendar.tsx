import Link from "next/link";
import type { ActivityCalendar, ActivityCalendarItem } from "../lib/activities";

const number = new Intl.NumberFormat("bg-BG", { maximumFractionDigits: 1 });
const dateLabel = new Intl.DateTimeFormat("bg-BG", { day: "numeric", month: "short", timeZone: "UTC" });
const weekLabel = new Intl.DateTimeFormat("bg-BG", { day: "numeric", month: "short", timeZone: "UTC" });
const weekdays = ["Пон", "Вто", "Сря", "Чет", "Пет", "Съб", "Нед"];

const isoDate = (value: Date) => value.toISOString().slice(0, 10);
const utcDate = (value: string) => new Date(`${value}T00:00:00Z`);
const addDays = (value: string, days: number) => {
  const date = utcDate(value); date.setUTCDate(date.getUTCDate() + days); return isoDate(date);
};
const monday = (value: string) => {
  const date = utcDate(value); const day = (date.getUTCDay() + 6) % 7; date.setUTCDate(date.getUTCDate() - day); return isoDate(date);
};
const sunday = (value: string) => addDays(monday(value), 6);

const sportClass = (sport: string) => {
  const value = sport.toLowerCase();
  if (value.includes("ski")) return "ski";
  if (value.includes("run")) return "run";
  if (value.includes("ride") || value.includes("cycl")) return "ride";
  if (value.includes("weight") || value.includes("strength")) return "strength";
  return "other";
};

function ActivityCard({ activity }: { activity: ActivityCalendarItem }) {
  const fallback = `${activity.sport} · ${activity.local_time}`;
  return <Link className={`completed-activity-card sport-${sportClass(activity.sport)}`} href={`/activities/${activity.activity_ref}`}>
    <span className="activity-card-top"><time dateTime={activity.start_local}>{activity.local_time}</time><span>{activity.sport}</span></span>
    <strong>{activity.name || fallback}</strong>
    <span className="activity-card-metrics">
      {activity.duration_min !== null && <span>{number.format(activity.duration_min)} мин</span>}
      {activity.distance_m !== null && <span>{number.format(activity.distance_m / 1000)} km</span>}
      {activity.average_hr_bpm !== null && <span>{number.format(activity.average_hr_bpm)} HR</span>}
      {activity.canonical_training_load !== null && <span>{number.format(activity.canonical_training_load)} load</span>}
    </span>
    <span className="activity-card-badges">
      {activity.quality_status !== "valid" && <span className={`quality-badge ${activity.quality_status}`}>{activity.quality_status === "limited" ? "Ограничено" : activity.quality_status === "excluded" ? "Изключено" : "Липсва при източника"}</span>}
      {activity.shadow_available && <span className="shadow-badge">Shadow</span>}
    </span>
  </Link>;
}

function WeekSummary({ calendar, weekStart }: { calendar: ActivityCalendar; weekStart: string }) {
  const fromApi = calendar.weeks.find((week) => week.week_start === weekStart);
  const activities = calendar.activities.filter((activity) => activity.local_date >= weekStart && activity.local_date <= sunday(weekStart));
  const duration = fromApi?.duration_min ?? activities.reduce((sum, activity) => sum + (activity.duration_min ?? 0), 0);
  const distance = fromApi?.distance_m ?? activities.reduce((sum, activity) => sum + (activity.distance_m ?? 0), 0);
  const load = fromApi?.canonical_training_load ?? activities.reduce((sum, activity) => sum + (activity.canonical_training_load ?? 0), 0);
  const zones = fromApi?.zones ?? ["Z1", "Z2", "Z3", "Z4", "Z5"].map((zone) => ({
    zone: zone as "Z1" | "Z2" | "Z3" | "Z4" | "Z5",
    raw_time_s: activities.flatMap((activity) => activity.zones).filter((item) => item.zone === zone).reduce((sum, item) => sum + item.raw_time_s, 0),
    equivalent_time_s: 0, effective_load: 0,
  }));
  const totalZone = zones.reduce((sum, zone) => sum + zone.raw_time_s, 0);
  return <aside className="completed-week-summary" aria-label={`Обобщение за седмицата от ${weekStart}`}>
    <strong>{weekLabel.format(utcDate(weekStart))} — {weekLabel.format(utcDate(sunday(weekStart)))}</strong>
    <span>{activities.length} активности</span><span>{number.format(duration / 60)} ч</span><span>{number.format(distance / 1000)} km</span><span>{number.format(load)} load</span>
    <span className="week-zone-bar" aria-label="Реално HR време по зони">
      {zones.map((zone) => <i key={zone.zone} className={zone.zone.toLowerCase()} style={{ flexGrow: totalZone ? zone.raw_time_s / totalZone : 0 }} title={`${zone.zone}: ${number.format(zone.raw_time_s / 60)} мин`} />)}
    </span>
  </aside>;
}

export function ActivityCalendarView({ calendar }: { calendar: ActivityCalendar }) {
  const gridStart = monday(calendar.period_start);
  const gridEnd = sunday(calendar.period_end);
  const days: string[] = [];
  for (let day = gridStart; day <= gridEnd; day = addDays(day, 1)) days.push(day);
  const byDate = new Map<string, ActivityCalendarItem[]>();
  for (const activity of calendar.activities) byDate.set(activity.local_date, [...(byDate.get(activity.local_date) ?? []), activity]);
  const weeks = Array.from({ length: Math.ceil(days.length / 7) }, (_, index) => days.slice(index * 7, index * 7 + 7));
  return <section className="completed-calendar" aria-label="Календар на завършените активности">
    <div className="calendar-weekdays" aria-hidden="true">{weekdays.map((day) => <span key={day}>{day}</span>)}</div>
    <div className="completed-calendar-grid">
      {weeks.map((week) => <section className="completed-week" key={week[0]}>
        <WeekSummary calendar={calendar} weekStart={week[0]} />
        {week.map((day) => <div key={day} className={`completed-day ${day < calendar.period_start || day > calendar.period_end ? "outside-period" : ""}`}>
          <header><time dateTime={day}>{dateLabel.format(utcDate(day))}</time><span>{byDate.get(day)?.length || ""}</span></header>
          <div className="day-activities">{(byDate.get(day) ?? []).map((activity) => <ActivityCard key={activity.activity_ref} activity={activity} />)}</div>
        </div>)}
      </section>)}
    </div>
  </section>;
}
