import { shiftDay } from "./planning-timeline";

const iso = (date: Date) => date.toISOString().slice(0, 10);

/** Display dates only: never change the programme or microcycle anchor. */
export function planningWindow(today: string, view: "month" | "year", page: number, history = false) {
  const base = new Date(`${today}T12:00:00Z`);
  const step = view === "year" ? 12 : 1;
  const offset = history ? -1 - page : page;
  const boundary = (n: number) => iso(new Date(Date.UTC(base.getUTCFullYear(), base.getUTCMonth() + n * step,
    view === "year" ? base.getUTCDate() : 1, 12)));
  const start = history ? boundary(offset) : [today, boundary(offset)].sort().at(-1)!;
  const end = history ? [shiftDay(today, -1), shiftDay(boundary(offset + 1), -1)].sort()[0] : shiftDay(boundary(offset + 1), -1);
  const months: string[] = [];
  for (const date = new Date(`${start.slice(0, 7)}-01T12:00:00Z`); iso(date) <= end; date.setUTCMonth(date.getUTCMonth() + 1)) {
    months.push(iso(date).slice(0, 7));
  }
  return { start, end, months };
}
