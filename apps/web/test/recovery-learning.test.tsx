// @vitest-environment jsdom
import { act } from "react";
import { createRoot } from "react-dom/client";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { renderToStaticMarkup } from "react-dom/server";
import { learningIndexPaths, learningQ3Paths, learningLoadSegments, learningPaths, RecoveryLearning } from "../components/recovery-learning-view";
import { LEARNING_COMPONENTS, LEARNING_PREPARING_MESSAGE, LearningPreparingError, parseRecoveryLearningView, unknownComponentQ, type RecoveryLearningView } from "../lib/recovery-learning";
import { currentAuthorizedAthlete } from "../lib/account-access";
import { getLearningView } from "../lib/api";
import LearningPage from "../app/learning/page";

vi.mock("../lib/account-access", () => ({ currentAuthorizedAthlete: vi.fn() }));
vi.mock("../lib/api", () => ({ getLearningView: vi.fn() }));
vi.mock("../lib/api-readiness",()=>({waitForApi:vi.fn().mockResolvedValue(undefined),markApiUnavailable:vi.fn()}));

const legacy = {
  schema_version: "recovery-learning-view-v1", as_of: "2026-11-03", source_date: "2026-11-02", timezone: "Europe/Sofia",
  mode: "SHADOW", status: "COLLECTING", summary: "Нужни са още съпоставими реакции.", evidence_count: 0,
  validation: { status: "WARMUP", evaluated: 0 },
  series: [
    { day: "2026-11-01", actual_q: 100, stress: 65, baseline: 45 },
    { day: "2026-11-02", actual_q: null, stress: null, baseline: 45 },
    { day: "2026-11-03", actual_q: 0, stress: 45, baseline: 45 },
  ],
  episodes: [{ id: "block-1", start: "2026-10-13", load_end: "2026-10-26", end: "2026-11-03", loading_days: 14,
    recovery_limit_days: 5, recovery_days: null, recovered: false, ratio: null, status: "INSUFFICIENT_DATA",
    proposed_change_percent: 0, reason: "Липсват последователни наблюдения.", performance_change_percent: null }],
  decision: { component: "Z1", status: "HOLD", proposed_change_percent: 4, applied_change_percent: 0, reason: "Следим предложението преди приложение." },
  observations: { load_days: 2, stress_days: 2 }, channels: [{ key: "hrv", label: "HRV", observed_days: 2 },{key:"resting_hr",label:"Пулс в покой",observed_days:2}], limitations: [],
};
const view: RecoveryLearningView = parseRecoveryLearningView({ ...legacy,
  series:legacy.series.map((row,index)=>({...row,
    q3:[100,60,40][index],q3_by_component:Object.fromEntries(LEARNING_COMPONENTS.map((key,i)=>[key,[[40,25,15,10,6,4],[26,16.5,7.5,5,3,2],[17.333,11,5,3.333,2,1.333]][index][i]])),q3_observed_days:Object.fromEntries(LEARNING_COMPONENTS.map(key=>[key,index+1])),q3_partial:index!==0,
    actual_q_by_component:index===0?{Z1:40,Z2:25,Z3:15,Z4:10,Z5:6,STR:4}:index===1?unknownComponentQ():{Z1:0,Z2:0,Z3:0,Z4:0,Z5:0,STR:0},
    index_7_40_by_component:Object.fromEntries(LEARNING_COMPONENTS.map((key,componentIndex)=>[key,1+componentIndex*.1+index*.1])),
    recorded_q_by_component:index===0?{Z1:40,Z2:25,Z3:15,Z4:10,Z5:6,STR:4}:index===1?{Z1:12,Z2:8,Z3:0,Z4:0,Z5:0,STR:0}:{Z1:0,Z2:0,Z3:0,Z4:0,Z5:0,STR:0},
    load_status:index===1?"PARTIAL":index===2?"REST":"COMPLETE",load_reason:index===1?"Има активност с непълна разбивка.":null,
    load_activity_count:index===2?0:1,load_unknown_minutes:index===1?30:0,learning_load_eligible:index!==1,
    index_load_status:index===0?"COMPLETE":"PARTIAL",index_history_days:50,
    stress_weights:index===1?{}:{hrv:60,resting_hr:40},stress_basis:index===1?null:"FROZEN_BLOCK",stress_reference:index===1?null:"actual:block-1"})),
  components:LEARNING_COMPONENTS.map((component,index)=>({component,q3:20-index*3,q3_observed_days:3,recent_weekly_q:140-index*20,reference_weekly_q:100-index*10,
    recent_observed_days:7,reference_observed_days:40,accent_ratio:(140-index*20)/(100-index*10),evidence_count:0,isolated_changes:0,
    validation_status:"WARMUP",response_status:"INSUFFICIENT_DATA",model_scope:null,test_step_percent:5,predicted_performance_percent:null,predicted_performance_low:null,predicted_performance_high:null,
    proposed_change_percent:index===0?4:null,applied_change_percent:index===0?0:null,action_status:index===0?"SELECTED":"COLLECTING",management_basis:index===0?"EXPLORATION":"HOLD",
    baseline_actual_weekly_q:index===0?100:null,target_weekly_q:index===0?104:null,execution_weekly_q_ceiling:null,effective_from:index===0?"2026-11-04":null,expires_on:index===0?"2026-11-17":null,
    reason:"Нужни са още наблюдения за отделима реакция."})),
  channels:legacy.channels.map((channel,index)=>({...channel,nominal_weight:index===0?18:12})),
});

describe("dedicated recovery learning presentation", () => {
  it("keeps chosen components and total in every period submission with an explicit history boundary",async()=> {
    vi.stubGlobal("IS_REACT_ACT_ENVIRONMENT",true);
    const container=document.createElement("div");document.body.append(container);
    const root=createRoot(container);
    const historical={...view,active_components:["Z1","Z3"] as const,history:{available_start:"2026-08-07",available_end:"2026-11-03",display_start:"2025-11-04",display_end:"2026-11-03",requested_days:366,limited:true}};
    try{
      await act(async()=>root.render(<RecoveryLearning view={{...historical,active_components:[...historical.active_components]}} period={{days:366,end:"2026-11-03",total:false}}/>));
      expect(container.querySelectorAll('svg g[data-component]')).toHaveLength(2);
      const form=container.querySelector('form[aria-label="Период на графиката"]')!;
      const presets=form.querySelector('[aria-label="Бърз избор на период"]')!;
      expect([...presets.querySelectorAll('button')].map(b=>[b.textContent,b.getAttribute('value')])).toEqual([["14 дни","14"],["1 месец","30"],["2 месеца","60"],["3 месеца","90"],["1 година","366"]]);
      expect(form.textContent).toContain("Наличната история е 07.08.2026 – 03.11.2026");
      expect(form.textContent).toContain("Текущата оценка и предложението са към 03.11.2026");
      const z2=[...container.querySelectorAll('.learning-zone-key button')].find(b=>b.textContent==='Z2') as HTMLButtonElement;
      await act(async()=>z2.click());
      expect((form.querySelector('input[name=components]') as HTMLInputElement).value).toBe('Z2');
      const total=container.querySelector('.learning-total-toggle input') as HTMLInputElement;
      await act(async()=>total.click());
      expect((form.querySelector('input[name=total]') as HTMLInputElement).value).toBe('1');
      expect((form.querySelector('input[type=date]') as HTMLInputElement).max).toBe(view.as_of);
    }finally{await act(async()=>root.unmount());container.remove();vi.unstubAllGlobals();}
  });
  it("reduces yearly point markers without changing the daily Q3 curve",()=>{
    const rows=Array.from({length:365},(_,i)=>({...view.series[0],day:new Date(Date.UTC(2025,10,4+i)).toISOString().slice(0,10)}));
    const html=renderToStaticMarkup(<RecoveryLearning view={{...view,series:rows}}/>);
    expect((html.match(/<circle/g)??[]).length).toBeLessThanOrEqual(7);
    expect(html).toContain('learning-index-line');
  });
  it("preserves additive transport fields and keeps legacy unknown components unknown", () => {
    const parsed = parseRecoveryLearningView({ ...view, memory: { private_data: "private replay" } });
    expect(parsed.series[0].actual_q_by_component).toEqual({Z1:40,Z2:25,Z3:15,Z4:10,Z5:6,STR:4});
    expect(parsed.series[0].stress_weights).toEqual({hrv:60,resting_hr:40});
    expect(parsed.series[0].stress_reference).toBe("actual:block-1");
    expect(parsed.channels[0].nominal_weight).toBe(18);
    expect(parsed.components[0]).toMatchObject({target_weekly_q:104,management_basis:"EXPLORATION",predicted_performance_percent:null});
    expect(parsed.series[1].stress).toBeNull();
    expect(parsed.series[1].actual_q).toBeNull();
    expect(parsed.source_date).toBe("2026-11-02");
    expect(parsed).not.toHaveProperty("memory");
    const old=parseRecoveryLearningView(legacy);
    expect(old.series[0].actual_q_by_component).toEqual(unknownComponentQ());
    expect(old.series[0].index_7_40_by_component).toEqual(unknownComponentQ());
    expect(old.series[0].load_status).toBe("UNAVAILABLE");
    expect(old.series[0].learning_load_eligible).toBe(false);
    expect(old.components).toHaveLength(6);
    expect(old.components.every(item=>item.recent_weekly_q===null&&item.proposed_change_percent===null)).toBe(true);
    const partial=parseRecoveryLearningView({...legacy,series:[{...legacy.series[0],actual_q_by_component:{Z1:0}}]});
    expect(partial.series[0].actual_q_by_component).toEqual({...unknownComponentQ(),Z1:0});
  });
  it("rejects impossible report values, invalid panels and multiple simultaneous choices", () => {
    for(const patch of [
      {decision:{...view.decision,applied_change_percent:4}},
      {decision:{...view.decision,proposed_change_percent:20.1}},
      {series:[...view.series,view.series[0]]},
      {series:[{...view.series[0],stress:NaN}]},
      {series:[{...view.series[0],day:"2026-11-04"}]},
      {series:[{...view.series[0],actual_q_by_component:{...view.series[0].actual_q_by_component,Z1:Infinity}}]},
      {series:[{...view.series[0],stress_weights:{hrv:60,resting_hr:30}}]},
      {series:[{...view.series[0],index_7_40_by_component:{Z1:Infinity}}]},
      {series:[{...view.series[0],index_load_status:"UNKNOWN"}]},
      {series:[{...view.series[0],load_status:"NO_TRAINING_ASSUMED"}]},
      {series:[{...view.series[0],load_unknown_minutes:-1}]},
      {components:[view.components[0],...view.components.slice(0,5)]},
      {components:view.components.slice(1)},
      {components:view.components.map((item,index)=>index===1?{...item,action_status:"SELECTED",proposed_change_percent:3,applied_change_percent:0}:item)},
      {components:view.components.map((item,index)=>index===0?{...item,recent_observed_days:0}:item)},
    ]) expect(()=>parseRecoveryLearningView({...view,...patch})).toThrow();
  });
  it("shows prepared three-day values and accepts preliminary intervals without eight-result gating",()=>{
    expect(view.series[1].q3_by_component.Z1).toBe(26);
    expect(learningQ3Paths(view.series,"Z1",true,i=>i,v=>v)).toEqual(["M0,40 L1,26 L2,17.333 "]);
    const preliminary={...view.components[0],evidence_count:1,isolated_changes:0,validation_status:"WARMUP",model_scope:"GLOBAL",response_status:"UNCERTAIN",predicted_performance_low:-2,predicted_performance_percent:1,predicted_performance_high:4,recent_observed_days:5};
    const parsed=parseRecoveryLearningView({...view,components:[preliminary,...view.components.slice(1)]});
    expect(parsed.components[0].predicted_performance_percent).toBe(1);
    for(const patch of [{q3:-1},{q3_by_component:{Z1:Infinity}},{q3_observed_days:{Z1:4}}]) expect(()=>parseRecoveryLearningView({...view,series:[{...view.series[0],...patch}]})).toThrow();
  });
  it("only transports supported finite model intervals and keeps their contrast distinct from actions", () => {
    const predicted={...view.components[1],evidence_count:8,isolated_changes:2,validation_status:"PASSED",model_scope:"GLOBAL",response_status:"SUPPORTED_POSITIVE",
      predicted_performance_percent:25,predicted_performance_low:10,predicted_performance_high:30};
    const report={...view,components:view.components.map(item=>item.component==="Z2"?predicted:item)};
    const parsed=parseRecoveryLearningView(report);
    expect(parsed.components[1].predicted_performance_percent).toBe(25);
    const html=renderToStaticMarkup(<RecoveryLearning view={parsed}/>);
    expect(html).toContain("Сравнение при +5% Q");
    expect(html).toContain("+25%");
    expect(html).toContain("обща работоспособност");
    expect(html).toContain("Моделен интервал");
    expect(html.match(/Предложена промяна/g)).toHaveLength(1);
    for(const invalid of [{isolated_changes:1},{validation_status:"WARMUP"},{predicted_performance_high:20},{predicted_performance_low:null}]) {
      expect(()=>parseRecoveryLearningView({...report,components:report.components.map(item=>item.component==="Z2"?{...item,...invalid}:item)})).toThrow();
    }
  });
  it("breaks stress and baseline paths across missing days or different measurement references", () => {
    expect(learningPaths(view.series,"stress",index=>index,value=>value)).toEqual(["M0,65 ","M2,45 "]);
    expect(learningPaths([view.series[0],view.series[2]],"stress",index=>index,value=>value)).toEqual(["M0,65 ","M1,45 "]);
    for(const patch of [{stress_reference:"other:block"},{stress_basis:"FIXED_PANEL" as const},{stress_weights:{hrv:50,resting_hr:50}}]) {
      const next={...view.series[0],day:"2026-11-02",...patch};
      for(const kind of ["stress","baseline"] as const) expect(learningPaths([view.series[0],next],kind,index=>index,value=>value)).toHaveLength(2);
    }
  });
  it("keeps known daily Q distinct from canonical 7/40 and incomplete coverage", () => {
    const segments=learningLoadSegments(view.series[0]);
    expect(segments.map(item=>item.end)).toEqual([40,65,80,90,96,100]);
    expect(learningLoadSegments(view.series[1])).toEqual([]);
    expect(view.series[1].recorded_q_by_component.Z1).toBe(12);
    expect(view.series[1].actual_q_by_component.Z1).toBeNull();
    expect(view.series[1].index_7_40_by_component.Z1).toBe(1.1);
    expect(learningIndexPaths(view.series,"Z1",false,index=>index,value=>value)).toEqual(["M0,1 "]);
    expect(learningIndexPaths(view.series,"Z1",true,index=>index,value=>value)).toEqual(["M1,1.1 L2,1.2 "]);
    const missing={...view.series[1],index_7_40_by_component:unknownComponentQ()};
    expect(learningIndexPaths([view.series[0],missing,view.series[2]],"Z1",true,index=>index,value=>value)).toEqual(["M2,1.2 "]);
    const html=renderToStaticMarkup(<RecoveryLearning view={view}/>);
    for(const component of LEARNING_COMPONENTS) expect(html).toContain(`data-component="${component}"`);
    expect(html.match(/<svg/g)).toHaveLength(1);
    expect(html).toContain("Една времева ос · две отделни скали");
    expect(html).toContain("Липсваща стойност не е нулево натоварване");
    expect(html).toContain("Тридневно натоварване и интегрален стрес");
    expect(html).toContain("Покрит ден без отчетена тренировка");
    expect(html).toContain("Следваща промяна · Z1");
    expect(html).toContain('href="#component-Z1"');
  });
  it("isolates a canonical component while retaining stress and inspects both at the same date",async()=> {
    vi.stubGlobal("IS_REACT_ACT_ENVIRONMENT",true);
    const container=document.createElement("div");document.body.append(container);
    const root=createRoot(container);
    try {
      await act(async()=>root.render(<RecoveryLearning view={view}/>));
      const z1=container.querySelector('[aria-labelledby="component-Z1"]')!;
      expect(z1.querySelectorAll("dd")[2].textContent).toContain("20");
      expect(z1.querySelectorAll("dd")[2].textContent).toContain("3/3 дни");
      const group=container.querySelector('[aria-label="Компоненти в графиката"]')!;
      const strength=[...group.querySelectorAll("button")].find(button=>button.textContent==="Сила")!;
      expect(container.querySelectorAll('svg g[data-component]')).toHaveLength(6);
      await act(async()=>strength.click());
      expect(strength.getAttribute("aria-pressed")).toBe("true");
      expect(container.querySelector('svg g[data-component="Z1"]')).toBeNull();
      expect(container.querySelector('svg g[data-component="STR"]')).not.toBeNull();
      expect(container.querySelector('svg .learning-stress-line')).not.toBeNull();
      const svg=container.querySelector("svg")!;
      vi.spyOn(svg,"getBoundingClientRect").mockReturnValue({left:0,right:600,width:600,height:266,top:0,bottom:266,x:0,y:0,toJSON:()=>({})});
      await act(async()=>{svg.dispatchEvent(new MouseEvent("pointerdown",{clientX:0,bubbles:true}));});
      const inspection=container.querySelector(".learning-chart-inspection")!;
      expect(inspection.textContent).toContain("01.11.2026");
      expect(inspection.textContent).toContain("Стрес 65");
      expect(inspection.textContent).toContain("Сила 4 Q/ден");
      expect(inspection.textContent).toContain("Пълно отчетено натоварване");
      await act(async()=>group.querySelector("button")!.click());
      expect(container.querySelectorAll('svg g[data-component]')).toHaveLength(6);
      expect(strength.getAttribute("aria-pressed")).toBe("true");
      await act(async()=>{svg.dispatchEvent(new MouseEvent("pointerdown",{clientX:300,bubbles:true}));});
      expect(inspection.textContent).toContain("02.11.2026");
      expect(inspection.textContent).toContain("Частично отчетено натоварване");
      expect(inspection.textContent).toContain("Z1 12");
      expect(inspection.textContent).toContain("30 мин без разпределение");
      expect(inspection.textContent).not.toContain("Покрит ден без отчетена тренировка");
    } finally {await act(async()=>root.unmount());container.remove();vi.unstubAllGlobals();}
  });
  it("shows one integral stress panel, six honest assessments and only the selected proposed dose", () => {
    const html=renderToStaticMarkup(<RecoveryLearning view={view}/>);
    for(const text of ["Наблюдение","Q/ден · 3 дни","Стрес · 0–100","Предложена промяна","+4%","Допуснато от модела","0%","Недостатъчни наблюдения","Няма съпоставим резултат",'href="/response"',"Проучване","База на съпоставимия блок: 100 Q/седм.","цел 104 Q/седм."]) expect(html).toContain(text);
    expect(html.match(/class="learning-component(?: is-selected)?"/g)).toHaveLength(6);
    expect(html.match(/class="learning-response-label">Събира данни/g)).toHaveLength(6);
    expect(html.match(/Предложена промяна/g)).toHaveLength(1);
    expect(html.match(/Допуснато от модела/g)).toHaveLength(1);
    expect(html.match(/class="learning-stress-line"/g)).toHaveLength(2);
    expect(html).not.toContain("NaN");
    expect(html).not.toContain(' open=""');
    expect(html).not.toContain("Слаб отговор");
  });
  it("dates weights to the latest observed stress assessment even when the final graph day is missing", () => {
    const html=renderToStaticMarkup(<RecoveryLearning view={view}/>);
    for(const text of ["18 т.","60%","Един и същ набор за проследения блок","Показатели и тегла за 03.11.2026","не са процент увереност"]) expect(html).toContain(text);
    const missing={...view,series:[view.series[0],{...view.series[2],stress:null,stress_weights:{},stress_basis:null,stress_reference:null}]};
    const prior=renderToStaticMarkup(<RecoveryLearning view={missing}/>);
    expect(prior).toContain("Последна оценка на стреса · 01.11.2026");
    expect(prior).toContain("Показатели и тегла за 01.11.2026");
    expect(prior).toContain(">60%<");
    expect(prior).not.toContain("Последна оценка на стреса · 03.11.2026");
    const empty=renderToStaticMarkup(<RecoveryLearning view={{...missing,series:[missing.series[1]]}}/>);
    expect(empty).toContain("За този ден липсва пълен набор");
    expect(empty).toContain("Няма наличен набор показатели");
    expect(empty).not.toContain(">60%<");
    expect(empty).not.toContain("Последна оценка на стреса");
  });
  it("shows an honest empty state and keeps missing component values as dashes", () => {
    const parsed=parseRecoveryLearningView({...legacy,series:[],episodes:[],channels:[]});
    const html=renderToStaticMarkup(<RecoveryLearning view={parsed}/>);
    for(const text of ["Няма годни измерени данни","Няма достатъчно независими наблюдения","Няма завършен съпоставим блок","Събира данни","—"]) expect(html).toContain(text);
    expect(html).not.toContain("<svg");
    expect(html).not.toContain("Предложена промяна");
  });
  it("does not equate recovery-policy reduction with weak performance response", () => {
    const reduced={...view,mode:"CONTROL" as const,decision:{...view.decision,proposed_change_percent:-10,applied_change_percent:-10},components:view.components.map((item,index)=>index===0?{...item,proposed_change_percent:-10,applied_change_percent:-10,management_basis:"POLICY_REDUCTION" as const}:item),evidence_count:2,observations:{reaction_episodes:2,performance_observations:0}};
    const html=renderToStaticMarkup(<RecoveryLearning view={reduced}/>);
    expect(html).toContain("Правило за намаляване");
    expect(html).toContain("Още няма съпоставим резултат за оценка на тренировъчния ефект");
    expect(html).not.toContain("Отрицателна връзка");
  });
  it("distinguishes no stress elevation from delayed recovery in both recovery views", () => {
    const summary="Основанието за текущото решение се показва веднъж.";
    const labels={NO_ELEVATED_RESPONSE:"Няма отчетено повишение на стреса",DELAYED:"Няма потвърдено връщане в експертния срок"};
    for(const [status,label] of Object.entries(labels)) {
      const html=renderToStaticMarkup(<RecoveryLearning view={{...view,summary,decision:{...view.decision,reason:summary},episodes:[{...view.episodes[0],status,reason:"Описание на наблюдението."}]}}/>);
      expect(html.split(label)).toHaveLength(3);
      expect(html).not.toContain(labels[status==="DELAYED"?"NO_ELEVATED_RESPONSE":"DELAYED"]);
      expect(html).not.toContain("Връщането още не е потвърдено");
      expect(html.split(summary)).toHaveLength(2);
    }
  });
});

describe("learning API transport",()=> {
  it("passes the private alias and preserves new fields from the prepared response",async()=> {
    const actual=await vi.importActual<typeof import("../lib/api")>("../lib/api");
    vi.stubEnv("ONFLOWS_API_BASE_URL","https://api.example.test");
    const fetch=vi.fn().mockResolvedValue(new Response(JSON.stringify(view),{headers:{"content-type":"application/json"}}));
    vi.stubGlobal("fetch",fetch);
    try {
      const result=await actual.getLearningView("private-athlete",30);
      expect(String(fetch.mock.calls[0][0])).toBe("https://api.example.test/api/v2/athlete/learning?days=30");
      expect(fetch.mock.calls[0][1].headers["X-OnFlows-Athlete-Alias"]).toBe("private-athlete");
      expect(result.components[0].target_weekly_q).toBe(104);
      expect(result.series[0].actual_q_by_component.STR).toBe(4);
    } finally {vi.unstubAllGlobals();vi.unstubAllEnvs();}
  });
  it("recognizes the preparing response before infrastructure retries",async()=> {
    const actual=await vi.importActual<typeof import("../lib/api")>("../lib/api");
    vi.stubEnv("ONFLOWS_API_BASE_URL","https://api.example.test");
    const fetch=vi.fn().mockResolvedValue(new Response(JSON.stringify({detail:LEARNING_PREPARING_MESSAGE}),{status:503,headers:{"content-type":"application/json","Retry-After":"15"}}));
    vi.stubGlobal("fetch",fetch);
    try {await expect(actual.getLearningView("private-athlete")).rejects.toBeInstanceOf(LearningPreparingError);expect(fetch).toHaveBeenCalledTimes(1);}
    finally {vi.unstubAllGlobals();vi.unstubAllEnvs();}
  });
});

describe("private learning page", () => {
  const access = { userId:"owner",actorUserId:"coach",athleteAlias:"ath-private",displayName:"Избран спортист",isOwner:false,canEditPlan:true,canViewRecovery:true };
  beforeEach(()=>{vi.clearAllMocks();vi.mocked(currentAuthorizedAthlete).mockResolvedValue(access);vi.mocked(getLearningView).mockResolvedValue(view);});
  it("blocks a plan-only coach before fetching private observations",async()=> {
    vi.mocked(currentAuthorizedAthlete).mockResolvedValue({...access,canViewRecovery:false});
    const html=renderToStaticMarkup(await LearningPage({searchParams:Promise.resolve({})}));
    expect(getLearningView).not.toHaveBeenCalled();
    expect(html).toContain("не е споделил данните си за възстановяване");
    expect(html).not.toContain("block-1");
    expect(html).not.toContain("Интегрален стрес");
  });
  it("keeps the calculation date separate from the imported history cutoff",async()=> {
    const html=renderToStaticMarkup(await LearningPage({searchParams:Promise.resolve({days:"180"})}));
    expect(getLearningView).toHaveBeenCalledWith("ath-private",30,undefined);
    expect(html).toContain("оценка към 03.11.2026");
    expect(html).toContain("Натоварване до 02.11.2026");
    expect(html).not.toContain("Последни 180 дни");
  });
  it("shows neutral preparation with a manual retry and no fabricated empty assessment",async()=> {
    vi.mocked(getLearningView).mockRejectedValue(new LearningPreparingError());
    const html=renderToStaticMarkup(await LearningPage({searchParams:Promise.resolve({days:"30"})}));
    expect(html).toContain("Подготвяме оценката във фонов режим");
    expect(html).toContain('href="/learning?days=30"');
    for(const text of ['role="alert"',"Не можем да покажем","0%","Събира данни","Няма данни"]) expect(html).not.toContain(text);
  });
});
