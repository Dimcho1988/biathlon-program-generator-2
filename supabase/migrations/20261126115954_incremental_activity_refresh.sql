-- Apply a measured HRmax from the recording day. Manual corrections include today.
create or replace function onflows_private.version_hr_calibration() returns trigger
language plpgsql security definer set search_path='' as $$
declare timeline jsonb; effective date; local_today date; changed boolean;
begin
 changed:=new.hrmax_bpm is distinct from old.hrmax_bpm or new.hr_zone_bounds is distinct from old.hr_zone_bounds
   or new.hr_zone_source is distinct from old.hr_zone_source or new.hr_zone_percentages is distinct from old.hr_zone_percentages;
 -- Clients cannot replace the authoritative timeline through generic settings saves.
 timeline:=coalesce(old.model_preferences->'hr_history','[]'::jsonb);
 if changed then
  local_today:=(clock_timestamp() at time zone new.timezone)::date;
  effective:=coalesce((new.model_preferences->>'hrmax_apply_from')::date,
    case when new.model_preferences->>'hrmax_source'='OBSERVED'
      and new.model_preferences->'hrmax_observation' is distinct from old.model_preferences->'hrmax_observation'
      then coalesce((new.model_preferences#>>'{hrmax_observation,local_date}')::date,local_today)
      else local_today-39 end);
  if effective>local_today or effective<local_today-179 then
   raise exception 'HR calibration date must be within the last 180 days' using errcode='22023'; end if;
  if jsonb_array_length(timeline)=0 then
   timeline:=jsonb_build_array(onflows_private.hr_calibration(old,date '0001-01-01'));
  end if;
  select coalesce(jsonb_agg(e order by e->>'from'),'[]'::jsonb) into timeline
   from jsonb_array_elements(timeline) e where (e->>'from')::date<effective;
  timeline:=timeline||jsonb_build_array(onflows_private.hr_calibration(new,effective));
  new.model_preferences:=(new.model_preferences-'hrmax_apply_from')||jsonb_build_object(
    'hr_history',timeline,'hr_settings_revision',gen_random_uuid()::text,
    'hr_recompute_from',case when onflows_private.hr_analysis_current(old.athlete_alias) then effective
      else least(effective,coalesce((old.model_preferences->>'hr_recompute_from')::date,effective)) end);
 else
  new.model_preferences:=(new.model_preferences-array['hrmax_apply_from','hr_history','hr_settings_revision','hr_recompute_from'])
    ||(select coalesce(jsonb_object_agg(key,value),'{}'::jsonb) from jsonb_each(old.model_preferences)
       where key in ('hr_history','hr_settings_revision','hr_recompute_from'));
 end if;
 return new;
end $$;
