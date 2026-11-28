-- Table-specific trigger fields must be accessed inside a guarded branch.
-- A boolean AND does not protect NEW/OLD record-field resolution in PL/pgSQL.
begin;
set local lock_timeout='3s';
set local statement_timeout='60s';
create or replace function onflows_private.invalidate_prepared_learning() returns trigger
language plpgsql security definer set search_path='' as $$
declare affected text;
begin
 if tg_op='DELETE' then
  affected:=old.athlete_alias;
  if tg_table_name='onflows_athlete_settings' then
   delete from onflows_private.prepared_learning_views where athlete_alias=affected;
   return old;
  end if;
  if tg_table_name='onflows_management_entries' then
   if old.kind not in ('PROFILE','DRAFT') then return old; end if;
  end if;
 else
  affected:=new.athlete_alias;
  if tg_table_name='onflows_management_entries' then
   if new.kind not in ('PROFILE','DRAFT') then return new; end if;
  end if;
  if tg_op='UPDATE' then
   if (to_jsonb(new)-'updated_at')=(to_jsonb(old)-'updated_at') then return new; end if;
  end if;
 end if;
 perform onflows_private.request_learning_preparation(affected);
 if tg_op='DELETE' then return old; else return new; end if;
end $$;
commit;
