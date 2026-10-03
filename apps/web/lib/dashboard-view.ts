import { parseCompletedWork, type CompletedWork } from "./completed-work";
import { parseLoadHistory, type LoadHistory } from "./load-history";
import { parseRecoveryHistory, type RecoveryHistory } from "./recovery-history";
import { exactKeys, finite, isCalendarDate, isRecord, parseTrainingStatus, type TrainingStatus } from "./training-status";
import { parseVolumeHistory, type VolumeHistory } from "./volume-history";

export interface DashboardView {
  schema_version: "dashboard-view-v1";
  generation_id: string | null;
  revision: number;
  analysis_as_of: string | null;
  activated_at: string | null;
  training_status: TrainingStatus | null;
  completed_work: CompletedWork | null;
  load_history: LoadHistory | null;
  recovery_history: RecoveryHistory | null;
  volume_history: VolumeHistory | null;
}

const rootKeys = [
  "schema_version",
  "generation_id",
  "revision",
  "analysis_as_of",
  "activated_at",
  "training_status",
  "completed_work",
  "load_history",
  "recovery_history",
  "volume_history",
];
const identifier = (value: unknown): value is string =>
  typeof value === "string" && value.length > 0 && value.length <= 128;
const nullableTimestamp = (value: unknown): value is string | null =>
  value === null || (
    typeof value === "string" &&
    value.length <= 64 &&
    finite(Date.parse(value))
  );

export function parseDashboardView(value: unknown): DashboardView {
  if (!isRecord(value) || !exactKeys(value, rootKeys) || value.schema_version !== "dashboard-view-v1")
    throw new Error("API услугата върна невалиден dashboard view.");
  if (
    !(value.generation_id === null || identifier(value.generation_id)) ||
    !Number.isInteger(value.revision) ||
    Number(value.revision) < 0 ||
    !(value.analysis_as_of === null || isCalendarDate(value.analysis_as_of)) ||
    !nullableTimestamp(value.activated_at)
  ) throw new Error("API услугата върна невалидна версия на анализа.");

  const empty = value.generation_id === null;
  if (empty !== (value.revision === 0))
    throw new Error("API услугата върна несъгласувана версия на анализа.");

  const trainingStatus = value.training_status === null ? null : parseTrainingStatus(value.training_status);
  const completedWork = value.completed_work === null ? null : parseCompletedWork(value.completed_work);
  const loadHistory = value.load_history === null ? null : parseLoadHistory(value.load_history);
  const recoveryHistory = value.recovery_history === null ? null : parseRecoveryHistory(value.recovery_history);
  const volumeHistory = value.volume_history === null ? null : parseVolumeHistory(value.volume_history);
  const hasAggregateData = Boolean(trainingStatus || completedWork || loadHistory || recoveryHistory || volumeHistory);
  if ((!empty || hasAggregateData) && (!trainingStatus || !loadHistory))
    throw new Error("Активната версия не съдържа задължителния тренировъчен анализ.");

  return {
    schema_version: "dashboard-view-v1",
    generation_id: value.generation_id as string | null,
    revision: value.revision as number,
    analysis_as_of: value.analysis_as_of as string | null,
    activated_at: value.activated_at as string | null,
    training_status: trainingStatus,
    completed_work: completedWork,
    load_history: loadHistory,
    recovery_history: recoveryHistory,
    volume_history: volumeHistory,
  };
}
