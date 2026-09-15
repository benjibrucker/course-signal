# 002 — Phone map recovery and multi-day race state

Status: accepted and implemented. Base release 1.2.0 published and publicly verified; animation-independent startup follow-up 1.2.1 locally verified before publication.

## Scope and evidence

The user approved moving forward and supplied an iPhone screenshot with a connected live feed but no visible basemap/course. The screenshot does not establish whether the cause was tile delivery, browser caching, or rendering. Current Chromium mobile reproduction loaded the basemap, so the exact physical-phone failure remains unconfirmed. Automated WebKit launch/new-page checks hung; they are not a Safari pass.

Independent of the screenshot, the official metadata can mark a future race active and retain prior-day status flags. Competitive Timing's public client adds `chip_start_seconds` to chip-relative `last_split_time` for gun-time display. Its `estimated_finish_seconds` clock basis was not established and must not be presumed chip-relative.

Source: https://competitivetiming.com/_next/static/chunks/app/events/%5BraceSlug%5D/%5Byear%5D/%5Bdistance%5D/page-88a5c4ec3426403f.js
Schedule: https://runtherut.com/schedule/

## Decisions

- Keep an explicitly sized, isolated map surface and remove mobile backdrop blur. Refresh Leaflet size after resize, page restore, and tab visibility changes.
- Provide an always-available map-retry button, bounded tile-provider fallback, and a visible failure notice while retaining course vectors.
- Open on the map, not an empty Finish watch panel. Version frontend asset URLs to avoid old CSS/new JS mixtures after rollout.
- Complete loading with the native hidden attribute and fit courses without animation after stopping pending Leaflet movement and refreshing map dimensions. Observed browser checks showed loaded data and tiles could coexist with pending CSS/Leaflet animation state; do not depend on animation frames to reveal or frame the map.
- Interpret event dates and planned start clocks in each event's timezone. Upcoming active flags do not indicate a start; actual start evidence is required. Prior-day events are closed for live spectator ranking, not automatically assigned official DNF.
- Default Race day to a started current-day event or preview the next course. Auto mode rolls forward; explicit course and runner locks remain visible until released.
- Finish watch fails closed for unknown schedule/start evidence, closed races, and pre-start races, in addition to existing position/source freshness rules.
- Add verified chip offsets to race start for checkpoint wall-clock age and arrival calculations. Invalid/missing offsets in real feeds do not default to the first wave.
- Derive real checkpoint estimates from observed checkpoint pace, explicitly labeled `CHECKPOINT_PACE_CHIP`, rather than guessing the clock basis of the upstream projected-finish field. Start-only goals do not manufacture moving real-feed positions.

## Verification and limitations

Node regression coverage includes Friday/Saturday/Sunday rollover, pre-start active flags, actual-start confirmation, unknown metadata, manual selection retention, and existing snapshot expiry. Python coverage includes zero/later chip starts, absent/invalid/future/overflow offsets, checkpoint wall-clock age, pace-based ETA, terminal runner exclusion, and anonymity.

Browser QA blocks both real tile providers: course vectors remain visible and a retry notice appears. Restoring network and clicking Retry map loads real tiles and clears the notice. Mobile 390-pixel layout, default 28K preview and public release must be checked independently of unit tests.

No synthetic rehearsal or scheduled morning preflight is part of this urgent repair release. No paid hosting changes. Actual iPhone confirmation remains a user-side check.
