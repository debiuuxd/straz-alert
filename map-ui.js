/* View and marker presentation; no database writes or notification settings. */
(function (root) {
  'use strict';
  const views = {
    poland: [[49, 14], [55.1, 24.2]],
    local: [[51.79, 19.43], [52.05, 19.89]]
  };
  function markerGroup(leaflet) {
    if (!leaflet.markerClusterGroup) return leaflet.layerGroup();
    return leaflet.markerClusterGroup({
      maxClusterRadius: zoom => zoom < 9 ? 52 : 36,
      showCoverageOnHover: false, spiderfyDistanceMultiplier: 1.5,
      animate: false,
      spiderLegPolylineOptions: {weight: 2, color: '#94a3b8', opacity: .8},
      iconCreateFunction(cluster) {
        const count = cluster.getChildCount();
        const noun = count%10>=2 && count%10<=4 && !(count%100>=12 && count%100<=14) ? 'zdarzenia' : 'zdarzeń';
        cluster.options.title = `${count} ${noun} — dotknij, aby rozwinąć`;
        cluster.options.alt = cluster.options.title;
        return leaflet.divIcon({className:'event-cluster',html:`<span>${count}</span>`,iconSize:[44,44]});
      }
    });
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
  const api = {views,markerGroup,initControls};
  root.StrazMapUI = api;
  if (typeof module !== 'undefined') module.exports = api;
})(typeof globalThis === 'undefined' ? window : globalThis);
