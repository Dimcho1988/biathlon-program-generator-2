import {afterEach,beforeEach,describe,expect,it,vi} from "vitest";
import {renderToStaticMarkup} from "react-dom/server";
import {MODEL_ZONES,parseRecoveryV2,type RecoveryV2} from "../lib/models";
import {RecoveryV2Section} from "../components/recovery-v2";
import {POST} from "../app/api/athlete/models/route";
import {currentAuthorizedAthlete} from "../lib/account-access";
vi.mock("next/navigation",()=>({useRouter:()=>({refresh:vi.fn()})}));
vi.mock("../lib/account-access",async load=>({...await load<typeof import("../lib/account-access")>(),currentAuthorizedAthlete:vi.fn()}));
vi.mock("../lib/api-readiness",()=>({waitForApi:vi.fn()}));
const fixture:RecoveryV2={schema_version:"recovery-history-v2",athlete_id:"ath-test",period_start:"2026-08-01",period_end:"2026-09-12",as_of:"2026-09-12",basis:"load-only",time_resolution:"calendar-day",ready_threshold_percent:90,
  model:{algorithm_version:"recovery-daily-e-biexponential-v2",parameter_version:"profile-0",parameter_fingerprint:"abc",practical_full_recovery_percent:90},config_revision:0,
  settings:Object.fromEntries(MODEL_ZONES.map(z=>[z,{duration_coefficient:1,shape:1,sensitivity:1,initial_daily_min:20}])) as RecoveryV2["settings"],
  current:MODEL_ZONES.map(zone=>({zone,readiness_percent:80,residual_fatigue:20,days_to_practical_recovery:1,baseline_daily_min:20,baseline_raw_daily_min:20,history_days:40,baseline_source:"PERSONAL"})),
  daily:[],forecast:MODEL_ZONES.flatMap(zone=>[{zone,days:0,readiness_percent:80},{zone,days:1,readiness_percent:90}]),source_as_of:"2026-09-12",source_stale:false,warnings:[]};
describe("Recovery v2 interface",()=>{
  it("validates absolute readiness and preserves six independent components",()=>{
    expect(parseRecoveryV2(fixture).current).toHaveLength(6);
    const wrong=structuredClone(fixture);wrong.current[0].readiness_percent=90;
    expect(()=>parseRecoveryV2(wrong)).toThrow(/готовност/);
  });
  it("renders configurable curve and causal baseline, and disables view-only edits",()=>{
    const html=renderToStaticMarkup(<RecoveryV2Section history={fixture} canEdit/>);
    for(const text of ["праг 90%","Стръмност","Среднодневна база","STR","Запази и преизчисли"])expect(html).toContain(text);
    expect(html).not.toContain("NaN");
    const readOnly=renderToStaticMarkup(<RecoveryV2Section history={fixture} canEdit={false}/>);
    expect(readOnly).not.toContain("Запази и преизчисли");expect(readOnly).toContain("disabled");
  });
});
describe("profile-scoped model writes",()=>{
  const owner={userId:"athlete",actorUserId:"athlete",athleteAlias:"ath-test",displayName:"Fixture",isOwner:true,canEditPlan:true,canViewRecovery:true};
  const request=(origin="https://web.example.test")=>new Request("http://internal/api/athlete/models",{method:"POST",headers:{origin,"x-forwarded-host":"web.example.test","x-forwarded-proto":"https","Content-Type":"application/json"},body:JSON.stringify({kind:"recovery",payload:{zones:fixture.settings,expected_revision:0}})});
  beforeEach(()=>{vi.mocked(currentAuthorizedAthlete).mockResolvedValue(owner);process.env.ONFLOWS_API_BASE_URL="https://api.example.test";process.env.ONFLOWS_SERVICE_TOKEN="server-test-secret";vi.stubGlobal("fetch",vi.fn().mockResolvedValue(Response.json({saved:true,revision:1})));});
  afterEach(()=>{vi.unstubAllGlobals();vi.clearAllMocks();delete process.env.ONFLOWS_API_BASE_URL;delete process.env.ONFLOWS_SERVICE_TOKEN;});
  it("uses the verified athlete and actor, keeping the service key on the server",async()=>{
    const result=await POST(request());expect(result.status).toBe(200);
    expect(fetch).toHaveBeenCalledWith(new URL("https://api.example.test/api/v2/athlete/models/recovery"),expect.objectContaining({headers:expect.objectContaining({"X-OnFlows-Athlete-Alias":"ath-test","X-OnFlows-Actor-Id":"athlete"})}));
    expect(await result.text()).not.toContain("server-test-secret");
  });
  it("rejects cross-origin, no-session and insufficient permissions",async()=>{
    expect((await POST(request("https://attacker.test"))).status).toBe(403);
    vi.mocked(currentAuthorizedAthlete).mockResolvedValue(null);expect((await POST(request())).status).toBe(401);
    vi.mocked(currentAuthorizedAthlete).mockResolvedValue({...owner,canEditPlan:false});expect((await POST(request())).status).toBe(403);
    vi.mocked(currentAuthorizedAthlete).mockResolvedValue({...owner,canViewRecovery:false});expect((await POST(request())).status).toBe(403);
    expect(fetch).not.toHaveBeenCalled();
  });
  it("does not retry conflicting settings",async()=>{
    vi.stubGlobal("fetch",vi.fn().mockResolvedValue(Response.json({detail:"changed"},{status:409})));
    expect((await POST(request())).status).toBe(409);expect(fetch).toHaveBeenCalledTimes(1);
  });
});
