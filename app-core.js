/* Pure display and geography rules; also exercised in Node regression tests. */
(function (root) {
  'use strict';
  const normalize = value => String(value ?? '').toLowerCase().replaceAll('ł', 'l')
    .normalize('NFKD').replace(/[\u0300-\u036f]/g, '').replace(/\s+/g, ' ').trim();
  const escapeHtml = value => String(value ?? '').replace(/[&<>"']/g,
    c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const safeUrl = value => {
    try { const url = new URL(value); return ['https:', 'http:'].includes(url.protocol) ? url.href : ''; }
    catch { return ''; }
  };
  const point = (lat, lng) => lat !== null && lat !== undefined && lat !== '' &&
    lng !== null && lng !== undefined && lng !== '' && Number.isFinite(Number(lat)) &&
    Number.isFinite(Number(lng)) && Number(lat) >= 49 && Number(lat) <= 55.1 &&
    Number(lng) >= 14 && Number(lng) <= 24.2;
  const warsawDay = (date = new Date()) => {
    const parts = new Intl.DateTimeFormat('en-CA', {timeZone:'Europe/Warsaw',year:'numeric',month:'2-digit',day:'2-digit'}).formatToParts(date);
    const get = key => parts.find(p => p.type === key).value;
    return `${get('year')}-${get('month')}-${get('day')}`;
  };
  const dateTime = value => value && Number.isFinite(new Date(value).getTime()) ? new Intl.DateTimeFormat('pl-PL', {
    timeZone:'Europe/Warsaw',dateStyle:'short',timeStyle:'short'
  }).format(new Date(value)) : 'brak danych';
  function localIncident(item, area) {
    if (!area || !point(item.lat, item.lng)) return false;
    const lat = Number(item.lat), lng = Number(item.lng);
    const text = normalize(`${item.title} ${item.location}`), loc = normalize(item.location);
    const road = /\b(?:a[12]|dk\s?14)\b|lodz polnoc/.test(text);
    const escapeRe = x => x.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
    if (!(road && loc.includes('lodz polnoc')) && area.excluded_cities.some(city =>
      new RegExp('^' + escapeRe(normalize(city)) + '(?:\\b|,)').test(loc))) return false;
    const nearby = lat >= 51.85 && lng >= 19.53 && area.centres.some(c => Math.hypot(lat-c[0],lng-c[1]) < .09);
    const [south,west,north,east] = area.name_check_bounds;
    const named = area.towns.some(town => new RegExp('\\b' + escapeRe(normalize(town)) + '\\b').test(text));
    return nearby || (lat >= south && lat <= north && lng >= west && lng <= east && named);
  }
  function mapRow(r) {
    return {id:Number(r.id),externalId:r.external_id || '',title:r.title,category:r.category || 'hazard',
      status:r.status || 'Nieustalony',location:r.location,region:r.region || 'Polska',
      lat:r.lat === null ? null : Number(r.lat),lng:r.lng === null ? null : Number(r.lng),
      dateStr:r.event_date || r.date_str,time:r.event_time || '',units:r.units || 'Brak danych',
      desc:r.description,sourceUrl:safeUrl(r.source_url),sources:Array.isArray(r.sources) ? r.sources : [],
      detectedAt:r.created_at,publishedAt:r.published_at,eventDate:r.event_date,eventTime:r.event_time,
      statusObservedAt:r.status_observed_at,accuracy:r.location_accuracy || 'legacy',localAlert:r.local_alert,
      alertEligible:r.alert_eligible === true,mergedInto:r.merged_into || null,editRevision:Number(r.edit_revision || 0)};
  }
  function timeLabel(item) {
    if (item.eventDate) return `Zdarzenie: ${item.eventDate}${item.eventTime ? ' • ' + item.eventTime : ' • godzina nieznana'}`;
    return `Znaleziono: ${dateTime(item.detectedAt)}`;
  }
  function detailTimes(item) {
    const day = /^(\d{4})-(\d{2})-(\d{2})$/.exec(item.eventDate || '');
    const hour = /^(?:[01]\d|2[0-3]):[0-5]\d$/.test(item.eventTime || '') ? item.eventTime : null;
    const rows = [{label:'Czas zdarzenia',value:day ? `${day[3]}.${day[2]}.${day[1]} · ${hour || 'godzina nieustalona'}` : 'Data i godzina nieustalone'}];
    if (item.publishedAt) rows.push({label:'Publikacja źródła',value:dateTime(item.publishedAt)});
    if (item.detectedAt) rows.push({label:'Znaleziono',value:dateTime(item.detectedAt)});
    return rows;
  }
  function statusLabel(item, now = Date.now()) {
    if (item.accuracy === 'legacy') return 'Status dawnego wpisu: ' + item.status;
    if (!item.statusObservedAt || item.status === 'Nieustalony') return 'Aktualny status nieznany';
    if (item.status !== 'Zakończone' && now-new Date(item.statusObservedAt).getTime() > 4*3600000)
      return 'Brak aktualnego potwierdzenia';
    return `${item.status} — wg informacji z ${dateTime(item.statusObservedAt)}`;
  }
  function locationLabel(item) {
    if (!point(item.lat,item.lng)) return 'Lokalizacja nieustalona — wpis dostępny na liście';
    if (item.accuracy === 'manual') return 'Punkt wskazany ręcznie';
    if (item.accuracy === 'legacy') return 'Dawna lokalizacja — dokładność niezweryfikowana';
    return 'Położenie przybliżone — okolica miejscowości';
  }
  function weatherActive(w, now = Date.now()) {
    // API returns Polish wall clock; use Europe/Warsaw offset including summer time.
    const parse = text => {
      if (!text) return null;
      const base = new Date(String(text).replace(' ', 'T')+'Z');
      if (!Number.isFinite(base.getTime())) return null;
      const offset = new Intl.DateTimeFormat('en-US',{timeZone:'Europe/Warsaw',timeZoneName:'longOffset'}).formatToParts(base).find(p=>p.type==='timeZoneName').value.replace('GMT','');
      return new Date(String(text).replace(' ', 'T')+offset);
    };
    const end = parse(w.obowiazuje_do);
    return !end || end.getTime() >= now;
  }
  const api = {normalize,escapeHtml,safeUrl,point,warsawDay,dateTime,localIncident,mapRow,timeLabel,detailTimes,statusLabel,locationLabel,weatherActive};
  root.StrazCore = api;
  if (typeof module !== 'undefined') module.exports = api;
})(typeof globalThis === 'undefined' ? window : globalThis);
