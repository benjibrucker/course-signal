/* Privacy-preserving completed-results UI. Public IDs are the only route state. */
(function attachResultsView(root, factory) {
  'use strict';
  const model = typeof module === 'object' && module.exports
    ? require('./results_model.js')
    : root && root.CourseSignalResultsModel;
  const api = factory(model);

  if (typeof module === 'object' && module.exports) {
    module.exports = api;
    return;
  }
  if (!root || !root.document) return;

  root.CourseSignalResultsView = api;
  root.addEventListener('DOMContentLoaded', async () => {
    const first = await api.initBrowser(root);
    if (first.active || typeof root.MutationObserver !== 'function') return;
    const observer = new root.MutationObserver(async () => {
      const result = await api.initBrowser(root);
      if (result.active) observer.disconnect();
    });
    observer.observe(root.document.body, {
      attributes: true,
      attributeFilter: ['data-course-signal-view'],
    });
  });
})(typeof window !== 'undefined' ? window : globalThis, function createResultsView(ResultsModel) {
  'use strict';

  const SVG_NAMESPACE = 'http://www.w3.org/2000/svg';
  const EARTH_RADIUS_M = 6371008.8;
  const METERS_PER_MILE = 1609.344;
  const FIXED_RACE_SLUG = 'the-rut';
  const FIXED_EVENT_ID = 'the-rut-28k-2026';
  const RUT_EVENTS = Object.freeze([
    Object.freeze({id: 'the-rut-50k-2026', label: '50K'}),
    Object.freeze({id: 'the-rut-28k-2026', label: '28K'}),
    Object.freeze({id: 'the-rut-21k-2026', label: '21K'}),
    Object.freeze({id: 'the-rut-11k-2026', label: '11K'}),
    Object.freeze({id: 'the-rut-vk-2026', label: 'VK'}),
  ]);
  const RUT_EVENT_IDS = new Set(RUT_EVENTS.map((event) => event.id));
  const SEARCH_DEBOUNCE_MS = 300;
  const SAFE_IDENTIFIER = /^[A-Za-z0-9][A-Za-z0-9._~-]{0,199}$/;
  const RUNNER_IDENTIFIER = /^[1-9][0-9]{0,15}$/;
  const MAX_RUNNER_IDENTIFIER = '9007199254740991';
  const STALE_RESULTS_WARNING = 'Showing cached completed results; source refresh was unavailable.';
  const LIMITS = Object.freeze({
    responseBytes: 1000000,
    trackPoints: 2000,
    sections: 100,
    limitations: 20,
    publicText: 500,
    fieldCount: 100000,
    matches: 20,
  });
  const CAPABILITIES = Object.freeze([
    {key: 'results', label: 'Results', aliases: []},
    {key: 'intermediate_splits', label: 'Intermediate splits', aliases: ['splits']},
    {key: 'course_map', label: 'Course map', aliases: ['map']},
    {key: 'elevation', label: 'Elevation', aliases: []},
    {key: 'live_gps', label: 'Live GPS', aliases: ['live_tracking', 'gps']},
  ]);
  const initializedRoots = new WeakMap();

  function finiteNumber(value) {
    return typeof value === 'number' && Number.isFinite(value) ? value : null;
  }

  function publicText(value, fallback = '—') {
    if (typeof value === 'string') {
      const text = value.trim();
      if (!text) return fallback;
      let output = '';
      let length = 0;
      for (const character of text) {
        if (length >= LIMITS.publicText) break;
        output += character;
        length += 1;
      }
      return output;
    }
    if (typeof value === 'number' && Number.isFinite(value)) return String(value);
    return fallback;
  }

  function publicName(value) {
    return typeof value === 'string' ? publicText(value, '') || null : null;
  }

  function normalizedStatus(value) {
    return publicText(value, 'INCOMPLETE').slice(0, 40).toUpperCase();
  }

  function normalizeQuery(value) {
    return String(value ?? '').normalize('NFKC').trim().replace(/\s+/g, ' ');
  }

  function identifier(value, label) {
    const text = typeof value === 'number' && Number.isInteger(value) && value >= 0
      ? String(value)
      : typeof value === 'string' ? value : '';
    if (!SAFE_IDENTIFIER.test(text)) throw new TypeError(`${label} identifier is invalid`);
    return text;
  }

  function runnerIdentifier(value) {
    const text = typeof value === 'number' && Number.isSafeInteger(value) && value > 0
      ? String(value)
      : typeof value === 'string' ? value : '';
    if (!RUNNER_IDENTIFIER.test(text)
      || (text.length === MAX_RUNNER_IDENTIFIER.length && text > MAX_RUNNER_IDENTIFIER)) {
      throw new TypeError('runner identifier is invalid');
    }
    return text;
  }

  function buildSearchEndpoint(eventId, query) {
    const event = identifier(eventId, 'event');
    const normalized = normalizeQuery(query);
    const length = Array.from(normalized).length;
    if (length < 2 || length > 100) throw new TypeError('query must contain 2 to 100 characters');
    const params = new URLSearchParams();
    params.set('event_id', event);
    params.set('q', normalized);
    params.set('limit', '20');
    return `/api/results/search?${params.toString()}`;
  }

  function buildRunnerEndpoint(eventId, runnerId) {
    const event = identifier(eventId, 'event');
    const runner = runnerIdentifier(runnerId);
    const params = new URLSearchParams();
    params.set('event_id', event);
    params.set('runner_id', runner);
    return `/api/results/runner?${params.toString()}`;
  }

  function resolveResultEndpoint(root, endpoint) {
    if (typeof endpoint !== 'string' || !endpoint.startsWith('/api/results/')) {
      throw new TypeError('result endpoint is invalid');
    }
    const page = new URL(root && root.location && root.location.href);
    if (!page.hostname.endsWith('.github.io')) return endpoint;
    const rawBase = root && root.RUT_CONFIG && root.RUT_CONFIG.apiBase;
    if (typeof rawBase !== 'string' || !rawBase.trim()) throw new TypeError('results API is unavailable');
    let api;
    try {
      api = new URL(rawBase);
    } catch (_error) {
      throw new TypeError('results API is unavailable');
    }
    if (api.protocol !== 'https:' || api.username || api.password || api.search || api.hash) {
      throw new TypeError('results API is unavailable');
    }
    const target = new URL(endpoint, 'https://course-signal.invalid');
    api.pathname = `${api.pathname.replace(/\/+$/, '')}${target.pathname}`;
    api.search = target.search;
    return api.href;
  }

  function resultTransport(root, url) {
    const page = new URL(root && root.location && root.location.href);
    if (!page.hostname.endsWith('.github.io')) return Object.freeze({url, headers: null});
    const api = new URL(root.RUT_CONFIG.apiBase);
    const target = new URL(url);
    if (target.origin !== api.origin || !target.pathname.startsWith(`${api.pathname.replace(/\/+$/, '')}/api/results/`)) {
      throw new TypeError('results API is unavailable');
    }
    const route = target.pathname.endsWith('/search') ? 'search'
      : target.pathname.endsWith('/runner') ? 'runner' : null;
    const allowed = route === 'search' ? new Set(['event_id', 'q', 'limit'])
      : route === 'runner' ? new Set(['event_id', 'runner_id']) : null;
    if (!allowed || [...target.searchParams.keys()].some((key) => !allowed.has(key))) {
      throw new TypeError('result endpoint is invalid');
    }
    const one = (name) => {
      const values = target.searchParams.getAll(name);
      if (values.length !== 1) throw new TypeError('result endpoint is invalid');
      return values[0];
    };
    const headers = {'X-Course-Signal-Event': one('event_id')};
    if (route === 'search') {
      headers['X-Course-Signal-Query'] = one('q');
      headers['X-Course-Signal-Limit'] = one('limit');
    } else {
      headers['X-Course-Signal-Runner'] = one('runner_id');
    }
    target.search = '';
    return Object.freeze({url: target.href, headers: Object.freeze(headers)});
  }

  function searchEventsForRoute(route) {
    if (route && route.race === FIXED_RACE_SLUG && RUT_EVENT_IDS.has(route.event)) {
      return RUT_EVENTS;
    }
    return Object.freeze([{id: identifier(route && route.event, 'event'), label: ''}]);
  }

  function interleaveSearchMatches(results, limit = LIMITS.matches) {
    const groups = results.map((result) => result.matches);
    const merged = [];
    for (let index = 0; merged.length < limit; index += 1) {
      let found = false;
      for (const group of groups) {
        if (index < group.length) {
          merged.push(group[index]);
          found = true;
          if (merged.length === limit) break;
        }
      }
      if (!found) break;
    }
    return merged;
  }

  function currentPageUrl(currentHref) {
    const source = String(currentHref);
    if (source.includes('\\')) throw new TypeError('current page URL path is invalid');
    let current;
    try {
      current = new URL(source);
    } catch (_error) {
      throw new TypeError('current page URL is invalid');
    }
    if (!['http:', 'https:'].includes(current.protocol) || current.username || current.password) {
      throw new TypeError('current page URL is invalid');
    }
    const page = new URL(current.href);
    page.search = '';
    page.hash = '';
    return page;
  }

  function buildResultsRouteUrl(currentHref, route, runnerId = null) {
    if (!ResultsModel || typeof ResultsModel.isPrivacySafeRoute !== 'function') {
      throw new Error('Results privacy model is unavailable');
    }
    if (!route || route.mode !== 'results') throw new TypeError('share route must be in results mode');
    const race = identifier(route.race, 'race');
    const event = identifier(route.event, 'event');
    const page = currentPageUrl(currentHref);
    const target = new URL(page.href);
    target.searchParams.set('race', race);
    target.searchParams.set('event', event);
    target.searchParams.set('mode', 'results');
    if (runnerId !== null) target.searchParams.set('runner', runnerIdentifier(runnerId));
    if (!ResultsModel.isPrivacySafeRoute(target.href, page.href)) {
      throw new Error('share route failed the privacy gate');
    }
    return target.href;
  }

  function buildShareUrl(currentHref, route, runnerId) {
    return buildResultsRouteUrl(currentHref, route, runnerId);
  }

  function adaptEnergyFactors(record) {
    if (!record || typeof record !== 'object' || Array.isArray(record)) return null;
    const estimate = finiteNumber(record.active_energy_kcal_per_kg);
    const communication = record.active_energy_communication_range_kcal_per_kg;
    if (estimate === null || estimate < 0 || !communication || typeof communication !== 'object' || Array.isArray(communication)) return null;
    const low = finiteNumber(communication.lower);
    const high = finiteNumber(communication.upper);
    if (low === null || high === null || low < 0 || low > estimate || estimate > high) return null;
    const factors = {low, estimate, high};
    return ResultsModel && ResultsModel.scaleEnergyRange(factors, 1) ? factors : null;
  }

  function formatDuration(value) {
    const seconds = finiteNumber(value);
    if (seconds === null || seconds < 0) return '—';
    const rounded = Math.round(seconds);
    const hours = Math.floor(rounded / 3600);
    const minutes = Math.floor((rounded % 3600) / 60);
    const remainder = rounded % 60;
    if (hours) return `${hours}:${String(minutes).padStart(2, '0')}:${String(remainder).padStart(2, '0')}`;
    return `${minutes}:${String(remainder).padStart(2, '0')}`;
  }

  function formatPace(value) {
    const seconds = finiteNumber(value);
    return seconds !== null && seconds > 0 ? `${formatDuration(seconds)} /mi` : '—';
  }

  function capabilityValue(capabilities, definition) {
    if (!capabilities || typeof capabilities !== 'object') return undefined;
    for (const key of [definition.key, ...definition.aliases]) {
      if (capabilities[key] === true || capabilities[key] === false || capabilities[key] === null) {
        return capabilities[key];
      }
    }
    return undefined;
  }

  function capabilityRows(capabilities) {
    return CAPABILITIES.map((definition) => {
      const value = capabilityValue(capabilities, definition);
      return {
        key: definition.key,
        label: definition.label,
        state: value === true ? 'available' : value === false ? 'unavailable' : 'unknown',
      };
    });
  }

  function runnerStopNotice(runner) {
    if (!runner || normalizedStatus(runner.status) === 'FINISHED') return null;
    const status = normalizedStatus(runner.status);
    const prefix = status === 'DNF' ? 'DNF result' : status === 'DNS' ? 'DNS result' : status === 'DQ' || status === 'DNQ' ? `${status} result` : 'In-progress result';
    const checkpoint = publicText(runner.last_recorded_split_name, 'no published checkpoint');
    return `${prefix} stops at the last verified split: ${checkpoint}. Finish was not inferred.`;
  }

  function radians(value) {
    return value * Math.PI / 180;
  }

  function greatCircleMeters(left, right) {
    const latitudeDelta = radians(right.lat - left.lat);
    const longitudeDelta = radians(right.lng - left.lng);
    const latitudeLeft = radians(left.lat);
    const latitudeRight = radians(right.lat);
    const haversine = Math.sin(latitudeDelta / 2) ** 2
      + Math.cos(latitudeLeft) * Math.cos(latitudeRight) * Math.sin(longitudeDelta / 2) ** 2;
    return EARTH_RADIUS_M * 2 * Math.asin(Math.min(1, Math.sqrt(haversine)));
  }

  function validCoordinates(track, requireElevation) {
    if (!Array.isArray(track) || track.length < 2) return null;
    const output = [];
    const count = Math.min(track.length, LIMITS.trackPoints);
    for (let sampleIndex = 0; sampleIndex < count; sampleIndex += 1) {
      const sourceIndex = track.length <= LIMITS.trackPoints
        ? sampleIndex
        : Math.round(sampleIndex * (track.length - 1) / (count - 1));
      const source = track[sourceIndex];
      if (!source || typeof source !== 'object') return null;
      const lat = finiteNumber(source.lat);
      const lng = finiteNumber(source.lng);
      const ele = finiteNumber(source.ele ?? source.elevation_m);
      if (lat === null || lng === null || Math.abs(lat) > 90 || Math.abs(lng) > 180) return null;
      if (requireElevation && (ele === null || Math.abs(ele) > 12000)) return null;
      output.push({lat, lng, ele});
    }
    return output;
  }

  function chartBounds(width, height, padding) {
    const w = finiteNumber(width);
    const h = finiteNumber(height);
    const pad = finiteNumber(padding);
    if (w === null || h === null || pad === null || w <= pad * 2 || h <= pad * 2 || pad < 0) return null;
    return {width: w, height: h, padding: pad};
  }

  function fixed(value) {
    return Number(value.toFixed(2));
  }

  function courseSvgPoints(track, width = 480, height = 260, padding = 20) {
    const points = validCoordinates(track, false);
    const bounds = chartBounds(width, height, padding);
    if (!points || !bounds) return null;
    const meanLatitude = points.reduce((total, point) => total + point.lat, 0) / points.length;
    const longitudeScale = Math.max(0.01, Math.cos(radians(meanLatitude)));
    const xs = points.map((point) => point.lng * longitudeScale);
    let minimumX = Infinity;
    let maximumX = -Infinity;
    let minimumY = Infinity;
    let maximumY = -Infinity;
    points.forEach((point, index) => {
      minimumX = Math.min(minimumX, xs[index]);
      maximumX = Math.max(maximumX, xs[index]);
      minimumY = Math.min(minimumY, point.lat);
      maximumY = Math.max(maximumY, point.lat);
    });
    const rangeX = maximumX - minimumX;
    const rangeY = maximumY - minimumY;
    if (rangeX === 0 && rangeY === 0) return null;
    const availableWidth = bounds.width - bounds.padding * 2;
    const availableHeight = bounds.height - bounds.padding * 2;
    const scale = Math.min(
      rangeX ? availableWidth / rangeX : Infinity,
      rangeY ? availableHeight / rangeY : Infinity,
    );
    if (!Number.isFinite(scale) || scale <= 0) return null;
    const drawnWidth = rangeX * scale;
    const drawnHeight = rangeY * scale;
    const offsetX = bounds.padding + (availableWidth - drawnWidth) / 2;
    const offsetY = bounds.padding + (availableHeight - drawnHeight) / 2;
    return points.map((point, index) => {
      const x = offsetX + (xs[index] - minimumX) * scale;
      const y = offsetY + drawnHeight - (point.lat - minimumY) * scale;
      return `${fixed(x)},${fixed(y)}`;
    }).join(' ');
  }

  function elevationSeries(track) {
    const points = validCoordinates(track, true);
    if (!points) return null;
    const distances = [0];
    let totalDistanceM = 0;
    for (let index = 1; index < points.length; index += 1) {
      const distance = greatCircleMeters(points[index - 1], points[index]);
      if (!Number.isFinite(distance) || distance <= 0) return null;
      totalDistanceM += distance;
      distances.push(totalDistanceM);
    }
    if (!Number.isFinite(totalDistanceM) || totalDistanceM <= 0) return null;
    return {points, distances, totalDistanceM};
  }

  function elevationSvgPoints(track, width = 480, height = 220, padding = 20) {
    const series = elevationSeries(track);
    const bounds = chartBounds(width, height, padding);
    if (!series || !bounds) return null;
    let minElevation = Infinity;
    let maxElevation = -Infinity;
    series.points.forEach((point) => {
      minElevation = Math.min(minElevation, point.ele);
      maxElevation = Math.max(maxElevation, point.ele);
    });
    const elevationRange = maxElevation - minElevation;
    const availableWidth = bounds.width - bounds.padding * 2;
    const availableHeight = bounds.height - bounds.padding * 2;
    const svgPoints = series.points.map((point, index) => {
      const x = bounds.padding + availableWidth * series.distances[index] / series.totalDistanceM;
      const y = elevationRange
        ? bounds.padding + availableHeight * (1 - (point.ele - minElevation) / elevationRange)
        : bounds.padding + availableHeight / 2;
      return `${fixed(x)},${fixed(y)}`;
    }).join(' ');
    return {
      points: svgPoints,
      totalDistanceM: series.totalDistanceM,
      minElevation,
      maxElevation,
    };
  }

  function element(document, tagName, className, text) {
    const node = document.createElement(tagName);
    if (className) node.className = className;
    if (text !== undefined) node.textContent = text;
    return node;
  }

  function svgElement(document, tagName, attributes = {}) {
    const node = document.createElementNS(SVG_NAMESPACE, tagName);
    Object.entries(attributes).forEach(([name, value]) => node.setAttribute(name, value));
    return node;
  }

  function formatPlace(value) {
    return Number.isInteger(value) && value > 0 ? `#${value}` : '—';
  }

  function matchSummary(match) {
    const status = normalizedStatus(match && match.status);
    const finish = status === 'FINISHED' ? formatDuration(match && match.finish_seconds) : '—';
    const place = status === 'FINISHED' ? formatPlace(match && match.finish_place) : '—';
    if (finish !== '—' || place !== '—') {
      return [finish !== '—' ? finish : null, place !== '—' ? `${place} overall` : null].filter(Boolean).join(' · ');
    }
    return status === 'FINISHED' ? 'Official finish unavailable' : 'No official finish';
  }

  function renderMatches(document, host, matches, onSelect) {
    host.replaceChildren();
    const rows = Array.isArray(matches) ? matches.slice(0, LIMITS.matches) : [];
    rows.forEach((match) => {
      let runnerId;
      let eventId = null;
      try {
        runnerId = runnerIdentifier(match && match.id);
        if (match && match.event_id !== undefined && match.event_id !== null) {
          eventId = identifier(match.event_id, 'event');
        }
      } catch (_error) {
        return;
      }
      const name = publicText(match.name, 'Anonymous Participant');
      const bib = match.bib === null || match.bib === undefined ? 'Bib not published' : `Bib ${publicText(match.bib)}`;
      const status = normalizedStatus(match.status).replace(/_/g, ' ');
      const eventLabel = eventId ? publicText(match.event_label, eventId) : '';
      const age = boundedInteger(match.age, 1, 120);
      const gender = publicText(match.gender, '');
      const ageGroup = publicText(match.age_group, '');
      const identityMeta = [
        eventLabel,
        bib,
        age === null ? null : `Age ${age}`,
        gender ? `Gender ${gender}` : null,
        ageGroup || null,
        status,
      ].filter(Boolean).join(' · ');
      const button = element(document, 'button', 'results-match');
      button.setAttribute('type', 'button');
      button.setAttribute('aria-label', `Select result ${runnerId}: ${name}, ${identityMeta}`);
      const identity = element(document, 'span', 'results-match-identity');
      identity.appendChild(element(document, 'strong', '', name));
      identity.appendChild(element(document, 'span', '', identityMeta));
      button.appendChild(identity);
      button.appendChild(element(document, 'span', 'results-match-finish', matchSummary(match)));
      button.addEventListener('click', () => onSelect(runnerId, eventId));
      host.appendChild(button);
    });
  }

  function renderCapabilityRows(document, host, capabilities) {
    host.replaceChildren();
    capabilityRows(capabilities).forEach((row) => {
      const label = row.state === 'available' ? 'Available' : row.state === 'unavailable' ? 'Unavailable' : 'Not reported';
      const badge = element(document, 'span', `results-capability is-${row.state}`, `${row.label}: ${label}`);
      host.appendChild(badge);
    });
  }

  function eventCapabilities(payload) {
    if (payload && payload.event && payload.event.capabilities && typeof payload.event.capabilities === 'object' && !Array.isArray(payload.event.capabilities)) return payload.event.capabilities;
    return {};
  }

  function renderEventHeader(document, nodes, payload, route) {
    const race = payload && payload.race && typeof payload.race === 'object' ? payload.race : {};
    const event = payload && payload.event && typeof payload.event === 'object' ? payload.event : {};
    nodes.raceName.textContent = publicText(race.name ?? race.slug, route.race);
    nodes.eventName.textContent = publicText(event.label ?? event.name, route.event);
    const date = publicText(event.event_date, '');
    const location = publicText(race.location ?? event.location, '');
    nodes.eventMeta.textContent = [date, location].filter(Boolean).join(' · ') || 'Published event details load with results.';
    renderCapabilityRows(document, nodes.capabilities, eventCapabilities(payload));
  }

  function setStatus(nodes, text, state) {
    nodes.status.textContent = text;
    nodes.status.dataset.state = state;
  }

  function renderRetry(document, host, text, action) {
    host.replaceChildren();
    const card = element(document, 'div', 'results-recovery');
    card.appendChild(element(document, 'p', '', text));
    const button = element(document, 'button', 'results-retry', 'Try again');
    button.setAttribute('type', 'button');
    button.addEventListener('click', action);
    card.appendChild(button);
    host.appendChild(card);
  }

  function formatDistance(value) {
    const meters = finiteNumber(value);
    if (meters === null || meters <= 0) return '—';
    const kilometers = meters / 1000;
    const miles = meters / METERS_PER_MILE;
    return `${kilometers.toFixed(kilometers < 10 ? 2 : 1)} km · ${miles.toFixed(miles < 10 ? 2 : 1)} mi`;
  }

  function formatElevation(section) {
    const gain = finiteNumber(section && section.gain_m);
    const loss = finiteNumber(section && section.loss_m);
    const grade = finiteNumber(section && section.net_grade);
    if (gain === null && loss === null && grade === null) return '—';
    const parts = [];
    if (gain !== null) parts.push(`+${Math.round(gain).toLocaleString('en-US')} m`);
    if (loss !== null) parts.push(`−${Math.round(loss).toLocaleString('en-US')} m`);
    if (grade !== null) parts.push(`${grade >= 0 ? '+' : '−'}${Math.abs(grade * 100).toFixed(1)}% net`);
    return parts.join(' · ');
  }

  function percentile(value) {
    const number = finiteNumber(value);
    if (number === null || number < 0 || number > 100) return '—';
    const rounded = Math.round(number);
    const mod100 = rounded % 100;
    const suffix = mod100 >= 11 && mod100 <= 13 ? 'th' : rounded % 10 === 1 ? 'st' : rounded % 10 === 2 ? 'nd' : rounded % 10 === 3 ? 'rd' : 'th';
    return `${rounded}${suffix} percentile`;
  }

  function sectionLabel(section) {
    return `${publicText(section && section.start_name, 'Unknown')} → ${publicText(section && section.end_name, 'Unknown')}`;
  }

  function summarySection(value) {
    return value && typeof value === 'object' ? sectionLabel(value) : 'Unavailable';
  }

  function summaryValue(document, title, body) {
    const card = element(document, 'div', 'results-summary-card');
    card.appendChild(element(document, 'dt', '', title));
    card.appendChild(element(document, 'dd', '', body));
    return card;
  }

  function boundedInteger(value, minimum, maximum) {
    return Number.isInteger(value) && value >= minimum && value <= maximum ? value : null;
  }

  function capabilityEnabled(capabilities, key) {
    return Boolean(capabilities && capabilities[key] === true);
  }

  function nonNegativeNumber(value) {
    const number = finiteNumber(value);
    return number !== null && number >= 0 ? number : null;
  }

  function sanitizeSection(source, capabilities) {
    if (!source || typeof source !== 'object' || Array.isArray(source)) return null;
    const startName = publicName(source.start_name);
    const endName = publicName(source.end_name);
    const recorded = source.status === 'recorded' && startName !== null && endName !== null;
    const terrain = capabilityEnabled(capabilities, 'terrain');
    const field = capabilityEnabled(capabilities, 'field_comparison');
    const ageGroup = capabilityEnabled(capabilities, 'age_group_comparison');
    const energy = capabilityEnabled(capabilities, 'active_energy');
    const section = {
      start_index: boundedInteger(source.start_index, 0, LIMITS.sections),
      start_name: startName || '',
      end_index: boundedInteger(source.end_index, 1, LIMITS.sections),
      end_name: endName || '',
      status: recorded ? 'recorded' : 'unknown',
      segment_seconds: recorded ? nonNegativeNumber(source.segment_seconds) : null,
      cumulative_seconds: recorded ? nonNegativeNumber(source.cumulative_seconds) : null,
      cumulative_place: recorded && field ? boundedInteger(source.cumulative_place, 1, LIMITS.fieldCount) : null,
      segment_place: recorded && field ? boundedInteger(source.segment_place, 1, LIMITS.fieldCount) : null,
      field_count: recorded && field ? boundedInteger(source.field_count, 1, LIMITS.fieldCount) : null,
      field_percentile: recorded && field && finiteNumber(source.field_percentile) !== null
        && source.field_percentile >= 0 && source.field_percentile <= 100 ? source.field_percentile : null,
      age_group_count: recorded && ageGroup ? boundedInteger(source.age_group_count, 1, LIMITS.fieldCount) : null,
      age_group_percentile: recorded && ageGroup && finiteNumber(source.age_group_percentile) !== null
        && source.age_group_percentile >= 0 && source.age_group_percentile <= 100 ? source.age_group_percentile : null,
      rank_change: recorded && field && Number.isSafeInteger(source.rank_change) ? source.rank_change : null,
      pace_seconds_per_mile: recorded && terrain ? nonNegativeNumber(source.pace_seconds_per_mile) : null,
      distance_m: recorded && terrain ? nonNegativeNumber(source.distance_m) : null,
      gain_m: recorded && terrain ? nonNegativeNumber(source.gain_m) : null,
      loss_m: recorded && terrain ? nonNegativeNumber(source.loss_m) : null,
      net_grade: recorded && terrain ? finiteNumber(source.net_grade) : null,
    };
    const factors = recorded && energy ? adaptEnergyFactors(source) : null;
    if (factors) {
      section.active_energy_kcal_per_kg = factors.estimate;
      section.active_energy_communication_range_kcal_per_kg = {lower: factors.low, upper: factors.high};
    }
    return section;
  }

  function validFinishedSections(sections) {
    const retained = [];
    let previous = null;
    for (const section of sections) {
      if (!section || section.status !== 'recorded') continue;
      if (
        section.start_index === null
        || section.end_index === null
        || section.end_index !== section.start_index + 1
        || (previous && section.start_index < previous.end_index)
        || (previous && section.start_index === previous.end_index && section.start_name !== previous.end_name)
      ) continue;
      retained.push(section);
      previous = section;
    }
    return retained;
  }

  function contiguousPartialSections(sections, runner) {
    const lastIndex = boundedInteger(runner.last_recorded_split_index, 1, LIMITS.sections);
    if (lastIndex === null) return [];
    const checkpoint = publicText(runner.last_recorded_split_name, '');
    const retained = [];
    let expectedStart = 0;
    let previousEndName = null;
    for (const section of sections) {
      if (
        section.status !== 'recorded'
        || section.start_index !== expectedStart
        || section.end_index !== expectedStart + 1
        || section.end_index > lastIndex
        || (previousEndName !== null && section.start_name !== previousEndName)
        || normalizedStatus(section.end_name) === 'FINISH'
        || (section.end_index === lastIndex && checkpoint && section.end_name !== checkpoint)
      ) break;
      retained.push(section);
      expectedStart = section.end_index;
      previousEndName = section.end_name;
      if (section.end_index === lastIndex) break;
    }
    return retained;
  }

  function sameNumber(left, right) {
    return finiteNumber(left) !== null && finiteNumber(right) !== null
      && Math.abs(left - right) <= 1e-9 * Math.max(1, Math.abs(left), Math.abs(right));
  }

  function partialEnergyIsSupported(sourceTotals, sections) {
    if (!sourceTotals || boundedInteger(sourceTotals.recorded_section_count, 1, LIMITS.sections) !== sections.length || !sections.length) {
      return false;
    }
    const total = adaptEnergyFactors(sourceTotals);
    if (!total) return false;
    const sum = {low: 0, estimate: 0, high: 0};
    for (const section of sections) {
      const factors = adaptEnergyFactors(section);
      if (!factors) return false;
      sum.low += factors.low;
      sum.estimate += factors.estimate;
      sum.high += factors.high;
    }
    return sameNumber(total.low, sum.low) && sameNumber(total.estimate, sum.estimate) && sameNumber(total.high, sum.high);
  }

  function finishedExtentIsSupported(runner, sections) {
    if (!sections.length) return false;
    const lastSection = sections[sections.length - 1];
    return runner.last_recorded_split_index === lastSection.end_index
      && runner.last_recorded_split_name === lastSection.end_name;
  }

  function clearEnergyFactors(sections) {
    sections.forEach((section) => {
      delete section.active_energy_kcal_per_kg;
      delete section.active_energy_communication_range_kcal_per_kg;
    });
  }

  function summaryReference(source, sections, metric) {
    if (!source || typeof source !== 'object' || Array.isArray(source)) return null;
    const startIndex = boundedInteger(source.start_index, 0, LIMITS.sections);
    const endIndex = boundedInteger(source.end_index, 1, LIMITS.sections);
    const startName = publicText(source.start_name, '');
    const endName = publicText(source.end_name, '');
    const row = sections.find((section) => (
      section.start_index === startIndex
      && section.end_index === endIndex
      && section.start_name === startName
      && section.end_name === endName
    ));
    if (!row || !sameNumber(source[metric], row[metric])) return null;
    return {start_index: startIndex, start_name: startName, end_index: endIndex, end_name: endName, [metric]: row[metric]};
  }

  function sanitizeSummary(source, sections, fieldEnabled) {
    if (!fieldEnabled || !source || typeof source !== 'object' || Array.isArray(source)) return {};
    const comparableSections = sections.filter((section) => (
      boundedInteger(section.field_count, 2, LIMITS.fieldCount) !== null
      && finiteNumber(section.field_percentile) !== null
      && section.field_percentile >= 0
      && section.field_percentile <= 100
    ));
    const validSectionCount = comparableSections.length;
    const summary = {
      comparison_cohort: source.comparison_cohort === 'overall_field' ? 'overall_field' : null,
      overall_field_count: boundedInteger(source.overall_field_count, 1, LIMITS.fieldCount),
      valid_section_count: validSectionCount,
      strongest_comparable_section: summaryReference(source.strongest_comparable_section, comparableSections, 'field_percentile'),
      weakest_comparable_section: summaryReference(source.weakest_comparable_section, comparableSections, 'field_percentile'),
      largest_places_gained: summaryReference(source.largest_places_gained, sections, 'rank_change'),
      largest_places_lost: summaryReference(source.largest_places_lost, sections, 'rank_change'),
    };
    const consistency = source.consistency && typeof source.consistency === 'object' ? source.consistency : {};
    const consistencyCount = boundedInteger(consistency.comparable_section_count, 0, sections.length);
    summary.consistency = consistency.status === 'available'
      && validSectionCount >= 3
      && consistencyCount === validSectionCount
      && nonNegativeNumber(consistency.percentile_range) !== null
      && finiteNumber(consistency.mean_percentile) !== null
      ? {
        status: 'available',
        comparable_section_count: validSectionCount,
        percentile_range: consistency.percentile_range,
        mean_percentile: consistency.mean_percentile,
      }
      : {status: 'unavailable', comparable_section_count: validSectionCount};
    const trend = source.trend && typeof source.trend === 'object' ? source.trend : {};
    const trendCount = boundedInteger(trend.comparable_section_count, 0, sections.length);
    const directions = new Set([
      'higher_relative_percentiles_later',
      'lower_relative_percentiles_later',
      'no_relative_change',
    ]);
    summary.trend = trend.status === 'available'
      && validSectionCount >= 3
      && trendCount === validSectionCount
      && directions.has(trend.direction)
      && finiteNumber(trend.percentile_points_per_section) !== null
      ? {
        status: 'available',
        comparable_section_count: validSectionCount,
        percentile_points_per_section: trend.percentile_points_per_section,
        direction: trend.direction,
      }
      : {status: 'unavailable', comparable_section_count: validSectionCount};
    return summary;
  }

  function sanitizeAnalysis(source, authoritativeCapabilities) {
    if (!source || typeof source !== 'object' || Array.isArray(source)
      || !capabilityEnabled(authoritativeCapabilities, 'results')) return null;
    const sourceRunner = source.runner && typeof source.runner === 'object' && !Array.isArray(source.runner) ? source.runner : null;
    if (!sourceRunner) return null;
    const reported = source.capabilities && typeof source.capabilities === 'object' && !Array.isArray(source.capabilities)
      ? source.capabilities : {};
    const splits = capabilityEnabled(authoritativeCapabilities, 'intermediate_splits')
      && reported.splits === true;
    const terrain = splits
      && capabilityEnabled(authoritativeCapabilities, 'course_map')
      && capabilityEnabled(authoritativeCapabilities, 'elevation');
    const capabilities = {
      splits,
      field_comparison: splits && reported.field_comparison === true,
      age_group_comparison: splits && reported.age_group_comparison === true,
      terrain: terrain && reported.terrain === true,
      active_energy: terrain && reported.active_energy === true,
    };
    const runner = {
      id: sourceRunner.id,
      name: publicText(sourceRunner.name, 'Anonymous Participant'),
      bib: sourceRunner.bib === null || sourceRunner.bib === undefined ? null : publicText(sourceRunner.bib, ''),
      age: boundedInteger(sourceRunner.age, 1, 120),
      gender: sourceRunner.gender === null || sourceRunner.gender === undefined ? null : publicText(sourceRunner.gender, ''),
      age_group: sourceRunner.age_group === null || sourceRunner.age_group === undefined ? null : publicText(sourceRunner.age_group, ''),
      age_group_place: boundedInteger(sourceRunner.age_group_place, 1, LIMITS.fieldCount),
      gender_place: boundedInteger(sourceRunner.gender_place, 1, LIMITS.fieldCount),
      status: normalizedStatus(sourceRunner.status),
      finish_seconds: nonNegativeNumber(sourceRunner.finish_seconds),
      finish_place: boundedInteger(sourceRunner.finish_place, 1, LIMITS.fieldCount),
      last_recorded_split_index: boundedInteger(sourceRunner.last_recorded_split_index, 0, LIMITS.sections),
      last_recorded_split_name: publicText(sourceRunner.last_recorded_split_name, ''),
    };
    const finished = runner.status === 'FINISHED';
    if (!finished) {
      runner.finish_seconds = null;
      runner.finish_place = null;
    }
    const sourceSections = capabilities.splits && Array.isArray(source.sections)
      ? source.sections.slice(0, LIMITS.sections) : [];
    const sanitizedSections = sourceSections.map((row) => sanitizeSection(row, capabilities));
    let sections;
    let finishedEvidenceComplete = true;
    if (finished) {
      sections = validFinishedSections(sanitizedSections);
      finishedEvidenceComplete = sections.length === sanitizedSections.length
        && sections.every((section, index) => section.start_index === index)
        && finishedExtentIsSupported(runner, sections);
    } else {
      sections = contiguousPartialSections(sanitizedSections.filter(Boolean), runner);
    }
    if (!finished && normalizedStatus(runner.last_recorded_split_name) === 'FINISH') {
      runner.last_recorded_split_name = sections.length ? sections[sections.length - 1].end_name : '';
    }

    const sourceTotals = source.totals && typeof source.totals === 'object' && !Array.isArray(source.totals) ? source.totals : {};
    const totals = {recorded_section_count: sections.filter((row) => row.status === 'recorded').length};
    const totalFactors = capabilities.active_energy ? adaptEnergyFactors(sourceTotals) : null;
    const energySupported = Boolean(
      totalFactors
      && (!finished || finishedEvidenceComplete)
      && partialEnergyIsSupported(sourceTotals, sections)
    );
    if (energySupported) {
      totals.active_energy_kcal_per_kg = totalFactors.estimate;
      totals.active_energy_communication_range_kcal_per_kg = {lower: totalFactors.low, upper: totalFactors.high};
    } else {
      clearEnergyFactors(sections);
      capabilities.active_energy = false;
    }
    return {
      runner,
      sections,
      totals,
      summary: sanitizeSummary(source.summary, sections, capabilities.field_comparison),
      capabilities,
      limitations: Array.isArray(source.limitations)
        ? source.limitations.slice(0, LIMITS.limitations).map((message) => publicText(message, '')).filter(Boolean)
        : [],
    };
  }

  function overallComparisonEvidence(analysis, includeSectionCount) {
    if (!capabilityEnabled(analysis && analysis.capabilities, 'field_comparison')) {
      return 'Unavailable — field comparison was not reported.';
    }
    const summary = analysis && analysis.summary && typeof analysis.summary === 'object' ? analysis.summary : {};
    if (summary.comparison_cohort !== 'overall_field') {
      return 'Unavailable — comparison cohort was not reported.';
    }
    const fieldCount = boundedInteger(summary.overall_field_count, 1, LIMITS.fieldCount);
    const parts = [
      'Overall field',
      fieldCount === null ? 'field count unavailable' : `${fieldCount.toLocaleString('en-US')} published result${fieldCount === 1 ? '' : 's'}`,
    ];
    if (includeSectionCount) {
      const sectionCount = boundedInteger(summary.valid_section_count, 0, LIMITS.sections);
      if (sectionCount !== null) parts.push(`${sectionCount} valid section${sectionCount === 1 ? '' : 's'} analyzed`);
    }
    return parts.join(' · ');
  }

  function ageGenderComparisonEvidence(analysis) {
    const runner = analysis && analysis.runner && typeof analysis.runner === 'object' ? analysis.runner : {};
    const label = publicText(runner.age_group, 'Age/gender group');
    if (!capabilityEnabled(analysis && analysis.capabilities, 'age_group_comparison')) {
      return `${label} · comparison unavailable`;
    }
    const counts = Array.isArray(analysis.sections)
      ? analysis.sections.map((row) => boundedInteger(row && row.age_group_count, 1, LIMITS.fieldCount)).filter((value) => value !== null)
      : [];
    const count = counts.length ? Math.max(...counts) : null;
    return `${label} · ${count === null ? 'group count unavailable' : `${count.toLocaleString('en-US')} comparable runner${count === 1 ? '' : 's'}`}`;
  }

  function renderSummary(document, analysis) {
    const section = element(document, 'section', 'results-summary');
    section.setAttribute('aria-labelledby', 'resultsSummaryTitle');
    section.appendChild(element(document, 'p', 'results-section-kicker', 'DESCRIPTIVE SUMMARY'));
    const title = element(document, 'h3', '', 'How the recorded sections compare');
    title.id = 'resultsSummaryTitle';
    section.appendChild(title);
    const summary = analysis && analysis.summary && typeof analysis.summary === 'object' ? analysis.summary : {};
    const list = element(document, 'dl', 'results-summary-grid');
    list.appendChild(summaryValue(document, 'Overall comparison', overallComparisonEvidence(analysis, true)));
    list.appendChild(summaryValue(document, 'Age + gender comparison', ageGenderComparisonEvidence(analysis)));
    const strongest = summary.strongest_comparable_section;
    const weakest = summary.weakest_comparable_section;
    list.appendChild(summaryValue(document, 'Strongest section', strongest
      ? `${summarySection(strongest)} · ${percentile(strongest.field_percentile)}`
      : 'Unavailable — no comparable section was reported.'));
    list.appendChild(summaryValue(document, 'Weakest section', weakest
      ? `${summarySection(weakest)} · ${percentile(weakest.field_percentile)}`
      : 'Unavailable — no comparable section was reported.'));

    const consistency = summary.consistency && typeof summary.consistency === 'object' ? summary.consistency : {};
    const consistencyCount = boundedInteger(consistency.comparable_section_count, 0, LIMITS.sections) ?? 0;
    const consistencyLabel = `${consistencyCount} comparable section${consistencyCount === 1 ? '' : 's'}`;
    const consistencyBody = consistency.status === 'available'
      ? `${consistencyLabel} · ${finiteNumber(consistency.percentile_range) === null ? 'range unavailable' : `${Math.round(consistency.percentile_range)} percentile-point range`} · ${finiteNumber(consistency.mean_percentile) === null ? 'mean unavailable' : `${Math.round(consistency.mean_percentile)}th-percentile mean`}`
      : `Unavailable — ${consistencyLabel}.`;
    list.appendChild(summaryValue(document, 'Consistency', consistencyBody));

    const trend = summary.trend && typeof summary.trend === 'object' ? summary.trend : {};
    const direction = trend.direction === 'higher_relative_percentiles_later'
      ? 'Higher relative percentiles in later sections'
      : trend.direction === 'lower_relative_percentiles_later'
        ? 'Lower relative percentiles in later sections'
        : trend.direction === 'no_relative_change' ? 'No relative percentile change' : null;
    const slope = finiteNumber(trend.percentile_points_per_section);
    const trendCount = boundedInteger(trend.comparable_section_count, 0, LIMITS.sections) ?? 0;
    const trendLabel = `${trendCount} comparable section${trendCount === 1 ? '' : 's'}`;
    const trendBody = trend.status === 'available' && direction
      ? `${direction}${slope === null ? '' : ` · ${slope >= 0 ? '+' : '−'}${Math.abs(slope).toFixed(1)} points per section`}`
      : `Unavailable — ${trendLabel}.`;
    list.appendChild(summaryValue(document, 'Trend', trendBody));

    const gained = summary.largest_places_gained;
    const lost = summary.largest_places_lost;
    const movementParts = [];
    if (gained && Number.isInteger(gained.rank_change) && gained.rank_change > 0) movementParts.push(`Gained ${gained.rank_change} at ${summarySection(gained)}`);
    if (lost && Number.isInteger(lost.rank_change) && lost.rank_change < 0) movementParts.push(`Lost ${Math.abs(lost.rank_change)} at ${summarySection(lost)}`);
    list.appendChild(summaryValue(document, 'Rank movement', movementParts.join(' · ') || 'No supported cumulative-place movement was reported.'));
    section.appendChild(list);
    return section;
  }

  function makeSvg(document, viewBox, label) {
    const svg = svgElement(document, 'svg', {
      viewBox,
      role: 'img',
      'aria-label': label,
      preserveAspectRatio: 'xMidYMid meet',
    });
    return svg;
  }

  function parsedSvgPoints(points) {
    if (typeof points !== 'string') return [];
    return points.trim().split(/\s+/).map((pair) => pair.split(',').map(Number)).filter(([x, y]) => Number.isFinite(x) && Number.isFinite(y));
  }

  function pointOnRoute(points, progress) {
    const pairs = parsedSvgPoints(points);
    if (pairs.length < 2 || !Number.isFinite(progress) || progress < 0 || progress > 1) return null;
    const distances = [0];
    for (let index = 1; index < pairs.length; index += 1) {
      distances.push(distances[index - 1] + Math.hypot(pairs[index][0] - pairs[index - 1][0], pairs[index][1] - pairs[index - 1][1]));
    }
    const total = distances[distances.length - 1];
    if (!(total > 0)) return null;
    const target = total * progress;
    let index = 1;
    while (index < distances.length - 1 && distances[index] < target) index += 1;
    const span = distances[index] - distances[index - 1];
    const ratio = span > 0 ? (target - distances[index - 1]) / span : 0;
    return {
      x: pairs[index - 1][0] + (pairs[index][0] - pairs[index - 1][0]) * ratio,
      y: pairs[index - 1][1] + (pairs[index][1] - pairs[index - 1][1]) * ratio,
    };
  }

  function pointOnElevation(points, progress, width, padding) {
    const pairs = parsedSvgPoints(points);
    if (pairs.length < 2 || !Number.isFinite(progress) || progress < 0 || progress > 1) return null;
    const targetX = padding + progress * (width - 2 * padding);
    let index = 1;
    while (index < pairs.length - 1 && pairs[index][0] < targetX) index += 1;
    const span = pairs[index][0] - pairs[index - 1][0];
    const ratio = span > 0 ? (targetX - pairs[index - 1][0]) / span : 0;
    return {x: targetX, y: pairs[index - 1][1] + (pairs[index][1] - pairs[index - 1][1]) * ratio};
  }

  function recordedCheckpoints(analysis, rawProgresses) {
    if (!Array.isArray(rawProgresses) || rawProgresses.length < 2 || rawProgresses.length > LIMITS.sections + 1) return [];
    const progresses = rawProgresses.map((value) => finiteNumber(value));
    if (progresses.some((value, index) => value === null || value < 0 || value > 1 || (index && value <= progresses[index - 1]))) return [];
    const rows = Array.isArray(analysis && analysis.sections) ? analysis.sections : [];
    const recorded = rows.filter((row) => row && row.status === 'recorded');
    if (!recorded.length || recorded[0].start_index !== 0) return [];
    const checkpoints = [{number: 1, name: publicText(recorded[0].start_name, 'Start'), seconds: 0, progress: progresses[0]}];
    recorded.forEach((row) => {
      const endIndex = boundedInteger(row.end_index, 1, progresses.length - 1);
      const seconds = nonNegativeNumber(row.cumulative_seconds);
      if (endIndex === null || seconds === null) return;
      checkpoints.push({
        number: checkpoints.length + 1,
        name: publicText(row.end_name, `Checkpoint ${endIndex}`),
        seconds,
        progress: progresses[endIndex],
      });
    });
    return checkpoints;
  }

  function appendCheckpointMarkers(document, svg, checkpoints, locate) {
    checkpoints.forEach((checkpoint) => {
      const point = locate(checkpoint.progress);
      if (!point) return;
      const marker = svgElement(document, 'g', {class: 'results-checkpoint-marker'});
      const title = svgElement(document, 'title');
      title.textContent = `${checkpoint.number}. ${checkpoint.name} — ${formatDuration(checkpoint.seconds)} elapsed`;
      marker.appendChild(title);
      marker.appendChild(svgElement(document, 'circle', {cx: point.x.toFixed(2), cy: point.y.toFixed(2), r: '9'}));
      const label = svgElement(document, 'text', {x: point.x.toFixed(2), y: point.y.toFixed(2)});
      label.textContent = String(checkpoint.number);
      marker.appendChild(label);
      svg.appendChild(marker);
    });
  }

  function renderCheckpointKey(document, checkpoints) {
    const key = element(document, 'ol', 'results-checkpoint-key');
    key.setAttribute('aria-label', 'Shared checkpoint key for course shape and elevation profile');
    checkpoints.forEach((checkpoint) => {
      const item = element(document, 'li');
      item.appendChild(element(document, 'span', 'results-checkpoint-number', String(checkpoint.number)));
      const copy = element(document, 'span', 'results-checkpoint-copy');
      copy.appendChild(element(document, 'strong', '', checkpoint.name));
      copy.appendChild(element(document, 'span', '', `${formatDuration(checkpoint.seconds)} elapsed`));
      item.appendChild(copy);
      key.appendChild(item);
    });
    return key;
  }

  function renderCourseVisualizations(document, event, capabilities, analysis) {
    const section = element(document, 'section', 'results-visuals');
    section.setAttribute('aria-labelledby', 'resultsVisualsTitle');
    const title = element(document, 'h3', '', 'Course evidence');
    title.id = 'resultsVisualsTitle';
    section.appendChild(title);
    const grid = element(document, 'div', 'results-visual-grid');
    const course = event && event.course && typeof event.course === 'object' ? event.course : {};
    const track = Array.isArray(course.track_points) ? course.track_points : Array.isArray(course.trackPoints) ? course.trackPoints : null;
    const rawProgresses = Array.isArray(course.progress_points) ? course.progress_points : course.progressPoints;
    const checkpoints = recordedCheckpoints(analysis, rawProgresses);
    const coursePoints = capabilityEnabled(capabilities, 'course_map') ? courseSvgPoints(track, 480, 260, 24) : null;

    const courseFigure = element(document, 'figure', 'results-figure');
    courseFigure.appendChild(element(document, 'h4', '', 'Course shape'));
    if (coursePoints) {
      const svg = makeSvg(document, '0 0 480 260', 'Published course shape with numbered recorded checkpoints');
      const polyline = svgElement(document, 'polyline', {points: coursePoints, class: 'results-course-line'});
      svg.appendChild(polyline);
      const pointPairs = coursePoints.split(' ');
      const [startX, startY] = pointPairs[0].split(',');
      const [finishX, finishY] = pointPairs[pointPairs.length - 1].split(',');
      svg.appendChild(svgElement(document, 'circle', {cx: startX, cy: startY, r: '5', class: 'results-course-start'}));
      svg.appendChild(svgElement(document, 'circle', {cx: finishX, cy: finishY, r: '6', class: 'results-course-finish'}));
      appendCheckpointMarkers(document, svg, checkpoints, (progress) => pointOnRoute(coursePoints, progress));
      courseFigure.appendChild(svg);
      courseFigure.appendChild(element(document, 'figcaption', '', 'Numbers match the shared checkpoint key below.'));
    } else {
      courseFigure.appendChild(element(document, 'p', 'results-unavailable', 'Course map unavailable. No route shape was inferred.'));
    }
    grid.appendChild(courseFigure);

    const elevationFigure = element(document, 'figure', 'results-figure');
    elevationFigure.appendChild(element(document, 'h4', '', 'Elevation profile'));
    const elevation = capabilityEnabled(capabilities, 'elevation') ? elevationSvgPoints(track, 480, 220, 24) : null;
    if (elevation) {
      const svg = makeSvg(document, '0 0 480 220', 'Published course elevation with numbered recorded checkpoints');
      svg.appendChild(svgElement(document, 'polyline', {points: elevation.points, class: 'results-elevation-line'}));
      appendCheckpointMarkers(document, svg, checkpoints, (progress) => pointOnElevation(elevation.points, progress, 480, 24));
      elevationFigure.appendChild(svg);
      elevationFigure.appendChild(element(
        document,
        'figcaption',
        '',
        `${formatDistance(elevation.totalDistanceM)} · ${Math.round(elevation.minElevation).toLocaleString('en-US')}–${Math.round(elevation.maxElevation).toLocaleString('en-US')} m`,
      ));
    } else {
      elevationFigure.appendChild(element(document, 'p', 'results-unavailable', 'Elevation unavailable. No cumulative-distance profile was inferred.'));
    }
    grid.appendChild(elevationFigure);
    if (checkpoints.length) grid.appendChild(renderCheckpointKey(document, checkpoints));
    section.appendChild(grid);
    return section;
  }

  function tableCell(document, label, text, className = '') {
    const cell = element(document, 'td', className, text);
    cell.setAttribute('data-label', label);
    return cell;
  }

  function formatRankChange(value) {
    if (!Number.isInteger(value)) return '—';
    if (value > 0) return `+${value} gained`;
    if (value < 0) return `−${Math.abs(value)} lost`;
    return 'No change';
  }

  function renderSections(document, analysis) {
    const section = element(document, 'section', 'results-sections');
    section.setAttribute('aria-labelledby', 'resultsSectionsTitle');
    const title = element(document, 'h3', '', 'Section evidence');
    title.id = 'resultsSectionsTitle';
    section.appendChild(title);
    const tableWrap = element(document, 'div', 'results-table-wrap');
    const table = element(document, 'table', 'results-section-table');
    table.appendChild(element(document, 'caption', '', 'Official elapsed crossings and section evidence for this runner'));
    const capabilities = analysis && analysis.capabilities || {};
    const terrain = capabilityEnabled(capabilities, 'terrain');
    const field = capabilityEnabled(capabilities, 'field_comparison');
    const ageGroup = capabilityEnabled(capabilities, 'age_group_comparison');
    const energy = capabilityEnabled(capabilities, 'active_energy');
    const headers = ['Section', 'Status', 'Cumulative', 'Section time'];
    if (terrain) headers.push('Pace', 'Distance', 'Grade / elevation');
    if (field) headers.push('Overall field', 'Rank delta');
    if (ageGroup) headers.push('Age + gender group');
    if (energy) headers.push('Active calories');
    const head = element(document, 'thead');
    const headingRow = element(document, 'tr');
    headers.forEach((header) => {
      const cell = element(document, 'th', '', header);
      cell.setAttribute('scope', 'col');
      headingRow.appendChild(cell);
    });
    head.appendChild(headingRow);
    table.appendChild(head);
    const body = element(document, 'tbody');
    const energyCells = [];
    const rows = Array.isArray(analysis && analysis.sections) ? analysis.sections.slice(0, LIMITS.sections) : [];
    rows.forEach((row) => {
      const tr = element(document, 'tr', row && row.status === 'recorded' ? 'is-recorded' : 'is-unverified');
      tr.appendChild(tableCell(document, 'Section', sectionLabel(row), 'results-section-name'));
      tr.appendChild(tableCell(document, 'Status', row && row.status === 'recorded' ? 'Verified split pair' : 'Not recorded'));
      const cumulative = formatDuration(row && row.cumulative_seconds);
      const cumulativePlace = field ? formatPlace(row && row.cumulative_place) : '—';
      tr.appendChild(tableCell(document, 'Cumulative', [cumulative, cumulativePlace !== '—' ? `${cumulativePlace} overall` : null].filter(Boolean).join(' · ') || '—'));
      const segment = formatDuration(row && row.segment_seconds);
      const segmentPlace = field ? formatPlace(row && row.segment_place) : '—';
      tr.appendChild(tableCell(document, 'Section time', [segment, segmentPlace !== '—' ? `${segmentPlace} section` : null].filter(Boolean).join(' · ') || '—'));
      if (terrain) {
        tr.appendChild(tableCell(document, 'Pace', formatPace(row && row.pace_seconds_per_mile)));
        tr.appendChild(tableCell(document, 'Distance', formatDistance(row && row.distance_m)));
        tr.appendChild(tableCell(document, 'Grade / elevation', formatElevation(row)));
      }
      if (field) {
        const fieldPercentile = percentile(row && row.field_percentile);
        const count = boundedInteger(row && row.field_count, 1, LIMITS.fieldCount);
        const fieldCount = count === null ? null : `${count.toLocaleString('en-US')} comparable`;
        tr.appendChild(tableCell(document, 'Overall field', [fieldPercentile !== '—' ? fieldPercentile : null, fieldCount].filter(Boolean).join(' · ') || '—'));
        tr.appendChild(tableCell(document, 'Rank delta', formatRankChange(row && row.rank_change)));
      }
      if (ageGroup) {
        const groupPercentile = percentile(row && row.age_group_percentile);
        const count = boundedInteger(row && row.age_group_count, 1, LIMITS.fieldCount);
        const groupCount = count === null ? null : `${count.toLocaleString('en-US')} comparable`;
        tr.appendChild(tableCell(document, 'Age + gender group', [groupPercentile !== '—' ? groupPercentile : null, groupCount].filter(Boolean).join(' · ') || '—'));
      }
      if (energy) {
        const factors = adaptEnergyFactors(row);
        const energyCell = tableCell(document, 'Active calories', factors ? 'Enter total weight' : 'Unavailable', 'results-section-energy');
        energyCells.push({node: energyCell, factors});
        tr.appendChild(energyCell);
      }
      body.appendChild(tr);
    });
    if (!rows.length) {
      const row = element(document, 'tr');
      const cell = tableCell(document, 'Sections', 'No intermediate section rows were published.', 'results-empty-table');
      cell.setAttribute('colspan', String(headers.length));
      row.appendChild(cell);
      body.appendChild(row);
    }
    table.appendChild(body);
    tableWrap.appendChild(table);
    section.appendChild(tableWrap);
    return {section, energyCells};
  }

  function renderEnergyCard(document, analysis, energyCells, runnerName) {
    const card = element(document, 'section', 'results-energy results-energy-inline');
    card.setAttribute('aria-labelledby', 'resultsEnergyTitle');
    const name = publicText(runnerName, 'this runner');
    const title = element(document, 'h3', '', `Active-calorie estimate for ${name}`);
    title.id = 'resultsEnergyTitle';
    card.appendChild(title);
    card.appendChild(element(document, 'p', 'results-energy-intro', 'Optional estimate: recorded route distance and elevation × total moved weight. Age and gender affect race comparisons above, not this calorie model.'));
    const totalFactors = adaptEnergyFactors(analysis && analysis.totals);
    const supported = analysis && analysis.capabilities && analysis.capabilities.active_energy === true && totalFactors;
    if (!supported || !ResultsModel) {
      card.appendChild(element(document, 'p', 'results-unavailable', 'Active-energy estimation is unavailable because the required recorded sections and elevated course geometry were not published.'));
      const method = element(document, 'a', 'results-method-link', 'Read the energy model');
      method.setAttribute('href', 'docs/energy-model.md');
      card.appendChild(method);
      return card;
    }

    const controls = element(document, 'div', 'results-energy-controls');
    const field = element(document, 'label', 'results-weight-field');
    field.setAttribute('for', 'resultsTotalWeight');
    field.appendChild(element(document, 'span', '', 'Total weight (including gear)'));
    const input = element(document, 'input');
    input.id = 'resultsTotalWeight';
    input.setAttribute('type', 'number');
    input.setAttribute('inputmode', 'decimal');
    input.setAttribute('min', '1');
    input.setAttribute('max', '1102');
    input.setAttribute('step', '0.1');
    input.setAttribute('autocomplete', 'off');
    input.setAttribute('aria-describedby', 'resultsWeightPrivacy resultsEnergyOutput');
    field.appendChild(input);
    controls.appendChild(field);

    const unitField = element(document, 'label', 'results-unit-field');
    unitField.setAttribute('for', 'resultsWeightUnit');
    unitField.appendChild(element(document, 'span', '', 'Unit'));
    const select = element(document, 'select');
    select.id = 'resultsWeightUnit';
    const pounds = element(document, 'option', '', 'lb');
    pounds.setAttribute('value', 'lb');
    const kilograms = element(document, 'option', '', 'kg');
    kilograms.setAttribute('value', 'kg');
    select.append(pounds, kilograms);
    select.value = 'lb';
    unitField.appendChild(select);
    controls.appendChild(unitField);
    card.appendChild(controls);

    const privacy = element(document, 'p', 'results-privacy-note', 'Used only in this page. It is not sent or saved.');
    privacy.id = 'resultsWeightPrivacy';
    card.appendChild(privacy);
    const output = element(document, 'p', 'results-energy-output', 'Enter total weight to see an estimated active-calorie range.');
    output.id = 'resultsEnergyOutput';
    output.setAttribute('role', 'status');
    output.setAttribute('aria-live', 'polite');
    card.appendChild(output);

    const disclosure = element(document, 'p', 'results-energy-disclosure');
    disclosure.appendChild(element(document, 'span', '', 'This active race energy value is an engineering estimate for running-only recorded sections, not a wearable reading and not a medical measurement or medical truth. Technical terrain, weather, walking, stops, and individual economy are not observed. The range is a product uncertainty band, not a medical confidence interval. '));
    const link = element(document, 'a', 'results-method-link', 'Method: Minetti graded-running transport model.');
    link.setAttribute('href', 'docs/energy-model.md');
    disclosure.appendChild(link);
    card.appendChild(disclosure);

    const update = () => {
      const raw = String(input.value ?? '').trim();
      if (!raw) {
        output.textContent = 'Enter total weight to see an estimated active-calorie range.';
        energyCells.forEach((entry) => { entry.node.textContent = entry.factors ? 'Enter total weight' : 'Unavailable'; });
        return;
      }
      const kilogramsValue = ResultsModel.weightKg(raw, select.value);
      const total = ResultsModel.scaleEnergyRange(totalFactors, kilogramsValue);
      if (kilogramsValue === null || !total) {
        output.textContent = 'Enter a valid total weight.';
        energyCells.forEach((entry) => { entry.node.textContent = entry.factors ? 'Weight unavailable' : 'Unavailable'; });
        return;
      }
      output.textContent = `Estimated active-calorie range: ${ResultsModel.formatRange(total)}.`;
      energyCells.forEach((entry) => {
        const scaled = ResultsModel.scaleEnergyRange(entry.factors, kilogramsValue);
        entry.node.textContent = scaled ? `Estimated range · ${ResultsModel.formatRange(scaled)}` : 'Unavailable';
      });
    };
    input.addEventListener('input', update);
    select.addEventListener('change', () => {
      input.setAttribute('max', select.value === 'kg' ? '500' : '1102');
      update();
    });
    return card;
  }

  function renderAnalysisCapabilities(document, analysis) {
    const section = element(document, 'section', 'results-analysis-capabilities');
    section.setAttribute('aria-labelledby', 'resultsAnalysisCapabilitiesTitle');
    const title = element(document, 'h3', '', 'Analysis coverage');
    title.id = 'resultsAnalysisCapabilitiesTitle';
    section.appendChild(title);
    const grid = element(document, 'div', 'results-analysis-capability-grid');
    const definitions = [
      ['splits', 'Recorded checkpoints', 'Official elapsed crossing times and adjacent section splits.', 'No verified adjacent checkpoint pairs were published.'],
      ['field_comparison', 'Overall field', 'Each recorded section is ranked against all comparable runners.', 'Overall section percentiles were not available.'],
      ['age_group_comparison', 'Age + gender group', 'The same sections are compared with the runner’s published age/gender category.', 'A comparable published age/gender category was not available.'],
      ['terrain', 'Course terrain', 'Distance, elevation change, grade, and pace are derived from the published route.', 'The published route could not support terrain detail.'],
      ['active_energy', 'Optional calorie estimate', 'Enter total weight above; age and gender are not used in this estimate.', 'The route did not support the optional weight-based estimate.'],
    ];
    const capabilities = analysis && analysis.capabilities && typeof analysis.capabilities === 'object' ? analysis.capabilities : {};
    definitions.forEach(([key, label, availableText, unavailableText]) => {
      const available = capabilities[key] === true;
      const item = element(document, 'div', available ? 'is-available' : 'is-unavailable');
      item.appendChild(element(document, 'strong', '', label));
      item.appendChild(element(document, 'span', '', available ? availableText : unavailableText));
      grid.appendChild(item);
    });
    section.appendChild(grid);
    const missing = [];
    if (capabilities.splits !== true) missing.push('Intermediate split timing was not available; only supported overall facts are shown.');
    if (capabilities.field_comparison !== true) missing.push('Overall field percentiles and rank comparisons were not available.');
    if (capabilities.age_group_comparison !== true) missing.push('Age-and-gender section percentiles were not available.');
    if (capabilities.terrain !== true) missing.push('Distance, grade, elevation, and pace detail were not derived without valid event-matched course geometry.');
    if (capabilities.active_energy !== true) missing.push('Active-calorie estimates were not derived without complete terrain evidence.');
    if (missing.length) {
      const list = element(document, 'ul', 'results-missing-list');
      missing.forEach((message) => list.appendChild(element(document, 'li', '', message)));
      section.appendChild(list);
    }
    const limitations = Array.isArray(analysis && analysis.limitations)
      ? analysis.limitations.slice(0, LIMITS.limitations) : [];
    if (limitations.length) {
      const details = element(document, 'details', 'results-limitations');
      details.appendChild(element(document, 'summary', '', 'Method and data notes'));
      const list = element(document, 'ul');
      limitations.forEach((message) => {
        if (typeof message === 'string' && message.trim()) list.appendChild(element(document, 'li', '', message.trim()));
      });
      details.appendChild(list);
      section.appendChild(details);
    }
    return section;
  }

  function renderShare(document, root, route, runner) {
    const share = element(document, 'div', 'results-share');
    const button = element(document, 'button', 'results-share-button', 'Share this result');
    button.setAttribute('type', 'button');
    const status = element(document, 'span', 'results-share-status');
    status.setAttribute('role', 'status');
    status.setAttribute('aria-live', 'polite');
    const fallback = element(document, 'div', 'results-share-fallback');
    fallback.hidden = true;
    const label = element(document, 'label', '', 'Privacy-safe result URL');
    label.setAttribute('for', 'resultsShareUrl');
    const input = element(document, 'input');
    input.id = 'resultsShareUrl';
    input.setAttribute('type', 'url');
    input.setAttribute('readonly', '');
    fallback.append(label, input);

    const expose = (url) => {
      input.value = url;
      fallback.hidden = false;
      input.focus();
      input.select();
      status.textContent = 'Copy the selected privacy-safe URL.';
    };
    button.addEventListener('click', async () => {
      let url;
      try {
        url = buildShareUrl(root.location.href, route, runner.id);
      } catch (_error) {
        status.textContent = 'A privacy-safe share URL could not be built.';
        return;
      }
      const clipboard = root.navigator && root.navigator.clipboard;
      if (!clipboard || typeof clipboard.writeText !== 'function') {
        expose(url);
        return;
      }
      try {
        await clipboard.writeText(url);
        fallback.hidden = true;
        status.textContent = 'Privacy-safe result URL copied.';
      } catch (_error) {
        expose(url);
      }
    });
    share.append(button, status, fallback);
    return share;
  }

  function renderRunnerDetail(document, host, root, route, payload, runnerLead = null) {
    const analysis = sanitizeAnalysis(payload && payload.analysis, eventCapabilities(payload));
    const runner = analysis && analysis.runner && typeof analysis.runner === 'object' ? analysis.runner : null;
    if (!analysis || !runner) throw new TypeError('runner analysis is unavailable');
    runnerIdentifier(runner.id);
    host.replaceChildren();
    host.hidden = false;
    const lead = runnerLead || host;
    if (runnerLead) {
      runnerLead.replaceChildren();
      runnerLead.hidden = false;
    }

    let renderedSections = null;
    if (capabilityEnabled(analysis.capabilities, 'splits')) renderedSections = renderSections(document, analysis);

    const header = element(document, 'section', 'results-runner-header');
    const identity = element(document, 'div', 'results-runner-identity');
    const bib = runner.bib === null || runner.bib === undefined ? 'Bib not published' : `Bib ${publicText(runner.bib)}`;
    identity.appendChild(element(document, 'p', 'results-section-kicker', `${bib} · ${normalizedStatus(runner.status).replace(/_/g, ' ')}`));
    identity.appendChild(element(document, 'h2', '', publicText(runner.name, 'Anonymous Participant')));
    header.appendChild(identity);
    header.appendChild(renderShare(document, root, route, runner));

    const facts = element(document, 'dl', 'results-headline-facts');
    facts.appendChild(summaryValue(document, 'Official finish', runner.status === 'FINISHED' ? formatDuration(runner.finish_seconds) : '—'));
    facts.appendChild(summaryValue(document, 'Overall place', runner.status === 'FINISHED' ? formatPlace(runner.finish_place) : '—'));
    const age = boundedInteger(runner.age, 1, 120);
    const gender = publicText(runner.gender, 'Not published');
    facts.appendChild(summaryValue(document, 'Age + gender', [age === null ? 'Age not published' : `Age ${age}`, gender].join(' · ')));
    const ageGroup = publicText(runner.age_group, 'Not published');
    const categoryPlaces = [
      formatPlace(runner.age_group_place) !== '—' ? `${formatPlace(runner.age_group_place)} age group` : null,
      formatPlace(runner.gender_place) !== '—' ? `${formatPlace(runner.gender_place)} gender` : null,
    ].filter(Boolean).join(' · ');
    facts.appendChild(summaryValue(document, ageGroup, categoryPlaces || 'Category place not published'));
    facts.appendChild(summaryValue(document, 'Last verified split', publicText(runner.last_recorded_split_name, 'Unavailable')));
    facts.appendChild(summaryValue(document, 'Recorded sections', publicText(analysis.totals && analysis.totals.recorded_section_count, '0')));
    header.appendChild(facts);
    const stop = runnerStopNotice(runner);
    if (stop) {
      const notice = element(document, 'p', 'results-stop-notice', stop);
      notice.setAttribute('role', 'status');
      header.appendChild(notice);
    }
    if (capabilityEnabled(analysis.capabilities, 'active_energy')) {
      header.appendChild(renderEnergyCard(document, analysis, renderedSections ? renderedSections.energyCells : [], runner.name));
    }
    lead.appendChild(header);
    host.appendChild(renderAnalysisCapabilities(document, analysis));

    const top = element(document, 'div', 'results-detail-grid');
    if (capabilityEnabled(analysis.capabilities, 'field_comparison')) top.appendChild(renderSummary(document, analysis));
    top.appendChild(renderCourseVisualizations(document, payload.event || {}, eventCapabilities(payload), analysis));
    host.appendChild(top);

    if (renderedSections) host.appendChild(renderedSections.section);
  }

  function activeResultsRoute(root) {
    const shellRoute = root && root.CourseSignalShell && root.CourseSignalShell.route;
    if (!shellRoute || shellRoute.kind !== 'race' || shellRoute.mode !== 'results') return null;
    let race;
    let event;
    try {
      race = identifier(shellRoute.race, 'race');
      event = identifier(shellRoute.event, 'event');
    } catch (_error) {
      return null;
    }
    let url;
    let base;
    try {
      url = new URL(root.location.href);
      base = currentPageUrl(root.location.href);
    } catch (_error) {
      return null;
    }
    if (!url.search) {
      if (race !== FIXED_RACE_SLUG || event !== FIXED_EVENT_ID) return null;
      return {race, event, mode: 'results', runner: null};
    }
    if (!ResultsModel || !ResultsModel.isPrivacySafeRoute(url.href, base.href)) return null;
    if (url.searchParams.get('race') !== race || url.searchParams.get('event') !== event) return null;
    const mode = url.searchParams.get('mode');
    if (mode !== null && mode !== 'results') return null;
    const runnerValue = url.searchParams.get('runner');
    let runner = null;
    if (runnerValue !== null) {
      try {
        runner = runnerIdentifier(runnerValue);
      } catch (_error) {
        return null;
      }
    }
    return {race, event, mode: 'results', runner};
  }

  function collectNodes(document) {
    const map = {
      searchForm: 'resultsSearchForm',
      searchInput: 'resultsRunnerSearch',
      searchSubmit: 'resultsSearchSubmit',
      status: 'resultsStatus',
      matches: 'resultsMatches',
      runnerLead: 'resultsRunnerLead',
      detail: 'resultsDetail',
      raceName: 'resultsRaceName',
      eventName: 'resultsEventName',
      eventMeta: 'resultsEventMeta',
      capabilities: 'resultsCapabilities',
    };
    const nodes = {};
    for (const [key, id] of Object.entries(map)) {
      const node = document.getElementById(id);
      if (!node) return null;
      nodes[key] = node;
    }
    return nodes;
  }

  function abortError() {
    const error = new Error('request aborted');
    error.name = 'AbortError';
    return error;
  }

  function utf8Length(text, limit = Infinity) {
    let bytes = 0;
    for (let index = 0; index < text.length; index += 1) {
      const code = text.charCodeAt(index);
      if (code < 0x80) bytes += 1;
      else if (code < 0x800) bytes += 2;
      else if (code >= 0xD800 && code <= 0xDBFF
        && index + 1 < text.length
        && text.charCodeAt(index + 1) >= 0xDC00
        && text.charCodeAt(index + 1) <= 0xDFFF) {
        bytes += 4;
        index += 1;
      } else bytes += 3;
      if (bytes > limit) return bytes;
    }
    return bytes;
  }

  function parsePayloadText(text) {
    if (typeof text !== 'string' || utf8Length(text, LIMITS.responseBytes) > LIMITS.responseBytes) {
      throw new Error('response is too large');
    }
    let payload;
    try {
      payload = JSON.parse(text);
    } catch (_error) {
      throw new Error('response unavailable');
    }
    if (!payload || payload.status !== 'ok') throw new Error('response unavailable');
    return payload;
  }

  async function boundedResponseText(response, signal) {
    const contentLength = response.headers && typeof response.headers.get === 'function'
      ? response.headers.get('content-length') : null;
    if (typeof contentLength === 'string' && /^\d+$/.test(contentLength)
      && Number(contentLength) > LIMITS.responseBytes) {
      throw new Error('response is too large');
    }
    if (response.body && typeof response.body.getReader === 'function' && typeof TextDecoder === 'function') {
      const reader = response.body.getReader();
      const decoder = new TextDecoder();
      const parts = [];
      let total = 0;
      while (true) {
        if (signal && signal.aborted) {
          if (typeof reader.cancel === 'function') await reader.cancel();
          throw abortError();
        }
        const chunk = await reader.read();
        if (chunk.done) break;
        if (!(chunk.value instanceof Uint8Array)) throw new Error('response unavailable');
        total += chunk.value.byteLength;
        if (total > LIMITS.responseBytes) {
          if (typeof reader.cancel === 'function') await reader.cancel();
          throw new Error('response is too large');
        }
        parts.push(decoder.decode(chunk.value, {stream: true}));
      }
      parts.push(decoder.decode());
      return parts.join('');
    }
    if (typeof response.text !== 'function') throw new Error('response unavailable');
    const text = await response.text();
    if (signal && signal.aborted) throw abortError();
    if (typeof text !== 'string' || utf8Length(text, LIMITS.responseBytes) > LIMITS.responseBytes) throw new Error('response is too large');
    return text;
  }

  async function requestPayload(root, url, signal) {
    const options = {cache: 'no-store'};
    if (signal) options.signal = signal;
    const transport = resultTransport(root, url);
    if (transport.headers) options.headers = transport.headers;
    const response = await root.fetch(transport.url, options);
    if (!response || !response.ok) throw new Error('request unavailable');
    return parsePayloadText(await boundedResponseText(response, signal));
  }

  function safeHistoryRoute(root, href) {
    try {
      const target = new URL(href);
      const base = currentPageUrl(root.location.href);
      if (!ResultsModel.isPrivacySafeRoute(target.href, base.href)) return false;
      if (root.history && typeof root.history.replaceState === 'function') {
        root.history.replaceState(null, '', target.href);
      }
      return true;
    } catch (_error) {
      return false;
    }
  }

  function createAbortController(root) {
    const Controller = root && typeof root.AbortController === 'function'
      ? root.AbortController
      : typeof globalThis !== 'undefined' && typeof globalThis.AbortController === 'function'
        ? globalThis.AbortController
        : null;
    if (!Controller) return null;
    try {
      return new Controller();
    } catch (_error) {
      return null;
    }
  }

  function invalidateRequest(state, channel) {
    const generationKey = `${channel}Generation`;
    const controllerKey = `${channel}Controller`;
    state[generationKey] += 1;
    const controller = state[controllerKey];
    state[controllerKey] = null;
    if (controller && typeof controller.abort === 'function') {
      try {
        controller.abort();
      } catch (_error) {
        // Generation checks remain authoritative when an AbortController shim fails.
      }
    }
    return state[generationKey];
  }

  function requestIsCurrent(state, channel, generation) {
    return state.active && state[`${channel}Generation`] === generation;
  }

  function sanitizeSearchMatches(payload, eventDefinition = null) {
    if (!payload || !Array.isArray(payload.matches)) throw new TypeError('search results are unavailable');
    const eventId = eventDefinition ? identifier(eventDefinition.id, 'event') : null;
    const eventLabel = eventDefinition ? publicText(eventDefinition.label, eventId) : '';
    const matches = [];
    for (const source of payload.matches.slice(0, LIMITS.matches)) {
      if (!source || typeof source !== 'object' || Array.isArray(source)) {
        throw new TypeError('search result is invalid');
      }
      const id = runnerIdentifier(source.id);
      matches.push({
        id,
        name: publicText(source.name, 'Anonymous Participant'),
        bib: source.bib === null || source.bib === undefined ? null : publicText(source.bib, ''),
        age: boundedInteger(source.age, 1, 120),
        gender: source.gender === null || source.gender === undefined ? null : publicText(source.gender, ''),
        age_group: source.age_group === null || source.age_group === undefined ? null : publicText(source.age_group, ''),
        age_group_place: boundedInteger(source.age_group_place, 1, LIMITS.fieldCount),
        gender_place: boundedInteger(source.gender_place, 1, LIMITS.fieldCount),
        status: normalizedStatus(source.status),
        finish_seconds: nonNegativeNumber(source.finish_seconds),
        finish_place: boundedInteger(source.finish_place, 1, LIMITS.fieldCount),
        ...(eventId ? {event_id: eventId, event_label: eventLabel} : {}),
      });
    }
    return matches;
  }

  async function initBrowser(root) {
    if (!root || !root.document || typeof root.fetch !== 'function') return {active: false, reason: 'browser_unavailable'};
    const route = activeResultsRoute(root);
    if (!route) return {active: false, reason: 'inactive_route'};
    if (initializedRoots.has(root)) return initializedRoots.get(root).ready;
    const nodes = collectNodes(root.document);
    if (!nodes) return {active: false, reason: 'missing_surface'};

    const state = {
      route,
      nodes,
      active: true,
      searchGeneration: 0,
      detailGeneration: 0,
      searchController: null,
      detailController: null,
      searchTimer: null,
      submitHandler: null,
      inputHandler: null,
    };
    const ready = {active: true, route, teardown: null};
    initializedRoots.set(root, {ready, state});
    renderEventHeader(root.document, nodes, null, route);
    renderCapabilityRows(root.document, nodes.capabilities, {});
    const searchScope = searchEventsForRoute(route);
    setStatus(
      nodes,
      searchScope.length > 1
        ? 'Type a public runner name or bib to search all five Rut 2026 distances. Search text stays out of this page URL and is not saved.'
        : 'Search by public runner name or bib. Search text stays out of this page URL and is not saved.',
      'instruction',
    );

    const loadRunner = async (runnerId, updateRoute) => {
      let endpoint;
      let normalizedRunnerId;
      try {
        normalizedRunnerId = runnerIdentifier(runnerId);
        endpoint = resolveResultEndpoint(root, buildRunnerEndpoint(route.event, normalizedRunnerId));
      } catch (_error) {
        if (state.active) setStatus(nodes, 'This runner link is invalid. Choose a result again.', 'error');
        return;
      }
      if (!state.active) return;
      invalidateRequest(state, 'search');
      const generation = invalidateRequest(state, 'detail');
      nodes.matches.removeAttribute('aria-busy');
      nodes.searchSubmit.disabled = false;
      if (updateRoute) {
        let shareUrl;
        try {
          shareUrl = buildShareUrl(root.location.href, route, normalizedRunnerId);
        } catch (_error) {
          if (requestIsCurrent(state, 'detail', generation)) {
            setStatus(nodes, 'A privacy-safe runner link could not be created.', 'error');
          }
          return;
        }
        if (!safeHistoryRoute(root, shareUrl)) {
          if (requestIsCurrent(state, 'detail', generation)) {
            setStatus(nodes, 'A privacy-safe runner link could not be created.', 'error');
          }
          return;
        }
      }
      if (!requestIsCurrent(state, 'detail', generation)) return;
      const controller = createAbortController(root);
      state.detailController = controller;
      nodes.searchInput.value = '';
      nodes.matches.replaceChildren();
      nodes.runnerLead.replaceChildren();
      nodes.runnerLead.hidden = true;
      nodes.detail.hidden = false;
      nodes.detail.setAttribute('aria-busy', 'true');
      setStatus(nodes, 'Loading the selected runner’s recorded result…', 'loading');
      try {
        const payload = await requestPayload(root, endpoint, controller && controller.signal);
        if (!requestIsCurrent(state, 'detail', generation)) return;
        if (typeof payload.stale !== 'boolean'
          || !payload.race || typeof payload.race !== 'object' || Array.isArray(payload.race)
          || payload.race.slug !== route.race
          || !payload.event || typeof payload.event !== 'object' || Array.isArray(payload.event)
          || payload.event.id !== route.event) {
          throw new TypeError('runner response does not match the requested route');
        }
        const analysis = sanitizeAnalysis(payload && payload.analysis, eventCapabilities(payload));
        if (!analysis || !analysis.runner
          || runnerIdentifier(analysis.runner.id) !== normalizedRunnerId) {
          throw new TypeError('runner analysis is unavailable');
        }
        renderEventHeader(root.document, nodes, payload, route);
        renderRunnerDetail(root.document, nodes.detail, root, route, {...payload, analysis}, nodes.runnerLead);
        setStatus(
          nodes,
          payload.stale === true ? STALE_RESULTS_WARNING : 'Runner result loaded.',
          payload.stale === true ? 'warning' : 'ready',
        );
      } catch (_error) {
        if (requestIsCurrent(state, 'detail', generation)) {
          nodes.runnerLead.replaceChildren();
          nodes.runnerLead.hidden = true;
          renderRetry(root.document, nodes.detail, 'This runner result is unavailable right now. Check the connection and try again.', () => loadRunner(normalizedRunnerId, false));
          setStatus(nodes, 'Runner result could not be loaded.', 'error');
        }
      } finally {
        if (requestIsCurrent(state, 'detail', generation)) {
          if (state.detailController === controller) state.detailController = null;
          nodes.detail.removeAttribute('aria-busy');
        }
      }
    };

    const selectSearchMatch = (runnerId, eventId) => {
      const selectedEvent = eventId || route.event;
      if (selectedEvent === route.event) {
        void loadRunner(runnerId, true);
        return;
      }
      try {
        const target = buildResultsRouteUrl(
          root.location.href,
          {race: route.race, event: selectedEvent, mode: 'results'},
          runnerId,
        );
        if (root.location && typeof root.location.assign === 'function') root.location.assign(target);
        else root.location.href = target;
      } catch (_error) {
        setStatus(nodes, 'A privacy-safe runner link could not be created.', 'error');
      }
    };

    const runSearch = async () => {
      if (!state.active) return;
      if (state.searchTimer !== null && typeof root.clearTimeout === 'function') {
        root.clearTimeout(state.searchTimer);
      }
      state.searchTimer = null;
      const generation = invalidateRequest(state, 'search');
      invalidateRequest(state, 'detail');
      nodes.detail.removeAttribute('aria-busy');
      nodes.detail.replaceChildren();
      nodes.detail.hidden = true;
      nodes.runnerLead.replaceChildren();
      nodes.runnerLead.hidden = true;
      let requests;
      try {
        requests = searchScope.map((eventDefinition) => ({
          eventDefinition,
          endpoint: resolveResultEndpoint(root, buildSearchEndpoint(eventDefinition.id, nodes.searchInput.value)),
        }));
      } catch (_error) {
        if (requestIsCurrent(state, 'search', generation)) {
          nodes.matches.removeAttribute('aria-busy');
          nodes.searchSubmit.disabled = false;
          setStatus(nodes, 'Enter 2 to 100 characters of a public name or bib.', 'error');
          nodes.searchInput.focus();
        }
        return;
      }
      const controller = createAbortController(root);
      state.searchController = controller;
      nodes.matches.replaceChildren();
      nodes.matches.setAttribute('aria-busy', 'true');
      nodes.searchSubmit.disabled = true;
      setStatus(
        nodes,
        requests.length > 1 ? 'Searching all five Rut 2026 distances…' : 'Searching published results…',
        'loading',
      );
      try {
        const settled = await Promise.allSettled(requests.map(async ({eventDefinition, endpoint}) => {
          const payload = await requestPayload(root, endpoint, controller && controller.signal);
          if (typeof payload.stale !== 'boolean' || payload.event_id !== eventDefinition.id) {
            throw new TypeError('search response does not match the requested event');
          }
          return {
            stale: payload.stale,
            matches: sanitizeSearchMatches(payload, requests.length > 1 ? eventDefinition : null),
          };
        }));
        if (!requestIsCurrent(state, 'search', generation)) return;
        const successful = settled.filter((result) => result.status === 'fulfilled').map((result) => result.value);
        if (!successful.length) throw new Error('runner search unavailable');
        const unavailableCount = settled.length - successful.length;
        const stale = successful.some((result) => result.stale === true);
        const matches = interleaveSearchMatches(successful);
        renderMatches(root.document, nodes.matches, matches, selectSearchMatch);
        if (unavailableCount) {
          setStatus(
            nodes,
            `Search incomplete: ${unavailableCount} of ${requests.length} distance${unavailableCount === 1 ? '' : 's'} could not be checked. Available matches are shown; no-match results are not conclusive.`,
            'warning',
          );
        } else if (stale) {
          setStatus(nodes, STALE_RESULTS_WARNING, 'warning');
        } else if (requests.length > 1) {
          setStatus(
            nodes,
            matches.length
              ? `${matches.length} published result${matches.length === 1 ? '' : 's'} found across ${requests.length} distances. Choose one for details.`
              : `No published results matched across all ${requests.length} distances. Check the spelling or try a bib number.`,
            matches.length ? 'ready' : 'empty',
          );
        } else {
          setStatus(nodes, matches.length ? `${matches.length} published result${matches.length === 1 ? '' : 's'} found. Choose one for details.` : 'No published results matched. Check the spelling or try a bib number.', matches.length ? 'ready' : 'empty');
        }
      } catch (_error) {
        if (requestIsCurrent(state, 'search', generation)) {
          renderRetry(root.document, nodes.matches, 'Runner search is unavailable right now. Check the connection and try again.', runSearch);
          setStatus(nodes, 'Runner search could not be completed.', 'error');
        }
      } finally {
        if (requestIsCurrent(state, 'search', generation)) {
          if (state.searchController === controller) state.searchController = null;
          nodes.matches.removeAttribute('aria-busy');
          nodes.searchSubmit.disabled = false;
        }
      }
    };

    const clearSearchTimer = () => {
      if (state.searchTimer !== null && typeof root.clearTimeout === 'function') {
        root.clearTimeout(state.searchTimer);
      }
      state.searchTimer = null;
    };
    state.inputHandler = () => {
      if (!state.active) return;
      clearSearchTimer();
      const query = normalizeQuery(nodes.searchInput.value);
      const length = Array.from(query).length;
      if (length < 2 || length > 100) {
        invalidateRequest(state, 'search');
        nodes.matches.replaceChildren();
        nodes.matches.removeAttribute('aria-busy');
        nodes.searchSubmit.disabled = false;
        setStatus(
          nodes,
          length > 100 ? 'Enter 2 to 100 characters of a public name or bib.'
            : searchScope.length > 1 ? 'Type at least 2 characters to search all five Rut 2026 distances.'
              : 'Type at least 2 characters to search published results.',
          length > 100 ? 'error' : 'instruction',
        );
        return;
      }
      invalidateRequest(state, 'search');
      if (typeof root.setTimeout === 'function') {
        state.searchTimer = root.setTimeout(() => {
          state.searchTimer = null;
          void runSearch();
        }, SEARCH_DEBOUNCE_MS);
      } else {
        void runSearch();
      }
    };
    state.submitHandler = (event) => {
      event.preventDefault();
      clearSearchTimer();
      void runSearch();
    };
    nodes.searchInput.addEventListener('input', state.inputHandler);
    nodes.searchForm.addEventListener('submit', state.submitHandler);

    ready.teardown = () => {
      if (!state.active) return;
      state.active = false;
      ready.active = false;
      clearSearchTimer();
      invalidateRequest(state, 'search');
      invalidateRequest(state, 'detail');
      if (typeof nodes.searchInput.removeEventListener === 'function') {
        nodes.searchInput.removeEventListener('input', state.inputHandler);
      }
      if (typeof nodes.searchForm.removeEventListener === 'function') {
        nodes.searchForm.removeEventListener('submit', state.submitHandler);
      }
      nodes.matches.removeAttribute('aria-busy');
      nodes.detail.removeAttribute('aria-busy');
      nodes.searchSubmit.disabled = false;
      nodes.matches.replaceChildren();
      nodes.runnerLead.replaceChildren();
      nodes.runnerLead.hidden = true;
      nodes.detail.replaceChildren();
      nodes.detail.hidden = true;
      initializedRoots.delete(root);
    };

    if (route.runner !== null) await loadRunner(route.runner, false);
    return ready;
  }

  return Object.freeze({
    normalizeQuery,
    buildSearchEndpoint,
    buildRunnerEndpoint,
    resolveResultEndpoint,
    resultTransport,
    buildShareUrl,
    adaptEnergyFactors,
    formatDuration,
    formatPace,
    capabilityRows,
    runnerStopNotice,
    courseSvgPoints,
    elevationSvgPoints,
    renderMatches,
    LIMITS,
    parsePayloadText,
    initBrowser,
  });
});
