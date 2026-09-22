"use client";
import Link from "next/link";
import { useState, type FormEvent } from "react";
import { useRouter } from "next/navigation";
import type { PlanningProfile, MesocycleAccentPreferencesResponse } from "../lib/planning-profile";
import { isRecord } from "../lib/training-status";
import { trainingDays, defaultManagementProfile, defaultPlanningControls, parseManagementProfile, parseManagementProfileResponse, type ManagementProfile, type ManagementProfileResponse } from "../lib/training-management";
import { ManagementRules } from "./management-rules";
import { PlanningControlsEditor } from "./planning-controls-editor";

export function ManagementProfileEditor({ initialProfile, today, canEdit = true, legacy, legacyAccents }: { initialProfile: ManagementProfileResponse; today: string; canEdit?: boolean; legacy?: PlanningProfile|null; legacyAccents?: MesocycleAccentPreferencesResponse }) {
  const router = useRouter();
  const [saved, setSaved] = useState(initialProfile);
  const [profile, setProfile] = useState<ManagementProfile>(()=>{
    const p=initialProfile.profile??defaultManagementProfile(today);
    if(p.planning_controls) return p;
    const c=defaultPlanningControls(p.actual_sport);
    if(legacy) Object.assign(c,{sessions_per_week:Math.min(7,legacy.sessions_per_week),intensity_days:legacy.intensity_days,strength_days:legacy.strength_days,long_session_day:legacy.long_session_day,mesocycle_anchor:legacy.mesocycle_anchor_date});
    if(legacyAccents?.preferences) Object.assign(c,{accent_mode:legacyAccents.preferences.accent_mode,accent_limit:legacyAccents.preferences.accent_limit,accents:legacyAccents.preferences.manual_components});
    return {...p,planning_controls:c,training_days:trainingDays(p).filter(i=>!legacy?.rest_days.includes(i)),available_minutes:p.available_minutes.map((v,i)=>legacy?.rest_days.includes(i)?0:v)};
  });
  const [step,setStep]=useState(1), [busy,setBusy]=useState(false), [error,setError]=useState(""), [notice,setNotice]=useState("");
  const dirty=JSON.stringify(profile)!==JSON.stringify(saved.profile);
  const update=<K extends keyof ManagementProfile>(key:K,value:ManagementProfile[K])=>setProfile(p=>({...p,[key]:value}));
  const optional=(v:string)=>v===""?null:Number(v);
  async function save(e:FormEvent<HTMLFormElement>){
    e.preventDefault();setError("");setNotice("");
    if(step<4){setStep(step+1);return;}
    setBusy(true);
    try{
      const checked=parseManagementProfile({...profile,discipline:profile.discipline.trim()});
      const response=await fetch("/api/athlete/management/profile",{method:"PUT",headers:{"Content-Type":"application/json"},body:JSON.stringify({profile:checked,expected_revision:saved.revision})});
      const data:unknown=await response.json();if(!response.ok)throw new Error(isRecord(data)&&typeof data.error==="string"?data.error:"Профилът не беше записан.");
      const result=parseManagementProfileResponse(data);setSaved(result);if(result.profile)setProfile(result.profile);
      setNotice("Профилът е запазен. Дългосрочният план вече използва новите настройки. Подготви нова седмична програма, за да ги приложиш и към тренировките.");router.refresh();
    }catch(caught){setError(caught instanceof Error?caught.message:"Профилът не беше записан.");}finally{setBusy(false);}
  }
  return <section className="management-panel management-profile" id="basic-profile">
    <p className="eyebrow">{saved.configured?"Профилът е попълнен":"Начална настройка"} · стъпка {step} от 4</p>
    <nav className="planning-step-tabs" aria-label="Стъпки на профила">{["Спорт и цел","Дни и обем","Мезоцикли и акценти","Методи и дозиране"].map((s,i)=><button type="button" key={s} aria-current={step===i+1?"step":undefined} onClick={()=>setStep(i+1)}>{i+1}. {s}</button>)}</nav>
    {error&&<p className="management-error" role="alert">{error}</p>}{notice&&<p className="management-notice" role="status">{notice}</p>}
    {saved.configured&&!saved.profile?.planning_controls&&<p className="management-notice">Заредени са старите стойности. Прегледай дните, средствата и акцентите и запази пълния профил, за да използваш новото управление.</p>}
    <form onSubmit={save}><fieldset disabled={!canEdit||busy}>
    {step===1&&<><h2>1. Спорт и цел</h2><p className="management-muted">Задължителни: спорт, средство, дисциплина и период на подготовката.</p><div className="management-form-grid">
      <label>Основен спорт · задължително<select value={profile.sport} onChange={e=>{const sport=e.target.value as ManagementProfile["sport"];setProfile(p=>({...p,sport,actual_sport:sport,planning_controls:{...p.planning_controls!,training_sports:[sport]}}));}}><option value="Run">Бягане</option><option value="NordicSki">Ски бягане</option></select></label>
      <label>Основно средство · задължително<select value={profile.actual_sport} onChange={e=>{const sport=e.target.value as ManagementProfile["actual_sport"];setProfile(p=>({...p,actual_sport:sport,planning_controls:{...p.planning_controls!,training_sports:Array.from(new Set([...p.planning_controls!.training_sports,sport]))}}));}}><option value="Run">Бягане</option>{profile.sport==="NordicSki"&&<><option value="NordicSki">Ски бягане</option><option value="RollerSki">Ролкови ски</option></>}</select></label>
      <label>Дисциплина · задължително<input required maxLength={100} value={profile.discipline} placeholder="5000 m или 10 km свободен стил" onChange={e=>update("discipline",e.target.value)}/></label>
      <label>Продължителност на старта, мин · по желание<input type="number" min=".1" max="1440" step=".1" value={profile.race_duration_min??""} onChange={e=>update("race_duration_min",optional(e.target.value))}/></label>
      <label>Възраст, години · по желание<input type="number" min="10" max="100" value={profile.age_years??""} onChange={e=>update("age_years",optional(e.target.value))}/></label>
      <label>Спортен стаж, години · по желание<input type="number" min="0" max="85" step=".5" value={profile.training_experience_years??""} onChange={e=>update("training_experience_years",optional(e.target.value))}/></label>
      <label>Начало · задължително<input required type="date" value={profile.program_start} onChange={e=>update("program_start",e.target.value)}/></label><label>Край · задължително<input required type="date" min={profile.program_start} value={profile.program_end} onChange={e=>update("program_end",e.target.value)}/></label>
      <label>Начало на подготовката<select value={profile.reentry_days===0?"continue":profile.reentry_days===null?"auto":"manual"} onChange={e=>update("reentry_days",e.target.value==="continue"?0:e.target.value==="auto"?null:7)}><option value="auto">Автоматично според историята и прекъсванията</option><option value="continue">Продължавам текуща подготовка · без ново вработване</option><option value="manual">Задавам дни за вработване</option></select></label>{profile.reentry_days!==null&&profile.reentry_days>0&&<label>Вработване, дни<input type="number" min="1" max="21" value={profile.reentry_days} onChange={e=>update("reentry_days",Number(e.target.value))}/></label>}
    </div><p className="management-muted">Началото на програмата не е непременно начало на спортната подготовка. Избери „Продължавам“, ако вече тренираш последователно. Реалната история и готовността се проверяват отделно.</p></>}
    {step===2&&<><PlanningControlsEditor profile={profile} onChange={setProfile} stage="days" today={today} history={initialProfile.history}/><details><summary>Нямам внесена история · въвеждане на обем</summary><label className="management-check"><input type="checkbox" checked={profile.recent_weekly_hours!==null} onChange={e=>update("recent_weekly_hours",e.target.checked?[0,0,0,0]:null)}/>Въвеждам четири завършени седмици за основното средство</label>{profile.recent_weekly_hours&&<div className="management-form-grid">{profile.recent_weekly_hours.map((v,i)=><label key={i}>Преди {4-i} седмици, часа<input type="number" min="0" max="80" step=".1" value={v} onChange={e=>update("recent_weekly_hours",profile.recent_weekly_hours!.map((x,j)=>j===i?Number(e.target.value):x))}/></label>)}</div>}<p>Тези стойности не създават измислени дневни товари или известна готовност.</p></details></>}
    {step===3&&<PlanningControlsEditor profile={profile} onChange={setProfile} stage="cycles" today={today}/>}
    {step===4&&<><h2>4. Методи, дозиране и адаптация</h2><p>Равномерни, прогресивни, редуващи се и прагови методи използват скорост–време, а при неподкрепена оценка — експертен Tref. Интервалите Z4/Z5 и силата имат отделни профили по-долу.</p>
      <div className="management-form-grid"><label>Изграждаща доза Z1–Z3, %<input type="number" min="50" max="60" value={profile.building_fraction*100} onChange={e=>update("building_fraction",Number(e.target.value)/100)}/></label><label>Поддържаща доза Z1–Z3, %<input type="number" min="30" max="40" value={profile.maintenance_fraction*100} onChange={e=>update("maintenance_fraction",Number(e.target.value)/100)}/></label><label>Доза при вработване, %<input type="number" min="40" max="50" value={profile.reentry_fraction*100} onChange={e=>update("reentry_fraction",Number(e.target.value)/100)}/></label><label>Максимум възстановителна работа, мин<input type="number" min="5" max="45" value={profile.recovery_session_cap_min} onChange={e=>update("recovery_session_cap_min",Number(e.target.value))}/></label><label>Тейпър пред основен старт, дни<input type="number" min="0" max="21" value={profile.taper_days} onChange={e=>update("taper_days",Number(e.target.value))}/></label><label>Адаптация<select value={profile.adaptation_mode} onChange={e=>update("adaptation_mode",e.target.value as "AUTO"|"REVIEW")}><option value="AUTO">Автоматично след реално изпълнение</option><option value="REVIEW">С треньорско утвърждаване</option></select></label></div>
      <label className="management-check"><input type="checkbox" checked={profile.allow_expert_fallback} onChange={e=>update("allow_expert_fallback",e.target.checked)}/>Експертен Tref при неподкрепен диапазон на скорост–време</label><label className="management-check"><input type="checkbox" checked={profile.auto_import_enabled} onChange={e=>update("auto_import_enabled",e.target.checked)}/>Автоматично обновяване на активностите при работеща програма</label>
      <label>Как да използвам кривата скорост–време?<select value={profile.planning_controls!.capacity_policy??"MODEL_WITH_PRIOR"} onChange={e=>update("planning_controls",{...profile.planning_controls!,capacity_policy:e.target.value as "OBSERVED_ONLY"|"MODEL_WITH_PRIOR"})}><option value="MODEL_WITH_PRIOR">Индивидуална опора + експертна форма на кривата</option><option value="OBSERVED_ONLY">Само диапазон между поне два надеждни теста</option></select></label><p className="management-muted">При един тест моделната форма е оценка, а не измерена устойчивост във всички диапазони. Тя се използва само с достатъчно скорошни данни за връзката пулс–скорост и в експертните граници. Всяка тренировка показва точния източник.</p>
      <ManagementRules profile={profile} onChange={setProfile} today={today} hideAutomation/>
    </>}
    <div className="management-actions">{step>1&&<button className="action-button secondary" type="button" onClick={()=>setStep(step-1)}>← Назад</button>}<button className="action-button" type="submit" disabled={step===4&&!dirty}>{busy?"Запазване…":step<4?"Напред →":"Запази целия профил"}</button></div>
    </fieldset></form><div className="management-actions"><a href="#planning-calendar">Стартове и лагери ↓</a><Link href="/management">Седмична програма →</Link><Link href="/management/outlook">Дългосрочен план →</Link></div>
  </section>;
}
