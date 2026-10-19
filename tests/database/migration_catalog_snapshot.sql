-- Read-only application-schema evidence. Run after a complete clean rebuild,
-- before synthetic fixtures, and unchanged against staging. No table data,
-- sequence counters, tokens or migration statements are read here. Definitions
-- are hashed on the server; application function bodies are not returned.
-- PostgreSQL deparsers must run on the same major version in both environments.
with
app_relations as (
  select c.*, n.nspname as schema_name
  from pg_catalog.pg_class c join pg_catalog.pg_namespace n on n.oid=c.relnamespace
  where (n.nspname in ('public','private') and starts_with(c.relname,'onflows_'))
     or n.nspname='onflows_private'
),
app_functions as (
  select p.*, n.nspname as schema_name
  from pg_catalog.pg_proc p join pg_catalog.pg_namespace n on n.oid=p.pronamespace
  where p.prokind in ('f','p') and (
    (n.nspname in ('public','private') and strpos(p.proname,'onflows_')>0)
    or n.nspname='onflows_private')
),
objects(kind,identity,definition) as (
  select 'schema', n.nspname::text,
    jsonb_build_object('owner',pg_catalog.pg_get_userbyid(n.nspowner),
      'acl',coalesce((select jsonb_agg(jsonb_build_object(
        'grantee',case when a.grantee=0 then 'PUBLIC' else pg_catalog.pg_get_userbyid(a.grantee) end,
        'privilege',a.privilege_type,'grantable',a.is_grantable)
        order by a.grantee=0,pg_catalog.pg_get_userbyid(a.grantee),a.privilege_type,a.is_grantable)
        from pg_catalog.aclexplode(coalesce(n.nspacl,pg_catalog.acldefault('n',n.nspowner))) a),'[]'::jsonb))
  from pg_catalog.pg_namespace n where n.nspname in ('private','onflows_private')
  union all
  select 'relation',r.schema_name||'.'||r.relname,
    jsonb_build_object('kind',r.relkind,'persistence',r.relpersistence,
      'owner',pg_catalog.pg_get_userbyid(r.relowner),'rls',r.relrowsecurity,
      'force_rls',r.relforcerowsecurity,'replica_identity',r.relreplident,
      'options',coalesce((select jsonb_agg(value order by value) from unnest(r.reloptions) value),'[]'::jsonb),
      'partition_key',case when r.relkind='p' then pg_catalog.pg_get_partkeydef(r.oid) end,
      'partition_bound',case when r.relispartition then pg_catalog.pg_get_expr(r.relpartbound,r.oid) end,
      'acl',coalesce((select jsonb_agg(jsonb_build_object(
        'grantee',case when a.grantee=0 then 'PUBLIC' else pg_catalog.pg_get_userbyid(a.grantee) end,
        'privilege',a.privilege_type,'grantable',a.is_grantable)
        order by a.grantee=0,pg_catalog.pg_get_userbyid(a.grantee),a.privilege_type,a.is_grantable)
        from pg_catalog.aclexplode(coalesce(r.relacl,pg_catalog.acldefault(
          case when r.relkind='S' then 'S'::"char" else 'r'::"char" end,r.relowner))) a),'[]'::jsonb),
      'effective_app_privileges',(select jsonb_agg(jsonb_build_object('role',role_name,
        'privileges',(select jsonb_agg(privilege order by privilege)
          from unnest(case when r.relkind='S' then array['USAGE','SELECT','UPDATE']
            else array['SELECT','INSERT','UPDATE','DELETE','TRUNCATE','REFERENCES','TRIGGER'] end) privilege
          where case when r.relkind='S' then pg_catalog.has_sequence_privilege(role_name,r.oid,privilege)
            else pg_catalog.has_table_privilege(role_name,r.oid,privilege) end)) order by role_name)
        from unnest(array['anon','authenticated','service_role']) role_name))
  from app_relations r where r.relkind in ('r','p','v','m','S','f')
  union all
  select 'column',r.schema_name||'.'||r.relname||'.'||a.attname,
    jsonb_build_object('position',a.attnum,'type',pg_catalog.format_type(a.atttypid,a.atttypmod),
      'not_null',a.attnotnull,'identity',a.attidentity,'generated',a.attgenerated,
      'dimensions',a.attndims,'storage',a.attstorage,'compression',a.attcompression,
      'collation',case when a.attcollation<>0 then cn.nspname||'.'||co.collname end,
      'default',pg_catalog.pg_get_expr(d.adbin,d.adrelid),
      'acl',coalesce((select jsonb_agg(jsonb_build_object(
        'grantee',case when ac.grantee=0 then 'PUBLIC' else pg_catalog.pg_get_userbyid(ac.grantee) end,
        'privilege',ac.privilege_type,'grantable',ac.is_grantable)
        order by ac.grantee=0,pg_catalog.pg_get_userbyid(ac.grantee),ac.privilege_type,ac.is_grantable)
        from pg_catalog.aclexplode(a.attacl) ac),'[]'::jsonb))
  from app_relations r join pg_catalog.pg_attribute a on a.attrelid=r.oid
  left join pg_catalog.pg_attrdef d on d.adrelid=r.oid and d.adnum=a.attnum
  left join pg_catalog.pg_collation co on co.oid=a.attcollation
  left join pg_catalog.pg_namespace cn on cn.oid=co.collnamespace
  where a.attnum>0 and not a.attisdropped and r.relkind in ('r','p','v','m','f')
  union all
  select 'constraint',r.schema_name||'.'||r.relname||'.'||c.conname,
    jsonb_build_object('type',c.contype,'definition',pg_catalog.pg_get_constraintdef(c.oid,true),
      'validated',c.convalidated,'deferrable',c.condeferrable,'deferred',c.condeferred,
      'no_inherit',c.connoinherit)
  from app_relations r join pg_catalog.pg_constraint c on c.conrelid=r.oid
  union all
  select 'index',r.schema_name||'.'||r.relname,
    jsonb_build_object('definition',pg_catalog.pg_get_indexdef(i.indexrelid),
      'unique',i.indisunique,'primary',i.indisprimary,'valid',i.indisvalid,
      'ready',i.indisready,'live',i.indislive,'replica_identity',i.indisreplident)
  from app_relations r join pg_catalog.pg_index i on i.indexrelid=r.oid
  union all
  select 'sequence',r.schema_name||'.'||r.relname,
    jsonb_build_object('type',pg_catalog.format_type(s.seqtypid,null),'start',s.seqstart,
      'increment',s.seqincrement,'min',s.seqmin,'max',s.seqmax,'cache',s.seqcache,'cycle',s.seqcycle,
      'owned_by',(select jsonb_agg(jsonb_build_object('table',tn.nspname||'.'||t.relname,
        'column',a.attname,'dependency',dep.deptype) order by tn.nspname,t.relname,a.attname)
        from pg_catalog.pg_depend dep
        join pg_catalog.pg_class t on t.oid=dep.refobjid
        join pg_catalog.pg_namespace tn on tn.oid=t.relnamespace
        join pg_catalog.pg_attribute a on a.attrelid=t.oid and a.attnum=dep.refobjsubid
        where dep.classid='pg_catalog.pg_class'::regclass and dep.objid=r.oid
          and dep.refclassid='pg_catalog.pg_class'::regclass and dep.deptype in ('a','i')))
  from app_relations r join pg_catalog.pg_sequence s on s.seqrelid=r.oid
  union all
  select 'view',r.schema_name||'.'||r.relname,
    jsonb_build_object('definition',pg_catalog.pg_get_viewdef(r.oid,true))
  from app_relations r where r.relkind in ('v','m')
  union all
  select 'policy',r.schema_name||'.'||r.relname||'.'||p.polname,
    jsonb_build_object('command',p.polcmd,'permissive',p.polpermissive,
      'roles',(select jsonb_agg(case when role_id=0 then 'PUBLIC' else pg_catalog.pg_get_userbyid(role_id) end
        order by case when role_id=0 then 'PUBLIC' else pg_catalog.pg_get_userbyid(role_id) end)
        from unnest(p.polroles) role_id),
      'using',pg_catalog.pg_get_expr(p.polqual,p.polrelid),
      'check',pg_catalog.pg_get_expr(p.polwithcheck,p.polrelid))
  from app_relations r join pg_catalog.pg_policy p on p.polrelid=r.oid
  union all
  select 'trigger',r.schema_name||'.'||r.relname||'.'||t.tgname,
    jsonb_build_object('definition',pg_catalog.pg_get_triggerdef(t.oid,true),'enabled',t.tgenabled)
  from app_relations r join pg_catalog.pg_trigger t on t.tgrelid=r.oid where not t.tgisinternal
  union all
  select 'function',p.schema_name||'.'||p.proname||'('||pg_catalog.pg_get_function_identity_arguments(p.oid)||')',
    jsonb_build_object('definition',pg_catalog.pg_get_functiondef(p.oid),
      'owner',pg_catalog.pg_get_userbyid(p.proowner),'security_definer',p.prosecdef,
      'leakproof',p.proleakproof,'acl',coalesce((select jsonb_agg(jsonb_build_object(
        'grantee',case when a.grantee=0 then 'PUBLIC' else pg_catalog.pg_get_userbyid(a.grantee) end,
        'privilege',a.privilege_type,'grantable',a.is_grantable)
        order by a.grantee=0,pg_catalog.pg_get_userbyid(a.grantee),a.privilege_type,a.is_grantable)
        from pg_catalog.aclexplode(coalesce(p.proacl,pg_catalog.acldefault('f',p.proowner))) a),'[]'::jsonb),
      'effective_app_execute',(select jsonb_agg(jsonb_build_object('role',role_name,
        'execute',pg_catalog.has_function_privilege(role_name,p.oid,'EXECUTE')) order by role_name)
        from unnest(array['anon','authenticated','service_role']) role_name))
  from app_functions p
  union all
  select 'role',r.rolname,jsonb_build_object('superuser',r.rolsuper,'inherit',r.rolinherit,
    'create_role',r.rolcreaterole,'create_db',r.rolcreatedb,'replication',r.rolreplication,'bypass_rls',r.rolbypassrls)
  from pg_catalog.pg_roles r where r.rolname in ('anon','authenticated','service_role')
  union all
  select 'extension',e.extname,jsonb_build_object('schema',n.nspname)
  from pg_catalog.pg_extension e join pg_catalog.pg_namespace n on n.oid=e.extnamespace
  where e.extname='pgcrypto'
)
select jsonb_build_object('format','onflows-catalog-v1',
  'postgres_major',current_setting('server_version_num')::integer/10000,
  'database_locale',(select jsonb_build_object('collate',datcollate,'ctype',datctype)
    from pg_catalog.pg_database where datname=current_database()),
  'objects',coalesce(jsonb_agg(jsonb_build_object('kind',kind,'identity',identity,
    'definition_sha256',encode(sha256(convert_to(definition::text,'UTF8')),'hex'))
    order by kind collate "C",identity collate "C"),'[]'::jsonb)) as catalog
from objects;
