import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { renderToStaticMarkup } from "react-dom/server";
import { ResponseMonitoring } from "../components/response-monitoring";
import { parseResponseHistory } from "../lib/response-monitoring";
import { responseFixture } from "../lib/response-fixture";
import { canViewAthleteRecovery, currentAuthorizedAthlete } from "../lib/account-access";
import { POST } from "../app/api/athlete/response/route";
import { waitForApi } from "../lib/api-readiness";

vi.mock("next/navigation",()=>({useRouter:()=>({refresh:vi.fn()})}));
vi.mock("../lib/account-access",async importOriginal=>({...await importOriginal<typeof import("../lib/account-access")>(),currentAuthorizedAthlete:vi.fn()}));
vi.mock("../lib/api-readiness",()=>({waitForApi:vi.fn()}));

describe("observation contract and interface",()=>{
  it("validates all fixed contributions and rejects totals with missing inputs",()=>{
    expect(parseResponseHistory(responseFixture).days).toHaveLength(14);
    const bad=structuredClone(responseFixture);
    bad.days[7].total=50;
    expect(()=>parseResponseHistory(bad)).toThrow();
  });
  it("rejects changed weights and controller actions",()=>{
    const bad=structuredClone(responseFixture);
    bad.weights.subjective=.7;
    expect(()=>parseResponseHistory(bad)).toThrow();
    expect(()=>parseResponseHistory({...responseFixture,automatic_increase:true})).toThrow();
  });
  it("shows the trend, selectable day and exact component contribution",()=>{
    const html=renderToStaticMarkup(<ResponseMonitoring history={responseFixture} canReport canEditPlan/>);
    for(const text of ["Тренд на стреса","Ден за подробности","Принос","50%","30%","20%","Ниската оценка не задейства увеличение","Доброволни контролни тестове"]) expect(html).toContain(text);
    expect(html).toContain('name="sleep_quality"');
    expect(html).toContain("Примерна тренировка");
    expect(html).not.toContain("NaN");
  });
  it("shows missing coverage without a zero and disables somebody else's self report",()=>{
    const h=structuredClone(responseFixture);
    h.days=[h.days[7]];
    const html=renderToStaticMarkup(<ResponseMonitoring history={h} canReport={false} canEditPlan={false}/>);
    expect(html).toContain("Непълни данни · 50% покритие");
    expect(html).toContain('fieldset disabled=""');
    expect(html).not.toContain("Задай следващ блок");
  });
});

describe("recovery sharing scope",()=>{
  it("does not let overview or planning permission reveal private wellness",()=>{
    expect(canViewAthleteRecovery("coach","athlete",[],[],[{owner_user_id:"athlete",viewer_user_id:"coach",edit_plan:true}])).toBe(false);
    expect(canViewAthleteRecovery("coach","athlete",[],[],[{owner_user_id:"athlete",viewer_user_id:"coach",edit_plan:false,view_recovery:true}])).toBe(true);
    expect(canViewAthleteRecovery("athlete","athlete",[],[],[])).toBe(true);
  });
  it("supports assigned view-only coaches but rejects suspended memberships",()=>{
    const memberships=[{organization_id:"org",user_id:"athlete",role:"ATHLETE" as const,status:"ACTIVE" as const},{organization_id:"org",user_id:"coach",role:"COACH" as const,status:"ACTIVE" as const}];
    const assignments=[{organization_id:"org",coach_user_id:"coach",athlete_user_id:"athlete",can_edit_plan:false}];
    expect(canViewAthleteRecovery("coach","athlete",memberships,assignments,[])).toBe(true);
    expect(canViewAthleteRecovery("coach","athlete",[memberships[0],{...memberships[1],status:"SUSPENDED"}],assignments,[])).toBe(false);
  });
});

describe("authenticated response writes",()=>{
  const owner={userId:"athlete",actorUserId:"athlete",athleteAlias:"ath-test",displayName:"Fixture",isOwner:true,canEditPlan:true,canViewRecovery:true};
  const request=(kind="daily",payload:unknown={},origin="https://web.example.test")=>new Request("http://internal:3000/api/athlete/response",{method:"POST",headers:{origin,"x-forwarded-host":"web.example.test","x-forwarded-proto":"https","Content-Type":"application/json"},body:JSON.stringify({kind,payload})});
  beforeEach(()=>{
    vi.mocked(currentAuthorizedAthlete).mockResolvedValue(owner);
    process.env.ONFLOWS_API_BASE_URL="https://api.example.test";
    process.env.ONFLOWS_SERVICE_TOKEN="server-only-test";
    vi.stubGlobal("fetch",vi.fn().mockResolvedValue(Response.json({saved:true,revision:1})));
  });
  afterEach(()=>{vi.unstubAllGlobals();vi.clearAllMocks();delete process.env.ONFLOWS_API_BASE_URL;delete process.env.ONFLOWS_SERVICE_TOKEN;});
  it("uses verified alias and actual actor behind the reverse proxy",async()=>{
    const r=await POST(request("daily",{day:"2026-09-09"}));
    expect(r.status).toBe(200);
    expect(waitForApi).toHaveBeenCalledWith("https://api.example.test");
    expect(fetch).toHaveBeenCalledWith(new URL("https://api.example.test/api/v2/athlete/response/daily"),expect.objectContaining({headers:expect.objectContaining({"X-OnFlows-Athlete-Alias":"ath-test","X-OnFlows-Actor-Id":"athlete",Authorization:"Bearer server-only-test"})}));
    expect(await r.text()).not.toContain("server-only-test");
  });
  it("rejects cross-origin and signed-out writes before calling the API",async()=>{
    expect((await POST(request("daily",{},"https://attacker.test"))).status).toBe(403);
    vi.mocked(currentAuthorizedAthlete).mockResolvedValue(null);
    expect((await POST(request())).status).toBe(401);
    expect(fetch).not.toHaveBeenCalled();
  });
  it("does not allow a coach to invent an athlete's self report",async()=>{
    vi.mocked(currentAuthorizedAthlete).mockResolvedValue({...owner,actorUserId:"coach",isOwner:false});
    expect((await POST(request("daily"))).status).toBe(403);
    expect((await POST(request("session"))).status).toBe(403);
    expect((await POST(request("block"))).status).toBe(200);
  });
  it("requires recovery permission even when plan editing is allowed",async()=>{
    vi.mocked(currentAuthorizedAthlete).mockResolvedValue({...owner,actorUserId:"coach",isOwner:false,canViewRecovery:false});
    expect((await POST(request("test"))).status).toBe(403);
    expect(fetch).not.toHaveBeenCalled();
  });
  it("returns conflict without retry or overwriting input",async()=>{
    vi.stubGlobal("fetch",vi.fn().mockResolvedValue(Response.json({detail:"changed"},{status:409})));
    const r=await POST(request());
    expect(r.status).toBe(409);
    expect(fetch).toHaveBeenCalledTimes(1);
    expect((await r.json()).error).toContain("Презаредете");
  });
  it("bounds UTF-8 bodies even without Content-Length",async()=>{
    expect((await POST(request("daily",{note:"а".repeat(11000)}))).status).toBe(413);
    expect(fetch).not.toHaveBeenCalled();
  });
});
