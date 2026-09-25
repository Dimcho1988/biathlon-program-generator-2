import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { renderToStaticMarkup } from "react-dom/server";
import { TrainingManagement } from "../components/training-management";
import { currentAuthorizedAthlete } from "../lib/account-access";
import { availabilityMode, trainingDays, defaultPlanningControls, defaultManagementProfile, parseDraftRecord, parseDrafts, parseManagementProfile, parseManagementProfileResponse, parseManagementOutlook, COMPONENTS } from "../lib/training-management";
import { revalidatePath } from "next/cache";
import { GET, POST, PUT } from "../app/api/athlete/management/[...path]/route";

vi.mock("../lib/account-access", () => ({ currentAuthorizedAthlete: vi.fn() }));
vi.mock("next/cache", () => ({revalidatePath: vi.fn()}));
vi.mock("../lib/api-readiness", () => ({ waitForApi: vi.fn() }));

const profile = { ...defaultManagementProfile("2026-09-21"), discipline: "5000 m" };
const vector = { Z1: 95, Z2: 95, Z3: 95, Z4: 95, Z5: 95, STR: 95 };
const record = parseDraftRecord({
  entry_key: "2026-09-22", revision: 1, stale: false, recorded_at: "2026-09-21T09:00:00Z",
  payload: {
    schema_version: "planning-draft-v1", engine_version: "training-management-draft-v1", status: "LIMITED_DRAFT", start_date: "2026-09-22", end_date: "2026-09-28",
    source: { history_days: 40, speed_model_version: "speed-duration-v1", recovery_model_version: "recovery-v2" },
    warnings: [{ code: "REVIEW", message: "Проект за треньорски преглед." }], parameters: {}, periodization: { phases: [{ kind: "SPECIAL_PREPARATION", start_date: "2026-09-21", end_date: "2026-10-01", days: 11, reason: "Оставаща част от подготовката." }] },
    days: [{ date: "2026-09-22", status: "TRAINING", period: "SPECIAL_PREPARATION", taper: false, explanation: "Поддържаме аеробната работа при достатъчна готовност.",
      readiness_before: vector, readiness_after: { ...vector, Z2: 81 }, rejected_alternatives: [],
      load_budget: { remaining_weekly_minutes: 240, components: Object.fromEntries(COMPONENTS.map(zone => [zone, { e7_daily: 5, e40_daily: 6, index_7_40: .83, target_weekly_effective: 42, rolling_7d_effective: 35, deficit_effective: 7 }])) },
      session: { method_id: "Z2-CONTINUOUS", title: "Равномерна аеробна работа", sport: "Run", zone: "Z2", purpose: "BUILD", main_work_minutes: 30, total_minutes: 45,
        canonical_effective_load: vector, direct_equivalent_minutes: vector,
        blocks: [
          { kind: "WARMUP", label: "Загрявка", zone: "Z1", duration_min: 10, target_hr_bpm: 130, target_speed_kmh: null, repetition: null, instructions: "Плавно леко движение." },
          { kind: "WORK", label: "Основна работа", zone: "Z2", duration_min: 30, target_hr_bpm: 145, target_speed_kmh: null, repetition: null, instructions: "Запазете равномерно усилие." },
          { kind: "COOLDOWN", label: "Разпускане", zone: "Z1", duration_min: 5, target_hr_bpm: 125, target_speed_kmh: null, repetition: null, instructions: "Постепенно намалете темпото." },
        ],
        dose_evidence: { capacity_source: "EXPERT_CONTINUOUS_TREF", capacity_minutes: 60, target_hr_bpm: 145, target_speed_kmh: null, fraction: .5, requested_work_minutes: 30, prescribed_work_minutes: 30, model_version: "expert-continuous-capacity-v1", technical_spill_reference: vector,
          limits: [{ code: "DAILY_AVAILABLE_WORK", limit_minutes: 45 }], fallback_reasons: ["OUTSIDE_OBSERVED_TEST_DURATION_SUPPORT"], explanation: "Използвана е резервна оценка за неподкрепения диапазон." },
      },
    }],
  },
});

describe("management data and review interface", () => {
  it("defaults to historical planning and preserves explicit day limits", () => {
    expect(availabilityMode(profile)).toBe("AUTO_HISTORY");
    expect(trainingDays(profile)).toEqual([0,1,2,3,4,5,6]);
    const old = {...profile, availability_mode: null, training_days: null};
    expect(availabilityMode(old)).toBe("AUTO_HISTORY");
    expect(availabilityMode({...old,available_minutes:[90,90,0,60,90,120,0]})).toBe("MANUAL");
    expect(availabilityMode({...old,availability_mode:"MANUAL"})).toBe("MANUAL");
    expect(parseManagementProfile({...profile,training_days:[0,2],planning_controls:{...defaultPlanningControls("Run"),intensity_days:[2]}}).training_days).toEqual([0,2]);
    expect(()=>parseManagementProfile({...profile,training_days:[0,2],planning_controls:{...defaultPlanningControls("Run"),intensity_days:[1]}})).toThrow();
  });
  it("does not present automatic time as zero or warn of a fictitious time shortage", () => {
    const auto = structuredClone(record);
    auto.payload.parameters = {availability_mode:"AUTO_HISTORY",available_weekly_minutes:null,weekly_minutes_ceiling:null,historical_training_weekly_minutes:840,volume_governor:"COMPONENT_7_40"};
    const html=renderToStaticMarkup(<TrainingManagement athleteName="Спортист" canEdit initialProfile={{configured:true,profile,revision:1}} initialDrafts={[auto]} today="2026-09-21"/>);
    expect(html).toContain("7/40 по компоненти");
    expect(html).toContain("Автоматично");
    expect(html).not.toContain("Свободното време в профила е под историческия обем");
    expect(html).toContain("Кои качества тренираме тази седмица?");
  });
  it("shows the current saved outlook without borrowing a stale weekly draft", () => {
    const outlook = parseManagementOutlook({ configured: true, outlook: {
      schema_version: "training-outlook-preview-v1", profile_revision: 8, generated_at: "2026-09-21T10:00:00Z",
      volume_context: { historical_training_weekly_minutes: 744, available_weekly_minutes: 390 },
      long_term: { schema_version: "training-outlook-v1", weeks: [{ start_date: "2026-09-21", end_date: "2026-09-27", accents: ["Z4"], phases: ["SPECIAL_PREPARATION"], volume_budget_minutes: 390,
        components: { Z4: { target_period_q: 58, target_weekly_effective: 321, target_index_7_40: 1.65 } } }] },
      periodization: { phases: [] }, history_comparison: [],
    } });
    const props = { athleteName: "Спортист", canEdit: true, initialProfile: { configured: true, profile, revision: 8 }, initialDrafts: [record], today: "2026-09-21" };
    const html = renderToStaticMarkup(<TrainingManagement {...props} initialView="overview" initialOutlook={outlook} />);
    expect(html).toContain("Актуални цели от записания профил");
    expect(html).toContain("версия 8");
    expect(html).not.toContain("1,65");
    expect(html).not.toContain("321");
    expect(html).toContain("0:58:00");
    expect(html).toContain("Записаното свободно време е по-малко");
    expect(html).not.toContain("Равномерна аеробна работа");
    expect(html).not.toContain("Показана е запазената версия");
    const absent = renderToStaticMarkup(<TrainingManagement {...props} initialView="overview" />);
    expect(absent).not.toContain("Равномерна аеробна работа");
    expect(() => parseManagementOutlook({ configured: true, outlook: record.payload })).toThrow();
  });
  it("accepts a valid profile, keeps unknown history null and rejects contradictory input", () => {
    expect(parseManagementProfile(profile).recent_weekly_hours).toBeNull();
    for (const bad of [{ available_minutes: [60] }, { actual_sport: "RollerSki" }, { recent_weekly_hours: [1, 2, 3] }, { age_years: 20, training_experience_years: 25 }, { building_fraction: .7 }]) expect(() => parseManagementProfile({ ...profile, ...bad })).toThrow();
    expect(() => parseManagementProfileResponse({ configured: false, profile, revision: 0 })).toThrow();
  });
  it("rejects malformed dates, readiness and incoherent work durations", () => {
    expect(parseDrafts({ drafts: [record] })).toHaveLength(1);
    const wrongDate = structuredClone(record); wrongDate.payload.days[0].date = "2026-02-30";
    expect(() => parseDraftRecord(wrongDate)).toThrow();
    const wrongReadiness = structuredClone(record); wrongReadiness.payload.days[0].readiness_before.Z3 = 110;
    expect(() => parseDraftRecord(wrongReadiness)).toThrow();
    const wrongDuration = structuredClone(record); wrongDuration.payload.days[0].session!.main_work_minutes = 60;
    expect(() => parseDraftRecord(wrongDuration)).toThrow();
  });
  it("explains capacity, fallback and projected readiness in Bulgarian with exact durations", () => {
    const html = renderToStaticMarkup(<TrainingManagement athleteName="Тестов спортист" canEdit initialProfile={{ configured: true, profile, revision: 1 }} initialDrafts={[record]} today="2026-09-21" />);
    for (const expected of ["Равномерна аеробна работа", "0:45:00", "0:30:00", "Защо тази задача и доза?", "Експертен Tref", "Избраната продължителност е извън диапазона", "Специално подготвителен", "90% готовност не означава 90%", "Изтегли пълния отчет"]) expect(html).toContain(expected);
    expect(html).not.toContain("NaN");
    expect(html).not.toContain("Активирай");
  });
  it("hides obsolete programme menus and all outdated training totals", () => {
    const stale = structuredClone(record); stale.stale = true; stale.payload.days[0].readiness_before.Z1 = null;
    stale.payload.parameters = { available_weekly_minutes: 390, historical_training_weekly_minutes: 840, weekly_minutes_ceiling: 390 };
    const html = renderToStaticMarkup(<TrainingManagement athleteName="Спортист" canEdit={false} initialProfile={{ configured: true, profile, revision: 2 }} initialDrafts={[stale]} today="2026-09-21" />);
    expect(html).toContain('fieldset disabled=""');
    const current = html.split('<details class="management-panel" id="plan-details">')[0];
    expect(current).toContain("Показваният досега проект е остарял");
    expect(current).not.toContain("6:30:00");
    expect(current).not.toContain("Равномерна аеробна работа");
    expect(html).not.toContain("Предишен проект — само за справка");
    expect(html).not.toContain("6:30:00");
    expect(html).not.toContain("Равномерна аеробна работа");
    expect(html).not.toContain("Запазени програми");
  });
});

describe("management API access and optimistic revision", () => {
  const access = { userId: "athlete", actorUserId: "coach", athleteAlias: "ath-test", displayName: "Fixture", isOwner: false, canEditPlan: true, canViewPlan: true, canViewRecovery: false };
  const context = (path: string) => ({ params: Promise.resolve({ path: path.split("/") }) });
  const request = (method: string, body?: unknown, origin = "https://web.example.test") => new Request("http://internal:3000/api/athlete/management/profile", { method, headers: { origin, "x-forwarded-host": "web.example.test", "x-forwarded-proto": "https", "Content-Type": "application/json" }, ...(body === undefined ? {} : { body: JSON.stringify(body) }) });
  beforeEach(() => {
    vi.mocked(currentAuthorizedAthlete).mockResolvedValue(access);
    process.env.ONFLOWS_API_BASE_URL = "https://api.example.test";
    process.env.ONFLOWS_SERVICE_TOKEN = "private-test-token";
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(Response.json({ saved: true })));
  });
  afterEach(() => { vi.clearAllMocks(); vi.unstubAllGlobals(); delete process.env.ONFLOWS_API_BASE_URL; delete process.env.ONFLOWS_SERVICE_TOKEN; });
  it("uses verified identities and only forwards expected mutation fields", async () => {
    const result = await PUT(request("PUT", { profile, expected_revision: 3, athlete_alias: "other", actor_id: "forged" }), context("profile"));
    expect(result.status).toBe(200);
    expect(fetch).toHaveBeenCalledWith(new URL("https://api.example.test/api/v2/athlete/management/profile"), expect.objectContaining({ headers: expect.objectContaining({ "X-OnFlows-Athlete-Alias": "ath-test", "X-OnFlows-Actor-Id": "coach", Authorization: "Bearer private-test-token" }), body: expect.any(String) }));
    expect(JSON.parse(vi.mocked(fetch).mock.calls[0][1]!.body as string)).toEqual({ profile, expected_revision: 3 });
    expect(await result.text()).not.toContain("private-test-token");
  });
  it("permits an authorized reader but prevents generation and cross-origin changes", async () => {
    vi.mocked(currentAuthorizedAthlete).mockResolvedValue({ ...access, canEditPlan: false });
    expect((await GET(request("GET"), context("drafts"))).status).toBe(200);
    expect((await POST(request("POST", {}), context("generate"))).status).toBe(403);
    vi.mocked(currentAuthorizedAthlete).mockResolvedValue(access);
    expect((await PUT(request("PUT", {}, "https://attacker.test"), context("profile"))).status).toBe(403);
    expect(fetch).toHaveBeenCalledTimes(1);
  });
  it("reads the live outlook with plan permissions and refuses it as a write endpoint", async () => {
    vi.mocked(currentAuthorizedAthlete).mockResolvedValue({ ...access, canEditPlan: false });
    expect((await GET(request("GET"), context("outlook"))).status).toBe(200);
    expect(fetch).toHaveBeenCalledWith(new URL("https://api.example.test/api/v2/athlete/management/outlook"), expect.objectContaining({ cache: "no-store", method: "GET" }));
    expect((await POST(request("POST", {}), context("outlook"))).status).toBe(404);
    vi.mocked(currentAuthorizedAthlete).mockResolvedValue({ ...access, canViewPlan: false });
    expect((await GET(request("GET"), context("outlook"))).status).toBe(403);
    expect(fetch).toHaveBeenCalledTimes(1);
  });
  it("rejects anonymous access, unsupported paths, missing versions and large bodies", async () => {
    vi.mocked(currentAuthorizedAthlete).mockResolvedValue(null);
    expect((await GET(request("GET"), context("profile"))).status).toBe(401);
    vi.mocked(currentAuthorizedAthlete).mockResolvedValue(access);
    expect((await POST(request("POST", {}), context("profile"))).status).toBe(404);
    expect((await GET(request("GET"), context("drafts/other-athlete"))).status).toBe(404);
    expect((await POST(request("POST", { start_date: "2026-09-22" }), context("generate"))).status).toBe(422);
    expect((await PUT(request("PUT", { profile: "а".repeat(11_000) }), context("profile"))).status).toBe(413);
    expect(fetch).not.toHaveBeenCalled();
  });
  it("does not disclose training plans to an overview-only viewer", async () => {
    vi.mocked(currentAuthorizedAthlete).mockResolvedValue({ ...access, canViewPlan: false, canEditPlan: false });
    expect((await GET(request("GET"), context("profile"))).status).toBe(403);
    expect((await GET(request("GET"), context("drafts"))).status).toBe(403);
    expect(fetch).not.toHaveBeenCalled();
  });
  it("forwards only a valid date filter to find revisions outside the recent history", async () => {
    const valid = new Request("http://internal:3000/api/athlete/management/drafts?start_date=2026-09-22&athlete_alias=other");
    expect((await GET(valid, context("drafts"))).status).toBe(200);
    expect(fetch).toHaveBeenCalledWith(new URL("https://api.example.test/api/v2/athlete/management/drafts?start_date=2026-09-22"), expect.any(Object));
    const invalid = new Request("http://internal:3000/api/athlete/management/drafts?start_date=2026-02-30");
    expect((await GET(invalid, context("drafts"))).status).toBe(422);
    expect(fetch).toHaveBeenCalledTimes(1);
  });
  it("previews duration with trusted scope and without invalidating saved plans", async () => {
    const body={discipline:"7.5 km",sport:"NordicSki",race_duration_min:22};
    expect((await POST(request("POST", {...body,athlete_alias:"forged"}),context("race-duration"))).status).toBe(200);
    expect(JSON.parse(vi.mocked(fetch).mock.calls[0][1]!.body as string)).toEqual(body);
    expect(revalidatePath).not.toHaveBeenCalled();
    expect((await POST(request("POST", {...body,sport:"RollerSki"}),context("race-duration"))).status).toBe(422);
    expect((await POST(request("POST", body,"https://attacker.test"),context("race-duration"))).status).toBe(403);
    vi.mocked(currentAuthorizedAthlete).mockResolvedValue({...access,canEditPlan:false});
    expect((await POST(request("POST", body),context("race-duration"))).status).toBe(403);
    expect(fetch).toHaveBeenCalledTimes(1);
  });
  it("preserves conflicts without retrying or silently replacing a newer plan", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(Response.json({ detail: "conflict" }, { status: 409 })));
    const result = await POST(request("POST", { start_date: "2026-09-22", expected_profile_revision: 3, expected_draft_revision: 2 }), context("generate"));
    expect(result.status).toBe(409);
    expect((await result.json()).error).toContain("Презаредете");
    expect(fetch).toHaveBeenCalledTimes(1);
  });
});


describe("active plan access", () => {
  const access = { userId: "athlete", actorUserId: "coach", athleteAlias: "ath-test", displayName: "Fixture", isOwner: false, canEditPlan: true, canViewPlan: true, canViewRecovery: false };
  const ctx = (path: string) => ({ params: Promise.resolve({ path: [path] }) });
  const req = (body: unknown) => new Request("https://web.test/api/athlete/management/action", { method: "POST", headers: { Origin: "https://web.test", "Content-Type": "application/json" }, body: JSON.stringify(body) });
  beforeEach(() => {
    vi.mocked(currentAuthorizedAthlete).mockResolvedValue(access);
    process.env.ONFLOWS_API_BASE_URL = "https://api.example.test";
    process.env.ONFLOWS_SERVICE_TOKEN = "private-test-token";
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(Response.json({ active: null, history: [] })));
  });
  afterEach(() => { vi.clearAllMocks(); vi.unstubAllGlobals(); delete process.env.ONFLOWS_API_BASE_URL; delete process.env.ONFLOWS_SERVICE_TOKEN; });
  it("forwards only the explicit approval revision and trusted actor", async () => {
    expect((await POST(req({ action: "APPROVE", expected_revision: 4, actor_id: "fake", plan: { status: "ACTIVE" } }), ctx("action"))).status).toBe(200);
    const options = vi.mocked(fetch).mock.calls[0][1]!;
    expect(JSON.parse(options.body as string)).toEqual({ action: "APPROVE", expected_revision: 4 });
    expect(options.headers).toMatchObject({ "X-OnFlows-Actor-Id": "coach" });
  });
  it("rejects missing versions and invented completion without actual load", async () => {
    expect((await POST(req({ action: "APPROVE" }), ctx("action"))).status).toBe(422);
    expect((await POST(req({ date: "2026-09-21", action: "MARK_COMPLETED", expected_revision: 2, note: "" }), ctx("day"))).status).toBe(422);
    expect(fetch).not.toHaveBeenCalled();
  });
  it("prevents a viewer from activating or changing a program", async () => {
    vi.mocked(currentAuthorizedAthlete).mockResolvedValue({ ...access, canEditPlan: false });
    for (const path of ["activate", "action", "day"]) expect((await POST(req({}), ctx(path))).status).toBe(403);
    expect(fetch).not.toHaveBeenCalled();
  });
});

it("identifies the exact calendar block when program start moves past it", () => {
  const cycle={name:"Септемврийски блок",kind:"BUILD",start_date:"2026-09-22",end_date:"2026-09-28",accents:["Z4","Z5"],target_index:1.72,volume_factor:1,recovery_days:7};
  const p={...profile,program_start:"2026-09-24",planning_controls:{...defaultPlanningControls("Run"),cycles:[cycle]}};
  expect(()=>parseManagementProfile(p)).toThrow(/Септемврийски блок.*2026-09-22.*2026-09-24/);
  expect(()=>parseManagementProfile({...p,program_start:"2026-09-01"})).not.toThrow();
});
