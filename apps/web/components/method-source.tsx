import Link from "next/link";
import { methodHref, type SessionMethod } from "../lib/method-library";
import { isRecord } from "../lib/training-status";

function sourceLinks(value: unknown): string[] {
  if (!isRecord(value) || !Array.isArray(value.links)) return [];
  return [...new Set(value.links.filter((url): url is string => {
    if (typeof url !== "string") return false;
    try { return ["https:", "http:"].includes(new URL(url).protocol); }
    catch { return false; }
  }))];
}

function SourceLinks({ value }: { value: unknown }) {
  const links = sourceLinks(value);
  return links.length > 0 ? <span className="method-source-links">{links.map((url, i) => <a key={url} href={url} target="_blank" rel="noopener noreferrer">{new URL(url).hostname === "doi.org" ? "DOI · публикация" : `Източник ${i + 1}`} ↗{i < links.length - 1 ? " · " : ""}</a>)}</span> : null;
}

/** Render the saved attribution and recipe; never infer a missing publication. */
export function MethodSourceDetail({ label, value, showLinks = true }: { label: string; value: unknown; showLinks?: boolean }) {
  if (typeof value === "string") return value ? <div><h4>{label}</h4><p>{value}</p></div> : null;
  if (!isRecord(value)) return null;
  const fieldLabels: Record<string, string> = { authors: "Автори", reference: value.authors === "onFlows" ? "Основа на профила" : "Публикация", locator: "Страница / раздел", provided_file: "Източников файл", evidence: "Данни", result_or_intent: "Цел / резултат", population_conditions: "Участници и условия", anchor: "Опора", work: "Работа", recovery: "Почивка", repetitions: "Повторения", notes: "Уточнение", classification: "Вид адаптация", anchor_label: "Скоростна опора", recipe_text: "Структура", zone_note: "Отчитане на товара" };
  const evidenceLabels: Record<string, string> = {
    EXPLICIT_ONFLOWS_COACH_ADAPTATION_NOT_SOURCE_EQUIVALENT: "Треньорска адаптация на onFlows. Числовите настройки са наши работни правила; не е установен еднакъв ефект с научния оригинал.",
    author_recommendation_in_narrative_review: "Авторска препоръка в обзорна публикация.",
    onFlows_coach_completion_of_published_MAS_framework: "Конкретна треньорска рецепта на onFlows по публикуваната рамка за MAS.",
    study_summarized_in_review_not_independently_reverified: "Изследване, описано в обзор; първичната публикация не е проверена отделно.",
  };
  const fieldValue = (key: string) => key === "evidence" && typeof value[key] === "string" ? evidenceLabels[value[key]] ?? value[key] : value[key] as string;
  return <div><h4>{label}</h4>{Object.entries(fieldLabels).map(([key, name]) => typeof value[key] === "string" && value[key] ? <p key={key}><strong>{name}:</strong> {fieldValue(key)}</p> : null)}{showLinks && sourceLinks(value).length > 0 && <p><SourceLinks value={value}/></p>}</div>;
}

export function SessionMethodSource({ method }: { method: SessionMethod }) {
  const source = method.source;
  const author = typeof source === "string" ? source : isRecord(source) && typeof source.authors === "string" ? source.authors : null;
  const coachSource = author === "onFlows";
  const original = typeof method.original === "string" ? method.original : isRecord(method.original)
    ? [method.original.work, method.original.repetitions, typeof method.original.recovery === "string" ? `Почивка: ${method.original.recovery}` : null].filter((v): v is string => typeof v === "string" && !!v).join(" · ") : "";
  const parents = [...new Set([...(method.derived_from ?? []), ...(method.personal_variant?.base_method_id ? [method.personal_variant.base_method_id] : [])])].filter(id => id !== method.id);
  if (!source && !method.original && !method.adaptation && !parents.length) return null;
  return <div className="method-source">
    {author && <p><strong>Източник:</strong> {author}</p>}
    {sourceLinks(source).length > 0 && <p><SourceLinks value={source}/></p>}
    {parents.length > 0 && <p><strong>Метод в основата на адаптацията:</strong> {parents.map((id, i) => <span key={id}>{i > 0 ? "; " : ""}<Link href={methodHref(id)}>{id} · {coachSource ? "отвори базовия метод" : "отвори оригинала"} в базата →</Link></span>)}</p>}
    {original && <p><strong>{coachSource ? "Базова треньорска рецепта:" : "Оригинално предписание:"}</strong> {original}</p>}
    {method.adaptation && <p><strong>Адаптация за onFlows:</strong> {typeof method.adaptation === "string" ? method.adaptation : typeof method.adaptation.recipe_text === "string" ? method.adaptation.recipe_text : typeof method.adaptation.classification === "string" ? method.adaptation.classification : "Индивидуалната рецепта е описана по-долу."}{isRecord(method.adaptation) && method.adaptation.is_source_equivalent === false && ` Адаптацията не е идентична с ${coachSource ? "базовото" : "оригиналното"} предписание.`}</p>}
    <details><summary>{coachSource ? "Основа и промени в треньорския метод" : "Източник, оригинал и промени в метода"}</summary>
      <MethodSourceDetail label="Източник" value={source} showLinks={false}/>
      <MethodSourceDetail label={coachSource ? "Базова треньорска рецепта" : "Публикувано предписание"} value={method.original}/>
      <MethodSourceDetail label="Адаптация за onFlows" value={method.adaptation}/>
    </details>
  </div>;
}
