import { isRecord } from "../lib/training-status";

const roles: Record<string,string> = { PRIMARY: "Водещ", SECONDARY: "Втори", LIGHT_DEVELOPMENT: "Леко развитие", INTRODUCTION: "Вработване" };
const number = (n: number) => n.toLocaleString("bg-BG", {maximumFractionDigits: 2});

export function MesocyclePriorities({ cycle }: { cycle?: Record<string,unknown> | null }) {
  if (!cycle || !isRecord(cycle.component_indices)) return null;
  const recovery = cycle.kind === "RECOVERY";
  const support = Array.isArray(cycle.accents) ? cycle.accents.map(String) : [];
  return <details className="management-detail"><summary>Роли и цели на компонентите</summary>
    {recovery ? <p>Разтоварване на водещите компоненти. Допълващо поддържане: <strong>{support.join(", ") || "няма подходящ компонент"}</strong>. Изборът е условен до проверката на дневната готовност; общият товар остава намален.</p> : <>
      <ul>{Object.entries(cycle.component_indices).filter(([,v])=>typeof v === "number").map(([z,value])=><li key={z}><strong>{z === "STR" ? "Сила" : z}</strong> · {cycle.kind === "STRESS" && isRecord(cycle.component_roles) && cycle.component_roles[z] === "LIGHT_DEVELOPMENT" ? "Трети ударен приоритет" : roles[String(isRecord(cycle.component_roles) ? cycle.component_roles[z] : "")] ?? "Приоритет"} · базова цел 7/40 <strong>{number(value as number)}</strong></li>)}</ul>
      <p>Седмичната вълна променя тези цели, с таван 2. Приравненият обем Q и дневната готовност ограничават съставените тренировки. Целта не означава автоматично разрешена доза.</p>
      {cycle.background_development === true && <p>Останалите зони получават възможност за слабо развитие в общата подготовка. Третият приоритет има по-малък дял от прираста.</p>}
    </>}
    {typeof cycle.race_component === "string" ? <p>Състезателна зона: <strong>{cycle.race_component}</strong> · определена по средите на експертните граници за максимална непрекъсната работа при горния край на зоните.</p> : <p>Няма надеждно зададена състезателна продължителност. Използва се временна схема, която изисква треньорски преглед.</p>}
  </details>;
}
