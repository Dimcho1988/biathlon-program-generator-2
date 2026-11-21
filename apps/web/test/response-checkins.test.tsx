// @vitest-environment jsdom
import { describe, expect, it, vi } from "vitest";
import { renderToStaticMarkup } from "react-dom/server";
import { ResponseMonitoring } from "../components/response-monitoring";
import { StressOverview } from "../components/stress-overview";
import { dailyInput, sessionInput } from "../lib/response-input";
import { responseFixture } from "../lib/response-fixture";
import { parseResponseHistory } from "../lib/response-monitoring";

vi.mock("next/navigation",()=>({useRouter:()=>({refresh:vi.fn()})}));

describe("simple check-ins without loss of observations",()=>{
  it("keeps five anchored morning answers first and preserves optional fields in the same record",()=>{
    const history=structuredClone(responseFixture);
    const day=history.days.at(-1)!;
    day.daily_report=null;
    const host=document.createElement("div");
    host.innerHTML=renderToStaticMarkup(<ResponseMonitoring history={history} canReport canEditPlan/>);
    const form=host.querySelector<HTMLFormElement>("#daily-report form")!;
    expect(form.querySelectorAll(".response-daily-fields select")).toHaveLength(5);
    expect(form.querySelector('select[name="competition_motivation"]')?.closest("details")?.open).toBe(false);
    const fatigue=form.querySelector<HTMLSelectElement>('select[name="fatigue"]')!;
    fatigue.value="3";
    const payload=dailyInput(new FormData(form),day,history.today);
    expect(payload).toMatchObject({fatigue:3,motivation:null,competition_motivation:null,input_method:"FORM",expected_revision:day.daily_revision});
    expect(host.firstElementChild?.id).toBe("daily-report");
  });
  it("adds feel while keeping imported effort, its unknown timing, and the activity duration",()=>{
    const session={...responseFixture.sessions[0],rpe:7,provider_rpe:7,rpe_source:"INTERVALS",feel:null,provider_feel:null,
      feel_source:null,source:"INTERVALS",timing:"UNKNOWN",duration_minutes:60,revision:0};
    const form=new FormData();form.set("rpe","7");form.set("feel","2");form.set("duration_minutes","60");
    const payload=sessionInput(form,session,false);
    expect(payload).toMatchObject({rpe:7,rpe_source:"INTERVALS",feel:2,feel_source:"ONFLOWS",timing:"UNKNOWN",duration_minutes:60});
    form.set("rpe","8");form.set("timing","DELAYED");
    expect(sessionInput(form,session,false)).toMatchObject({rpe:8,rpe_source:"ONFLOWS",timing:"DELAYED"});
  });
  it("shows a single physiological signal without displaying it as an overall stress score",()=>{
    const history=structuredClone(responseFixture),day=history.days[0];
    day.channels=day.channels.map(c=>({...c,score:c.key==="resting_hr"?55:null,raw:c.key==="resting_hr"?44:null}));
    day.total=55;day.coverage=7;day.assessment_quality="PARTIAL";
    const host=document.createElement("div");
    host.innerHTML=renderToStaticMarkup(<StressOverview history={history} selected={0} onSelect={()=>undefined}/>);
    expect(host.querySelector(".stress-kpis strong")?.textContent).toBe("— / 100");
    expect(host.textContent).toContain("Пулс в покой: 44");
    expect(host.textContent).toContain("Един показател не определя общото ниво на стреса");
    expect(()=>parseResponseHistory({...responseFixture,sessions:[{...responseFixture.sessions[0],feel:7}]})).toThrow();
  });
});
