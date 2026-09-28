"use client";
import { useState } from "react";
import type { Session } from "../lib/response-monitoring";
import { LACTATE_ZONES, lactateRangeText, type LactateSample } from "../lib/training-observations";

const emptySample = (): LactateSample => ({ value_mmol: 0, zone: null, after: "SESSION", repetition: null, delay_seconds: null, planned_low_mmol: null, planned_high_mmol: null, comparison_confirmed: false, note: "" });
const results: Record<string, string> = { WITHIN: "Във въведения диапазон", BELOW: "Под въведения диапазон", ABOVE: "Над въведения диапазон", NOT_COMPARED: "Без потвърдено сравнение" };

export function SessionTrainingObservations({ session }: { session: Session }) {
  const [rows, setRows] = useState(() => (session.lactate_samples ?? []).map((sample, id) => ({ sample, id })));
  const [nextId, setNextId] = useState(rows.length);
  const [nms, setNms] = useState(!!session.neuromuscular);
  const observed = session.neuromuscular;
  return <>
    <details><summary>Лактат след тренировка · {rows.length ? `${rows.length} проби` : "по желание"}</summary>
      <p>Запиши реално измерените стойности и момента на пробата. Ориентирът за сравнение се преписва от съответната част на плана; не се заменя с текущите настройки на профила.</p>
      <label>Уред за измерване<input name="lactate_device" maxLength={80} defaultValue={session.lactate_device ?? ""}/></label>
      {rows.map(({ sample, id }, index) => <fieldset className="training-sample" key={id}><legend>Проба {index + 1}</legend><input type="hidden" name="lactate_row" value={id}/>
        <div className="response-fields">
          <label>Лактат · mmol/L<input name={`la_${id}_value`} type="number" min=".1" max="40" step=".1" required defaultValue={sample.value_mmol || ""}/></label>
          <label>Зона на работния блок<select name={`la_${id}_zone`} defaultValue={sample.zone ?? ""}><option value="">Не е уточнена</option>{LACTATE_ZONES.map(z => <option key={z}>{z}</option>)}</select></label>
          <label>Момент на пробата<select name={`la_${id}_after`} value={sample.after} onChange={e => setRows(rows.map(r => r.id === id ? { ...r, sample: { ...r.sample, after: e.target.value as LactateSample["after"] } } : r))}><option value="REPETITION">След отсечка</option><option value="BLOCK">След работния блок</option><option value="SESSION">След цялата тренировка</option></select></label>
          {sample.after === "REPETITION" && <label>След коя отсечка?<input name={`la_${id}_repetition`} type="number" min="1" max="200" required defaultValue={sample.repetition ?? ""}/></label>}
          <label>Секунди след края на избраната част<input name={`la_${id}_delay`} type="number" min="0" max="3600" defaultValue={sample.delay_seconds ?? ""} placeholder="0 = веднага; празно = неизвестно"/></label>
        </div>
        <details><summary>Сравни с лактатния ориентир в плана</summary><div className="response-fields">
          <label>Планиран ориентир от · mmol/L<input name={`la_${id}_low`} type="number" min="0" max="40" step=".1" defaultValue={sample.planned_low_mmol ?? ""}/></label>
          <label>Планиран ориентир до · mmol/L<input name={`la_${id}_high`} type="number" min=".1" max="40" step=".1" defaultValue={sample.planned_high_mmol ?? ""}/></label>
        </div><label className="response-check"><input name={`la_${id}_confirmed`} type="checkbox" defaultChecked={sample.comparison_confirmed}/>Потвърждавам същата част на тренировката и съпоставим момент на пробата</label><p>Стойност над ориентира е отклонение за обсъждане, а не автоматично доказателство за лоша тренировка.</p></details>
        <label>Бележка за пробата<textarea name={`la_${id}_note`} maxLength={250} defaultValue={sample.note}/></label>
        <button type="button" className="action-button secondary" onClick={() => setRows(rows.filter(r => r.id !== id))}>Премахни проба {index + 1}</button>
      </fieldset>)}
      <button type="button" className="action-button secondary" disabled={rows.length >= 20} onClick={() => { setRows([...rows, { id: nextId, sample: emptySample() }]); setNextId(nextId + 1); }}>Добави проба</button>
    </details>
    <details><summary>Изпълнени кратки ускорения / спринтове · по желание</summary>
      <label className="response-check"><input type="checkbox" name="nms_record" checked={nms} onChange={e => setNms(e.target.checked)}/>Записвам нервно-мускулната работа</label>
      {nms && <><div className="response-fields">
        <label>Изпълнени повторения<input type="number" name="nms_repetitions" min="0" max="200" required defaultValue={observed?.repetitions ?? ""}/></label>
        <label>Общо време на отсечките · секунди<input type="number" name="nms_seconds" min="0" max="3600" step=".1" required defaultValue={observed?.work_seconds ?? ""}/></label>
        <label>Максимална измерена скорост · km/h<input type="number" name="nms_speed" min=".1" max="150" step=".1" defaultValue={observed?.peak_speed_kmh ?? ""}/></label>
        <label>Планирани повторения · по желание<input type="number" name="nms_planned_repetitions" min="0" max="200" defaultValue={observed?.planned_repetitions ?? ""}/></label>
        <label>Планирано общо време на отсечките · секунди<input type="number" name="nms_planned_seconds" min="0" max="3600" step=".1" defaultValue={observed?.planned_work_seconds ?? ""}/></label>
      </div><label>Техника, условия и бележка<textarea name="nms_note" maxLength={250} defaultValue={observed?.note ?? ""}/></label></>}
      <p>Времето тук е само за отсечките, без почивките. Това е отделен отчет на изпълнението; не се добавя повторно към пулсовия товар и не означава измерена мощност.</p>
    </details>
  </>;
}

export function SavedTrainingObservations({ session }: { session: Session }) {
  const samples = session.lactate_samples ?? [];
  const nms = session.neuromuscular;
  if (!samples.length && !nms) return null;
  return <div className="training-observation-summary"><strong>Записани наблюдения</strong>
    {samples.length > 0 && <ul>{samples.map((s, i) => <li key={i}>{s.value_mmol.toLocaleString("bg-BG")} mmol/L · {s.zone ?? "неуточнена зона"} · {s.after === "REPETITION" ? `след отсечка ${s.repetition}` : s.after === "BLOCK" ? "след блока" : "след сесията"}{s.delay_seconds != null ? ` + ${s.delay_seconds} s` : " · моментът не е уточнен"}. {results[s.comparison ?? "NOT_COMPARED"]}{s.comparison && s.comparison !== "NOT_COMPARED" ? ` (${lactateRangeText({ low_mmol: s.planned_low_mmol, high_mmol: s.planned_high_mmol })})` : ""}.</li>)}</ul>}
    {nms && <p>NMS: {nms.repetitions} повторения{nms.planned_repetitions != null ? ` / ${nms.planned_repetitions} планирани` : ""}; {nms.work_seconds} s работа{nms.planned_work_seconds != null ? ` / ${nms.planned_work_seconds} s планирани` : ""}{nms.peak_speed_kmh != null ? `; максимална измерена скорост ${nms.peak_speed_kmh} km/h` : ""}.</p>}
  </div>;
}
