'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const {buildProfile, atProgress, checkpointPoints, runnerPoint} = require('./elevation-profile.js');

const point = (lng, ele, lat = 0) => ({lat, lng, ele});
const course = () => ({track_points: [point(0, 100), point(1, 200), point(3, 0)]});
const near = (actual, expected) => assert.ok(Math.abs(actual - expected) < 1e-7, `${actual} != ${expected}`);
const invalidNumbers = [null, undefined, true, false, '', '1', {}, [], NaN, Infinity, -Infinity];

function freeze(value) {
  if (value && typeof value === 'object') {
    Object.values(value).forEach(freeze);
    Object.freeze(value);
  }
  return value;
}

test('CommonJS and browser global expose exactly the four pure functions', () => {
  const context = vm.createContext({});
  vm.runInContext(fs.readFileSync(require.resolve('./elevation-profile.js'), 'utf8'), context);
  assert.deepEqual(Object.keys(context.RutElevation).sort(), ['atProgress', 'buildProfile', 'checkpointPoints', 'runnerPoint']);
  assert.equal(typeof context.RutElevation.buildProfile, 'function');
});

test('profile preserves all elevations/endpoints and great-circle horizontal distance', () => {
  const result = buildProfile(course());
  const degree = 6371000 * Math.PI / 180;
  assert.equal(result.points.length, 3);
  assert.deepEqual(result.points.map(p => p.elevation_m), [100, 200, 0]);
  near(result.points[1].distance_m, degree);
  near(result.total_m, 3 * degree);
  assert.equal(result.min_m, 0);
  assert.equal(result.max_m, 200);
  assert.deepEqual(atProgress(result, 0), {distance_m: 0, elevation_m: 100});
  assert.deepEqual(atProgress(result, 1), {distance_m: result.total_m, elevation_m: 0});
});

test('interpolation is by cumulative distance, not point index', () => {
  const profile = buildProfile(course());
  near(atProgress(profile, 0.5).elevation_m, 150);
  near(atProgress(profile, 1 / 6).elevation_m, 150);
  near(atProgress(profile, 0.5).distance_m, profile.total_m / 2);
});

test('great-circle distances handle latitude, antimeridian, and antipodes', () => {
  const high = buildProfile({track_points: [point(0, 0, 60), point(90, 100, 60)]});
  near(high.total_m, 6371000 * Math.acos(0.75));
  const crossing = buildProfile({track_points: [point(179, 0), point(-179, 0)]});
  near(crossing.total_m, 6371000 * Math.PI / 90);
  assert.ok(Number.isFinite(buildProfile({track_points: [point(0, 0), point(180, 0)]}).total_m));
});

test('missing, short, sparse, and all-zero-length tracks are rejected', () => {
  for (const input of [null, {}, {track_points: null}, {track_points: []},
    {track_points: [point(0, 0)]}, {track_points: [point(0, 0), point(0, 100)]},
    {track_points: [point(0, 0), , point(1, 1)]}]) assert.equal(buildProfile(input), null);
});

test('raw numeric guards reject coercion and any corrupt point without bridging', () => {
  for (const key of ['lat', 'lng', 'ele']) {
    for (const value of invalidNumbers) {
      const input = course();
      input.track_points[1][key] = value;
      assert.equal(buildProfile(input), null, `${key}: ${String(value)}`);
    }
  }
  for (const bad of [null, false, {}, [], {lat: 91, lng: 1, ele: 0},
    {lat: -91, lng: 1, ele: 0}, {lat: 0, lng: 181, ele: 0}, {lat: 0, lng: -181, ele: 0}]) {
    assert.equal(buildProfile({track_points: [point(0, 0), bad, point(3, 0)]}), null);
  }
  assert.ok(buildProfile({track_points: [point(-180, -20, -90), point(180, 0, 90)]}));
});

test('elevation bounds cover Earth terrain but reject finite overflow inputs', () => {
  const boundary = {track_points: [point(0, -12000), point(1, 12000)]};
  assert.ok(buildProfile(boundary));
  near(atProgress(buildProfile(boundary), 0.5).elevation_m, 0);
  for (const ele of [12001, -12001, 1e308, -1e308, Number.MAX_VALUE]) {
    const input = course();input.track_points[1].ele = ele;
    assert.equal(buildProfile(input), null, String(ele));
  }
});

test('duplicate coordinates preserve geometry and cannot divide by zero', () => {
  const input = {track_points: [point(0, 10), point(0, 20), point(1, 30), point(1, 40), point(2, 50), point(2, 60)]};
  const profile = buildProfile(input);
  assert.equal(profile.points.length, 6);
  assert.equal(profile.points[0].distance_m, profile.points[1].distance_m);
  assert.equal(atProgress(profile, 0).elevation_m, 10);
  assert.equal(atProgress(profile, 1).elevation_m, 60);
  assert.equal(atProgress(profile, 0.5).elevation_m, 30);
  near(atProgress(profile, 0.25).elevation_m, 25);
  near(atProgress(profile, 0.75).elevation_m, 45);
});

test('progress must be a raw finite number in [0,1], never defaulted or clamped', () => {
  const profile = buildProfile(course());
  for (const progress of [...invalidNumbers, -0.001, 1.001]) assert.equal(atProgress(profile, progress), null);
  for (const profile of [null, {}, {points: [], total_m: 1}, {points: [{distance_m: 0, elevation_m: 1}], total_m: 0}]) {
    assert.equal(atProgress(profile, 0.5), null);
  }
});

test('checkpoint indexes follow splitNames, not filtered split_points', () => {
  const input = {...course(), progress_points: [0, null, 0.5, 1], split_points: [{name: 'WRONG', progress: 0.9}]};
  const profile = buildProfile(input);
  const markers = checkpointPoints(profile, input, ['Start', 'Missing', 'Aid', 'Finish']);
  assert.deepEqual(markers.map(({index, name, progress}) => ({index, name, progress})), [
    {index: 0, name: 'Start', progress: 0}, {index: 2, name: 'Aid', progress: 0.5}, {index: 3, name: 'Finish', progress: 1}
  ]);
  near(markers[1].elevation_m, 150);
  near(markers[1].distance_m, profile.total_m / 2);
});

test('checkpoint missing/invalid/sparse progress skips only that original index', () => {
  const profile = buildProfile(course());
  const input = {progress_points: [0, , false, '0.5', 2, NaN, 1, 0.4]};
  assert.deepEqual(checkpointPoints(profile, input, ['A', 'B', 'C', 'D', 'E', 'F', 'G']).map(p => p.index), [0, 6]);
  assert.deepEqual(checkpointPoints(profile, {progress_points: [0]}, ['A', 'B']).map(p => p.index), [0]);
  assert.deepEqual(checkpointPoints(profile, {}, ['A']), []);
  assert.deepEqual(checkpointPoints(profile, input, null), []);
  assert.deepEqual(checkpointPoints(null, input, ['A']), []);
});

test('runner mapping requires explicit authoritative GPS match or split estimate', () => {
  const profile = buildProfile(course());
  for (const [source, distance_source] of [['GPS', 'GPS_MATCHED'], ['ESTIMATED', 'SPLIT_ESTIMATE']]) {
    const result = runnerPoint(profile, {source, distance_source, progress: 0.5});
    near(result.elevation_m, 150);
    near(result.distance_m, profile.total_m / 2);
    assert.equal(result.progress, 0.5);
  }
  for (const position of [null, {}, {source: 'GPS', progress: 0.5, lat: 0, lng: 1},
    {source: 'GPS', distance_source: 'GPS_UNMATCHED', progress: 0.5},
    {source: 'GPS', distance_source: 'SPLIT_ESTIMATE', progress: 0.5},
    {source: 'ESTIMATED', distance_source: 'GPS_MATCHED', progress: 0.5},
    {source: 'OTHER', distance_source: 'SPLIT_ESTIMATE', progress: 0.5}]) assert.equal(runnerPoint(profile, position), null);
});

test('runner mapping rejects absent/invalid progress including null instead of fabricating start', () => {
  const profile = buildProfile(course());
  for (const progress of [...invalidNumbers, -0.1, 1.1]) {
    assert.equal(runnerPoint(profile, {source: 'GPS', distance_source: 'GPS_MATCHED', progress}), null);
  }
  assert.equal(runnerPoint(null, {source: 'GPS', distance_source: 'GPS_MATCHED', progress: 0}), null);
});

test('stale GPS and held estimates plot supplied frozen progress without clock advancement', () => {
  const profile = buildProfile(course());
  for (const position of [
    {source: 'GPS', distance_source: 'GPS_MATCHED', progress: 0.25, freshness: 'STALE', recorded_at: '2000-01-01T00:00:00Z'},
    {source: 'ESTIMATED', distance_source: 'SPLIT_ESTIMATE', progress: 0.25, estimate_held: true, estimate_overdue: true}
  ]) {
    freeze(position);
    const expected = {...atProgress(profile, 0.25), progress: 0.25};
    assert.deepEqual(runnerPoint(profile, position), expected);
    assert.deepEqual(runnerPoint(profile, position), expected);
  }
});

test('inputs are not mutated and returned points do not alias profile geometry', () => {
  const input = freeze({...course(), progress_points: [0, 0.5, 1]});
  const profile = freeze(buildProfile(input));
  const result = atProgress(profile, 0);
  result.elevation_m = -999;
  assert.equal(profile.points[0].elevation_m, 100);
  assert.equal(checkpointPoints(profile, input, freeze(['Start', 'Aid', 'Finish'])).length, 3);
  assert.ok(runnerPoint(profile, freeze({source: 'GPS', distance_source: 'GPS_MATCHED', progress: 1})));
});
