import { renderToStaticMarkup } from "react-dom/server";
import { afterEach, describe, expect, it, vi } from "vitest";
import { Dashboard } from "../components/dashboard";
import { getCompletedWork, getDashboardView, getLoadHistory, getRecoveryHistory, getTrainingStatus, getVolumeHistory } from "../lib/api";
import { completedWorkFixture, loadHistoryFixture, recoveryHistoryFixture, trainingStatusFixture, volumeHistoryFixture } from "../lib/fixture";
import { parseCompletedWork } from "../lib/completed-work";
import { parseLoadHistory } from "../lib/load-history";
import { parseTrainingStatus } from "../lib/training-status";
import { parseRecoveryHistory } from "../lib/recovery-history";
import { parseVolumeHistory } from "../lib/volume-history";
import { applyTheme, resolveInitialTheme, THEME_STORAGE_KEY } from "../components/theme-toggle";
import { ErrorState } from "../components/error-state";
import { waitForApi } from "../lib/api-readiness";

describe("training-status-v1 contract", () => {
  it("accepts the canonical fixture", () => expect(parseTrainingStatus(trainingStatusFixture)).toEqual(trainingStatusFixture));
  it("rejects malformed and extra fields", () => {
    expect(() => parseTrainingStatus({ ...trainingStatusFixture, schema_version: "v2" })).toThrow(/версия/);
    expect(() => parseTrainingStatus({ ...trainingStatusFixture, unexpected: true })).toThrow(/структура/);
    const malformedZones = trainingStatusFixture.zones.map((zone) =>
      zone.zone === "Z1" ? { ...zone, raw_time_min: "38.4" } : zone,
    );
    expect(() => parseTrainingStatus({ ...trainingStatusFixture, zones: malformedZones })).toThrow(/зонални/);
  });
  it("requires exactly five zones in exact Z1–Z5 order", () => {
    expect(() => parseTrainingStatus({ ...trainingStatusFixture, zones: trainingStatusFixture.zones.slice(0, 4) })).toThrow(/точно Z1–Z5/);
    expect(() => parseTrainingStatus({ ...trainingStatusFixture, zones: [] })).toThrow(/точно Z1–Z5/);
    const reordered = [...trainingStatusFixture.zones];
    [reordered[0], reordered[1]] = [reordered[1], reordered[0]];
    expect(() => parseTrainingStatus({ ...trainingStatusFixture, zones: reordered })).toThrow(/неподредени/);
  });
  it("rejects impossible calendar dates", () => {
    expect(() => parseTrainingStatus({ ...trainingStatusFixture, as_of: "2026-02-31" })).toThrow(/дата/);
    expect(() => parseTrainingStatus({ ...trainingStatusFixture, as_of: "2025-02-29" })).toThrow(/дата/);
    expect(parseTrainingStatus({ ...trainingStatusFixture, as_of: "2024-02-29" }).as_of).toBe("2024-02-29");
  });
});

describe("load-history-v1/v2 contract", () => {
  it("accepts the aggregate fixture", () => expect(parseLoadHistory(loadHistoryFixture)).toEqual(loadHistoryFixture));
  it("normalizes harmless floating-point drift at the percentage boundary", () => {
    const activities = loadHistoryFixture.activities.map((activity, index) =>
      index === 0 ? { ...activity, hr_coverage_percent: 100.00000000000001 } : activity,
    );
    expect(parseLoadHistory({ ...loadHistoryFixture, activities }).activities[0].hr_coverage_percent).toBe(100);
    expect(() => parseLoadHistory({
      ...loadHistoryFixture,
      activities: activities.map((activity, index) =>
        index === 0 ? { ...activity, hr_coverage_percent: 100.01 } : activity,
      ),
    })).toThrow(/Невалидна активност/);
  });
  it("rejects incomplete daily zone groups and provider-shaped extras", () => {
    expect(() => parseLoadHistory({ ...loadHistoryFixture, daily: loadHistoryFixture.daily.slice(0, -1) })).toThrow(/дневна история/);
    expect(() => parseLoadHistory({ ...loadHistoryFixture, provider_athlete_id: "private" })).toThrow(/структура/);
  });
  it("requires persisted per-day Tref provenance in v2 while retaining v1 compatibility", () => {
    const version2 = {
      ...loadHistoryFixture,
      schema_version: "load-history-v2",
      tref_bounds_profile_version: "tref-bounds-profile-v1",
      daily: loadHistoryFixture.daily.map((row) => ({ ...row, tref_used_min: 120 })),
      strength: loadHistoryFixture.strength ? {
        ...loadHistoryFixture.strength,
        daily: loadHistoryFixture.strength.daily.map((row) => ({ ...row, tref_used_min: 90 })),
      } : loadHistoryFixture.strength,
    };
    expect(parseLoadHistory(version2).schema_version).toBe("load-history-v2");
    expect(parseLoadHistory(version2).daily[0].tref_used_min).toBe(120);
    expect(() => parseLoadHistory({ ...version2, tref_bounds_profile_version: null })).toThrow(/границите за Tref/);
    expect(() => parseLoadHistory({ ...version2, daily: loadHistoryFixture.daily })).toThrow(/дневен ред/);
  });

  it("accepts current API serialization of a legacy v1 history", () => {
    const serializedLegacy = {
      ...loadHistoryFixture,
      tref_bounds_profile_version: null,
      daily: loadHistoryFixture.daily.map((row) => ({ ...row, tref_used_min: null })),
      strength: {
        ...loadHistoryFixture.strength!,
        daily: loadHistoryFixture.strength!.daily.map((row) => ({ ...row, tref_used_min: null })),
      },
    };
    expect(parseLoadHistory(serializedLegacy).schema_version).toBe("load-history-v1");
  });
});

describe("completed-work-v1 contract", () => {
  it("accepts the aggregate fixture", () => expect(parseCompletedWork(completedWorkFixture)).toEqual(completedWorkFixture));
  it("rejects altered metadata, incomplete zones and duplicate provider labels", () => {
    expect(() => parseCompletedWork({ ...completedWorkFixture, model: { ...completedWorkFixture.model, sport_grouping: "mapped" } })).toThrow(/метаданни/);
    expect(() => parseCompletedWork({ ...completedWorkFixture, zones: completedWorkFixture.zones.slice(0, 4) })).toThrow(/точно Z1–Z5/);
    expect(() => parseCompletedWork({ ...completedWorkFixture, sports: [completedWorkFixture.sports[0], completedWorkFixture.sports[0]] })).toThrow(/вид активност/);
  });
  it("accepts an aggregate derived from load-history-v2", () => {
    const report = { ...completedWorkFixture, model: { ...completedWorkFixture.model, source_schema_version: "load-history-v2" } };
    expect(parseCompletedWork(report).model.source_schema_version).toBe("load-history-v2");
  });
});

describe("volume-history-v1 contract", () => {
  it("accepts the calendar-week fixture", () => expect(parseVolumeHistory(volumeHistoryFixture)).toEqual(volumeHistoryFixture));
  it("rejects altered aggregation semantics and inconsistent quality totals", () => {
    expect(() => parseVolumeHistory({ ...volumeHistoryFixture, model: { ...volumeHistoryFixture.model, calendar_week_start: "sunday" } })).toThrow(/метаданни/);
    expect(() => parseVolumeHistory({ ...volumeHistoryFixture, quality: { ...volumeHistoryFixture.quality, modeled_activities: 3 } })).toThrow(/качество/);
  });
  it("accepts weekly volume derived from load-history-v2", () => {
    const history = { ...volumeHistoryFixture, model: { ...volumeHistoryFixture.model, source_schema_version: "load-history-v2" } };
    expect(parseVolumeHistory(history).model.source_schema_version).toBe("load-history-v2");
  });
});

describe("recovery-history-v1 contract", () => {
  it("accepts the canonical recovery fixture", () => expect(parseRecoveryHistory(recoveryHistoryFixture)).toEqual(recoveryHistoryFixture));
  it("accepts a legacy snapshot without aggregate wellness diagnostics", () => {
    const legacy = { ...recoveryHistoryFixture };
    delete legacy.wellness_diagnostics;
    expect(parseRecoveryHistory(legacy).wellness_diagnostics).toBeUndefined();
  });
  it("rejects incomplete zones and non-load recovery claims", () => {
    expect(() => parseRecoveryHistory({ ...recoveryHistoryFixture, current: recoveryHistoryFixture.current.slice(0, 4) })).toThrow(/точно Z1–Z5/);
    expect(() => parseRecoveryHistory({ ...recoveryHistoryFixture, basis: "integrated" })).toThrow(/основа/);
  });
  it("rejects impossible wellness coverage without accepting raw values", () => {
    const diagnostics = recoveryHistoryFixture.wellness_diagnostics!;
    expect(() => parseRecoveryHistory({
      ...recoveryHistoryFixture,
      wellness_diagnostics: {
        ...diagnostics,
        fields: diagnostics.fields.map((field, index) => index === 0 ? { ...field, coverage_percent: 101 } : field),
      },
    })).toThrow(/wellness покритие/);
  });
});

describe("data access", () => {
  afterEach(() => { vi.unstubAllGlobals(); delete process.env.ONFLOWS_DATA_MODE; delete process.env.ONFLOWS_API_BASE_URL; delete process.env.ONFLOWS_API_RESOURCE; delete process.env.ONFLOWS_SERVICE_TOKEN; });
  it("returns an explicit API error without falling back to fixture", async () => {
    process.env.ONFLOWS_API_BASE_URL = "https://api.example.test";
    vi.stubGlobal("fetch", vi.fn()
      .mockResolvedValueOnce(Response.json({ status: "ok" }))
      .mockResolvedValue(new Response("failure", { status: 503 })));
    await expect(getTrainingStatus()).rejects.toThrow("API услугата върна грешка (503)");
  });
  it("retries a transient recovery gateway failure after the API is already awake", async () => {
    process.env.ONFLOWS_API_BASE_URL = "https://api.example.test";
    process.env.ONFLOWS_API_RESOURCE = "real";
    process.env.ONFLOWS_SERVICE_TOKEN = "server-secret";
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(Response.json({ status: "ok" }))
      .mockResolvedValueOnce(new Response("temporary", { status: 503 }))
      .mockResolvedValueOnce(Response.json(recoveryHistoryFixture));
    vi.stubGlobal("fetch", fetchMock);

    await expect(getRecoveryHistory()).resolves.toEqual(recoveryHistoryFixture);
    expect(fetchMock).toHaveBeenCalledTimes(3);
  });
  it("uses the protected real endpoint and server-only authorization", async () => {
    process.env.ONFLOWS_API_BASE_URL = "https://api.example.test";
    process.env.ONFLOWS_API_RESOURCE = "real";
    process.env.ONFLOWS_SERVICE_TOKEN = "server-secret";
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(Response.json({ status: "ok" }))
      .mockResolvedValueOnce(Response.json(trainingStatusFixture));
    vi.stubGlobal("fetch", fetchMock);
    await expect(getTrainingStatus()).resolves.toEqual({ data: trainingStatusFixture, mode: "api" });
    const [url, init] = fetchMock.mock.calls[1];
    expect(String(url)).toBe("https://api.example.test/api/v2/real/training-status");
    expect(init.headers.Authorization).toBe("Bearer server-secret");
  });
  it("fails closed when real server authentication is missing", async () => {
    process.env.ONFLOWS_API_BASE_URL = "https://api.example.test";
    process.env.ONFLOWS_API_RESOURCE = "real";
    const fetchMock = vi.fn(); vi.stubGlobal("fetch", fetchMock);
    await expect(getTrainingStatus()).rejects.toThrow("ONFLOWS_SERVICE_TOKEN");
    expect(fetchMock).not.toHaveBeenCalled();
  });
  it("loads the protected aggregate history without exposing the token to the browser", async () => {
    process.env.ONFLOWS_API_BASE_URL = "https://api.example.test";
    process.env.ONFLOWS_API_RESOURCE = "real";
    process.env.ONFLOWS_SERVICE_TOKEN = "server-secret";
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(Response.json({ status: "ok" }))
      .mockResolvedValueOnce(Response.json(loadHistoryFixture));
    vi.stubGlobal("fetch", fetchMock);
    await expect(getLoadHistory()).resolves.toEqual(loadHistoryFixture);
    const [url, init] = fetchMock.mock.calls[1];
    expect(String(url)).toBe("https://api.example.test/api/v2/real/load-history");
    expect(init.headers.Authorization).toBe("Bearer server-secret");
  });
  it("loads a selected completed-work period from the protected snapshot endpoint", async () => {
    process.env.ONFLOWS_API_BASE_URL = "https://api.example.test";
    process.env.ONFLOWS_API_RESOURCE = "real";
    process.env.ONFLOWS_SERVICE_TOKEN = "server-secret";
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(Response.json({ status: "ok" }))
      .mockResolvedValueOnce(Response.json(completedWorkFixture));
    vi.stubGlobal("fetch", fetchMock);
    await expect(getCompletedWork("ath-profile", "2026-06-01", "2026-06-20")).resolves.toEqual(completedWorkFixture);
    const [url, init] = fetchMock.mock.calls[1];
    expect(String(url)).toBe("https://api.example.test/api/v2/real/completed-work?period_start=2026-06-01&period_end=2026-06-20");
    expect(init.headers.Authorization).toBe("Bearer server-secret");
    expect(init.headers["X-OnFlows-Athlete-Alias"]).toBe("ath-profile");
  });
  it("loads the protected recovery history without exposing the token to the browser", async () => {
    process.env.ONFLOWS_API_BASE_URL = "https://api.example.test";
    process.env.ONFLOWS_API_RESOURCE = "real";
    process.env.ONFLOWS_SERVICE_TOKEN = "server-secret";
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(Response.json({ status: "ok" }))
      .mockResolvedValueOnce(Response.json(recoveryHistoryFixture));
    vi.stubGlobal("fetch", fetchMock);
    await expect(getRecoveryHistory()).resolves.toEqual(recoveryHistoryFixture);
    const [url, init] = fetchMock.mock.calls[1];
    expect(String(url)).toBe("https://api.example.test/api/v2/real/recovery-history");
    expect(init.headers.Authorization).toBe("Bearer server-secret");
  });
  it("loads the protected weekly volume history for the active profile", async () => {
    process.env.ONFLOWS_API_BASE_URL = "https://api.example.test";
    process.env.ONFLOWS_API_RESOURCE = "real";
    process.env.ONFLOWS_SERVICE_TOKEN = "server-secret";
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(Response.json({ status: "ok" }))
      .mockResolvedValueOnce(Response.json(volumeHistoryFixture));
    vi.stubGlobal("fetch", fetchMock);
    await expect(getVolumeHistory("ath-profile")).resolves.toEqual(volumeHistoryFixture);
    const [url, init] = fetchMock.mock.calls[1];
    expect(String(url)).toBe("https://api.example.test/api/v2/real/volume-history");
    expect(init.headers.Authorization).toBe("Bearer server-secret");
    expect(init.headers["X-OnFlows-Athlete-Alias"]).toBe("ath-profile");
  });
  it("loads one coherent generation for every dashboard resource", async () => {
    process.env.ONFLOWS_API_BASE_URL = "https://api.example.test";
    process.env.ONFLOWS_SERVICE_TOKEN = "server-secret";
    const view = {
      schema_version: "dashboard-view-v1",
      generation_id: "gen-41",
      revision: 41,
      analysis_as_of: trainingStatusFixture.as_of,
      activated_at: "2026-08-26T18:00:00Z",
      training_status: trainingStatusFixture,
      completed_work: completedWorkFixture,
      load_history: loadHistoryFixture,
      recovery_history: recoveryHistoryFixture,
      volume_history: volumeHistoryFixture,
    };
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(Response.json({ status: "ok" }))
      .mockResolvedValueOnce(Response.json(view));
    vi.stubGlobal("fetch", fetchMock);

    await expect(getDashboardView("ath-profile", "2026-06-01", "2026-06-20")).resolves.toEqual(view);
    const [url, init] = fetchMock.mock.calls[1];
    expect(String(url)).toBe("https://api.example.test/api/v2/real/dashboard-view?period_start=2026-06-01&period_end=2026-06-20");
    expect(init.headers["X-OnFlows-Athlete-Alias"]).toBe("ath-profile");
  });
});

describe("API readiness", () => {
  it("shares one wake-up probe across parallel server requests", async () => {
    let release: ((response: Response) => void) | undefined;
    const fetchMock = vi.fn().mockImplementation(() => new Promise<Response>((resolve) => { release = resolve; }));
    vi.stubGlobal("fetch", fetchMock);

    const first = waitForApi("https://api.example.test");
    const second = waitForApi("https://api.example.test");
    expect(fetchMock).toHaveBeenCalledTimes(1);
    release?.(Response.json({ status: "ok" }));
    await Promise.all([first, second]);
    vi.unstubAllGlobals();
  });
});

describe("dashboard", () => {
  it("does not offer a new OAuth connection for an already active athlete session", () => {
    const html = renderToStaticMarkup(<ErrorState
      message="API услугата не се събуди навреме."
      integrationActions
      connectAvailable={false}
      retryAvailable
    />);
    expect(html).toContain("Опитай отново");
    expect(html).toContain("Подготвяме автоматичен повторен опит");
    expect(html).not.toContain("Свържи Intervals");
  });
  it("renders the official logo and accessible theme control without losing content", () => {
    const html = renderToStaticMarkup(<Dashboard data={trainingStatusFixture} mode="fixture" />);
    expect(html).toContain('%2Fbrand%2Fonflows-mark.png');
    expect(html).toContain('alt="onFlows лого"');
    expect(html).toContain('aria-label="Превключи светла или тъмна тема"');
    expect(html).toContain("Тренировъчен статус");
  });
  it("renders an accessible ordered module navigation without client state", () => {
    const html = renderToStaticMarkup(<Dashboard data={trainingStatusFixture} mode="fixture" completedWork={completedWorkFixture} volumeHistory={volumeHistoryFixture} loadHistory={loadHistoryFixture} recoveryHistory={recoveryHistoryFixture} />);
    expect(html).toContain('aria-label="Модули на тренировъчния анализ"');
    expect([...html.matchAll(/href="(#(?:quality|zones|completed-work|volume|history|recovery)-title|#model-metadata)"/g)].map((match) => match[1])).toEqual([
      "#quality-title", "#zones-title", "#completed-work-title", "#volume-title", "#history-title", "#recovery-title", "#model-metadata",
    ]);
    for (const label of ["Качество", "Статус по зони", "Извършена работа", "Общ обем", "7/40 и зонален товар", "Възстановяване", "Версии на моделите"]) expect(html).toContain(label);
    expect(html).toContain('href="/activities"');
    expect(html).toContain("Активности");
    expect(html).not.toContain('href="/planning"');
    const protectedHtml = renderToStaticMarkup(<Dashboard data={trainingStatusFixture} mode="api" sessionActions />);
    expect(protectedHtml).toContain('href="/planning"');
    expect(protectedHtml).toContain("Профил за планиране");
    expect(protectedHtml).toContain('href="/?settings=edit"');
    expect(protectedHtml).toContain("Зони и HRmax");
  });
  it("labels fixture mode and renders ordered zones with every required field", () => {
    const html = renderToStaticMarkup(<Dashboard data={trainingStatusFixture} mode="fixture" />);
    expect(html).toContain("Демо данни");
    expect([...html.matchAll(/id="title-(Z[1-5])"/g)].map((match) => match[1])).toEqual(["Z1", "Z2", "Z3", "Z4", "Z5"]);
    for (const label of ["Реално време", "Еквивалентно време", "Tref", "7/40", "Готовност за натоварване", "Дни до пълно възстановяване"]) expect(html).toContain(label);
    expect(html).toContain("50,9 мин"); expect(html).toContain("97,8%"); expect(html).toContain("3,5 дни");
  });
  it("does not show the demo label in API mode", () => expect(renderToStaticMarkup(<Dashboard data={trainingStatusFixture} mode="api" />)).not.toContain("Демо данни"));
  it("shows the exact active analysis generation separately from model versions", () => {
    const html = renderToStaticMarkup(<Dashboard
      data={trainingStatusFixture}
      mode="api"
      generationId="gen-41"
      generationRevision={41}
      generationActivatedAt="2026-08-26T18:00:00Z"
    />);
    expect(html).toContain("Активна версия");
    expect(html).toContain("№ 41");
    expect(html).toContain("Generation ID");
    expect(html).toContain("gen-41");
  });
  it("renders 7/40 and canonical daily effective-load dynamics with aggregate activity detail", () => {
    const html = renderToStaticMarkup(<Dashboard data={trainingStatusFixture} mode="fixture" loadHistory={loadHistoryFixture} />);
    expect(html).toContain("Натоварване и динамика");
    expect(html).toContain("Динамика на индекса 7/40 по зони");
    expect(html).toContain("Дневен ефективен товар E по зони");
    expect(html).toContain("без изглаждане или сумиране");
    expect(html).toContain("линиите не се сумират до нов общ резултат");
    expect(html).toContain("Реално → приравнено → ефективно");
    expect(html).toContain("NordicSki");
    expect(html).toContain("Силова тренировка");
    expect(html).toContain("STR · без двойно HR");
    expect(html).toContain("Коефициент 1,0");
  });
  it("renders weekly real volume without inventing a total effective load", () => {
    const html = renderToStaticMarkup(<Dashboard data={trainingStatusFixture} mode="fixture" volumeHistory={volumeHistoryFixture} />);
    expect(html).toContain("Обща динамика на реалния обем");
    expect(html).toContain("Реален седмичен обем");
    expect(html).toContain("Продължителност на активностите");
    expect(html).toContain("HR-зонирано време Z1–Z5");
    expect(html).toContain("Това не е сбор на ефективен товар E");
    expect(html).toContain("STR компонент");
  });
  it("renders the completed-work report without reclassifying provider sport labels", () => {
    const html = renderToStaticMarkup(<Dashboard data={trainingStatusFixture} mode="fixture" completedWork={completedWorkFixture} />);
    expect(html).toContain("Отчет за извършеното натоварване");
    expect(html).toContain("Натоварване по пулсови зони");
    expect(html).toContain("По вид активност от Intervals");
    expect(html).toContain("NordicSki");
    expect(html).toContain("не са автоматично интерпретирани");
    expect(html).not.toContain("Покажи периода");
  });
  it("renders canonical load-only recovery dynamics and read-only settings", () => {
    const html = renderToStaticMarkup(<Dashboard data={trainingStatusFixture} mode="fixture" recoveryHistory={recoveryHistoryFixture} />);
    expect(html).toContain("Товарно възстановяване");
    expect(html).toContain("Динамика на товарната готовност по компоненти");
    expect(html).toContain("Load-only резултат");
    expect(html).toContain("Покритие на wellness данните");
    expect(html).toContain("32/40 дни");
    expect(html).toContain("sleepSecs");
    expect(html).toContain("не се заместват с неутрални стойности");
    expect(html).toContain("Настройки на recovery модела");
    expect(html).toContain("Какво означават показателите?");
    expect(html).toContain("Δумора = 100 × чувствителност × E / Tref");
    expect(html).toContain("След τ дни остават приблизително 37%");
    expect(html).toContain("силова готовност");
    expect(html).toContain("STR се възстановява като отделен компонент");
    expect(html).toContain("0,55");
    expect(html).toContain("0,75 дни");
    expect(html).toContain("main-load-recovery-v1");
  });
  it("offers a full refresh when legacy load history cannot restore recovery exactly", () => {
    const html = renderToStaticMarkup(<Dashboard
      data={trainingStatusFixture}
      mode="api"
      loadHistory={loadHistoryFixture}
      recoveryHistory={null}
      recoveryHistoryMessage="API услугата върна грешка (503)."
      integrationActions
    />);
    expect(html).toContain("Моделът не е премахнат");
    expect(html).toContain("recovery-history-v1");
    expect(html).toContain('action="/api/integrations/intervals/refresh"');
    expect(html).toContain('name="scope"');
    expect(html).toContain('value="FULL"');
    expect(html).toContain("Пълно обновяване");
  });
  it("offers deterministic recovery restore when daily Tref provenance exists", () => {
    const html = renderToStaticMarkup(<Dashboard
      data={trainingStatusFixture}
      mode="api"
      loadHistory={{
        ...loadHistoryFixture,
        schema_version: "load-history-v2",
        tref_bounds_profile_version: "tref-bounded-40d-expert-v1",
        daily: loadHistoryFixture.daily.map((row) => ({ ...row, tref_used_min: 120 })),
        strength: loadHistoryFixture.strength ? {
          ...loadHistoryFixture.strength,
          daily: loadHistoryFixture.strength.daily.map((row) => ({ ...row, tref_used_min: 90 })),
        } : loadHistoryFixture.strength,
      }}
      recoveryHistory={null}
      integrationActions
    />);
    expect(html).toContain("canonical история");
    expect(html).toContain('value="RECOVERY"');
    expect(html).toContain("Възстанови recovery модела");
  });
});

describe("theme preference", () => {
  it("uses system preference when no manual choice exists", () => {
    expect(resolveInitialTheme(null, true)).toBe("dark");
    expect(resolveInitialTheme(null, false)).toBe("light");
  });
  it("restores stored light and dark choices regardless of the system", () => {
    expect(resolveInitialTheme("light", true)).toBe("light");
    expect(resolveInitialTheme("dark", false)).toBe("dark");
    expect(THEME_STORAGE_KEY).toBe("onflows-theme");
  });
  it("applies manual switching and persists the choice", () => {
    const root = { dataset: {} as DOMStringMap, style: { colorScheme: "" } as CSSStyleDeclaration };
    const storage = { setItem: vi.fn() };
    applyTheme("dark", root, storage);
    expect(root.dataset.theme).toBe("dark");
    expect(root.style.colorScheme).toBe("dark");
    expect(storage.setItem).toHaveBeenCalledWith("onflows-theme", "dark");
    applyTheme("light", root, storage);
    expect(root.dataset.theme).toBe("light");
    expect(storage.setItem).toHaveBeenLastCalledWith("onflows-theme", "light");
  });
});
