-- Keep original records and their external identifiers, but show one canonical pin.
-- A deliberate owner deletion still deletes the whole event, including its aliases.
alter table public.incidents add column if not exists merged_into bigint
  references public.incidents(id) on delete cascade;
do $$ begin
  if not exists (select 1 from pg_constraint where conname='incidents_cannot_merge_into_self'
                 and conrelid='public.incidents'::regclass) then
    alter table public.incidents add constraint incidents_cannot_merge_into_self
      check (merged_into is null or merged_into <> id);
  end if;
end $$;
create index if not exists incidents_merged_into_idx on public.incidents(merged_into);
