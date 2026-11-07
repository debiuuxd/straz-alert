/* Owner corrections use checked RPCs; no browser UPDATE permission is needed. */
(function(root){
  'use strict';
  const core=root.StrazCore || (typeof require==='function' ? require('./app-core.js') : null);
  const esc=core.escapeHtml;
  function sourceLinks(row){
    const list=[...(Array.isArray(row.sources)?row.sources:[])];
    if(row.source_url)list.push({url:row.source_url,title:'Materiał źródłowy'});
    const seen=new Map();
    for(const source of list)if(core.safeUrl(source.url) && !seen.has(core.safeUrl(source.url)))seen.set(core.safeUrl(source.url),source);
    return [...seen.values()];
  }
  function moveArgs(row,lat,lng,location,area){
    if(!core.point(lat,lng) || !String(location).trim() || String(location).trim().length>200)
      throw Error('Wskaż punkt w Polsce i podaj nazwę miejsca (do 200 znaków).');
    return {p_id:Number(row.id),p_revision:Number(row.edit_revision||0),p_lat:Number(lat),p_lng:Number(lng),
      p_location:String(location).trim(),p_local_alert:core.localIncident({title:row.title,location,lat,lng},area)};
  }
  function mergeArgs(rows,keepId){
    const kept=rows.find(r=>Number(r.id)===Number(keepId)),other=rows.find(r=>Number(r.id)!==Number(keepId));
    if(rows.length!==2 || !kept || !other)throw Error('Wybierz dwa różne wpisy.');
    return {p_keep_id:Number(kept.id),p_duplicate_id:Number(other.id),
      p_keep_revision:Number(kept.edit_revision||0),p_duplicate_revision:Number(other.edit_revision||0)};
  }
  function init(o){
    const section=document.getElementById('owner-actions');
    let currentId=null,cardToken=0,editing=null,busy=false,searchToken=0,searchTimer=null,returnFocus=null;
    section.innerHTML='<h4>Popraw zdarzenie</h4><p>Opcje właściciela · bez dodatkowych powiadomień</p><div class="owner-buttons"><button id="owner-move">Przesuń pinezkę</button><button id="owner-merge">Połącz duplikat</button><button id="owner-undo" hidden>Cofnij ostatnią poprawkę</button></div><p id="owner-card-state" role="status"></p>';
    const byId=id=>document.getElementById(id);
    const dialog=document.createElement('dialog');dialog.id='owner-dialog';dialog.className='owner-dialog';
    dialog.setAttribute('aria-labelledby','owner-dialog-title');document.body.append(dialog);
    const picker=document.createElement('section');picker.id='owner-picker';picker.hidden=true;
    picker.setAttribute('aria-label','Poprawianie położenia pinezki');
    picker.innerHTML='<h3>Wskaż prawidłowe miejsce</h3><p>Kliknij mapę lub przeciągnij niebieską pinezkę.</p><label>Nazwa miejsca<input id="owner-place" maxlength="200" autocomplete="off"></label><p id="owner-coordinates"></p><p id="owner-picker-state" role="status"></p><div class="owner-buttons"><button id="owner-cancel-move">Anuluj</button><button id="owner-reload" hidden>Odśwież dane wpisu</button><button id="owner-save-move" class="owner-primary">Zapisz położenie</button></div>';
    document.body.append(picker);
    function requireOwner(){if(!o.canEdit())throw Error('Zaloguj się jako właściciel w ustawieniach.');}
    async function rpc(name,args){requireOwner();const {data,error}=await o.client().rpc(name,args);if(error)throw error;return data;}
    async function fetchRow(id){
      const {data,error}=await o.client().from('incidents').select('*').eq('id',id).single();
      if(error)throw error;
      if(data.merged_into)throw Error('Ten wpis został już połączony. Odśwież mapę.');
      return data;
    }
    function message(target,error){target.innerText=error?.message || 'Nie udało się zapisać. Sprawdź połączenie i spróbuj ponownie.';}
    function buttonsLocked(locked){busy=locked;for(const group of [section,dialog,picker])group.querySelectorAll('button,input').forEach(b=>b.disabled=locked);}
    async function afterSaved(result){
      try{await o.saved(result.incident_id);syncCard(result.incident_id);}
      catch{alert('Poprawka została zapisana, ale nie udało się odświeżyć widoku. Odśwież stronę, żeby zobaczyć wynik.');}
    }
    function finishDialog(){++searchToken;clearTimeout(searchTimer);dialog.close();if(returnFocus?.isConnected)returnFocus.focus({preventScroll:true});}
    function showDialog(title,content){
      returnFocus=document.activeElement;
      dialog.innerHTML=`<header><h3 id="owner-dialog-title">${esc(title)}</h3><button id="owner-close" aria-label="Zamknij">✕</button></header>${content}<p id="owner-dialog-state" role="status"></p>`;
      byId('owner-close').onclick=()=>{if(!busy)finishDialog();};dialog.showModal();
    }
    dialog.addEventListener('cancel',e=>{e.preventDefault();if(!busy)finishDialog();});
    async function syncCard(id=currentId){
      currentId=id;const token=++cardToken;section.hidden=!o.canEdit() || !id;
      byId('owner-undo').hidden=true;byId('owner-card-state').innerText='';
      if(section.hidden)return;
      try {
        const last=await rpc('owner_last_incident_edit',{p_id:id});
        if(token!==cardToken || !o.canEdit())return;
        if(last?.can_undo){byId('owner-undo').hidden=false;byId('owner-undo').onclick=()=>previewUndo(id,last);}
        else if(last)byId('owner-card-state').innerText='Późniejsza aktualizacja wpisu wyłączyła cofnięcie poprzedniej poprawki.';
      }catch(error){if(token===cardToken)message(byId('owner-card-state'),error);}
    }
    function cancelMove(restore=true){
      if(!editing)return;
      const draft=editing;editing=null;
      o.map.off('click',draft.click);if(draft.marker)o.map.removeLayer(draft.marker);
      picker.hidden=true;document.body.classList.remove('picking-pin');
      if(restore)o.map.setView(draft.camera.center,draft.camera.zoom,{animate:false});
    }
    async function beginMove(){
      if(busy)return;
      const id=currentId;
      try{
        buttonsLocked(true);
        requireOwner();const row=await fetchRow(id);requireOwner();
        o.closeDetails();o.closeList();
        editing={row,marker:null,point:null,camera:{center:o.map.getCenter(),zoom:o.map.getZoom()}};
        document.body.classList.add('picking-pin');picker.hidden=false;
        byId('owner-place').value=row.location;byId('owner-picker-state').innerText='';byId('owner-reload').hidden=true;
        function setPoint(point){
          if(!editing)return;
          if(!core.point(point.lat,point.lng)){byId('owner-picker-state').innerText='Wybierz punkt w Polsce.';return;}
          byId('owner-picker-state').innerText='';editing.point=point;
          if(!editing.marker){
            editing.marker=L.marker(point,{draggable:true,autoPan:true,title:'Przeciągnij do prawidłowego miejsca',
              icon:L.divIcon({className:'owner-draft-pin',html:'<span>＋</span>',iconSize:[44,44],iconAnchor:[22,22]})}).addTo(o.map);
            editing.marker.on('dragend',()=>setPoint(editing.marker.getLatLng()));
          }else editing.marker.setLatLng(point);
          byId('owner-coordinates').innerText=`Wybrany punkt: ${point.lat.toFixed(5)}, ${point.lng.toFixed(5)}`;
          byId('owner-save-move').disabled=false;
        }
        editing.click=e=>setPoint(e.latlng);o.map.on('click',editing.click);
        byId('owner-coordinates').innerText='Nie wybrano punktu.';byId('owner-save-move').disabled=true;
        if(core.point(row.lat,row.lng)){setPoint(L.latLng(row.lat,row.lng));o.map.setView([row.lat,row.lng],Math.max(o.map.getZoom(),14),{animate:false});}
        byId('owner-place').focus({preventScroll:true});
      }catch(error){message(byId('owner-card-state'),error);}
      finally{buttonsLocked(false);if(editing && !editing.point)byId('owner-save-move').disabled=true;}
    }
    byId('owner-cancel-move').onclick=()=>{if(!busy)cancelMove();};
    byId('owner-save-move').onclick=async()=>{
      if(!editing || busy)return;
      const draft=editing;
      try{
        const args=moveArgs(draft.row,draft.point?.lat,draft.point?.lng,byId('owner-place').value,o.area());
        buttonsLocked(true);const result=await rpc('owner_move_incident',args);cancelMove(false);await afterSaved(result);
      }catch(error){message(byId('owner-picker-state'),error);byId('owner-reload').hidden=error.code!=='40001';}
      finally{buttonsLocked(false);}
    };
    byId('owner-reload').onclick=async()=>{
      if(!editing || busy)return;
      try{buttonsLocked(true);editing.row=await fetchRow(editing.row.id);byId('owner-picker-state').innerText='Dane odświeżone. Sprawdź wybrane miejsce i ponownie naciśnij „Zapisz położenie”.';byId('owner-reload').hidden=true;}
      catch(error){message(byId('owner-picker-state'),error);}finally{buttonsLocked(false);}
    };
    function previewCard(row){
      return `<h4>${esc(row.title)}</h4><p>${esc(row.location)} · ${esc(row.event_date||row.date_str)}${row.event_time?' · '+esc(row.event_time):''}</p><p>${core.point(row.lat,row.lng)?`${Number(row.lat).toFixed(5)}, ${Number(row.lng).toFixed(5)}`:'Brak ustalonego punktu'}</p><p class="owner-description">${esc(row.description)}</p><ul>${sourceLinks(row).map(s=>`<li><a href="${esc(core.safeUrl(s.url))}" target="_blank" rel="noopener noreferrer">${esc(s.title||new URL(s.url).hostname)} ↗</a></li>`).join('')}</ul>`;
    }
    async function beginMerge(){
      if(busy)return;
      try{
        buttonsLocked(true);
        requireOwner();const first=await fetchRow(currentId);requireOwner();
        showDialog('Znajdź drugi wpis',`<p>Wybierz drugi opis tego samego zdarzenia. Lista obejmuje wszystkie dni i cały kraj.</p><label>Szukaj po miejscu lub tytule<input id="owner-search" type="search" maxlength="100" autocomplete="off"></label><div id="owner-candidates"></div>`);
        const input=byId('owner-search');input.value=first.location.split(',')[0];
        async function search(){
          const seq=++searchToken,query=input.value.trim();byId('owner-candidates').innerText='Wyszukiwanie…';
          try{
            const base=()=>o.client().from('incidents').select('id,title,location,event_date,date_str').is('merged_into',null).neq('id',first.id).order('id',{ascending:false}).limit(40);
            const pattern='%'+query.replace(/[\\%_]/g,'\\$&')+'%';
            const results=query ? await Promise.all([base().ilike('location',pattern),base().ilike('title',pattern)]) : [await base()];
            if(seq!==searchToken || !dialog.open)return;
            for(const result of results)if(result.error)throw result.error;
            const rows=[...new Map(results.flatMap(r=>r.data||[]).map(r=>[r.id,r])).values()];
            rows.sort((a,b)=>Number(b.event_date===first.event_date && !!first.event_date)-Number(a.event_date===first.event_date && !!first.event_date) || Number(b.id)-Number(a.id));
            byId('owner-candidates').innerHTML=rows.map(row=>`<button data-merge-id="${Number(row.id)}"><strong>${esc(row.title)}</strong><small>${esc(row.location)} · ${esc(row.event_date||row.date_str)}</small></button>`).join('') || '<p>Brak pasujących wpisów. Spróbuj krótszej nazwy.</p>';
            byId('owner-candidates').querySelectorAll('button').forEach(button=>button.onclick=()=>previewMerge(first.id,Number(button.dataset.mergeId)));
          }catch(error){if(seq===searchToken)message(byId('owner-candidates'),error);}
        }
        input.oninput=()=>{clearTimeout(searchTimer);++searchToken;searchTimer=setTimeout(search,250);};search();input.focus();input.select();
      }catch(error){message(byId('owner-card-state'),error);}
      finally{buttonsLocked(false);}
    }
    async function previewMerge(firstId,secondId){
      try{
        const rows=await Promise.all([fetchRow(firstId),fetchRow(secondId)]);requireOwner();++searchToken;
        if(!dialog.open)return;
        const defaultKeep=Math.min(firstId,secondId);
        dialog.innerHTML=`<header><h3 id="owner-dialog-title">Sprawdź łączenie</h3><button id="owner-close" aria-label="Zamknij">✕</button></header><p>Wybierz opis i położenie, które mają zostać. Materiały źródłowe z obu wpisów zostaną zachowane.</p><div class="owner-merge-grid">${rows.map(row=>`<label class="owner-merge-card"><span><input type="radio" name="owner-keep" value="${Number(row.id)}" ${Number(row.id)===defaultKeep?'checked':''}> Zachowaj ten opis i punkt</span>${previewCard(row)}</label>`).join('')}</div><label class="owner-confirm"><input id="owner-confirm-merge" type="checkbox"> Sprawdziłem opisy i źródła — to jest to samo zdarzenie.</label><p>Drugi wpis zniknie z mapy. Nie zostanie trwale usunięty i nie wyśle powiadomienia.</p><div class="owner-buttons"><button id="owner-back">Wróć do wyszukiwania</button><button id="owner-submit-merge" class="owner-primary" disabled>Połącz wpisy</button></div><p id="owner-dialog-state" role="status"></p>`;
        byId('owner-close').onclick=()=>{if(!busy)finishDialog();};byId('owner-back').onclick=()=>{finishDialog();beginMerge();};
        byId('owner-confirm-merge').onchange=e=>byId('owner-submit-merge').disabled=!e.target.checked;
        byId('owner-submit-merge').onclick=async()=>{
          if(busy || !byId('owner-confirm-merge').checked)return;
          try{
            buttonsLocked(true);const result=await rpc('owner_merge_incidents',mergeArgs(rows,dialog.querySelector('input[name="owner-keep"]:checked').value));
            finishDialog();await afterSaved(result);
          }catch(error){message(byId('owner-dialog-state'),error);}
          finally{buttonsLocked(false);if(dialog.open && byId('owner-confirm-merge'))byId('owner-submit-merge').disabled=!byId('owner-confirm-merge').checked;}
        };
        byId('owner-close').focus();
      }catch(error){if(dialog.open)message(byId('owner-dialog-state'),error);}
    }
    function previewUndo(id,last){
      showDialog('Cofnij ostatnią poprawkę',`<p>${last.action==='merge'?'Przywrócisz dwa osobne wpisy i ich wcześniejsze źródła.':'Przywrócisz poprzednie położenie pinezki i nazwę miejsca.'} Ta operacja nie wyśle powiadomień.</p><p>Jeżeli bot lub właściciel później zmienił wpis, cofnięcie zostanie zablokowane, żeby zachować nowsze dane.</p><button id="owner-submit-undo" class="owner-primary">Cofnij poprawkę</button>`);
      byId('owner-submit-undo').onclick=async()=>{
        try{buttonsLocked(true);const result=await rpc('owner_undo_incident_edit',{p_edit_id:last.edit_id});finishDialog();await afterSaved(result);}
        catch(error){message(byId('owner-dialog-state'),error);}finally{buttonsLocked(false);}
      };
    }
    byId('owner-move').onclick=beginMove;byId('owner-merge').onclick=beginMerge;
    document.addEventListener('keydown',e=>{if(e.key==='Escape' && editing && !busy){e.preventDefault();cancelMove();}});
    return {syncCard,accessChanged(){if(!o.canEdit()){cancelMove();if(dialog.open)finishDialog();}syncCard();},isPicking:()=>Boolean(editing)};
  }
  const api={init,moveArgs,mergeArgs,sourceLinks};root.StrazOwner=api;if(typeof module!=='undefined')module.exports=api;
})(typeof globalThis==='undefined'?window:globalThis);
