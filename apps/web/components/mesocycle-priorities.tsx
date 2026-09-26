import { isRecord } from "../lib/training-status";

const roles: Record<string,string> = { PRIMARY: "Водещ", SECONDARY: "Втори", LIGHT_DEVELOPMENT: "Леко развитие", INTRODUCTION: "Вработване" };
const number = (n: number) => n.toLocaleString("bg-BG", {maximumFractionDigits: 2});
const stages: Record<string, string> = {
  GENERAL_COVERAGE: "Обща подготовка: ротацията цели включване на всички разрешени компоненти в развиваща роля.",
  SPECIAL_FOUNDATION: "Начало на специалната подготовка: състезателната зона е водеща; редуват се съседните зони, със сила, когато е включена.",
  SPECIAL_RACE_BAND: "Край на специалната подготовка: приоритет имат състезателната зона и близките до нея зони.",
  PRECOMPETITION_RACE_BAND: "Предсъстезателна подготовка: приоритет имат състезателната зона и близките до нея зони. Тейпърът запазва отделните си ограничения.",
  COMPETITION: "Състезателен период: водещ акцент е състезателната зона.",
};
const componentName = (value: string) => value === "STR" ? "Сила" : value;

export function MesocyclePriorities({ cycle }: { cycle?: Record<string,unknown> | null }) {
  if (!cycle || !isRecord(cycle.component_indices)) return null;
  const recovery = cycle.kind === "RECOVERY";
  const support = Array.isArray(cycle.accents) ? cycle.accents.map(String) : [];
  const hasRace = typeof cycle.race_component === "string";
  const stage = typeof cycle.focus_stage === "string" ? cycle.focus_stage : "";
  const raceBand = hasRace && Array.isArray(cycle.race_band) ? cycle.race_band.filter((value): value is string => typeof value === "string") : [];
  const missing = stage === "GENERAL_COVERAGE" && Array.isArray(cycle.focus_coverage_missing) ? cycle.focus_coverage_missing.filter((value): value is string => typeof value === "string") : [];
  const cycleNumber = cycle.phase_mesocycle_number;
  const cycleCount = cycle.phase_mesocycle_count;
  return <details className="management-detail"><summary>Роли и цели на компонентите</summary>
    {recovery ? <p>Разтоварване на водещите компоненти. Допълващо поддържане: <strong>{support.map(componentName).join(", ") || "няма подходящ компонент"}</strong>. Изборът е условен до проверката на дневната готовност; общият товар остава намален. Поддържането не се брои за развиващ акцент.</p> : <>
      {stages[stage] && (hasRace || stage === "GENERAL_COVERAGE") && <p>{stages[stage]}</p>}
      {typeof cycleNumber === "number" && Number.isInteger(cycleNumber) && cycleNumber > 0 && typeof cycleCount === "number" && Number.isInteger(cycleCount) && cycleCount >= cycleNumber && <p>Мезоцикъл {cycleNumber} от {cycleCount} с автоматични развиващи акценти за периода.</p>}
      {raceBand.length > 0 && <p>Ориентир за избор на акценти около състезателната продължителност: <strong>{raceBand.join(", ")}</strong>. Конкретните роли и цели са посочени по-долу.</p>}
      <ul>{Object.entries(cycle.component_indices).filter(([,v])=>typeof v === "number").map(([z,value])=><li key={z}><strong>{z === "STR" ? "Сила" : z}</strong> · {cycle.kind === "STRESS" && isRecord(cycle.component_roles) && cycle.component_roles[z] === "LIGHT_DEVELOPMENT" ? "Трети ударен приоритет" : roles[String(isRecord(cycle.component_roles) ? cycle.component_roles[z] : "")] ?? "Приоритет"} · базова цел 7/40 <strong>{number(value as number)}</strong></li>)}</ul>
      <p>Седмичната вълна променя тези цели, с таван 2. Приравненият обем и дневната готовност ограничават съставените тренировки. Целта не означава автоматично разрешена доза.</p>
      {cycle.background_development === true && <p>През общата подготовка всяка аеробна зона следва своя годишен темп на развитие. Рангът определя седмичния приоритет; третият компонент получава по-малка седмична цел.</p>}
      {missing.length > 0 && <p>В автоматичната схема за този период няма планирана развиваща роля за: <strong>{missing.map(componentName).join(", ")}</strong>. Това не отчита ръчно зададените акценти или реално изпълнените тренировки.</p>}
    </>}
    {hasRace ? <p>Състезателна зона: <strong>{String(cycle.race_component)}</strong> · определена по средите на експертните граници за максимална непрекъсната работа при горния край на зоните.</p> : <p>Няма надеждно зададена състезателна продължителност. Използва се временна схема, която изисква треньорски преглед.</p>}
  </details>;
}
