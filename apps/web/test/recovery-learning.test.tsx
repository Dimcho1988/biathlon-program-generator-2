import { beforeEach, describe, expect, it, vi } from "vitest";
import { renderToStaticMarkup } from "react-dom/server";
import { learningPaths, RecoveryLearning } from "../components/recovery-learning-view";
import { parseRecoveryLearningView, type RecoveryLearningView } from "../lib/recovery-learning";
import { currentAuthorizedAthlete } from "../lib/account-access";
import { getLearningView } from "../lib/api";
import LearningPage from "../app/learning/page";

vi.mock("../lib/account-access", () => ({ currentAuthorizedAthlete: vi.fn() }));
vi.mock("../lib/api", () => ({ getLearningView: vi.fn() }));

const view: RecoveryLearningView = {
  schema_version: "recovery-learning-view-v1", as_of: "2026-11-03", source_date: "2026-11-02", timezone: "Europe/Sofia",
  mode: "SHADOW", status: "COLLECTING", summary: "Нужни са още съпоставими реакции.", evidence_count: 0,
  validation: { status: "WARMUP", evaluated: 0 },
  series: [
    { day: "2026-11-01", actual_q: 100, stress: 65, baseline: 45 },
    { day: "2026-11-02", actual_q: null, stress: null, baseline: 45 },
    { day: "2026-11-03", actual_q: 0, stress: 45, baseline: 45 },
  ],
  episodes: [{ id: "block-1", start: "2026-10-13", load_end: "2026-10-26", end: "2026-11-03", loading_days: 14,
    recovery_limit_days: 5, recovery_days: null, recovered: false, ratio: null, status: "INSUFFICIENT_DATA",
    proposed_change_percent: 0, reason: "Липсват последователни наблюдения.", performance_change_percent: null }],
  decision: { component: "Z1", status: "HOLD", proposed_change_percent: 4, applied_change_percent: 0, reason: "Следим предложението преди приложение." },
  observations: { load_days: 2, stress_days: 2 }, channels: [{ key: "hrv", label: "HRV", observed_days: 2 }], limitations: [],
};

describe("dedicated recovery learning presentation", () => {
  it("retains missing values and rejects impossible or misleading report values", () => {
    const parsed = parseRecoveryLearningView({ ...view, memory: { private_data: "private replay" } });
    expect(parsed.series[1].stress).toBeNull();
    expect(parsed.series[1].actual_q).toBeNull();
    expect(parsed.source_date).toBe("2026-11-02");
    expect(parsed).not.toHaveProperty("memory");
    expect(() => parseRecoveryLearningView({ ...view, decision: { ...view.decision, applied_change_percent: 4 } })).toThrow();
    expect(() => parseRecoveryLearningView({ ...view, decision: { ...view.decision, proposed_change_percent: 20.1 } })).toThrow();
    expect(() => parseRecoveryLearningView({ ...view, series: [...view.series, view.series[0]] })).toThrow();
    expect(() => parseRecoveryLearningView({ ...view, series: [{ ...view.series[0], stress: NaN }] })).toThrow();
    expect(() => parseRecoveryLearningView({ ...view, series: [{ ...view.series[0], day: "2026-11-04" }] })).toThrow();
  });
  it("breaks the observed stress line across missing values and absent calendar days", () => {
    expect(learningPaths(view.series,"stress",index=>index,value=>value)).toEqual(["M0,65 ","M2,45 "]);
    expect(learningPaths([view.series[0],view.series[2]],"stress",index=>index,value=>value)).toEqual(["M0,65 ","M1,45 "]);
  });
  it("shows independent scales and separates proposal from permitted dose", () => {
    const html = renderToStaticMarkup(<RecoveryLearning view={view}/>);
    for (const text of ["Наблюдение", "Q · еквивалентни минути за деня", "Стрес · точки от 0 до 100", "Предложена промяна", "+4%", "Допуснато от модела", "0%", "Недостатъчни наблюдения", "Няма съпоставим резултат", "Няма данни", 'href="/response"']) expect(html).toContain(text);
    expect(html).not.toContain("NaN");
    expect(html).not.toContain(' open=""');
  });
  it("shows an honest empty state with a route to observations", () => {
    const html = renderToStaticMarkup(<RecoveryLearning view={{ ...view, series: [], episodes: [], channels: [] }}/>);
    expect(html).toContain("Няма годни измерени данни");
    expect(html).toContain("Няма достатъчно независими наблюдения");
    expect(html).toContain("Няма завършен съпоставим блок");
    expect(html).not.toContain("<svg");
  });
  it("does not equate reaction episodes with observed training benefit", () => {
    const html=renderToStaticMarkup(<RecoveryLearning view={{...view,evidence_count:2,observations:{reaction_episodes:2,performance_observations:0}}}/>);
    expect(html).toContain("Наблюдаваната реакция се отчита");
    expect(html).toContain("Още няма съпоставим резултат за оценка на тренировъчния ефект");
  });
  it("distinguishes absent stress elevation from delayed recovery in both the chart and table", () => {
    const summary="Основанието за текущото решение се показва веднъж.";
    const labels={NO_ELEVATED_RESPONSE:"Няма отчетено повишение на стреса",DELAYED:"Няма потвърдено връщане в експертния срок"};
    for(const [status,label] of Object.entries(labels)) {
      const html=renderToStaticMarkup(<RecoveryLearning view={{...view,summary,decision:{...view.decision,reason:summary},
        episodes:[{...view.episodes[0],status,reason:"Описание на наблюдението."}]}}/>);
      expect(html.split(label)).toHaveLength(3);
      expect(html).not.toContain(labels[status==="DELAYED"?"NO_ELEVATED_RESPONSE":"DELAYED"]);
      expect(html).not.toContain("Връщането още не е потвърдено");
      expect(html.split(summary)).toHaveLength(2);
    }
  });
});

describe("private learning page", () => {
  const access = { userId: "owner", actorUserId: "coach", athleteAlias: "ath-private", displayName: "Избран спортист", isOwner: false, canEditPlan: true, canViewRecovery: true };
  beforeEach(() => { vi.clearAllMocks(); vi.mocked(currentAuthorizedAthlete).mockResolvedValue(access); vi.mocked(getLearningView).mockResolvedValue(view); });
  it("blocks a plan-only coach before fetching any private observations", async () => {
    vi.mocked(currentAuthorizedAthlete).mockResolvedValue({ ...access, canViewRecovery: false });
    const html = renderToStaticMarkup(await LearningPage({ searchParams: Promise.resolve({}) }));
    expect(getLearningView).not.toHaveBeenCalled();
    expect(html).toContain("не е споделил данните си за възстановяване");
    expect(html).not.toContain("block-1");
    expect(html).not.toContain("Наблюдавана реакция");
  });
  it("keeps the calculation date separate from the imported history cutoff", async () => {
    const html = renderToStaticMarkup(await LearningPage({ searchParams: Promise.resolve({ days: "180" }) }));
    expect(getLearningView).toHaveBeenCalledWith("ath-private",90);
    expect(html).toContain("оценка към 03.11.2026");
    expect(html).toContain("Натоварване до 02.11.2026");
    expect(html).not.toContain("Последни 180 дни");
  });
});
