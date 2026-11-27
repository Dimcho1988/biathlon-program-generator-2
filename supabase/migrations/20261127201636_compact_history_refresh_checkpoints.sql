-- A refresh checkpoint is a reusable import cache, not a lifetime scientific
-- input. Retain it on the immutable generation catalogue for subsequent syncs;
-- do not duplicate it for every captured history day. Keep the 256 KiB bound.
do $$
declare definition text; fragment text:='a.catalog_payload||jsonb_build_object';
begin
 definition:=pg_get_functiondef('onflows_private.capture_lifetime_generation(uuid,date,date)'::regprocedure);
 if (length(definition)-length(replace(definition,fragment,'')))/length(fragment)<>1 then
  raise exception 'Lifetime catalogue capture definition changed';
 end if;
 execute replace(definition,fragment,'(a.catalog_payload-''refresh_checkpoint'')||jsonb_build_object');
end $$;
