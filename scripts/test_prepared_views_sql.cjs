/* Local PostgreSQL contract test using PGlite, with no cloud writes.
 * Pass a test-only @electric-sql/pglite module path as the first argument.
 * Core table definitions and the access/source readers come from migrations.
 * This validates the new SQL, not the unchanged authoritative plan writer:
 * guard tests deliberately fail if that writer would be reached.
 */
const fs = require("node:fs");
const path = require("node:path");
const { PGlite } = require(process.argv[2] || "@electric-sql/pglite");
const root = path.resolve(__dirname, "..");
const migrations = path.join(root, "supabase/migrations");
const sources = fs.readdirSync(migrations).sort().map(name => fs.readFileSync(path.join(migrations, name), "utf8"));

function table(name) {
  const escaped = name.replaceAll(".", "\\.");
  const pattern = new RegExp("create table (?:if not exists )?" + escaped + " \\([\\s\\S]*?\\n\\);", "i");
  for (const source of sources) {
    const match = source.match(pattern);
    if (match) return match[0];
  }
  throw new Error("Missing core table " + name);
}

function routine(name) {
  for (const source of sources) {
    const start = source.indexOf("create function " + name + "(");
    if (start >= 0) {
      const end = source.indexOf("$$;", start);
      if (end >= 0) return source.slice(start, end+3);
    }
  }
  throw new Error("Missing core routine " + name);
}

(async () => {
  const db = new PGlite();
  try {
    await db.exec(`
      create role anon; create role authenticated; create role service_role bypassrls;
      create schema auth; create schema onflows_private;
      grant usage on schema onflows_private to service_role;
      create table auth.users(id uuid primary key);
      create table public.onflows_profiles(user_id uuid primary key references auth.users(id),display_name text);
      create table public.onflows_user_athletes(user_id uuid,athlete_alias text,is_owner boolean);
      create table public.onflows_sharing_grants(owner_user_id uuid,viewer_user_id uuid,edit_plan boolean);
      create table public.onflows_organization_memberships(organization_id uuid,user_id uuid,role text,status text);
      create table public.onflows_coach_athlete_assignments(organization_id uuid,athlete_user_id uuid,coach_user_id uuid,can_edit_plan boolean);
    `);
    for (const name of ["public.onflows_intervals_connections", "public.onflows_athlete_settings"]) await db.exec(table(name));
    await db.exec("alter table public.onflows_athlete_settings add column if not exists hrmax_bpm double precision;");
    for (const name of ["public.onflows_management_entries", "public.onflows_management_plan_revisions", "public.onflows_management_plan_state",
      "public.onflows_training_snapshots", "public.onflows_activity_catalog", "public.onflows_athlete_analysis_state", "public.onflows_sync_jobs",
      "public.onflows_analysis_generations"]) await db.exec(table(name));
    await db.exec(`
      alter table public.onflows_training_snapshots add column if not exists activated_at timestamptz;
      create table public.onflows_activity_model_inputs(input_key text,athlete_alias text,activity_ref text,primary key(input_key,athlete_alias,activity_ref));
      create table public.onflows_activity_canonical_runs(run_key text,athlete_alias text,activity_ref text,primary key(run_key,athlete_alias,activity_ref));
      create table public.onflows_activity_derived_runs(run_key text,input_key text,athlete_alias text,activity_ref text,primary key(run_key,input_key,athlete_alias,activity_ref));
      create table public.onflows_model_entries(athlete_alias text,kind text,entry_key text,revision integer,payload jsonb,recorded_at timestamptz);
    `);
    for (const name of ["public.onflows_analysis_generation_activities", "onflows_private.planning_jobs",
      "onflows_private.load_projection_cache", "onflows_private.planning_computations"]) await db.exec(table(name));
    await db.exec(routine("public.active_onflows_analysis"));
    await db.exec(routine("onflows_private.can_prepare_plan"));
    await db.exec(`create function public.save_onflows_management_plan_compact(text,jsonb,integer,uuid,text,integer,jsonb,boolean)
      returns jsonb language plpgsql as $$ begin raise exception 'Unexpected authoritative writer invocation in guard tests'; end $$;`);
    await db.exec(fs.readFileSync(path.join(migrations, "20261102172430_prepared_dashboard_and_training_events.sql"), "utf8"));
    await db.exec(fs.readFileSync(path.join(migrations, "20261104043353_dashboard_dirty_event_invalidation.sql"), "utf8"));
    await db.exec(fs.readFileSync(path.join(root, "tests/database/prepared_dashboard_and_training_events.sql"), "utf8"));
    await db.exec(table("public.onflows_response_entries"));
    await db.exec(fs.readFileSync(path.join(migrations, "20261106104212_prepared_page_views_and_daily_adaptive_horizon.sql"), "utf8"));
    await db.exec(fs.readFileSync(path.join(migrations, "20261106104859_seed_prepared_sports_from_active_recordings.sql"), "utf8"));
    const validationMigration=fs.readdirSync(migrations).find(name=>name.endsWith("_revalidate_adaptive_inputs_after_analysis.sql"));
    if(validationMigration) await db.exec(fs.readFileSync(path.join(migrations,validationMigration),"utf8"));
    const overviewMigration=fs.readdirSync(migrations).find(name=>name.endsWith("_preserve_approved_overview_with_input_hints.sql"));
    if(overviewMigration) await db.exec(fs.readFileSync(path.join(migrations,overviewMigration),"utf8"));
    const recoveryMigration=fs.readdirSync(migrations).find(name=>name.endsWith("_prepared_management_page_recovery.sql"));
    if(recoveryMigration) await db.exec(fs.readFileSync(path.join(migrations,recoveryMigration),"utf8"));
    // Use the current scheduler contract, including daily input checks in both
    // planning modes. The authoritative publication routines from this same
    // migration run separately in test_automatic_current_plan_sql.cjs.
    const automaticMigration=fs.readdirSync(migrations).find(name=>name.endsWith("_automatic_current_training_plan.sql"));
    if(automaticMigration) {
      const source=fs.readFileSync(path.join(migrations,automaticMigration),"utf8");
      const start=source.indexOf("create or replace function onflows_private.training_plan_update(");
      const end=source.indexOf("$$;",start);
      if(start<0||end<0) throw new Error("Missing current training plan scheduler");
      await db.exec(source.slice(start,end+3));
    }
    await db.exec("grant select on public.onflows_management_plan_state,public.onflows_management_plan_revisions to service_role;");
    // Re-run the prior contract against the upgraded queue/publication guards.
    try { await db.exec(fs.readFileSync(path.join(root, "tests/database/prepared_dashboard_and_training_events.sql"), "utf8")); }
    catch (error) { error.message="Upgraded dashboard contract: "+error.message; throw error; }
    try { await db.exec(fs.readFileSync(path.join(root, "tests/database/prepared_views.sql"), "utf8")); }
    catch (error) { error.message="Prepared view contract: "+error.message; throw error; }
    await db.exec(fs.readFileSync(path.join(root,"tests/database/prepared_planning_source_changes.sql"),"utf8"));
    await db.exec(fs.readFileSync(path.join(root,"tests/database/management_overview.sql"),"utf8"));
    const clean = await db.query("select count(*)::integer as remaining from public.onflows_intervals_connections");
    if (clean.rows[0].remaining !== 0) throw new Error("Contract fixtures did not roll back");
    console.log(JSON.stringify({engine: (await db.query("select version() as version")).rows[0].version,
      migration: "prepared_views_and_daily_adaptive_horizon", contracts: "passed", fixture_rows_after_rollback: 0}));
  } finally {
    await db.close();
  }
})().catch(error => { console.error(error.message); if (error.where) console.error(error.where); if (error.position) console.error("position",error.position); process.exitCode = 1; });
