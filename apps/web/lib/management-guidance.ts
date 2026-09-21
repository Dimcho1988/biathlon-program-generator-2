import type { DraftRecord } from "./training-management";
import { syncInProgress, type SyncState } from "./sync";

export type ManagementStep = "PROFILE" | "SYNC" | "WAIT" | "GENERATE" | "START_DATE" | "REVIEW" | "CHECK";
export interface ManagementGuidance { step: ManagementStep; title: string; description: string }

// Presentation only. Optional method profiles never grant or prevent activation;
// the engine's eligibility flag and the server's publication checks still decide.
export function managementGuidance({ configured, dirty, draft, sync, today }: {
  configured: boolean; dirty: boolean; draft?: DraftRecord; sync?: SyncState | null; today: string;
}): ManagementGuidance {
  if (!configured || dirty) return { step: "PROFILE", title: configured ? "Запази промените в профила" : "За какво се подготвяш?", description: configured ? "Новата програма ще използва запазените настройки." : "Посочи спорта, целта и времето за тренировки. Началните настройки са попълнени." };
  if (sync && syncInProgress(sync)) return { step: "WAIT", title: "Обновяваме тренировките", description: "Остани тук. Екранът ще се обнови автоматично, когато данните са готови." };
  const freshGeneration = !!sync?.active_generation_id && !!draft?.payload.source.generation_id && sync.active_generation_id !== draft.payload.source.generation_id;
  const staleAnalysis = !!sync?.analysis_as_of && sync.analysis_as_of < today;
  if (freshGeneration && !staleAnalysis) return { step: "GENERATE", title: "Данните са обновени", description: "Подготви програмата с последните тренировки и текущите настройки." };
  const codes = new Set(draft?.payload.warnings.map(w => w.code));
  if (codes.has("STALE_LOAD_SNAPSHOT") || codes.has("ACTUAL_LOAD_WITHOUT_SESSION_METADATA") || staleAnalysis) return {
    step: "SYNC", title: "Първо обнови тренировките", description: "Има дни без потвърдени данни. Обновяването от Intervals ще позволи да оценим днешната готовност.",
  };
  if (!draft || draft.stale !== false || codes.has("INPUT_GENERATION_CHANGED") || draft.payload.start_date < today) return { step: "GENERATE", title: draft ? "Подготви актуална програма" : "Готови сме да подготвим програмата", description: "Ще видиш конкретните тренировки за следващите дни и ще ги прегледаш преди започване." };
  if (codes.has("WEEKLY_VOLUME_REQUIRED")) return { step: "PROFILE", title: "Липсва досегашният тренировъчен обем", description: "В профила въведи обема за последните четири седмици. За готовността са нужни и актуални записи на тренировките." };
  if (codes.has("UNKNOWN_INTERVENING_LOAD")) return { step: "START_DATE", title: "Избери начало днес или утре", description: "За по-далечно начало още не знаем междинните тренировки. Програмата се уточнява според реалното изпълнение." };
  if (draft.payload.activation_eligible === true) return { step: "REVIEW", title: "Програмата е готова за преглед", description: "Избери ден, за да видиш тренировката. Натисни „Започни програмата“, когато си готов." };
  if (codes.has("LIMITED_LOAD_HISTORY") || codes.has("NO_ACTUAL_MODE_EXPOSURE") || codes.has("AGGREGATE_HISTORY_ONLY")) return { step: "CHECK", title: "Нужна е повече тренировъчна история", description: "Наличните данни още не позволяват да потвърдим дневната доза. Провери дали тренировките за избрания спорт са внесени." };
  return { step: "CHECK", title: "Програмата още не е готова за започване", description: "Нужен е преглед на данните или настройките. Конкретните причини са в подробностите по-долу." };
}

export function hasProgramDays(draft: DraftRecord["payload"]) {
  return draft.status !== "BLOCKED" && draft.days.some(day => day.status !== "REVIEW_REQUIRED");
}
