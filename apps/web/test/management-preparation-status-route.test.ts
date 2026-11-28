import {beforeEach, expect, it, vi} from "vitest";
const {access, fetcher}=vi.hoisted(()=>({access:vi.fn(),fetcher:vi.fn()}));
vi.mock("../lib/account-access",()=>({currentAuthorizedAthlete:access}));
import {GET} from "../app/api/athlete/management/preparation-status/route";
const request=(view="week",alias="ath")=>new Request(`https://onflows.test/api/athlete/management/preparation-status?view=${view}`,{headers:{"X-Onflows-Athlete":alias,"X-Onflows-Actor":"actor"}});
beforeEach(()=>{vi.clearAllMocks();vi.stubGlobal("fetch",fetcher);vi.stubEnv("ONFLOWS_API_BASE_URL","https://api.test");vi.stubEnv("ONFLOWS_SERVICE_TOKEN","private-token");access.mockResolvedValue({athleteAlias:"ath",actorUserId:"actor",canViewPlan:true});});
it("returns only bounded status through authenticated server request",async()=>{
 fetcher.mockResolvedValue(new Response(JSON.stringify({ready:true,payload:{private:"data"}})));
 const result=await GET(request("overview"));expect(await result.json()).toEqual({ready:true,limited:false});
 expect(result.headers.get("Cache-Control")).toBe("no-store");
 expect(String(fetcher.mock.calls[0][0])).toContain("view=overview");
 expect(fetcher.mock.calls[0][1].headers["X-OnFlows-Actor-Id"]).toBe("actor");
});
it.each([401,403,412,422])("rejects %s before contacting API",async status=>{
 if(status===401)access.mockResolvedValue(null);
 if(status===403)access.mockResolvedValue({athleteAlias:"ath",actorUserId:"actor",canViewPlan:false});
 const result=await GET(request(status===422?"invalid":"week",status===412?"other":"ath"));
 expect(result.status).toBe(status);expect(fetcher).not.toHaveBeenCalled();
});
