import { durationHms } from "../lib/duration-format";
import { TREF_BOUNDS_MINUTES, type Zone } from "../lib/training-status";

export function TrefDetails({ zones, strength }: { zones: Array<{ zone: Zone; tref_min: number }>; strength?: number }) {
  return <details className="tref-technical">
    <summary>Технически параметри · Tref</summary>
    <p>Tref е 7 × средния дневен E от наличната 40-дневна история, ограничен в експертните граници. Това е референтен параметър, а не измерен обем или максимална продължителност на усилието. Tmax за дозиране и влияние между зоните е отделна величина: от индивидуалната крива скорост–време, а при липсваща използваема крива — експертна оценка.</p>
    <dl>{zones.map((z) => <div key={z.zone}><dt>{z.zone} · граници {TREF_BOUNDS_MINUTES[z.zone].map(durationHms).join("–")}</dt><dd>{durationHms(z.tref_min)}</dd></div>)}{strength !== undefined && <div><dt>STR · фиксиран</dt><dd>{durationHms(strength)}</dd></div>}</dl>
  </details>;
}
