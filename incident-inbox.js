/* Read-only incident snapshots and per-device review state. No notifications. */
(function(root) {
  'use strict';
  const STORAGE_KEY = 'straz_incident_reviews_v1';
  const validId = id => Number.isSafeInteger(id) && id >= 0;
  function createReadTracker(storage) {
    let through = null, persistent = true;
    try {
      const saved = JSON.parse(storage.getItem(STORAGE_KEY));
      if(saved?.version===1 && validId(saved.through)) through=saved.through;
    } catch { persistent=false; }
    function save() {
      try { storage.setItem(STORAGE_KEY,JSON.stringify({version:1,through})); persistent=true; }
      catch { persistent=false; }
    }
    return {
      get through(){return through;}, get persistent(){return persistent;},
      initialize(head) { if(through===null && validId(head)){through=head;save();} },
      markReviewed(head) {
        if(through!==null && validId(head) && head>=through){through=head;save();}
      },
      isNew(item) { return through!==null && validId(Number(item.id)) && Number(item.id)>through && !item.mergedInto && !item.merged_into; }
    };
  }
  async function fetchSnapshot(client, through, pageSize=500) {
    const latest = await client.from('incidents').select('id').is('merged_into',null).order('id',{ascending:false}).limit(1);
    if(latest.error || !Array.isArray(latest.data)) throw new Error('Nie udało się sprawdzić nowych zdarzeń.');
    const head=Number(latest.data[0]?.id || 0);
    if(!validId(head)) throw new Error('Nieprawidłowy odczyt nowych zdarzeń.');
    // First use seeds a baseline; it does not label the whole archive as new.
    if(through===null || head<=through) return {head,rows:[]};
    const rows=[]; let cursor=through;
    while(cursor<head) {
      const page=await client.from('incidents').select('*').is('merged_into',null)
        .gt('id',cursor).lte('id',head).order('id',{ascending:true}).limit(pageSize);
      if(page.error || !Array.isArray(page.data)) throw new Error('Nie udało się pobrać wszystkich nowych zdarzeń.');
      if(!page.data.length) break;
      const last=Number(page.data.at(-1).id);
      if(!validId(last) || last<=cursor || last>head) throw new Error('Niepełny odczyt nowych zdarzeń.');
      rows.push(...page.data); cursor=last;
      if(page.data.length<pageSize) break;
    }
    return {head,rows};
  }
  function filterRows(rows,{date='',category='all',search='',bounds=null}={}) {
    const query=String(search).toLowerCase().trim();
    return rows.filter(item=>{
      if(item.mergedInto || item.merged_into) return false;
      if(date && item.dateStr!==date) return false;
      if(category!=='all' && item.category!==category) return false;
      if(query && !['title','location','region','units','desc'].some(key=>String(item[key]||'').toLowerCase().includes(query))) return false;
      if(bounds) {
        if(bounds.empty || !root.StrazCore.point(item.lat,item.lng)) return false;
        if(item.lat<bounds.south || item.lat>bounds.north || item.lng<bounds.west || item.lng>bounds.east) return false;
      }
      return true;
    });
  }
  const api={STORAGE_KEY,createReadTracker,fetchSnapshot,filterRows};
  root.StrazInbox=api;
  if(typeof module!=='undefined') module.exports=api;
})(typeof globalThis==='undefined' ? window : globalThis);
