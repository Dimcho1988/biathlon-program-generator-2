import {describe,expect,it} from "vitest";
import {availableScheduleWindows,planDates,planWeekView,selectedScheduleWindow} from "../lib/plan-week-view";
import type {PlanningDraft} from "../lib/training-management";

function programme(mode = "AUTO") {
  return {
    schema_version:"planning-draft-v1",status:"DRAFT",engine_version:"test",source:{},warnings:[],
    start_date:"2026-10-28",end_date:"2026-11-03",
    input_snapshot:{management_profile:{adaptation_mode:mode}},
    parameters:{horizon:{effective_end:"2026-12-31"}},
    allocation:{segments:[{window_start:"2026-10-27",window_end:"2026-11-02"},{window_start:"2026-11-03",window_end:"2026-11-09"}]},
    days:planDates("2026-10-28","2026-11-03").map(date=>({date})),
    lookahead_days:planDates("2026-11-04","2026-11-16").map(date=>({date})),
  } as unknown as PlanningDraft; // This fixture supplies only schedule/projection fields.
}

describe("prepared adaptive display",()=>{
  it("shows today and six future days while preserving the model's accounting cycles",()=>{
    const plan=programme(),before=structuredClone(plan);
    const windows=availableScheduleWindows(plan,"2026-11-01");
    expect(windows).toEqual([{start:"2026-11-01",end:"2026-11-07"},{start:"2026-11-08",end:"2026-11-14"}]);
    const display=planWeekView(plan,windows[0]);
    expect(display.days.map(d=>d.date)).toEqual(planDates("2026-11-01","2026-11-07"));
    expect(display.missingDates).toEqual([]);
    expect(plan).toEqual(before);
  });
  it("moves the default window by one local calendar day without retaining the prior selection",()=>{
    const plan=programme();
    expect(selectedScheduleWindow(plan,"2026-11-02","2026-11-01")).toEqual({start:"2026-11-02",end:"2026-11-08"});
    expect(selectedScheduleWindow(plan,"2026-11-02","2026-11-09")).toEqual({start:"2026-11-09",end:"2026-11-15"});
  });
  it("retains coach-anchored week selection in fixed mode",()=>{
    expect(availableScheduleWindows(programme("REVIEW"),"2026-10-28")).toEqual([
      {start:"2026-10-27",end:"2026-11-02"},{start:"2026-11-03",end:"2026-11-09"}]);
  });
  it("clips the final view to the actual preparation horizon",()=>{
    const plan=programme();
    plan.parameters!.horizon={effective_end:"2026-11-05"};
    expect(availableScheduleWindows(plan,"2026-11-02")).toEqual([{start:"2026-11-02",end:"2026-11-05"}]);
  });
  it("uses calendar days across the daylight-saving change",()=>{
    const plan=programme();plan.start_date="2026-03-01";
    expect(availableScheduleWindows(plan,"2026-03-28")[0]).toEqual({start:"2026-03-28",end:"2026-04-03"});
  });
  it("leaves an unprepared future date missing instead of inventing a rest day",()=>{
    const plan=programme();plan.lookahead_days=plan.lookahead_days!.filter(d=>d.date!=="2026-11-06");
    expect(planWeekView(plan,availableScheduleWindows(plan,"2026-11-01")[0]).missingDates).toEqual(["2026-11-06"]);
  });
});
