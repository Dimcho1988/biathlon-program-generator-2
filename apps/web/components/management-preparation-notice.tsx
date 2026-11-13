import type {ManagementPreparation} from "../lib/management-overview";
import {PreparedViewRetry} from "./prepared-view-retry";

const REASONS: Record<string, string> = {
  INPUTS_CHANGED: "Има нови данни или променени настройки.",
  DAY_CHANGED: "Подготвя се оценката за новия ден.",
  MODEL_CHANGED: "Изчисленията се обновяват след промяна на модела.",
};

export function ManagementPreparationNotice({preparation, timezone = "UTC"}: {
  preparation: ManagementPreparation | null; timezone?: string;
}) {
  if (!preparation) return null;
  const day = preparation.evaluated_on
    ? new Date(`${preparation.evaluated_on}T12:00:00Z`).toLocaleDateString("bg-BG", {timeZone: "UTC"}) : null;
  const preparedAt = preparation.prepared_at;
  const timestamp = preparedAt ? new Date(preparedAt).toLocaleString("bg-BG", {
    timeZone: timezone, day: "2-digit", month: "2-digit", year: "numeric", hour: "2-digit", minute: "2-digit",
  }) : null;
  const provenance = <>{timestamp && <>Подготвен на <time dateTime={preparedAt!}>{timestamp}</time>. </>}
    {day && <>Оценка към <time dateTime={preparation.evaluated_on!}>{day}</time>.</>}</>;
  if (preparation.status === "READY") return <p className="management-muted" aria-label="Актуалност на изгледа">{provenance}</p>;
  return <section className="management-notice" aria-label="Обновяване на програмата">
    <p role="status">Показана е последната налична програма. {REASONS[preparation.reason ?? ""] ?? "Подготвя се нов изглед."} Обновяването продължава във фонов режим. Дозите може да не отразяват последните промени.</p>
    <p>{provenance}</p>
    <PreparedViewRetry compact/>
  </section>;
}
