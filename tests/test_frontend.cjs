const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const core = require('../app-core.js');
const area = require('../local-alert-area.json');
require('./test_owner.cjs');
for(const example of require('./local-cases.json')) {
  assert.equal(core.localIncident(example,area),example.expected,example.location);
}
assert.equal(core.point(null,null),false);
assert.equal(core.safeUrl('javascript:alert(1)'), '');
assert.equal(core.escapeHtml('<img onerror="alert(1)">'), '&lt;img onerror=&quot;alert(1)&quot;&gt;');
assert.equal(core.warsawDay(new Date('2026-11-06T23:30:00Z')),'2026-11-07');
assert.equal(core.warsawDay(new Date('2026-07-06T22:30:00Z')),'2026-07-07');
const row=core.mapRow({id:1,lat:null,lng:null,status:'Trwa akcja',location_accuracy:'unknown',created_at:'2026-11-07T12:00:00Z'});
assert.equal(row.lat,null);
assert.equal(core.statusLabel(row),'Aktualny status nieznany');
assert.match(core.timeLabel(row),/^Znaleziono:/);
const unknownTimes=core.detailTimes({...row,publishedAt:'2026-11-07T11:30:00Z'});
assert.equal(unknownTimes[0].value,'Data i godzina nieustalone','Publication/detection must not invent an event time');
assert.equal(unknownTimes.filter(x=>x.label==='Znaleziono').length,1);
assert.equal(unknownTimes.find(x=>x.label==='Publikacja źródła').value,'7.11.2026, 12:30');
assert.equal(core.detailTimes({eventDate:'2026-11-07',eventTime:'09:15'})[0].value,'07.11.2026 · 09:15');
assert.equal(core.detailTimes({eventDate:'2026-11-07'})[0].value,'07.11.2026 · godzina nieustalona');
assert.equal(core.weatherActive({obowiazuje_do:'2026-07-01 10:00:00'},Date.parse('2026-07-01T08:01:00Z')),false);
assert.equal(core.weatherActive({obowiazuje_do:'2026-11-07 10:00:00'},Date.parse('2026-11-07T09:01:00Z')),false);
const html=fs.readFileSync(require('node:path').join(__dirname,'../index.html'),'utf8');
for(const match of html.matchAll(/<script\b[^>]*>([\s\S]*?)<\/script>/gi)) new vm.Script(match[1]);
const ids=[...html.matchAll(/\bid="([^"]+)"/g)].map(x=>x[1]);
assert.equal(ids.length,new Set(ids).size,'Duplicate HTML IDs');
assert.ok(!html.includes('sendNtfyPushFromBrowser'),'Manual submissions must not bypass the central notification gate');
new vm.Script(fs.readFileSync(require('node:path').join(__dirname,'../map-ui.js'),'utf8'));
// Exercise the real camera controls: switching views must preserve alarm and
// date/category preferences. No network or database is involved in these controls.
const ui=require('../map-ui.js');
// A coincident group is a list, never fabricated marker positions. A spatial
// group zooms in, and a still-overlapping group at maximum zoom also uses a list.
const pin=(lat,lng)=>({getLatLng:()=>({lat,lng})});
let clusterOptions,clusterClick,zoom=7,selectedGroups=[],camera=[];
const groupMap={getZoom:()=>zoom,getMaxZoom:()=>19,getBoundsZoom:()=>14,
  fitBounds:(bounds,options)=>{camera.push(options);zoom=options.maxZoom;},setView:(_point,next)=>{zoom=next;}};
const fakeLeaflet={markerClusterGroup:options=>{clusterOptions=options;return {on:(_events,fn)=>{clusterClick=fn;}};},
  point:value=>({add:other=>[value[0]+other[0],value[1]+other[1]]})};
ui.markerGroup(fakeLeaflet,{map:groupMap,onSelectGroup:(markers,context)=>selectedGroups.push({markers,context})});
const bounds={getCenter:()=>({lat:51.9,lng:19.6})};
const shared=[pin(51.9,19.6),pin(51.9,19.6)];
clusterClick({type:'clusterclick',layer:{getAllChildMarkers:()=>shared,getBounds:()=>bounds}});
assert.equal(selectedGroups.length,1);assert.equal(selectedGroups[0].context.samePoint,true);assert.equal(camera.length,0);
const separate=[pin(51.9,19.6),pin(51.905,19.6)];
clusterClick({type:'clusterclick',layer:{getAllChildMarkers:()=>separate,getBounds:()=>bounds}});
assert.equal(zoom,14);assert.equal(selectedGroups.length,1);
zoom=19;
clusterClick({type:'clusterclick',layer:{getAllChildMarkers:()=>separate,getBounds:()=>bounds}});
assert.equal(selectedGroups.at(-1).context.samePoint,false);
assert.equal(clusterOptions.spiderfyOnMaxZoom,false);
assert.equal(ui.samePosition([pin(51.9,19.6),pin(51.9,19.6001)]),false);
class Control {
  constructor(id){this.id=id;this.attributes={};this.listeners={};this.classes=new Set();
    this.classList={contains:x=>this.classes.has(x),toggle:(x,on)=>on ? this.classes.add(x) : this.classes.delete(x)};}
  addEventListener(name,fn){this.listeners[name]=fn;}
  setAttribute(key,value){this.attributes[key]=value;}
  getBoundingClientRect(){return {bottom:180,height:64};}
}
const controlIds=['header-tools','btn-mobile-tools','category-filters','btn-toggle-filters','view-poland','view-local','map-toolbar','map-navigation','btn-reset-poland'];
const controls=Object.fromEntries(controlIds.map(id=>[id,new Control(id)]));
global.document={getElementById:id=>controls[id],addEventListener(){}};
const preferences=new Map([['straz_alert_pref','local'],['selectedDate','2026-11-06'],['activeCategory','fire']]);
global.localStorage={getItem:key=>preferences.get(key),setItem:(key,value)=>preferences.set(key,value)};
const cameraMoves=[];
const fakeMap={on(){},flyToBounds:(bounds,options)=>cameraMoves.push({bounds,options})};
ui.initControls(fakeMap);
controls['view-local'].listeners.click();
assert.deepEqual(cameraMoves.at(-1).bounds,ui.views.local);
controls['view-poland'].listeners.click();
assert.deepEqual(cameraMoves.at(-1).bounds,ui.views.poland);
assert.equal(preferences.get('straz_alert_pref'),'local');
assert.equal(preferences.get('selectedDate'),'2026-11-06');
assert.equal(preferences.get('activeCategory'),'fire');
assert.deepEqual([...preferences.keys()],['straz_alert_pref','selectedDate','activeCategory','straz_map_view']);
assert.deepEqual(cameraMoves[0].options.paddingTopLeft,[20,192]);
delete global.document;delete global.localStorage;
require('./test_inbox.cjs').then(()=>console.log('Frontend regression checks passed')).catch(error=>{console.error(error);process.exitCode=1;});
