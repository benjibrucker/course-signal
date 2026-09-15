'use strict';

const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const ResultsModel = require('./results_model.js');

let View = {};
try {
  View = require('./results_view.js');
} catch (_error) {
  // RED starts with the production module absent; each contract still reports separately.
}

class FakeElement {
  constructor(tagName = 'div', id = '') {
    this.tagName = String(tagName).toUpperCase();
    this.id = id;
    this.children = [];
    this.attributes = new Map();
    this.dataset = {};
    this.hidden = false;
    this.value = '';
    this.className = '';
    this.listeners = new Map();
    this.selectedIndex = 0;
    this._textContent = '';
    Object.defineProperty(this, 'innerHTML', {
      get() { return ''; },
      set() { throw new Error('Unsafe HTML insertion attempted'); },
    });
  }
  set textContent(value) {
    this._textContent = String(value ?? '');
    this.children = [];
  }
  get textContent() {
    return this._textContent + this.children.map((child) => child.textContent || '').join('');
  }
  appendChild(child) { this.children.push(child); return child; }
  append(...children) { children.forEach((child) => this.appendChild(child)); }
  replaceChildren(...children) { this._textContent = ''; this.children = [...children]; }
  setAttribute(name, value) {
    this.attributes.set(name, String(value));
    if (name === 'class') this.className = String(value);
  }
  getAttribute(name) { return this.attributes.get(name) ?? null; }
  removeAttribute(name) { this.attributes.delete(name); }
  addEventListener(type, handler) { this.listeners.set(type, handler); }
  removeEventListener(type, handler) {
    if (this.listeners.get(type) === handler) this.listeners.delete(type);
  }
  dispatch(type, extra = {}) {
    const event = {preventDefault() {}, currentTarget: this, target: this, ...extra};
    return this.listeners.get(type)?.(event);
  }
  focus() { this.focused = true; }
  select() { this.selected = true; }
}

class FakeDocument {
  constructor(ids = []) {
    this.nodes = new Map(ids.map((id) => [id, new FakeElement('div', id)]));
    this.body = new FakeElement('body', 'body');
  }
  getElementById(id) { return this.nodes.get(id) || null; }
  createElement(tagName) { return new FakeElement(tagName); }
  createElementNS(_namespace, tagName) { return new FakeElement(tagName); }
}

function coordinates(points) {
  return points.trim().split(/\s+/).map((pair) => pair.split(',').map(Number));
}

const RESULT_NODE_IDS = [
  'resultsSearchForm', 'resultsRunnerSearch', 'resultsSearchSubmit', 'resultsStatus',
  'resultsMatches', 'resultsRunnerLead', 'resultsDetail', 'resultsRaceName', 'resultsEventName',
  'resultsEventMeta', 'resultsCapabilities',
];

function descendants(node) {
  return (node && node.children || []).flatMap((child) => [child, ...descendants(child)]);
}

function byClass(node, className) {
  return descendants(node).filter((child) => String(child.className).split(/\s+/).includes(className));
}

const FULL_EVENT_CAPABILITIES = Object.freeze({
  results: true,
  intermediate_splits: true,
  course_map: true,
  elevation: true,
  live_gps: false,
});

function resultPayload({
  runner = {},
  sections,
  summary = {},
  totals = {},
  capabilities = {},
  eventCapabilities = FULL_EVENT_CAPABILITIES,
} = {}) {
  return {
    status: 'ok',
    stale: false,
    race: {slug: 'race-1', name: 'Test Race'},
    event: {id: 'evt-1', label: 'Test Event', capabilities: {...eventCapabilities}},
    analysis: {
      runner: {
        id: 42,
        name: 'Ada Runner',
        bib: '42',
        age: 36,
        gender: 'X',
        age_group: 'X 30-39',
        age_group_place: 2,
        gender_place: 3,
        status: 'FINISHED',
        finish_seconds: 1800,
        finish_place: 3,
        last_recorded_split_index: 3,
        last_recorded_split_name: 'Finish',
        ...runner,
      },
      sections: sections || [
        {start_index: 0, start_name: 'Start', end_index: 1, end_name: 'Aid One', status: 'recorded', segment_seconds: 400, cumulative_seconds: 400, field_count: 4, field_percentile: 50},
        {start_index: 1, start_name: 'Aid One', end_index: 2, end_name: 'Aid Two', status: 'recorded', segment_seconds: 600, cumulative_seconds: 1000, field_count: 4, field_percentile: 50},
        {start_index: 2, start_name: 'Aid Two', end_index: 3, end_name: 'Finish', status: 'recorded', segment_seconds: 800, cumulative_seconds: 1800, field_count: 4, field_percentile: 75},
      ],
      summary: {
        comparison_cohort: 'overall_field',
        valid_section_count: 3,
        ...summary,
      },
      totals: {recorded_section_count: 3, ...totals},
      capabilities: {splits: true, field_comparison: true, terrain: false, active_energy: false, ...capabilities},
      limitations: [],
    },
  };
}

function fullySupportedResultPayload() {
  const perSection = {
    active_energy_kcal_per_kg: 10,
    active_energy_communication_range_kcal_per_kg: {lower: 8, upper: 12},
  };
  const payload = resultPayload({
    sections: [
      {
        start_index: 0, start_name: 'Start', end_index: 1, end_name: 'Aid One', status: 'recorded',
        segment_seconds: 400, cumulative_seconds: 400, cumulative_place: 3, segment_place: 2,
        field_count: 4, field_percentile: 50, age_group_count: 2, age_group_percentile: 25, rank_change: 1,
        pace_seconds_per_mile: 500, distance_m: 1000, gain_m: 100, loss_m: 20, net_grade: 0.08,
        ...perSection,
      },
      {
        start_index: 1, start_name: 'Aid One', end_index: 2, end_name: 'Aid Two', status: 'recorded',
        segment_seconds: 600, cumulative_seconds: 1000, cumulative_place: 2, segment_place: 2,
        field_count: 4, field_percentile: 60, age_group_count: 2, age_group_percentile: 50, rank_change: 1,
        pace_seconds_per_mile: 510, distance_m: 2000, gain_m: 200, loss_m: 40, net_grade: 0.08,
        ...perSection,
      },
      {
        start_index: 2, start_name: 'Aid Two', end_index: 3, end_name: 'Finish', status: 'recorded',
        segment_seconds: 800, cumulative_seconds: 1800, cumulative_place: 3, segment_place: 4,
        field_count: 4, field_percentile: 75, age_group_count: 2, age_group_percentile: 50, rank_change: -1,
        pace_seconds_per_mile: 520, distance_m: 3000, gain_m: 300, loss_m: 60, net_grade: 0.08,
        ...perSection,
      },
    ],
    summary: {
      comparison_cohort: 'overall_field',
      overall_field_count: 4,
      valid_section_count: 3,
      strongest_comparable_section: {
        start_index: 2, start_name: 'Aid Two', end_index: 3, end_name: 'Finish', field_percentile: 75,
      },
      weakest_comparable_section: {
        start_index: 0, start_name: 'Start', end_index: 1, end_name: 'Aid One', field_percentile: 50,
      },
      consistency: {
        status: 'available', comparable_section_count: 3, percentile_range: 25, mean_percentile: 61.67,
      },
      trend: {
        status: 'available', comparable_section_count: 3,
        percentile_points_per_section: 12.5, direction: 'higher_relative_percentiles_later',
      },
    },
    totals: {
      recorded_section_count: 3,
      active_energy_kcal_per_kg: 30,
      active_energy_communication_range_kcal_per_kg: {lower: 24, upper: 36},
    },
    capabilities: {splits: true, field_comparison: true, age_group_comparison: true, terrain: true, active_energy: true},
  });
  payload.event.course = {track_points: [
    {lat: 45, lng: -111, ele: 1000},
    {lat: 45.01, lng: -111.01, ele: 1200},
  ], progress_points: [0, 0.3, 0.65, 1]};
  return payload;
}

function searchPayload(matches = [], overrides = {}) {
  return {
    status: 'ok',
    event_id: 'evt-1',
    stale: false,
    matches,
    ...overrides,
  };
}

function resultRoot(payload, runner = '42') {
  const document = new FakeDocument(RESULT_NODE_IDS);
  const calls = [];
  const root = {
    document,
    location: {
      href: `https://course.test/?race=race-1&event=evt-1&mode=results&runner=${runner}`,
      origin: 'https://course.test',
      pathname: '/',
    },
    CourseSignalShell: {
      route: {kind: 'race', race: 'race-1', event: 'evt-1', mode: 'results', explicitMode: true},
    },
    fetch: async (url) => {
      calls.push(url);
      return payload
        ? jsonResponse(payload)
        : jsonResponse({status: 'error'}, 503);
    },
    history: {replaceState() { throw new Error('Direct load must not rewrite history'); }},
    navigator: {},
  };
  return {root, document, calls};
}

function deferred() {
  let resolve;
  let reject;
  const promise = new Promise((onResolve, onReject) => {
    resolve = onResolve;
    reject = onReject;
  });
  return {promise, resolve, reject};
}

function jsonResponse(payload, status = 200) {
  const text = typeof payload === 'string' ? payload : JSON.stringify(payload);
  return new Response(text, {
    status,
    headers: {'content-type': 'application/json'},
  });
}

function interactiveRoot(fetchImpl) {
  const document = new FakeDocument(RESULT_NODE_IDS);
  const historyWrites = [];
  const root = {
    document,
    location: {
      href: 'https://course.test/results/?race=race-1&event=evt-1&mode=results',
      origin: 'https://course.test',
      pathname: '/results/',
    },
    CourseSignalShell: {
      route: {kind: 'race', race: 'race-1', event: 'evt-1', mode: 'results', explicitMode: true},
    },
    fetch: fetchImpl,
    history: {replaceState(_state, _title, value) { historyWrites.push(value); }},
    navigator: {},
    AbortController,
  };
  return {root, document, historyWrites};
}

function flushAsync() {
  return new Promise((resolve) => setImmediate(resolve));
}

test('CommonJS import stays isolated from browser-like globals and DOM listeners', () => {
  const viewPath = require.resolve('./results_view.js');
  const hadWindow = Object.prototype.hasOwnProperty.call(globalThis, 'window');
  const previousWindow = globalThis.window;
  const listeners = [];
  const browserLikeWindow = {
    document: {},
    addEventListener(type, listener) { listeners.push({type, listener}); },
  };

  try {
    delete require.cache[viewPath];
    globalThis.window = browserLikeWindow;
    const isolatedView = require(viewPath);
    assert.equal(typeof isolatedView.initBrowser, 'function');
    assert.equal(Object.prototype.hasOwnProperty.call(browserLikeWindow, 'CourseSignalResultsView'), false);
    assert.deepEqual(listeners, []);
  } finally {
    if (hadWindow) globalThis.window = previousWindow;
    else delete globalThis.window;
    delete require.cache[viewPath];
  }
});

test('result endpoints are fixed, encoded, bounded, and reject unsafe identifiers', () => {
  assert.equal(
    View.buildSearchEndpoint('evt-2026', '  Ada  Runner &  '),
    '/api/results/search?event_id=evt-2026&q=Ada+Runner+%26&limit=20',
  );
  assert.equal(
    View.buildRunnerEndpoint('evt-2026', '42'),
    '/api/results/runner?event_id=evt-2026&runner_id=42',
  );
  for (const eventId of ['', '../event', 'event/id', '-event', 'event id', '☃']) {
    assert.throws(() => View.buildSearchEndpoint(eventId, 'Ada'), /event/i, eventId);
    assert.throws(() => View.buildRunnerEndpoint(eventId, '42'), /event/i, eventId);
  }
  for (const query of ['', 'a', 'x'.repeat(101)]) {
    assert.throws(() => View.buildSearchEndpoint('evt-2026', query), /query/i);
  }
  for (const runnerId of ['', 'result_42', 0, '0', -1, '-1', '1.5', ' 42', '42 ', '1e3', '+42', '01', '?runner=1', '9'.repeat(65), Number.MAX_SAFE_INTEGER + 1]) {
    assert.throws(() => View.buildRunnerEndpoint('evt-2026', runnerId), /runner/i, runnerId);
  }
});

test('GitHub Pages result requests use only the configured HTTPS API origin', () => {
  const endpoint = View.buildRunnerEndpoint('evt-2026', '42');
  const pagesRoot = {
    location: {href: 'https://benjibrucker.github.io/course-signal/'},
    RUT_CONFIG: {apiBase: 'https://course-signal-api.vercel.app'},
  };
  assert.equal(View.resolveResultEndpoint({location: {href: 'https://course.test/results/'}}, endpoint), endpoint);
  const resolved = View.resolveResultEndpoint(pagesRoot, endpoint);
  assert.equal(resolved, 'https://course-signal-api.vercel.app/api/results/runner?event_id=evt-2026&runner_id=42');
  assert.deepEqual(View.resultTransport(pagesRoot, resolved), {
    url: 'https://course-signal-api.vercel.app/api/results/runner',
    headers: {'X-Course-Signal-Event': 'evt-2026', 'X-Course-Signal-Runner': '42'},
  });
  const search = View.resolveResultEndpoint(pagesRoot, View.buildSearchEndpoint('evt-2026', 'Ada Runner', 7));
  assert.deepEqual(View.resultTransport(pagesRoot, search), {
    url: 'https://course-signal-api.vercel.app/api/results/search',
    headers: {
      'X-Course-Signal-Event': 'evt-2026',
      'X-Course-Signal-Query': 'Ada Runner',
      'X-Course-Signal-Limit': '20',
    },
  });
  assert.deepEqual(View.resultTransport({location: {href: 'https://course.test/results/'}}, endpoint), {url: endpoint, headers: null});
  for (const apiBase of ['', 'http://course-signal-api.vercel.app', 'https://user@example.com', 'https://example.com/?token=x', 'not a URL']) {
    assert.throws(
      () => View.resolveResultEndpoint({
        location: {href: 'https://benjibrucker.github.io/course-signal/'},
        RUT_CONFIG: {apiBase},
      }, endpoint),
      /results API/i,
      apiBase,
    );
  }
  assert.throws(
    () => View.resolveResultEndpoint({location: {href: 'https://benjibrucker.github.io/course-signal/'}}, '/private'),
    /result endpoint/i,
  );
  assert.throws(
    () => View.resultTransport(pagesRoot, `${resolved}&runner_id=43`),
    /result endpoint/i,
  );
});

test('runner IDs accept only canonical positive ASCII decimals through Number.MAX_SAFE_INTEGER', async () => {
  const maximum = String(Number.MAX_SAFE_INTEGER);
  const maximumEndpoint = `/api/results/runner?event_id=evt-2026&runner_id=${maximum}`;
  assert.equal(View.buildRunnerEndpoint('evt-2026', maximum), maximumEndpoint);
  assert.equal(View.buildRunnerEndpoint('evt-2026', Number.MAX_SAFE_INTEGER), maximumEndpoint);
  assert.equal(
    View.buildShareUrl(
      'https://course.test/results/?race=race-1&event=evt-2026&mode=results',
      {race: 'race-1', event: 'evt-2026', mode: 'results'},
      maximum,
    ),
    `https://course.test/results/?race=race-1&event=evt-2026&mode=results&runner=${maximum}`,
  );

  const invalidRunnerIds = [
    '9007199254740992', 0, '0', -1, '-1', '+1', '01', '١', '１', '9'.repeat(64),
    Number.MAX_SAFE_INTEGER + 1,
  ];
  for (const runnerId of invalidRunnerIds) {
    assert.throws(() => View.buildRunnerEndpoint('evt-2026', runnerId), /runner/i, String(runnerId));
    assert.throws(
      () => View.buildShareUrl(
        'https://course.test/results/?race=race-1&event=evt-2026&mode=results',
        {race: 'race-1', event: 'evt-2026', mode: 'results'},
        runnerId,
      ),
      /runner/i,
      String(runnerId),
    );
    const invalidRoute = resultRoot(null, runnerId);
    assert.deepEqual(await View.initBrowser(invalidRoute.root), {active: false, reason: 'inactive_route'}, String(runnerId));
    assert.deepEqual(invalidRoute.calls, [], String(runnerId));
  }

  const maximumRoute = resultRoot(null, maximum);
  assert.equal((await View.initBrowser(maximumRoute.root)).active, true);
  assert.deepEqual(maximumRoute.calls, [maximumEndpoint.replace('evt-2026', 'evt-1')]);
});

test('successful search and detail payloads honor literal boolean freshness', async () => {
  const warning = 'Showing cached completed results; source refresh was unavailable.';
  const search = interactiveRoot(() => Promise.resolve(jsonResponse(searchPayload(
    [{id: 42, name: 'Cached Runner', status: 'FINISHED'}],
    {stale: true},
  ))));
  await View.initBrowser(search.root);
  search.document.getElementById('resultsRunnerSearch').value = 'Cached Runner';
  search.document.getElementById('resultsSearchForm').dispatch('submit');
  await flushAsync();
  assert.equal(search.document.getElementById('resultsStatus').textContent, warning);
  assert.doesNotMatch(search.document.getElementById('resultsStatus').textContent, /published result.*found/i);

  const detailPayload = resultPayload();
  detailPayload.stale = true;
  const detail = resultRoot(detailPayload);
  await View.initBrowser(detail.root);
  assert.equal(detail.document.getElementById('resultsStatus').textContent, warning);
  assert.doesNotMatch(detail.document.getElementById('resultsStatus').textContent, /Runner result loaded/);

  const freshSearch = interactiveRoot(() => Promise.resolve(jsonResponse(searchPayload(
    [{id: 42, name: 'Fresh Runner', status: 'FINISHED'}],
  ))));
  await View.initBrowser(freshSearch.root);
  freshSearch.document.getElementById('resultsRunnerSearch').value = 'Fresh Runner';
  freshSearch.document.getElementById('resultsSearchForm').dispatch('submit');
  await flushAsync();
  assert.equal(freshSearch.document.getElementById('resultsStatus').textContent, '1 published result found. Choose one for details.');

  const freshDetail = resultRoot(resultPayload());
  await View.initBrowser(freshDetail.root);
  assert.equal(freshDetail.document.getElementById('resultsStatus').textContent, 'Runner result loaded.');
});

test('search and detail responses are bound to the exact requested route before rendering', async () => {
  const searchCases = [
    ['foreign', (payload) => { payload.event_id = 'evt-foreign'; }],
    ['missing', (payload) => { delete payload.event_id; }],
    ['malformed', (payload) => { payload.event_id = {id: 'evt-1'}; }],
  ];
  const searchObservations = [];
  for (const [label, mutate] of searchCases) {
    const payload = searchPayload([{id: 42, name: `FOREIGN SEARCH ${label}`, status: 'FINISHED'}]);
    mutate(payload);
    const fixture = interactiveRoot(() => Promise.resolve(jsonResponse(payload)));
    await View.initBrowser(fixture.root);
    fixture.document.getElementById('resultsRunnerSearch').value = 'Foreign Runner';
    fixture.document.getElementById('resultsSearchForm').dispatch('submit');
    await flushAsync();
    searchObservations.push({
      label,
      state: fixture.document.getElementById('resultsStatus').dataset.state,
      text: fixture.document.getElementById('resultsStatus').textContent,
      renderedForeignData: fixture.document.getElementById('resultsMatches').textContent.includes('FOREIGN SEARCH'),
    });
  }
  const detailCases = [
    ['foreign race', (payload) => { payload.race.slug = 'race-foreign'; }],
    ['foreign event', (payload) => { payload.event.id = 'evt-foreign'; }],
    ['missing race', (payload) => { delete payload.race.slug; }],
    ['missing event', (payload) => { delete payload.event.id; }],
    ['malformed race', (payload) => { payload.race.slug = ['race-1']; }],
    ['malformed event', (payload) => { payload.event.id = {id: 'evt-1'}; }],
  ];
  const detailObservations = [];
  for (const [label, mutate] of detailCases) {
    const payload = resultPayload({runner: {name: `FOREIGN DETAIL ${label}`}});
    mutate(payload);
    const fixture = resultRoot(payload);
    await View.initBrowser(fixture.root);
    detailObservations.push({
      label,
      state: fixture.document.getElementById('resultsStatus').dataset.state,
      text: fixture.document.getElementById('resultsStatus').textContent,
      renderedForeignData: fixture.document.getElementById('resultsDetail').textContent.includes('FOREIGN DETAIL'),
    });
  }
  assert.deepEqual({searchObservations, detailObservations}, {
    searchObservations: searchCases.map(([label]) => ({
      label,
      state: 'error',
      text: 'Runner search could not be completed.',
      renderedForeignData: false,
    })),
    detailObservations: detailCases.map(([label]) => ({
      label,
      state: 'error',
      text: 'Runner result could not be loaded.',
      renderedForeignData: false,
    })),
  });
});

test('search ignores undocumented race and event metadata', async () => {
  const payload = searchPayload(
    [{id: 42, name: 'Expected Runner', status: 'FINISHED'}],
    {
      race: {slug: 'race-foreign', name: 'FOREIGN RACE', location: 'FOREIGN LOCATION'},
      event: {
        id: 'evt-foreign',
        label: 'FOREIGN EVENT',
        event_date: '2099-01-01',
        capabilities: {results: true},
      },
    },
  );
  const fixture = interactiveRoot(() => Promise.resolve(jsonResponse(payload)));

  await View.initBrowser(fixture.root);
  fixture.document.getElementById('resultsRunnerSearch').value = 'Expected Runner';
  fixture.document.getElementById('resultsSearchForm').dispatch('submit');
  await flushAsync();

  assert.equal(fixture.document.getElementById('resultsStatus').dataset.state, 'ready');
  assert.match(fixture.document.getElementById('resultsMatches').textContent, /Expected Runner/);
  assert.equal(fixture.document.getElementById('resultsRaceName').textContent, 'race-1');
  assert.equal(fixture.document.getElementById('resultsEventName').textContent, 'evt-1');
  assert.equal(
    fixture.document.getElementById('resultsEventMeta').textContent,
    'Published event details load with results.',
  );
  assert.equal(
    byClass(fixture.document.getElementById('resultsCapabilities'), 'results-capability')
      .every((badge) => badge.textContent.endsWith(': Not reported')),
    true,
  );
});

test('search and detail fail closed when stale is absent or not a boolean', async () => {
  const malformedFreshness = [
    ['missing', undefined, true],
    ['null', null, false],
    ['number', 1, false],
    ['string', 'true', false],
    ['array', [], false],
    ['object', {}, false],
  ];
  const searchObservations = [];
  const detailObservations = [];
  for (const [label, value, omit] of malformedFreshness) {
    const searchResponse = searchPayload([{id: 42, name: `BAD SEARCH ${label}`, status: 'FINISHED'}]);
    const detailResponse = resultPayload({runner: {name: `BAD DETAIL ${label}`}});
    if (omit) {
      delete searchResponse.stale;
      delete detailResponse.stale;
    } else {
      searchResponse.stale = value;
      detailResponse.stale = value;
    }

    const search = interactiveRoot(() => Promise.resolve(jsonResponse(searchResponse)));
    await View.initBrowser(search.root);
    search.document.getElementById('resultsRunnerSearch').value = 'Bad Freshness';
    search.document.getElementById('resultsSearchForm').dispatch('submit');
    await flushAsync();
    searchObservations.push({
      label,
      state: search.document.getElementById('resultsStatus').dataset.state,
      renderedBadData: search.document.getElementById('resultsMatches').textContent.includes('BAD SEARCH'),
    });

    const detail = resultRoot(detailResponse);
    await View.initBrowser(detail.root);
    detailObservations.push({
      label,
      state: detail.document.getElementById('resultsStatus').dataset.state,
      renderedBadData: detail.document.getElementById('resultsDetail').textContent.includes('BAD DETAIL'),
    });
  }
  const expected = malformedFreshness.map(([label]) => ({label, state: 'error', renderedBadData: false}));
  assert.deepEqual(searchObservations, expected);
  assert.deepEqual(detailObservations, expected);
});

test('share URLs discard free text and all transient or private values', () => {
  const href = 'https://course.test/?race=race-1&event=evt-1&mode=results&q=Secret+Name&weight=180&calories=2400';
  const url = View.buildShareUrl(href, {race: 'race-1', event: 'evt-1', mode: 'results'}, '42');
  const parsed = new URL(url);
  assert.deepEqual([...parsed.searchParams.keys()], ['race', 'event', 'mode', 'runner']);
  assert.equal(parsed.href, 'https://course.test/?race=race-1&event=evt-1&mode=results&runner=42');
  assert.equal(ResultsModel.isPrivacySafeRoute(parsed.href, 'https://course.test/'), true);
  for (const secret of ['Secret', 'Name', 'weight', '180', 'calories', '2400', 'q=']) {
    assert.equal(parsed.href.includes(secret), false, secret);
  }
  assert.throws(
    () => View.buildShareUrl(href, {race: 'race-1', event: 'evt-1', mode: 'live'}, '42'),
    /results/i,
  );
  for (const runnerId of ['result_42', '0', '-1', '1.5', ' 42', '1e3', '+42', '01', '9'.repeat(65)]) {
    assert.throws(
      () => View.buildShareUrl(href, {race: 'race-1', event: 'evt-1', mode: 'results'}, runnerId),
      /runner/i,
      runnerId,
    );
  }
});

test('exact analysis energy fields adapt to the approved client model', () => {
  const record = {
    active_energy_kcal_per_kg: 10,
    active_energy_communication_range_kcal_per_kg: {lower: 7.5, upper: 12.5},
  };
  assert.deepEqual(View.adaptEnergyFactors(record), {low: 7.5, estimate: 10, high: 12.5});
  const scaled = ResultsModel.scaleEnergyRange(View.adaptEnergyFactors(record), 80);
  assert.deepEqual(scaled, {low: 600, estimate: 800, high: 1000});
  assert.equal(ResultsModel.formatRange(scaled), '600–1,000 active kcal');
  for (const malformed of [
    {},
    {...record, active_energy_kcal_per_kg: null},
    {...record, active_energy_communication_range_kcal_per_kg: {lower: 11, upper: 12}},
    {...record, active_energy_communication_range_kcal_per_kg: {lower: 8}},
  ]) assert.equal(View.adaptEnergyFactors(malformed), null);
});

test('official times and paces format without coercing missing values', () => {
  assert.equal(View.formatDuration(0), '0:00');
  assert.equal(View.formatDuration(65.4), '1:05');
  assert.equal(View.formatDuration(3661), '1:01:01');
  assert.equal(View.formatDuration(null), '—');
  assert.equal(View.formatDuration('65'), '—');
  assert.equal(View.formatPace(485.2), '8:05 /mi');
  assert.equal(View.formatPace(undefined), '—');
  assert.equal(View.formatPace(0), '—');
});

test('capabilities distinguish available, unavailable, and unreported evidence', () => {
  assert.deepEqual(View.capabilityRows({
    results: true,
    intermediate_splits: false,
    course_map: null,
    elevation: true,
    live_gps: false,
  }), [
    {key: 'results', label: 'Results', state: 'available'},
    {key: 'intermediate_splits', label: 'Intermediate splits', state: 'unavailable'},
    {key: 'course_map', label: 'Course map', state: 'unknown'},
    {key: 'elevation', label: 'Elevation', state: 'available'},
    {key: 'live_gps', label: 'Live GPS', state: 'unavailable'},
  ]);
  assert.equal(View.capabilityRows({}).every((row) => row.state === 'unknown'), true);
});

test('DNF and incomplete records stop at the last verified split', () => {
  assert.equal(
    View.runnerStopNotice({status: 'DNF', last_recorded_split_name: 'Aid Two', finish_seconds: null}),
    'DNF result stops at the last verified split: Aid Two. Finish was not inferred.',
  );
  assert.equal(
    View.runnerStopNotice({status: 'INCOMPLETE', last_recorded_split_name: 'Ridge', finish_seconds: null}),
    'In-progress result stops at the last verified split: Ridge. Finish was not inferred.',
  );
  assert.equal(View.runnerStopNotice({status: 'FINISHED', last_recorded_split_name: 'Finish', finish_seconds: 4000}), null);
});

test('DNF DOM stops before future unrecorded section names and content', async () => {
  const payload = resultPayload({
    runner: {
      status: 'DNF',
      finish_seconds: null,
      finish_place: null,
      last_recorded_split_index: 2,
      last_recorded_split_name: 'Aid Two',
    },
    sections: [
      {start_index: 0, start_name: 'Start', end_index: 1, end_name: 'Aid One', status: 'recorded', segment_seconds: 400, cumulative_seconds: 400},
      {start_index: 1, start_name: 'Aid One', end_index: 2, end_name: 'Aid Two', status: 'recorded', segment_seconds: 600, cumulative_seconds: 1000},
      {start_index: 2, start_name: 'Aid Two', end_index: 3, end_name: 'Future Summit', status: 'unknown', segment_seconds: 'UNRECORDED-FUTURE-CONTENT'},
    ],
    summary: {valid_section_count: 0},
    totals: {recorded_section_count: 2},
  });
  const {root, document} = resultRoot(payload);

  await View.initBrowser(root);

  const text = document.getElementById('resultsRunnerLead').textContent
    + document.getElementById('resultsDetail').textContent;
  assert.match(text, /DNF result stops at the last verified split: Aid Two/);
  assert.match(text, /Official finish—/);
  assert.doesNotMatch(text, /Future Summit|UNRECORDED-FUTURE-CONTENT|Aid Two → Future Summit/);
});

test('headline and summary name the overall field without inferring its count', async () => {
  const supported = resultPayload({summary: {overall_field_count: 237}});
  const first = resultRoot(supported);
  await View.initBrowser(first.root);
  assert.match(first.document.getElementById('resultsDetail').textContent, /Overall field · 237 published results · 3 valid sections analyzed/);

  for (const invalidCount of [0, -1, 237.5, '237', 100001, null]) {
    const unavailable = resultPayload({
      summary: {overall_field_count: invalidCount},
      sections: [{start_index: 0, start_name: 'Start', end_index: 1, end_name: 'Finish', status: 'recorded', field_count: 999, field_percentile: 50}],
    });
    const second = resultRoot(unavailable);
    await View.initBrowser(second.root);
    const text = second.document.getElementById('resultsDetail').textContent;
    assert.match(text, /Overall field · field count unavailable/, String(invalidCount));
    assert.doesNotMatch(text, /999 published results/, String(invalidCount));
  }
});

test('visible energy disclosure rejects wearable and medical truth claims', async () => {
  const factors = {
    active_energy_kcal_per_kg: 10,
    active_energy_communication_range_kcal_per_kg: {lower: 7.5, upper: 12.5},
  };
  const payload = resultPayload({
    runner: {last_recorded_split_index: 1, last_recorded_split_name: 'Finish'},
    totals: {recorded_section_count: 1, ...factors},
    sections: [{start_index: 0, start_name: 'Start', end_index: 1, end_name: 'Finish', status: 'recorded', segment_seconds: 1800, cumulative_seconds: 1800, ...factors}],
    summary: {valid_section_count: 1},
    capabilities: {active_energy: true},
  });
  const {root, document} = resultRoot(payload);

  await View.initBrowser(root);

  const lead = document.getElementById('resultsRunnerLead');
  const disclosure = byClass(lead, 'results-energy-disclosure')[0];
  assert.ok(disclosure);
  assert.match(disclosure.textContent, /engineering estimate/i);
  assert.match(disclosure.textContent, /active race energy/i);
  assert.match(disclosure.textContent, /running-only/i);
  assert.match(disclosure.textContent, /not a wearable reading/i);
  assert.match(disclosure.textContent, /not (?:a )?medical measurement or medical truth/i);
});

test('Results home and energy-method links expose 44px target contracts', async () => {
  const factors = {
    active_energy_kcal_per_kg: 10,
    active_energy_communication_range_kcal_per_kg: {lower: 7.5, upper: 12.5},
  };
  const {root, document} = resultRoot(resultPayload({
    runner: {last_recorded_split_index: 1, last_recorded_split_name: 'Finish'},
    sections: [{
      start_index: 0, start_name: 'Start', end_index: 1, end_name: 'Finish', status: 'recorded',
      segment_seconds: 1800, cumulative_seconds: 1800, ...factors,
    }],
    totals: {recorded_section_count: 1, ...factors},
    capabilities: {active_energy: true},
  }));
  await View.initBrowser(root);
  const methodLinks = byClass(document.getElementById('resultsRunnerLead'), 'results-method-link');
  assert.equal(methodLinks.length, 1);
  assert.equal(methodLinks[0].tagName, 'A');

  const styles = fs.readFileSync('styles.css', 'utf8');
  assert.match(styles, /\.shell-brand\s*\{[^}]*min-height:\s*44px\b[^}]*\}/s);
  assert.match(styles, /\.results-method-link\s*\{[^}]*min-height:\s*44px\b[^}]*\}/s);
});

test('match rendering uses text nodes and stable public IDs for ambiguous names', () => {
  const document = new FakeDocument();
  const host = new FakeElement('div');
  const selected = [];
  View.renderMatches(document, host, [
    {id: '101', name: '<img src=x onerror=alert(1)>', bib: '7', status: 'FINISHED', finish_seconds: 4000, finish_place: 9, secret: 'never-render'},
    {id: 'result_42', name: 'Invalid spelling', bib: '9', status: 'FINISHED'},
    {id: 0, name: 'Invalid zero', bib: '10', status: 'FINISHED'},
    {id: '202', name: '<img src=x onerror=alert(1)>', bib: '8', status: 'DNF', finish_seconds: null, finish_place: null},
  ], (id) => selected.push(id));
  assert.equal(host.children.length, 2);
  assert.equal(host.textContent.includes('<img src=x onerror=alert(1)>'), true);
  assert.equal(host.textContent.includes('never-render'), false);
  assert.equal(host.children[0].tagName, 'BUTTON');
  assert.notEqual(host.children[0].getAttribute('aria-label'), host.children[1].getAttribute('aria-label'));
  host.children[1].dispatch('click');
  assert.deepEqual(selected, ['202']);
});

test('SVG points use route coordinates and cumulative distance, never equal spacing', () => {
  const track = [
    {lat: 0, lng: 0, ele: 100},
    {lat: 0, lng: 0.001, ele: 200},
    {lat: 0, lng: 0.01, ele: 150},
  ];
  const course = View.courseSvgPoints(track, 200, 100, 10);
  assert.equal(typeof course, 'string');
  assert.equal(coordinates(course).length, 3);
  assert.equal(coordinates(course).flat().every(Number.isFinite), true);

  const elevation = View.elevationSvgPoints(track, 200, 100, 10);
  assert.ok(elevation && elevation.totalDistanceM > 1000);
  const [first, middle, last] = coordinates(elevation.points);
  assert.equal(first[0], 10);
  assert.equal(last[0], 190);
  assert.ok(middle[0] < 50, `middle x=${middle[0]} must reflect the short first segment`);
  assert.notEqual(middle[1], first[1]);
  assert.equal(View.courseSvgPoints([{lat: null, lng: 0}, {lat: 1, lng: 1}], 200, 100, 10), null);
  assert.equal(View.elevationSvgPoints([{lat: 0, lng: 0}, {lat: 1, lng: 1}], 200, 100, 10), null);
});

test('the queryless root activates the fixed Rut 28K recap without a request', async () => {
  const calls = [];
  const {root} = interactiveRoot(async (url) => {
    calls.push(url);
    return jsonResponse({status: 'error'}, 503);
  });
  root.location.href = 'https://course.test/';
  root.location.pathname = '/';
  root.CourseSignalShell.route = {
    kind: 'race', race: 'the-rut', event: 'the-rut-28k-2026', mode: 'results', explicitMode: true,
  };

  const result = await View.initBrowser(root);

  assert.equal(result.active, true);
  assert.deepEqual(result.route, {
    race: 'the-rut', event: 'the-rut-28k-2026', mode: 'results', runner: null,
  });
  assert.deepEqual(calls, []);
  result.teardown();
});

test('Rut typeahead searches all five distances and routes Katrina to her 21K course', async () => {
  const calls = [];
  const navigations = [];
  const timers = [];
  const {root, document} = interactiveRoot(async (url) => {
    calls.push(url);
    const parsed = new URL(url, 'https://course.test/');
    const eventId = parsed.searchParams.get('event_id');
    const matches = eventId === 'the-rut-50k-2026'
      ? Array.from({length: 20}, (_value, index) => ({
        id: 1000 + index, name: `Other runner ${index + 1}`, bib: 1000 + index, status: 'FINISHED',
      }))
      : eventId === 'the-rut-21k-2026'
        ? [{id: 87319813, name: 'Katrina Brucker', bib: 3786, status: 'FINISHED'}]
        : [];
    return jsonResponse(searchPayload(matches, {event_id: eventId}));
  });
  root.location.href = 'https://course.test/?race=the-rut&event=the-rut-28k-2026&mode=results';
  root.location.pathname = '/';
  root.location.assign = (url) => navigations.push(url);
  root.CourseSignalShell.route = {
    kind: 'race', race: 'the-rut', event: 'the-rut-28k-2026', mode: 'results', explicitMode: true,
  };
  root.setTimeout = (callback, delay) => {
    timers.push({callback, delay});
    return timers.length;
  };
  root.clearTimeout = () => {};

  const ready = await View.initBrowser(root);
  const input = document.getElementById('resultsRunnerSearch');
  input.value = 'Katrina Brucker';
  input.dispatch('input');

  assert.deepEqual(calls, []);
  assert.equal(timers.length, 1);
  assert.equal(timers[0].delay, 300);
  timers[0].callback();
  await flushAsync();

  const expectedEvents = [
    'the-rut-50k-2026', 'the-rut-28k-2026', 'the-rut-21k-2026',
    'the-rut-11k-2026', 'the-rut-vk-2026',
  ];
  assert.deepEqual(
    calls.map((url) => new URL(url, 'https://course.test/').searchParams.get('event_id')),
    expectedEvents,
  );
  assert.equal(document.getElementById('resultsStatus').textContent, '20 published results found across 5 distances. Choose one for details.');
  assert.match(document.getElementById('resultsMatches').textContent, /Katrina Brucker/);
  assert.match(document.getElementById('resultsMatches').textContent, /21K/);

  const katrinaMatch = document.getElementById('resultsMatches').children.find(
    (child) => child.textContent.includes('Katrina Brucker'),
  );
  assert.ok(katrinaMatch, 'later-distance matches must not be starved by an earlier full result set');
  katrinaMatch.dispatch('click');
  assert.deepEqual(navigations, [
    'https://course.test/?race=the-rut&event=the-rut-21k-2026&mode=results&runner=87319813',
  ]);
  ready.teardown();
});

test('a direct privacy-safe runner route fetches details without a search request', async () => {
  const {root, calls} = resultRoot(null);
  const result = await View.initBrowser(root);
  assert.equal(result.active, true);
  assert.deepEqual(calls, ['/api/results/runner?event_id=evt-1&runner_id=42']);
  assert.equal(calls.some((url) => url.includes('/search')), false);

  for (const runnerId of ['result_42', '0', '-1', '1.5', '%2042', '1e3', '%2B42', '01', '9'.repeat(65)]) {
    const invalid = resultRoot(null, runnerId);
    const inactive = await View.initBrowser(invalid.root);
    assert.deepEqual(inactive, {active: false, reason: 'inactive_route'}, runnerId);
    assert.deepEqual(invalid.calls, [], runnerId);
  }
});

test('page wiring keeps Results DOM-safe, storage-free, timer-free, and separate from Leaflet', () => {
  const html = fs.readFileSync('index.html', 'utf8');
  const css = fs.readFileSync('styles.css', 'utf8');
  const source = fs.existsSync('results_view.js') ? fs.readFileSync('results_view.js', 'utf8') : '';
  assert.ok(html.indexOf('results_model.js') < html.indexOf('results_view.js'));
  assert.ok(html.indexOf('results_view.js') < html.indexOf('app.js'));
  for (const id of ['resultsSearchForm', 'resultsRunnerSearch', 'resultsStatus', 'resultsMatches', 'resultsRunnerLead', 'resultsOverview', 'resultsDetail']) {
    assert.match(html, new RegExp(`id="${id}"`), id);
  }
  assert.ok(html.indexOf('id="resultsRunnerLead"') < html.indexOf('id="resultsOverview"'));
  assert.match(css, /html\s*\{[^}]*overflow:\s*auto/);
  assert.match(css, /body\[data-course-signal-view="results"\][^{]*\{[^}]*overflow:\s*visible/);
  assert.doesNotMatch(source, /innerHTML|outerHTML|insertAdjacentHTML/);
  assert.doesNotMatch(source, /localStorage|sessionStorage|indexedDB|document\.cookie/);
  assert.doesNotMatch(source, /setInterval|Leaflet|\bL\.(?:map|polyline|marker)/);
});

test('share and direct-route bases preserve exact same-origin paths without URL-reference reparsing', async () => {
  const route = {race: 'race-1', event: 'evt-1', mode: 'results'};
  for (const href of [
    'https://course.test//evil.test/collect?secret=value',
    'https://course.test/results//nested/?secret=value',
    'https://course.test/%2F%2Fevil.test/collect?secret=value',
    'https://course.test/results/%5Cevil.test/collect?secret=value',
  ]) {
    const expected = new URL(href);
    const share = new URL(View.buildShareUrl(href, route, '42'));
    assert.equal(share.origin, expected.origin, href);
    assert.equal(share.pathname, expected.pathname, href);
    assert.equal(share.searchParams.get('runner'), '42', href);
  }
  assert.throws(
    () => View.buildShareUrl('https://course.test/\\evil.test/collect?secret=value', route, '42'),
    /URL|route|path/i,
  );

  const direct = resultRoot(null);
  direct.root.location.href = 'https://course.test//evil.test/collect?race=race-1&event=evt-1&mode=results&runner=42';
  direct.root.location.pathname = '//evil.test/collect';
  const activated = await View.initBrowser(direct.root);
  assert.equal(activated.active, true);
  assert.deepEqual(direct.calls, ['/api/results/runner?event_id=evt-1&runner_id=42']);

  const historyFixture = interactiveRoot((url) => Promise.resolve(url.includes('/search')
    ? jsonResponse(searchPayload([{id: 42, name: 'History Runner', status: 'FINISHED'}]))
    : jsonResponse(resultPayload())));
  historyFixture.root.location.href = 'https://course.test//evil.test/collect?race=race-1&event=evt-1&mode=results';
  historyFixture.root.location.pathname = '//evil.test/collect';
  await View.initBrowser(historyFixture.root);
  historyFixture.document.getElementById('resultsRunnerSearch').value = 'History Runner';
  historyFixture.document.getElementById('resultsSearchForm').dispatch('submit');
  await flushAsync();
  historyFixture.document.getElementById('resultsMatches').children[0].dispatch('click');
  await flushAsync();
  assert.deepEqual(historyFixture.historyWrites, [
    'https://course.test//evil.test/collect?race=race-1&event=evt-1&mode=results&runner=42',
  ]);
});

test('non-finish search rows never expose contradictory finish time or place', () => {
  const document = new FakeDocument();
  const host = new FakeElement('div');
  View.renderMatches(document, host, [
    {id: '1', name: 'Contradictory DNF', bib: '1', status: 'DNF', finish_seconds: 1234, finish_place: 1},
    {id: '2', name: 'Contradictory DNS', bib: '2', status: 'DNS', finish_seconds: 4567, finish_place: 2},
  ], () => {});
  assert.match(host.textContent, /No official finish/);
  assert.doesNotMatch(host.textContent, /20:34|1:16:07|#1|#2/);
});

test('capability false or unreported suppresses contradictory metrics and geometry', async () => {
  const factors = {
    active_energy_kcal_per_kg: 10,
    active_energy_communication_range_kcal_per_kg: {lower: 7.5, upper: 12.5},
  };
  for (const reported of [false, undefined]) {
    const payload = resultPayload({
      sections: [{
        start_index: 0, start_name: 'Start', end_index: 1, end_name: 'Finish', status: 'recorded',
        segment_seconds: 1800, cumulative_seconds: 1800, pace_seconds_per_mile: 300,
        distance_m: 12345, gain_m: 678, loss_m: 12, net_grade: 0.05,
        field_count: 999, field_percentile: 99, rank_change: 5, ...factors,
      }],
      summary: {
        overall_field_count: 999,
        strongest_comparable_section: {start_index: 0, start_name: 'Start', end_index: 1, end_name: 'Finish', field_percentile: 99},
      },
      totals: {recorded_section_count: 1, ...factors},
      capabilities: {
        splits: reported,
        field_comparison: reported,
        terrain: reported,
        active_energy: reported,
      },
    });
    payload.event.capabilities = {
      ...FULL_EVENT_CAPABILITIES,
      course_map: reported,
      elevation: reported,
    };
    payload.event.course = {track_points: [
      {lat: 45, lng: -111, ele: 1000},
      {lat: 45.01, lng: -111.01, ele: 1200},
    ]};
    const fixture = resultRoot(payload);
    await View.initBrowser(fixture.root);
    const detail = fixture.document.getElementById('resultsDetail');
    assert.doesNotMatch(detail.textContent, /Start → Finish|12\.3 km|\+678 m|99th percentile|999 comparable|Total weight/, String(reported));
    assert.equal(byClass(detail, 'results-course-line').length, 0, String(reported));
    assert.equal(byClass(detail, 'results-elevation-line').length, 0, String(reported));
    assert.equal(byClass(detail, 'results-weight-field').length, 0, String(reported));
  }
});

test('contradictory DNF analysis retains only a contiguous recorded prefix and drops unsupported energy', async () => {
  const factors = {
    active_energy_kcal_per_kg: 10,
    active_energy_communication_range_kcal_per_kg: {lower: 7.5, upper: 12.5},
  };
  const payload = resultPayload({
    runner: {
      status: 'DNF', finish_seconds: 9999, finish_place: 1,
      last_recorded_split_index: 2, last_recorded_split_name: 'Aid Two',
    },
    sections: [
      {start_index: 0, start_name: 'Start', end_index: 1, end_name: 'Aid One', status: 'recorded', segment_seconds: 400, cumulative_seconds: 400, ...factors},
      {start_index: 9, start_name: 'WRONG ORDER', end_index: 2, end_name: 'Aid Two', status: 'recorded', segment_seconds: 600, cumulative_seconds: 1000, ...factors},
      {start_index: 1, start_name: 'Aid One', end_index: 2, end_name: 'Aid Two', status: 'recorded', segment_seconds: 600, cumulative_seconds: 1000, ...factors},
      {start_index: 2, start_name: 'Aid Two', end_index: 3, end_name: 'Finish', status: 'recorded', segment_seconds: 800, cumulative_seconds: 1800, ...factors},
    ],
    summary: {
      valid_section_count: 4,
      strongest_comparable_section: {start_index: 2, start_name: 'Aid Two', end_index: 3, end_name: 'Finish', field_percentile: 99},
      largest_places_gained: {start_index: 2, start_name: 'Aid Two', end_index: 3, end_name: 'Finish', rank_change: 99},
    },
    totals: {
      recorded_section_count: 4,
      active_energy_kcal_per_kg: 999,
      active_energy_communication_range_kcal_per_kg: {lower: 900, upper: 1100},
    },
    capabilities: {splits: true, field_comparison: true, terrain: true, active_energy: true},
  });
  const fixture = resultRoot(payload);
  await View.initBrowser(fixture.root);
  const text = fixture.document.getElementById('resultsDetail').textContent;
  assert.match(text, /Start → Aid One/);
  assert.doesNotMatch(text, /WRONG ORDER|Aid One → Aid Two|Aid Two → Finish|9999|#1 overall|99th percentile|Total weight/);
  assert.equal(byClass(fixture.document.getElementById('resultsDetail'), 'results-weight-field').length, 0);
});

test('finished analysis suppresses unrecorded evidence and invalid or nonmonotonic section topology', async () => {
  const factors = {
    active_energy_kcal_per_kg: 10,
    active_energy_communication_range_kcal_per_kg: {lower: 9, upper: 11},
  };
  const unrecorded = resultPayload({
    sections: [
      {start_index: 0, start_name: 'Start', end_index: 1, end_name: 'Aid One', status: 'recorded', segment_seconds: 400, cumulative_seconds: 400, ...factors},
      {
        start_index: 1, start_name: 'Aid One', end_index: 2, end_name: 'Aid Two', status: 'future',
        segment_seconds: 54321, cumulative_seconds: 65432, cumulative_place: 2, segment_place: 3,
        field_count: 99999, field_percentile: 99, rank_change: 77, pace_seconds_per_mile: 987,
        distance_m: 123456, gain_m: 12345, loss_m: 2345, net_grade: 0.123,
        active_energy_kcal_per_kg: 99,
        active_energy_communication_range_kcal_per_kg: {lower: 98, upper: 100},
      },
    ],
    totals: {recorded_section_count: 2, ...factors},
    capabilities: {splits: true, field_comparison: true, terrain: true, active_energy: true},
  });
  const unrecordedFixture = resultRoot(unrecorded);
  await View.initBrowser(unrecordedFixture.root);
  const unrecordedText = unrecordedFixture.document.getElementById('resultsDetail').textContent;
  const evidencePattern = /15:05:21|18:10:32|16:27 \/mi|123\.5 km|12,345 m|99th percentile|99,999 comparable|\+77 gained|Total weight/;

  const topologyCases = [
    ['OUT OF ORDER', {start_index: 0, start_name: 'Start', end_index: 1, end_name: 'OUT OF ORDER'}],
    ['INDEX SPAN', {start_index: 1, start_name: 'Aid One', end_index: 3, end_name: 'INDEX SPAN'}],
    ['NAME MISMATCH', {start_index: 1, start_name: 'Other Aid', end_index: 2, end_name: 'NAME MISMATCH'}],
  ];
  const topologyObservations = [];
  for (const [sentinel, malformed] of topologyCases) {
    const payload = resultPayload({
      sections: [
        {start_index: 0, start_name: 'Start', end_index: 1, end_name: 'Aid One', status: 'recorded', segment_seconds: 400, cumulative_seconds: 400},
        {...malformed, status: 'recorded', segment_seconds: 9999, cumulative_seconds: 9999},
      ],
      totals: {recorded_section_count: 2},
    });
    const fixture = resultRoot(payload);
    await View.initBrowser(fixture.root);
    const text = fixture.document.getElementById('resultsDetail').textContent;
    topologyObservations.push({
      sentinel,
      keptValidPrefix: text.includes('Start → Aid One'),
      renderedMalformed: text.includes(sentinel),
    });
  }
  assert.deepEqual({
    unrecordedObservation: {
      keptValidPrefix: unrecordedText.includes('Start → Aid One'),
      renderedEvidence: evidencePattern.test(unrecordedText),
    },
    topologyObservations,
  }, {
    unrecordedObservation: {keptValidPrefix: true, renderedEvidence: false},
    topologyObservations: topologyCases.map(([sentinel]) => ({
      sentinel,
      keptValidPrefix: true,
      renderedMalformed: false,
    })),
  });
});

test('finished sparse analysis keeps valid recorded rows and derives comparable counts from evidence', async () => {
  const payload = resultPayload({
    runner: {last_recorded_split_index: 4, last_recorded_split_name: 'Finish'},
    sections: [
      {
        start_index: 0, start_name: 'Start', end_index: 1, end_name: 'Aid One', status: 'unknown',
        segment_seconds: 9999, field_count: 999, field_percentile: 99,
      },
      {
        start_index: 1, start_name: 'Aid One', end_index: 2, end_name: 'Aid Two', status: 'recorded',
        segment_seconds: 600, cumulative_seconds: 1000, field_count: 1, field_percentile: 50,
      },
      {start_index: 2, start_name: 'Aid Two', end_index: 3, end_name: 'Ridge', status: 'unknown'},
      {
        start_index: 3, start_name: 'Ridge', end_index: 4, end_name: 'Finish', status: 'recorded',
        segment_seconds: 800, cumulative_seconds: 1800, field_count: 7, field_percentile: 75,
      },
    ],
    summary: {
      valid_section_count: 2,
      strongest_comparable_section: {
        start_index: 3, start_name: 'Ridge', end_index: 4, end_name: 'Finish', field_percentile: 75,
      },
      consistency: {
        status: 'available', comparable_section_count: 2, percentile_range: 25, mean_percentile: 62.5,
      },
      trend: {
        status: 'available', comparable_section_count: 2,
        percentile_points_per_section: 25, direction: 'higher_relative_percentiles_later',
      },
    },
    totals: {recorded_section_count: 2},
  });
  const fixture = resultRoot(payload);
  await View.initBrowser(fixture.root);
  const detail = fixture.document.getElementById('resultsDetail');
  assert.deepEqual(
    byClass(detail, 'results-section-name').map((node) => node.textContent),
    ['Aid One → Aid Two', 'Ridge → Finish'],
  );
  assert.doesNotMatch(detail.textContent, /Start → Aid One|Aid Two → Ridge|9999|999 comparable/);
  const cards = byClass(detail, 'results-summary-card').map((node) => node.textContent);
  assert.ok(cards.includes('Overall comparisonOverall field · field count unavailable · 1 valid section analyzed'));
  assert.ok(cards.includes('ConsistencyUnavailable — 1 comparable section.'));
  assert.ok(cards.includes('TrendUnavailable — 1 comparable section.'));
});

test('finished sparse topology ignores unknown boundary claims while retaining later recorded evidence', async () => {
  const sectionFactors = {
    active_energy_kcal_per_kg: 10,
    active_energy_communication_range_kcal_per_kg: {lower: 7.5, upper: 12.5},
  };
  const payload = resultPayload({
    runner: {last_recorded_split_index: 3, last_recorded_split_name: 'Finish'},
    sections: [
      {
        start_index: 0, start_name: 'Start', end_index: 1, end_name: 'Aid One', status: 'recorded',
        segment_seconds: 400, cumulative_seconds: 400, field_count: 5, field_percentile: 40, ...sectionFactors,
      },
      {
        start_index: 1, start_name: 'Aid One', end_index: 2, end_name: 'UNVERIFIED WRONG BOUNDARY', status: 'unknown',
        segment_seconds: 54321, cumulative_seconds: 65432, cumulative_place: 1, segment_place: 1,
        field_count: 99999, field_percentile: 99, rank_change: 77, pace_seconds_per_mile: 987,
        distance_m: 123456, gain_m: 12345, loss_m: 2345, net_grade: 0.123,
        active_energy_kcal_per_kg: 99,
        active_energy_communication_range_kcal_per_kg: {lower: 98, upper: 100},
      },
      {
        start_index: 2, start_name: 'Ridge', end_index: 3, end_name: 'Finish', status: 'recorded',
        segment_seconds: 800, cumulative_seconds: 1800, field_count: 7, field_percentile: 75, ...sectionFactors,
      },
    ],
    totals: {
      recorded_section_count: 2,
      active_energy_kcal_per_kg: 20,
      active_energy_communication_range_kcal_per_kg: {lower: 15, upper: 25},
    },
    capabilities: {splits: true, field_comparison: true, terrain: true, active_energy: true},
  });
  const fixture = resultRoot(payload);
  await View.initBrowser(fixture.root);
  const detail = fixture.document.getElementById('resultsDetail');
  assert.deepEqual(
    byClass(detail, 'results-section-name').map((node) => node.textContent),
    ['Start → Aid One', 'Ridge → Finish'],
  );
  assert.doesNotMatch(
    detail.textContent,
    /UNVERIFIED WRONG BOUNDARY|15:05:21|18:10:32|16:27 \/mi|123\.5 km|12,345 m|99th percentile|99,999 comparable|\+77 gained|Total weight/,
  );
  assert.equal(byClass(detail, 'results-weight-field').length, 0);
});

test('recorded rows without both real endpoint names expose no section evidence', async () => {
  const factors = {
    active_energy_kcal_per_kg: 10,
    active_energy_communication_range_kcal_per_kg: {lower: 7.5, upper: 12.5},
  };
  const malformedRows = [
    ['missing start', {end_name: 'Finish'}],
    ['blank start', {start_name: '   ', end_name: 'Finish'}],
    ['missing end', {start_name: 'Start'}],
    ['blank end', {start_name: 'Start', end_name: '\t'}],
  ];
  const observations = [];
  for (const [label, names] of malformedRows) {
    const payload = resultPayload({
      runner: {last_recorded_split_index: 1, last_recorded_split_name: 'Finish'},
      sections: [{
        start_index: 0, end_index: 1, status: 'recorded', ...names,
        segment_seconds: 54321, cumulative_seconds: 65432, cumulative_place: 1, segment_place: 1,
        field_count: 99999, field_percentile: 99, rank_change: 77, pace_seconds_per_mile: 987,
        distance_m: 123456, gain_m: 12345, loss_m: 2345, net_grade: 0.123, ...factors,
      }],
      totals: {recorded_section_count: 1, ...factors},
      capabilities: {splits: true, field_comparison: true, terrain: true, active_energy: true},
    });
    const fixture = resultRoot(payload);
    await View.initBrowser(fixture.root);
    const detail = fixture.document.getElementById('resultsDetail');
    observations.push({
      label,
      names: byClass(detail, 'results-section-name').map((node) => node.textContent),
      renderedHiddenEvidence: /Unknown|15:05:21|18:10:32|16:27 \/mi|123\.5 km|12,345 m|99th percentile|99,999 comparable|\+77 gained|Total weight/.test(detail.textContent),
      energyInputs: byClass(detail, 'results-weight-field').length,
    });
  }
  assert.deepEqual(observations, malformedRows.map(([label]) => ({
    label,
    names: [],
    renderedHiddenEvidence: false,
    energyInputs: 0,
  })));
});

test('finished aggregate energy requires runner extent and endpoint-name agreement', async () => {
  const factors = {
    active_energy_kcal_per_kg: 10,
    active_energy_communication_range_kcal_per_kg: {lower: 7.5, upper: 12.5},
  };
  const mismatches = [
    ['extent', {last_recorded_split_index: 2, last_recorded_split_name: 'Finish'}],
    ['name', {last_recorded_split_index: 1, last_recorded_split_name: 'Wrong Finish'}],
  ];
  const observations = [];
  for (const [label, runner] of mismatches) {
    const fixture = resultRoot(resultPayload({
      runner,
      sections: [{
        start_index: 0, start_name: 'Start', end_index: 1, end_name: 'Finish', status: 'recorded',
        segment_seconds: 1800, cumulative_seconds: 1800, ...factors,
      }],
      totals: {recorded_section_count: 1, ...factors},
      capabilities: {active_energy: true},
    }));
    await View.initBrowser(fixture.root);
    const detail = fixture.document.getElementById('resultsDetail');
    const lead = fixture.document.getElementById('resultsRunnerLead');
    observations.push({
      label,
      energyInputs: byClass(lead, 'results-weight-field').length,
      reportsUnavailable: detail.textContent.includes('Optional calorie estimateThe route did not support the optional weight-based estimate.'),
    });
  }
  assert.deepEqual(observations, mismatches.map(([label]) => ({
    label,
    energyInputs: 0,
    reportsUnavailable: true,
  })));
});

test('finished aggregate energy requires an exact literal source count and every section factor', async () => {
  const factors = {
    active_energy_kcal_per_kg: 10,
    active_energy_communication_range_kcal_per_kg: {lower: 7.5, upper: 12.5},
  };
  const cases = [
    ['count mismatch', {recorded_section_count: 2, ...factors}, factors],
    ['string count', {recorded_section_count: '1', ...factors}, factors],
    ['fractional count', {recorded_section_count: 1.5, ...factors}, factors],
    ['null count', {recorded_section_count: null, ...factors}, factors],
    ['missing section factors', {recorded_section_count: 1, ...factors}, {}],
  ];
  const observations = [];
  for (const [label, totals, rowFactors] of cases) {
    const fixture = resultRoot(resultPayload({
      runner: {last_recorded_split_index: 1, last_recorded_split_name: 'Finish'},
      sections: [{
        start_index: 0, start_name: 'Start', end_index: 1, end_name: 'Finish', status: 'recorded',
        segment_seconds: 1800, cumulative_seconds: 1800, ...rowFactors,
      }],
      totals,
      capabilities: {active_energy: true},
    }));
    await View.initBrowser(fixture.root);
    const detail = fixture.document.getElementById('resultsDetail');
    const lead = fixture.document.getElementById('resultsRunnerLead');
    observations.push({
      label,
      energyInputs: byClass(lead, 'results-weight-field').length,
      reportsUnavailable: detail.textContent.includes('Optional calorie estimateThe route did not support the optional weight-based estimate.'),
    });
  }
  assert.deepEqual(observations, cases.map(([label]) => ({
    label,
    energyInputs: 0,
    reportsUnavailable: true,
  })));
});

test('summary consistency and trend need at least three derived comparable sections', async () => {
  const observations = [];
  for (let count = 0; count <= 3; count += 1) {
    const sections = Array.from({length: count}, (_value, index) => ({
      start_index: index,
      start_name: index === 0 ? 'Start' : `Aid ${index}`,
      end_index: index + 1,
      end_name: index + 1 === count ? 'Finish' : `Aid ${index + 1}`,
      status: 'recorded',
      segment_seconds: 100,
      cumulative_seconds: (index + 1) * 100,
      field_count: 5,
      field_percentile: 40 + index * 10,
    }));
    const fixture = resultRoot(resultPayload({
      runner: {
        last_recorded_split_index: count,
        last_recorded_split_name: count ? 'Finish' : '',
      },
      sections,
      summary: {
        consistency: {
          status: 'available', comparable_section_count: count,
          percentile_range: count ? (count - 1) * 10 : 0, mean_percentile: count ? 40 + (count - 1) * 5 : 0,
        },
        trend: {
          status: 'available', comparable_section_count: count,
          percentile_points_per_section: 10, direction: 'higher_relative_percentiles_later',
        },
      },
      totals: {recorded_section_count: count},
    }));
    await View.initBrowser(fixture.root);
    const cards = byClass(fixture.document.getElementById('resultsDetail'), 'results-summary-card');
    observations.push({
      count,
      consistency: cards.find((node) => node.textContent.startsWith('Consistency')).textContent,
      trend: cards.find((node) => node.textContent.startsWith('Trend')).textContent,
    });
  }
  assert.deepEqual(observations, [
    {count: 0, consistency: 'ConsistencyUnavailable — 0 comparable sections.', trend: 'TrendUnavailable — 0 comparable sections.'},
    {count: 1, consistency: 'ConsistencyUnavailable — 1 comparable section.', trend: 'TrendUnavailable — 1 comparable section.'},
    {count: 2, consistency: 'ConsistencyUnavailable — 2 comparable sections.', trend: 'TrendUnavailable — 2 comparable sections.'},
    {
      count: 3,
      consistency: 'Consistency3 comparable sections · 20 percentile-point range · 50th-percentile mean',
      trend: 'TrendHigher relative percentiles in later sections · +10.0 points per section',
    },
  ]);
});

test('fully supported event and analysis capabilities retain complete detail behavior', async () => {
  const payload = fullySupportedResultPayload();
  payload.analysis.private_internal_value = 'PRIVATE INTERNAL VALUE';
  payload.source_payload = {raw_secret: 'RAW SOURCE VALUE'};
  const fixture = resultRoot(payload);

  await View.initBrowser(fixture.root);

  const detail = fixture.document.getElementById('resultsDetail');
  const lead = fixture.document.getElementById('resultsRunnerLead');
  const sectionTable = byClass(detail, 'results-section-table')[0];
  assert.equal(fixture.document.getElementById('resultsStatus').dataset.state, 'ready');
  assert.equal(byClass(detail, 'results-section-name').length, 3);
  assert.equal(byClass(lead, 'results-weight-field').length, 1);
  assert.match(lead.textContent, /Ada Runner|Age 36 · X|X 30-39|2nd age group|3rd gender/);
  assert.equal(byClass(detail, 'results-checkpoint-key').length, 1);
  assert.equal(byClass(detail, 'results-checkpoint-marker').length, 8);
  assert.match(detail.textContent, /Start0:00 elapsed|Aid One6:40 elapsed|Aid Two16:40 elapsed|Finish30:00 elapsed/);
  assert.match(sectionTable.textContent, /6:40|50th percentile|1\.00 km|\+100 m|Active calories/);
  assert.match(detail.textContent, /Consistency3 comparable sections|TrendHigher relative percentiles/);
  assert.doesNotMatch(detail.textContent, /PRIVATE INTERNAL VALUE|RAW SOURCE VALUE|private_internal_value|source_payload|raw_secret/);
});

test('event results capability must be literal true before detail analysis is accepted', async () => {
  const invalidValues = [
    ['false', false],
    ['missing', undefined],
    ['null', null],
    ['number', 1],
    ['string', 'true'],
    ['array', []],
    ['object', {}],
  ];
  const observations = [];
  for (const [label, value] of invalidValues) {
    const payload = fullySupportedResultPayload();
    payload.analysis.runner.name = `REJECTED DETAIL ${label}`;
    payload.analysis.private_internal_value = `PRIVATE ${label}`;
    payload.source_payload = {raw_secret: `SOURCE ${label}`};
    if (value === undefined) delete payload.event.capabilities.results;
    else payload.event.capabilities.results = value;
    const fixture = resultRoot(payload);

    await View.initBrowser(fixture.root);

    const detail = fixture.document.getElementById('resultsDetail');
    observations.push({
      label,
      state: fixture.document.getElementById('resultsStatus').dataset.state,
      runnerHeaders: byClass(detail, 'results-runner-header').length,
      sectionRows: byClass(detail, 'results-section-name').length,
      energyInputs: byClass(detail, 'results-weight-field').length,
      reportsUnavailable: /result is unavailable right now/i.test(detail.textContent),
      leakedSource: /REJECTED DETAIL|PRIVATE|SOURCE|private_internal_value|source_payload|raw_secret/.test(detail.textContent),
    });
  }
  assert.deepEqual(observations, invalidValues.map(([label]) => ({
    label,
    state: 'error',
    runnerHeaders: 0,
    sectionRows: 0,
    energyInputs: 0,
    reportsUnavailable: true,
    leakedSource: false,
  })));
});

test('event intermediate-splits false overrides all nested section-derived claims', async () => {
  const payload = fullySupportedResultPayload();
  payload.event.capabilities = {
    results: true,
    intermediate_splits: false,
    course_map: false,
    elevation: false,
    live_gps: false,
  };
  payload.analysis.sections[0].start_name = 'PRIVATE SPLIT SOURCE';
  payload.analysis.private_internal_value = 'PRIVATE INTERNAL VALUE';
  payload.source_payload = {raw_secret: 'RAW SOURCE VALUE'};
  const fixture = resultRoot(payload);

  await View.initBrowser(fixture.root);

  const detail = fixture.document.getElementById('resultsDetail');
  const lead = fixture.document.getElementById('resultsRunnerLead');
  const text = lead.textContent + detail.textContent;
  assert.equal(fixture.document.getElementById('resultsStatus').dataset.state, 'ready');
  assert.equal(byClass(detail, 'results-section-name').length, 0);
  assert.equal(byClass(detail, 'results-summary').length, 0);
  assert.equal(byClass(lead, 'results-weight-field').length, 0);
  assert.match(text, /Recorded sections0/);
  assert.match(text, /Intermediate split timing was not available; only supported overall facts are shown/);
  assert.match(text, /Overall field percentiles and rank comparisons were not available/);
  assert.match(text, /Distance, grade, elevation, and pace detail were not derived/);
  assert.match(text, /Active-calorie estimates were not derived/);
  assert.doesNotMatch(
    text,
    /PRIVATE SPLIT SOURCE|PRIVATE INTERNAL VALUE|RAW SOURCE VALUE|private_internal_value|source_payload|raw_secret|Consistency3|TrendHigher|50th percentile|1\.00 km|\+100 m|Total weight/,
  );
});

test('event intermediate-splits capability rejects every malformed non-boolean claim', async () => {
  const malformedValues = [undefined, null, 1, 'true', [], {}];
  const observations = [];
  for (const value of malformedValues) {
    const payload = fullySupportedResultPayload();
    if (value === undefined) delete payload.event.capabilities.intermediate_splits;
    else payload.event.capabilities.intermediate_splits = value;
    const fixture = resultRoot(payload);

    await View.initBrowser(fixture.root);

    const detail = fixture.document.getElementById('resultsDetail');
    observations.push({
      sectionRows: byClass(detail, 'results-section-name').length,
      summaries: byClass(detail, 'results-summary').length,
      energyInputs: byClass(detail, 'results-weight-field').length,
      limited: /Intermediate split timing was not available/.test(detail.textContent),
    });
  }
  assert.deepEqual(observations, malformedValues.map(() => ({
    sectionRows: 0,
    summaries: 0,
    energyInputs: 0,
    limited: true,
  })));
});

test('event course-map and elevation gates suppress terrain and energy but retain timing and field', async () => {
  const malformedValues = [false, undefined, null, 1, 'true', [], {}];
  const observations = [];
  for (const gate of ['course_map', 'elevation']) {
    for (const value of malformedValues) {
      const payload = fullySupportedResultPayload();
      if (value === undefined) delete payload.event.capabilities[gate];
      else payload.event.capabilities[gate] = value;
      const fixture = resultRoot(payload);

      await View.initBrowser(fixture.root);

      const detail = fixture.document.getElementById('resultsDetail');
      const lead = fixture.document.getElementById('resultsRunnerLead');
      const sectionTable = byClass(detail, 'results-section-table')[0];
      observations.push({
        gate,
        sectionRows: byClass(detail, 'results-section-name').length,
        keptTimingAndField: /Start → Aid One/.test(sectionTable.textContent)
          && /6:40/.test(sectionTable.textContent)
          && /50th percentile/.test(sectionTable.textContent)
          && /4 comparable/.test(sectionTable.textContent),
        leakedTerrain: /Pace|Distance|Grade \/ elevation|8:20 \/mi|1\.00 km|\+100 m/.test(sectionTable.textContent),
        keptFieldSummary: /Consistency3 comparable sections/.test(detail.textContent)
          && /TrendHigher relative percentiles/.test(detail.textContent),
        energyInputs: byClass(lead, 'results-weight-field').length,
        honestUnavailable: /Course terrainThe published route could not support terrain detail/.test(detail.textContent)
          && /Optional calorie estimateThe route did not support the optional weight-based estimate/.test(detail.textContent)
          && /Distance, grade, elevation, and pace detail were not derived/.test(detail.textContent)
          && /Active-calorie estimates were not derived/.test(detail.textContent),
      });
    }
  }
  assert.deepEqual(observations, ['course_map', 'elevation'].flatMap((gate) => malformedValues.map(() => ({
    gate,
    sectionRows: 3,
    keptTimingAndField: true,
    leakedTerrain: false,
    keptFieldSummary: true,
    energyInputs: 0,
    honestUnavailable: true,
  }))));
});

test('newer search wins reverse resolution and abort-ignoring fetch', async () => {
  const requests = [];
  const fixture = interactiveRoot((url, options) => {
    const wait = deferred();
    requests.push({url, options, wait});
    return wait.promise;
  });
  await View.initBrowser(fixture.root);
  const input = fixture.document.getElementById('resultsRunnerSearch');
  const form = fixture.document.getElementById('resultsSearchForm');
  input.value = 'Old Runner';
  form.dispatch('submit');
  input.value = 'New Runner';
  form.dispatch('submit');
  assert.equal(requests.length, 2);
  assert.equal(requests[0].options.signal.aborted, true);
  requests[1].wait.resolve(jsonResponse(searchPayload([{id: 2, name: 'New Runner', status: 'FINISHED'}])));
  await flushAsync();
  requests[0].wait.resolve(jsonResponse(searchPayload([{id: 1, name: 'Old Runner', status: 'FINISHED'}])));
  await flushAsync();
  const text = fixture.document.getElementById('resultsMatches').textContent;
  assert.match(text, /New Runner/);
  assert.doesNotMatch(text, /Old Runner/);
  assert.equal(fixture.document.getElementById('resultsSearchSubmit').disabled, false);
});

test('rejected stale search cannot replace a newer successful search', async () => {
  const requests = [];
  const fixture = interactiveRoot((url, options) => {
    const wait = deferred();
    requests.push({url, options, wait});
    return wait.promise;
  });
  await View.initBrowser(fixture.root);
  const input = fixture.document.getElementById('resultsRunnerSearch');
  const form = fixture.document.getElementById('resultsSearchForm');
  input.value = 'Old Runner';
  form.dispatch('submit');
  input.value = 'New Runner';
  form.dispatch('submit');
  requests[1].wait.resolve(jsonResponse(searchPayload([{id: 2, name: 'New Runner', status: 'FINISHED'}])));
  await flushAsync();
  requests[0].wait.reject(new Error('late failure'));
  await flushAsync();
  assert.match(fixture.document.getElementById('resultsMatches').textContent, /New Runner/);
  assert.equal(fixture.document.getElementById('resultsStatus').dataset.state, 'ready');
});

async function detailRaceFixture() {
  const detailRequests = [];
  const fixture = interactiveRoot((url, options) => {
    if (url.includes('/search')) {
      return Promise.resolve(jsonResponse(searchPayload([
        {id: 1, name: 'First Choice', status: 'FINISHED'},
        {id: 2, name: 'Second Choice', status: 'FINISHED'},
      ])));
    }
    const wait = deferred();
    detailRequests.push({url, options, wait});
    return wait.promise;
  });
  await View.initBrowser(fixture.root);
  fixture.document.getElementById('resultsRunnerSearch').value = 'Choice';
  fixture.document.getElementById('resultsSearchForm').dispatch('submit');
  await flushAsync();
  return {fixture, detailRequests, buttons: [...fixture.document.getElementById('resultsMatches').children]};
}

test('newer runner detail wins reverse resolution and abort-ignoring fetch', async () => {
  const {fixture, detailRequests, buttons} = await detailRaceFixture();
  buttons[0].dispatch('click');
  buttons[1].dispatch('click');
  assert.equal(detailRequests.length, 2);
  assert.equal(detailRequests[0].options.signal.aborted, true);
  detailRequests[1].wait.resolve(jsonResponse(resultPayload({runner: {id: 2, name: 'Second Detail'}})));
  await flushAsync();
  detailRequests[0].wait.resolve(jsonResponse(resultPayload({runner: {id: 1, name: 'First Detail'}})));
  await flushAsync();
  const text = fixture.document.getElementById('resultsRunnerLead').textContent
    + fixture.document.getElementById('resultsDetail').textContent;
  assert.match(text, /Second Detail/);
  assert.doesNotMatch(text, /First Detail/);
  assert.equal(fixture.document.getElementById('resultsDetail').getAttribute('aria-busy'), null);
});

test('a new search invalidates and safely ignores a rejected old detail request', async () => {
  const {fixture, detailRequests, buttons} = await detailRaceFixture();
  buttons[0].dispatch('click');
  fixture.document.getElementById('resultsRunnerSearch').value = 'Replacement';
  fixture.document.getElementById('resultsSearchForm').dispatch('submit');
  await flushAsync();
  assert.equal(detailRequests[0].options.signal.aborted, true);
  detailRequests[0].wait.reject(new Error('late detail failure'));
  await flushAsync();
  const detail = fixture.document.getElementById('resultsDetail');
  assert.equal(detail.textContent, '');
  assert.equal(detail.hidden, true);
  assert.equal(fixture.document.getElementById('resultsStatus').dataset.state, 'ready');
});

test('teardown aborts both request channels and prevents late rendering', async () => {
  const requests = [];
  const fixture = interactiveRoot((url, options) => {
    const wait = deferred();
    requests.push({url, options, wait});
    return wait.promise;
  });
  const ready = await View.initBrowser(fixture.root);
  assert.strictEqual(await View.initBrowser(fixture.root), ready);
  fixture.document.getElementById('resultsRunnerSearch').value = 'Runner';
  fixture.document.getElementById('resultsSearchForm').dispatch('submit');
  assert.equal(typeof ready.teardown, 'function');
  ready.teardown();
  assert.equal(ready.active, false);
  assert.equal(requests[0].options.signal.aborted, true);
  requests[0].wait.resolve(jsonResponse(searchPayload([{id: 1, name: 'Too Late', status: 'FINISHED'}])));
  await flushAsync();
  assert.doesNotMatch(fixture.document.getElementById('resultsMatches').textContent, /Too Late/);
  fixture.document.getElementById('resultsSearchForm').dispatch('submit');
  assert.equal(requests.length, 1);

  const restarted = await View.initBrowser(fixture.root);
  assert.notStrictEqual(restarted, ready);
  fixture.document.getElementById('resultsRunnerSearch').value = 'Runner Again';
  fixture.document.getElementById('resultsSearchForm').dispatch('submit');
  assert.equal(requests.length, 2);
  restarted.teardown();
  assert.equal(requests[1].options.signal.aborted, true);
});

test('public payload limits enforce exact boundaries before DOM and SVG work', async () => {
  const limits = View.LIMITS;
  assert.deepEqual(limits, {
    responseBytes: 1000000,
    trackPoints: 2000,
    sections: 100,
    limitations: 20,
    publicText: 500,
    fieldCount: 100000,
    matches: 20,
  });

  const prefix = '{"status":"ok","padding":"';
  const suffix = '"}';
  const exact = prefix + 'x'.repeat(limits.responseBytes - Buffer.byteLength(prefix + suffix)) + suffix;
  assert.equal(Buffer.byteLength(exact), limits.responseBytes);
  assert.equal(View.parsePayloadText(exact).status, 'ok');
  assert.throws(() => View.parsePayloadText(`${exact}x`), /large|size|bytes/i);

  const oversizedPayload = resultPayload();
  oversizedPayload.padding = 'x'.repeat(limits.responseBytes);
  const oversized = resultRoot(oversizedPayload);
  await View.initBrowser(oversized.root);
  assert.match(oversized.document.getElementById('resultsDetail').textContent, /unavailable right now/i);

  const track = Array.from({length: 150000}, (_value, index) => ({
    lat: 40 + index / 1000000,
    lng: -110 + index / 1000000,
    ele: 1000 + index / 1000,
  }));
  const course = View.courseSvgPoints(track, 480, 260, 20);
  const elevation = View.elevationSvgPoints(track, 480, 220, 20);
  assert.equal(coordinates(course).length, limits.trackPoints);
  assert.equal(coordinates(elevation.points).length, limits.trackPoints);
  const endpointOnly = coordinates(View.courseSvgPoints([track[0], track.at(-1)], 480, 260, 20));
  assert.deepEqual(coordinates(course)[0], endpointOnly[0]);
  assert.deepEqual(coordinates(course).at(-1), endpointOnly.at(-1));
  assert.equal(coordinates(elevation.points)[0][0], 20);
  assert.equal(coordinates(elevation.points).at(-1)[0], 460);

  const sections = Array.from({length: limits.sections + 1}, (_value, index) => ({
    start_index: index,
    start_name: `Point ${index}`,
    end_index: index + 1,
    end_name: `Point ${index + 1}`,
    status: 'recorded',
    segment_seconds: 60,
    cumulative_seconds: (index + 1) * 60,
    field_count: index === 0 ? limits.fieldCount : limits.fieldCount + 1,
    field_percentile: 50,
  }));
  const payload = resultPayload({
    runner: {name: 'N'.repeat(limits.publicText + 1), last_recorded_split_index: limits.sections + 1},
    sections,
    summary: {overall_field_count: limits.fieldCount},
    capabilities: {splits: true, field_comparison: true},
  });
  payload.analysis.limitations = Array.from({length: limits.limitations + 1}, (_value, index) => `Limit ${index}`);
  const fixture = resultRoot(payload);
  await View.initBrowser(fixture.root);
  const detail = fixture.document.getElementById('resultsDetail');
  assert.equal(byClass(detail, 'results-section-name').length, limits.sections);
  const limitationDetails = byClass(detail, 'results-limitations')[0];
  assert.equal(descendants(limitationDetails).filter((node) => node.tagName === 'LI').length, limits.limitations);
  const runnerHeading = descendants(fixture.document.getElementById('resultsRunnerLead')).find((node) => node.tagName === 'H2');
  assert.equal(runnerHeading.textContent.length, limits.publicText);
  assert.match(detail.textContent, /100,000 comparable/);
  assert.doesNotMatch(detail.textContent, /100,001 comparable/);
});
