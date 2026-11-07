-- Apply only with the prepared login UI and confirmed owner email in app_editors.
begin;
create table if not exists public.app_editors (
  email text primary key check (email = lower(email))
);
alter table public.app_editors enable row level security;
revoke all on public.app_editors from anon, authenticated;
grant select on public.app_editors to authenticated;
grant all on public.app_editors to service_role;
create policy "Read own editor membership" on public.app_editors for select to authenticated
  using (email = lower((select auth.jwt()->>'email')));
drop policy if exists "Pozwól na dodawanie zgłoszeń" on public.incidents;
drop policy if exists "Allow public delete" on public.incidents;
revoke insert, update, delete, truncate, references, trigger on public.incidents from anon, authenticated;
grant insert, delete on public.incidents to authenticated;
create policy "Owner can add incidents" on public.incidents for insert to authenticated
  with check ((select auth.uid()) is not null and exists
    (select 1 from public.app_editors where email = lower((select auth.jwt()->>'email'))));
create policy "Owner can delete incidents" on public.incidents for delete to authenticated
  using ((select auth.uid()) is not null and exists
    (select 1 from public.app_editors where email = lower((select auth.jwt()->>'email'))));
commit;
