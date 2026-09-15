// Anonymous in-memory fixtures; no network or real participant histories.
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const NOW=Date.parse('2026-09-12T16:00:00Z');
const GUIDE_EVENT_ID='the-rut-28k-2026';
class Clock extends Date {static now(){return NOW;}}
function fixture(event={id:GUIDE_EVENT_ID,label:'QA 28K'}) {
  const source = fs.readFileSync('app.js','utf8');
  const modified = source.replace('  window.addEventListener("DOMContentLoaded", init);','  window.qa = {state,el,renderCheckpointHistory,openDetails,clearCheckpointHistory,syncGuideAvailability};');
  const nodes={};
  const document={activeElement:null,getElementById(id){return nodes[id] || null;}};
  const window = {location:{hostname:'localhost',search:''},RutRules:require('./race-logic.js')};
  vm.runInNewContext(modified,{window,document,URLSearchParams,Map,Set,WeakMap,Date:Clock,Intl,console});
  const q=window.qa;
  const node=()=>({open:false,hidden:false,disabled:false,inert:false,textContent:'',innerHTML:'',dataset:{},scrollTop:0,showModal(){this.open=true;},close(){this.open=false;},focus(){document.activeElement=this;},getClientRects(){return [{}];}});
  for(const id of ['detailsDialog','detailsTitle','checkpointContent','guideContent','checkpointIdentity','checkpointStatus','checkpointList']) q.el[id]=node();
  q.el.detailsDialog.contains=(target)=>target===q.el.guideContent;
  q.el.checkpointButton=node();q.el.guideButton=node();
  nodes.resultsModeLink=node();nodes.liveModeLink=node();
  const eventId=event?.id || null, key=eventId ? `${eventId}:9` : null;
  q.state.delivery='live_api';q.state.feedGeneratedAt=NOW;q.state.selectedKey=key;q.state.finishEventId=eventId;q.state.manualLock=true;
  if(event) q.state.eventData.set(eventId,{...event,split_names:['Start','Aid <script>','Finish']});
  const runner=eventId ? {key,event_id:eventId,id:9,name:'Anonymous QA',bib:9,checkpoint_passages_status:'recorded',checkpoint_passages_stale:false,checkpoint_passages:[{split_index:0,elapsed_seconds:0,passed_at:'2026-09-11T14:30:00Z'},{split_index:1,elapsed_seconds:3600,passed_at:'2026-09-11T15:30:00Z'}]} : null;
  q.state.runners=runner ? [runner] : [];
  q.syncGuideAvailability();
  return {q,runner,document,nodes};
}
test('history includes evidenced Start, checkpoint Mountain clock, elapsed and missing finish',()=>{
  const {q}=fixture();q.openDetails('checkpoints');
  assert.match(q.el.checkpointList.innerHTML,/Start/);assert.match(q.el.checkpointList.innerHTML,/8:30:00 AM/);
  assert.match(q.el.checkpointList.innerHTML,/9:30:00 AM/);assert.match(q.el.checkpointList.innerHTML,/1:00:00/);
  assert.match(q.el.checkpointList.innerHTML,/No recorded time/);assert.match(q.el.checkpointList.innerHTML,/0:00/);
  assert.doesNotMatch(q.el.checkpointList.innerHTML,/<script>/);assert.match(q.el.checkpointList.innerHTML,/Aid &lt;script&gt;/);
});
test('opening either bottom detail keeps both primary views and selection intact',()=>{
  for(const view of ['map','elevation']) {
    const {q}=fixture();q.state.viewMode=view;
    q.openDetails('checkpoints');assert.equal(q.el.detailsDialog.open,true);
    assert.equal(q.state.selectedKey,`${GUIDE_EVENT_ID}:9`);assert.equal(q.state.manualLock,true);assert.equal(q.state.finishEventId,GUIDE_EVENT_ID);assert.equal(q.state.viewMode,view);
    q.openDetails('guide');assert.equal(q.el.guideContent.hidden,false);assert.equal(q.el.checkpointContent.hidden,true);
    assert.equal(q.el.checkpointIdentity.textContent,'');assert.equal(q.el.checkpointList.innerHTML,'');
  }
});
test('history works for finished/locationless roster runner; never derives passage from progress',()=>{
  const {q,runner}=fixture();runner.status='FINISHED';runner.progress=1;runner.checkpoint_passages=[];
  q.openDetails('checkpoints');assert.equal((q.el.checkpointList.innerHTML.match(/No recorded time/g)||[]).length,3);
  assert.doesNotMatch(q.el.checkpointList.innerHTML,/8:30:00/);
});
test('missing clock retains elapsed without inventing Mountain timestamp',()=>{
  const {q,runner}=fixture();runner.checkpoint_passages=[{split_index:1,elapsed_seconds:90,passed_at:null}];q.openDetails('checkpoints');
  assert.match(q.el.checkpointList.innerHTML,/Clock time unavailable/);assert.match(q.el.checkpointList.innerHTML,/1:30/);
});
test('snapshot and stale histories remain labeled, but recorded times stay fixed',()=>{
  const {q,runner}=fixture();q.state.delivery='periodic_snapshot';q.openDetails('checkpoints');assert.match(q.el.checkpointStatus.textContent,/snapshot.*not live/i);
  const html=q.el.checkpointList.innerHTML;q.state.delivery='live_api';runner.checkpoint_passages_stale=true;q.renderCheckpointHistory();
  assert.match(q.el.checkpointStatus.textContent,/stale/i);assert.equal(q.el.checkpointList.innerHTML,html);
});
test('refresh removes prior identity while visible, closed and guide-open',()=>{
  const {q,runner}=fixture();runner.name='QA Old Identity';q.openDetails('checkpoints');
  runner.name='Anonymous';q.renderCheckpointHistory();assert.doesNotMatch(q.el.checkpointIdentity.textContent,/Old Identity/);
  q.el.detailsDialog.open=false;q.renderCheckpointHistory();assert.equal(q.el.checkpointIdentity.textContent,'');assert.equal(q.el.checkpointList.innerHTML,'');assert.equal(q.el.checkpointList.dataset.signature,'');
  q.openDetails('guide');q.renderCheckpointHistory();assert.equal(q.el.checkpointIdentity.textContent,'');
});
test('no selection and older API safely display no history',()=>{
  const {q,runner}=fixture();delete runner.checkpoint_passages;q.openDetails('checkpoints');assert.match(q.el.checkpointStatus.textContent,/unavailable/i);
  q.state.selectedKey=null;q.renderCheckpointHistory();assert.match(q.el.checkpointStatus.textContent,/Select a runner/);assert.equal(q.el.checkpointList.innerHTML,'');
});
test('bad numeric values, duplicate indexes and future dates cannot masquerade as reads',()=>{
  const {q,runner}=fixture();runner.checkpoint_passages=[{split_index:0,elapsed_seconds:null,passed_at:'2026-09-11T14:30:00Z'},{split_index:1,elapsed_seconds:Infinity,passed_at:'2026-09-11T14:30:00Z'},{split_index:2,elapsed_seconds:120,passed_at:'2099-01-01T00:00:00Z'}];
  q.openDetails('checkpoints');assert.doesNotMatch(q.el.checkpointList.innerHTML,/Infinity|NaN|2099/);assert.equal((q.el.checkpointList.innerHTML.match(/No recorded time/g)||[]).length,3);
  runner.checkpoint_passages=[{split_index:1,elapsed_seconds:90,passed_at:null},{split_index:1,elapsed_seconds:120,passed_at:null}];q.renderCheckpointHistory();assert.equal((q.el.checkpointList.innerHTML.match(/No recorded time/g)||[]).length,3);
});
test('unchanged history render preserves scroll and avoids list rebuilds',()=>{
  const {q}=fixture();q.openDetails('checkpoints');let html=q.el.checkpointList.innerHTML,writes=0;
  Object.defineProperty(q.el.checkpointList,'innerHTML',{get(){return html;},set(value){html=value;writes++;}});
  q.el.checkpointContent.scrollTop=150;q.renderCheckpointHistory();assert.equal(writes,0);assert.equal(q.el.checkpointContent.scrollTop,150);
});
test('guide is available only for the exact Rut 28K 2026 event id',()=>{
  const {q}=fixture();
  assert.equal(q.el.guideButton.hidden,false);assert.equal(q.el.guideButton.disabled,false);assert.equal(q.el.guideButton.inert,false);
  q.openDetails('guide');assert.equal(q.el.detailsDialog.open,true);assert.equal(q.el.guideContent.hidden,false);
});
test('a 28K label cannot enable the event-specific guide',()=>{
  const {q}=fixture({id:'some-other-race-2026',label:'The Rut 28K 2026'});
  assert.equal(q.el.guideButton.hidden,true);assert.equal(q.el.guideButton.disabled,true);assert.equal(q.el.guideButton.inert,true);
  q.openDetails('guide');assert.equal(q.el.detailsDialog.open,false);assert.equal(q.el.guideContent.hidden,true);
});
test('unrelated, missing and ambiguous event identities fail closed',()=>{
  for(const event of [{id:'the-rut-21k-2026',label:'The Rut 21K 2026'},null]) {
    const {q}=fixture(event);q.openDetails('guide');
    assert.equal(q.el.guideButton.hidden,true);assert.equal(q.el.guideButton.disabled,true);assert.equal(q.el.guideButton.inert,true);
    assert.equal(q.el.detailsDialog.open,false);assert.equal(q.state.detailsMode,'checkpoints');assert.equal(q.el.guideContent.hidden,true);
  }
  const {q}=fixture();
  q.state.eventData.set('the-rut-21k-2026',{id:'the-rut-21k-2026',label:'The Rut 21K 2026'});
  q.state.selectedKey=null;q.state.finishEventId=null;q.syncGuideAvailability();q.openDetails('guide');
  assert.equal(q.el.guideButton.hidden,true);assert.equal(q.el.detailsDialog.open,false);
});
test('legacy multi-event mode follows the selected runner, then the finish event',()=>{
  const {q}=fixture();
  q.state.eventData.set('the-rut-21k-2026',{id:'the-rut-21k-2026',label:'The Rut 21K 2026'});
  q.state.runners.push({key:'the-rut-21k-2026:4',event_id:'the-rut-21k-2026',id:4,name:'Other runner'});
  q.state.selectedKey='the-rut-21k-2026:4';q.syncGuideAvailability();assert.equal(q.el.guideButton.hidden,true);
  q.state.selectedKey=null;q.syncGuideAvailability();assert.equal(q.el.guideButton.hidden,false);
});
test('changing away from Rut closes and hides an open guide',()=>{
  const {q}=fixture();q.openDetails('guide');assert.equal(q.el.detailsDialog.open,true);
  q.state.eventData=new Map([['the-rut-21k-2026',{id:'the-rut-21k-2026',label:'The Rut 21K 2026'}]]);
  q.state.runners=[];q.state.selectedKey=null;q.state.finishEventId='the-rut-21k-2026';q.syncGuideAvailability();
  assert.equal(q.el.detailsDialog.open,false);assert.equal(q.el.guideContent.hidden,true);assert.equal(q.el.guideButton.hidden,true);
});
test('forced guide closure moves focus outside the hidden dialog',()=>{
  const {q,document,nodes}=fixture();q.openDetails('guide');document.activeElement=q.el.guideContent;
  q.state.eventData=new Map([['the-rut-21k-2026',{id:'the-rut-21k-2026',label:'The Rut 21K 2026'}]]);
  q.state.runners=[];q.state.selectedKey=null;q.state.finishEventId='the-rut-21k-2026';q.syncGuideAvailability();
  assert.equal(q.el.detailsDialog.open,false);assert.equal(document.activeElement,nodes.resultsModeLink);
  assert.equal(q.el.detailsDialog.contains(document.activeElement),false);
});
