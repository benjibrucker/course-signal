/* Course Signal route, catalog, and browser shell. No live-map state lives here. */
((root, factory) => {
  const api = factory();
  if (typeof module === "object" && module.exports) {
    module.exports = api;
    return;
  }

  const shell = Object.assign({}, api);
  const route = api.parseRoute(root.location?.href);
  shell.route = route;
  shell.shouldBootLive = route.kind === "race" && route.mode === "live";
  let browserTeardown = null;
  let domReadyListener = null;
  shell.initBrowser = () => {
    if (domReadyListener) {
      root.removeEventListener?.("DOMContentLoaded", domReadyListener);
      domReadyListener = null;
    }
    browserTeardown = api.initBrowser(root, shell);
    return browserTeardown;
  };
  shell.teardown = () => {
    if (domReadyListener) {
      root.removeEventListener?.("DOMContentLoaded", domReadyListener);
      domReadyListener = null;
    }
    browserTeardown?.();
    browserTeardown = null;
    shell.shouldBootLive = false;
  };
  root.CourseSignalShell = shell;
  if (root.document?.readyState === "loading") {
    domReadyListener = shell.initBrowser;
    root.addEventListener("DOMContentLoaded", domReadyListener);
  } else {
    shell.initBrowser();
  }
})(typeof window !== "undefined" ? window : globalThis, () => {
  "use strict";

  const FIXED_RACE_SLUG = "the-rut";
  const FIXED_EVENT_ID = "the-rut-28k-2026";
  const RUT_EVENT_IDS = new Set([
    "the-rut-50k-2026",
    "the-rut-28k-2026",
    "the-rut-21k-2026",
    "the-rut-11k-2026",
    "the-rut-vk-2026",
  ]);
  const MAX_CATALOG_RACES = 30;
  const MAX_EVENTS_PER_RACE = 100;
  const MAX_CATALOG_EVENTS = 300;
  const MAX_PUBLIC_TEXT_LENGTH = 200;
  const MAX_IDENTIFIER_LENGTH = 200;
  const MAX_QUERY_LENGTH = 100;
  const MIN_CATALOG_YEAR = 2000;
  const MAX_CATALOG_YEAR = 2100;
  const SEARCH_DEBOUNCE_MS = 300;
  const SAFE_ROUTE_FIELDS = new Set(["race", "event", "mode", "runner"]);
  const SAFE_IDENTIFIER = /^[A-Za-z0-9][A-Za-z0-9._~-]{0,199}$/;
  const RUNNER_IDENTIFIER = /^[1-9][0-9]{0,15}$/;
  const MAX_RUNNER_IDENTIFIER = "9007199254740991";
  const DATE_ONLY = /^(\d{4})-(\d{2})-(\d{2})$/;
  const ISO_TIMESTAMP = /^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2}):(\d{2})(?:\.(\d{1,9}))?(Z|[+-]\d{2}:\d{2})$/;
  const LIVE_STATUSES = new Set(["upcoming", "active", "awaiting", "racing", "started", "in_progress"]);
  const RESULTS_STATUSES = new Set(["closed", "past", "complete", "completed", "finished"]);
  const browserSetups = new WeakMap();
  const LIMITS = Object.freeze({
    maxCatalogRaces: MAX_CATALOG_RACES,
    maxEventsPerRace: MAX_EVENTS_PER_RACE,
    maxCatalogEvents: MAX_CATALOG_EVENTS,
    maxPublicTextLength: MAX_PUBLIC_TEXT_LENGTH,
    maxIdentifierLength: MAX_IDENTIFIER_LENGTH,
    maxQueryLength: MAX_QUERY_LENGTH,
    minCatalogYear: MIN_CATALOG_YEAR,
    maxCatalogYear: MAX_CATALOG_YEAR,
    searchDebounceMs: SEARCH_DEBOUNCE_MS,
  });

  function emptyRoute(kind) {
    return {kind, race: null, event: null, mode: null, explicitMode: false};
  }

  function fixedResultsRoute() {
    return {
      kind: "race",
      race: FIXED_RACE_SLUG,
      event: FIXED_EVENT_ID,
      mode: "results",
      explicitMode: true,
    };
  }

  function validDeploymentPath(value) {
    if (typeof value !== "string" || !value.startsWith("/") || !value.endsWith("/")) return false;
    if (value.includes("\\") || value.includes("%") || value.includes("//")) return false;
    if (value === "/") return true;
    const segments = value.slice(1, -1).split("/");
    return segments.every((segment) => SAFE_IDENTIFIER.test(segment) && segment !== "." && segment !== "..");
  }

  function parseRoute(input, baseUrl) {
    let raw;
    if (typeof input === "string") raw = input;
    else if (input && typeof input.href === "string") raw = input.href;
    else if (input && typeof input.search === "string") raw = `${input.pathname || "/"}${input.search}${input.hash || ""}`;
    else return emptyRoute("invalid");

    if (!raw || /[\u0000-\u0020\u007f]/.test(raw) || raw.includes("#") || raw.includes("\\") || raw.includes("%")) {
      return emptyRoute("invalid");
    }
    const rawPath = raw.split("?", 1)[0];
    if (/(?:^|\/)\.{1,2}(?:\/|$)/.test(rawPath)) return emptyRoute("invalid");

    let url;
    let base;
    try {
      if (baseUrl !== undefined) {
        const baseRaw = String(baseUrl);
        if (!baseRaw || /[\u0000-\u0020\u007f]/.test(baseRaw) || baseRaw.includes("#") || baseRaw.includes("\\") || baseRaw.includes("%")) {
          return emptyRoute("invalid");
        }
        base = new URL(baseRaw);
      } else if (/^[A-Za-z][A-Za-z0-9+.-]*:/.test(raw)) {
        const current = new URL(raw);
        base = new URL(`${current.origin}${current.pathname}`);
      } else {
        base = new URL("https://course.signal/");
      }
      if (
        !["http:", "https:"].includes(base.protocol)
        || base.username
        || base.password
        || base.search
        || base.hash
        || !validDeploymentPath(base.pathname)
      ) return emptyRoute("invalid");
      url = new URL(raw, base);
    } catch {
      return emptyRoute("invalid");
    }

    if (
      !["http:", "https:"].includes(url.protocol)
      || url.username
      || url.password
      || url.origin !== base.origin
      || url.pathname !== base.pathname
      || url.hash
      || !validDeploymentPath(url.pathname)
    ) return emptyRoute("invalid");

    const entries = [...url.searchParams.entries()];
    if (!entries.length) return fixedResultsRoute();
    const seen = new Set();
    for (const [key] of entries) {
      if (!SAFE_ROUTE_FIELDS.has(key) || seen.has(key)) return emptyRoute("invalid");
      seen.add(key);
    }
    if (!seen.has("race") || !seen.has("event")) return emptyRoute("invalid");
    const race = url.searchParams.get("race");
    const event = url.searchParams.get("event");
    if (!SAFE_IDENTIFIER.test(race) || !SAFE_IDENTIFIER.test(event)) return emptyRoute("invalid");
    const explicitMode = seen.has("mode");
    const mode = explicitMode ? url.searchParams.get("mode") : null;
    if (explicitMode && mode !== "live" && mode !== "results") return emptyRoute("invalid");
    if (!seen.has("runner")) return {kind: "race", race, event, mode, explicitMode};
    const runner = url.searchParams.get("runner");
    if (!explicitMode || mode !== "results" || canonicalRunnerIdentifier(runner) !== runner) {
      return emptyRoute("invalid");
    }
    return {kind: "race", race, event, mode, explicitMode, runner};
  }

  function requiredIdentifier(value, label) {
    if (typeof value !== "string" || !SAFE_IDENTIFIER.test(value)) {
      throw new TypeError(`${label} must be a canonical identifier of 1–${MAX_IDENTIFIER_LENGTH} characters`);
    }
    return value;
  }

  function requiredBasePath(value) {
    const path = value === undefined || value === null ? "/" : value;
    if (!validDeploymentPath(path)) throw new TypeError("base path must be a canonical deployment root");
    return path;
  }

  function buildRaceUrl({race, event, mode, basePath} = {}) {
    const safeRace = requiredIdentifier(race, "race");
    const safeEvent = requiredIdentifier(event, "event");
    if (mode !== undefined && mode !== null && mode !== "live" && mode !== "results") {
      throw new TypeError("mode must be live or results");
    }
    const query = [`race=${safeRace}`, `event=${safeEvent}`];
    if (mode !== undefined && mode !== null) query.push(`mode=${mode}`);
    return `${requiredBasePath(basePath)}?${query.join("&")}`;
  }

  function validCalendarParts(year, month, day) {
    const date = new Date(0);
    date.setUTCHours(0, 0, 0, 0);
    date.setUTCFullYear(year, month - 1, day);
    return date.getUTCFullYear() === year && date.getUTCMonth() === month - 1 && date.getUTCDate() === day;
  }

  function utcDateKey(date) {
    return date.getUTCFullYear() * 10000 + (date.getUTCMonth() + 1) * 100 + date.getUTCDate();
  }

  function calendarDateKey(value) {
    if (typeof value !== "string") return null;
    let match = DATE_ONLY.exec(value);
    if (match) {
      const [year, month, day] = match.slice(1).map(Number);
      return validCalendarParts(year, month, day) ? year * 10000 + month * 100 + day : null;
    }
    match = ISO_TIMESTAMP.exec(value);
    if (!match) return null;
    const [year, month, day, hour, minute, second] = match.slice(1, 7).map(Number);
    if (!validCalendarParts(year, month, day) || hour > 23 || minute > 59 || second > 59) return null;
    const zone = match[8];
    if (zone !== "Z") {
      const zoneHour = Number(zone.slice(1, 3));
      const zoneMinute = Number(zone.slice(4, 6));
      if (zoneHour > 14 || zoneMinute > 59 || (zoneHour === 14 && zoneMinute !== 0)) return null;
    }
    const instant = new Date(value);
    return Number.isFinite(instant.getTime()) ? utcDateKey(instant) : null;
  }

  function automaticMode(event, now = new Date()) {
    const source = typeof event === "string" ? event : event?.course_status ?? event?.courseStatus;
    const status = boundedPublicText(source).trim().toLowerCase();
    if (RESULTS_STATUSES.has(status)) return "results";
    if (LIVE_STATUSES.has(status)) return "live";
    const eventDate = typeof event === "object" && event !== null ? event.event_date ?? event.eventDate : null;
    const eventDateKey = calendarDateKey(eventDate);
    const current = new Date(now);
    if (eventDateKey !== null && Number.isFinite(current.getTime()) && eventDateKey < utcDateKey(current)) return "results";
    return "live";
  }

  function primitiveText(value) {
    return typeof value === "string" || typeof value === "number" ? String(value) : "";
  }

  function boundedPublicText(value) {
    const raw = primitiveText(value).slice(0, MAX_PUBLIC_TEXT_LENGTH);
    return raw.normalize("NFKC").slice(0, MAX_PUBLIC_TEXT_LENGTH).replace(/[\u0000-\u001f\u007f-\u009f]/g, " ");
  }

  function canonicalIdentifier(value) {
    const candidate = primitiveText(value);
    return candidate.length <= MAX_IDENTIFIER_LENGTH && SAFE_IDENTIFIER.test(candidate) ? candidate : null;
  }

  function canonicalRunnerIdentifier(value) {
    if (typeof value !== "string" || !RUNNER_IDENTIFIER.test(value)) return null;
    if (value.length === MAX_RUNNER_IDENTIFIER.length && value > MAX_RUNNER_IDENTIFIER) return null;
    return value;
  }

  function attachCatalogMeta(races, meta) {
    Object.defineProperty(races, "catalogMeta", {
      configurable: false,
      enumerable: false,
      writable: false,
      value: Object.freeze({...meta}),
    });
    return races;
  }

  function normalizeCatalog(payload) {
    const output = [];
    const sourceRaces = payload?.status === "ok" && Array.isArray(payload.races) ? payload.races : null;
    const meta = {
      malformed: sourceRaces === null,
      sourceRaceCount: sourceRaces?.length || 0,
      sourceRaceCapped: Boolean(sourceRaces && (
        payload?.has_more === true
        || sourceRaces.length > MAX_CATALOG_RACES
        || (sourceRaces.length === MAX_CATALOG_RACES && payload?.has_more !== false)
      )),
      sourceHadRows: Boolean(sourceRaces?.length),
      eventsCapped: false,
      renderedEventCount: 0,
    };
    if (!sourceRaces) return attachCatalogMeta(output, meta);

    const seenRaces = new Set();
    const seenEvents = new Set();
    for (const race of sourceRaces.slice(0, MAX_CATALOG_RACES)) {
      if (!race || typeof race !== "object" || Array.isArray(race)) {
        meta.malformed = true;
        continue;
      }
      const slug = canonicalIdentifier(race?.slug);
      const name = boundedPublicText(race?.name);
      if (!slug || !name || seenRaces.has(slug)) continue;
      seenRaces.add(slug);
      const events = [];
      if (race.events != null && !Array.isArray(race.events)) {
        meta.malformed = true;
        continue;
      }
      const sourceEvents = race.events || [];
      if (sourceEvents.length > MAX_EVENTS_PER_RACE) meta.eventsCapped = true;
      for (const event of sourceEvents.slice(0, MAX_EVENTS_PER_RACE)) {
        if (meta.renderedEventCount >= MAX_CATALOG_EVENTS) {
          meta.eventsCapped = true;
          break;
        }
        if (!event || typeof event !== "object" || Array.isArray(event)) {
          meta.malformed = true;
          continue;
        }
        const id = canonicalIdentifier(event?.id);
        const eventName = boundedPublicText(event?.name);
        if (!id || !eventName || seenEvents.has(id)) continue;
        seenEvents.add(id);
        events.push({
          id,
          name: eventName,
          label: boundedPublicText(event.label),
          eventDate: boundedPublicText(event.event_date),
          courseStatus: boundedPublicText(event.course_status),
          liveTrackingEnabled: event.live_tracking_enabled === true,
        });
        meta.renderedEventCount += 1;
      }
      output.push({
        slug,
        name,
        raceType: boundedPublicText(race.race_type),
        location: boundedPublicText(race.location),
        events,
      });
    }
    return attachCatalogMeta(output, meta);
  }

  function catalogRenderModel(normalizedRaces, now, basePath = "/") {
    const sourceRaces = Array.isArray(normalizedRaces) ? normalizedRaces : [];
    const sourceMeta = sourceRaces.catalogMeta || {};
    const races = [];
    const seenRaces = new Set();
    const seenEvents = new Set();
    let renderedEventCount = 0;
    let eventsCapped = sourceMeta.eventsCapped === true;
    let malformed = sourceMeta.malformed === true;
    for (const race of sourceRaces.slice(0, MAX_CATALOG_RACES)) {
      if (!race || typeof race !== "object" || Array.isArray(race)) {
        malformed = true;
        continue;
      }
      const slug = canonicalIdentifier(race?.slug);
      const name = boundedPublicText(race?.name);
      if (!slug || !name || seenRaces.has(slug)) continue;
      seenRaces.add(slug);
      const renderedEvents = [];
      if (race.events != null && !Array.isArray(race.events)) {
        malformed = true;
        continue;
      }
      const sourceEvents = race.events || [];
      if (sourceEvents.length > MAX_EVENTS_PER_RACE) eventsCapped = true;
      for (const event of sourceEvents.slice(0, MAX_EVENTS_PER_RACE)) {
        if (renderedEventCount >= MAX_CATALOG_EVENTS) {
          eventsCapped = true;
          break;
        }
        if (!event || typeof event !== "object" || Array.isArray(event)) {
          malformed = true;
          continue;
        }
        const id = canonicalIdentifier(event?.id);
        const eventName = boundedPublicText(event?.name);
        if (!id || !eventName || seenEvents.has(id)) continue;
        seenEvents.add(id);
        const normalizedEvent = {
          id,
          name: eventName,
          label: boundedPublicText(event.label),
          eventDate: boundedPublicText(event.eventDate),
          courseStatus: boundedPublicText(event.courseStatus),
          liveTrackingEnabled: event.liveTrackingEnabled === true,
        };
        const mode = automaticMode(normalizedEvent, now);
        renderedEvents.push({
          ...normalizedEvent,
          mode,
          url: buildRaceUrl({race: slug, event: id, mode, basePath}),
          liveUrl: buildRaceUrl({race: slug, event: id, mode: "live", basePath}),
          resultsUrl: buildRaceUrl({race: slug, event: id, mode: "results", basePath}),
        });
        renderedEventCount += 1;
      }
      races.push({
        slug,
        name,
        raceType: boundedPublicText(race.raceType),
        location: boundedPublicText(race.location),
        events: renderedEvents,
      });
    }
    return {
      races,
      meta: Object.freeze({
        malformed,
        sourceRaceCount: sourceMeta.sourceRaceCount ?? sourceRaces.length,
        sourceRaceCapped: typeof sourceMeta.sourceRaceCapped === "boolean"
          ? sourceMeta.sourceRaceCapped
          : sourceRaces.length >= MAX_CATALOG_RACES,
        sourceHadRows: sourceMeta.sourceHadRows === true || sourceRaces.length > 0,
        eventsCapped,
        renderedEventCount,
      }),
    };
  }

  function normalizeQuery(value) {
    const normalized = String(value ?? "").normalize("NFKC").trim().replace(/\s+/g, " ");
    return Array.from(normalized).length <= MAX_QUERY_LENGTH ? normalized : null;
  }

  function queryLength(value) {
    return typeof value === "string" ? Array.from(value).length : 0;
  }

  function selectedCatalogYear(input) {
    const value = String(input?.value ?? "").trim();
    if (!/^\d{4}$/.test(value)) return null;
    const numeric = Number(value);
    if (numeric < MIN_CATALOG_YEAR || numeric > MAX_CATALOG_YEAR) return null;
    return value;
  }

  function element(document, tag, className, value) {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (value !== undefined) node.textContent = value;
    return node;
  }

  function setSurface(document, name) {
    const surfaces = {
      landing: document.getElementById("courseSignalLanding"),
      loading: document.getElementById("courseSignalRouteLoading"),
      results: document.getElementById("courseSignalResults"),
      live: document.getElementById("app"),
    };
    Object.entries(surfaces).forEach(([key, node]) => {
      if (node) node.hidden = key !== name;
    });
    document.body.dataset.courseSignalView = name;
    document.title = name === "landing" ? "Course Signal" : name === "results" ? "Results · Course Signal" : name === "live" ? "Live · Course Signal" : "Loading event · Course Signal";
  }

  function setLinkState(link, active) {
    if (!link) return;
    if (active) link.setAttribute("aria-current", "page");
    else link.removeAttribute("aria-current");
  }

  function configureHomeNavigation(document, basePath) {
    const homeUrl = requiredBasePath(basePath);
    const routeHome = document.getElementById("routeLoadingHome");
    if (routeHome) routeHome.href = homeUrl;
    const homeLinks = document.querySelectorAll?.(".shell-brand, .results-search-link, .shell-home-link") || [];
    for (const link of homeLinks) link.href = homeUrl;
  }

  function configureRaceShell(document, route, mode, basePath = "/") {
    const liveUrl = buildRaceUrl({race: route.race, event: route.event, mode: "live", basePath});
    const resultsUrl = buildRaceUrl({race: route.race, event: route.event, mode: "results", basePath});
    for (const id of ["liveModeLink", "resultsLiveModeLink"]) {
      const link = document.getElementById(id);
      if (link) link.href = liveUrl;
    }
    for (const id of ["resultsModeLink", "resultsResultsModeLink"]) {
      const link = document.getElementById(id);
      if (link) link.href = resultsUrl;
    }
    setLinkState(document.getElementById("liveModeLink"), mode === "live");
    setLinkState(document.getElementById("resultsModeLink"), mode === "results");
    setLinkState(document.getElementById("resultsLiveModeLink"), mode === "live");
    setLinkState(document.getElementById("resultsResultsModeLink"), mode === "results");
    const liveContext = document.getElementById("liveEventContext");
    const resultsContext = document.getElementById("resultsEventContext");
    if (liveContext) liveContext.textContent = route.race ? `Live · ${route.race}` : "Live event";
    if (resultsContext) resultsContext.textContent = route.event;
  }

  function eventMeta(event) {
    const date = event.eventDate ? event.eventDate.slice(0, 10) : "Date pending";
    const status = event.courseStatus ? event.courseStatus.replace(/_/g, " ") : "status pending";
    return `${date} · ${status}`;
  }

  function renderCatalog(document, model) {
    const host = document.getElementById("catalogResults");
    if (!host) return;
    host.replaceChildren();
    model.races.forEach((race) => {
      const card = element(document, "article", "catalog-card");
      const heading = element(document, "div", "catalog-card-heading");
      heading.appendChild(element(document, "h2", "catalog-race-name", race.name));
      const details = [race.raceType.replace(/_/g, " "), race.location].filter(Boolean).join(" · ");
      if (details) heading.appendChild(element(document, "p", "catalog-race-meta", details));
      card.appendChild(heading);

      const eventList = element(document, "ul", "catalog-event-list");
      if (!race.events.length) {
        const empty = element(document, "li", "catalog-event-empty", "No selectable events are available for this race.");
        eventList.appendChild(empty);
      }
      race.events.forEach((event) => {
        const row = element(document, "li", "catalog-event-row");
        const primary = element(document, "a", "catalog-event-choice");
        primary.href = event.url;
        primary.appendChild(element(document, "strong", "", event.label || event.name));
        if (event.label && event.label !== event.name) primary.appendChild(element(document, "span", "catalog-event-name", event.name));
        primary.appendChild(element(document, "span", "catalog-event-meta", eventMeta(event)));
        const autoLabel = element(document, "span", `catalog-auto-mode ${event.mode}`, `Opens ${event.mode === "live" ? "Live" : "Results"}`);
        primary.appendChild(autoLabel);
        row.appendChild(primary);

        const actions = element(document, "div", "catalog-event-actions");
        const live = element(document, "a", "catalog-mode-link", "Live");
        live.href = event.liveUrl;
        live.setAttribute("aria-label", `Open ${event.label || event.name} live`);
        const results = element(document, "a", "catalog-mode-link", "Results");
        results.href = event.resultsUrl;
        results.setAttribute("aria-label", `Open ${event.label || event.name} results`);
        actions.append(live, results);
        row.appendChild(actions);
        eventList.appendChild(row);
      });
      card.appendChild(eventList);
      host.appendChild(card);
    });
  }

  function setCatalogStatus(document, message, state = "instruction") {
    const status = document.getElementById("catalogStatus");
    if (!status) return;
    status.textContent = message;
    status.dataset.state = state;
  }

  function initCatalogSearch(root, document, lifecycle, basePath) {
    const search = document.getElementById("catalogSearch");
    const year = document.getElementById("catalogYear");
    const results = document.getElementById("catalogResults");
    if (!search || !year || !results) return;
    let timer = null;
    let controller = null;
    let requestSequence = 0;

    const invalidateRequest = () => {
      requestSequence += 1;
      controller?.abort();
      controller = null;
    };

    const reset = (message = "Type at least 2 characters to search race series, places, and events.") => {
      invalidateRequest();
      results.replaceChildren();
      results.removeAttribute("aria-busy");
      setCatalogStatus(document, message, "instruction");
    };

    const run = async () => {
      if (!lifecycle.isActive()) return;
      const query = normalizeQuery(search.value);
      if (query === null) {
        reset(`Search terms must contain 2 to ${MAX_QUERY_LENGTH} characters.`);
        return;
      }
      if (queryLength(query) < 2) {
        reset();
        return;
      }
      controller?.abort();
      const requestController = typeof AbortController === "function" ? new AbortController() : null;
      controller = requestController;
      const sequence = ++requestSequence;
      const params = new URLSearchParams();
      params.set("q", query);
      const selectedYear = selectedCatalogYear(year);
      if (selectedYear !== null) params.set("year", selectedYear);
      params.set("limit", String(MAX_CATALOG_RACES));
      results.replaceChildren();
      results.setAttribute("aria-busy", "true");
      setCatalogStatus(document, "Searching the race catalog…", "loading");
      try {
        const response = await root.fetch(`/api/catalog?${params.toString()}`, {
          cache: "no-store",
          signal: requestController?.signal,
        });
        if (!lifecycle.isActive() || sequence !== requestSequence) return;
        if (!response?.ok) throw new Error(`Catalog request failed (${response?.status})`);
        const payload = await response.json();
        if (!lifecycle.isActive() || sequence !== requestSequence) return;
        if (payload?.status !== "ok" || !Array.isArray(payload.races)) {
          throw new Error("Catalog response was unavailable");
        }
        const model = catalogRenderModel(normalizeCatalog(payload), undefined, basePath);
        if (model.meta.malformed) throw new Error("Catalog response contained malformed rows");
        renderCatalog(document, model);

        const raceCapped = model.meta.sourceRaceCapped;
        const eventCapped = model.meta.eventsCapped;
        if (raceCapped && eventCapped && model.races.length) {
          setCatalogStatus(document, `Showing the first ${model.races.length} race series and the first ${model.meta.renderedEventCount} events. Race and event results are capped; refine your search to see more.`, "ready");
        } else if (raceCapped) {
          if (!model.races.length) setCatalogStatus(document, `No usable race series found among the first ${MAX_CATALOG_RACES} source results. Results are capped; refine your search to see more.`, "empty");
          else setCatalogStatus(document, `Showing the first ${model.races.length} race series. Results are capped; refine your search to see more.`, "ready");
        } else if (eventCapped) {
          setCatalogStatus(document, `Showing ${model.races.length} race series and the first ${model.meta.renderedEventCount} events. Event results are capped; refine your search to see more.`, "ready");
        } else if (!model.races.length && model.meta.sourceHadRows) {
          setCatalogStatus(document, `No usable race series found for “${query}”.`, "empty");
        } else if (!model.races.length) {
          setCatalogStatus(document, `No races found for “${query}”.`, "empty");
        } else {
          setCatalogStatus(document, `${model.races.length} race series found. Choose an event or mode.`, "ready");
        }
      } catch (error) {
        if (error?.name === "AbortError" || !lifecycle.isActive() || sequence !== requestSequence) return;
        results.replaceChildren();
        setCatalogStatus(document, "Race search is unavailable right now. Check the connection and try again.", "error");
      } finally {
        if (lifecycle.isActive() && sequence === requestSequence) {
          if (controller === requestController) controller = null;
          results.removeAttribute("aria-busy");
        }
      }
    };

    const schedule = () => {
      if (!lifecycle.isActive()) return;
      if (timer !== null) root.clearTimeout(timer);
      timer = null;
      const query = normalizeQuery(search.value);
      if (query === null) {
        reset(`Search terms must contain 2 to ${MAX_QUERY_LENGTH} characters.`);
        return;
      }
      if (queryLength(query) < 2) {
        reset();
        return;
      }
      invalidateRequest();
      timer = root.setTimeout(() => {
        timer = null;
        return run();
      }, SEARCH_DEBOUNCE_MS);
    };
    search.addEventListener("input", schedule);
    year.addEventListener("input", schedule);
    lifecycle.addCleanup(() => {
      if (timer !== null) root.clearTimeout(timer);
      timer = null;
      search.removeEventListener("input", schedule);
      year.removeEventListener("input", schedule);
      invalidateRequest();
      results.removeAttribute("aria-busy");
    });
    setCatalogStatus(document, "Type at least 2 characters to search race series, places, and events.", "instruction");
  }

  function selectedEvent(payload, id) {
    if (!payload || typeof payload !== "object" || !Array.isArray(payload.events)) return null;
    let selected = null;
    let malformed = false;
    for (const event of payload.events.slice(0, MAX_EVENTS_PER_RACE)) {
      if (!event || typeof event !== "object" || Array.isArray(event)) {
        malformed = true;
        continue;
      }
      for (const field of ["course_status", "courseStatus", "event_date", "eventDate", "label", "name"]) {
        if (event[field] != null && !["string", "number"].includes(typeof event[field])) malformed = true;
      }
      if (canonicalIdentifier(event.id) === id) selected = event;
    }
    return malformed ? null : selected;
  }

  function showRouteProblem(document, message) {
    setSurface(document, "loading");
    const status = document.getElementById("routeLoadingStatus");
    if (status) status.textContent = message;
    const home = document.getElementById("routeLoadingHome");
    if (home) {
      home.hidden = false;
      home.focus?.();
    }
  }

  function createBrowserLifecycle(root) {
    let active = true;
    const cleanups = new Set();
    let lifecycle;
    const teardown = () => {
      if (!active) return;
      active = false;
      for (const cleanup of [...cleanups].reverse()) {
        try {
          cleanup();
        } catch {
          // Continue teardown so one failed cleanup cannot strand other resources.
        }
      }
      cleanups.clear();
      if (browserSetups.get(root) === lifecycle) browserSetups.delete(root);
    };
    lifecycle = {
      teardown,
      isActive: () => active,
      addCleanup(cleanup) {
        if (typeof cleanup !== "function") return;
        if (active) cleanups.add(cleanup);
        else cleanup();
      },
    };
    return lifecycle;
  }

  async function resolveAutomaticRoute(root, shell, lifecycle, basePath) {
    const document = root.document;
    setSurface(document, "loading");
    const routeStatus = document.getElementById("routeLoadingStatus");
    if (routeStatus) routeStatus.textContent = "Checking event status…";
    const home = document.getElementById("routeLoadingHome");
    if (home) home.hidden = true;
    const requestController = typeof AbortController === "function" ? new AbortController() : null;
    lifecycle.addCleanup(() => requestController?.abort());
    try {
      const eventId = encodeURIComponent(shell.route.event);
      const response = await root.fetch(`/api/event?event_id=${eventId}&include_course=0`, {
        cache: "no-store",
        signal: requestController?.signal,
      });
      if (!lifecycle.isActive()) return;
      if (!response?.ok) throw new Error(`Event request failed (${response?.status})`);
      const payload = await response.json();
      if (!lifecycle.isActive()) return;
      const event = selectedEvent(payload, shell.route.event);
      if (!event) throw new Error("Selected event was unavailable");
      const mode = automaticMode(event);
      const unresolvedRoute = shell.route;
      const resolvedRoute = {...unresolvedRoute, mode};
      lifecycle.addCleanup(() => {
        if (shell.route === resolvedRoute) shell.route = unresolvedRoute;
      });
      shell.route = resolvedRoute;
      shell.shouldBootLive = mode === "live";
      configureRaceShell(document, shell.route, mode, basePath);
      if (mode === "live") {
        setSurface(document, "live");
        root.__courseSignalLiveBoot?.();
      } else {
        const context = document.getElementById("resultsEventContext");
        if (context) context.textContent = boundedPublicText(event.label) || boundedPublicText(event.name) || shell.route.event;
        setSurface(document, "results");
      }
    } catch (error) {
      if (!lifecycle.isActive() || error?.name === "AbortError") return;
      shell.shouldBootLive = false;
      showRouteProblem(document, "This event could not be loaded. Return to race search and try again.");
    }
  }

  function initBrowser(root, shell) {
    if (!root || (typeof root !== "object" && typeof root !== "function")) return () => {};
    const existing = browserSetups.get(root);
    if (existing) return existing.teardown;

    const lifecycle = createBrowserLifecycle(root);
    browserSetups.set(root, lifecycle);
    const {teardown} = lifecycle;
    const document = root.document;
    if (!document || !shell || typeof shell !== "object") return teardown;

    const basePath = root.location?.pathname ?? "/";
    if (!validDeploymentPath(basePath)) {
      shell.shouldBootLive = false;
      showRouteProblem(document, "This race link is invalid. Return to race search and choose an event again.");
      return teardown;
    }

    const route = shell.route;
    lifecycle.addCleanup(() => { shell.shouldBootLive = false; });
    configureHomeNavigation(document, basePath);
    if (route?.kind === "landing") {
      shell.shouldBootLive = false;
      setSurface(document, "landing");
      initCatalogSearch(root, document, lifecycle, basePath);
      return teardown;
    }
    if (!route || route.kind !== "race") {
      shell.shouldBootLive = false;
      showRouteProblem(document, "This race link is invalid. Return to race search and choose an event again.");
      return teardown;
    }
    if (!route.race || !route.event) {
      shell.shouldBootLive = false;
      showRouteProblem(document, "This race link is incomplete. Return to race search and choose an event again.");
      return teardown;
    }
    if (
      canonicalIdentifier(route.race) !== route.race
      || canonicalIdentifier(route.event) !== route.event
      || !(
        (route.explicitMode === true && ["live", "results"].includes(route.mode))
        || (route.explicitMode === false && route.mode === null)
      )
      || (route.runner !== undefined && (
        route.mode !== "results"
        || canonicalRunnerIdentifier(route.runner) !== route.runner
      ))
    ) {
      shell.shouldBootLive = false;
      showRouteProblem(document, "This race link is invalid. Return to race search and choose an event again.");
      return teardown;
    }
    if (route.race !== FIXED_RACE_SLUG || !RUT_EVENT_IDS.has(route.event)) {
      shell.shouldBootLive = false;
      showRouteProblem(document, "This version supports the five Rut 2026 distances only.");
      return teardown;
    }
    if (route.mode === null) {
      shell.shouldBootLive = false;
      resolveAutomaticRoute(root, shell, lifecycle, basePath);
      return teardown;
    }
    configureRaceShell(document, route, route.mode, basePath);
    shell.shouldBootLive = route.mode === "live";
    setSurface(document, shell.shouldBootLive ? "live" : "results");
    return teardown;
  }

  return {
    LIMITS,
    parseRoute,
    buildRaceUrl,
    automaticMode,
    normalizeCatalog,
    catalogRenderModel,
    normalizeQuery,
    initBrowser,
  };
});
