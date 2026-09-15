// Anonymous in-memory fixtures only; no network or participant snapshots.
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const R = require('./race-logic.js');
const NOW = Date.parse('2026-09-12T16:00:00Z');
const event = {id:'qa',event_date:'2026-09-12',start_at:'2026-09-12T13:20:00Z',start_time:'07:20:00',timezone:'America/Denver',course_status:'active',split_names:['Start','Aid One','Lone Peak Summit','Finish']};
const row = {key:'qa:1',id:1,event_id:'qa',name:'Anonymous QA',status:'ON COURSE',last_split_index:1,source:'ESTIMATED',rank_eligible:true,estimate_basis:'TERRAIN_CHECKPOINT_PILOT',next_checkpoint_at:'2026-09-12T16:20:00Z'};
const show = (r={}, e={}, o={}) => R.nextCheckpointEstimate({...row,...r},{...event,...e},{now:NOW,generatedAt:NOW,delivery:'live_api',...o});

test('next checkpoint uses the authoritative next index and existing Mountain arrival',()=>{
  const result=show();
  assert.equal(result.checkpoint,'Lone Peak Summit');
  assert.equal(result.arrival,'~10:20 AM MT');
  assert.equal(result.remaining,'About 20 min');
  assert.equal(result.kind,'estimate');
  assert.match(result.note,/pilot.*not.*recorded/i);
  assert.equal(row.next_checkpoint_at,'2026-09-12T16:20:00Z');
});
test('countdown ages without sliding the predicted arrival forward',()=>{
  const a=show(),b=show({}, {}, {now:NOW+60000,generatedAt:NOW+60000});
  assert.equal(a.arrival,b.arrival);assert.equal(b.remaining,'About 19 min');
  assert.equal(show({next_checkpoint_at:'2026-09-12T16:00:30Z'}).remaining,'Less than 1 min');
  assert.equal(show({next_checkpoint_at:'2026-09-12T18:10:00Z'}).remaining,'About 2 hr 10 min');
});
test('expired arrival and held estimates never invent another ETA or negative countdown',()=>{
  for(const override of [{next_checkpoint_at:'2026-09-12T15:59:00Z'},{next_checkpoint_at:'2026-09-12T16:00:00Z'},{estimate_overdue:true},{estimate_held:true}]) {
    const result=show(override);assert.equal(result.arrival,'Awaiting checkpoint read');assert.equal(result.remaining,'—');assert.equal(result.kind,'awaiting');
  }
});
test('start-only, missing position and missing or invalid ETA have honest empty states',()=>{
  for(const next_checkpoint_at of [null,undefined,'',false,123,'bad','2026-09-12T10:20:00']) {
    const result=show({next_checkpoint_at});assert.equal(result.arrival,'Not enough timing data');assert.equal(result.remaining,'—');
  }
  assert.equal(show({last_split_index:0}).arrival,'Not enough timing data');
  assert.equal(R.nextCheckpointEstimate(null,event,{now:NOW}).checkpoint,'—');
});
test('invalid next indexes never coerce missing values to Start or skip checkpoints',()=>{
  for(const last_split_index of [null,undefined,'',false,-1,1.5,3,99,Infinity]) {
    const result=show({last_split_index});assert.equal(result.checkpoint,'—');assert.notEqual(result.kind,'estimate');
  }
  assert.equal(show({last_split_index:2}).checkpoint,'Finish');
  assert.equal(show({last_split_index:'1'}).checkpoint,'Lone Peak Summit');
});
test('terminal runners, pre-start, prior-day and unknown phases do not predict arrival',()=>{
  for(const status of ['FINISHED','DROPPED','DNF','DNS','DQ','REGISTERED']) assert.notEqual(show({status}).kind,'estimate');
  assert.equal(show({status:'FINISHED'}).checkpoint,'—');
  assert.equal(show({}, {event_date:'2026-09-13'}).arrival,'Awaiting race start');
  assert.equal(show({}, {start_at:null}).arrival,'Awaiting race start');
  assert.equal(show({}, {event_date:'2026-09-11'}).arrival,'Race closed');
  assert.notEqual(show({}, {event_date:null}).kind,'estimate');
});
test('stale, incomplete, unsupported and mismatched sources fail closed',()=>{
  for(const override of [{rank_eligible:false},{rank_exclusion:'UPSTREAM_STALE'},{upstream_stale:true},{freshness:'STALE'},{event_id:'other'}]) assert.notEqual(show(override).kind,'estimate');
  for(const generatedAt of [null,0,NOW-90001,NOW+5001]) assert.notEqual(show({}, {}, {generatedAt}).kind,'estimate');
  assert.notEqual(show({}, {}, {degraded:true}).kind,'estimate');
  assert.notEqual(show({}, {upstream_stale:true}).kind,'estimate');
});
test('GPS arrival remains a pace prediction and expires with its observation',()=>{
  const r={source:'GPS',freshness:'LIVE',recorded_at:new Date(NOW-5000).toISOString()};
  assert.equal(show(r).kind,'estimate');assert.match(show(r).note,/pace/i);
  assert.notEqual(show({...r,recorded_at:new Date(NOW-91000).toISOString()}).kind,'estimate');
  assert.notEqual(show({...r,freshness:'STALE'}).kind,'estimate');
});
test('snapshot ETA and remaining time are explicitly frozen at capture and expire',()=>{
  const options={delivery:'periodic_snapshot',generatedAt:NOW-240000};
  const a=show({}, {}, options), b=show({}, {}, {...options,now:NOW+60000});
  assert.equal(a.kind,'snapshot');assert.match(a.note,/snapshot.*not live/i);
  assert.equal(a.arrival,'~10:20 AM MT');assert.equal(a.remaining,'About 24 min at snapshot');
  assert.equal(a.remaining,b.remaining);
  assert.notEqual(show({}, {}, {...options,now:NOW+900000}).kind,'snapshot');
});
test('snapshot GPS freshness uses capture time without resurrecting stale observations',()=>{
  const generatedAt=NOW-240000;
  const gps={source:'GPS',freshness:'LIVE',recorded_at:new Date(generatedAt-5000).toISOString()};
  const options={delivery:'periodic_snapshot',generatedAt};
  assert.equal(show(gps,{},options).kind,'snapshot');
  for(const override of [{recorded_at:new Date(generatedAt-90001).toISOString()},{recorded_at:new Date(generatedAt+5001).toISOString()},{freshness:'STALE'},{rank_eligible:false}]) {
    assert.notEqual(show({...gps,...override},{},options).kind,'snapshot');
  }
  assert.equal(show({...gps,next_checkpoint_at:new Date(NOW-1).toISOString()},{},options).kind,'awaiting');
  assert.notEqual(show(gps,{}, {...options,now:generatedAt+900001}).kind,'snapshot');
});
test('snapshot card survives wall-clock GPS aging using the same selected capture row only for ETA',()=>{
  const q=fixture(),generatedAt=NOW-240000;
  const gps={...row,source:'GPS',freshness:'LIVE',recorded_at:new Date(generatedAt-5000).toISOString()};
  Object.assign(q.state,{delivery:'periodic_snapshot',feedGeneratedAt:generatedAt});
  q.state.positions.set(row.key,{...gps});
  q.state.snapshotPositions=[Object.freeze({...gps,key:'qa:other',next_checkpoint_at:null}),Object.freeze({...gps})];
  q.ageAllPositions();q.updateRunnerCard();
  assert.equal(q.state.positions.get(row.key).freshness,'STALE');
  assert.equal(q.state.positions.get(row.key).rank_eligible,false);
  assert.equal(q.el.sourceBadge.textContent,'STALE GPS');
  assert.equal(q.el.runnerNextArrival.textContent,'~10:20 AM MT');
  assert.equal(q.el.runnerNextRemaining.textContent,'About 24 min at snapshot');
  assert.match(q.el.runnerNextNote.textContent,/snapshot.*not live/i);
  assert.equal(q.state.snapshotPositions[1].freshness,'LIVE');
  assert.equal(q.state.snapshotPositions[1].rank_eligible,true);
  assert.equal(q.state.selectedKey,row.key);assert.equal(q.state.manualLock,true);
  assert.equal(q.el.infoContent.scrollTop,120);
  q.state.positions.get(row.key).status='FINISHED';q.updateRunnerCard();
  assert.equal(q.el.runnerNextArrival.textContent,'Finished');
  q.state.selectedKey=null;q.updateRunnerCard();
  assert.equal(q.el.runnerNextArrival.textContent,'—');
});
test('snapshot card cannot resurrect GPS stale or ineligible at capture after aging',()=>{
  for(const override of [{freshness:'STALE'},{rank_eligible:false},{recorded_at:new Date(NOW-240000-90001).toISOString()}]) {
    const q=fixture();
    const gps={...row,source:'GPS',freshness:'LIVE',recorded_at:new Date(NOW-245000).toISOString(),...override};
    Object.assign(q.state,{delivery:'periodic_snapshot',feedGeneratedAt:NOW-240000});
    q.state.positions.set(row.key,{...gps});q.state.snapshotPositions=[Object.freeze({...gps})];
    q.ageAllPositions();q.updateRunnerCard();
    assert.equal(q.el.runnerNextRemaining.textContent,'—');
    assert.doesNotMatch(q.el.runnerNextNote.textContent,/snapshot pace/i);
  }
});
test('Mountain formatting stays fixed regardless of the event display timezone',()=>{
  assert.equal(show({}, {timezone:'UTC'}).arrival,'~10:20 AM MT');
});

function fixture() {
  const source=fs.readFileSync('app.js','utf8');
  const modified=source.replace('  window.addEventListener("DOMContentLoaded", init);','  window.qa={state,el,updateRunnerCard,renderNextCheckpoint,ageAllPositions}; renderElevation=()=>{}; renderCheckpointHistory=()=>{};');
  assert.notEqual(modified,source,'Startup replacement must match');
  class Clock extends Date { static now(){return NOW;} }
  const window={location:{hostname:'localhost',search:''}};
  const context=vm.createContext({window,URLSearchParams,Map,Set,WeakMap,Date:Clock,Intl,console});
  vm.runInContext(fs.readFileSync('race-logic.js','utf8'),context);
  window.RutRules=context.RutRules;
  vm.runInContext(modified,context);
  const q=window.qa;
  for(const match of fs.readFileSync('index.html','utf8').matchAll(/\bid="([^"]+)"/g)) q.el[match[1]]={textContent:'',innerHTML:'',hidden:false,scrollTop:120,dataset:{},classList:{add(){},toggle(){}},querySelector(){return {textContent:''};}};
  Object.assign(q.state,{selectedKey:'qa:1',finishEventId:'qa',delivery:'live_api',feedGeneratedAt:NOW,manualLock:true});
  q.state.eventData.set('qa',structuredClone(event));q.state.runners=[structuredClone(row)];q.state.positions.set('qa:1',structuredClone(row));return q;
}
test('selected runner card renders next checkpoint in either view without changing selection or scroll',()=>{
  for(const viewMode of ['map','elevation']) {
    const q=fixture();q.state.viewMode=viewMode;q.updateRunnerCard();
    assert.equal(q.el.runnerNextCheckpoint.textContent,'Lone Peak Summit');assert.equal(q.el.runnerNextArrival.textContent,'~10:20 AM MT');assert.equal(q.el.runnerNextRemaining.textContent,'About 20 min');
    assert.equal(q.state.manualLock,true);assert.equal(q.state.selectedKey,'qa:1');assert.equal(q.state.viewMode,viewMode);assert.equal(q.el.infoContent.scrollTop,120);
  }
});
test('selection change, no-position and hidden Info updates cannot retain old ETA',()=>{
  const q=fixture();q.updateRunnerCard();q.el.infoPane.hidden=true;
  const next={...row,key:'qa:2',id:2,last_split_index:2,next_checkpoint_at:undefined};
  q.state.selectedKey=next.key;q.state.runners.push(next);q.updateRunnerCard();
  assert.equal(q.el.runnerNextCheckpoint.textContent,'Finish');assert.equal(q.el.runnerNextArrival.textContent,'Not enough timing data');
  assert.equal(q.el.runnerNextRemaining.textContent,'—');
  q.state.selectedKey=null;q.updateRunnerCard();assert.equal(q.el.runnerNextCheckpoint.textContent,'—');assert.equal(q.el.runnerNextArrival.textContent,'—');
});
test('clock refresh rerenders next-checkpoint time through the existing card path',()=>{
  const source=fs.readFileSync('app.js','utf8');
  assert.match(source,/function updateClock\(\)[\s\S]*?updateRunnerCard\(\)/);
});
