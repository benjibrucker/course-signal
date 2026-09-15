// In-memory integration fixtures; no network or participant records.
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const rules = require('./race-logic.js');
function fixture(delivery, age) {
 const source=fs.readFileSync('app.js','utf8');
 const modified=source.replace('  window.addEventListener("DOMContentLoaded", init);','  window.qa = {state,el,renderFinishWatch,finishWatchRows,syncRaceSelection,visibleEventIds};');
 assert.ok(modified!==source, 'Expected startup hook must be present');
 const window={location:{hostname:'benjibrucker.github.io',search:''},RUT_CONFIG:{apiBase:'https://example.invalid'},RutRules:rules};
 vm.runInNewContext(modified,{window,URLSearchParams,Map,Set,Date,Intl,console});
 const {state,el}=window.qa;
 for (const name of ['finishRace','finishCount','finishStatus','finishExcluded','finishList']) el[name]={dataset:{},value:'',innerHTML:'',textContent:'',querySelectorAll:()=>[]};
 const row={key:'race:1',id:1,event_id:'race',name:'QA fixture',status:'ON COURSE',source:'ESTIMATED',rank_eligible:true,remaining_m:50};
 const day=new Date().toISOString().slice(0,10);
 state.eventData.set('race',{id:'race',label:'QA race',event_date:day,start_time:'00:00:00',start_at:day+'T00:00:00Z',timezone:'UTC',course_status:'active'});state.finishEventId='race';state.positions.set(row.key,row);state.snapshotPositions=[row];state.delivery=delivery;state.feedGeneratedAt=Date.now()-age*1000;
 window.qa.renderFinishWatch();
 return window.qa;
}
test('pilot list labels and metadata changes remain explicit',()=>{
 const q=fixture('live_api',0);
 q.state.eventData.get('race').estimator={model:'terrain-pilot-v1',terrain_ready:true};
 const row=q.state.positions.get('race:1');
 Object.assign(row,{estimate_basis:'TERRAIN_CHECKPOINT_PILOT',eta_basis:'TERRAIN_CHECKPOINT_PILOT',pace_basis:'RECENT_SEGMENTS',pace_segments_used:3,eta_at:new Date(Date.now()+600000).toISOString()});
 q.renderFinishWatch();
 assert.match(q.el.finishExcluded.textContent,/Terrain pace pilot/);
 assert.match(q.el.finishList.innerHTML,/Terrain estimate · pilot/);
 assert.match(q.el.finishList.innerHTML,/Pilot arrival/);
 row.estimate_basis='CHECKPOINT_PACE_CHIP';row.eta_basis='CHECKPOINT_PACE_CHIP';
 q.renderFinishWatch();
 assert.doesNotMatch(q.el.finishList.innerHTML,/Terrain estimate · pilot|Pilot arrival/);
 q.state.eventData.get('race').estimator.terrain_ready=false;
 q.renderFinishWatch();assert.match(q.el.finishExcluded.textContent,/fallback · terrain unavailable/);
});
test('configured live API does not relabel a fallback snapshot live',()=>{
 for (const age of [30,120]) {
  const q=fixture('periodic_snapshot',age);
  assert.equal(q.finishWatchRows().length,1);
  assert.match(q.el.finishStatus.textContent,/SNAPSHOT ORDER · NOT LIVE/);
  assert.doesNotMatch(q.el.finishStatus.textContent,/checks every 15s/);
 }
});
test('actual live delivery retains the strict 90-second ranking limit',()=>{
 const q=fixture('live_api',120);
 assert.equal(q.finishWatchRows().length,0);
 assert.match(q.el.finishStatus.textContent,/paused/);
});
test('automatic race-day selection rolls forward without changing a manual course',()=>{
 const q=fixture('live_api',0), now=Date.parse('2026-09-12T07:30:00-06:00');
 const friday={id:'fri',label:'21K',event_date:'2026-09-11',start_time:'10:00:00',start_at:'2026-09-11T10:00:00-06:00',course_status:'active'};
 const saturday={id:'sat',label:'28K',event_date:'2026-09-12',start_time:'07:20:00',start_at:'2026-09-12T07:20:00-06:00',course_status:'active'};
 q.state.eventData=new Map([[friday.id,friday],[saturday.id,saturday]]);
 q.state.finishEventId='fri';q.state.filter='active';q.state.manualLock=false;q.state.selectedKey='old-runner';
 assert.equal(q.syncRaceSelection(now),true);assert.equal(q.state.finishEventId,'sat');assert.equal(q.state.selectedKey,null);
 q.state.finishEventId='fri';q.state.filter='fri';
 assert.equal(q.syncRaceSelection(now),false);assert.equal(q.state.finishEventId,'fri');
 q.state.filter='active';q.state.manualLock=true;
 assert.equal(q.syncRaceSelection(now),false);assert.equal(q.state.finishEventId,'fri');
 assert.deepEqual([...q.visibleEventIds()],['fri']);
});
test('missing race metadata fails closed even if a row says eligible',()=>{
 const q=fixture('live_api',0);q.state.eventData.set('race',{id:'race',label:'QA'});
 q.renderFinishWatch();assert.equal(q.finishWatchRows().length,0);
 assert.match(q.el.finishStatus.textContent,/verified race schedule/);
});
test('upcoming and closed races never rank stale on-course records',()=>{
 for(const date of ['2020-01-01','2099-01-01']) {
  const q=fixture('live_api',0);
  q.state.eventData.set('race',{id:'race',label:'QA',event_date:date,start_time:'07:20:00',start_at:null,course_status:'active'});
  q.renderFinishWatch();assert.equal(q.finishWatchRows().length,0);
  assert.match(q.el.finishStatus.textContent,/Upcoming|Race closed/);
 }
});
