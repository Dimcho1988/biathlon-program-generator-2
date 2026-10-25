import type { ActivePlanRecord, ManagementProfileResponse } from "../lib/training-management";
import { proposalHasIncompleteLoad, proposedWeekMode, savedWeekMode, WEEK_MODE_LABEL } from "../lib/week-mode";

export function WeekModeStatus({ record, profile }: { record: ActivePlanRecord; profile?: ManagementProfileResponse }) {
  const p = record.payload;
  const saved = savedWeekMode(record, profile);
  const proposed = proposedWeekMode(record);
  const pending = saved !== undefined && saved !== p.mode;
  const obsolete = proposed !== undefined && saved !== undefined && proposed !== saved;
  const behavior = p.status === "PAUSED" ? "Програмата е на пауза. Автоматични промени не се прилагат."
    : p.status === "COMPLETED" ? "Периодът на тази програма е завършен."
    : record.stale || p.status === "REVIEW_REQUIRED" || pending ? "Утвърденият график е запазен. Промените изискват актуална проверка и преглед преди прилагане."
    : p.mode === "REVIEW" ? "Датите, методите и дозите се запазват до утвърждаване на промяна."
    : "Оставащите задачи се адаптират автоматично след нов анализ и при смяна на деня. Променени правила изискват утвърждаване.";
  const nextStep = p.status === "COMPLETED" ? "Записаният избор ще се използва при започване на нова програма."
    : p.status === "PAUSED" ? "Избери „Продължи с текущите настройки“, после прегледай актуалното предложение."
    : record.stale || !p.proposal || obsolete || !proposed ? "Избери „Обнови сега“, после „Прегледай“, за да провериш актуалното предложение за режима."
    : proposalHasIncompleteLoad(p.proposal) ? "Изборът е записан, но предложението не може да се утвърди заради непълна история на натоварването. Това задържа и смяната на режима. Нужно е да се изяснят непокритите участъци; дотогава действа утвърденият график."
    : "Избери „Прегледай“ в предложението по-долу, после „Утвърди предложението“, когато условията позволяват това.";
  return <div aria-label="Режим на седмицата">
    <dl className="management-week-modes">
      <div><dt>{p.status === "COMPLETED" ? "Режим на завършената програма" : "Режим на утвърдената програма"}</dt><dd>{WEEK_MODE_LABEL[p.mode]}</dd></div>
      {saved && <div className={pending ? "management-mode-pending" : undefined}><dt>Записан избор в профила</dt><dd>{WEEK_MODE_LABEL[saved]}{pending && <span>Смяната още не е приложена</span>}</dd></div>}
    </dl>
    <p>{behavior}</p>
    {pending && <p className="management-mode-next" role="status">Запазването в профила не сменя режима на вече утвърдената програма. {nextStep}</p>}
    {proposed && proposed !== p.mode && <p>Предложена смяна към {proposed === "AUTO" ? "адаптивна" : "фиксирана"} седмица. {obsolete ? "Това предложение не съвпада със записания избор. Обнови го преди утвърждаване." : "Текущият режим остава до утвърждаване."}</p>}
  </div>;
}
