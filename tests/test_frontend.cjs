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
assert.equal(core.weatherActive({obowiazuje_do:'2026-07-01 10:00:00'},Date.parse('2026-07-01T08:01:00Z')),false);
assert.equal(core.weatherActive({obowiazuje_do:'2026-11-07 10:00:00'},Date.parse('2026-11-07T09:01:00Z')),false);
const html=fs.readFileSync(require('node:path').join(__dirname,'../index.html'),'utf8');
for(const match of html.matchAll(/<script\b[^>]*>([\s\S]*?)<\/script>/gi)) new vm.Script(match[1]);
const ids=[...html.matchAll(/\bid="([^"]+)"/g)].map(x=>x[1]);
assert.equal(ids.length,new Set(ids).size,'Duplicate HTML IDs');
assert.ok(!html.includes('sendNtfyPushFromBrowser'),'Manual submissions must not bypass the central notification gate');
console.log('Frontend regression checks passed');
