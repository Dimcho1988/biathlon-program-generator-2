// @vitest-environment jsdom
import {act} from 'react';
import {createRoot, type Root} from 'react-dom/client';
import {afterEach,beforeEach,expect,it,vi} from 'vitest';
import {TrainingManagement} from '../components/training-management';
import {defaultManagementProfile} from '../lib/training-management';
import {parsePlanningJob,planningPending,type PlanningJob} from '../lib/planning-job';

const id='22222222-2222-4222-8222-222222222222';
const queued:PlanningJob={job_id:id,status:'RUNNING',stage:'BASELINE',start_date:'2026-11-02',result_revision:null,error_code:null};
const record={entry_key:'2026-11-02',revision:1,stale:false,payload:{schema_version:'planning-draft-v1',
  engine_version:'fixture',status:'DRAFT',start_date:'2026-11-02',end_date:'2026-11-08',days:[],source:{},parameters:{},warnings:[]}};
let root:Root,container:HTMLDivElement;
beforeEach(()=>{
  vi.useFakeTimers();
  vi.stubGlobal('IS_REACT_ACT_ENVIRONMENT',true);
  container=document.createElement('div');document.body.append(container);root=createRoot(container);
});
afterEach(async()=>{
  await act(async()=>root.unmount());container.remove();
  vi.clearAllTimers();vi.useRealTimers();vi.restoreAllMocks();vi.unstubAllGlobals();
});
async function mount(canEdit=true){
  await act(async()=>root.render(<TrainingManagement athleteName="Fixture athlete" canEdit={canEdit} today="2026-11-01"
    initialProfile={{configured:true,revision:1,profile:{...defaultManagementProfile('2026-11-01'),discipline:'5000 m'}}}
    initialDrafts={[]}/>));
}
async function submit(){
  const form=container.querySelector('form.management-generate')!;
  expect(form).toBeTruthy();
  await act(async()=>form.dispatchEvent(new Event('submit',{bubbles:true,cancelable:true})));
}
const ready:PlanningJob={...queued,status:'SUCCEEDED',stage:'READY',result_revision:1};

it('accepts one short request and receives only the exact completed draft',async()=>{
  let state=queued;
  const transport=vi.fn(async(url:string,init?:RequestInit)=>{
    if(url.includes('draft-metadata?'))return Response.json({revision:0});
    if(url.includes('drafts?'))return Response.json({drafts:[record]});
    return Response.json({job:init?.method==='POST'?queued:url.includes('job_id=')?state:null},{status:init?.method==='POST'?202:200});
  });
  vi.stubGlobal('fetch',transport);await mount();await submit();
  expect(container.textContent).toContain('Съставяме тренировъчната програма.');
  expect(container.textContent).toContain('Заявката остава запазена.');
  expect(transport.mock.calls.filter(([,init])=>init?.method==='POST')).toHaveLength(1);
  state=ready;
  await act(async()=>vi.advanceTimersByTimeAsync(5000));
  expect(container.textContent).toContain('Програмата е подготвена. Прегледай я преди започване.');
  expect(transport.mock.calls.some(([url])=>url.endsWith('/drafts?start_date=2026-11-02&revision=1'))).toBe(true);
  expect(transport.mock.calls.filter(([,init])=>init?.method==='POST')).toHaveLength(1);
  expect(transport.mock.calls.some(([url])=>url.endsWith('/activate'))).toBe(false);
});

it('recovers an existing background job after reopening without a new mutation',async()=>{
  vi.stubGlobal('fetch',vi.fn(async()=>Response.json({job:queued})));
  await mount();
  expect(container.textContent).toContain('Съставяме тренировъчната програма.');
  expect(fetch).toHaveBeenCalledTimes(1);
  await act(async()=>root.unmount());root=createRoot(container);await mount();
  expect(fetch).toHaveBeenCalledTimes(2);
  expect(vi.mocked(fetch).mock.calls.every(([,init])=>init?.method==='GET')).toBe(true);
});

it('reads back an uncertain acceptance instead of replaying its POST',async()=>{
  let accepted=false;
  const transport=vi.fn(async(url:string,init?:RequestInit)=>{
    if(url.includes('draft-metadata?'))return Response.json({revision:0});
    if(init?.method==='POST'){accepted=true;throw new TypeError('response lost');}
    return Response.json({job:accepted?queued:null});
  });
  vi.stubGlobal('fetch',transport);await mount();await submit();
  expect(container.textContent).toContain('Съставяме тренировъчната програма.');
  await act(async()=>vi.advanceTimersByTimeAsync(15_000));
  expect(transport.mock.calls.filter(([,init])=>init?.method==='POST')).toHaveLength(1);
});

it('stops after an input conflict and never retries or approves automatically',async()=>{
  vi.stubGlobal('fetch',vi.fn(async(url:string,init?:RequestInit)=>{
    if(url.includes('draft-metadata?'))return Response.json({revision:0});
    return Response.json({job:init?.method==='POST'?queued:url.includes('job_id=')?{
      ...queued,status:'FAILED',stage:'FAILED',error_code:'INPUT_CHANGED'}:null});
  }));
  await mount();await submit();await act(async()=>vi.advanceTimersByTimeAsync(5000));
  expect(container.querySelector('[role="alert"]')?.textContent).toContain('променени по време на подготовката');
  await act(async()=>vi.advanceTimersByTimeAsync(30_000));
  expect(vi.mocked(fetch).mock.calls.filter(([,init])=>init?.method==='POST')).toHaveLength(1);
});

it('pauses status requests in a hidden tab and resumes on visibility',async()=>{
  let visibility:'visible'|'hidden'='visible';
  vi.spyOn(document,'visibilityState','get').mockImplementation(()=>visibility);
  const transport=vi.fn(async()=>Response.json({job:queued}));vi.stubGlobal('fetch',transport);
  await mount();visibility='hidden';
  await act(async()=>vi.advanceTimersByTimeAsync(20_000));
  expect(transport).toHaveBeenCalledTimes(1);
  visibility='visible';await act(async()=>document.dispatchEvent(new Event('visibilitychange')));
  expect(transport).toHaveBeenCalledTimes(2);
});

it('does not inspect editable jobs for a read-only viewer',async()=>{
  const transport=vi.fn();vi.stubGlobal('fetch',transport);await mount(false);
  expect(transport).not.toHaveBeenCalled();
});

it.each([
  {job:{...queued,job_id:'not-an-id'}},{job:{...queued,start_date:'2026-02-30'}},
  {job:{...queued,status:'UNKNOWN'}},{job:{...queued,stage:'UNKNOWN'}},
  {job:{...queued,status:'SUCCEEDED',result_revision:null}},{job:{...ready,result_revision:-1}},
])('rejects invalid job state %#',value=>expect(()=>parsePlanningJob(value)).toThrow());

it('recognizes no saved request without inventing progress',()=>{
  expect(parsePlanningJob({job:null})).toBeNull();
  expect(planningPending(null)).toBe(false);
});
