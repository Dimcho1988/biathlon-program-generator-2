import {expect,it} from "vitest";
import {loadHistoryFixture,trainingStatusFixture} from "../lib/fixture";
import {parseLoadHistory} from "../lib/load-history";
import {parseDashboardView} from "../lib/dashboard-view";

const version2={...loadHistoryFixture,schema_version:"load-history-v2",
  tref_bounds_profile_version:"aerobic-tref-v1",
  equivalence_version:"intra_zone_linear_v2_z5_5pp",zone_bounds_bpm:[40,138,148,160,168,180],hrmax_bpm:180,
  daily:loadHistoryFixture.daily.map(row=>({...row,tref_used_min:120})),
  strength:loadHistoryFixture.strength?{...loadHistoryFixture.strength,
    daily:loadHistoryFixture.strength.daily.map(row=>({...row,tref_used_min:90}))}:null};

it.each([null,"sport-hr-reference-cycling-plus7-v1"])("accepts serialized sport policy %s through the complete dashboard",policy=>{
  const history={...version2,sport_hr_policy_version:policy};
  const dashboard={schema_version:"dashboard-view-v1",generation_id:"active-generation",revision:1,
    analysis_as_of:history.period_end,activated_at:"2026-10-01T11:00:00Z",training_status:trainingStatusFixture,
    load_history:history,completed_work:null,recovery_history:null,volume_history:null};
  const parsed=parseDashboardView(JSON.parse(JSON.stringify(dashboard)));
  expect(parsed.load_history).toMatchObject(history);
  expect(parsed.training_status).toEqual(trainingStatusFixture);
});

it("preserves compatibility with unstamped history and rejects malformed policy metadata",()=>{
  expect(parseLoadHistory(loadHistoryFixture)).toEqual(loadHistoryFixture);
  expect(parseLoadHistory(version2).daily).toEqual(version2.daily);
  for(const policy of [7,false,{},"","  "])
    expect(()=>parseLoadHistory({...version2,sport_hr_policy_version:policy})).toThrow();
  expect(()=>parseLoadHistory({...version2,sport_hr_policy_version:null,unrecognized_field:true})).toThrow(/структура/);
});
