import { isCalendarDate, isRecord } from './training-status';

export type PlanningJob = {
  job_id: string;
  status: 'QUEUED' | 'RUNNING' | 'SUCCEEDED' | 'FAILED';
  stage: 'QUEUED' | 'PREPARING' | 'BASELINE' | 'SEARCH' | 'SAVING' | 'READY' | 'FAILED';
  start_date: string;
  result_revision: number | null;
  result_active_revision?: number | null;
  error_code: string | null;
};

export function parsePlanningJob(value: unknown): PlanningJob | null {
  if (!isRecord(value) || !Object.hasOwn(value, 'job')) throw new Error('Невалидно състояние на подготовката.');
  if (value.job === null) return null;
  const job = value.job;
  if (!isRecord(job) || typeof job.job_id !== 'string'
    || !/^[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}$/i.test(job.job_id)
    || !['QUEUED', 'RUNNING', 'SUCCEEDED', 'FAILED'].includes(String(job.status))
    || !['QUEUED', 'PREPARING', 'BASELINE', 'SEARCH', 'SAVING', 'READY', 'FAILED'].includes(String(job.stage))
    || !isCalendarDate(job.start_date)
    || !(job.result_revision === null || Number.isSafeInteger(job.result_revision) && Number(job.result_revision) > 0)
    || !(job.result_active_revision === undefined || job.result_active_revision === null || Number.isSafeInteger(job.result_active_revision) && Number(job.result_active_revision) > 0)
    || !(job.error_code === null || typeof job.error_code === 'string')
    || job.status === 'SUCCEEDED' && job.result_revision === null) throw new Error('Невалидно състояние на подготовката.');
  return job as PlanningJob;
}

export const planningPending = (job: PlanningJob | null) => job?.status === 'QUEUED' || job?.status === 'RUNNING';
export const planningStageLabel = (job: PlanningJob) => ({
  QUEUED: 'Заявката е запазена и изчаква ред за изчисление.',
  PREPARING: 'Подготвяме входните данни.',
  BASELINE: 'Съставяме тренировъчната програма.',
  SEARCH: 'Проверяваме разпределението на тренировките.',
  SAVING: 'Запазваме проверената програма.',
  READY: 'Програмата е подготвена.',
  FAILED: 'Подготовката не завърши.',
}[job.stage]);

const FAILURES: Record<string, string> = {
  INPUT_CHANGED: 'Данните или профилът са променени по време на подготовката. Презареди и подготви нова програма.',
  INVALID_INPUT: 'Провери периода и профила за планиране, след което подготви програмата отново.',
  ACCESS_CHANGED: 'Правата за редакция на програмата са променени. Презареди страницата.',
  RETRIES_EXHAUSTED: 'Подготовката беше прекъсната няколко пъти. Опитай отново по-късно.',
  COMPUTATION_FAILED: 'Техническа грешка при изчисляването или записването на програмата. Нужна е техническа проверка на резултата.',
};
export const planningFailure = (job: PlanningJob) => FAILURES[job.error_code ?? '']
  ?? 'Подготовката не завърши. Опитай отново или обнови входните данни.';
