// Synthetic, in-memory workspace fixtures. No network or participant records.
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
function fixture(phone = true, deferredRAF = false) {
  const source = fs.readFileSync('app.js', 'utf8');
  const modified = source.replace('  window.addEventListener("DOMContentLoaded", init);', '  window.qa={state,el,setWorkspaceMode,syncWorkspaceLayout,handleWorkspaceEscape,setView,toggleFinishWatch,bindControls,updateSearchResults,stubSelection(fn){selectKey=fn;}};');
  const document = {activeElement:null,addEventListener(){}};
  const mediaListeners=[];
  const media={get matches(){return phone;},addEventListener(type,fn){if(type==='change')mediaListeners.push(fn);}};
  const window = {location:{hostname:'localhost',search:''},RutRules:require('./race-logic.js'),matchMedia:()=>media,addEventListener(){}};
  const frames=[];
  vm.runInNewContext(modified,{window,document,URLSearchParams,Map,Set,WeakMap,Date,Intl,console,setTimeout:fn=>fn(),requestAnimationFrame:fn=>deferredRAF?frames.push(fn):fn()});
  const q=window.qa;
  function node() {
    const classes=new Set();
    return {hidden:false,inert:false,open:false,textContent:'',innerHTML:'',dataset:{},attributes:{},scrollTop:75,
      classList:{toggle(k,on){const add=on??!classes.has(k);if(add)classes.add(k);else classes.delete(k);return add;},contains:k=>classes.has(k)},
      listeners:{},addEventListener(type,fn){this.listeners[type]=fn;},click(){this.listeners.click?.({target:this});},blur(){document.activeElement=null;},
      appendChild(child){child.parentElement=this;},insertBefore(child){child.parentElement=this;},
      setAttribute(k,v){this.attributes[k]=v;},focus(){document.activeElement=this;},contains(n){return n===this;}};
  }
  for(const id of [...fs.readFileSync('index.html','utf8').matchAll(/\bid="([^"]+)"/g)].map(m=>m[1]).concat(['infoHeading','viewControls'])) q.el[id]=node();
  q.el.infoHeading.appendChild(q.el.finishToggle);
  const sizes=[];
  q.state.map={_loaded:true,stop(){},invalidateSize:options=>sizes.push(options),setView:()=>assert.fail('layout must not reset map'),fitBounds:()=>assert.fail('layout must not fit map')};
  return {q,document,sizes,node,flushRAF:()=>{while(frames.length)frames.shift()();},setPhone:value=>{phone=value;},mediaChange:()=>mediaListeners.forEach(fn=>fn({matches:phone}))};
}
test('phone panes independently expand, return focus, and preserve director and map state',()=>{
  const {q,document,sizes}=fixture();
  Object.assign(q.state,{selectedKey:'qa:1',filter:'qa',finishEventId:'qa',manualLock:true,followSelected:false,directorMode:'MANUAL'});
  const preserved=()=>JSON.stringify([q.state.selectedKey,q.state.filter,q.state.finishEventId,q.state.manualLock,q.state.followSelected,q.state.directorMode,q.state.viewMode]);
  const before=preserved();
  for(const mode of ['info','display']) {
    q.setWorkspaceMode(mode);
    assert.equal(q.state.workspaceMode,mode);
    assert.equal(q.el.workspace.dataset.layout,mode);
    const active=mode==='info'?'info':'display',other=mode==='info'?'display':'info';
    assert.equal(q.el[other+'Pane'].hidden,true);assert.equal(q.el[other+'Pane'].inert,true);
    assert.equal(q.el[active+'Pane'].hidden,false);
    assert.equal(q.el[active+'ExpandButton'].textContent,'Back to split');
    assert.equal(document.activeElement,q.el[active+'ExpandButton']);
    q.setWorkspaceMode('split');
    assert.equal(document.activeElement,q.el[active+'ExpandButton']);
    assert.equal(q.el.infoPane.hidden,false);assert.equal(q.el.displayPane.inert,false);
    assert.equal(preserved(),before);assert.equal(q.el.infoContent.scrollTop,75);
  }
  assert.ok(sizes.length>0);assert.ok(sizes.every(options=>options.pan===false));
});
test('Escape respects the details dialog; desktop resize removes hidden/inert expansion',()=>{
  const {q,setPhone}=fixture();q.setWorkspaceMode('info');let prevented=0;
  const event={key:'Escape',preventDefault(){prevented++;}};
  q.el.detailsDialog.open=true;q.handleWorkspaceEscape(event);assert.equal(q.state.workspaceMode,'info');assert.equal(prevented,0);
  q.el.detailsDialog.open=false;q.handleWorkspaceEscape(event);assert.equal(q.state.workspaceMode,'split');assert.equal(prevented,1);
  q.setWorkspaceMode('display');setPhone(false);q.syncWorkspaceLayout();
  assert.equal(q.state.workspaceMode,'split');assert.equal(q.el.infoPane.hidden,false);assert.equal(q.el.infoPane.inert,false);
  q.setWorkspaceMode('info');assert.equal(q.state.workspaceMode,'split');
});
test('Leaflet resize restores the captured center without fitting or changing zoom',()=>{
  const {q,flushRAF}=fixture(true,true);const original={lat:45.25,lng:-111.41};let center=original,zoom=14.5,restores=0;
  q.state.map.getCenter=()=>center;
  q.state.map.invalidateSize=options=>{assert.equal(options.pan,false);center={lat:45.3,lng:-111.4};};
  q.state.map.panTo=(value,options)=>{assert.equal(options.animate,false);center=value;restores++;};
  for(const mode of ['display','split','info','split']) {q.setWorkspaceMode(mode);flushRAF();assert.deepEqual(center,original);assert.equal(zoom,14.5);}
  assert.ok(restores>0);
});
for(const movement of ['feed-follow','manual selection']) test(`queued workspace resize cannot overwrite newer ${movement} camera movement`,()=>{
  const {q,flushRAF}=fixture(true,true);
  let center={lat:45.25,lng:-111.41};
  q.state.map.getCenter=()=>center;
  q.state.map.panTo=value=>{center=value;};
  q.state.followSelected=movement==='feed-follow';
  q.setWorkspaceMode('display');
  const newest={lat:45.3,lng:-111.45};
  q.state.map.panTo(newest);
  flushRAF();
  assert.deepEqual(center,newest);
});
test('rapid workspace mode switches preserve the newest selection after queued frames',()=>{
  const {q,flushRAF}=fixture(true,true);
  let center={lat:45.25,lng:-111.41};
  q.state.map.getCenter=()=>center;
  q.state.map.panTo=value=>{center=value;};
  for(const mode of ['display','info','split','display','split']) {
    q.setWorkspaceMode(mode);
    q.state.map.panTo({lat:center.lat+0.01,lng:center.lng-0.01});
  }
  const newest={lat:45.3,lng:-111.45};
  q.state.selectedKey='qa:newest';q.state.map.panTo(newest);
  flushRAF();
  assert.deepEqual(center,newest);
  assert.equal(q.state.selectedKey,'qa:newest');
  assert.equal(q.state.workspaceMode,'split');
});
test('phone Finish watch belongs to Info and never replaces Map/Elevation',()=>{
  const {q}=fixture();q.setWorkspaceMode('display');q.toggleFinishWatch();
  assert.equal(q.el.app.classList.contains('show-finish'),true);assert.equal(q.state.viewMode,'map');
  assert.equal(q.el.finishToggle.textContent,'← Runner info');
  q.setView('elevation');assert.equal(q.el.app.classList.contains('show-finish'),true);
  assert.equal(q.el.elevationPanel.hidden,false);assert.equal(q.state.workspaceMode,'display');
  q.setView('map');assert.equal(q.el.app.classList.contains('show-finish'),true);
});
test('desktop Finish toggle has the original toolbar ancestry required by short elevation CSS',()=>{
  const {q}=fixture(false);q.syncWorkspaceLayout();
  assert.equal(q.el.finishToggle.parentElement,q.el.viewControls);
  const css=fs.readFileSync('styles.css','utf8');
  assert.match(css,/\.show-elevation \.view-controls \.finish-toggle\s*\{\s*display: block/);
});
test('single Finish toggle returns to original desktop toolbar and survives breakpoint change without resize',()=>{
  const {q,setPhone,mediaChange}=fixture();q.bindControls();
  const toggle=q.el.finishToggle;q.setWorkspaceMode('display');setPhone(false);mediaChange();
  assert.equal(q.el.infoPane.hidden,false);assert.equal(q.el.infoPane.inert,false);
  assert.equal(toggle.parentElement,q.el.viewControls);
  q.setView('elevation');assert.equal(toggle.parentElement,q.el.viewControls);
  setPhone(true);mediaChange();assert.equal(toggle.parentElement,q.el.infoHeading);
  assert.equal(q.el.finishToggle,toggle);
});
test('explicit search and Finish selections reveal phone runner stats only, preserving selection state',()=>{
  for(const phone of [true,false]) {
    const {q,node}=fixture(phone);q.bindControls();
    Object.assign(q.state,{filter:'all',viewMode:'elevation',selectedKey:'qa:1',finishEventId:'qa',manualLock:true,followSelected:false,directorMode:'MANUAL'});
    const before=JSON.stringify(q.state,(key,value)=>key==='map'?null:value);
    const calls=[];q.stubSelection((...args)=>{calls.push(args);return true;});
    const result=node();result.dataset.runnerKey='qa:1';
    q.state.runners=[{key:'qa:1',event_id:'qa',name:'Synthetic QA',bib:'1',course:'QA'}];
    q.el.searchResults.querySelectorAll=()=>[result];q.el.runnerSearch.value='Synthetic';
    q.updateSearchResults();q.el.infoContent.scrollTop=450;
    q.el.app.classList.toggle('show-finish',true);result.click();
    assert.equal(q.el.infoContent.scrollTop,phone?0:450);
    assert.equal(q.el.app.classList.contains('show-finish'),!phone);
    assert.equal(q.el.runnerCard.inert,false);
    q.el.app.classList.toggle('show-finish',false);q.el.infoContent.scrollTop=350;
    q.el.finishList.listeners.click({target:{closest:()=>({dataset:{finishKey:'qa:1'}})}});
    assert.equal(q.el.infoContent.scrollTop,phone?0:350);
    assert.deepEqual(calls,[['qa:1',true,'MANUAL'],['qa:1',true,'FINISH WATCH']]);
    q.state.runners=[];
    assert.equal(JSON.stringify(q.state,(key,value)=>key==='map'?null:value),before);
    q.el.infoContent.scrollTop=225;q.syncWorkspaceLayout();assert.equal(q.el.infoContent.scrollTop,225);
  }
});
test('markup has single info/display identity trees, external dialog and versioned frontend',()=>{
  const html=fs.readFileSync('index.html','utf8'),css=fs.readFileSync('styles.css','utf8');
  const ids=[...html.matchAll(/\bid="([^"]+)"/g)].map(m=>m[1]);assert.equal(new Set(ids).size,ids.length);
  const info=html.slice(html.indexOf('id="infoPane"'),html.indexOf('id="displayPane"'));
  for(const id of ['courseBar','runnerSearch','runnerCard','finishPanel','checkpointButton','guideButton','finishToggle']) assert.ok(info.includes(`id="${id}"`),id+' must be in info');
  assert.ok(info.indexOf('id="runnerCard"')<info.indexOf('id="runnerSearch"'),'runner stats precede controls');
  const display=html.slice(html.indexOf('id="displayPane"'),html.indexOf('<dialog'));
  for(const id of ['mapCanvas','elevationPanel','mapViewButton','elevationViewButton','displayExpandButton']) assert.ok(display.includes(`id="${id}"`),id+' must be in display');
  assert.doesNotMatch(display,/id="(?:runnerCard|controlPanel|finishPanel|runnerSearch)"/);
  assert.match(html,/styles\.css\?v=1\.9\.1/);assert.match(html,/app\.js\?v=1\.9\.1/);assert.match(html,/rut-2026-aid-chart\.png/);
  assert.match(css,/grid-template-rows:\s*minmax\(0,\s*1fr\) minmax\(0,\s*1fr\)/);
  assert.match(css,/@media[^\{]*max-width: 1000px[^\{]*max-height: 560px/);
  assert.match(css,/\.info-content[^\{]*\{[^}]*overflow-y:\s*auto/s);
  const tooltipRule=css.match(/\.leaflet-tooltip\.runner-tooltip\s*\{([^}]+)\}/)?.[1] || '';
  assert.match(tooltipRule,/width:\s*max-content/,'Leaflet tooltip pane needs an explicit intrinsic width before wrapping');
  assert.match(tooltipRule,/max-width:[^;]*100vw/,'wrapped labels stay viewport-bounded');
  assert.match(tooltipRule,/white-space:\s*normal/);
});
