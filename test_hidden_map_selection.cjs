// Synthetic in-memory records only; exercise the real selectKey, never network.
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');

function fixture() {
  const source = fs.readFileSync('app.js', 'utf8').replace(
    '  window.addEventListener("DOMContentLoaded", init);',
    `  renderMarkers = updateRunnerCard = renderFinishWatch = renderElevation = () => {};
       window.qa = {state, el, selectKey, setWorkspaceMode, setView};`
  );
  const document = {activeElement: null};
  const window = {location: {hostname: 'localhost', search: ''}, RutRules: require('./race-logic.js'), matchMedia: () => ({matches: true})};
  vm.runInNewContext(source, {window, document, URLSearchParams, Map, Set, WeakMap, Date, Intl, console, setTimeout: fn => fn()});
  const q = window.qa;
  const node = () => ({hidden: false, inert: false, dataset: {}, classList: {contains: () => false, toggle() {}}, setAttribute() {}, focus() {}});
  for (const id of ['app', 'workspace', 'infoPane', 'displayPane', 'infoExpandButton', 'displayExpandButton', 'infoContent', 'finishPanel', 'runnerCard', 'finishToggle', 'mapCanvas', 'mapViewButton', 'elevationViewButton', 'elevationPanel', 'elevationChart', 'elevationRunner', 'elevationSubtitle']) q.el[id] = node();
  let dimensions = {x: 640, y: 480}, center = {lat: 45, lng: -111}, zoom = 13;
  const size = () => q.el.displayPane.hidden || q.state.viewMode === 'elevation' ? {x: 0, y: 0} : dimensions;
  Object.defineProperties(q.el.mapCanvas, {clientWidth: {get: () => size().x}, clientHeight: {get: () => size().y}});
  const calls = [];
  const move = (target, nextZoom) => {center = {lat: target[0], lng: target[1]}; zoom = nextZoom;};
  q.state.map = {
    _loaded: true, stop() {}, getSize: size, getCenter: () => center, getZoom: () => zoom,
    setView(target, nextZoom, options) {assert.equal(options.animate, false); calls.push('setView'); move(target, nextZoom);},
    flyTo(target, nextZoom, options) {
      if (!size().x || !size().y) throw new Error('Invalid LatLng object: (NaN, NaN)');
      assert.equal(options.duration, 1.1); calls.push('flyTo'); move(target, nextZoom);
    },
    invalidateSize(options) {assert.equal(options.pan, false); center = {lat: 0, lng: 0};},
    panTo(target, options) {assert.equal(options.animate, false); center = target;},
  };
  q.state.filter = 'qa';
  for (const [key, lat, lng] of [['qa:1', 45.1, -111.1], ['qa:2', 45.2, -111.2]]) q.state.positions.set(key, {key, event_id: 'qa', name: 'Synthetic QA', lat, lng});
  return {q, calls, setDimensions: value => {dimensions = value;}, setZoom: value => {zoom = value;}};
}

for (const manual of [true, false]) test(`hidden Info ${manual ? 'manual' : 'Auto'} selection updates the camera and preserves newest target on reveal`, () => {
  const {q, calls} = fixture();
  q.setWorkspaceMode('info');
  assert.deepEqual(q.state.map.getSize(), {x: 0, y: 0});
  const mode = manual ? 'MANUAL' : 'AUTO · FRONT';
  for (const key of ['qa:1', 'qa:2', 'qa:1', 'qa:2']) {
    q.state.followSelected = false;
    assert.equal(q.selectKey(key, manual, mode), true);
    assert.equal(q.state.selectedKey, key);
    assert.equal(q.state.finishEventId, 'qa');
    assert.equal(q.state.manualLock, manual);
    assert.equal(q.state.followSelected, true);
    assert.equal(q.state.directorMode, mode);
    if (!manual) assert.ok(q.state.directorDeadline > Date.now());
    assert.deepEqual(q.state.map.getCenter(), {lat: q.state.positions.get(key).lat, lng: q.state.positions.get(key).lng});
  }
  assert.deepEqual(calls, ['setView', 'setView', 'setView', 'setView']);
  q.setWorkspaceMode('split');
  assert.deepEqual(q.state.map.getCenter(), {lat: 45.2, lng: -111.2});
  assert.equal(q.state.map.getZoom(), 14.5);
  assert.equal(q.state.selectedKey, 'qa:2');
  assert.equal(q.state.manualLock, manual);
  assert.equal(q.state.followSelected, true);
  assert.equal(q.state.directorMode, mode);
});

for (const dimensions of [{x: 0, y: 480}, {x: 640, y: 0}, {x: 0, y: 0}]) test(`zero-size map ${dimensions.x}x${dimensions.y} selects without animation`, () => {
  const {q, calls, setDimensions} = fixture();
  setDimensions(dimensions);
  assert.equal(q.selectKey('qa:2'), true);
  assert.deepEqual(calls, ['setView']);
  setDimensions({x: 640, y: 480});
  q.setWorkspaceMode('display');
  assert.deepEqual(q.state.map.getCenter(), {lat: 45.2, lng: -111.2});
  assert.equal(q.state.map.getZoom(), 14.5);
});

test('visible map keeps flyTo and selection zoom bounds', () => {
  const {q, calls, setZoom} = fixture();
  for (const [initial, expected] of [[13, 14.5], [15, 15], [18, 16]]) {
    setZoom(initial);
    assert.equal(q.selectKey('qa:1'), true);
    assert.equal(q.state.map.getZoom(), expected);
  }
  assert.deepEqual(calls, ['flyTo', 'flyTo', 'flyTo']);
});

test('elevation selection remains nonanimated and returns to the selected map center', () => {
  const {q, calls, setZoom} = fixture();
  setZoom(15);
  q.setView('elevation');
  assert.equal(q.selectKey('qa:2'), true);
  assert.deepEqual(calls, ['setView']);
  q.setView('map');
  assert.deepEqual(q.state.map.getCenter(), {lat: 45.2, lng: -111.2});
  assert.equal(q.state.map.getZoom(), 15);
});
