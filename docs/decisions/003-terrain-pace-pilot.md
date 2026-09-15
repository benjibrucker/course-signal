# 003 — Terrain-aware checkpoint pace pilot

Status: implemented, published and verified as release 1.3.0; still an unvalidated race-day pilot.

## Approved scope

Use the runner's recent confirmed checkpoint intervals to estimate terrain-adjusted pace, integrate the upcoming route's elevation rather than move at a constant distance speed, update on each new check-in, and retain distinct estimated/GPS labels and the next-checkpoint hold boundary. Preserve managed hosting, privacy, chip clocks, source freshness, manual browsing and static fallback.

## Evidence and source contracts

- Bulk history: `https://api.competitivetiming.com/events/{event_id}/results`. The public results client requests this endpoint without per-runner calls or pagination. `splits` contains index and elapsed seconds; private/identity fields are not needed by the estimator and must not be exposed or recorded.
- Index >=1 `elapsed_seconds` is cumulative chip time. Index 0 is special: its elapsed value can equal the start offset, not chip elapsed zero. Build a chip-origin `(0, 0)` anchor; do not subtract the offset again from later split times. Source: https://competitivetiming.com/_next/static/chunks/app/events/%5BraceSlug%5D/%5Byear%5D/%5Bdistance%5D/results/page-5ec1be894934fac6.js . Parent inspection confirmed a deidentified finished record's final split equals finish time and first segment equals first non-start elapsed time.
- Elevation: course-map `trackPoints[].ele` is meters; the official elevation client multiplies by 3.28084 and labels feet: https://competitivetiming.com/_next/static/chunks/9753-5c7c51347c79cb7f.js . The current 28K map has 920 elevation-bearing samples. Use event-matched geometry; missing elevation is not sea level.
- Minetti et al. (2002), DOI 10.1152/japplphysiol.01177.2001, Figure 1: running cost `C(g)=155.4g^5-30.4g^4-43.3g^3+46.3g^2+19.5g+3.6`, grade is rise/horizontal run. Full paper: https://iris.unibs.it/bitstream/11379/540545/1/Minetti%20JAP%202002.pdf . This treadmill energy model is NOT a validated trail-speed predictor. The paper warns that unrestricted downhill speed predictions exceed observed race speeds substantially.

## Implementation choices — pilot parameters, not published recommendations

1. Construct a distance-resampled/smoothed elevation profile, then accumulate bounded terrain effort along the route. Reject corrupt, incomplete, excessively sparse profiles rather than fill unknowns.
2. Bound grade to the paper's domain and effort multiplier to a conservative range. Remove automatic downhill metabolic speed bonuses where terrain technicality is unknown. Terrain roughness, weather, rests and walking transitions remain unobserved.
3. Divide confirmed interval time by interval effort. Use at most three recent intervals with recency weighting, blend toward the runner's cumulative effort-normalized pace, and bound abrupt changes. Include long plausible intervals: they may contain aid-station stops and are not measured moving pace.
4. Invert accumulated effort only between the last confirmed and next unconfirmed checkpoint. Keep the existing 99%-of-interval cap and overdue hold; never infer passing later checkpoints or finishing.
5. Refresh optional bulk history on a bounded cache. If stale, malformed or inconsistent with the current leaderboard's checkpoint/chip offset, use only the confirmed start-to-latest interval and label limited history. Optional history failure must not hide healthy measured GPS or invent a new observation time.
6. Missing terrain falls back to the existing explicitly labeled checkpoint-average model. Invalid core timing still fails closed. GPS precedence, terminal status and anonymity union remain authoritative across all sources.
7. Publish `TERRAIN_CHECKPOINT_PILOT` basis plus pace-history support fields. Do not present a confidence interval or claim accuracy until race-day observations support it. Record future evaluation as aggregate errors only, not participant histories in Git.

## Historical data decision

The 2025 and 2026 28K endpoints publish identical track arrays, but two checkpoint markers differ and top-level race distances disagree. Prior-year segment times are therefore NOT automatically applied as an exact calibration. Public pace multipliers also have no verified historical provenance. The pilot uses the participant's verified checkpoint history and terrain; historical reference is reserved for retrospective comparison until course/checkpoint compatibility is settled. Do not double-count climbing by multiplying an already terrain-informed historical duration by another hill penalty.

## Verification gates

- Unit tests: grade/position behavior, rolling history, incomplete elevations, timestamp integrity, next-checkpoint containment, chip offsets and numerical guards.
- Integration: bulk-result normalization, history fallback, anonymity/terminal status union, stale holds, GPS precedence and serialized public metadata.
- Retrospective withheld-checkpoint comparison against the existing estimator; report coverage and error honestly, not as proof of tomorrow's accuracy.
- Public API readback, packaging/import check, frontend pilot/fallback labels, mobile rendering and snapshot recovery. No scheduled morning job or paid hosting change is included.

## Verification record before publication

- Real aggregate replay reports in `docs/qa/` show improved overall median next-checkpoint timing error without tuning on the replay; 28K checkpoint 4 and 21K checkpoint 3 worsened. These are completed-runner cohorts, not future field validation.
- Independent frontend/evaluator/packaging review approved and demonstrated prefix-only prediction inputs by changing future times without changing the earlier prediction.
- API review found optional-history latency expiring core metadata: first within an event, then across concurrently loaded events. Both were reproduced with failing regressions. Optional history now completes for **all races** before any core snapshots are fetched. Original TTLs remain unchanged.
- Local HTTP returned 1.3.0, five terrain-ready courses, fresh optional histories and no core errors. Phone-size Chromium showed the 28K preview, painted basemap tiles and no overflow/JS errors. Five-course geometry was verified through actual Leaflet layer membership and viewport bounds; generic DOM-pane probes were invalid, not application failures.
- An isolated synthetic browser fixture verified terrain/recent-history labels, pilot arrival wording, readable phone layout and overdue exclusion. It was never written to public data or Git snapshots.
- Final API rereview approved the two-phase history/core fix; no optional history re-fetch or TTL relaxation. Parent final suites: 114 Python / 19 Node pass.
- A small display-only addition uses neutral pre-start wording for source DNS-shaped records without changing raw status or eligibility. Card/search behavior was verified against the real upcoming roster. Initial test-fixture clock-field and predicate mistakes were corrected before frontend publication.
- API 1.3.0 was deployed to the existing production project. Public health/bootstrap confirmed five terrain-ready courses and fresh histories; source/config routes remain 404. Pages workflow 34672037825 succeeded for runtime commit ae0390883d02db0179c3482ec82a2ed9ae47eb9d. Public phone-size and desktop rendering, all five course layers, explicit periodic-snapshot fallback and live recovery were verified. Actual iPhone/Safari and next-day field accuracy remain unverified.
