import { isRawMovingSpeed } from "./speed-basis";
import { speedRangeText, type RunningSpeedUnit } from "./speed-format";
import { lactateRangeText } from "./training-observations";
import type { DraftSession, SessionBlock } from "./training-management";
import { componentLabel } from "./training-visuals";

type Pair = [number, number];

function positive(value: unknown): value is number {
  return typeof value === "number" && Number.isFinite(value) && value > 0;
}

function bounds(range: unknown, value?: unknown): Pair | undefined {
  if (Array.isArray(range) && range.length === 2 && range.every(positive) && range[1] >= range[0]) return [range[0], range[1]];
  if (positive(value)) return [value, value];
}

function shortTime(seconds: number) {
  return Math.floor(seconds / 60) + ":" + String(seconds % 60).padStart(2, "0");
}

export function phaseDuration(block: SessionBlock) {
  const range = bounds(block.duration_range_seconds, block.duration_min * 60);
  if (!range) return "";
  const [low, high] = range.map(Math.round);
  if (high < 60) return (low === high ? high : low + "–" + high) + " сек.";
  if (low % 60 === 0 && high % 60 === 0) return (low === high ? high / 60 : low / 60 + "–" + high / 60) + " мин";
  return (low === high ? shortTime(high) : shortTime(low) + "–" + shortTime(high)) + " мин";
}

// A saved zone is a reference for easy movement, never a new prescribed speed.
function easyZoneSpeed(block: SessionBlock, session: DraftSession) {
  if (!["WARMUP", "COOLDOWN", "RECOVERY"].includes(block.kind) || passive(block)
    || ["PASSIVE", "WALK", "WALK_OR_JOG"].includes(block.recovery_mode_hint ?? block.recovery_type ?? "")) return;
  const profile = session.dose_evidence?.dosing_speed_zones;
  if (profile?.status !== "AVAILABLE" || profile.sport !== (block.load_sport ?? session.sport) || !Array.isArray(profile.zones)) return;
  const zone = profile.zones.find(row => row?.zone === block.zone);
  const speeds = bounds([zone?.low_kmh, zone?.high_kmh]);
  if (!speeds || !zone) return;
  const pulse = bounds(block.hr_reference_range_bpm, block.target_hr_bpm);
  const lo = zone.low_reference_hr_bpm, hi = zone.high_reference_hr_bpm;
  const offset = profile.hr_policy?.offset_bpm;
  // The saved paired profile interpolates linearly inside each zone. Apply
  // only its own recorded sport offset; do not extrapolate or mix sports.
  if (!isRawMovingSpeed(profile) && pulse && positive(lo) && positive(hi) && hi > lo
    && typeof offset === "number" && Number.isFinite(offset) && pulse[0] + offset >= lo && pulse[1] + offset <= hi) {
    const at = (bpm: number) => speeds[0] + (bpm + offset - lo) / (hi - lo) * (speeds[1] - speeds[0]);
    return [at(pulse[0]), at(pulse[1])] as Pair;
  }
  return speeds;
}

export function shortWork(block: SessionBlock) {
  if (block.kind !== "WORK" || block.zone === "STR" || block.zone === "NMS") return false;
  if (block.primary_control === "HRMAX" || block.hr_role === "PRIMARY_SOURCE_PRESCRIPTION") return false;
  // Same short-effort boundary as the method library's SECONDARY_OBSERVATION_WITH_LAG.
  const seconds = bounds(block.duration_range_seconds, block.duration_min * 60)?.[1];
  const paced = !!bounds(block.target_speed_range_kmh, block.target_speed_kmh) || block.repetition != null
    || ["Z3", "Z4", "Z5"].includes(block.zone) || block.primary_control === "EFFORT_AND_QUALITY";
  return block.hr_role === "SECONDARY_OBSERVATION_WITH_LAG" || (paced && seconds !== undefined && seconds < 180);
}

export function savedInstruction(block: SessionBlock, session: DraftSession) {
  const reference = /^Виж част (\d+)\.$/.exec(block.instructions);
  return reference ? session.blocks[Number(reference[1]) - 1]?.instructions ?? "" : block.instructions;
}

export function recoveryCue(block: SessionBlock, session: DraftSession) {
  if (block.kind !== "RECOVERY" && block.kind !== "TRANSITION") return;
  // A target or HR range for active rest does not specify when to restart.
  // Retain a written restart condition verbatim, including its comparator.
  const instruction = savedInstruction(block, session);
  const hasRestart = instruction?.split(/(?<=[.!?])\s+(?=[А-ЯA-Z])/u).some(sentence =>
    /пулс|сърдечна честота/iu.test(sentence) && /когато|докато|щом|след като|при спад|до спад|изчакай|(?:поднови|следващата).{0,45}при/iu.test(sentence)
    && /спад|падне|пониж|слезе|достиг|върне|(?:под|до|≤|<)\s*\d/iu.test(sentence));
  return hasRestart ? instruction : undefined;
}

export function phaseIntensity(block: SessionBlock, session: DraftSession, unit: RunningSpeedUnit): string[] {
  const restart = recoveryCue(block, session);
  const values: string[] = restart ? [restart] : [];
  if (passive(block) || block.zone === "STR" || block.zone === "NMS") return values;
  const hr = bounds(block.hr_reference_range_bpm, block.target_hr_bpm);
  if (hr && !shortWork(block) && block.kind !== "RECOVERY" && block.kind !== "TRANSITION") {
    const [low, high] = hr.map(Math.round);
    values.push((block.primary_control === "HRMAX" ? "пулс " : "пулсов ориентир ") + (low === high ? low : low + "–" + high) + " уд./мин");
  }
  const prescribed = bounds(block.target_speed_range_kmh, block.target_speed_kmh);
  const speed = prescribed ?? easyZoneSpeed(block, session);
  if (speed) {
    const basis = prescribed ? block : session.dose_evidence.dosing_speed_zones;
    const qualifier = isRawMovingSpeed(basis) ? " в движение (начална оценка)"
      : basis?.speed_basis === "ACTUAL" ? "" : " на равно";
    const prefix = prescribed ? "" : "ориентир за " + block.zone + " ";
    values.push(prefix + speedRangeText(speed, unit, block.load_sport ?? session.sport) + qualifier);
  }
  const lactate = block.lactate_reference;
  if (lactate && (positive(lactate.low_mmol) || positive(lactate.high_mmol))) {
    const source = lactate.source === "GENERAL_OLT_2024" ? "общ ориентир" : lactate.label || "ориентир";
    values.push("лактат " + lactateRangeText(lactate) + " (" + source.toLocaleLowerCase("bg") + ")");
  }
  if (lactate?.source === "INDIVIDUAL_REQUIRED" && !positive(lactate.low_mmol) && !positive(lactate.high_mmol)) {
    values.push("без зададен индивидуален лактатен диапазон");
  }
  return values;
}

export interface WorkoutPhase {
  block: SessionBlock;
  count: number;
  between?: SessionBlock;
}

function passive(block: SessionBlock) {
  return block.passive_rest === true || block.recovery_type === "PASSIVE" || /^пасивна почивка/i.test(block.label);
}

export function phaseName(block: SessionBlock): string {
  if (block.exercise_id) return block.label.split(" · ")[0];
  if (block.kind === "WARMUP") return "Загрявка";
  if (block.kind === "COOLDOWN") return "Разпускане";
  if (block.kind === "PREPARATION") return block.label || "Подготвителни упражнения";
  if (block.kind === "NEUROMUSCULAR") return block.label.replace(/\s+\d+\/\d+$/, "") || "Ускорение";
  if (block.kind === "RECOVERY" && passive(block)) return block.label || "Пасивна почивка";
  if (block.kind === "RECOVERY" && /преход|серии|кръгове|ходене/i.test(block.label)) return block.label;
  if (block.kind === "RECOVERY") return block.label || "Активна почивка";
  return block.label.replace(/(отсечка|повторение|работна част)\s+\d+\/\d+/giu, "$1")
    .replace(/\s+\d+\/\d+$/, "") || "Основна работа";
}

// Only collapse adjacent equal prescriptions. A differing rest, set, method,
// target or instruction starts a new row. The graph always uses the full list.
export type PhaseGroup = { repeats: number; phases: WorkoutPhase[] };

export function workoutStructure(session: DraftSession): PhaseGroup[] {
  const saved = session.blocks;
  const blocks = saved.filter(block => positive(block.duration_min));
  const signatures = blocks.map(block => {
    const instruction = savedInstruction(block, session);
    const timedRest = block.kind === "RECOVERY" || block.kind === "TRANSITION";
    const hr = shortWork(block) || timedRest ? undefined : bounds(block.hr_reference_range_bpm, block.target_hr_bpm);
    const speed = bounds(block.target_speed_range_kmh, block.target_speed_kmh) ?? easyZoneSpeed(block, session);
    return JSON.stringify([block.kind, block.zone, phaseName(block), block.duration_min, block.duration_range_seconds,
      hr, speed,
      block.speed_role, block.speed_basis, block.distance_basis, block.distance_range_m, block.primary_control, block.hr_role,
      block.reserve_repetitions, block.repetitions, block.exercise_id, block.combination_part,
      block.parent_method_id ?? block.parent_method_card_ref ?? block.parent_method_card?.id ?? block.method_library?.id,
      block.recovery_type, block.recovery_mode_hint, block.passive_rest, block.load_sport, instruction]);
  });
  // The archive keeps repeated lactate references only on their first block.
  // Share a reference only for an explicit repeated-instruction link and an
  // otherwise identical prescription with a single unambiguous reference.
  const references = new Map<string, Set<string>>();
  blocks.forEach((block, i) => {
    if (!block.lactate_reference) return;
    const values = references.get(signatures[i]) ?? new Set<string>();
    values.add(JSON.stringify(block.lactate_reference));
    references.set(signatures[i], values);
  });
  const keys = blocks.map((block, i) => {
    const known = references.get(signatures[i]);
    const lactate = block.lactate_reference ? JSON.stringify(block.lactate_reference)
      : /^Виж част \d+\.$/.test(block.instructions) && known?.size === 1 ? [...known][0] : undefined;
    return JSON.stringify([signatures[i], lactate]);
  });
  const phases: WorkoutPhase[] = [];
  const phaseKeys: string[] = [];
  for (let i = 0; i < blocks.length;) {
    const start = i;
    const block = blocks[i++];
    const phase: WorkoutPhase = {block, count: 1};
    if (block.kind === "WORK" || block.kind === "NEUROMUSCULAR") {
      const rest = blocks[i]?.kind === "RECOVERY" ? blocks[i] : undefined;
      const step = rest ? 2 : 1;
      while (keys[i + step - 1] === keys[start] && (!rest || keys[i] === keys[start + 1])) {
        phase.count++;
        phase.between = rest;
        i += step;
      }
    }
    phases.push(phase);
    phaseKeys.push(JSON.stringify([keys[start], phase.count, phase.between && keys[start + 1]]));
  }
  // Write repeated alternating or progressive sequences once, in execution
  // order. Keep any incomplete final sequence and any extra set rest separate.
  const groups: PhaseGroup[] = [];
  for (let start = 0; start < phases.length;) {
    let size = 1, repeats = 1;
    if (phases[start].block.kind === "WORK" || phases[start].block.kind === "NEUROMUSCULAR") {
      for (let width = 2; start + 2 * width <= phases.length; width++) {
        if (phaseKeys[start] !== phaseKeys[start + width]) continue;
        let length = width;
        while (start + length < phases.length && phaseKeys[start + length] === phaseKeys[start + length % width]) length++;
        if (Math.floor(length / width) > 1) {
          size = width; repeats = Math.floor(length / width); break;
        }
      }
    }
    groups.push({repeats, phases: phases.slice(start, start + size)});
    start += size * repeats;
  }
  return groups;
}

export function phaseHeading({block, count}: WorkoutPhase): string {
  const name = phaseName(block);
  if (count === 1) return name;
  if (block.exercise_id) return name + " · " + count + " серии";
  if (block.kind === "NEUROMUSCULAR") return name;
  if (/^(Отсечка|Работна част|Основна работа|WORK)$/i.test(name)) return "Работни отсечки";
  return name;
}

export function phaseDose({block, count}: WorkoutPhase): string {
  const time = phaseDuration(block);
  if (block.zone === "STR" && positive(block.repetitions)) {
    return (count > 1 && !block.exercise_id ? count + " × " : "") + block.repetitions + (block.repetitions === 1 ? " повторение" : " повторения") + (time ? " · около " + time + " на серия" : "");
  }
  const duration = (count > 1 ? count + " × " : "") + time;
  return passive(block) || !block.zone ? duration : duration + " · " + componentLabel(block.zone);
}
