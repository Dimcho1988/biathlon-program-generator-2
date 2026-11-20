"use client";
import { useState, type FormEvent } from "react";
import { useRouter } from "next/navigation";
import Link from "next/link";
import type { EnergyDay, EnergyHistory } from "../lib/energy";
const macros = [
  ["carbohydrate", "Въглехидрати", "#159b8d", 4],
  ["fat", "Мазнини", "#cc8a30", 9],
  ["protein", "Белтъчини", "#7c78c8", 4],
] as const;
const sources = [
  ["resting_outside_kcal", "Покой извън тренировки", "#647e9c"],
  ["movement_outside_kcal", "Движение извън тренировки", "#cc8a30"],
  ["activity_kcal", "Тренировки", "#159b8d"],
] as const;
const n = (v: number | null | undefined, d = 0) =>
  v == null ? "—" : v.toLocaleString("bg-BG", { maximumFractionDigits: d });
const dailyValue = (d: EnergyDay) => d.total ?? (d.outside || d.activity.kcal > 0 ? d.accounted : null);
const dailyLabel = (d: EnergyDay, today: string) => d.total ? "Целият ден · моделна оценка"
  : !dailyValue(d) ? "Няма оценка" : d.day === today ? "До момента · частична сума" : "Частична сума";
const inputLabels = { sex: "пол", weight_kg: "тегло", height_cm: "ръст", birth_year: "година на раждане" };

function ProfileForm({ data, day, canEdit }: { data: EnergyHistory; day: EnergyDay; canEdit: boolean }) {
  const router = useRouter();
  const [message, setMessage] = useState("");
  const [busy, setBusy] = useState(false);
  const [effective, setEffective] = useState(day.day);
  const manual = data.profiles.filter(p => p.effective_from <= effective).at(-1);
  const p = data.profile;
  const missing = data.profile_missing ?? (!p ? ["sex", "weight_kg", "birth_year", "height_cm"] : []);
  async function save(e: FormEvent<HTMLFormElement>) {
    e.preventDefault();
    const f = new FormData(e.currentTarget);
    const numeric = (key: string) => f.get(key) ? Number(f.get(key)) : null;
    const payload = {
      effective_from: effective,
      sex: f.get("sex") || null,
      weight_kg: numeric("weight_kg"), height_cm: numeric("height_cm"), birth_year: numeric("birth_year"),
      resting_kcal: numeric("resting_kcal"), outside_factor: numeric("outside_factor"),
      outside_cho_fraction: Number(f.get("outside_cho_fraction")) / 100,
      strength_met: numeric("strength_met"), strength_cho_fraction: Number(f.get("strength_cho_fraction")) / 100,
      preferences: String(f.get("preferences") || ""), allergies: String(f.get("allergies") || ""),
      expected_revision: data.profiles.find(r => r.effective_from === effective)?.revision ?? 0,
    };
    setBusy(true); setMessage("");
    try {
      const r = await fetch("/api/athlete/energy/profile", { method: "POST",
        headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload) });
      const v = await r.json();
      if (!r.ok || !v.saved) throw new Error(v.error || "Записът не е потвърден.");
      setMessage("Настройките са записани. Отчетът се обновява във фонов режим.");
      router.refresh();
    } catch (e) { setMessage(e instanceof Error ? e.message : "Записът не успя."); }
    finally { setBusy(false); }
  }
  return <section className="energy-panel">
    <h2>Движение извън тренировките</h2>
    <form onSubmit={save} className="energy-form">
      <fieldset disabled={!canEdit || busy}>
        <div className="energy-fields">
          <label>В сила от<input name="effective_from" type="date" max={data.today} required value={effective}
            onChange={e => setEffective(e.target.value)} /></label>
          <label>Дневна активност · фактор<input key={`factor-${effective}`} name="outside_factor" type="number"
            min="1" max="1.8" step="0.05" required defaultValue={manual?.outside_factor ?? 1.2} /></label>
        </div>
        <p className="energy-caption">1,0: само покой; 1,2: начален ориентир за спокойно ежедневие. Включва движение и термичния ефект на храната. Тренировките се отчитат отделно. Промяната важи от избраната дата до следващата записана настройка.</p>
        <details open={missing.length > 0}>
          <summary>{missing.length ? `Липсващи данни: ${missing.map(k => inputLabels[k as keyof typeof inputLabels] ?? k).join(", ")}` : "Лични данни · използват се наличните"}</summary>
          <p>{data.profile_source_note ?? "Наличните данни се използват автоматично. Попълнете само липсващото."}</p>
          <p>За избрания ден: {n(day.weight_kg, 1)} kg · ръст {n(p?.height_cm, 1)} cm · година на раждане {p?.birth_year ?? "—"}.</p>
          <div className="energy-fields" key={`body-${effective}`}>
            <label>Пол<select name="sex" defaultValue={manual?.sex ?? ""}>
              <option value="">Автоматично{p?.sex ? ` · ${p.sex === "MALE" ? "мъж" : "жена"}` : " · липсва"}</option>
              <option value="MALE">Мъж</option><option value="FEMALE">Жена</option>
            </select></label>
            <label>Тегло · kg<input name="weight_kg" type="number" min="21" max="250" step="0.1"
              defaultValue={manual?.weight_kg != null ? (data.days.find(d => d.day === effective)?.weight_kg ?? manual.weight_kg) : ""} placeholder={day.weight_kg ? `Автоматично · ${n(day.weight_kg, 1)}` : "Липсва"} />
              <small>По-новите измервания от дневника имат предимство.</small></label>
            <label>Година на раждане<input name="birth_year" type="number" min="1900" max={Number(data.today.slice(0, 4))}
              defaultValue={manual?.birth_year ?? ""} placeholder={p?.birth_year ? String(p.birth_year) : "Липсва"} /></label>
            <label>Ръст · cm<input name="height_cm" type="number" min="100" max="230" step="0.1"
              defaultValue={manual?.height_cm ?? ""} placeholder={p?.height_cm ? String(p.height_cm) : "Липсва"} /></label>
          </div>
        </details>
        <details>
          <summary>Допълнителни настройки и хранителни предпочитания</summary>
          <div className="energy-fields" key={`advanced-${effective}`}>
            <label>Разход в покой · kcal/24 h<input name="resting_kcal" type="number" min="500" max="4000"
              defaultValue={manual?.resting_kcal ?? ""} /><small>По избор; иначе се оценява по Mifflin–St Jeor за пълнолетни при налични данни.</small></label>
            <label>Въглехидрати извън тренировки · %<input name="outside_cho_fraction" type="number" min="0" max="100" required
              defaultValue={(manual?.outside_cho_fraction ?? 0.5) * 100} /><small>Допускане за небелтъчната енергия.</small></label>
            <label>Силова тренировка · средни MET<input name="strength_met" type="number" min="1" max="12" step="0.1" required
              defaultValue={manual?.strength_met ?? 3.5} /><small>Средна стойност за сесията, включително почивките.</small></label>
            <label>Въглехидрати при сила · %<input name="strength_cho_fraction" type="number" min="0" max="100" required
              defaultValue={(manual?.strength_cho_fraction ?? 0.5) * 100} /></label>
            <label>Алергии и непоносимости<textarea name="allergies" maxLength={1000} defaultValue={manual?.allergies ?? ""} placeholder="Посочете и ако няма известни." /></label>
            <label>Хранителни предпочитания<textarea name="preferences" maxLength={1000} defaultValue={manual?.preferences ?? ""} /></label>
          </div>
        </details>
        {canEdit && <button className="action-button" disabled={busy}>{busy ? "Записване…" : "Запази настройките"}</button>}
      </fieldset><p role="status">{message}</p>
    </form>
  </section>;
}

export function EnergyDashboard({ data, canEdit }: { data: EnergyHistory; canEdit: boolean }) {
  const [selected, setSelected] = useState(data.days.filter(d => d.day < data.today && dailyValue(d)).at(-1)?.day ?? data.days.at(-1)?.day ?? "");
  const [view, setView] = useState<"energy" | "g" | "kcal" | "fuel_g" | "fuel_kcal">("energy");
  const day = data.days.find(d => d.day === selected) ?? data.days.at(-1);
  const fuel = view === "fuel_g" || view === "fuel_kcal";
  const fuelUnit = view === "fuel_g" ? "g" : "kcal";
  const fuelLabel = (d: EnergyDay) => macros.slice(0, 2).map(([k, label]) => `${label} ${n(dailyValue(d)?.[`${k}_${fuelUnit}`], 1)} ${fuelUnit}`).join(" · ");
  const parts = (d: EnergyDay) => view === "energy"
    ? sources.map(([k]) => d.energy_sources?.[k] ?? (k === "activity_kcal" ? d.activity.kcal : 0))
    : fuel ? macros.slice(0, 2).map(([k]) => dailyValue(d)?.[`${k}_${fuelUnit}`] ?? 0)
    : macros.map(([k, , , factor]) => (d.targets?.[`${k}_g`] ?? 0) * (view === "kcal" ? factor : 1));
  const quantity = (d: EnergyDay) => parts(d).reduce((a, b) => a + b, 0);
  const max = Math.max(1, ...data.days.map(quantity));
  const legend = view === "energy" ? sources : fuel ? macros.slice(0, 2) : macros;
  return <>
    <section className="energy-panel">
      <form method="get" className="energy-period">
        <label>От<input type="date" name="start" required defaultValue={data.period_start} /></label>
        <label>До<input type="date" name="end" max={data.today} required defaultValue={data.period_end} /></label>
        <button className="action-button secondary">Покажи</button>
      </form>
      <div className="energy-heading"><h2>Дневна динамика</h2>
        <label>Показател<select value={view} onChange={e => setView(e.target.value as typeof view)}>
          <option value="energy">Енергоразход · kcal</option>
          <option value="fuel_g">Оценени въглехидрати и мазнини · g</option>
          <option value="fuel_kcal">Оценени въглехидрати и мазнини · kcal</option>
          <option value="g">Хранителни цели · g</option>
          <option value="kcal">Хранителни цели · kcal</option>
        </select></label>
      </div>
      <p className="energy-caption">{view === "energy"
        ? "Щрихованите колони показват само оценената част. Липсващ разход не означава нула."
        : fuel ? "Оценени окислени въглехидрати и мазнини за отчетената част от деня. Белтъчините са показани отделно като цел за прием."
        : "Разпределение на хранителните цели. При непълен ден е налична само резервната белтъчна цел."}</p>
      <div className="energy-bars" role="group" aria-label="Избери ден от графиката">
        {data.days.map(d => <button key={d.day} type="button" className={`energy-daybar ${d.total ? "" : "partial"} ${selected === d.day ? "selected" : ""}`}
          aria-label={`${d.day}: ${view === "energy" ? `${n(dailyValue(d)?.kcal)} kcal, ${dailyLabel(d, data.today)}` : fuel ? fuelLabel(d) : macros.map(([k, label]) => `${label} ${n(d.targets?.[`${k}_g`])} g`).join(", ")}`}
          title={fuel ? `${d.day}: ${fuelLabel(d)}` : undefined}
          aria-pressed={selected === d.day} onClick={() => setSelected(d.day)}>
          <span className="energy-stack" style={{ height: `${Math.max(2, 160 * quantity(d) / max)}px` }}>
            {parts(d).map((v, i) => <i key={i} style={{ background: legend[i][2], flex: v }} />)}
          </span><small>{d.day.slice(8)}</small>
        </button>)}
      </div>
      <div className="energy-legend">{legend.map(([k, label, color]) => <span key={k}><i style={{ background: color }} />{label}</span>)}</div>
      {fuel && day && <p className="energy-caption">{day.day}: {fuelLabel(day)} · {dailyLabel(day, data.today)}.</p>}
      <details><summary>Дневни стойности</summary>
        <div className="energy-table energy-daily-table" tabIndex={0} aria-label="Дневни калории и хранителни цели">
          <table><caption>Разход и цели за прием · избери ден за подробности</caption>
            <thead><tr><th>Дата</th><th>Разход · kcal</th>{macros.map(([k, label]) => <th key={k}>{label} · g</th>)}<th>Обхват</th></tr></thead>
            <tbody>{[...data.days].reverse().map(d => <tr key={d.day} className={selected === d.day ? "selected" : undefined}>
              <th scope="row"><button type="button" onClick={() => setSelected(d.day)}>{d.day}</button></th>
              <td>{n(dailyValue(d)?.kcal)}</td>{macros.map(([k]) => <td key={k}>{n(d.targets?.[`${k}_g`])}</td>)}
              <td>{dailyLabel(d, data.today)}{d.targets?.protein_basis === "INCOMPLETE_DAY_FALLBACK" ? " · резервни белтъчини" : ""}</td>
            </tr>)}</tbody>
          </table>
        </div>
      </details>
    </section>
    {day && <>
      <section className="energy-panel">
        <div className="energy-heading"><h2>{day.day}</h2><label>Избери ден<select value={day.day} onChange={e => setSelected(e.target.value)}>
          {data.days.map(d => <option key={d.day}>{d.day}</option>)}</select></label></div>
        <div className="energy-total"><span>{day.total ? "Общ дневен разход" : "Оценен разход до момента"}</span>
          <strong>{n(dailyValue(day)?.kcal)} <small>kcal</small></strong>
          <p>{dailyValue(day) ? dailyLabel(day, data.today) : "Няма пълна дневна оценка"}</p></div>
        <div className="energy-cards">{sources.map(([k, label]) => <article key={k}><span>{label}</span>
          <strong>{n(day.energy_sources?.[k])} <small>kcal</small></strong></article>)}</div>
        <p className="energy-caption">Тренировките включват разхода в покой за същото време. Той е изключен от останалите две стойности, така че няма двойно броене.</p>
        {day.reason && <p className="energy-notice">{day.reason}</p>}
        <h3>Хранителни цели</h3>
        {day.targets ? <>
          <div className="energy-cards">{macros.map(([k, label, color, factor]) => <article key={k} style={{ borderTop: `3px solid ${color}` }}>
            <span>{label}</span><strong>{n(day.targets?.[`${k}_g`])} <small>g</small></strong>
            <p>{day.targets?.[`${k}_g`] != null ? `${n(day.targets[`${k}_g`]! * factor)} kcal` : "Изчаква пълен дневен отчет"}</p>
          </article>)}</div>
          <p>Белтъчини: {n(day.targets.protein_g_per_kg, 2)} g/kg · тегло {n(day.weight_kg, 1)} kg.
            {day.targets.energy_kcal_per_kg != null && <> Дневен разход: {n(day.targets.energy_kcal_per_kg, 1)} kcal/kg.</>}</p>
          <p className="energy-caption">{day.targets.allocation_note}</p>
        </> : <p>За хранителна цел са необходими тегло и данни за възрастта. При непълнолетни целите се определят индивидуално.</p>}
        <Link className="action-button secondary" href={`/assistant?energy_date=${encodeURIComponent(day.day)}`}>Обсъди меню с ИИ</Link>
      </section>
      <ProfileForm key={`${day.day}-${data.profile_revision}`} data={data} day={day} canEdit={canEdit} />
      <details className="energy-panel"><summary>Подробности за тренировките</summary>
        {day.activities.length === 0 ? <p>Няма записани активности за деня.</p> : day.activities.map(a => <details key={a.activity_ref}>
          <summary>{a.method === "STRENGTH_MET" ? "Силова тренировка" : a.sport} · {n(a.duration_s / 60)} min · {n(a.kcal)} kcal
            {a.method === "STRENGTH_MET" ? ` · приблизително по ${n(a.met, 1)} MET` : ""}</summary>
          <p>{a.method === "STRENGTH_MET" ? "Средна оценка за цялата сесия с почивките." : `По пулс/скорост: ${n(a.coverage_percent, 1)}% от времето. Без оценка: ${n((a.unknown_seconds ?? a.duration_s - (a.covered_seconds ?? 0)) / 60, 1)} min.`}</p>
          <p>Оценка на окислени субстрати: въглехидрати {n(a.kcal == null ? null : a.carbohydrate_g, 1)} g; мазнини {n(a.kcal == null ? null : a.fat_g, 1)} g. Това са енергийни еквиваленти, отделни от целите за прием.</p>
          <div className="energy-table"><table><caption>Разпределение през 5 минути · оценен разход</caption>
            <thead><tr><th>Минути</th><th>kcal</th><th>Въглехидрати · g</th><th>Мазнини · g</th></tr></thead>
            <tbody>{a.timeline.map(t => <tr key={t.start_s}><td>{n(t.start_s / 60)}–{n((t.start_s + Math.min(300, a.duration_s - t.start_s)) / 60)}</td>
              <td>{n(t.kcal)}</td><td>{n(t.carbohydrate_g, 1)}</td><td>{n(t.fat_g, 1)}</td></tr>)}</tbody>
          </table></div>
        </details>)}
      </details>
    </>}
    <details className="energy-panel"><summary>Методика и ограничения</summary>
      <ul>{data.assumptions.map(t => <li key={t}>{t}</li>)}</ul>
      <p>Няма независима валидация на целия дневен модел.</p>
      <ul>{data.references.map(h => <li key={h}><a href={h} target="_blank" rel="noreferrer">{h}</a></li>)}</ul>
    </details>
  </>;
}
