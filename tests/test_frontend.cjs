const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const core = require('../app-core.js');
const area = require('../local-alert-area.json');
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
