-- Lifetime scientific facts are independent of the rolling operational cache.
-- Large original channels continue through the existing verified private archive.
create table onflows_private.history_activity_sources (
 athlete_alias text not null references public.onflows_intervals_connections(athlete_alias) on update cascade on delete cascade,
 activity_ref text not null, start_at_utc timestamptz, local_date date,
 source_revision bigint not null, source_generation_id uuid not null,
 primary key(athlete_alias,activity_ref)
);
create index history_activity_sources_age on onflows_private.history_activity_sources(start_at_utc);
create table onflows_private.history_day_inputs (
 athlete_alias text not null references public.onflows_intervals_connections(athlete_alias) on update cascade on delete cascade,
 day date not null, source_revision bigint not null, source_generation_id uuid not null,
 payload jsonb not null check(jsonb_typeof(payload)='object' and octet_length(payload::text)<=262144),
 updated_at timestamptz not null default clock_timestamp(),
 primary key(athlete_alias,day)
);
create table onflows_private.learning_timeline_days (
 athlete_alias text not null references public.onflows_intervals_connections(athlete_alias) on update cascade on delete cascade,
 day date not null, model_key text not null, model_version text not null,
 source_revision bigint not null, needs_refresh boolean not null default false, payload jsonb not null,
 prepared_at timestamptz not null default clock_timestamp(),
 primary key(athlete_alias,day),
 check(jsonb_typeof(payload)='object' and payload->>'day'=day::text and octet_length(payload::text)<=16384)
);
alter table onflows_private.history_activity_sources enable row level security;
alter table onflows_private.history_day_inputs enable row level security;
alter table onflows_private.learning_timeline_days enable row level security;
revoke all on onflows_private.history_activity_sources,onflows_private.history_day_inputs,onflows_private.learning_timeline_days from public,anon,authenticated,service_role;

