// Anonymous, in-memory display fixtures only. No network or stored runner data.
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const rules = require('./race-logic.js');
function fixture() {
  const source = fs.readFileSync('app.js','utf8');
  const modified = source.replace('  window.addEventListener("DOMContentLoaded", init);', '  window.qa={state,el,setView,profileEvent,renderElevation};');
  const window={location:{hostname:'localhost',search:''},RutRules:rules};
  vm.runInNewContext(modified,{window,URLSearchParams,Map,WeakMap,Set,Date,Intl,console,setTimeout:fn=>fn()});
  const q=window.qa;
  const classes=new Set();
  function node() { return {hidden:false,textContent:'',innerHTML:'',dataset:{},attributes:{},clientWidth:320,style:{setProperty(){}},classList:{toggle(key,on){if(on??!classes.has(key)) classes.add(key);else classes.delete(key);},contains:key=>classes.has(key)},setAttribute(key,value){this.attributes[key]=value;}}; }
  for(const id of ['app','mapViewButton','elevationViewButton','elevationPanel','mapCanvas','finishToggle','elevationTitle','elevationSubtitle','elevationChart','elevationSummary','elevationCheckpoints','elevationRunner','elevationNote']) q.el[id]=node();
  q.state.map={stop(){},invalidateSize(){},_loaded:true};
  return {q,classes,window};
}
test('persistent Map/Elevation switch keeps selected runner, race and manual lock',()=>{
  const {q,classes}=fixture();
  q.state.selectedKey='race:9';q.state.finishEventId='race';q.state.manualLock=true;
  classes.add('show-finish');q.setView('elevation');
  assert.equal(q.state.viewMode,'elevation');assert.ok(classes.has('show-elevation'));assert.ok(!classes.has('show-finish'));
  assert.equal(q.state.selectedKey,'race:9');assert.equal(q.state.finishEventId,'race');assert.equal(q.state.manualLock,true);
  assert.equal(q.el.elevationViewButton.attributes['aria-pressed'],'true');assert.equal(q.el.mapCanvas.attributes['aria-hidden'],'true');
  q.setView('map');assert.equal(q.state.viewMode,'map');assert.equal(q.state.selectedKey,'race:9');
  assert.equal(q.el.mapViewButton.attributes['aria-pressed'],'true');assert.equal(q.el.elevationPanel.hidden,true);
  q.setView('invalid');assert.equal(q.state.viewMode,'map');
});
test('profile selects runner course, explicit filter, then selected-race preview',()=>{
  const {q}=fixture();q.state.eventData=new Map([['a',{id:'a'}],['b',{id:'b'}]]);
  q.state.finishEventId='a';q.state.filter='all';assert.equal(q.profileEvent().id,'a');
  q.state.filter='b';assert.equal(q.profileEvent().id,'b');
  q.state.runners=[{key:'a:9',event_id:'a'}];q.state.selectedKey='a:9';assert.equal(q.profileEvent().id,'a');
});
function populated() {
  const {q,window}=fixture();window.RutElevation=require('./elevation-profile.js');
  const day=new Date().toISOString().slice(0,10);
  const course={track_points:[{lat:45,lng:-111,ele:1000},{lat:45.01,lng:-111,ele:2000},{lat:45.02,lng:-111,ele:1000}],progress_points:[0,.5,1]};
  q.state.eventData.set('a',{id:'a',label:'QA <course>',event_date:day,start_time:'00:00:00',start_at:day+'T00:00:00Z',timezone:'UTC',course_status:'active',course,split_names:['Start','<img src=x onerror=alert(1)>','Finish'],estimator:{elevation_gain_m:1000,elevation_loss_m:1000}});
  q.state.selectedKey='a:9';q.state.finishEventId='a';q.state.feedGeneratedAt=Date.now();q.state.delivery='live_api';q.state.viewMode='elevation';
  const row={key:'a:9',event_id:'a',name:'Anonymous QA <script>',bib:9,status:'ON COURSE',source:'ESTIMATED',distance_source:'SPLIT_ESTIMATE',progress:.63,estimate_basis:'TERRAIN_CHECKPOINT_PILOT',observation_at:new Date().toISOString()};
  q.state.positions.set(row.key,row);return {q,row};
}
test('elevation labels estimates, holds, stale GPS and snapshots without changing progress',()=>{
  const {q,row}=populated();
  for(const [fields,expected] of [
    [{},/TERRAIN EST\./],
    [{estimate_held:true},/EST\. HELD/],
    [{source:'GPS',distance_source:'GPS_MATCHED',freshness:'STALE'},/STALE GPS/],
  ]) {
    Object.assign(row,fields);q.renderElevation();assert.match(q.el.elevationRunner.innerHTML,expected);
    assert.match(q.el.elevationChart.innerHTML,/data-progress="0.63"/);assert.equal(row.progress,.63);
  }
  q.state.delivery='periodic_snapshot';q.renderElevation();assert.match(q.el.elevationRunner.innerHTML,/SNAPSHOT · NOT LIVE/);
  q.state.delivery='live_api';q.state.feedGeneratedAt=Date.now()-100000;q.renderElevation();assert.match(q.el.elevationRunner.innerHTML,/OLD FEED · HELD/);
});
test('unmatched GPS and pre-start rows do not get a profile marker',()=>{
  const {q,row}=populated();Object.assign(row,{source:'GPS',distance_source:null});
  q.renderElevation();assert.doesNotMatch(q.el.elevationChart.innerHTML,/class="profile-runner/);
  row.distance_source='GPS_MATCHED';q.state.eventData.get('a').event_date='2099-01-01';
  q.renderElevation();assert.doesNotMatch(q.el.elevationChart.innerHTML,/class="profile-runner/);
});
test('profile escapes untrusted text and does not rebuild unchanged SVG on clock ticks',()=>{
  const {q}=populated();q.renderElevation();
  assert.doesNotMatch(q.el.elevationChart.innerHTML,/<img|<script|NaN|Infinity/);
  assert.match(q.el.elevationChart.innerHTML,/&lt;img/);assert.match(q.el.elevationRunner.innerHTML,/&lt;script/);
  let writes=0,html=q.el.elevationChart.innerHTML;
  Object.defineProperty(q.el.elevationChart,'innerHTML',{get:()=>html,set:v=>{html=v;writes++;}});
  q.renderElevation();assert.equal(writes,0);
  q.el.elevationPanel.clientHeight=270;q.renderElevation();assert.equal(writes,1);
});

test('anonymization removes the old runner name from every elevation surface',()=>{
  const {q,row}=populated();row.name='Private QA Runner';q.renderElevation();
  assert.match(q.el.elevationChart.innerHTML,/<title>Private QA Runner/);
  row.name='Anonymous';q.renderElevation();
  for(const node of Object.values(q.el)) {
    assert.doesNotMatch(node.innerHTML + node.textContent,/Private QA Runner/);
  }
  assert.match(q.el.elevationChart.innerHTML,/<title>Anonymous ·/);
  assert.match(q.el.elevationRunner.innerHTML,/<strong>Anonymous /);
});
test('hidden elevation discards identity and rebuilds anonymously without changing view state',()=>{
  const {q,row}=populated();row.name='Private QA Runner';q.state.manualLock=true;
  q.state.map.invalidateSize=options=>assert.equal(options.pan,false);
  q.state.map.setView=()=>assert.fail('must not reset map center or zoom');
  q.renderElevation();
  assert.match(q.el.elevationChart.innerHTML,/<title>Private QA Runner/);
  assert.match(q.el.elevationRunner.innerHTML,/<strong>Private QA Runner/);
  assert.match(q.el.elevationSubtitle.textContent,/Private QA Runner/);
  assert.match(q.el.elevationChart.dataset.signature,/Private QA Runner/);
  q.setView('map');row.name='Anonymous';q.renderElevation();
  assert.equal(q.el.elevationPanel.hidden,true);
  for(const surface of [q.el.elevationChart.innerHTML,q.el.elevationRunner.innerHTML,q.el.elevationSubtitle.textContent,q.el.elevationChart.dataset.signature]) {
    assert.doesNotMatch(surface,/Private QA Runner/);
  }
  assert.equal(q.el.elevationChart.dataset.signature,'');
  let writes=0,html=q.el.elevationChart.innerHTML;
  Object.defineProperty(q.el.elevationChart,'innerHTML',{get:()=>html,set:value=>{html=value;writes++;}});
  q.renderElevation();q.renderElevation();assert.equal(writes,0);
  q.setView('elevation');
  assert.equal(writes,1);
  assert.match(q.el.elevationChart.innerHTML,/<title>Anonymous ·/);
  assert.match(q.el.elevationChart.innerHTML,/data-progress="0.63"/);
  assert.match(q.el.elevationRunner.innerHTML,/<strong>Anonymous /);
  assert.match(q.el.elevationSubtitle.textContent,/Anonymous/);
  assert.doesNotMatch(q.el.elevationChart.dataset.signature,/Private QA Runner/);
  assert.equal(q.state.selectedKey,'a:9');assert.equal(q.state.finishEventId,'a');assert.equal(q.state.manualLock,true);
});
test('course label refresh invalidates the SVG accessible name',()=>{
  const {q}=populated();q.renderElevation();
  q.state.eventData.get('a').label='Updated QA course';q.renderElevation();
  assert.match(q.el.elevationChart.innerHTML,/aria-label="Updated QA course course elevation/);
  assert.doesNotMatch(q.el.elevationChart.innerHTML,/QA &lt;course&gt;/);
});
test('extreme or malformed elevations replace a cached chart with unavailable',()=>{
  const {q}=populated();q.renderElevation();
  const event=q.state.eventData.get('a'),course=event.course;
  for(const ele of [1e308,-1e308,12001,-12001,Infinity,NaN,'1000',null]) {
    event.course={...course,track_points:course.track_points.map((p,i)=>i===1?{...p,ele}:{...p})};
    q.renderElevation();
    assert.match(q.el.elevationChart.innerHTML,/Elevation profile unavailable/,String(ele));
    assert.doesNotMatch(q.el.elevationChart.innerHTML,/<svg|NaN|Infinity|∞/);
    assert.equal(q.el.elevationRunner.innerHTML,'');
    assert.equal(q.el.elevationCheckpoints.innerHTML,'');
  }
});

test('elevation unavailable remains explicit instead of inventing a chart',()=>{
  const {q,window}=fixture();window.RutElevation={buildProfile:()=>null};
  q.state.viewMode='elevation';q.state.eventData.set('a',{id:'a',label:'QA'});q.state.finishEventId='a';
  q.renderElevation();assert.match(q.el.elevationChart.innerHTML,/Elevation profile unavailable/);
  assert.equal(q.el.elevationRunner.innerHTML,'');assert.equal(q.el.elevationCheckpoints.innerHTML,'');
});
