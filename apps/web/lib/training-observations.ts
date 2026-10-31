import { isCalendarDate, isRecord } from "./training-status";

export type LactateZone = "Z1" | "Z2" | "Z3" | "Z4" | "Z5";
export const LACTATE_ZONES: LactateZone[] = ["Z1", "Z2", "Z3", "Z4", "Z5"];
export interface LactateRange { low_mmol: number | null; high_mmol: number | null }
export interface LactateStage { duration_min: number; hr_bpm: number | null; speed_kmh: number | null; lactate_mmol: number }
export interface LactateProfile {
  sport: "Run" | "NordicSki" | "RollerSki"; source: "MANUAL" | "TEST"; assessed_on: string;
  device: string; protocol: string; note: string; zone_ranges: Partial<Record<LactateZone, LactateRange>>; stages: LactateStage[];
}
export interface NeuromuscularProfile {
  enabled: boolean; mode: "PROGRESSIVE_FINISH" | "SHORT_SPRINT"; repetitions: number;
  work_seconds: number; recovery_seconds: number; days: number[];
}
export const defaultNeuromuscular = (): NeuromuscularProfile => ({ enabled: false, mode: "PROGRESSIVE_FINISH", repetitions: 4, work_seconds: 10, recovery_seconds: 120, days: [1, 4] });
export interface LactateReference extends LactateRange {
  source: string; label: string; assessed_on: string | null; note?: string; source_url?: string | null;
}
export interface LactateSample {
  value_mmol: number; zone: LactateZone | null; after: "REPETITION" | "BLOCK" | "SESSION";
  repetition: number | null; delay_seconds: number | null; planned_low_mmol: number | null; planned_high_mmol: number | null;
  comparison_confirmed: boolean; note: string; comparison?: string; difference_mmol?: number | null;
}
export interface NeuromuscularReport {
  repetitions: number; work_seconds: number; peak_speed_kmh: number | null;
  planned_repetitions: number | null; planned_work_seconds: number | null; note: string;
}
export interface NeuromuscularExposure { repetitions: number; work_seconds: number; note: string }

const finite = (v: unknown, low: number, high: number): v is number => typeof v === "number" && Number.isFinite(v) && v >= low && v <= high;
const optional = (v: unknown, low: number, high: number) => v == null || finite(v, low, high);
const integer = (v: unknown, low: number, high: number) => finite(v, low, high) && Number.isInteger(v);
const text = (v: unknown, max: number) => typeof v === "string" && v.length <= max;

export function parseTrainingObservations(value: Record<string, unknown>) {
  const profiles = value.lactate_profiles ?? [];
  const enabled = value.lactate_guidance_enabled ?? true;
  if (typeof enabled !== "boolean" || !Array.isArray(profiles) || profiles.length > 3) throw new Error("Невалидни лактатни настройки.");
  const sports = new Set();
  for (const p of profiles) {
    if (!isRecord(p) || !["Run", "NordicSki", "RollerSki"].includes(String(p.sport)) || sports.has(p.sport)
      || !["MANUAL", "TEST"].includes(String(p.source)) || !isCalendarDate(p.assessed_on)
      || !text(p.device, 80) || !text(p.protocol, 250) || !text(p.note, 500) || !isRecord(p.zone_ranges)
      || !Array.isArray(p.stages) || p.stages.length > 30) throw new Error("Проверете средството, датата и описанието на лактатния профил.");
    sports.add(p.sport);
    for (const [zone, r] of Object.entries(p.zone_ranges)) {
      if (!LACTATE_ZONES.includes(zone as LactateZone) || !isRecord(r) || !optional(r.low_mmol, 0, 40) || !optional(r.high_mmol, .001, 40)
        || (r.low_mmol == null && r.high_mmol == null) || (typeof r.low_mmol === "number" && typeof r.high_mmol === "number" && r.low_mmol >= r.high_mmol)) throw new Error("Въведете подредени лактатни граници в mmol/L.");
    }
    if (p.source === "MANUAL" && (!Object.keys(p.zone_ranges).length || p.stages.length)) throw new Error("Добавете поне един индивидуален лактатен ориентир.");
    if (p.source === "TEST" && (p.stages.length < 3 || !(p.protocol as string).trim())) throw new Error("Лактатният тест изисква протокол и поне три стъпала.");
    for (const s of p.stages) {
      if (!isRecord(s) || !finite(s.duration_min, .001, 60) || !optional(s.hr_bpm, 1, 250)
        || !optional(s.speed_kmh, .001, 150) || !finite(s.lactate_mmol, .001, 40)) throw new Error("Попълнете стъпалата с нарастващ пулс и реални лактатни стойности.");
    }
    if(p.source==="TEST") {
      const stages=p.stages as unknown as LactateStage[];
      const ordered=(key:"hr_bpm"|"speed_kmh")=>stages.every((s,i)=>s[key]!=null&&(i===0||stages[i-1][key]!=null&&s[key]!>stages[i-1][key]!));
      if(!ordered("hr_bpm")&&!ordered("speed_kmh"))throw new Error("Тестът изисква нарастващ пулс или скорост във всички стъпала.");
    }
  }
  const n = value.neuromuscular ?? defaultNeuromuscular();
  if (!isRecord(n) || typeof n.enabled !== "boolean" || !["PROGRESSIVE_FINISH", "SHORT_SPRINT"].includes(String(n.mode))
    || !integer(n.repetitions, 2, 20) || !integer(n.work_seconds, 5, n.mode === "SHORT_SPRINT" ? 10 : 20)
    || !integer(n.recovery_seconds, 30, 600) || (n.recovery_seconds as number) < 4 * (n.work_seconds as number)
    || !Array.isArray(n.days) || !n.days.length || new Set(n.days).size !== n.days.length || !n.days.every(d => integer(d, 0, 6))) throw new Error("Проверете повторенията, пълните почивки и дните за ускоренията.");
  return { lactate_guidance_enabled: enabled, lactate_profiles: profiles as LactateProfile[], neuromuscular: n as unknown as NeuromuscularProfile };
}

export function lactateRangeText(range: LactateRange): string {
  const f = (v: number) => v.toLocaleString("bg-BG", { maximumFractionDigits: 2 });
  if(range.low_mmol!=null&&range.low_mmol===range.high_mmol)return `около ${f(range.low_mmol)} mmol/L`;
  return range.low_mmol == null ? range.high_mmol == null ? "индивидуално" : `до ${f(range.high_mmol)} mmol/L`
    : range.high_mmol == null ? `от ${f(range.low_mmol)} mmol/L` : `${f(range.low_mmol)}–${f(range.high_mmol)} mmol/L`;
}

export function samplesFromForm(form: FormData): LactateSample[] {
  const read = (key: string) => { const v = form.get(key); return v == null || v === "" ? null : Number(v); };
  return form.getAll("lactate_row").map(String).map(id => ({
    value_mmol: Number(form.get(`la_${id}_value`)), zone: (form.get(`la_${id}_zone`) || null) as LactateZone | null,
    after: form.get(`la_${id}_after`) as LactateSample["after"], repetition: read(`la_${id}_repetition`),
    delay_seconds: read(`la_${id}_delay`), planned_low_mmol: read(`la_${id}_low`), planned_high_mmol: read(`la_${id}_high`),
    comparison_confirmed: form.get(`la_${id}_confirmed`) === "on", note: String(form.get(`la_${id}_note`) || ""),
  }));
}

export function nmsFromForm(form: FormData): NeuromuscularReport | null {
  if (form.get("nms_record") !== "on") return null;
  const read = (key: string) => { const v = form.get(key); return v == null || v === "" ? null : Number(v); };
  return { repetitions: Number(form.get("nms_repetitions")), work_seconds: Number(form.get("nms_seconds")),
    peak_speed_kmh: read("nms_speed"), planned_repetitions: read("nms_planned_repetitions"), planned_work_seconds: read("nms_planned_seconds"), note: String(form.get("nms_note") || "") };
}
