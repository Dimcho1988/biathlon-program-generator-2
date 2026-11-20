-- Synthetic local-only integration fixtures; all writes roll back.
begin;
insert into auth.users(id) values ('98000000-0000-0000-0000-000000000001'),('98000000-0000-0000-0000-000000000002');
insert into public.onflows_profiles(user_id,display_name) values
 ('98000000-0000-0000-0000-000000000001','Energy fixture'),('98000000-0000-0000-0000-000000000002','Unrelated fixture');
insert into public.onflows_intervals_connections(athlete_alias,provider_athlete_id,encrypted_access_token,status)
 values('energy-fixture','energy-fixture-provider','synthetic-not-a-token','REVOKED');
insert into public.onflows_athlete_settings(athlete_alias,hr_zone_bounds,hrmax_bpm,timezone)
 values('energy-fixture',array[100,120,140,160,180,200],200,'UTC');
insert into public.onflows_user_athletes(user_id,athlete_alias,is_owner)
 values('98000000-0000-0000-0000-000000000001','energy-fixture',true);
do $$ begin
 assert not has_function_privilege('authenticated','public.save_onflows_response_entry(text,text,text,date,jsonb,integer,uuid)','EXECUTE');
 assert not has_table_privilege('authenticated','public.onflows_response_entries','SELECT');
 assert not has_table_privilege('service_role','onflows_private.activity_derivations','INSERT');
end $$;
set local role service_role;
do $$ declare r jsonb; denied boolean:=false; begin
 r:=public.save_onflows_response_entry('energy-fixture','NUTRITION','2026-11-01','2026-11-01','{"weight_kg":68}',0,'98000000-0000-0000-0000-000000000001');
 assert r->>'saved'='true' and r->>'revision'='1';
 r:=public.save_onflows_response_entry('energy-fixture','NUTRITION','2026-11-01','2026-11-01','{"weight_kg":69}',0,'98000000-0000-0000-0000-000000000001');
 assert r->>'conflict'='true';
 begin
  perform public.save_onflows_response_entry('energy-fixture','NUTRITION','2026-11-01','2026-11-01','{"weight_kg":69}',1,'98000000-0000-0000-0000-000000000002');
 exception when others then denied:=true; end;
 assert denied,'Unrelated actor could change nutritional inputs';
 perform public.onflows_activity_derivations('energy-fixture','ENERGY',null,jsonb_build_object(repeat('a',64),'{"kcal":120}'::jsonb));
 r:=public.onflows_activity_derivations('energy-fixture','ENERGY',array[repeat('a',64)],null);
 assert r->repeat('a',64)->>'kcal'='120','Energy cache did not round trip';
 r:=public.onflows_prepared_view('READ','energy-fixture','energy','{}',null,null,null,repeat('b',64),null);
 assert r is not null,'Energy prepared kind is not accepted';
end $$;
rollback;
