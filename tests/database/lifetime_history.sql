-- Rollback-only scientific retention, private reads and projection fencing.
begin;
set local lock_timeout='3s';
set local statement_timeout='45s';
\ir history_generation_fixture.sql
\ir safe_retention_fixture.sql
do $$
declare f jsonb:=pg_temp.safe_retention_fixture('lifetime-contract');a text:=f->>'alias';
 g uuid:=(f->>'active')::uuid;d date:=current_date-730;data jsonb;result jsonb;claim jsonb;
 token uuid:='22ace48c-09fa-492e-913c-99c6a6c025c7';model text:=repeat('a',64);n integer;
begin
 if has_table_privilege('authenticated','onflows_private.history_day_inputs','select')
  or has_table_privilege('anon','onflows_private.learning_timeline_days','select')
  or has_function_privilege('authenticated','public.read_onflows_learning_history(text,integer,date,text)','execute')
  or has_function_privilege('anon','public.read_onflows_history_day_inputs(text,date,date)','execute')
  or has_function_privilege('service_role','onflows_private.capture_lifetime_generation(uuid,date,date)','execute') then
  raise exception 'Lifetime history exposed beyond its service boundary';end if;
 update public.onflows_analysis_generation_activities set local_date=d,start_at_utc=d::timestamptz where generation_id=g;
 update public.onflows_analysis_generations set period_start=d,period_end=d+89,as_of=d+89,
  snapshot_payload=jsonb_build_object('schema_version','athlete-snapshot-v1',
   'load_history',jsonb_build_object('period_start',d,'period_end',d+89,
    'daily',jsonb_build_array(jsonb_build_object('date',d,'zone','Z1','effective_load',42.)),
    'activities',jsonb_build_array(jsonb_build_object('date',d,'sport','Run','zones',jsonb_build_array(jsonb_build_object('zone','Z1','equivalent_time_min',42.))))),
   'wellness_calendar',jsonb_build_array(jsonb_build_object('date',d,'metrics',jsonb_build_object('hrv',jsonb_build_object('value',60)))))
  where generation_id=g;
 n:=onflows_private.capture_lifetime_generation(g);
 if n<>90 then raise exception 'Covered history was not captured: %',n;end if;
 if onflows_private.capture_lifetime_generation(g)<>0 then raise exception 'Replay duplicated day inputs';end if;
 data:=public.read_onflows_history_day_inputs(a,d,d+2);
 if jsonb_array_length(data)<>3 or data#>>'{0,payload,activities,0,zones,0,equivalent_time_min}'<>'42'
  or jsonb_array_length(data#>'{1,payload,daily}')<>0 then raise exception 'Original facts changed or unknown days invented';end if;
 -- Technical generations may disappear; permanent scientific roots remain.
 delete from public.onflows_analysis_generation_activities where generation_id=g;
 update public.onflows_activity_catalog set start_at_utc=null where activity_ref=f->>'active_activity';
 if onflows_private.history_activity_is_cold(a,f->>'active_activity',365) is not true then raise exception 'Archive lost the scientific activity date';end if;
 begin
  delete from public.onflows_activity_derived_runs where run_key=f->>'active_shadow';
  set constraints all immediate;
  raise exception 'Used scientific run was deleted';
 exception when foreign_key_violation then null;end;
 set constraints all deferred;
 -- The main fenced projection atomically publishes the permanent timeline.
 perform onflows_private.request_learning_preparation(a);
 claim:=public.onflows_learning_preparation('CLAIM',a,token,p_model_key=>model);
 data:=jsonb_build_object('athlete_alias',a,'report',jsonb_build_object('schema_version','recovery-learning-view-v1',
  'history_version','lifetime-timeline-v1','as_of',claim->>'day','series',jsonb_build_array(
   jsonb_build_object('day',d,'actual_q',42.,'q3',42.),jsonb_build_object('day',d+2,'actual_q',null,'q3',21.)),
  'decision',jsonb_build_object('component','Z1','proposed_change_percent',5)));
 result:=public.onflows_learning_preparation('STORE',a,token,(claim->>'revision')::bigint,(claim->>'day')::date,model,data);
 if result->>'stored'<>'true' then raise exception 'Current projection not stored';end if;
 result:=public.read_onflows_learning_history(a,14,d+2,model);
 if result->>'ready'<>'true' or jsonb_array_length(result#>'{payload,report,series}')<>2
  or result#>>'{payload,report,history,display_end}'<>(d+2)::text
  or result#>>'{payload,report,decision,component}'<>'Z1' then raise exception 'History navigation changed current decision or dates';end if;
 result:=public.read_onflows_learning_history(a,30,current_date+2,model);
 if result->>'invalid_period'<>'true' then raise exception 'Future range accepted';end if;
 begin
  perform public.read_onflows_history_day_inputs(a,d,d+1000);raise exception 'Unbounded read accepted';
 exception when raise_exception then if sqlerrm='Unbounded read accepted' then raise;end if;end;
 -- Older years get bounded worker turns, not HTTP fitting.
 update public.onflows_intervals_connections set status='CONNECTED' where athlete_alias=a;
 claim:=public.onflows_history_window('CLAIM',a,token,model);
 if claim is null or (claim->>'last_day')::date-(claim->>'first_day')::date>364 then raise exception 'Historical work not bounded';end if;
 result:=public.onflows_history_window('STORE',a,'063b013b-ce37-49f7-b42a-1722ab1e91f2',model,
  jsonb_build_object('history_version','lifetime-timeline-v1','series','[]'::jsonb));
 if result->>'stored'<>'false' then raise exception 'Wrong history lease published';end if;
 update onflows_private.history_day_inputs set source_revision=source_revision+1 where athlete_alias=a and day=d+1;
 result:=public.onflows_history_window('STORE',a,token,model,
  jsonb_build_object('history_version','lifetime-timeline-v1','series',jsonb_build_array(jsonb_build_object('day',d+1))));
 if result->>'stored'<>'false' then raise exception 'Changed historical inputs were published';end if;
 -- Account removal retains its existing explicit cascade.
 delete from public.onflows_intervals_connections where athlete_alias=a;
 set constraints all immediate;
 if exists(select 1 from onflows_private.history_day_inputs where athlete_alias=a)
  or exists(select 1 from onflows_private.learning_timeline_days where athlete_alias=a) then raise exception 'Account cascade left private data';end if;
end $$;
rollback;
