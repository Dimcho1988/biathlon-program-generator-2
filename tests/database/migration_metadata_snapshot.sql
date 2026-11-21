-- Metadata only. No historical payloads or stored migration SQL are returned.
select jsonb_build_object('format','onflows-migration-metadata-v1',
  'rows',coalesce(jsonb_agg(jsonb_build_object(
    'version',version,'name',name,'statement_count',cardinality(statements),
    'joined_bytes',octet_length(array_to_string(statements,E'\n')),
    'joined_sha256',encode(sha256(convert_to(array_to_string(statements,E'\n'),'UTF8')),'hex'))
    order by version collate "C"),'[]'::jsonb)) as metadata
from supabase_migrations.schema_migrations;
