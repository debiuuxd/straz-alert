-- Integration checks against the real RPCs and roles. All fixtures roll back.
begin;
do $$
declare a bigint; b bigint; c bigint; owner_email text; result jsonb; edit_id bigint; rev bigint;
  denied boolean; sources_count integer; before_position double precision;
begin
  select email into strict owner_email from public.app_editors;
  insert into public.incidents(external_id,title,category,location,date_str,time_str,units,description,lat,lng,location_accuracy,source_url)
    values ('qa-rollback-'||gen_random_uuid(),'Test A','fire','Stryków','2026-11-07','','Brak','Opis A',51.9,19.6,'town','https://example.org/a') returning id into a;
  insert into public.incidents(external_id,title,category,location,date_str,time_str,units,description,lat,lng,location_accuracy,source_url)
    values ('qa-rollback-'||gen_random_uuid(),'Test B','fire','Stryków','2026-11-07','','Brak','Opis B',51.91,19.61,'town','https://example.org/b') returning id into b;
  insert into public.incidents(external_id,title,category,location,date_str,time_str,units,description,merged_into,source_url)
    values ('qa-rollback-'||gen_random_uuid(),'Test alias','fire','Stryków','2026-11-07','','Brak','Alias',b,'https://example.org/c') returning id into c;
  if has_table_privilege('authenticated','public.incidents','UPDATE') or has_function_privilege('anon','public.owner_last_incident_edit(bigint)','EXECUTE') then
    raise exception 'Unexpected public update/RPC permission';
  end if;
  perform set_config('request.jwt.claims',jsonb_build_object('sub',gen_random_uuid(),'email','other@example.org','role','authenticated')::text,true);
  execute 'set local role authenticated';
  denied:=false;
  begin perform public.owner_move_incident(a,0,51.901,19.602,'Stryków',true);
    exception when insufficient_privilege then denied:=true; end;
  if not denied then raise exception 'Non-owner was allowed'; end if;
  execute 'reset role';
  perform set_config('request.jwt.claims',jsonb_build_object('sub',gen_random_uuid(),'email',owner_email,'role','authenticated')::text,true);
  execute 'set local role authenticated';
  denied:=false;
  begin perform public.owner_move_incident(a,0,0,0,'Test',false);
    exception when invalid_parameter_value then denied:=true; end;
  if not denied then raise exception 'Invalid coordinates accepted'; end if;
  result:=public.owner_move_incident(a,0,51.901,19.602,'Stryków, poprawiony punkt',true);
  edit_id:=(result->>'edit_id')::bigint;
  if not (public.owner_last_incident_edit(a)->>'can_undo')::boolean then raise exception 'Move cannot be undone'; end if;
  denied:=false;
  begin perform public.owner_move_incident(a,0,51.902,19.603,'Stryków',true);
    exception when serialization_failure then denied:=true; end;
  if not denied then raise exception 'Stale revision accepted'; end if;
  perform public.owner_undo_incident_edit(edit_id);
  execute 'reset role';
  select edit_revision into rev from public.incidents where id=a;
  if (select lat<>51.9 or location_accuracy<>'town' from public.incidents where id=a) then raise exception 'Undo failed'; end if;
  execute 'set local role authenticated';
  result:=public.owner_move_incident(a,rev,51.901,19.602,'Stryków, poprawiony punkt',true);
  edit_id:=(result->>'edit_id')::bigint;
  execute 'reset role';
  -- Ordinary background writes may enrich content but must preserve owner geometry.
  update public.incidents set lat=52,lng=20,location='Wrong stale point',location_accuracy='town',local_alert=false,description='Nowszy opis' where id=a;
  if (select lat<>51.901 or lng<>19.602 or location<>'Stryków, poprawiony punkt' or location_accuracy<>'manual' or not local_alert from public.incidents where id=a) then raise exception 'Background write overrode manual point'; end if;
  select edit_revision into rev from public.incidents where id=a;
  execute 'set local role authenticated';
  if (public.owner_last_incident_edit(a)->>'can_undo')::boolean then raise exception 'Undo allowed after update'; end if;
  denied:=false;
  begin perform public.owner_undo_incident_edit(edit_id);
    exception when serialization_failure then denied:=true; end;
  if not denied then raise exception 'Stale undo accepted'; end if;
  result:=public.owner_merge_incidents(a,b,rev,0);edit_id:=(result->>'edit_id')::bigint;
  execute 'reset role';
  if (select merged_into from public.incidents where id=b)<>a or (select merged_into from public.incidents where id=c)<>a then raise exception 'Aliases not flattened'; end if;
  if (select jsonb_array_length(sources) from public.incidents where id=a)<>3 then raise exception 'Sources lost'; end if;
  if (select count(*) from public.incidents where id in(a,b,c))<>3 then raise exception 'Merge deleted records'; end if;
  if exists(select 1 from public.incidents where id in(a,b,c) and (notify_requested or alert_eligible)) then raise exception 'Correction enabled notifications'; end if;
  denied:=false;
  begin update public.incidents set description='Stale alias update' where id=b;
    exception when serialization_failure then denied:=true; end;
  if not denied then raise exception 'Stale alias accepted update'; end if;
  execute 'set local role authenticated';
  perform public.owner_undo_incident_edit(edit_id);
  execute 'reset role';
  if (select merged_into from public.incidents where id=b) is not null or (select merged_into from public.incidents where id=c)<>b then raise exception 'Merge undo failed'; end if;
  if (select jsonb_array_length(sources) from public.incidents where id=a)<>0 then raise exception 'Undo did not restore sources'; end if;
  execute 'set local role anon';
  denied:=false;
  begin perform public.owner_last_incident_edit(a); exception when insufficient_privilege then denied:=true; end;
  if not denied then raise exception 'Anonymous audit exposed'; end if;
  execute 'reset role';
end $$;
rollback;
select 'Owner access, pin protection, revisions, source preservation, alias flattening and undo checks passed; fixtures rolled back.' as result;
