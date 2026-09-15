'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');

const Shell = require('./course_signal_shell.js');

const ALL_NODE_IDS = [
  'courseSignalLanding', 'courseSignalRouteLoading', 'courseSignalResults', 'app',
  'catalogSearch', 'catalogYear', 'catalogResults', 'catalogStatus',
  'routeLoadingStatus', 'routeLoadingHome', 'liveModeLink', 'resultsModeLink',
  'resultsLiveModeLink', 'resultsResultsModeLink', 'liveEventContext', 'resultsEventContext',
];

function browserHarness(
  route = {kind: 'landing', race: null, event: null, mode: null, explicitMode: false},
  locationPath = '/',
) {
  let document;
  const makeNode = () => {
    const listeners = new Map();
    const attributes = new Map();
    const node = {
      hidden: false,
      value: '',
      min: '',
      max: '',
      href: '',
      className: '',
      textContent: '',
      dataset: {},
      children: [],
      focusCount: 0,
      addEventListener(type, listener) {
        if (!listeners.has(type)) listeners.set(type, new Set());
        listeners.get(type).add(listener);
      },
      removeEventListener(type, listener) { listeners.get(type)?.delete(listener); },
      listenerCount(type) { return listeners.get(type)?.size || 0; },
      dispatch(type) {
        for (const listener of [...(listeners.get(type) || [])]) listener({target: this});
      },
      focus() { this.focusCount += 1; document.activeElement = this; },
      appendChild(child) { this.children.push(child); return child; },
      append(...children) { this.children.push(...children); },
      replaceChildren(...children) { this.children = children; },
      setAttribute(name, value) { attributes.set(name, String(value)); },
      getAttribute(name) { return attributes.get(name) ?? null; },
      removeAttribute(name) { attributes.delete(name); },
    };
    return node;
  };
  const nodes = Object.fromEntries(ALL_NODE_IDS.map((id) => [id, makeNode()]));
  const homeLinks = Array.from({length: 4}, makeNode);
  nodes.catalogYear.min = '2000';
  nodes.catalogYear.max = '2100';
  document = {
    body: {dataset: {}},
    title: '',
    activeElement: null,
    createElement() { return makeNode(); },
    getElementById(id) { return nodes[id] || null; },
    querySelectorAll(selector) {
      return selector === '.shell-brand, .results-search-link, .shell-home-link' ? homeLinks : [];
    },
  };
  const requests = [];
  const timers = new Map();
  let nextTimer = 0;
  let liveBoots = 0;
  const root = {
    document,
    location: {href: `https://course.test${locationPath}`, pathname: locationPath},
    fetch(url, options) {
      return new Promise((resolve, reject) => requests.push({url, options, resolve, reject}));
    },
    setTimeout(callback, delay) {
      const id = ++nextTimer;
      timers.set(id, {callback, delay});
      return id;
    },
    clearTimeout(id) { timers.delete(id); },
    __courseSignalLiveBoot() { liveBoots += 1; },
  };
  const shell = {route, shouldBootLive: route.kind === 'race' && route.mode === 'live'};
  const teardown = Shell.initBrowser(root, shell);
  const timerDelays = () => [...timers.values()].map(({delay}) => delay);
  const flushTimers = () => {
    const scheduled = [...timers.values()];
    timers.clear();
    return scheduled.map(({callback}) => callback());
  };
  return {
    document, nodes, homeLinks, requests, root, shell, teardown, timers, timerDelays, flushTimers,
    liveBoots: () => liveBoots,
  };
}

function catalogSearchHarness() {
  return browserHarness();
}

function catalogResponse(races = []) {
  return {ok: true, status: 200, async json() { return {status: 'ok', races}; }};
}

test('route parser accepts only canonical same-origin deployment-root race routes', () => {
  const defaultRoute = {
    kind: 'race', race: 'the-rut', event: 'the-rut-28k-2026', mode: 'results', explicitMode: true,
  };
  const invalid = {kind: 'invalid', race: null, event: null, mode: null, explicitMode: false};
  assert.deepEqual(Shell.parseRoute('https://course.test/'), defaultRoute);
  assert.deepEqual(Shell.parseRoute('https://course.test/?race=the-rut&event=evt-21k&mode=live'), {
    kind: 'race', race: 'the-rut', event: 'evt-21k', mode: 'live', explicitMode: true,
  });
  assert.deepEqual(Shell.parseRoute('/?race=the-rut&event=evt-50k'), {
    kind: 'race', race: 'the-rut', event: 'evt-50k', mode: null, explicitMode: false,
  });
  assert.deepEqual(
    Shell.parseRoute('/course-signal/?race=rut&event=50k&mode=results', 'https://pages.test/course-signal/'),
    {kind: 'race', race: 'rut', event: '50k', mode: 'results', explicitMode: true},
  );
  assert.deepEqual(Shell.parseRoute('https://evil.test/?race=rut&event=50k', 'https://course.test/'), invalid);
  assert.deepEqual(Shell.parseRoute('https://course.test/other/?race=rut&event=50k', 'https://course.test/'), invalid);
});

test('the public shell defaults to Rut 28K Results and accepts all five Rut 2026 events only', () => {
  const fixed = browserHarness(Shell.parseRoute('https://course.test/'));
  assert.equal(fixed.requests.length, 0);
  assert.equal(fixed.liveBoots(), 0);
  assert.equal(fixed.shell.shouldBootLive, false);
  assert.equal(fixed.document.body.dataset.courseSignalView, 'results');
  assert.equal(fixed.nodes.liveModeLink.href, '/?race=the-rut&event=the-rut-28k-2026&mode=live');
  assert.equal(fixed.nodes.resultsModeLink.href, '/?race=the-rut&event=the-rut-28k-2026&mode=results');
  fixed.teardown();

  for (const event of [
    'the-rut-50k-2026', 'the-rut-28k-2026', 'the-rut-21k-2026',
    'the-rut-11k-2026', 'the-rut-vk-2026',
  ]) {
    const results = browserHarness({kind: 'race', race: 'the-rut', event, mode: 'results', explicitMode: true});
    assert.equal(results.requests.length, 0);
    assert.equal(results.shell.shouldBootLive, false);
    assert.equal(results.document.body.dataset.courseSignalView, 'results');
    assert.equal(results.nodes.resultsModeLink.href, `/?race=the-rut&event=${event}&mode=results`);
    results.teardown();

    const live = browserHarness({kind: 'race', race: 'the-rut', event, mode: 'live', explicitMode: true});
    assert.equal(live.requests.length, 0);
    assert.equal(live.shell.shouldBootLive, true);
    assert.equal(live.document.body.dataset.courseSignalView, 'live');
    assert.equal(live.nodes.liveModeLink.href, `/?race=the-rut&event=${event}&mode=live`);
    live.teardown();
  }

  for (const route of [
    {kind: 'race', race: 'other-race', event: 'the-rut-28k-2026', mode: 'results', explicitMode: true},
    {kind: 'race', race: 'the-rut', event: 'the-rut-100k-2026', mode: 'results', explicitMode: true},
  ]) {
    const rejected = browserHarness(route);
    assert.equal(rejected.requests.length, 0);
    assert.equal(rejected.liveBoots(), 0);
    assert.equal(rejected.shell.shouldBootLive, false);
    assert.equal(rejected.document.body.dataset.courseSignalView, 'loading');
    assert.match(rejected.nodes.routeLoadingStatus.textContent, /five Rut 2026 distances/i);
    rejected.teardown();
  }
});

test('route parser accepts one canonical public runner only on explicit Results routes', () => {
  const invalid = {kind: 'invalid', race: null, event: null, mode: null, explicitMode: false};
  const maximum = '9007199254740991';
  const directRoute = Shell.parseRoute(
    `/?race=the-rut&event=the-rut-28k-2026&mode=results&runner=${maximum}`,
    'https://course.test/',
  );
  assert.deepEqual(directRoute, {
    kind: 'race', race: 'the-rut', event: 'the-rut-28k-2026', mode: 'results', explicitMode: true, runner: maximum,
  });
  const direct = browserHarness(directRoute);
  assert.equal(direct.requests.length, 0);
  assert.equal(direct.liveBoots(), 0);
  assert.equal(direct.shell.shouldBootLive, false);
  assert.equal(direct.document.body.dataset.courseSignalView, 'results');
  assert.equal(direct.nodes.liveModeLink.href, '/?race=the-rut&event=the-rut-28k-2026&mode=live');
  assert.equal(direct.nodes.resultsModeLink.href, '/?race=the-rut&event=the-rut-28k-2026&mode=results');
  direct.teardown();
  for (const route of [
    '/?race=rut&event=50k&runner=42',
    '/?race=rut&event=50k&mode=live&runner=42',
    '/?race=rut&event=50k&mode=results&runner=',
    '/?race=rut&event=50k&mode=results&runner=1&runner=2',
    '/?race=rut&event=50k&mode=results&runner=runner-42',
    '/?race=rut&event=50k&mode=results&runner=01',
    '/?race=rut&event=50k&mode=results&runner=-1',
    '/?race=rut&event=50k&mode=results&runner=+1',
    '/?race=rut&event=50k&mode=results&runner=1.5',
    '/?race=rut&event=50k&mode=results&runner=1e3',
    '/?race=rut&event=50k&mode=results&runner=١',
    '/?race=rut&event=50k&mode=results&runner=１',
    '/?race=rut&event=50k&mode=results&runner=9007199254740992',
    '/?race=rut&event=50k&mode=results&runner=42&weight=180',
    '/?race=rut&event=50k&mode=results&runner=42&q=name',
    '/?race=rut&event=50k&mode=results&runner=42&calories=2000',
    '/?race=rut&event=50k&mode=results&runner=42&unknown=value',
  ]) assert.deepEqual(Shell.parseRoute(route, 'https://course.test/'), invalid, route);
});

test('route parser fails closed on malformed, ambiguous, duplicate, or private routes', () => {
  const invalid = {kind: 'invalid', race: null, event: null, mode: null, explicitMode: false};
  for (const route of [
    'ftp://course.test/?race=rut&event=50k',
    'https://user:pass@course.test/?race=rut&event=50k',
    '//evil.test/?race=rut&event=50k',
    '/\\evil.test/?race=rut&event=50k',
    '/%2f%2fevil.test/?race=rut&event=50k',
    '/?race=rut&event=50k#literal',
    '/?race=rut&event=50k&runner=private-id',
    '/?race=rut&race=other&event=50k',
    '/?race=rut&event=50k&event=other',
    '/?race=rut&event=50k&mode=RESULTS',
    '/?race=rut&event=50k&mode=',
    '/?race=the+rut&event=50k',
    '/?race=rut&event=evt%2F50k',
    '/?race=rut&event=50k%00',
    '/?race=rut',
    '/?event=50k',
  ]) assert.deepEqual(Shell.parseRoute(route, 'https://course.test/'), invalid, route);
});

test('race URLs accept bounded canonical identifiers and preserve Results isolation', () => {
  assert.equal(
    Shell.buildRaceUrl({race: 'The-Rut_2026', event: 'event.id~7', mode: 'results', runner: 'must-not-leak'}),
    '/?race=The-Rut_2026&event=event.id~7&mode=results',
  );
  assert.equal(
    Shell.buildRaceUrl({race: 'rut', event: '21k', mode: 'live', basePath: '/course-signal/'}),
    '/course-signal/?race=rut&event=21k&mode=live',
  );
  assert.equal(Shell.buildRaceUrl({race: 'r'.repeat(200), event: '21k'}), `/?race=${'r'.repeat(200)}&event=21k`);
  for (const value of ['', 'the rut', 'evt/21k', 'nul\0id', 'r'.repeat(201)]) {
    assert.throws(() => Shell.buildRaceUrl({race: value, event: '21k'}), /race/i, JSON.stringify(value));
    assert.throws(() => Shell.buildRaceUrl({race: 'rut', event: value}), /event/i, JSON.stringify(value));
  }
  assert.throws(() => Shell.buildRaceUrl({race: 'rut', event: '21k', mode: 'replay'}), /mode/i);
  assert.throws(() => Shell.buildRaceUrl({race: 'rut', event: '21k', basePath: '/unexpected'}), /path/i);
});

test('automatic mode sends active race phases live and completed phases to results', () => {
  for (const status of ['upcoming', 'active', 'awaiting', 'racing', 'started', 'in_progress']) {
    assert.equal(Shell.automaticMode({course_status: status}), 'live', status);
  }
  for (const status of ['closed', 'past', 'complete', 'completed', 'finished']) {
    assert.equal(Shell.automaticMode({course_status: status}), 'results', status);
  }
  assert.equal(Shell.automaticMode({course_status: 'unknown'}), 'live');
});

test('automatic mode uses strict event dates and one UTC calendar convention', () => {
  const now = new Date('2026-09-14T12:00:00Z');
  assert.equal(Shell.automaticMode({course_status: 'unknown', event_date: '2026-09-13'}, now), 'results');
  assert.equal(Shell.automaticMode({event_date: '2026-09-14'}, now), 'live');
  assert.equal(Shell.automaticMode({course_status: 'unknown', event_date: '2026-09-15'}, now), 'live');
  assert.equal(Shell.automaticMode({event_date: '2026-09-13T23:59:59Z'}, now), 'results');
  assert.equal(Shell.automaticMode({event_date: '2026-09-13T23:30:00-14:00'}, now), 'live');
  for (const malformed of [
    '2026-09-13garbage', '2026-09-13Tgarbage', '2026-09-13T25:00:00Z',
    '2026-09-13T12:00:00', '2026-02-30', '2026-09-13T12:00:00+15:00',
  ]) assert.equal(Shell.automaticMode({event_date: malformed}, now), 'live', malformed);

  const model = Shell.catalogRenderModel([{
    slug: 'fallback-race', name: 'Fallback Race', raceType: 'trail', location: '', events: [{
      id: 'past-event', name: 'Past Event', label: '', eventDate: '2026-09-13',
      courseStatus: 'unknown', liveTrackingEnabled: false,
    }],
  }], now);
  assert.equal(model.races[0].events[0].mode, 'results');
  assert.equal(model.races[0].events[0].url, '/?race=fallback-race&event=past-event&mode=results');
});

test('catalog normalization is bounded, rejects unusable rows, and builds grouped event choices', () => {
  const races = Array.from({length: 32}, (_, index) => ({
    slug: index ? `race-${index}` : 'wild-race',
    name: index ? `Race ${index}` : '<img src=x onerror=alert(1)>',
    race_type: 'trail',
    location: 'Somewhere',
    events: index === 1 ? [{name: 'Missing id'}] : [{
      id: `event-${index}`,
      name: index ? `${index}K` : '<script>unsafe()</script>',
      label: index ? undefined : 'Distance <21K>',
      event_date: '2026-09-12',
      course_status: index ? 'active' : 'closed',
      live_tracking_enabled: index !== 0,
    }],
  }));
  const normalized = Shell.normalizeCatalog({status: 'ok', races});
  assert.equal(normalized.length, 30);
  assert.equal(normalized[1].events.length, 0);
  assert.equal(normalized[0].name, '<img src=x onerror=alert(1)>');
  assert.equal(normalized[0].events[0].name, '<script>unsafe()</script>');

  const model = Shell.catalogRenderModel(normalized);
  assert.equal(model.races.length, 30);
  assert.equal(model.races[0].events[0].mode, 'results');
  assert.equal(model.races[0].events[0].url, '/?race=wild-race&event=event-0&mode=results');
  assert.equal(model.races[0].events[0].liveUrl, '/?race=wild-race&event=event-0&mode=live');
  assert.equal(model.races[0].events[0].resultsUrl, '/?race=wild-race&event=event-0&mode=results');
});

test('catalog limits bound source work, public text, duplicate IDs, and rendered events', () => {
  assert.deepEqual(Shell.LIMITS, {
    maxCatalogRaces: 30,
    maxEventsPerRace: 100,
    maxCatalogEvents: 300,
    maxPublicTextLength: 200,
    maxIdentifierLength: 200,
    maxQueryLength: 100,
    minCatalogYear: 2000,
    maxCatalogYear: 2100,
    searchDebounceMs: 300,
  });
  const hugeRace = {
    slug: 'race-a',
    name: 'N'.repeat(201),
    race_type: 'trail',
    location: 'L'.repeat(201),
    events: Array.from({length: 5000}, (_, index) => ({
      id: `event-${index}`,
      name: `Event ${index}`,
      label: 'X'.repeat(201),
      event_date: '2026-09-14',
      course_status: 'active',
    })),
  };
  const bounded = Shell.normalizeCatalog({status: 'ok', races: [hugeRace]});
  assert.equal(bounded.length, 1);
  assert.equal(bounded[0].name.length, 200);
  assert.equal(bounded[0].location.length, 200);
  assert.equal(bounded[0].events.length, 100);
  assert.equal(bounded[0].events[0].id, 'event-0');
  assert.equal(bounded[0].events[99].id, 'event-99');
  assert.equal(bounded[0].events[0].label.length, 200);
  assert.equal(Shell.catalogRenderModel(bounded).races[0].events.length, 100);

  const many = Array.from({length: 4}, (_, raceIndex) => ({
    slug: `race-${raceIndex}`,
    name: `Race ${raceIndex}`,
    events: Array.from({length: 100}, (_, eventIndex) => ({
      id: `event-${raceIndex}-${eventIndex}`,
      name: `Event ${eventIndex}`,
    })),
  }));
  const totalBounded = Shell.normalizeCatalog({status: 'ok', races: many});
  assert.deepEqual(totalBounded.map((race) => race.events.length), [100, 100, 100, 0]);

  const deduped = Shell.normalizeCatalog({status: 'ok', races: [
    {slug: 'first', name: 'First', events: [{id: 'shared', name: 'First event'}, {id: 'shared', name: 'Duplicate'}]},
    {slug: 'first', name: 'Duplicate race', events: []},
    {slug: 'second', name: 'Second', events: [{id: 'shared', name: 'Duplicate event'}, {id: 'unique', name: 'Unique'}]},
  ]});
  assert.deepEqual(deduped.map((race) => race.slug), ['first', 'second']);
  assert.deepEqual(deduped.map((race) => race.events.map((event) => event.id)), [['shared'], ['unique']]);
});

test('catalog search debounce is exactly 300 ms', () => {
  const {nodes, timerDelays} = catalogSearchHarness();
  nodes.catalogSearch.value = 'alpha';
  nodes.catalogSearch.dispatch('input');
  assert.deepEqual(timerDelays(), [300]);
});

test('catalog search accepts 2–100 normalized Unicode characters and immediately rejects 101+', async () => {
  assert.equal(Shell.normalizeQuery('e\u0301 e\u0301'), 'é é');
  assert.equal(Shell.normalizeQuery('x'.repeat(100)), 'x'.repeat(100));
  assert.equal(Shell.normalizeQuery('x'.repeat(101)), null);

  for (const [input, expected] of [
    ['ab', 'ab'],
    ['e\u0301'.repeat(100), 'é'.repeat(100)],
  ]) {
    const {nodes, requests, timerDelays, flushTimers} = catalogSearchHarness();
    nodes.catalogSearch.value = input;
    nodes.catalogSearch.dispatch('input');
    assert.deepEqual(timerDelays(), [300], String(Array.from(expected).length));
    const [search] = flushTimers();
    assert.equal(requests.length, 1);
    assert.equal(new URL(requests[0].url, 'https://course.test/').searchParams.get('q'), expected);
    requests[0].resolve(catalogResponse());
    await search;
  }

  const {nodes, requests, timers, flushTimers} = catalogSearchHarness();
  nodes.catalogSearch.value = 'ab';
  nodes.catalogSearch.dispatch('input');
  const [pending] = flushTimers();
  assert.equal(requests.length, 1);
  nodes.catalogSearch.value = 'x'.repeat(101);
  nodes.catalogSearch.dispatch('input');
  assert.equal(requests[0].options.signal.aborted, true);
  assert.equal(timers.size, 0);
  assert.equal(requests.length, 1);
  assert.equal(nodes.catalogStatus.textContent, 'Search terms must contain 2 to 100 characters.');
  assert.equal(nodes.catalogStatus.dataset.state, 'instruction');
  requests[0].resolve(catalogResponse());
  await pending;
});

test('search and year changes immediately abort and invalidate in-flight catalog requests', async () => {
  const {nodes, requests, flushTimers} = catalogSearchHarness();
  nodes.catalogSearch.value = 'alpha';
  nodes.catalogSearch.dispatch('input');
  const [firstSearch] = flushTimers();
  assert.equal(requests.length, 1);

  nodes.catalogSearch.value = 'beta';
  nodes.catalogSearch.dispatch('input');
  assert.equal(requests[0].options.signal.aborted, true, 'search input must abort before the next debounce');
  requests[0].resolve(catalogResponse());
  await firstSearch;
  assert.doesNotMatch(nodes.catalogStatus.textContent, /alpha/, 'a fetch that ignores abort must still be sequence-guarded');

  const [secondSearch] = flushTimers();
  assert.equal(requests.length, 2);
  assert.notEqual(requests[1].options.signal, requests[0].options.signal);
  assert.equal(requests[1].options.signal.aborted, false);

  nodes.catalogYear.value = '2026';
  nodes.catalogYear.dispatch('input');
  assert.equal(requests[1].options.signal.aborted, true, 'year input must abort before the next debounce');
  requests[1].resolve(catalogResponse());
  await secondSearch;
  assert.equal(nodes.catalogStatus.dataset.state, 'loading', 'a stale year response must not replace the loading state');

  const [thirdSearch] = flushTimers();
  assert.equal(requests.length, 3);
  assert.notEqual(requests[2].options.signal, requests[1].options.signal);
  assert.equal(new URL(requests[2].url, 'https://course.test/').searchParams.get('year'), '2026');
  requests[2].resolve(catalogResponse());
  await thirdSearch;
  assert.equal(nodes.catalogStatus.textContent, 'No races found for “beta”.');
});

test('catalog requests include only complete years within the year input bounds', async () => {
  const {nodes, requests, flushTimers} = catalogSearchHarness();
  nodes.catalogSearch.value = 'alpha';

  for (const [year, expected] of [
    ['', null],
    ['202', null],
    ['1999', null],
    ['2101', null],
    ['2026', '2026'],
  ]) {
    nodes.catalogYear.value = year;
    nodes.catalogYear.dispatch('input');
    const [search] = flushTimers();
    const request = requests.at(-1);
    const url = new URL(request.url, 'https://course.test/');
    assert.equal(url.searchParams.get('year'), expected, year || 'blank year');
    request.resolve(catalogResponse());
    await search;
  }
});

test('catalog requests enforce 2000–2100 when DOM year bounds are absent or mutated', async () => {
  const {nodes, requests, flushTimers} = catalogSearchHarness();
  nodes.catalogSearch.value = 'alpha';
  const observed = [];
  for (const {year, min, max} of [
    {year: '1999', min: '', max: ''},
    {year: '2101', min: '1900', max: '2200'},
    {year: '2000', min: '2050', max: '2051'},
    {year: '2100', min: '', max: ''},
  ]) {
    nodes.catalogYear.value = year;
    nodes.catalogYear.min = min;
    nodes.catalogYear.max = max;
    nodes.catalogYear.dispatch('input');
    const [search] = flushTimers();
    const request = requests.at(-1);
    observed.push(new URL(request.url, 'https://course.test/').searchParams.get('year'));
    request.resolve(catalogResponse());
    await search;
  }
  assert.deepEqual(observed, [null, null, '2000', '2100']);
});

test('catalog status announces the 30-race render cap and keeps names text-only', async () => {
  const {nodes, requests, flushTimers} = catalogSearchHarness();
  const unsafeName = '<img src=x onerror=alert(1)>';
  const races = Array.from({length: 31}, (_, index) => ({
    slug: `race-${index}`,
    name: index === 0 ? unsafeName : `Race ${index}`,
    race_type: 'trail',
    location: 'Somewhere',
    events: [],
  }));
  nodes.catalogSearch.value = 'race';
  nodes.catalogSearch.dispatch('input');
  const [search] = flushTimers();
  requests[0].resolve(catalogResponse(races));
  await search;

  assert.equal(nodes.catalogResults.children.length, 30);
  assert.equal(nodes.catalogResults.children[0].children[0].children[0].textContent, unsafeName);
  assert.equal(
    nodes.catalogStatus.textContent,
    'Showing the first 30 race series. Results are capped; refine your search to see more.',
  );
});

test('catalog status combines the source cap with zero usable race series', async () => {
  const {nodes, requests, flushTimers} = catalogSearchHarness();
  const races = Array.from({length: 31}, (_, index) => ({
    slug: '',
    name: `Unusable Race ${index}`,
    events: [],
  }));
  nodes.catalogSearch.value = 'race';
  nodes.catalogSearch.dispatch('input');
  const [search] = flushTimers();
  requests[0].resolve(catalogResponse(races));
  await search;

  assert.equal(nodes.catalogResults.children.length, 0);
  assert.equal(
    nodes.catalogStatus.textContent,
    'No usable race series found among the first 30 source results. Results are capped; refine your search to see more.',
  );
  assert.equal(nodes.catalogStatus.dataset.state, 'empty');
});

test('catalog treats an exact 30-row usable response as cap-bound unless strict metadata proves otherwise', async () => {
  const races = Array.from({length: 30}, (_, index) => ({
    slug: `race-${index}`,
    name: `Race ${index}`,
    events: [],
  }));
  assert.equal(Shell.normalizeCatalog({status: 'ok', races}).catalogMeta.sourceRaceCapped, true);
  assert.equal(Shell.normalizeCatalog({status: 'ok', races, has_more: false}).catalogMeta.sourceRaceCapped, false);
  assert.equal(Shell.normalizeCatalog({status: 'ok', races, has_more: 'false'}).catalogMeta.sourceRaceCapped, true);

  const {nodes, requests, flushTimers} = catalogSearchHarness();
  nodes.catalogSearch.value = 'race';
  nodes.catalogSearch.dispatch('input');
  const [search] = flushTimers();
  requests[0].resolve(catalogResponse(races));
  await search;
  assert.equal(nodes.catalogResults.children.length, 30);
  assert.equal(
    nodes.catalogStatus.textContent,
    'Showing the first 30 race series. Results are capped; refine your search to see more.',
  );
  assert.equal(nodes.catalogStatus.dataset.state, 'ready');
});

test('catalog treats an exact 30-row zero-usable response as cap-bound', async () => {
  const races = Array.from({length: 30}, (_, index) => ({
    slug: '',
    name: `Unusable Race ${index}`,
    events: [],
  }));
  const {nodes, requests, flushTimers} = catalogSearchHarness();
  nodes.catalogSearch.value = 'race';
  nodes.catalogSearch.dispatch('input');
  const [search] = flushTimers();
  requests[0].resolve(catalogResponse(races));
  await search;
  assert.equal(nodes.catalogResults.children.length, 0);
  assert.equal(
    nodes.catalogStatus.textContent,
    'No usable race series found among the first 30 source results. Results are capped; refine your search to see more.',
  );
  assert.equal(nodes.catalogStatus.dataset.state, 'empty');
});

test('catalog reports malformed and unusable provider responses without false empty claims', async () => {
  {
    const {nodes, requests, flushTimers} = catalogSearchHarness();
    nodes.catalogSearch.value = 'race';
    nodes.catalogSearch.dispatch('input');
    const [search] = flushTimers();
    requests[0].resolve({ok: true, status: 200, async json() { return {status: 'ok', races: {bad: true}}; }});
    await search;
    assert.equal(nodes.catalogStatus.textContent, 'Race search is unavailable right now. Check the connection and try again.');
    assert.equal(nodes.catalogStatus.dataset.state, 'error');
  }
  {
    const {nodes, requests, flushTimers} = catalogSearchHarness();
    nodes.catalogSearch.value = 'race';
    nodes.catalogSearch.dispatch('input');
    const [search] = flushTimers();
    requests[0].resolve(catalogResponse([
      {slug: '', name: 'Missing slug', events: []},
      {slug: 'missing-name', name: '', events: []},
    ]));
    await search;
    assert.equal(nodes.catalogStatus.textContent, 'No usable race series found for “race”.');
    assert.equal(nodes.catalogStatus.dataset.state, 'empty');
  }
});

test('catalog honestly announces deterministic event truncation', async () => {
  const {nodes, requests, flushTimers} = catalogSearchHarness();
  nodes.catalogSearch.value = 'race';
  nodes.catalogSearch.dispatch('input');
  const [search] = flushTimers();
  requests[0].resolve(catalogResponse([{
    slug: 'race-a',
    name: 'Race A',
    events: Array.from({length: 101}, (_, index) => ({id: `event-${index}`, name: `Event ${index}`})),
  }]));
  await search;
  const eventList = nodes.catalogResults.children[0].children[1];
  assert.equal(eventList.children.length, 100);
  assert.equal(
    nodes.catalogStatus.textContent,
    'Showing 1 race series and the first 100 events. Event results are capped; refine your search to see more.',
  );
  assert.equal(nodes.catalogStatus.dataset.state, 'ready');
});

test('initBrowser is idempotent and teardown removes listeners, timers, and requests', () => {
  const harness = catalogSearchHarness();
  const {nodes, requests, root, shell, teardown, timers, flushTimers} = harness;
  assert.equal(typeof teardown, 'function');
  assert.equal(nodes.catalogSearch.listenerCount('input'), 1);
  assert.equal(nodes.catalogYear.listenerCount('input'), 1);
  assert.equal(Shell.initBrowser(root, shell), teardown);
  assert.equal(nodes.catalogSearch.listenerCount('input'), 1);

  nodes.catalogSearch.value = 'alpha';
  nodes.catalogSearch.dispatch('input');
  const [pending] = flushTimers();
  assert.equal(requests.length, 1);
  assert.equal(Shell.initBrowser(root, shell), teardown);
  assert.equal(requests.length, 1, 'repeat init must not duplicate an in-flight request');
  assert.equal(requests[0].options.signal.aborted, false);

  teardown();
  teardown();
  assert.equal(requests[0].options.signal.aborted, true);
  assert.equal(nodes.catalogSearch.listenerCount('input'), 0);
  assert.equal(nodes.catalogYear.listenerCount('input'), 0);
  assert.equal(timers.size, 0);
  requests[0].resolve(catalogResponse());
  return pending;
});

test('teardown clears derived route state and supports a clean automatic-route reinit', async () => {
  const unresolvedRoute = {kind: 'race', race: 'the-rut', event: 'the-rut-28k-2026', mode: null, explicitMode: false};
  const harness = browserHarness(unresolvedRoute);
  harness.requests[0].resolve({
    ok: true,
    status: 200,
    async json() { return {events: [{id: 'the-rut-28k-2026', course_status: 'active'}]}; },
  });
  await new Promise((resolve) => setImmediate(resolve));
  assert.equal(harness.shell.route.mode, 'live');
  assert.equal(harness.shell.shouldBootLive, true);

  harness.teardown();
  assert.deepEqual(harness.shell.route, unresolvedRoute);
  assert.equal(harness.shell.shouldBootLive, false);

  const reinitializedTeardown = Shell.initBrowser(harness.root, harness.shell);
  assert.notEqual(reinitializedTeardown, harness.teardown);
  assert.equal(harness.requests.length, 2);
  assert.equal(harness.nodes.routeLoadingStatus.textContent, 'Checking event status…');
  reinitializedTeardown();
  assert.equal(harness.requests[1].options.signal.aborted, true);
});

test('all shell home navigation preserves a validated deployment subpath', () => {
  const harness = browserHarness({
    kind: 'race', race: 'the-rut', event: 'the-rut-28k-2026', mode: 'results', explicitMode: true,
  }, '/course-signal/');
  assert.equal(harness.nodes.routeLoadingHome.href, '/course-signal/');
  for (const link of harness.homeLinks) assert.equal(link.href, '/course-signal/');
  harness.teardown();
});

test('critical automatic, explicit Results, and invalid route branches stay isolated', async () => {
  const automatic = browserHarness({
    kind: 'race', race: 'the-rut', event: 'the-rut-28k-2026', mode: null, explicitMode: false,
  });
  assert.equal(automatic.nodes.routeLoadingStatus.textContent, 'Checking event status…');
  assert.equal(automatic.requests.length, 1);
  assert.equal(automatic.requests[0].url, '/api/event?event_id=the-rut-28k-2026&include_course=0');
  automatic.requests[0].resolve({
    ok: true,
    status: 200,
    async json() {
      return {events: [
        {id: 'other', course_status: 'active'},
        {id: 'the-rut-28k-2026', label: 'Rut 28K', course_status: 'completed'},
      ]};
    },
  });
  await new Promise((resolve) => setImmediate(resolve));
  assert.equal(automatic.shell.route.mode, 'results');
  assert.equal(automatic.shell.shouldBootLive, false);
  assert.equal(automatic.liveBoots(), 0);
  assert.equal(automatic.document.body.dataset.courseSignalView, 'results');
  assert.equal(automatic.nodes.resultsEventContext.textContent, 'Rut 28K');
  assert.equal(automatic.nodes.resultsModeLink.getAttribute('aria-current'), 'page');
  automatic.teardown();

  const explicitResults = browserHarness({
    kind: 'race', race: 'the-rut', event: 'the-rut-28k-2026', mode: 'results', explicitMode: true,
  }, '/course-signal/');
  assert.equal(explicitResults.requests.length, 0);
  assert.equal(explicitResults.liveBoots(), 0);
  assert.equal(explicitResults.document.body.dataset.courseSignalView, 'results');
  assert.equal(explicitResults.nodes.liveModeLink.href, '/course-signal/?race=the-rut&event=the-rut-28k-2026&mode=live');
  assert.equal(explicitResults.nodes.resultsModeLink.href, '/course-signal/?race=the-rut&event=the-rut-28k-2026&mode=results');
  explicitResults.teardown();

  const invalidRoute = Shell.parseRoute('/?race=rut&event=chosen&mode=invalid', 'https://course.test/');
  const invalid = browserHarness(invalidRoute);
  assert.equal(invalid.requests.length, 0);
  assert.equal(invalid.liveBoots(), 0);
  assert.equal(invalid.shell.shouldBootLive, false);
  assert.equal(invalid.nodes.routeLoadingStatus.textContent, 'This race link is invalid. Return to race search and choose an event again.');
  assert.equal(invalid.nodes.routeLoadingHome.hidden, false);
  assert.equal(invalid.document.activeElement, invalid.nodes.routeLoadingHome);
  invalid.teardown();

  const invalidExplicitMode = browserHarness({
    kind: 'race', race: 'rut', event: 'chosen', mode: null, explicitMode: true,
  });
  assert.equal(invalidExplicitMode.requests.length, 0);
  assert.equal(invalidExplicitMode.liveBoots(), 0);
  invalidExplicitMode.teardown();
});

test('initBrowser fails closed unless explicitMode and mode form a consistent route state', () => {
  for (const route of [
    {kind: 'race', race: 'rut', event: 'chosen', mode: 'live', explicitMode: false},
    {kind: 'race', race: 'rut', event: 'chosen', mode: 'results', explicitMode: false},
    {kind: 'race', race: 'rut', event: 'chosen', mode: null, explicitMode: true},
  ]) {
    const harness = browserHarness(route);
    assert.equal(harness.requests.length, 0, JSON.stringify(route));
    assert.equal(harness.liveBoots(), 0, JSON.stringify(route));
    assert.equal(harness.shell.shouldBootLive, false, JSON.stringify(route));
    assert.equal(harness.document.body.dataset.courseSignalView, 'loading', JSON.stringify(route));
    assert.equal(
      harness.nodes.routeLoadingStatus.textContent,
      'This race link is invalid. Return to race search and choose an event again.',
      JSON.stringify(route),
    );
    harness.teardown();
  }
});

test('automatic Live boots once, while teardown suppresses stale callbacks and focuses failures', async () => {
  const live = browserHarness({kind: 'race', race: 'the-rut', event: 'the-rut-28k-2026', mode: null, explicitMode: false});
  assert.equal(Shell.initBrowser(live.root, live.shell), live.teardown);
  assert.equal(live.requests.length, 1);
  live.requests[0].resolve({
    ok: true,
    status: 200,
    async json() { return {events: [{id: 'the-rut-28k-2026', course_status: 'active'}]}; },
  });
  await new Promise((resolve) => setImmediate(resolve));
  assert.equal(live.shell.route.mode, 'live');
  assert.equal(live.shell.shouldBootLive, true);
  assert.equal(live.liveBoots(), 1);
  assert.equal(live.document.body.dataset.courseSignalView, 'live');
  assert.equal(Shell.initBrowser(live.root, live.shell), live.teardown);
  assert.equal(live.liveBoots(), 1, 'resolved automatic state must stay inside its existing lifecycle');
  live.teardown();

  const stale = browserHarness({kind: 'race', race: 'the-rut', event: 'the-rut-28k-2026', mode: null, explicitMode: false});
  stale.teardown();
  assert.equal(stale.requests[0].options.signal.aborted, true);
  stale.requests[0].resolve({
    ok: true,
    status: 200,
    async json() { return {events: [{id: 'the-rut-28k-2026', course_status: 'active'}]}; },
  });
  await new Promise((resolve) => setImmediate(resolve));
  assert.equal(stale.liveBoots(), 0);

  const failed = browserHarness({kind: 'race', race: 'the-rut', event: 'the-rut-28k-2026', mode: null, explicitMode: false});
  failed.requests[0].resolve({ok: false, status: 503, async json() { return {}; }});
  await new Promise((resolve) => setImmediate(resolve));
  assert.equal(failed.nodes.routeLoadingStatus.textContent, 'This event could not be loaded. Return to race search and try again.');
  assert.equal(failed.nodes.routeLoadingHome.hidden, false);
  assert.equal(failed.document.activeElement, failed.nodes.routeLoadingHome);
  failed.teardown();
});

test('browser DOM-ready bootstrap initializes once and exposes working teardown', () => {
  const seed = browserHarness({kind: 'race', race: 'the-rut', event: 'the-rut-28k-2026', mode: 'results', explicitMode: true});
  seed.teardown();
  const {root, nodes} = seed;
  root.location = {
    href: 'https://course.test/?race=the-rut&event=the-rut-28k-2026&mode=results',
    pathname: '/',
  };
  root.document.readyState = 'loading';
  const listeners = new Set();
  root.addEventListener = (type, listener) => { if (type === 'DOMContentLoaded') listeners.add(listener); };
  root.removeEventListener = (type, listener) => { if (type === 'DOMContentLoaded') listeners.delete(listener); };
  const source = fs.readFileSync('course_signal_shell.js', 'utf8');
  vm.runInNewContext(source, {window: root, globalThis: root, URL, URLSearchParams, AbortController, WeakMap, Set});
  assert.equal(listeners.size, 1);
  for (const listener of [...listeners]) listener();
  assert.equal(listeners.size, 0);
  assert.equal(root.CourseSignalShell.shouldBootLive, false);
  assert.equal(root.document.body.dataset.courseSignalView, 'results');
  assert.equal(nodes.resultsModeLink.getAttribute('aria-current'), 'page');
  const teardown = root.CourseSignalShell.initBrowser();
  assert.equal(typeof teardown, 'function');
  assert.equal(root.CourseSignalShell.initBrowser(), teardown);
  root.CourseSignalShell.teardown();
});

test('browser teardown revokes pending Live boot state and reinit restores it from the route', () => {
  const seed = browserHarness({kind: 'race', race: 'the-rut', event: 'the-rut-28k-2026', mode: 'live', explicitMode: true});
  seed.teardown();
  const {root} = seed;
  root.location = {
    href: 'https://course.test/?race=the-rut&event=the-rut-28k-2026&mode=live',
    pathname: '/',
  };
  root.document.readyState = 'loading';
  const listeners = new Set();
  root.addEventListener = (type, listener) => { if (type === 'DOMContentLoaded') listeners.add(listener); };
  root.removeEventListener = (type, listener) => { if (type === 'DOMContentLoaded') listeners.delete(listener); };
  const source = fs.readFileSync('course_signal_shell.js', 'utf8');
  vm.runInNewContext(source, {window: root, globalThis: root, URL, URLSearchParams, AbortController, WeakMap, Set});

  assert.equal(root.CourseSignalShell.shouldBootLive, true);
  assert.equal(listeners.size, 1);
  root.CourseSignalShell.teardown();
  assert.equal(root.CourseSignalShell.shouldBootLive, false);
  assert.equal(listeners.size, 0);

  const firstTeardown = root.CourseSignalShell.initBrowser();
  assert.equal(root.CourseSignalShell.shouldBootLive, true);
  firstTeardown();
  assert.equal(root.CourseSignalShell.shouldBootLive, false);
  const secondTeardown = root.CourseSignalShell.initBrowser();
  assert.notEqual(secondTeardown, firstTeardown);
  assert.equal(root.CourseSignalShell.shouldBootLive, true);
  secondTeardown();
});

test('app startup remains inert for explicit Results routes with no Leaflet or polling', () => {
  const source = fs.readFileSync('app.js', 'utf8');
  const listeners = [];
  let intervals = 0;
  const window = {
    location: {hostname: 'course.test', search: ''},
    CourseSignalShell: {route: {event: 'chosen'}, shouldBootLive: false},
    RutRules: {finite: Number.isFinite},
    addEventListener(type, listener) { if (type === 'DOMContentLoaded') listeners.push(listener); },
  };
  vm.runInNewContext(source, {
    window,
    document: {getElementById() { throw new Error('Results must not touch the Live DOM'); }},
    URLSearchParams,
    Map,
    Set,
    WeakMap,
    Date,
    Intl,
    console,
    encodeURIComponent,
    setInterval() { intervals += 1; },
  });
  assert.equal(listeners.length, 1);
  assert.doesNotThrow(() => listeners[0](), 'Results must not initialize Leaflet');
  assert.equal(intervals, 0, 'Results must not start Live polling');
});

test('dynamic app boot URLs use the selected event while legacy tests retain Rut endpoints', () => {
  const source = fs.readFileSync('app.js', 'utf8');
  const startup = '  window.addEventListener("DOMContentLoaded", init);';
  const expose = '  window.qa = {BOOTSTRAP_URL,LIVE_URL,DYNAMIC_EVENT_ID};';
  assert.notEqual(source.replace(startup, expose), source, 'legacy startup seam must remain stable');
  const evaluate = (shell) => {
    const window = {location: {hostname: 'localhost', search: ''}, RutRules: {finite: Number.isFinite}};
    if (shell) window.CourseSignalShell = shell;
    vm.runInNewContext(source.replace(startup, expose), {
      window, URLSearchParams, Map, Set, WeakMap, Date, Intl, console, encodeURIComponent,
    });
    return window.qa;
  };
  assert.deepEqual(
    JSON.parse(JSON.stringify(evaluate({route: {event: 'evt/id'}, shouldBootLive: true}))),
    {
      BOOTSTRAP_URL: '/api/event?event_id=evt%2Fid&include_course=1',
      LIVE_URL: '/api/event?event_id=evt%2Fid&include_course=0',
      DYNAMIC_EVENT_ID: 'evt/id',
    },
  );
  assert.deepEqual(
    JSON.parse(JSON.stringify(evaluate(null))),
    {BOOTSTRAP_URL: '/api/bootstrap', LIVE_URL: '/api/live', DYNAMIC_EVENT_ID: null},
  );
});

test('HTML loads the synchronous shell before app and provides one live workspace plus landing/results surfaces', () => {
  const html = fs.readFileSync('index.html', 'utf8');
  assert.ok(html.indexOf('course_signal_shell.js') < html.indexOf('app.js'));
  assert.equal((html.match(/id="workspace"/g) || []).length, 1);
  for (const id of ['courseSignalLanding', 'catalogSearch', 'catalogYear', 'catalogStatus', 'catalogResults', 'courseSignalResults', 'raceModeSwitch']) {
    assert.match(html, new RegExp(`id="${id}"`), id);
  }
});
