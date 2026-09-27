"use client";

import Link from "next/link";
import { useState } from "react";
import examples from "../lib/learning-examples.json";
import { COMPONENTS, type PlanProjection } from "../lib/training-management";
import { componentColor } from "../lib/training-visuals";
import { LEARNING_MODE_LABELS, LEARNING_CONFIDENCE_LABELS, parseIndividualLearningReport, type IndividualLearningReport } from "../lib/individual-learning";

const number = (value: number) => value.toLocaleString("bg-BG", { maximumFractionDigits: 1 });
const signed = (value: number, unit: string) => Math.abs(value) < .005 ? "Без промяна" : `${value > 0 ? "+" : "−"}${number(Math.abs(value))}${unit}`;
const change = (factor: number, intensity: number) => [
  `обем ${signed((factor - 1) * 100, "%")}`,
  ...(Math.abs(intensity) > .00001 ? [`в зоната ${signed(intensity * 100, " п.п.")}`] : []),
].join(" · ");
const date = (value: string) => new Date(`${value}T12:00:00Z`).toLocaleDateString("bg-BG", { day: "numeric", month: "short", timeZone: "UTC" });
const VALIDATION = { WARMUP: "Натрупват се данни за проверка", PASSED: "Прогнозите преминават проверката", FAILED: "Прогнозите още не са достатъчно точни" } as const;

export function IndividualLearningPanel({ plan }: { plan?: PlanProjection | null }) {
  let report;
  try { report = parseIndividualLearningReport(plan?.parameters?.individual_learning ?? plan?.individual_learning); }
  catch { return <aside className="management-panel management-muted">Оценката на самообучението не може да бъде показана. Обнови програмата.</aside>; }
  if (!report) return null;
  const changes = COMPONENTS.filter(key => report.components[key].action !== "HOLD");
  return <details className="management-panel management-learning" aria-label="Самообучение на плана">
    <summary>
      <span className="management-learning-heading"><strong>Самообучение</strong><span className="management-badge">{LEARNING_MODE_LABELS[report.mode]}</span>{report.status === "EXPERIMENT" && <span className="management-badge">Пробна промяна</span>}</span>
      <span className="management-learning-summary">{report.summary}</span>
      <span className="management-learning-meta">{report.evidence_count} наблюдения · {LEARNING_CONFIDENCE_LABELS[report.confidence].toLocaleLowerCase("bg-BG")} увереност{changes.length > 0 ? ` · ${changes.map(key => key === "STR" ? "Сила" : key).join(", ")}` : ""}</span>
    </summary>
    <LearningReportDetails report={report}/>
    <LearningExamples/>
    <Link href="/planning#basic-profile">Настройки в „Мезоцикли и акценти“ →</Link>
  </details>;
}

function LearningReportDetails({ report }: { report: IndividualLearningReport }) {
  return <div className="management-learning-body">
      {report.mode === "SHADOW" && <p className="management-notice">Предложенията са за наблюдение. Самообучението не променя предписаното натоварване.</p>}
      {report.mode === "CONTROL" && <p className="management-muted">„Допуснато в плана“ е разрешената корекция. Действителната промяна може да е по-малка или нулева според периода, готовността, избрания метод и ограниченията на деня.</p>}
      <div className="management-table-wrap" role="region" aria-label="Предложени и приложени промени" tabIndex={0}>
        <table><thead><tr><th scope="col">Компонент</th><th scope="col">Предложено</th><th scope="col">Допуснато в плана</th><th scope="col">Основание</th></tr></thead>
          <tbody>{COMPONENTS.map(key => { const item = report.components[key]; return <tr key={key}>
            <th scope="row"><span className="management-learning-zone"><i style={{ backgroundColor: componentColor(key) }}/>{key === "STR" ? "Сила" : key}</span></th>
            <td>{change(item.proposed_volume_factor, item.proposed_intensity_delta)}</td>
            <td>{change(item.volume_factor, item.intensity_delta)}</td>
            <td>{item.reason}<small>{LEARNING_CONFIDENCE_LABELS[item.confidence]} увереност · {item.evidence_count} наблюдения</small></td>
          </tr>; })}</tbody></table>
      </div>
      <p><strong>{VALIDATION[report.validation.status]}</strong>{report.validation.evaluated > 0 && ` · ${report.validation.evaluated} проверки върху последващи данни`}</p>
      <p className="management-muted">Увереността описва опората в данните и точността на проверените прогнози. Тя не доказва, че дадена тренировка е причинила наблюдаваната промяна.</p>
      {report.limitations.length > 0 && <ul className="management-learning-limitations">{report.limitations.map((item, index) => <li key={index}>{item}</li>)}</ul>}
      <p className="management-learning-meta">Данни към {date(report.as_of)}{report.effective_from ? ` · от ${date(report.effective_from)}` : ""}{report.expires_on ? ` до ${date(report.expires_on)}` : ""}</p>
    </div>;
}

function LearningExamples() {
  const [open, setOpen] = useState(false);
  const [selected, setSelected] = useState(examples[0]?.id ?? "");
  const example = examples.find(item => item.id === selected);
  let report: IndividualLearningReport | null = null;
  try { report = parseIndividualLearningReport(example?.report); } catch { /* An unavailable example never affects the live plan. */ }
  if (!examples.length) return null;
  return <details className="management-learning-examples" onToggle={event => setOpen(event.currentTarget.open)}>
    <summary>Разгледай примери</summary>
    {open && <>
      <p className="management-notice">Примерни данни · не променят програмата.</p>
      <label>Примерна ситуация<select value={selected} onChange={event => setSelected(event.target.value)}>
        {examples.map(item => <option key={item.id} value={item.id}>{item.label}</option>)}
      </select></label>
      <div role="region" aria-label="Пример на самообучението">
        {report ? <><p><strong>{LEARNING_MODE_LABELS[report.mode]}</strong> · {report.summary}</p><LearningReportDetails report={report}/></>
          : <p>Примерът временно не е достъпен.</p>}
      </div>
    </>}
  </details>;
}
