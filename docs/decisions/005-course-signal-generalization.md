# 005 — Course Signal generalization and retrospective runner analysis

Status: accepted for local implementation; publication requires separate user approval.

## Context
The verified Rut Live Map is hard-coded to five 2026 Rut events. The user approved a new product, Course Signal, that discovers Competitive Timing foot races, preserves live map behavior where sources support it, and adds completed-runner split/terrain/field analysis. The existing Rut URL and repository must remain operational and unchanged during local development.

Competitive Timing publishes a large public race catalog with authoritative `race_type`, embedded event IDs, public result/split endpoints, and optional course/GPS endpoints. Coverage varies materially by event. Raw results can include fields the product does not need and must not publish.

## Decision
1. Develop in a cloned, isolated repository at `/Users/benjibook/Documents/Course Signal`. Retain the Rut source as fetch-only and disable its push URL.
2. Support `road`, `cross_country`, and `trail_running` races only in version 1. Treat event/race titles as display data, not authority for sport type.
3. Fetch only from the fixed Competitive Timing API host. Accept only race/event IDs discovered in the cached catalog and matching a strict slug/identifier grammar. User input must never become a URL host or arbitrary upstream path.
4. Add a minimized search/catalog contract and dynamic event contracts. Allowlist every emitted field; never relay raw records, arbitrary source errors, email, phone, DOB, custom responses, or unused demographic/device fields.
5. Derive a capability matrix per event: results, intermediate splits, course map, elevation, live tracking/GPS, and supported checkpoint estimation. Show unavailable capabilities explicitly rather than synthesizing them.
6. Open active/started events in Live mode and completed/closed events in Results mode, with an explicit user switch. Existing live safety, freshness, privacy, chip-clock, route-matching, and hold rules remain authoritative.
7. Build completed analysis from official cumulative splits and event-matched course geometry. Missing or nonmonotone reads remain unknown. DNF/incomplete runners stop at their last recorded checkpoint. Field comparisons use only valid comparable section records and publish aggregates, not other runners’ raw rows.
8. Compute route energy per kilogram from the existing bounded Minetti graded-running transport model. The browser alone multiplies by the user’s `Total weight (including gear)`. Show an explicitly heuristic range, not a confidence interval or medical/wearable claim. Exclude resting calories, age, sex, and height.
9. Share URLs may contain only public race slug/year/event/runner identifiers and display state. Weight and personalized calories never enter a request, URL, analytics event, server log payload, or shared state.
10. Continue with standard-library Python, vanilla JavaScript, and vendored Leaflet unless a demonstrated requirement cannot be met. No account, database, paid service, or new dependency is approved.
11. Every managed Results request uses one immutable monotonic absolute deadline rooted at HTTP handler entry. Search expires at entry +45 seconds; runner detail expires at entry +50 seconds, and its catalog/completed-result phase must finish by entry +35 seconds to reserve 15 seconds for exact event/course analysis and response work. Every redirect hop, bounded body read, cache lock/flight wait, pagination page, sanitizer/analysis loop, serialization step, and socket write consumes the same timeline; no stage restarts a duration. Deadline exhaustion returns only the generic 503 contract, publishes no partial response/cache value, and leaves prior complete bounded-stale entries unchanged. Existing Live behavior remains backward compatible and does not inherit new Results-only route behavior.

## Rejected alternatives
- Rebrand or overwrite the existing Rut deployment during development: breaks the isolation and rollback boundary.
- Support all timing providers or all sports in version 1: requires incompatible ingestion and energy models.
- Hide data-limited foot races: unnecessarily prevents useful finish/split lookup.
- Collect age, sex, or height for active calories: does not improve the selected graded transport model enough to justify added personal data.
- Fetch Competitive Timing directly from the Pages browser: bypasses minimization and has failed origin checks.
- Persist raw results or participant snapshots: unnecessary and contrary to the accepted privacy boundary.

## Verification gates
- Catalog filtering, ID validation, SSRF/path safety, minimization, and capability tests.
- Completed runner tests for full, sparse, DNF, anonymous, malformed, and no-course records.
- Energy invariants: flat baseline, uphill/downhill bounds, additive sections, positive finite output, unit conversion, and no demographic inputs.
- Share-link tests proving no weight/calorie/private values appear.
- Regression suites for all inherited live behavior.
- Real browser checks on Rut 2026, another full-data foot race, and a limited-data foot race at desktop and phone sizes.
- Independent spec and code-quality review before a local verified commit.
- No remote creation, deployment, push, or existing-site change in this implementation phase.
