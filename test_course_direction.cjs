// Synthetic in-memory geometry and marker fixtures; no network or identities saved.
const test = require('node:test'), assert = require('node:assert/strict');
const fs = require('node:fs'), vm = require('node:vm');
const rules = require('./race-logic.js');
const NOW = Date.parse('2026-09-12T16:00:00Z');
const LABEL = 'Course direction · not measured heading';
const point = (lat, lng) => ({lat, lng});
const eventFor = track_points => ({id:'qa', label:'QA course', event_date:'2026-09-12', start_at:'2026-09-12T12:00:00Z', timezone:'UTC', course_status:'active', split_names:['Start','Finish'], course:{track_points, split_points:[{name:'Start',lat:0,lng:0}], progress_points:[0,1]}});
const position = (change = {}) => ({key:'qa:1', event_id:'qa', name:'QA <old>', bib:1, course:'QA', event_color:'#55aa99', status:'ON COURSE', source:'ESTIMATED', freshness:'ESTIMATED', distance_source:'SPLIT_ESTIMATE', rank_exclusion:null, lat:0, lng:0, progress:0, ...change});
const near = (actual, expected, tolerance = 1e-7) => {assert.equal(typeof actual,'number');assert.ok(Math.abs(actual-expected)<tolerance,`${actual} != ${expected}`);};
const direction = (track, progress=0, change={}) => rules.courseDirection(eventFor(track), position({progress,...change}));

for (const [name, end, expected] of [['north',point(1,0),0],['east',point(0,1),90],['south',point(-1,0),180],['west',point(0,-1),270]]) test(`forward course bearing is ${name}, not supplied GPS heading`, () => {
  for (const progress of [0,.4,.999999999]) near(direction([point(0,0),end],progress,{heading:37,speed_mps:999}),expected);
});
test('distance-weighted route order selects the local leg and outgoing segment at exact bends', () => {
  const track = [point(-1,0),point(0,0),point(0,3)];
  near(direction(track,.249),0);near(direction(track,.25-1e-12),0);near(direction(track,.25),90);near(direction(track,.6),90);
  // Longitude distances shrink at high latitude; this is not point-index or planar degree progress.
  near(direction([point(59,0),point(60,0),point(60,1)],.6),0);
});
test('duplicates at start, bend and finish are skipped without changing progress or dividing by zero', () => {
  const track=[point(-1,0),point(-1,0),point(0,0),point(0,0),point(0,3),point(0,3)];
  near(direction(track,0),0);near(direction(track,.25),90);near(direction(track,.999999),90);
  assert.equal(direction([point(0,0),point(0,0)]),null);
});
test('loop and out-and-back visits use supplied progress, never nearest coordinates or checkpoint chords', () => {
  const track=[point(0,0),point(1,0),point(0,0),point(0,1),point(0,0)];
  for(const [p,bearing] of [[0,0],[.25,180],[.5,90],[.75,270]]) near(direction(track,p,{lat:0,lng:0,last_split_index:0}),bearing);
});
test('progress is a raw finite number in [0,1); missing, null and finish never fabricate a direction', () => {
  const track=[point(0,0),point(1,0)];
  for(const progress of [undefined,null,'',false,true,'0','0.5',NaN,Infinity,-Infinity,-.01,1,1.01,{},[]]) assert.equal(direction(track,progress,{progress}),null,String(progress));
  near(direction(track,0),0);
});
test('missing, sparse, corrupt and ambiguous tracks fail closed rather than bridge invalid points', () => {
  for(const track of [undefined,null,{},[],[point(0,0)],[point(0,0),,point(1,0)],[point(0,0),null,point(1,0)],[point(0,0),point(0,180)],[point(90,0),point(89,0)]]) assert.equal(direction(track),null);
  for(const key of ['lat','lng']) for(const value of [undefined,null,'0',NaN,Infinity,{},[],true,key==='lat'?91:181]) {
    assert.equal(direction([point(0,0),{...point(.5,0),[key]:value},point(1,0)]),null);
  }
  assert.equal(rules.courseDirection(null,position()),null);
});
test('nonlocal antimeridian/polar geometry fails closed and elevation is not required', () => {
  assert.equal(direction([point(0,179),point(0,-179)]),null);
  assert.equal(direction([point(86,0),point(87,0)]),null);
  near(direction([{...point(0,0),ele:NaN},{...point(1,0),ele:null}]),0);
  // East at high latitude aligns with Leaflet's line, not a great-circle initial heading.
  near(direction([point(60,0),point(60,1)]),90);
  const dy=Math.log(Math.tan(Math.PI/4+61*Math.PI/360))-Math.log(Math.tan(Math.PI/4+60*Math.PI/360));
  near(direction([point(60,0),point(61,1)]),Math.atan2(Math.PI/180,dy)*180/Math.PI);
});
test('only ON COURSE, same-event mapped sources and valid located positions support an arrow', () => {
  const event=eventFor([point(0,0),point(1,0)]);
  for(const change of [{status:'FINISHED'},{status:'DNF'},{status:'DNS'},{status:'REGISTERED'},{status:'GPS'},{status:null},{source:'GPS',distance_source:'SPLIT_ESTIMATE'},{source:'ESTIMATED',distance_source:'GPS_MATCHED'},{source:'GPS',distance_source:null},{source:'OTHER'},{event_id:'other'},{lat:null},{lng:undefined},{lat:'0'},{lng:Infinity},{lat:91},{rank_exclusion:'GPS_AMBIGUOUS'},{rank_exclusion:'GPS_OFF_ROUTE'},{rank_exclusion:'ROUTE_UNAVAILABLE'}]) assert.equal(rules.courseDirection(event,position(change)),null,JSON.stringify(change));
  assert.equal(rules.courseDirection(event,null),null);
  near(rules.courseDirection(event,position({source:'GPS',distance_source:'GPS_MATCHED'})),0);
});
test('held, stale and old observations retain static course direction without clock extrapolation or mutation', () => {
  const event=eventFor([point(-1,0),point(0,0),point(0,3)]);
  for(const change of [{estimate_held:true,estimate_overdue:true,freshness:'STALE',rank_exclusion:'UPSTREAM_STALE'},{source:'GPS',distance_source:'GPS_MATCHED',freshness:'STALE',recorded_at:'2000-01-01T00:00:00Z',rank_exclusion:'STALE_GPS'}]) {
    const row=Object.freeze(position({progress:.25,...change})),before=JSON.stringify(event);
    near(rules.courseDirection(event,row),90);assert.equal(JSON.stringify(event),before);assert.equal(row.progress,.25);
  }
});
test('immutable track geometry including invalid tracks is cached; replacement track invalidates it', () => {
  let reads=0;
  const counted = (lat,lng) => Object.freeze({get lat(){reads++;return lat;},get lng(){reads++;return lng;}});
  const track=Object.freeze([counted(-1,0),counted(0,0),counted(0,3)]),event=eventFor(track);
  near(rules.courseDirection(event,position()),0);const built=reads;assert.ok(built>0);
  for(const progress of [.1,.25,.9]) rules.courseDirection({...event,course:{track_points:track}},position({progress}));
  assert.equal(reads,built,'no re-scan of an unchanged immutable track');
  event.course.track_points=[point(0,0),point(0,-1)];near(rules.courseDirection(event,position()),270);
  const invalid=eventFor([counted(0,0),null,counted(1,0)]);
  assert.equal(rules.courseDirection(invalid,position()),null);const rejectedReads=reads;
  assert.equal(rules.courseDirection(invalid,position()),null);assert.equal(reads,rejectedReads);
});

function fixture() {
  class Clock extends Date {static now(){return NOW;}}
  let calls=0, icons=0;
  const window={location:{hostname:'localhost',search:''},RutRules:{...rules,courseDirection(...args){calls++;return rules.courseDirection(...args);}},RutElevation:require('./elevation-profile.js'),matchMedia:()=>({matches:true})};
  const layers=new Set();
  const L={divIcon:options=>{icons++;return options;},marker:(coords,options)=>({coords,options,dom:{title:options.title},iconWrites:0,
    bindTooltip(t,options){this.tooltip=t;this.tooltipOptions=options;return this;},getTooltip(){return {options:this.tooltipOptions};},addTo(){layers.add(this);return this;},openTooltip(){return this;},closeTooltip(){return this;},unbindTooltip(){this.tooltip='';return this;},on(){return this;},
    setLatLng(c){this.coords=c;},setIcon(i){this.options.icon=i;this.iconWrites++;},setZIndexOffset(){},setTooltipContent(t){this.tooltip=t;},getElement(){return this.dom;}})};
  const source=fs.readFileSync('app.js','utf8').replace('  window.addEventListener("DOMContentLoaded", init);','  window.qa={state,el,renderMarkers,markerIcon,setView,setWorkspaceMode};');
  vm.runInNewContext(source,{window,L,URLSearchParams,Map,Set,WeakMap,Date:Clock,Intl,console});
  const q=window.qa;
  for(const [,id] of fs.readFileSync('index.html','utf8').matchAll(/\bid="([^"]+)"/g)) q.el[id]={textContent:'',innerHTML:'',dataset:{},hidden:false,clientWidth:390,clientHeight:500,classList:{toggle(){},contains(){return false;}},setAttribute(){},focus(){}};
  const center={lat:0,lng:0},map={_loaded:true,removeLayer(m){layers.delete(m);},getCenter:()=>center,stop(){},invalidateSize(){},panTo(c){assert.deepEqual(c,center);},setView(){assert.fail('render/layout cannot change camera');},fitBounds(){assert.fail('render/layout cannot fit camera');}};
  Object.assign(q.state,{selectedKey:'qa:1',finishEventId:'qa',filter:'qa',manualLock:true,followSelected:false,directorMode:'MANUAL',viewMode:'map',workspaceMode:'split',delivery:'live_api',feedGeneratedAt:NOW,map});
  const event=eventFor([point(-1,0),point(0,0),point(0,3)]);q.state.eventData.set('qa',event);
  const first=position({progress:.1}),second=position({key:'qa:2',name:'QA second',progress:.5,source:'GPS',distance_source:'GPS_MATCHED',freshness:'LIVE'});
  q.state.positions.set(first.key,first);q.state.positions.set(second.key,second);q.state.runners=[{...first,checkpoint_passages_status:'recorded',checkpoint_passages:[{split_index:0,passed_at:null,elapsed_seconds:0}]},{...second}];
  return {q,event,first,second,layers,counts:()=>({calls,icons})};
}
const iconHtml = (q,key=q.state.selectedKey) => q.state.markers.get(key).options.icon.html;
const arrow = html => html.match(/<svg\b[^]*?<\/svg>/)?.[0] || '';
const rotation = html => Number(arrow(html).match(/rotate\(([-\d.e+]+)deg\)/)?.[1]);
test('selected tooltip clears the full arrow radius and resets offsets on invalidation or selection switch', () => {
  const {q,first,second}=fixture();q.renderMarkers();
  const offset=key=>Array.from(q.state.markers.get(key).getTooltip().options.offset);
  assert.deepEqual(offset(first.key),[0,-42]);assert.deepEqual(offset(second.key),[0,-8]);
  first.progress=null;q.renderMarkers();assert.deepEqual(offset(first.key),[0,-8]);
  first.progress=.1;first.name='Anonymous';q.renderMarkers();assert.deepEqual(offset(first.key),[0,-42]);assert.match(q.state.markers.get(first.key).tooltip,/Anonymous/);
  q.state.selectedKey=second.key;q.renderMarkers();assert.deepEqual(offset(first.key),[0,-8]);assert.deepEqual(offset(second.key),[0,-42]);
  q.state.selectedKey=null;q.renderMarkers();assert.deepEqual(offset(second.key),[0,-8]);
});
test('actual selected marker adds one notched red dart, preserving location, ring, source and recorded square', () => {
  const {q,first,second}=fixture();q.renderMarkers();
  const marker=q.state.markers.get(first.key),svg=arrow(iconHtml(q));assert.match(svg,/class="course-direction"/);assert.match(svg,/<path\b/);assert.match(svg,new RegExp(LABEL));
  assert.match(iconHtml(q),/runner-pin estimated[^"\n]*selected/);assert.equal(arrow(iconHtml(q,second.key)),'');assert.doesNotMatch(q.state.recordedMarker.options.icon.html,/course-direction|<svg/);
  assert.deepEqual(Array.from(marker.coords),[first.lat,first.lng]);assert.deepEqual(Array.from(marker.options.icon.iconSize),[30,30]);assert.deepEqual(Array.from(marker.options.icon.iconAnchor),[15,15]);
  assert.equal(marker.options.keyboard,true);assert.equal(marker.options.pane,'runnerDots');assert.match(marker.tooltip,/Estimated location/);assert.ok(marker.tooltip.includes(LABEL));assert.ok(marker.options.title.includes(LABEL));
});
test('progress, route and selection changes refresh directions; unchanged icons are not rebuilt', () => {
  const {q,first,second,event,counts}=fixture();q.renderMarkers();const marker=q.state.markers.get(first.key),initial=counts();
  near(rotation(iconHtml(q)),0);q.renderMarkers();assert.equal(counts().icons,initial.icons);assert.equal(marker.iconWrites,0);assert.equal(counts().calls-initial.calls,1,'only selected runner computes direction');
  first.progress=.25;q.renderMarkers();near(rotation(iconHtml(q)),90);assert.equal(marker.iconWrites,1);
  first.progress=.8;q.renderMarkers();assert.equal(marker.iconWrites,1,'same segment need not rebuild');
  event.course={track_points:[point(0,0),point(0,-1)]};q.renderMarkers();near(rotation(iconHtml(q)),270);assert.equal(marker.iconWrites,2);
  q.state.selectedKey=second.key;q.renderMarkers();assert.equal(arrow(iconHtml(q,first.key)),'');assert.ok(arrow(iconHtml(q,second.key)));assert.doesNotMatch(marker.tooltip,/Course direction/);assert.doesNotMatch(marker.dom.title,/Course direction/);
  q.state.selectedKey=null;const before=counts().calls;q.renderMarkers();assert.equal(counts().calls,before);assert.equal(arrow(iconHtml(q,second.key)),'');
});
test('invalid refresh clears stale arrow and accessible labels without removing original located marker', () => {
  const {q,first,event}=fixture();
  for(const change of [{progress:null},{progress:1},{progress:'0.5'},{status:'FINISHED'},{source:'GPS',distance_source:null},{rank_exclusion:'GPS_AMBIGUOUS'}]) {
    Object.assign(first,position({progress:.1}));q.renderMarkers();assert.ok(arrow(iconHtml(q)));
    Object.assign(first,change);q.renderMarkers();assert.equal(arrow(iconHtml(q)),'');assert.match(iconHtml(q),/selected/);assert.doesNotMatch(q.state.markers.get(first.key).tooltip,/Course direction/);assert.doesNotMatch(q.state.markers.get(first.key).dom.title,/Course direction/);
  }
  Object.assign(first,position());q.renderMarkers();assert.ok(arrow(iconHtml(q)));event.course={...event.course,track_points:[point(0,0),null,point(1,0)]};q.renderMarkers();assert.equal(arrow(iconHtml(q)),'');
  first.lat=null;q.renderMarkers();assert.equal(q.state.markers.has(first.key),false);assert.ok(q.state.recordedMarker,'recorded evidence remains independent');
});
test('expanded Map, view switching and privacy refresh retain source truth without SVG identity or camera changes', () => {
  const {q,first}=fixture();const stable=()=>JSON.stringify([q.state.selectedKey,q.state.filter,q.state.finishEventId,q.state.manualLock,q.state.followSelected,q.state.directorMode]);const before=stable();
  for(const change of [{source:'ESTIMATED',distance_source:'SPLIT_ESTIMATE',freshness:'ESTIMATED',estimate_held:true},{source:'GPS',distance_source:'GPS_MATCHED',freshness:'LIVE',estimate_held:false},{source:'GPS',distance_source:'GPS_MATCHED',freshness:'STALE'}]) {
    Object.assign(first,change);q.renderMarkers();assert.match(iconHtml(q),new RegExp(`runner-pin ${first.source==='GPS'?'gps':'estimated'} ${first.freshness==='ESTIMATED'?'':first.freshness.toLowerCase()}`));
    const m=q.state.markers.get(first.key);assert.ok(m.tooltip.includes(first.source==='GPS'?'Measured GPS location':'Estimated location · held'));assert.ok(m.tooltip.includes(first.freshness));assert.ok(m.tooltip.includes(LABEL));
  }
  q.state.delivery='periodic_snapshot';q.setWorkspaceMode('display');q.renderMarkers();assert.match(q.state.markers.get(first.key).tooltip,/snapshot.*not live/i);assert.ok(q.state.markers.get(first.key).dom.title.includes(LABEL));
  q.setView('elevation');first.name='Anonymous';q.state.runners[0].name='Anonymous';q.renderMarkers();q.setView('map');q.setWorkspaceMode('split');q.renderMarkers();
  assert.equal(stable(),before);assert.doesNotMatch(q.state.markers.get(first.key).tooltip,/old/);assert.doesNotMatch(q.state.markers.get(first.key).dom.title,/old/);assert.doesNotMatch(q.state.markers.get(first.key).options.title,/old/);assert.doesNotMatch(arrow(iconHtml(q)),/QA|Anonymous|qa:1|bib/);assert.ok(arrow(iconHtml(q)).includes(LABEL));
});
test('malformed bearings cannot enter SVG transforms and arrow CSS leaves ring and hit target intact', () => {
  assert.equal((fs.readFileSync('index.html','utf8').match(/\?v=1\.9\.1/g)||[]).length,5);
  const {q,first}=fixture();
  for(const value of [null,undefined,NaN,Infinity,'90','0) rotate(999',{},[]]) assert.equal(arrow(q.markerIcon(first,true,value).html),'');
  assert.equal(arrow(q.markerIcon(first,false,90).html),'');assert.ok(arrow(q.markerIcon(first,true,90).html));
  const css=fs.readFileSync('styles.css','utf8'),rule=css.match(/\.course-direction\s*\{([^}]+)\}/)?.[1] || '';
  assert.match(rule,/position:\s*absolute/);assert.match(rule,/pointer-events:\s*none/);assert.match(rule,/transform-origin:\s*50% 50%/);assert.match(rule,/overflow:\s*visible/);
  assert.match(css,/\.course-direction path\s*\{[^}]*fill:\s*#ff3b30/);assert.match(css,/\.course-direction path\s*\{[^}]*stroke:\s*white/);
  assert.match(css,/\.runner-pin\.selected:after\s*\{[^}]*border:\s*3px solid #ff3b30/);assert.match(css,/\.runner-pin\.selected\.estimated:before\s*\{[^}]*border:\s*2px dashed white/);
});
