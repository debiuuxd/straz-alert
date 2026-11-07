-- Owner-only atomic corrections. Public readers retain no UPDATE privilege.
create schema if not exists straz_editor;
revoke all on schema straz_editor from public,anon,authenticated;
grant usage on schema straz_editor to authenticated;
alter table public.incidents add column if not exists edit_revision bigint not null default 0;

create table straz_editor.incident_edits (
  id bigint generated always as identity primary key,
  action text not null check (action in ('move','merge')),
  incident_ids bigint[] not null,
  editor_id uuid not null,
  created_at timestamptz not null default now(),
  before_rows jsonb not null,
  after_rows jsonb not null,
  undone_at timestamptz
);
alter table straz_editor.incident_edits enable row level security;
revoke all on all tables in schema straz_editor from public,anon,authenticated;
revoke all on all sequences in schema straz_editor from public,anon,authenticated;

create function straz_editor.assert_owner() returns void
language plpgsql security invoker set search_path='' as $$
begin
  if auth.uid() is null or not exists (
    select 1 from public.app_editors where email=lower(auth.jwt()->>'email')
  ) then
    raise exception 'Edycja jest dostępna tylko dla zalogowanego właściciela.' using errcode='42501';
  end if;
end $$;

create function straz_editor.source_list(rows_json jsonb) returns jsonb
language plpgsql immutable set search_path='' as $$
declare rec jsonb; src jsonb; url text; seen jsonb:='{}'; result jsonb:='[]'; entries jsonb;
begin
  for rec in select value from jsonb_array_elements(rows_json) loop
    entries:=case when jsonb_typeof(rec->'sources')='array' then rec->'sources' else '[]'::jsonb end;
    if coalesce(rec->>'source_url','')<>'' then
      entries:=entries || jsonb_build_array(jsonb_build_object('url',rec->>'source_url','title',rec->>'title'));
    end if;
    for src in select value from jsonb_array_elements(entries) loop
      url:=src->>'url';
      if coalesce(url,'')<>'' and not (seen ? url) then
        result:=result || jsonb_build_array(src); seen:=seen || jsonb_build_object(url,true);
      end if;
    end loop;
  end loop;
  return result;
end $$;

create function straz_editor.guard_revision() returns trigger
language plpgsql security invoker set search_path='' as $$
begin
  if new.edit_revision=old.edit_revision then
    if old.merged_into is not null then
      raise exception 'Wpis został połączony. Pobierz aktualny wpis główny.' using errcode='40001';
    end if;
    if old.location_accuracy='manual' then
      -- Background enrichment must never undo a manually chosen position.
      new.lat:=old.lat; new.lng:=old.lng; new.location:=old.location;
      new.location_accuracy:=old.location_accuracy; new.local_alert:=old.local_alert;
    end if;
  end if;
  new.edit_revision:=old.edit_revision+1;
  return new;
end $$;
create trigger incidents_revision_guard before update on public.incidents
  for each row execute function straz_editor.guard_revision();

create function straz_editor.move_pin(p_id bigint,p_revision bigint,p_lat double precision,
  p_lng double precision,p_location text,p_local_alert boolean) returns jsonb
language plpgsql security definer set search_path='' set timezone='UTC' as $$
declare rec public.incidents%rowtype; after_row jsonb; edit_id bigint;
begin
  perform straz_editor.assert_owner();
  perform pg_advisory_xact_lock(5724051);
  if p_lat is null or p_lng is null or not (p_lat between 49 and 55.1 and p_lng between 14 and 24.2)
     or p_location is null or length(btrim(p_location)) not between 1 and 200 then
    raise exception 'Wskaż punkt w Polsce i podaj nazwę miejsca.' using errcode='22023';
  end if;
  select * into rec from public.incidents where id=p_id for update;
  if not found or rec.merged_into is not null then
    raise exception 'Ten wpis nie jest już dostępny. Odśwież mapę.' using errcode='40001';
  end if;
  if rec.edit_revision is distinct from p_revision then
    raise exception 'Wpis zmienił się w międzyczasie. Otwórz ponownie jego kartę.' using errcode='40001';
  end if;
  update public.incidents as i set lat=p_lat,lng=p_lng,location=btrim(p_location),
    location_accuracy='manual',local_alert=coalesce(p_local_alert,false),
    notify_requested=false,alert_eligible=false,updated_at=now(),edit_revision=i.edit_revision+1
    where i.id=p_id returning to_jsonb(i) into after_row;
  insert into straz_editor.incident_edits(action,incident_ids,editor_id,before_rows,after_rows)
    values ('move',array[p_id],auth.uid(),jsonb_build_array(to_jsonb(rec)),jsonb_build_array(after_row)) returning id into edit_id;
  return jsonb_build_object('incident_id',p_id,'edit_id',edit_id);
end $$;

create function straz_editor.merge_entries(p_keep_id bigint,p_duplicate_id bigint,
  p_keep_revision bigint,p_duplicate_revision bigint) returns jsonb
language plpgsql security definer set search_path='' set timezone='UTC' as $$
declare rec public.incidents%rowtype; kept public.incidents%rowtype; duplicate_row public.incidents%rowtype;
  before_json jsonb:='[]'; after_json jsonb; ids bigint[]:='{}'; combined jsonb; edit_id bigint;
begin
  perform straz_editor.assert_owner();
  perform pg_advisory_xact_lock(5724051);
  if p_keep_id is null or p_duplicate_id is null or p_keep_id=p_duplicate_id then
    raise exception 'Wybierz dwa różne wpisy.' using errcode='22023';
  end if;
  -- Serialize owner corrections and lock affected records in one fixed order.
  for rec in select * from public.incidents where id in (p_keep_id,p_duplicate_id)
      or merged_into=p_duplicate_id order by id for update loop
    before_json:=before_json || jsonb_build_array(to_jsonb(rec)); ids:=array_append(ids,rec.id);
  end loop;
  select * into kept from public.incidents where id=p_keep_id;
  if not found or kept.merged_into is not null then
    raise exception 'Wpis główny został zmieniony lub połączony. Otwórz ponownie podgląd.' using errcode='40001';
  end if;
  select * into duplicate_row from public.incidents where id=p_duplicate_id;
  if not found or duplicate_row.merged_into is not null then
    raise exception 'Drugi wpis został zmieniony lub połączony. Otwórz ponownie podgląd.' using errcode='40001';
  end if;
  if kept.edit_revision is distinct from p_keep_revision or duplicate_row.edit_revision is distinct from p_duplicate_revision then
    raise exception 'Wpisy zmieniły się w międzyczasie. Sprawdź ponownie podgląd.' using errcode='40001';
  end if;
  combined:=straz_editor.source_list(jsonb_build_array(to_jsonb(kept)) || before_json);
  update public.incidents as i set sources=combined,notify_requested=false,alert_eligible=false,
    updated_at=now(),edit_revision=i.edit_revision+1 where i.id=p_keep_id;
  update public.incidents as i set merged_into=p_keep_id,notify_requested=false,alert_eligible=false,
    updated_at=now(),edit_revision=i.edit_revision+1 where i.id=p_duplicate_id or i.merged_into=p_duplicate_id;
  select jsonb_agg(to_jsonb(i) order by i.id) into after_json from public.incidents i where i.id=any(ids);
  insert into straz_editor.incident_edits(action,incident_ids,editor_id,before_rows,after_rows)
    values ('merge',ids,auth.uid(),before_json,after_json) returning id into edit_id;
  return jsonb_build_object('incident_id',p_keep_id,'edit_id',edit_id,'sources_count',jsonb_array_length(combined));
end $$;

create function straz_editor.last_edit(p_id bigint) returns jsonb
language plpgsql security definer set search_path='' set timezone='UTC' as $$
declare entry straz_editor.incident_edits%rowtype; current_json jsonb;
begin
  perform straz_editor.assert_owner();
  select * into entry from straz_editor.incident_edits where p_id=any(incident_ids) order by id desc limit 1;
  if not found or entry.undone_at is not null then return null; end if;
  select jsonb_agg(to_jsonb(i) order by i.id) into current_json from public.incidents i where i.id=any(entry.incident_ids);
  return jsonb_build_object('edit_id',entry.id,'action',entry.action,'created_at',entry.created_at,
    'can_undo',current_json=entry.after_rows);
end $$;

create function straz_editor.undo_edit(p_edit_id bigint) returns jsonb
language plpgsql security definer set search_path='' set timezone='UTC' as $$
declare entry straz_editor.incident_edits%rowtype; current_json jsonb; old_row jsonb; result_id bigint;
begin
  perform straz_editor.assert_owner();
  perform pg_advisory_xact_lock(5724051);
  select * into entry from straz_editor.incident_edits where id=p_edit_id for update;
  if not found or entry.undone_at is not null then
    raise exception 'Ta poprawka nie jest już dostępna do cofnięcia.' using errcode='40001';
  end if;
  perform 1 from public.incidents where id=any(entry.incident_ids) order by id for update;
  select jsonb_agg(to_jsonb(i) order by i.id) into current_json from public.incidents i where i.id=any(entry.incident_ids);
  if current_json is distinct from entry.after_rows then
    raise exception 'Wpis został później zmieniony. Cofnięcie nadpisałoby nowsze dane.' using errcode='40001';
  end if;
  for old_row in select value from jsonb_array_elements(entry.before_rows) loop
    update public.incidents as i set lat=(old_row->>'lat')::double precision,lng=(old_row->>'lng')::double precision,
      location=old_row->>'location',location_accuracy=old_row->>'location_accuracy',
      local_alert=(old_row->>'local_alert')::boolean,sources=old_row->'sources',
      merged_into=(old_row->>'merged_into')::bigint,notify_requested=false,alert_eligible=false,
      updated_at=now(),edit_revision=i.edit_revision+1 where i.id=(old_row->>'id')::bigint;
    if old_row->>'merged_into' is null then result_id:=(old_row->>'id')::bigint; end if;
  end loop;
  update straz_editor.incident_edits set undone_at=now() where id=p_edit_id;
  return jsonb_build_object('incident_id',result_id,'edit_id',p_edit_id);
end $$;

-- Definer code lives outside the exposed schema and checks the JWT owner list.
revoke all on all functions in schema straz_editor from public,anon,authenticated;
grant execute on function straz_editor.move_pin(bigint,bigint,double precision,double precision,text,boolean),
  straz_editor.merge_entries(bigint,bigint,bigint,bigint),straz_editor.last_edit(bigint),straz_editor.undo_edit(bigint) to authenticated;

create function public.owner_move_incident(p_id bigint,p_revision bigint,p_lat double precision,
  p_lng double precision,p_location text,p_local_alert boolean) returns jsonb
language sql security invoker set search_path='' as $$
  select straz_editor.move_pin(p_id,p_revision,p_lat,p_lng,p_location,p_local_alert);
$$;
create function public.owner_merge_incidents(p_keep_id bigint,p_duplicate_id bigint,p_keep_revision bigint,p_duplicate_revision bigint) returns jsonb
language sql security invoker set search_path='' as $$
  select straz_editor.merge_entries(p_keep_id,p_duplicate_id,p_keep_revision,p_duplicate_revision);
$$;
create function public.owner_last_incident_edit(p_id bigint) returns jsonb
language sql security invoker set search_path='' as $$ select straz_editor.last_edit(p_id); $$;
create function public.owner_undo_incident_edit(p_edit_id bigint) returns jsonb
language sql security invoker set search_path='' as $$ select straz_editor.undo_edit(p_edit_id); $$;
revoke all on function public.owner_move_incident(bigint,bigint,double precision,double precision,text,boolean),
  public.owner_merge_incidents(bigint,bigint,bigint,bigint),public.owner_last_incident_edit(bigint),public.owner_undo_incident_edit(bigint) from public,anon;
grant execute on function public.owner_move_incident(bigint,bigint,double precision,double precision,text,boolean),
  public.owner_merge_incidents(bigint,bigint,bigint,bigint),public.owner_last_incident_edit(bigint),public.owner_undo_incident_edit(bigint) to authenticated;
