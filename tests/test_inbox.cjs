const assert=require('node:assert/strict');
const core=require('../app-core.js');
const inbox=require('../incident-inbox.js');
function storage(initial={}) {
  const values=new Map(Object.entries(initial));
  return {values,getItem:key=>values.get(key)||null,setItem:(key,value)=>values.set(key,value)};
}
function client(rows,{afterQuery=()=>{},failPage=0}={}) {
  let pages=0;
  return {from(name) {
    assert.equal(name,'incidents');
    let fields,ascending=false,limit=500,after=-1,before=Infinity,excludeMerged=false;
    return {
      select(value){fields=value;return this;},
      is(key,value){assert.equal(key,'merged_into');assert.equal(value,null);excludeMerged=true;return this;},
      gt(key,value){assert.equal(key,'id');after=value;return this;},
      lte(key,value){assert.equal(key,'id');before=value;return this;},
      order(key,options){assert.equal(key,'id');ascending=options.ascending;return this;},
      limit(value){limit=value;return this;},
      then(resolve,reject) {
        if(fields==='*')pages++;
        if(pages===failPage && fields==='*')return Promise.resolve({data:null,error:{message:'offline'}}).then(resolve,reject);
        assert.ok(excludeMerged,'Archived aliases must never enter the inbox');
        const data=rows.filter(row=>!row.merged_into && row.id>after && row.id<=before)
          .sort((a,b)=>ascending ? a.id-b.id : b.id-a.id).slice(0,limit).map(row=>fields==='id' ? {id:row.id} : {...row});
        afterQuery({fields,pages,rows});
        return Promise.resolve({data,error:null}).then(resolve,reject);
      }
    };
  }};
}
module.exports=(async()=>{
  const local=storage({straz_alert_pref:'local'}), tracker=inbox.createReadTracker(local);
  const first=await inbox.fetchSnapshot(client([{id:1},{id:4}]),tracker.through);
  assert.deepEqual(first,{head:4,rows:[]},'First use must not mark the archive new');
  tracker.initialize(first.head);
  assert.equal(tracker.isNew({id:4}),false);
  assert.equal(tracker.isNew({id:5}),true);
  assert.equal(tracker.isNew({id:5,mergedInto:4}),false);
  assert.equal(tracker.isNew({id:5,merged_into:4}),false);
  tracker.initialize(9);
  assert.equal(tracker.through,4,'Opening/reinitializing must never acknowledge unseen entries');
  const reopened=inbox.createReadTracker(local);
  assert.equal(reopened.through,4,'Review state must survive a reload');
  assert.equal(reopened.isNew({id:4,sources:[{url:'https://example.org/another-report'}]}),false);
  const rows=[1,2,3,4,5,6,7].map(id=>({id}));
  rows.push({id:8,merged_into:7});
  const snapshot=await inbox.fetchSnapshot(client(rows,{afterQuery({fields,pages,rows}) {
    if(fields==='id') rows.push({id:9}); // Arrives after the snapshot boundary.
    if(fields==='*' && pages===1) rows.splice(rows.findIndex(row=>row.id===1),1);
  }}),0,3);
  assert.deepEqual(snapshot.rows.map(row=>row.id),[1,2,3,4,5,6,7],
    'Keyset pages must survive deletion of an earlier row and exclude later arrivals');
  reopened.markReviewed(snapshot.head);
  assert.equal(reopened.through,7);
  assert.equal(reopened.isNew({id:9}),true,'Acknowledge must not consume a concurrent later arrival');
  reopened.markReviewed(3);
  assert.equal(reopened.through,7,'An older snapshot must not move the watermark backwards');
  assert.equal(local.getItem('straz_alert_pref'),'local','Reviewing must not alter alarm preferences');
  await assert.rejects(()=>inbox.fetchSnapshot(client([{id:1},{id:2},{id:3}],{failPage:2}),0,2));
  assert.equal(reopened.through,7,'A failed fetch cannot acknowledge anything');
  const broken=inbox.createReadTracker({getItem(){throw Error();},setItem(){throw Error();}});
  broken.initialize(3);assert.equal(broken.persistent,false);assert.equal(broken.isNew({id:4}),true);
  const malformed=inbox.createReadTracker(storage({[inbox.STORAGE_KEY]:'invalid json'}));
  assert.equal(malformed.through,null);malformed.initialize(8);assert.equal(malformed.persistent,true);

  const examples=[
    {id:1,dateStr:'2026-11-07',category:'fire',title:'Pożar domu',location:'Stryków',lat:51.9,lng:19.6},
    {id:2,dateStr:'2026-11-07',category:'accident',title:'Wypadek',location:'Kraków',lat:50.06,lng:19.94},
    {id:3,dateStr:'2026-11-06',category:'fire',title:'Pożar',lat:51.9,lng:19.6},
    {id:4,dateStr:'2026-11-07',category:'fire',title:'Pożar',lat:null,lng:null},
    {id:5,dateStr:'2026-11-07',category:'fire',title:'Pożar',lat:51.9,lng:19.6,mergedInto:1}
  ];
  const bounds={south:51.8,north:52,west:19.5,east:19.7};
  assert.deepEqual(inbox.filterRows(examples,{date:'2026-11-07',bounds}).map(x=>x.id),[1]);
  assert.deepEqual(inbox.filterRows(examples,{date:'2026-11-07'}).map(x=>x.id),[1,2,4]);
  assert.deepEqual(inbox.filterRows(examples,{date:'2026-11-07',category:'fire',search:'Stryków',bounds}).map(x=>x.id),[1]);
  assert.deepEqual(inbox.filterRows(examples,{bounds:{south:49,north:51,west:19,east:21}}).map(x=>x.id),[2]);
  assert.equal(inbox.filterRows(examples,{bounds:{empty:true}}).length,0);
  assert.equal(core.localIncident(examples[1],require('../local-alert-area.json')),false,
    'Seeing Kraków on the map must not make it eligible for local alerts');
  console.log('Inbox persistence, snapshot pagination and viewport checks passed');
})();
