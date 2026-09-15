# Recorded check-in versus estimated position — QA

Target frontend1.8.0; baseaea0b9c. API1.5.0 and terrain estimator unchanged.
User approved official-timing-only display; no manual sighting logging.

## Acceptance

- Last recorded checkpoint, Mountain clock/date/age lead each selected runner's Info card, before predictive content.
- Use only validated recorded passages. Missing clocks/read histories explicit; no GPS or estimator timestamp substitution. Preserve observed Start and finished/locationless histories.
- Selected recorded checkpoint and estimated current location have visibly distinct Map/Elevation markers and clear labels; checkpoint highlight is not the estimated runner coordinate.
- Geographic name/index guards handle filtered VK split_points without shifting Swiftcurrent onto Finish. Elevation retains authoritative timing index.
- Clear old marker/tooltip/hidden SVG state on selection/privacy/no-selection refresh; no full redraw or scroll reset on ordinary ticks.
- Retain Map/Elevation, split/expanded modes, manual/follow/camera and existing checkpoint/ETA controls.

## Verification status

Baseline80Node/141Python passed. Parent browser reproduced1.7.0: actual latest idx3/time exists in recorded passages, but card shows only checkpoint name while estimated progress is farther along the route. Geographic source check confirms VK’s filtered split array omits Swiftcurrent. Implementation90Node/141Python passed. Local390×675 and320×568 show actual clock before estimates, real source-to-marker coordinates match, and four settled Map/Elevation×Info/Display transitions preserve actual Leaflet camera/selection/follow/manual state. Elevation shows separate recorded square and estimated circle. Browser QA caught wrapping collapse in Leaflet’s zero-width tooltip pane; explicit max-content width with viewport cap corrects it. Spec review found impossible dates, missing elevation provenance and malformed geography; narrow RED-first fixes passed94Node/141Python. Independent final spec review passed, including actual-app terminal/locationless/null-geometry/provenance probes. Corrected five-course browser selection/geometry checks passed8/6/5/3/3 profile checkpoints; prior-day Finish dates stay explicit and upcoming races have no invented Start. Isolated real-browser snapshot+stale+partial labels remain present with Info hidden, and anonymity/selection clearing removes old names, clock and markers. Independent final quality/security review approved94Node/141Python, injection/calendar/geometry probes and no blocking findings. Published d4d024830923d936c7dbac39c8c1b268e66ac418; Pages34707070535 succeeded. All four hosted runtime assets equal local source. Public390×675,320×568,1280×800,1200×500 verified actual clocks/anchors, distinct profile markers, no overflow/errors and appropriate Finish access. Blocked-API startup loaded an explicitly dated snapshot; live recovery retained selection/Elevation/expanded Display and advanced to a genuinely new recorded checkpoint. No physical iPhone/Safari claim. Synthetic probes isolated from real data and public deployment.
