// Anonymous, in-memory fixtures only. No network or participant snapshots.
const test=require('node:test'), assert=require('node:assert/strict'), fs=require('node:fs'), vm=require('node:vm');
const rules=require('./race-logic.js');
const NOW=Date.parse('2026-09-12T16:00:00Z');
const event={id:'qa',label:'QA VK',split_names:['Start','Aid <x>','Finish'],event_date:'2026-09-12',start_at:'2026-09-12T12:00:00Z',timezone:'UTC',course_status:'active',course:{split_points:[{name:'Start',lat:45,lng:-111},{name:'Finish',lat:45.02,lng:-111}],progress_points:[0,.5,1],track_points:[{lat:45,lng:-111,ele:1000},{lat:45.01,lng:-111,ele:2000},{lat:45.02,lng:-111,ele:1000}]}};
const passage=(split_index=1,passed_at='2026-09-12T15:00:03Z',elapsed_seconds=3603)=>({split_index,passed_at,elapsed_seconds});
const runner=()=>({key:'qa:9',event_id:'qa',name:'QA Old <name>',bib:9,status:'ON COURSE',checkpoint_passages_status:'recorded',checkpoint_passages:[passage()]});
test('only actual passages, not estimated progress or GPS observation, establish latest check-in',()=>{
 const row={...runner(),last_split_index:2,progress:.95,recorded_at:new Date(NOW).toISOString(),observation_at:new Date(NOW).toISOString()};
 assert.equal(rules.recordedCheckIn(row,event,{now:NOW,generatedAt:NOW}).latest.index,1);
 delete row.checkpoint_passages;assert.equal(rules.recordedCheckIn(row,event,{now:NOW}).latest,null);
});
test('Start needs an actual row and latest unknown clock stays latest by timing index',()=>{
 const row=runner();row.checkpoint_passages=[];assert.equal(rules.recordedCheckIn(row,event,{now:NOW}).latest,null);
 row.checkpoint_passages=[passage(0,null,0)];assert.equal(rules.recordedCheckIn(row,event,{now:NOW}).latest.index,0);
 row.checkpoint_passages=[passage(),passage(2,null,7200)];const result=rules.recordedCheckIn(row,event,{now:NOW});assert.equal(result.latest.index,2);assert.equal(result.latest.when,null);assert.match(result.clock,/unavailable/i);
});
test('malformed, duplicate including invalid first, future and timezone-less rows are rejected',()=>{
 for(const rows of [[passage(1,null,NaN),passage()],[passage(),passage()],[passage(1,'2099-01-01T00:00:00Z')],[passage(1,'2026-09-12T15:00:00')],[passage(0,null,1)],[passage(1,null,0)]]) assert.equal(rules.recordedCheckIn({...runner(),checkpoint_passages:rows},event,{now:NOW}).latest,null);
});
test('Mountain seconds/date/age and stale, snapshot, partial sources remain distinct',()=>{
 let result=rules.recordedCheckIn(runner(),event,{now:NOW,generatedAt:NOW});assert.match(result.clock,/9:00:03 AM MT.*Sep 12.*2026/);assert.equal(result.ageSeconds,3597);
 result=rules.recordedCheckIn({...runner(),checkpoint_passages_status:'partial',checkpoint_passages_stale:true},event,{now:NOW,generatedAt:NOW,delivery:'periodic_snapshot'});assert.match(result.source,/snapshot.*not live/i);assert.match(result.source,/stale/i);assert.match(result.source,/partial/i);
 assert.match(rules.recordedCheckIn(runner(),event,{now:NOW}).source,/stale/i);
});
test('filtered VK geographic splits cannot shift timing indexes; missing anchor is explicit',()=>{
 assert.equal(rules.recordedCheckpointAnchor(event,{index:1,name:'Aid <x>'}),null);
 assert.deepEqual(rules.recordedCheckpointAnchor(event,{index:2,name:'Finish'}),{lat:45.02,lng:-111});
 const duplicate={...event,course:{split_points:[{name:'Finish',lat:1,lng:2},{name:'Finish',lat:3,lng:4}]}};assert.equal(rules.recordedCheckpointAnchor(duplicate,{index:2,name:'Finish'}),null);
});
function fixture(){
 class Clock extends Date {static now(){return NOW;}}
 const window={location:{hostname:'localhost',search:''},RutRules:rules,RutElevation:require('./elevation-profile.js')};
 const layers=new Set(),L={divIcon:x=>x,marker:(coords,options)=>({coords,options,bindTooltip(t,options){this.tooltip=t;this.tooltipOptions=options;return this;},getTooltip(){return {options:this.tooltipOptions};},addTo(){layers.add(this);return this;},openTooltip(){return this;},closeTooltip(){return this;},unbindTooltip(){this.tooltip='';return this;},on(){return this;},setLatLng(c){this.coords=c;},setIcon(i){this.options.icon=i;},setZIndexOffset(){},setTooltipContent(t){this.tooltip=t;},getElement(){return {};}})};
 const source=fs.readFileSync('app.js','utf8').replace('  window.addEventListener("DOMContentLoaded", init);','  window.qa={state,el,renderRecordedCheckIn,renderRecordedMarker,renderMarkers,renderElevation};');
 vm.runInNewContext(source,{window,L,URLSearchParams,Map,Set,WeakMap,Date:Clock,Intl,console});
 const q=window.qa;for(const id of ['runnerRecordedName','runnerRecordedClock','runnerRecordedAge','runnerRecordedSource','runnerRecordedAnchor','positionCount','elevationTitle','elevationSubtitle','elevationChart','elevationSummary','elevationCheckpoints','elevationRunner','elevationNote','elevationPanel','displayPane'])q.el[id]={textContent:'',innerHTML:'',dataset:{},clientWidth:600,clientHeight:500};
 Object.assign(q.state,{selectedKey:'qa:9',finishEventId:'qa',filter:'qa',manualLock:true,followSelected:false,viewMode:'elevation',workspaceMode:'info',delivery:'live_api',feedGeneratedAt:NOW,map:{removeLayer(m){layers.delete(m);}}});
 q.state.eventData.set('qa',structuredClone(event));const row=runner();q.state.runners=[row];q.state.positions.set(row.key,{...row,checkpoint_passages:[passage(0,null,0)],lat:45.019,lng:-111,source:'ESTIMATED',freshness:'ESTIMATED',distance_source:'SPLIT_ESTIMATE',progress:.9});return {q,row,layers};
}
test('roster evidence wins; finished/DNF/locationless still retain passage and no virtual anchor',()=>{
 const {q,row}=fixture();for(const status of ['FINISHED','DNF']){row.status=status;q.state.positions.clear();q.renderRecordedCheckIn();assert.equal(q.el.runnerRecordedName.textContent,'Aid <x>');assert.match(q.el.runnerRecordedAnchor.textContent,/unavailable/i);}
 delete row.checkpoint_passages;q.state.positions.set(row.key,{...row,checkpoint_passages:[passage(0,null,0)]});q.renderRecordedCheckIn();assert.equal(q.el.runnerRecordedName.textContent,'Start');
});
test('Map and Elevation show distinct recorded square and estimated marker without changing selection',()=>{
 const {q,row,layers}=fixture();row.checkpoint_passages=[passage(2)];q.renderRecordedCheckIn();q.renderMarkers();q.renderElevation();
 assert.equal(layers.size,2);assert.deepEqual(Array.from(q.state.recordedMarker.coords),[45.02,-111]);assert.match(q.state.recordedMarker.tooltip,/Last recorded check-in.*Finish.*9:00:03/);assert.match(q.state.markers.get(row.key).tooltip,/Estimated location/);
 assert.match(q.el.elevationChart.innerHTML,/class="profile-recorded".*data-split-index="2"/);assert.match(q.el.elevationChart.innerHTML,/class="profile-runner/);assert.match(q.el.elevationRunner.innerHTML,/Estimated location/);
 assert.equal(q.state.selectedKey,'qa:9');assert.equal(q.state.filter,'qa');assert.equal(q.state.manualLock,true);assert.equal(q.state.followSelected,false);assert.equal(q.state.workspaceMode,'info');
 let html=q.el.elevationChart.innerHTML,writes=0;Object.defineProperty(q.el.elevationChart,'innerHTML',{get:()=>html,set:v=>{html=v;writes++;}});q.renderElevation();assert.equal(writes,0);
});
test('profile checkpoint matching uses original index when earlier geometry is missing',()=>{
 const {q,row}=fixture();q.state.eventData.get('qa').course.progress_points=[null,.5,1];row.checkpoint_passages=[passage(1)];q.renderElevation();assert.match(q.el.elevationChart.innerHTML,/class="profile-recorded".*data-split-index="1"/);assert.match(q.el.elevationChart.innerHTML,/Aid &lt;x&gt;/);
});
test('identity/privacy refresh and no selection clear hidden evidence and marker signatures',()=>{
 const {q,row,layers}=fixture();row.checkpoint_passages=[passage(2)];q.renderMarkers();q.renderElevation();const old=q.state.recordedMarker;row.name='Anonymous';q.renderMarkers();assert.doesNotMatch(q.state.recordedMarker.tooltip,/Old/);assert.equal(layers.has(old),false);
 q.state.viewMode='map';q.el.displayPane.hidden=true;q.renderElevation();assert.equal(q.el.elevationChart.innerHTML,'');assert.equal(q.el.elevationChart.dataset.signature,'');
 q.state.selectedKey=null;q.renderRecordedCheckIn();q.renderMarkers();assert.equal(q.state.recordedMarker,null);assert.equal(q.state.recordedMarkerSignature,'');assert.equal(q.el.runnerRecordedName.textContent,'—');
});
test('app rejects impossible civil dates and times instead of publishing normalized official clocks',()=>{
 for(const clock of ['2026-02-30T15:00:00Z','2026-04-31T15:00:00Z','2026-02-29T15:00:00Z','2026-09-11T24:00:00Z','2026-09-11T12:60:00Z','2026-09-11T12:00:60Z','2026-09-11T12:00:00+24:00','2026-09-11T12:00:00+01:60']) {
  const {q,row}=fixture();row.checkpoint_passages=[passage(2,clock)];q.renderRecordedCheckIn();q.renderMarkers();q.renderElevation();
  assert.equal(q.el.runnerRecordedName.textContent,'No recorded check-in',clock);assert.equal(q.state.recordedMarker,null);assert.doesNotMatch(q.el.elevationChart.innerHTML,/class="profile-recorded"/);
 }
 for(const clock of ['2024-02-29T15:00:00Z','2026-09-12T08:00:03-07:00','2026-09-12T15:00:03.123Z']) assert.ok(rules.recordedCheckIn({...runner(),checkpoint_passages:[passage(2,clock)]},event,{now:NOW}).latest,clock);
});
test('expanded elevation retains source qualifiers and redraws on timing-only source changes',()=>{
 const {q,row}=fixture();q.state.workspaceMode='display';row.checkpoint_passages=[passage(2)];q.renderElevation();let previous=q.el.elevationChart.dataset.signature;
 for(const change of [()=>{row.checkpoint_passages_stale=true;},()=>{row.checkpoint_passages_status='partial';},()=>{q.state.delivery='periodic_snapshot';}]) {
  change();q.renderElevation();q.renderMarkers();const evidence=rules.recordedCheckIn(row,event,{now:NOW,generatedAt:NOW,delivery:q.state.delivery});
  assert.notEqual(q.el.elevationChart.dataset.signature,previous);previous=q.el.elevationChart.dataset.signature;
  assert.ok(q.el.elevationRunner.innerHTML.includes(evidence.source));assert.ok(q.el.elevationChart.innerHTML.match(/class="profile-recorded"[^]*?<title>(.*?)<\/title>/)[1].includes(evidence.source));assert.ok(q.state.recordedMarker.tooltip.includes(evidence.source));
 }
});
test('malformed geographic collections and candidates never crash the card or shift timing indexes',()=>{
 for(const points of [[null],{},'broken',[null,7,{}, {name:'Finish',lat:45.02,lng:-111}], [{name:'Finish',lat:'45',lng:-111}], [{name:'Finish',lat:91,lng:-111}]]) {
  const {q,row}=fixture();q.state.eventData.get('qa').course.split_points=points;row.checkpoint_passages=[passage(1)];
  assert.doesNotThrow(()=>{q.renderRecordedCheckIn();q.renderMarkers();q.renderElevation();});assert.equal(q.el.runnerRecordedName.textContent,'Aid <x>');assert.equal(q.state.recordedMarker,null);assert.match(q.el.runnerRecordedAnchor.textContent,/unavailable/);
  row.checkpoint_passages=[passage(2)];assert.doesNotThrow(()=>{q.renderRecordedCheckIn();q.renderMarkers();});
  assert.equal(Boolean(q.state.recordedMarker),Array.isArray(points)&&points.some(p=>p?.name==='Finish'&&p.lat===45.02));
 }
});
test('snapshot never displays a passage later than its valid capture timestamp',()=>{
 const {q,row}=fixture();q.state.delivery='periodic_snapshot';q.state.feedGeneratedAt=Date.parse('2026-09-12T14:59:00Z');row.checkpoint_passages=[passage(2)];q.renderRecordedCheckIn();q.renderMarkers();q.renderElevation();
 assert.equal(q.el.runnerRecordedName.textContent,'No recorded check-in');assert.equal(q.state.recordedMarker,null);assert.doesNotMatch(q.el.elevationChart.innerHTML,/class="profile-recorded"/);
});
test('red selection outline follows only the selected location marker and preserves source styles',()=>{
 const css=fs.readFileSync('styles.css','utf8');
 const ring=css.match(/\.runner-pin\.selected:after\s*\{([^}]+)\}/)?.[1] || '';
 assert.match(ring,/border:\s*3px solid #ff3b30/);
 assert.match(ring,/box-shadow:[^;]*white/);
 assert.match(ring,/pointer-events:\s*none/);
 assert.match(css,/\.recorded-pin\s*\{[^}]*background:\s*#d7ff4f/);
 const {q,row}=fixture(),first=q.state.positions.get(row.key),second={...first,key:'qa:10',name:'Anonymous second',source:'GPS',freshness:'LIVE'};
 q.state.runners.push({...row,key:second.key,name:second.name});q.state.positions.set(second.key,second);
 const selected=key=>/runner-pin[^"\n]*\bselected\b/.test(q.state.markers.get(key).options.icon.html);
 for(const [source,freshness] of [['ESTIMATED','ESTIMATED'],['GPS','LIVE'],['GPS','STALE']]){
  Object.assign(first,{source,freshness});q.state.selectedKey=first.key;q.renderMarkers();
  assert.equal(selected(first.key),true);assert.equal(selected(second.key),false);
  assert.match(q.state.markers.get(first.key).options.icon.html,source==='GPS'?new RegExp(`runner-pin gps ${freshness.toLowerCase()}\\b`):/runner-pin estimated/);
 }
 q.state.selectedKey=second.key;q.renderMarkers();assert.equal(selected(first.key),false);assert.equal(selected(second.key),true);
 q.state.selectedKey=null;q.renderMarkers();assert.equal(selected(first.key),false);assert.equal(selected(second.key),false);
});
test('primary check-in precedes estimates and source explanation; versions travel together',()=>{
 const html=fs.readFileSync('index.html','utf8');assert.ok(html.indexOf('id="runnerRecordedName"')<html.indexOf('id="sourceMessage"'));assert.ok(html.indexOf('id="runnerRecordedName"')<html.indexOf('id="runnerNextArrival"'));assert.equal((html.match(/\?v=1\.9\.1/g)||[]).length,5);
});
