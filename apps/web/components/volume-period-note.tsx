import type { LoadHistory } from "../lib/load-history";
import { displayDate, equivalentWindow } from "../lib/dashboard-periods";

export function VolumePeriodNote({ history }: { history: LoadHistory }) {
  const short = equivalentWindow(history, 7), long = equivalentWindow(history, 40);
  return <div className="volume-period-note">
    <p><strong>Приравнен обем от всички спортове:</strong> сбор за {displayDate(short.start)} – {displayDate(short.end)}; седмичен еквивалент от {displayDate(long.start)} – {displayDate(long.end)} = сбор ÷ {long.days} × 7. Крайната дата е включена; текущият ден може да е непълен.</p>
    <p>Минутите са приравнени към горната пулсова граница на зоната. Товарът E включва и влиянието между зоните и се използва отделно за 7/40.</p>
    {(short.partial || long.partial) && <p>Непълна история: налични {short.days}/7 и {long.days}/40 календарни дни. Седмичният еквивалент е предварителен.</p>}
    {(!short.complete || !long.complete) && <p>Липсват дневни данни; засегнатият обем не се изчислява.</p>}
    {long.limited && <p>Историята съдържа ограничени или изключени пулсови записи; показаният обем може да е непълен.</p>}
  </div>;
}
