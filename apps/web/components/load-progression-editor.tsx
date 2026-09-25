"use client";
import { defaultLoadProgression, type ManagementProfile } from "../lib/training-management";

export function LoadProgressionEditor({profile, onChange}: {profile: ManagementProfile; onChange: (p: ManagementProfile) => void}) {
  const value = profile.load_progression ?? defaultLoadProgression();
  const update = (patch: Partial<typeof value>) => onChange({...profile, load_progression: {...value, ...patch}});
  return <section aria-label="Прираст на натоварването">
    <h3>Дългосрочен прираст</h3>
    <label className="management-check"><input type="checkbox" checked={value.enabled} onChange={e=>update({enabled:e.target.checked})}/>Управление според обема и наблюдаваната реакция</label>
    {value.enabled && <>
      <p>Прирастът се оценява за цял мезоцикъл, включително разтоварването. Основното повишаване е в подготвителния период. Пропуснатото не се наваксва автоматично.</p>
      <p>Надеждният исторически обем се запазва между експертните граници; извън тях опората е съответната граница. Настройките за ниво и позиция се използват само при липсваща надеждна история. Опората се запазва за подготовката. Нова база се избира при нова програма, промяна на пулсовите граници или изрично преоценяване. По-нисък обем в поддържащ мезоцикъл не я намалява автоматично.</p>
      <label>Ниво за опора при липсваща история<select value={value.training_level ?? "AUTO"} onChange={e=>update({training_level:e.target.value as NonNullable<typeof value.training_level>})}>
        <option value="AUTO">Според стажа и общия обем</option><option value="LOW">Начално</option><option value="MEDIUM">Средно</option><option value="HIGH">Високо</option>
      </select></label>
      <details><summary>Резервна опора при липсваща история</summary>
        <p>По желание: 0% е долната граница, 100% — горната. Празно използва нивото, стажа и обема. Това е дългосрочна опора, а не задължителен седмичен минимум.</p>
        {(["Z1","Z2","Z3","Z4","Z5"] as const).map(z=><label key={z}>{z}, позиция %<input type="number" min="0" max="100" value={value.component_reference_positions?.[z] === undefined ? "" : Math.round(value.component_reference_positions[z]!*100)} onChange={e=>{
          const positions={...value.component_reference_positions}; if(e.target.value==="") delete positions[z]; else positions[z]=Number(e.target.value)/100; update({component_reference_positions:positions});
        }}/></label>)}
        <button type="button" onClick={()=>update({reference_revision:(value.reference_revision ?? 0)+1})}>Преоцени опорната база при следващото запазване</button>
        {(value.reference_revision ?? 0)>0&&<p>Заявена версия на преоценката: {value.reference_revision}. Запази профила, за да приложиш промяната.</p>}
      </details>
      <p className="management-muted">Z5 се приравнява към долната граница с 5% за удар; Z1–Z4 остават с 3% към горната. Годишният процент се натрупва постепенно само в подходящите акцентни периоди.</p>
      <div className="management-form-grid">
        <label>Годишен прираст при нисък обем, %<input type="number" min="0" max="30" step=".5" value={value.low_volume_annual_percent} onChange={e=>update({low_volume_annual_percent:Number(e.target.value)})}/></label>
        <label>При горната експертна граница, %<input type="number" min="0" max={value.low_volume_annual_percent} step=".5" value={value.upper_volume_annual_percent} onChange={e=>update({upper_volume_annual_percent:Number(e.target.value)})}/></label>
        <label>Максимална обичайна доза, %<input type="number" min="50" max="80" value={value.max_dose_fraction*100} onChange={e=>update({max_dose_fraction:Number(e.target.value)/100})}/></label>
      </div>
      <label className="management-check"><input type="checkbox" checked={value.feedback_enabled} onChange={e=>update({feedback_enabled:e.target.checked})}/>Адаптиране от завършени блокове и съпоставими тестове</label>
      <p className="management-muted">Очакваната временна умора не е отрицателен резултат. Нужни са изпълнено натоварване, наблюдение през разтоварването и съпоставим тест. При болка или заболяване новите задачи изискват преглед.</p>
      <details><summary>Експертни граници и настройка по периоди</summary>
        <p>Начални треньорски настройки за приравнен обем Q преди преливането. Реалната история се запазва и извън границите.</p>
        <table><thead><tr><th>Компонент</th><th>Минути Q / седмица</th></tr></thead><tbody>{[["Z1","240–840"],["Z2","60–300"],["Z3","30–120"],["Z4","10–40"],["Z5","5–30"]].map(([z,b])=><tr key={z}><td>{z}</td><td>{b}</td></tr>)}</tbody></table>
        <p>При {Math.round((value.ceiling_ratio-1)*100)}% над горната граница планираният прираст става нула. За сила няма приета граница за автоматичен годишен прираст.</p>
        <div className="management-form-grid"><label>Дял от прираста предсъстезателно, %<input type="number" min="0" max="100" value={value.precompetition_factor*100} onChange={e=>update({precompetition_factor:Number(e.target.value)/100})}/></label><label>Дял от прираста състезателно, %<input type="number" min="0" max={value.precompetition_factor*100} value={value.competition_factor*100} onChange={e=>update({competition_factor:Number(e.target.value)/100})}/></label></div>
        <p>При три или по-малко сесии дозата може да нарасне до избрания таван само когато остава товар по 7/40. Загрявките, паузите и разпускането остават в наличното време. Неизпълнимият остатък се показва отделно.</p>
      </details>
    </>}
  </section>;
}
