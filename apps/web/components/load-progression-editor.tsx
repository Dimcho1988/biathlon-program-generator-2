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
