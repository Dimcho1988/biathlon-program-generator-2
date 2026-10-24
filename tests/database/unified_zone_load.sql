-- Synthetic fixtures only; every setting and peak update rolls back.
begin;
\ir history_generation_fixture.sql
do $$
declare
 f jsonb := pg_temp.history_generation_fixture('unified-load');
 a text := f->>'alias';
 other_a text := f->>'other_alias';
 observation jsonb := '{"source":"RAW_MEASURED","window_seconds":15,"window_range_bpm":2,
   "raw_mean_bpm":198,"offset_bpm":7,"start_s":10,"end_s":25}'::jsonb;
 checkpoint jsonb;
 item jsonb;
 rejected boolean;
 role_name text;
begin
 assert (select not prosecdef and proconfig=array['search_path=""']
   from pg_proc where oid='public.increase_onflows_hrmax(text,integer,integer,jsonb)'::regprocedure);
 assert has_function_privilege('service_role','public.increase_onflows_hrmax(text,integer,integer,jsonb)','EXECUTE');
 foreach role_name in array array['anon','authenticated'] loop
   assert not has_function_privilege(role_name,'public.increase_onflows_hrmax(text,integer,integer,jsonb)','EXECUTE');
 end loop;
 update public.onflows_athlete_settings set hrmax_bpm=200,
   hr_zone_bounds=array[90,144,158,178,188,200],hr_zone_source='AUTOMATIC_HRMAX',
   hr_zone_percentages=array[45,72,79,89,94,100],
   model_preferences='{"leading_indicator":"HEART_RATE","age_years":40,"hrmax_source":"MANUAL"}'
 where athlete_alias=a;
 checkpoint:=public.onflows_management_checkpoint(a);
 update public.onflows_athlete_settings set model_preferences=model_preferences || '{"leading_indicator":"SPEED"}'::jsonb
 where athlete_alias=a;
 assert public.onflows_management_checkpoint(a)<>checkpoint,
   'Changing the leading indicator must invalidate a saved plan checkpoint';
 assert public.increase_onflows_hrmax(a,205,200,observation);
 assert not public.increase_onflows_hrmax(a,206,200,observation || '{"raw_mean_bpm":199}'::jsonb),
   'Stale settings must never overwrite a newer maximum';
 assert not public.increase_onflows_hrmax(a,204,205,observation || '{"raw_mean_bpm":197}'::jsonb),
   'A lower measured peak must never lower the working maximum';
 select to_jsonb(s) into item from public.onflows_athlete_settings s where athlete_alias=a;
 assert item->>'hrmax_bpm'='205' and item#>>'{model_preferences,hrmax_source}'='OBSERVED';
 assert item#>>'{model_preferences,leading_indicator}'='SPEED';
 assert item->'hr_zone_bounds'='[92,148,162,182,193,205]'::jsonb;
 assert (select hrmax_bpm=190 from public.onflows_athlete_settings where athlete_alias=other_a),
   'Observed peak update escaped its athlete scope';
 assert public.read_onflows_planning_inputs(a)#>'{settings_rows,0,model_preferences}'=item->'model_preferences';
 observation := observation || '{"raw_mean_bpm":199}'::jsonb;
 foreach observation in array array[
   observation-'raw_mean_bpm', observation || '{"window_range_bpm":null}'::jsonb,
   observation || '{"window_seconds":14}'::jsonb, observation || '{"source":"MODEL"}'::jsonb,
   observation || '{"window_range_bpm":11}'::jsonb, observation || '{"end_s":24}'::jsonb
 ] loop
   rejected:=false;
   begin
     perform public.increase_onflows_hrmax(a,206,205,observation);
   exception when invalid_parameter_value then rejected:=true;
   end;
   assert rejected, 'Invalid or estimated HRmax evidence was admitted';
 end loop;
end $$;
rollback;
