-- Additive migration. No incidents are removed or historical event times invented.
begin;
alter table public.incidents alter column lat drop not null;
alter table public.incidents alter column lng drop not null;
alter table public.incidents alter column status set default 'Nieustalony';
alter table public.incidents add column if not exists event_date date;
alter table public.incidents add column if not exists event_time text;
alter table public.incidents add column if not exists published_at timestamptz;
alter table public.incidents add column if not exists updated_at timestamptz default now();
alter table public.incidents add column if not exists status_observed_at timestamptz;
alter table public.incidents add column if not exists location_accuracy text not null default 'legacy';
alter table public.incidents add column if not exists local_alert boolean;
alter table public.incidents add column if not exists sources jsonb not null default '[]'::jsonb;
alter table public.incidents add column if not exists notify_requested boolean not null default false;
alter table public.incidents add column if not exists alert_eligible boolean not null default false;
create index if not exists incidents_date_str_idx on public.incidents(date_str);
create index if not exists incidents_event_date_idx on public.incidents(event_date);

create table if not exists public.worker_sources (
  source_key text primary key, label text not null, last_attempt timestamptz,
  last_success timestamptz, status text not null default 'unknown',
  entry_count integer not null default 0, pending_count integer not null default 0,
  detail text not null default '', initialized_at timestamptz
);
alter table public.worker_sources enable row level security;
revoke all on public.worker_sources from anon, authenticated;
grant select on public.worker_sources to anon, authenticated;
grant all on public.worker_sources to service_role;
create policy "Read source health" on public.worker_sources for select to anon, authenticated using (true);

create table if not exists public.feed_queue (
  external_id text primary key, source_key text not null,
  payload jsonb not null, state text not null default 'pending',
  first_seen timestamptz not null default now(), next_attempt timestamptz not null default now(),
  attempts integer not null default 0, incident_id bigint references public.incidents(id) on delete set null,
  last_error text not null default ''
);
create index if not exists feed_queue_pending_idx on public.feed_queue(state,next_attempt,first_seen);
create index if not exists feed_queue_incident_id_idx on public.feed_queue(incident_id);
alter table public.feed_queue enable row level security;
revoke all on public.feed_queue from anon, authenticated;
grant all on public.feed_queue to service_role;

create table if not exists public.geocode_cache (
  query text primary key, lat double precision, lng double precision,
  checked_at timestamptz not null default now()
);
alter table public.geocode_cache enable row level security;
revoke all on public.geocode_cache from anon, authenticated;
grant all on public.geocode_cache to service_role;

create table if not exists public.notification_deliveries (
  incident_id bigint not null references public.incidents(id) on delete cascade,
  topic text not null, state text not null default 'reserved',
  attempted_at timestamptz not null default now(), sent_at timestamptz,
  primary key(incident_id,topic)
);
alter table public.notification_deliveries enable row level security;
revoke all on public.notification_deliveries from anon, authenticated;
grant all on public.notification_deliveries to service_role;

-- New sources start in quiet mode: first successful fetch seeds the baseline.
insert into public.worker_sources(source_key,label) values
 ('psp','PSP Łódź'),('press-0','Google News — PSP'),
 ('press-1','Google News — okolica'),('press-2','Google News — drogi'),
 ('press-3','Remiza'),('press-4','Google News — Polska'),('worker','Bot')
on conflict do nothing;
commit;
