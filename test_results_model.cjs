'use strict';

const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const modelPath = require.resolve('./results_model.js');
const model = require(modelPath);

test('exports through CommonJS without polluting its global and still exposes a browser global', () => {
  delete require.cache[modelPath];
  delete globalThis.CourseSignalResultsModel;

  const commonJsModel = require(modelPath);
  assert.equal(typeof commonJsModel.weightKg, 'function');
  assert.equal(globalThis.CourseSignalResultsModel, undefined);

  const browserGlobal = {};
  vm.runInNewContext(fs.readFileSync(modelPath, 'utf8'), browserGlobal);
  assert.equal(typeof browserGlobal.CourseSignalResultsModel.weightKg, 'function');
  assert.equal(browserGlobal.CourseSignalResultsModel.weightKg(80, 'kg'), 80);
});

test('weightKg accepts pounds and kilograms without coercing empty values', () => {
  assert.equal(model.weightKg('176.37', 'lb').toFixed(3), '80.000');
  assert.equal(model.weightKg(80, 'kg'), 80);
  for (const value of [null, undefined, '', '   ', false, 0, -1, 'runner']) {
    assert.equal(model.weightKg(value, 'lb'), null);
  }
  assert.equal(model.weightKg(1103, 'lb'), null);
  assert.equal(model.weightKg(501, 'kg'), null);
});

test('personalizeEnergy scales active energy linearly and does not mutate analysis', () => {
  const analysis = {
    totals: {energy_kcal_per_kg: {low: 20, estimate: 25, high: 30}},
    sections: [
      {name: 'Start → Ridge', energy_kcal_per_kg: {low: 8, estimate: 10, high: 12}},
      {name: 'Ridge → Finish', energy_kcal_per_kg: {low: 12, estimate: 15, high: 18}},
    ],
  };
  const original = JSON.stringify(analysis);
  const personalized = model.personalizeEnergy(analysis, 80, 'kg');
  assert.deepEqual(personalized.total, {low: 1600, estimate: 2000, high: 2400});
  assert.deepEqual(personalized.sections.map((row) => row.energy), [
    {low: 640, estimate: 800, high: 960},
    {low: 960, estimate: 1200, high: 1440},
  ]);
  assert.equal(JSON.stringify(analysis), original);
});

test('personalizeEnergy fails closed for missing terrain energy or invalid weight', () => {
  assert.deepEqual(model.personalizeEnergy({totals: {}, sections: []}, 150, 'lb'), {
    status: 'unavailable', reason: 'energy_model_unavailable', total: null, sections: [],
  });
  assert.deepEqual(model.personalizeEnergy({totals: {energy_kcal_per_kg: {low: 1, estimate: 2, high: 3}}, sections: []}, '', 'lb'), {
    status: 'invalid', reason: 'invalid_weight', total: null, sections: [],
  });
});

test('energy range rejects malformed and non-monotonic factors', () => {
  assert.equal(model.scaleEnergyRange({low: 1, estimate: 2, high: 3}, 70).estimate, 140);
  for (const factors of [null, {}, {low: '', estimate: 2, high: 3}, {low: 3, estimate: 2, high: 4}, {low: 1, estimate: 4, high: 3}, {low: -1, estimate: 2, high: 3}]) {
    assert.equal(model.scaleEnergyRange(factors, 70), null);
  }
});

test('scaleEnergyRange preserves exact linearity for fractional factors and mass', () => {
  const factors = {low: 0.37, estimate: 0.91, high: 1.46};
  const mass = 72.5;
  assert.deepEqual(model.scaleEnergyRange(factors, mass), {
    low: factors.low * mass,
    estimate: factors.estimate * mass,
    high: factors.high * mass,
  });
});

test('route privacy gate accepts only the same public app origin and pathname', () => {
  assert.equal(model.isPrivacySafeRoute('/?race=the-rut&event=the-rut-28k-2026&mode=results&runner=123'), true);
  for (const url of [
    '?event=x',
    './?event=x&mode=live',
    'https://course-signal.local/?event=x&mode=results',
  ]) assert.equal(model.isPrivacySafeRoute(url), true, url);

  const githubPagesBase = 'https://benjibrucker.github.io/course-signal/';
  for (const url of [
    '?event=x',
    './?event=x&mode=live',
    'https://benjibrucker.github.io/course-signal/?event=x&mode=results',
  ]) assert.equal(model.isPrivacySafeRoute(url, githubPagesBase), true, url);

  for (const url of [
    'javascript:alert(document.domain)?event=x',
    'data:text/html,<script>alert(1)</script>?event=x',
    'https://evil.example/?event=x',
    '//evil.example/?event=x',
    'https://Alex:private@course-signal.local/?event=x',
    '/private/Alex?event=x',
    'https://course-signal.local:444/?event=x',
  ]) assert.equal(model.isPrivacySafeRoute(url), false, url);

  for (const url of [
    '/?event=x&weight=176', '/?event=x&weight_kg=80', '/?event=x&calories=2000',
    '/?event=x&name=Alex', '/?event=x&bib=42', '/?event=x&q=Alex', '/?event=x&search=Alex',
    '/?event=x&runner=abc&extra=1',
  ]) assert.equal(model.isPrivacySafeRoute(url), false, url);

  for (const url of [
    'https://benjibrucker.github.io/?event=x',
    'https://benjibrucker.github.io/private/Alex?event=x',
    'https://benjibrucker.github.io/course-signal/Alex?event=x',
    'https://benjibrucker.github.io:444/course-signal/?event=x',
  ]) assert.equal(model.isPrivacySafeRoute(url, githubPagesBase), false, url);

  for (const baseUrl of [
    'javascript:alert(1)',
    'data:text/html,course-signal',
    'https://Alex:private@course-signal.local/',
    'https://course-signal.local/#private',
    '/course-signal/',
  ]) assert.equal(model.isPrivacySafeRoute('?event=x', baseUrl), false, baseUrl);
});

test('route privacy gate rejects duplicate fields, invalid modes, malformed identifiers, and fragments', () => {
  for (const url of [
    '/?event=x&event=y',
    '/?event=x&race=a&race=b',
    '/?event=x&mode=live&mode=results',
    '/?event=x&runner=1&runner=2',
    '/?event=x&mode=preview',
    '/?event=',
    '/?event=-x',
    '/?event=x%20y',
    '/?event=x/y',
    '/?event=%E2%98%83',
    '/?event=x#details',
    '/?event=x#',
  ]) assert.equal(model.isPrivacySafeRoute(url), false, url);
});

test('formatRange rounds for display without claiming false precision', () => {
  assert.equal(model.formatRange({low: 1234.4, estimate: 1501, high: 1789.6}), '1,230–1,790 active kcal');
  assert.equal(model.formatRange(null), 'Unavailable');
});
