// Anonymous in-memory fixtures; deterministic clocks; no participant files/network.
const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
const R=require('./race-logic.js'),NOW=Date.parse('2026-09-12T16:00:00Z');
const event={id:'qa',label:'QA',event_date:'2026-09-12',start_at:'2026-09-12T12:00:00Z',timezone:'America/Denver',course_status:'active',split_names:['Start','Missing aid','Last aid','Summit','Finish'],course:{split_points:[{name:'Finish',lat:45,lng:-111}]}};
const passage=(split_index,passed_at,elapsed_seconds)=>({split_index,passed_at,elapsed_seconds});
const runner=()=>({key:'qa:1',event_id:'qa',name:'Anonymous <old>',bib:1,status:'ON COURSE',source:'ESTIMATED',last_split_index:2,rank_eligible:true,eta_basis:'TERRAIN_CHECKPOINT_PILOT',checkpoint_forecast_basis:'TERRAIN_CHECKPOINT_PILOT',next_checkpoint_at:'2026-09-12T16:20:00Z',eta_at:'2026-09-12T17:00:00Z',checkpoint_passages_status:'partial',checkpoint_passages:[passage(0,'2026-09-12T12:00:00Z',0),passage(2,'2026-09-12T15:00:03Z',10803)],checkpoint_forecasts:[{split_index:3,estimated_at:'2026-09-12T16:20:00Z'},{split_index:4,estimated_at:'2026-09-12T17:00:00Z'}]});
const show=(r={},e={},o={})=>R.checkpointTimeline({...runner(),...r},{...event,...e},{now:NOW,generatedAt:NOW,...o});
test('timeline covers original indexes, actual Start, missing past, next and all later forecasts',()=>{
 const t=show();assert.equal(t.rows.length,5);assert.deepEqual(t.rows.map(r=>r.kind),['recorded','missing','recorded','forecast','forecast']);assert.equal(t.currentSegment,'Last aid → Summit');assert.equal(t.rows[3].isNext,true);assert.match(t.rows[3].arrival,/^~10:20 AM MT/);assert.equal(t.rows[4].qualifier,'Less certain');assert.equal(t.next.remaining,'About 20 min');assert.match(t.source,/partial/i);
});
test('actual reads win and crossing an ETA never turns a forecast into a passage or advances leg',()=>{
 const t=show({next_checkpoint_at:'2026-09-12T16:00:00Z'});assert.equal(t.next.kind,'awaiting');assert.equal(t.currentSegment,'Last aid → Summit');assert.equal(t.rows.filter(r=>r.kind==='forecast').length,0);assert.equal(t.rows[3].kind,'awaiting');assert.equal(t.rows[4].kind,'unavailable');
});
test('older API retains next card but does not synthesize a timeline schedule',()=>{
 for(const checkpoint_forecasts of [undefined,[]]){const t=show({checkpoint_forecasts});assert.equal(t.next.kind,'estimate');assert.equal(t.rows.filter(r=>r.kind==='forecast').length,0);assert.match(t.source,/unavailable/i);}
});
test('all forecast civil dates are validated, including the first ETA',()=>{
 for(const date of ['2026-02-30T16:20:00Z','2026-09-12T24:00:00Z','2026-09-12T16:20:00','bad']) {
  assert.notEqual(show({next_checkpoint_at:date}).next.kind,'estimate');
  const r=runner();r.checkpoint_forecasts[1].estimated_at=date;assert.equal(show(r).forecastAvailable,false);
 }
});
test('malformed schedules fail closed: duplicate, order, gaps, extent, type, clock, endpoint, basis',()=>{
 const good=runner().checkpoint_forecasts;
 const bad=[null,{},[good[0]],[good[1],good[0]],[good[0],good[0]],[{...good[0],split_index:'3'},good[1]],[{...good[0],split_index:2},good[1]],[good[0],{...good[1],split_index:99}],[good[0],{...good[1],estimated_at:good[0].estimated_at}],[{...good[0],estimated_at:'2026-09-12T16:21:00Z'},good[1]],[good[0],{...good[1],estimated_at:'2026-09-12T17:01:00Z'}]];
 for(const checkpoint_forecasts of bad)assert.equal(show({checkpoint_forecasts}).forecastAvailable,false,JSON.stringify(checkpoint_forecasts));
 for(const r of [{checkpoint_forecast_basis:'UNKNOWN'},{checkpoint_forecast_basis:'CHECKPOINT_PACE_CHIP'},{last_split_index:'2'},{last_split_index:1},{checkpoint_passages:[]}])assert.equal(show(r).forecastAvailable,false);
});
test('stale, held, overdue, terminal, nonstart, feed failure and closed context suppress every forecast',()=>{
 for(const r of [{estimate_held:true},{estimate_overdue:true},{upstream_stale:true},{checkpoint_passages_stale:true},{status:'FINISHED'},{status:'DNF'},{status:'DNS'},{status:'REGISTERED'},{last_split_index:0}])assert.equal(show(r).forecastAvailable,false);
 for(const o of [{degraded:true},{generatedAt:NOW-90001}])assert.equal(show({}, {},o).forecastAvailable,false);
 assert.equal(show({}, {event_date:'2026-09-11'}).forecastAvailable,false);
});
test('future recorded evidence inconsistent with last index cannot validate a forecast schedule',()=>{
 const r=runner();r.checkpoint_passages.push(passage(3,'2026-09-12T15:30:00Z',12600));const t=show(r);assert.equal(t.forecastAvailable,false);assert.equal(t.rows[3].kind,'recorded');
});
test('snapshot uses immutable capture GPS freshness, frozen remaining and fifteen-minute expiry',()=>{
 const stamp=NOW-240000,r={source:'GPS',freshness:'LIVE',recorded_at:new Date(stamp-5000).toISOString()},o={delivery:'periodic_snapshot',generatedAt:stamp};
 const a=show(r,{},o),b=show(r,{},{...o,now:NOW+60000});assert.equal(a.forecastAvailable,true);assert.equal(a.next.remaining,b.next.remaining);assert.match(a.source,/snapshot.*not live/i);assert.equal(show(r,{},{...o,now:stamp+900001}).forecastAvailable,false);assert.equal(show({...r,freshness:'STALE'},{},o).forecastAvailable,false);
});
function fixture(){
 class Clock extends Date{static now(){return NOW;}}
 const window={RutRules:R,location:{hostname:'localhost',search:''}},source=fs.readFileSync('app.js','utf8').replace('  window.addEventListener("DOMContentLoaded", init);','  window.qa={state,el,renderNextCheckpoint,renderCheckpointHistory,ageAllPositions,openDetails};');
 vm.runInNewContext(source,{window,URLSearchParams,Map,Set,WeakMap,Date:Clock,Intl,console});const q=window.qa;
 for(const m of fs.readFileSync('index.html','utf8').matchAll(/\bid="([^"]+)"/g))q.el[m[1]]={textContent:'',innerHTML:'',dataset:{},hidden:false,open:false,showModal(){this.open=true;}};
 Object.assign(q.state,{selectedKey:'qa:1',delivery:'live_api',feedGeneratedAt:NOW,manualLock:true,viewMode:'map',workspaceMode:'split',followSelected:false});q.state.eventData.set('qa',structuredClone(event));q.state.runners=[runner()];q.state.positions.set('qa:1',runner());return q;
}
test('single dialog renders labeled forecasts and recorded second/date precision, escapes names',()=>{
 const q=fixture();q.openDetails('checkpoints');assert.match(q.el.checkpointList.innerHTML,/Forecast/);assert.match(q.el.checkpointList.innerHTML,/Less certain/);assert.match(q.el.checkpointList.innerHTML,/9:00:03 AM/);assert.match(q.el.checkpointList.innerHTML,/2026/);assert.match(q.el.checkpointList.innerHTML,/passage-next/);assert.equal(q.state.manualLock,true);assert.equal(q.state.followSelected,false);
 q.state.runners[0].name='Anonymous';q.renderCheckpointHistory();assert.doesNotMatch(q.el.checkpointIdentity.textContent,/old/);q.el.detailsDialog.open=false;q.renderCheckpointHistory();assert.equal(q.el.checkpointList.innerHTML,'');assert.equal(q.el.checkpointList.dataset.signature,'');assert.equal(q.el.checkpointIdentity.textContent,'');
});
test('snapshot dialog and card share selected capture; live-aged position cannot replace or revive it',()=>{
 const q=fixture(),stamp=NOW-240000,gps={...runner(),source:'GPS',freshness:'LIVE',recorded_at:new Date(stamp-5000).toISOString()};
 Object.assign(q.state,{delivery:'periodic_snapshot',feedGeneratedAt:stamp});q.state.positions.set(gps.key,{...gps});q.state.snapshotPositions=[Object.freeze({...gps,key:'qa:other'}),Object.freeze(gps)];q.ageAllPositions();q.openDetails('checkpoints');q.renderNextCheckpoint(q.state.positions.get(gps.key));assert.match(q.el.checkpointList.innerHTML,/~10:20 AM MT/);assert.equal(q.el.runnerNextRemaining.textContent,'About 24 min at snapshot');assert.equal(q.state.positions.get(gps.key).freshness,'STALE');
 q.state.feedError='offline';q.renderCheckpointHistory();q.renderNextCheckpoint(q.state.positions.get(gps.key));assert.doesNotMatch(q.el.checkpointList.innerHTML,/~10:20/);assert.equal(q.el.runnerNextRemaining.textContent,'—');assert.equal(gps.freshness,'LIVE');
});
test('new degraded or terminal roster state cannot resurrect a snapshot forecast',()=>{
 for(const change of [q=>{q.state.positions.get('qa:1').checkpoint_passages_stale=true;},q=>{q.state.runners[0].status='FINISHED';},q=>{q.state.summary.upstream_stale=true;},q=>{q.state.positions.get('qa:1').rank_exclusion='GPS_OFF_ROUTE';}]){
  const q=fixture();q.state.delivery='periodic_snapshot';q.state.snapshotPositions=[Object.freeze(runner())];change(q);q.openDetails('checkpoints');q.renderNextCheckpoint(q.state.positions.get('qa:1'));assert.doesNotMatch(q.el.checkpointList.innerHTML,/~10:20/);assert.equal(q.el.runnerNextRemaining.textContent,'—');
 }
});
test('Info promotes the current leg and a shared-dialog CTA before narrative; all assets version together',()=>{
 const html=fs.readFileSync('index.html','utf8'),app=fs.readFileSync('app.js','utf8');assert.ok(html.indexOf('id="runnerRecordedName"')<html.indexOf('id="runnerCurrentSegment"'));assert.ok(html.indexOf('id="runnerNextArrival"')<html.indexOf('id="sourceMessage"'));assert.match(html,/id="timelineButton"[^>]*aria-controls="detailsDialog"/);assert.match(html,/All checkpoint times &amp; forecasts/);assert.match(app,/el\.timelineButton\.addEventListener\("click", \(\) => openDetails\("checkpoints"\)\)/);assert.equal((html.match(/\?v=1\.9\.1/g)||[]).length,5);
});
