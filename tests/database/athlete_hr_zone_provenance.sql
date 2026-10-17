-- Run against a migrated local database. Only synthetic fixtures, rolled back.
begin;
insert into public.onflows_intervals_connections
  (athlete_alias, provider_athlete_id, encrypted_access_token, status)
values ('zone-provenance-test', 'zone-provenance-test-provider', 'synthetic', 'REVOKED');
insert into public.onflows_athlete_settings
  (athlete_alias, hr_zone_bounds, hrmax_bpm, timezone)
values ('zone-provenance-test', array[100,120,140,160,180,200], 200, 'UTC');

do $$
declare rejected boolean;
begin
  assert (select hr_zone_source = 'MANUAL' and hr_zone_percentages is null
    from public.onflows_athlete_settings where athlete_alias = 'zone-provenance-test'),
    'Existing-style writes must remain manual';
  assert (select relrowsecurity from pg_class where oid = 'public.onflows_athlete_settings'::regclass),
    'Settings RLS must remain enabled';
  assert not has_table_privilege('authenticated', 'public.onflows_athlete_settings', 'SELECT'),
    'Settings must not be exposed directly';

  -- Synthetic test fractions are deliberately not a product default.
  update public.onflows_athlete_settings set
    hr_zone_source = 'AUTOMATIC_HRMAX', hr_zone_percentages = array[50,60,70,80,90,100]
  where athlete_alias = 'zone-provenance-test';
  assert (select hr_zone_percentages = array[50,60,70,80,90,100]::numeric[]
    from public.onflows_athlete_settings where athlete_alias = 'zone-provenance-test'),
    'Exact automatic provenance must round-trip';

  rejected := false;
  begin
    update public.onflows_athlete_settings set hrmax_bpm = 205
      where athlete_alias = 'zone-provenance-test';
  exception when check_violation then rejected := true;
  end;
  assert rejected, 'HRmax cannot move independently of an automatic scheme';

  rejected := false;
  begin
    update public.onflows_athlete_settings set hr_zone_percentages = array[50,60,null,80,90,100]
      where athlete_alias = 'zone-provenance-test';
  exception when check_violation then rejected := true;
  end;
  assert rejected, 'Null percentages must not bypass SQL checks';

  update public.onflows_athlete_settings set hrmax_bpm = 195,
    hr_zone_bounds = array[98,117,137,156,176,195]
    where athlete_alias = 'zone-provenance-test';
  assert (select hr_zone_bounds = array[98,117,137,156,176,195]::smallint[]
    from public.onflows_athlete_settings where athlete_alias = 'zone-provenance-test'),
    'Half-up rounding must match the API';

  rejected := false;
  begin
    update public.onflows_athlete_settings set hr_zone_source = 'MANUAL'
      where athlete_alias = 'zone-provenance-test';
  exception when check_violation then rejected := true;
  end;
  assert rejected, 'Manual edits must clear automatic provenance';

  update public.onflows_athlete_settings set hr_zone_source = 'MANUAL', hr_zone_percentages = null
    where athlete_alias = 'zone-provenance-test';
end $$;
rollback;
