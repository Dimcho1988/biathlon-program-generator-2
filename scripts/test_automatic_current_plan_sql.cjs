/* Execute real publication functions against isolated PostgreSQL (PGlite).
 * The fixture schema contains the relevant application tables only. Nothing
 * connects to hosted Supabase, and every contract transaction rolls back.
 */
const fs=require('node:fs');
const path=require('node:path');
const {PGlite}=require(process.argv[2]||'@electric-sql/pglite');
const root=path.resolve(__dirname,'..');
const dir=path.join(root,'supabase/migrations');
const sources=fs.readdirSync(dir).sort().map(name=>({name,sql:fs.readFileSync(path.join(dir,name),'utf8')}));
function table(name){
 const re=new RegExp('create table (?:if not exists )?'+name.replaceAll('.','\\.')+' \\([\\s\\S]*?\\n\\);','i');
 for(const source of sources){const match=source.sql.match(re);if(match)return match[0];}
 throw new Error('Missing table '+name);
}
function routine(name,before='9999'){
 const re=new RegExp('create(?: or replace)? function '+name.replaceAll('.','\\.')+'\\(');
 for(const source of [...sources].reverse()){
  if(source.name>=before)continue;
  const match=source.sql.match(re);if(match){const end=source.sql.indexOf('$$;',match.index);return source.sql.slice(match.index,end+3);}
 }
 throw new Error('Missing routine '+name);
}
(async()=>{
 const db=new PGlite();
 try{
  await db.exec(`
   create role anon;create role authenticated;create role service_role bypassrls;
   create schema auth;create schema onflows_private;
   grant usage on schema onflows_private to service_role;
   create table auth.users(id uuid primary key);
   create table public.onflows_profiles(user_id uuid primary key references auth.users(id) on delete cascade,display_name text);
   create table public.onflows_user_athletes(user_id uuid,athlete_alias text,is_owner boolean);
   create table public.onflows_sharing_grants(owner_user_id uuid,viewer_user_id uuid,edit_plan boolean,view_plan boolean);
   create table public.onflows_organization_memberships(organization_id uuid,user_id uuid,role text,status text);
   create table public.onflows_coach_athlete_assignments(organization_id uuid,athlete_user_id uuid,coach_user_id uuid,can_edit_plan boolean);
  `);
  for(const name of ['public.onflows_intervals_connections','public.onflows_athlete_settings',
   'public.onflows_management_entries','public.onflows_management_plan_revisions','public.onflows_management_plan_state',
   'public.onflows_response_entries','onflows_private.planning_jobs','onflows_private.training_plan_updates'])await db.exec(table(name));
  await db.exec(`
   alter table public.onflows_athlete_settings add column if not exists hrmax_bpm double precision;
   alter table public.onflows_athlete_settings add column if not exists planning_profile jsonb default '{}';
   alter table public.onflows_athlete_settings add column if not exists planning_calendar jsonb default '{}';
   alter table public.onflows_athlete_settings add column if not exists mesocycle_accent_preferences jsonb default '{}';
   alter table public.onflows_athlete_settings add column if not exists model_preferences jsonb default '{}';
   create table public.onflows_athlete_analysis_state(athlete_alias text primary key,active_generation_id uuid,active_revision integer);
   create table public.onflows_model_entries(athlete_alias text,kind text,entry_key text,revision integer,payload jsonb,recorded_at timestamptz);
   alter table onflows_private.training_plan_updates add column claimed_actor uuid,add column claimed_manual boolean default false,
    add column claimed_plan_revision integer,add column requested_actor uuid,add column requested_plan_revision integer,
    add column requested_manual boolean not null default false,add column requested_record_check boolean not null default false,
    add column claimed_record_check boolean not null default false,add column claimed_day date,add column last_evaluated_on date;
   grant select on all tables in schema public to service_role;
   grant insert on public.onflows_management_entries to service_role;
  `);
  for(const name of ['onflows_private.can_edit_management','onflows_private.can_prepare_plan',
   'onflows_private.management_plan_eligible','public.onflows_management_checkpoint',
   'public.save_onflows_management_entry'])await db.exec(routine(name));
  await db.exec(fs.readFileSync(path.join(dir,'20261025215106_unified_planning_estimate_approval.sql'),'utf8'));
  await db.exec(fs.readFileSync(path.join(dir,'20261019112003_unchanged_management_checks.sql'),'utf8'));
  await db.exec(routine('public.save_onflows_management_plan'));
  await db.exec('revoke all on function public.save_onflows_management_plan(text,jsonb,integer,uuid,text,integer,jsonb,boolean) from public,anon,authenticated; grant execute on function public.save_onflows_management_plan(text,jsonb,integer,uuid,text,integer,jsonb,boolean) to service_role;');
  await db.exec(fs.readFileSync(path.join(dir,'20261019092624_compact_management_save_response.sql'),'utf8'));
  await db.exec(fs.readFileSync(path.join(dir,'20261018182514_whole_microcycle_revision_capacity.sql'),'utf8'));
  await db.exec(fs.readFileSync(path.join(dir,'20261031134707_bound_plan_publication_timeout.sql'),'utf8'));
  await db.exec(routine('onflows_private.planning_job_view','20261112215950'));
  await db.exec(fs.readFileSync(path.join(dir,'20261101215206_fence_background_planning_inputs.sql'),'utf8'));
  await db.exec(routine('public.onflows_planning_job'));
  await db.exec(routine('onflows_private.training_plan_update_core_v1'));
  await db.exec(`create function onflows_private.request_prepared_views(text,boolean default true) returns void language sql as 'select';`);
  const currentMigration=sources.find(source=>source.name.endsWith('_automatic_current_training_plan.sql'));
  await db.exec(currentMigration.sql);
  await db.exec(routine('public.save_onflows_management_plan_after_training'));
  await db.exec(routine('public.onflows_training_plan_update'));
  await db.exec('revoke all on function public.onflows_training_plan_update(text,text,uuid) from public,anon,authenticated; grant execute on function public.onflows_training_plan_update(text,text,uuid) to service_role;');
  const contracts=['automatic_current_training_plan.sql','compact_management_save.sql','unified_planning_approval.sql','unchanged_management_checks.sql','prepared_program_publication.sql'];
  for(const name of contracts){
   try{await db.exec(fs.readFileSync(path.join(root,'tests/database',name),'utf8'));}
   catch(error){error.message=name+': '+error.message;throw error;}
  }
  const remaining=(await db.query('select count(*)::integer as n from public.onflows_intervals_connections')).rows[0].n;
  if(remaining!==0)throw new Error('Fixtures did not roll back');
  console.log(JSON.stringify({engine:(await db.query('select version() as v')).rows[0].v,contracts,passed:true,fixture_rows_after_rollback:remaining}));
 }finally{await db.close();}
})().catch(error=>{console.error(error.message);if(error.where)console.error(error.where);if(error.position)console.error('position',error.position);process.exitCode=1;});
