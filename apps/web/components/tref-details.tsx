import { TREF_BOUNDS_MINUTES, type Zone } from "../lib/training-status";

const number = new Intl.NumberFormat("bg-BG", { maximumFractionDigits: 1 });
export function TrefDetails({ zones, strength }: { zones: Array<{ zone: Zone; tref_min: number }>; strength?: number }) {
  return <details className="tref-technical">
    <summary>Технически параметри · Tref</summary>
    <p>Tref е 7 × средния дневен E от наличната 40-дневна история, ограничен в експертните граници. Това е референтен параметър, а не измерен обем или максимална продължителност на усилието.</p>
    <dl>{zones.map((z) => <div key={z.zone}><dt>{z.zone} · граници {TREF_BOUNDS_MINUTES[z.zone].join("–")} мин</dt><dd>{number.format(z.tref_min)} мин</dd></div>)}{strength !== undefined && <div><dt>STR · фиксиран</dt><dd>{number.format(strength)} мин</dd></div>}</dl>
  </details>;
}
