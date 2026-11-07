/* View and marker presentation; no database writes or notification settings. */
(function (root) {
  'use strict';
  const views = {
    poland: [[49, 14], [55.1, 24.2]],
    local: [[51.79, 19.43], [52.05, 19.89]]
  };
  function samePosition(markers) {
    if (!markers.length) return false;
    const first=markers[0].getLatLng();
    return markers.every(marker=>{
      const point=marker.getLatLng();
      return Math.abs(point.lat-first.lat)<1e-7 && Math.abs(point.lng-first.lng)<1e-7;
    });
  }
  function markerGroup(leaflet, options={}) {
    if (!leaflet.markerClusterGroup) return leaflet.layerGroup();
    const group=leaflet.markerClusterGroup({
      maxClusterRadius: zoom => zoom<9 ? 52 : 36,
      showCoverageOnHover: false, spiderfyOnMaxZoom: false, zoomToBoundsOnClick: false,
      animate: false,
      iconCreateFunction(cluster) {
        const count = cluster.getChildCount();
        const noun = count%10>=2 && count%10<=4 && !(count%100>=12 && count%100<=14) ? 'wpisy' : 'wpisów';
        const shared=samePosition(cluster.getAllChildMarkers());
        cluster.options.title = `${count} ${noun} — ${shared ? 'ten sam zapisany punkt; otwórz listę' : 'przybliż lub otwórz listę'}`;
        cluster.options.alt = cluster.options.title;
        return leaflet.divIcon({className:'event-cluster',html:`<span>${count}</span>`,iconSize:[44,44]});
      }
    });
    // Spiderfying changes the displayed marker positions. For shared town
    // coordinates use a list instead, without inventing separate locations.
    group.on('clusterclick clusterkeypress',event=>{
      if(event.type==='clusterkeypress' && event.originalEvent?.keyCode!==13)return;
      const cluster=event.layer, markers=cluster.getAllChildMarkers(), map=options.map;
      const shared=samePosition(markers);
      if(shared || !map || map.getZoom()>=map.getMaxZoom()) {
        options.onSelectGroup?.(markers,{samePoint:shared});return;
      }
      const padding=options.padding?.() || {};
      const bounds=cluster.getBounds();
      const insets=leaflet.point(padding.paddingTopLeft || [20,20]).add(leaflet.point(padding.paddingBottomRight || [20,20]));
      const fitZoom=map.getBoundsZoom(bounds,false,insets);
      const next=Math.min(map.getMaxZoom(),Math.max(map.getZoom()+1,fitZoom));
      map.fitBounds(bounds,{...padding,maxZoom:next,animate:false});
      // fitBounds may choose a lower zoom to fit widely spread points. Ensure
      // a click always makes progress for a cluster partly behind the toolbar.
      if(map.getZoom()<next)map.setView(bounds.getCenter(),next,{animate:false});
    });
    return group;
  }
  function showClusterList(items,onSelect,context={}) {
    document.getElementById('cluster-dialog')?.remove();
    const esc=root.StrazCore.escapeHtml, previous=document.activeElement;
    let selecting=false;
    items=[...items].sort((a,b)=>b.id-a.id);
    const dialog=document.createElement('dialog');dialog.id='cluster-dialog';dialog.className='cluster-dialog';
    dialog.setAttribute('aria-labelledby','cluster-dialog-title');
    const message=context.samePoint
      ? 'Te wpisy mają zapisany ten sam punkt na mapie. Zbliżenie nie ustali ich dokładniejszego położenia. Wybierz zdarzenie, aby sprawdzić opis i źródła.'
      : 'Pinezki leżą bardzo blisko siebie. Wybierz zdarzenie z listy, aby sprawdzić jego opis i źródła.';
    dialog.innerHTML=`<header><h3 id="cluster-dialog-title">${context.samePoint?'Wspólny punkt na mapie':'Zdarzenia blisko siebie'} <span>(${items.length})</span></h3><button class="cluster-close" aria-label="Zamknij listę">✕</button></header><p>${message}</p><div class="cluster-event-list">${items.map(item=>`<button class="cluster-event" data-incident-id="${Number(item.id)}"><strong>${esc(item.title)}</strong><span>${esc(item.location)}</span>${item.desc?`<span class="cluster-summary">${esc(item.desc.slice(0,180))}${item.desc.length>180?'…':''}</span>`:''}<small>${esc(root.StrazCore.timeLabel(item))}</small><small>${esc(root.StrazCore.locationLabel(item))}</small><span class="cluster-open">Otwórz szczegóły →</span></button>`).join('')}</div>`;
    dialog.querySelector('.cluster-close').onclick=()=>dialog.close();
    dialog.addEventListener('click',event=>{if(event.target===dialog)dialog.close();});
    dialog.addEventListener('close',()=>{dialog.remove();if(!selecting && previous?.isConnected)previous.focus({preventScroll:true});});
    dialog.querySelectorAll('[data-incident-id]').forEach(button=>button.onclick=()=>{selecting=true;dialog.close();onSelect(Number(button.dataset.incidentId));});
    document.body.append(dialog);dialog.showModal();dialog.querySelector('.cluster-close').focus();
  }
  function initControls(map) {
    const tools = document.getElementById('header-tools');
    const menuButton = document.getElementById('btn-mobile-tools');
    const filters = document.getElementById('category-filters');
    const filterButton = document.getElementById('btn-toggle-filters');
    const setMenu = open => {
      tools.classList.toggle('is-open', open);
      document.getElementById('map-toolbar').classList.toggle('tools-open',open);
      menuButton.setAttribute('aria-expanded', String(open));
    };
    menuButton.addEventListener('click', () => setMenu(!tools.classList.contains('is-open')));
    tools.addEventListener('click', event => { if (event.target.closest('button')) setMenu(false); });
    document.addEventListener('click', event => {
      if (!event.target.closest('#header-tools') && !event.target.closest('#btn-mobile-tools')) setMenu(false);
    });
    document.addEventListener('keydown', event => {
      if (event.key === 'Escape' && tools.classList.contains('is-open')) {
        setMenu(false); menuButton.focus();
      }
    });
    filterButton.addEventListener('click', () => {
      const open = !filters.classList.contains('is-open');
      filters.classList.toggle('is-open', open);
      filterButton.setAttribute('aria-expanded', String(open));
    });
    const viewButtons = [document.getElementById('view-poland'), document.getElementById('view-local')];
    let activeView = '';
    const selectState = preset => {
      activeView = preset;
      viewButtons.forEach(button => button.setAttribute('aria-pressed', String(button.id === 'view-'+preset)));
    };
    function selectView(preset, animate = true) {
      if (!views[preset]) return;
      const toolbarBottom = document.getElementById('map-toolbar').getBoundingClientRect().bottom;
      const bottom = document.getElementById('map-navigation').getBoundingClientRect().height;
      map.flyToBounds(views[preset], {paddingTopLeft:[20, toolbarBottom+12],paddingBottomRight:[20,bottom+20],maxZoom:preset==='local' ? 12 : 7,animate});
      selectState(preset);
      localStorage.setItem('straz_map_view', preset);
    }
    // Choosing a view only moves the camera. All incident/date/category and alarm
    // settings retain their values, including after a new page visit.
    map.on('movestart', () => selectState(''));
    map.on('resize', () => { if(activeView) selectView(activeView,false); });
    viewButtons.forEach(button => button.addEventListener('click', () => selectView(button.id.replace('view-', ''))));
    document.getElementById('btn-reset-poland').addEventListener('click', () => selectView('poland'));
    selectView(localStorage.getItem('straz_map_view') === 'local' ? 'local' : 'poland', false);
    return {activeView:()=>activeView,restoreViewState:selectState};
  }
  const api = {views,markerGroup,initControls,samePosition,showClusterList};
  root.StrazMapUI = api;
  if (typeof module !== 'undefined') module.exports = api;
})(typeof globalThis === 'undefined' ? window : globalThis);
