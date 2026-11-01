import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { AthleteWorkout } from "../components/athlete-workout";
import { phaseDose, phaseIntensity, workoutStructure } from "../lib/workout-prescription";
import { workoutTitle } from "../lib/training-visuals";
import type { DraftDay, DraftSession, SessionBlock } from "../lib/training-management";

const block = (kind: string, zone: string, duration_min: number, extra: Partial<SessionBlock> = {}): SessionBlock => ({
  kind, zone, duration_min, label:kind, instructions:"", target_hr_bpm:null, target_speed_kmh:null, repetition:null, ...extra,
});
const work = block("WORK", "Z4", 70 / 60, {label:"Отсечка 1/10", repetition:1, instructions:"Запази ритъма.",
  duration_range_seconds:[60,70], target_speed_range_kmh:[14,15], hr_reference_range_bpm:[162,170], reserve_repetitions:2,
  lactate_reference:{source:"INDIVIDUAL_MANUAL",label:"Личен ориентир",assessed_on:null,low_mmol:3,high_mmol:4}});
const rest = block("RECOVERY", "Z1", .5, {label:"Активна почивка",target_speed_range_kmh:[7.5,8],hr_reference_range_bpm:[118,125]});
const warm = block("WARMUP", "Z1", 12, {duration_range_seconds:[600,720]});
const cool = block("COOLDOWN", "Z1", 5);
const session = (blocks: SessionBlock[], extra: Partial<DraftSession> = {}): DraftSession => ({
  title:"Тренировка",method_id:"test",sport:"Run",zone:"Z4",purpose:"BUILDING",blocks,
  total_minutes:blocks.reduce((a,b)=>a+b.duration_min,0),main_work_minutes:0,
  direct_equivalent_minutes:{} as DraftSession["direct_equivalent_minutes"],canonical_effective_load:{} as DraftSession["canonical_effective_load"],
  dose_evidence:{limits:[],fallback_reasons:[],explanation:""} as unknown as DraftSession["dose_evidence"], ...extra,
});
const day = {date:"2026-11-02",period:"GENERAL_PREPARATION",taper:false,cycle:{accents:["Z4"]},
  load_budget:{components:{Z4:{prescription_basis:"LONG_TERM_MICROCYCLE_Q",remaining_target_q:12}}}} as unknown as DraftDay;
const html = (s: DraftSession) => renderToStaticMarkup(<AthleteWorkout session={s} day={day}/>);

describe("the saved session as an athlete reads it", () => {
  it("shows ten identical intervals once with exactly nine active recoveries and two initially closed notes", () => {
    const blocks = [warm, ...Array.from({length:10}, (_, i) => [
      {...work, label:"Отсечка " + (i+1) + "/10", repetition:i+1,
        instructions:i ? "Виж част 2." : work.instructions, lactate_reference:i ? undefined : work.lactate_reference},
      ...(i<9 ? [rest] : []),
    ]).flat(), cool];
    const s = session(blocks);
    const before = structuredClone(s);
    const groups = workoutStructure(s);
    expect(groups.map(g=>g.phases[0].count)).toEqual([1,10,1]);
    expect(groups[1].phases[0].between).toBe(rest);
    const rendered = html(s);
    for (const text of ["10–12 мин","10 × 1:00–1:10 мин","4:00–4:17 мин/км",
      "лактат 3–4 mmol/L","30 сек.","7:30–8:00 мин/км","2 отсечки","броя на оставащите отсечки",
      "Бележки за изпълнението","Защо тази тренировка сега?","акцентът е върху работата в Z4"]) expect(rendered).toContain(text);
    expect(rendered.match(/<details\b/g)).toHaveLength(2);
    expect(rendered).not.toMatch(/<details[^>]*\bopen\b/);
    expect(rendered).not.toContain("162–170 уд./мин");
    expect(rendered).not.toContain("118–125 уд./мин");
    expect(rendered).not.toContain("Виж част");
    expect(rendered).not.toContain("Приложени ограничения");
    expect(s).toEqual(before);
  });

  it("keeps changing targets, different rests, reordered components and a real final recovery", () => {
    const faster = {...work,target_speed_range_kmh:[15,16] as [number,number]};
    const long = {...rest,duration_min:1.5};
    const change = {...work,lactate_reference:{...work.lactate_reference!,low_mmol:4,high_mmol:5}};
    const phases = workoutStructure(session([work,rest,work,long,faster,rest,change,rest,{...change,combination_part:2},long])).flatMap(g=>g.phases);
    expect(phases.map(p=>p.count)).toEqual([2,1,1,1,1,1,1,1]);
    expect(phases[0].between).toBe(rest);
    expect(phases[1].block).toBe(long);
    expect(phases.at(-1)?.block).toBe(long);
    expect(phases.some(p=>p.block===faster)).toBe(true);
    expect(phases.some(p=>p.block===change)).toBe(true);
    expect(phaseDose({block:{...work,duration_range_seconds:[60,60]},count:1})).toBe("1 мин · Z4");
    const parts = workoutStructure(session([work,{...work,zone:"Z2"},work,{...work,zone:"Z2"},work,{...work,zone:"Z2"}]));
    expect(parts.map(p=>[p.repeats,p.phases.length])).toEqual([[3,2]]);
  });

  it("labels a same-sport saved easy-zone reference and keeps passive rests and absent measurements free of numeric targets", () => {
    const s = session([warm,rest]);
    s.dose_evidence.dosing_speed_zones = {status:"AVAILABLE",sport:"Run",speed_role:"RAW_MOVING_SPEED",zones:[{zone:"Z1",low_kmh:7,high_kmh:9}]};
    expect(phaseIntensity(warm,s,"kmh").join(" ")).toContain("ориентир за Z1 7–9 км/ч в движение (начална оценка)");
    expect(phaseIntensity(rest,s,"kmh").join(" ")).not.toContain("ориентир за Z1");
    expect(phaseIntensity({...rest,passive_rest:true},s,"kmh")).toEqual([]);
    expect(phaseIntensity({...warm,load_sport:"Ride"},s,"kmh")).toEqual([]);
    expect(phaseIntensity({...work,lactate_reference:undefined},s,"kmh").join(" ")).not.toContain("лактат");
    const limited = {...work,lactate_reference:{...work.lactate_reference!,source:"INDIVIDUAL_REQUIRED",low_mmol:null,high_mmol:null}};
    expect(phaseIntensity(limited,s,"kmh").join(" ")).toContain("без зададен индивидуален лактатен диапазон");
  });

  it("shows fifteen one-minute efforts once, and uses recovery HR only for an actual restart condition", () => {
    const one = {...work, duration_min:1, duration_range_seconds:undefined, instructions:"Запази ритъма."};
    const interval = session(Array.from({length:15}, (_, i) => {
      const effort = {...one,target_hr_bpm:160+i,hr_reference_range_bpm:[158+i,162+i] as [number,number]};
      return i < 14 ? [effort,rest] : [effort];
    }).flat());
    expect(workoutStructure(interval)[0].phases[0].count).toBe(15);
    expect(html(interval)).toContain("15 × 1 мин");
    expect(html(interval)).not.toContain("уд./мин");
    const gated = {...rest, passive_rest:true, instructions:"Започни следващата отсечка, когато пулсът спадне под 120 уд./мин."};
    expect(phaseIntensity(gated,interval,"kmh")).toEqual([gated.instructions]);
    expect(phaseIntensity({...rest,primary_control:"HRMAX"},interval,"kmh").join(" ")).not.toContain("уд./мин");
    expect(phaseIntensity({...one,duration_min:4},interval,"kmh").join(" ")).toContain("162–170 уд./мин");
    expect(phaseIntensity({...warm,target_hr_bpm:130},interval,"kmh").join(" ")).toContain("130 уд./мин");
  });

  it("narrows an easy speed reference only inside a recorded same-sport HR/speed zone", () => {
    const b = {...rest,target_speed_range_kmh:undefined,hr_reference_range_bpm:[120,124] as [number,number]};
    const s = session([b],{sport:"Ride"});
    s.dose_evidence.dosing_speed_zones = {status:"AVAILABLE",sport:"Ride",hr_policy:{offset_bpm:7},zones:[
      {zone:"Z1",low_kmh:10,high_kmh:20,low_reference_hr_bpm:100,high_reference_hr_bpm:150}]};
    expect(phaseIntensity(b,s,"kmh").join(" ")).toContain("15,4–16,2 км/ч");
    expect(phaseIntensity({...b,hr_reference_range_bpm:[140,155]},s,"kmh").join(" ")).toContain("10–20 км/ч");
  });

  it("uses actual exercise repetitions, set rests and the strength reserve", () => {
    const lift = block("WORK","STR",.4,{label:"Полуклек · серия 1/3",exercise_id:"squat",repetitions:5,reserve_repetitions:2});
    const pause = block("RECOVERY","STR",3,{label:"Почивка между сериите",passive_rest:true});
    const s = session([warm,lift,pause,lift,pause,lift,cool],{zone:"STR"});
    const rendered = html(s);
    for (const value of ["Полуклек · 3 серии","5 повторения","Почивка между сериите","Резерв от 2 повторения","същата тежест"]) expect(rendered).toContain(value);
    expect(rendered).not.toContain("броя на оставащите отсечки");
  });

  it("leaves a continuous or older fixed prescription readable without adding repeats, speed or lactate", () => {
    const s = session([block("WORK","Z2",31,{label:"Равномерна работа",target_hr_bpm:142})],{zone:"Z2",purpose:"MAINTENANCE"});
    expect(html(s)).toContain("31 мин · Z2");
    expect(html(s)).toContain("142 уд./мин");
    expect(html(s)).toContain("съкратиш основната част");
    expect(html(s)).not.toMatch(/mmol\/L|мин\/км|Работни отсечки/);
    expect(workoutTitle({...s,title:"5 повторения по 50% Tmax"})).toBe("Работа в Z2");
    expect(html(session([block("NEUROMUSCULAR","NMS",1/6,{label:"Ускорение 1/1"})]))).toContain("възстановена координация");
  });
});
