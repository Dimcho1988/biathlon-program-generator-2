/* PostgreSQL contracts for date-effective calibration, queueing and publication. */
const fs = require('node:fs');
const path = require('node:path');
const assert = require('node:assert/strict');
const {PGlite} = require(process.argv[2] || '@electric-sql/pglite');
const root = path.resolve(__dirname, '..');
(async () => {
 const db = new PGlite();
 try {
  await db.exec(`create role anon; create role authenticated; create role service_role bypassrls;
   create schema onflows_private;
   create table public.onflows_athlete_settings(athlete_alias text primary key,hrmax_bpm integer,
    hr_zone_bounds smallint[],hr_zone_source text,hr_zone_percentages float8[],timezone text,
    model_preferences jsonb not null default '{}');
   create table public.onflows_intervals_connections(athlete_alias text primary key,status text);
   create table public.onflows_athlete_analysis_state(athlete_alias text primary key,active_generation_id uuid);
   create table public.onflows_analysis_generations(generation_id uuid primary key,athlete_alias text,status text,snapshot_payload jsonb);
   create table queue(athlete_alias text,kind text,key text,payload jsonb);
   create function public.enqueue_onflows_sync_job(text,text,text,jsonb) returns void language sql as
    $$insert into public.queue values($1,$2,$3,$4)$$;
   create function onflows_private.dashboard_preparation(p_operation text,p_alias text,p_token uuid,p_revision bigint,p_day date,p_model_key text,p_envelope jsonb)
    returns jsonb language plpgsql as $$begin return '{"ready":true,"stored":true}'; end$$;
   create function onflows_private.learning_preparation(p_operation text,p_alias text,p_token uuid,p_revision bigint,p_day date,p_model_key text,p_payload jsonb)
    returns jsonb language plpgsql as $$begin return '{"ready":true,"stored":true}'; end$$;
   create function onflows_private.prepared_view(p_operation text,p_alias text,p_kind text,p_parameters jsonb,p_token uuid,p_revision bigint,p_day date,p_model_key text,p_payload jsonb)
    returns jsonb language plpgsql as $$begin return '{"ready":true,"stored":true}'; end$$;`);
  await db.exec(fs.readFileSync(path.join(root,'supabase/migrations/20261114210632_hrmax_effective_history.sql'),'utf8'));
  await db.exec(`insert into onflows_athlete_settings values('a',190,array[86,137,150,169,179,190],'AUTOMATIC_HRMAX',array[45,72,79,89,94,100],'UTC','{}');
   insert into onflows_athlete_settings select 'b',hrmax_bpm,hr_zone_bounds,hr_zone_source,hr_zone_percentages,timezone,model_preferences from onflows_athlete_settings where athlete_alias='a';
   insert into onflows_intervals_connections values('a','CONNECTED'),('b','CONNECTED');
   update onflows_athlete_settings set hrmax_bpm=hrmax_bpm where athlete_alias='b';
   update onflows_athlete_settings set hrmax_bpm=200,hr_zone_bounds=array[90,144,158,178,188,200] where athlete_alias='a';`);
  const row = async () => (await db.query("select model_preferences p from onflows_athlete_settings where athlete_alias='a'")).rows[0].p;
  assert.deepEqual((await db.query("select model_preferences p from onflows_athlete_settings where athlete_alias='b'")).rows[0].p,{});
  let p=await row();
  assert.equal(p.hr_history.length,2); assert.equal(p.hr_history[0].hrmax_bpm,190); assert.equal(p.hr_history[1].hrmax_bpm,200);
  const dates=(await db.query("select (current_date-89)::text d90,(current_date-10)::text d10,(current_date-100)::text d100")).rows[0];
  assert.equal(p.hr_history[1].from,dates.d90);
  assert.equal((await db.query('select count(*)::int n from queue')).rows[0].n,1);
  assert.equal((await db.query("select onflows_private.hr_analysis_current('a') ok")).rows[0].ok,false);
  for(const operation of ['READ','STORE']) {
   const r=await db.query(`select onflows_private.dashboard_preparation($1,'a',null,null,null,null,null) p`,[operation]);
   assert.equal(r.rows[0].p.ready,false);
  }
  const first=p.hr_settings_revision;
  // No-op and unrelated preferences retain history and never enqueue another job.
  await db.exec(`update onflows_athlete_settings set model_preferences='{"age_years":32}' where athlete_alias='a';`);
  assert.equal((await row()).hr_settings_revision,first);
  assert.equal((await db.query('select count(*)::int n from queue')).rows[0].n,1);
  // A second calibration preserves the previous interval and uses an exact date.
  await db.query(`update onflows_athlete_settings set hrmax_bpm=205,hr_zone_bounds=array[92,148,162,182,193,205],
    model_preferences=model_preferences||jsonb_build_object('hrmax_apply_from',$1::text) where athlete_alias='a'`,[dates.d10]);
  p=await row(); assert.equal(p.hr_history.length,3);assert.equal(p.hr_history[2].from,dates.d10);
  assert.notEqual(p.hr_settings_revision,first);
  // A correction starting earlier replaces superseded intervals, including repeated corrections.
  await db.query(`update onflows_athlete_settings set hrmax_bpm=195,hr_zone_bounds=array[88,140,154,174,183,195],
    model_preferences=model_preferences||jsonb_build_object('hrmax_apply_from',$1::text) where athlete_alias='a'`,[dates.d100]);
  p=await row();assert.equal(p.hr_history.length,2);assert.equal(p.hr_history[1].from,dates.d100);
  // No future values; failure rolls back settings, timeline and queue together.
  const count=(await db.query('select count(*)::int n from queue')).rows[0].n;
  await assert.rejects(db.exec(`update onflows_athlete_settings set hrmax_bpm=199,
   model_preferences=model_preferences||jsonb_build_object('hrmax_apply_from',(current_date+1)::text) where athlete_alias='a'`));
  assert.deepEqual(await row(),p);assert.equal((await db.query('select count(*)::int n from queue')).rows[0].n,count);
  const id='00000000-0000-0000-0000-000000000001';
  await db.query(`insert into onflows_analysis_generations values($1,'a','READY','{}')`,[id]);
  await assert.rejects(db.query("update onflows_analysis_generations set status='ACTIVE' where generation_id=$1",[id]));
  await db.query(`update onflows_analysis_generations set snapshot_payload=jsonb_build_object('load_history',jsonb_build_object('hr_settings_revision',$2::text)) where generation_id=$1`,[id,p.hr_settings_revision]);
  await db.query("update onflows_analysis_generations set status='ACTIVE' where generation_id=$1",[id]);
  await db.query("insert into onflows_athlete_analysis_state values('a',$1)",[id]);
  assert.equal((await db.query("select onflows_private.hr_analysis_current('a') ok")).rows[0].ok,true);
  assert.equal((await db.query("select onflows_private.hr_analysis_current('b') ok")).rows[0].ok,true);
  assert.equal((await db.query("select onflows_private.dashboard_preparation('READ','a',null,null,null,null,null) p")).rows[0].p.ready,true);
  assert.equal((await db.query("select count(*)::int n from queue where athlete_alias='b'")).rows[0].n,0);
  console.log('HR calibration SQL contracts passed: dates, repeated corrections, rollback, isolation, queue and activation fences.');
 } finally {await db.close();}
})().catch(error=>{console.error(error);process.exitCode=1;});
