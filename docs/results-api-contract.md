# Course Signal — Completed-results API contract

## Routes
### `GET /api/results/search`
Required query:
- `event_id`: strict provider identifier, catalog membership required before any event-specific fetch.
- `q`: an actual string normalized with Unicode NFKC, trimmed, and whitespace-collapsed; reject controls/surrogates and require 2–100 normalized characters; matched as public name or bib.

Optional query:
- `limit`: canonical ASCII decimal integer 1–20 with no sign, leading zero, padding, decimal, exponent, or Unicode digits; default 10.

Success body:
```json
{
  "status": "ok",
  "event_id": "public-event-id",
  "stale": false,
  "matches": [
    {
      "id": 123,
      "name": "Public Runner or Anonymous Participant",
      "bib": "42",
      "status": "FINISHED",
      "finish_seconds": 12345.6,
      "finish_place": 12
    }
  ]
}
```
No split arrays or non-selected participant metadata belong in search output. Source result ID `0` is never a public selectable ID and must be omitted rather than replaced.

### `GET /api/results/runner`
Required query:
- `event_id`: same grammar and membership rule.
- `runner_id`: canonical positive ASCII decimal public result identifier, with no sign, leading zero, whitespace, decimal, exponent, or Unicode digits, and no value above `9,007,199,254,740,991`.

Success body:
```json
{
  "status": "ok",
  "stale": false,
  "race": {"slug": "race-slug", "name": "Race name", "location": "Published location"},
  "event": {"id": "event-id", "label": "Distance", "event_date": "2026-01-01", "course_status": "finished", "course": {"track_points": [], "split_points": [], "progress_points": []}, "capabilities": {}},
  "analysis": {"runner": {}, "sections": [], "totals": {}, "summary": {}, "capabilities": {}, "limitations": []}
}
```
The event course object is exactly the minimized snake_case browser map contract above, not raw provider course metadata. Internal analysis-only `trackPoints` data never leaves the backend.

## Trust boundary
1. Validate identifier grammar.
2. Resolve event membership from the cached minimized catalog.
3. Build a fixed, URL-encoded provider path under `https://api.competitivetiming.com`.
4. Bound response bytes and record count.
5. Immediately copy only analysis-required fields into a sanitized in-memory source.
6. Cache only that sanitized source; do not place the raw provider result/leaderboard body in the shared HTTP cache.
7. Return only public runner search rows or one selected analysis.

## Sanitized internal result source
Allowed per-runner fields before analysis:
- `id`, `name`, `bib`
- `is_anonymous`, `athlete_anonymous`
- official status fields required by normalization
- `finish_time_seconds`, official finish/overall place
- `chip_start_seconds`, `last_split_index`, `last_split_time`
- `splits`, each reduced to `split_index`, `elapsed_seconds`, official cumulative place, and official segment place

A separately sanitized leaderboard identity/anonymity evidence set may be included. No email, phone, date of birth, age, sex/gender, address/location, question responses, custom answers, registration fields, video/user metadata, or source configuration survives this step.

## Cache behavior
- Key by exact event ID.
- Keep only sanitized structures.
- Use a bounded TTL appropriate for completed data and return a previously sanitized value on transient provider error when available.
- The production Results singleton must configure a combined cold results-plus-leaderboard deadline below the managed function’s 60-second `maxDuration`; fail early rather than leave background work running past the response budget.
- Do not write result caches to disk or source artifacts.
- Do not include provider exception text or bodies in public errors.

## Capability and fallback behavior
- Search/results may work without a course map.
- Runner analysis may be timing-only when route, elevation, or strict split progress is unavailable.
- Start/Finish-only events show overall status, finish time/place, and last verified split when supported, but emit no intermediate sections, no synthetic Start→Finish section, and a `recorded_section_count` of zero.
- Running energy is allowed only for catalog-eligible foot events with complete event-matched elevated geometry and valid adjacent section progress.
- Missing, malformed, incomplete, or DNF records remain partial; Finish is never inferred.
- Both successful routes emit a real boolean `stale`. A stale search may return 200. If a stale detail source does not contain the requested runner, return generic 503 rather than an authoritative 404; a fresh source miss returns 404. A cold optional course failure degrades to timing-only 200 with `stale: true`, while event-metadata failure remains 503.

## Logging
- Do not log raw upstream bodies or sanitized result records.
- Redact runner-search free text from local request logs.
- Weight never appears in these routes, parameters, or bodies.

## HTTP behavior
- Read-only GET/OPTIONS only; existing non-GET rejection remains.
- Accept only the documented unique parameters. Reject missing, blank, duplicate, unknown, aliased, malformed, or excessive query fields before catalog/cache/source work.
- Local and Vercel adapters expose identical validation and generic 400/404/503 behavior.
- CORS remains restricted to the approved GitHub Pages origin for the managed adapter.
- Successful and failed Results responses use `Cache-Control: no-store`; the managed adapter also uses `Vercel-CDN-Cache-Control: no-store`.
- Vercel includes every imported runtime module and routes both nested result paths.
