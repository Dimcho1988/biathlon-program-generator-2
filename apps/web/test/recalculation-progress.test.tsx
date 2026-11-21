import {renderToStaticMarkup} from "react-dom/server";
import {expect,it,vi} from "vitest";
import {ErrorState} from "../components/error-state";
import {PREPARED_VIEW_MESSAGE} from "../lib/prepared-view";
import type {SyncState} from "../lib/sync";
vi.mock("next/navigation",()=>({useRouter:()=>({refresh:vi.fn()})}));
const state:SyncState={schema_version:"sync-state-v1",job_id:"job",scope:"FULL",state:"RUNNING",stage:"ACTIVITIES:12:30",progress_percent:36,requested_at:null,started_at:null,finished_at:null,retry_at:null,failure_code:null,active_generation_id:"previous",active_revision:1,analysis_as_of:"2026-11-18",activated_at:null};
it("renders measured progress on a gated dashboard with an old generation",()=>{
 const html=renderToStaticMarkup(<ErrorState message={PREPARED_VIEW_MESSAGE} syncState={state}/>);
 expect(html).toContain("Обработени 12 от 30 активности");
 expect(html).toContain('value="40"');
 expect(html).not.toContain("Показана остава");
});
it("does not present a failed recalculation as ongoing preparation",()=>{
 const html=renderToStaticMarkup(<ErrorState message={PREPARED_VIEW_MESSAGE} syncState={{...state,state:"FAILED"}}/>);
 expect(html).toContain("Обновяването не завърши");
 expect(html).toContain("Опитай отново");
 expect(html).not.toContain("<progress");
 expect(html).not.toContain("<h1>Подготвяме данните");
});
