# Selected runner course-direction arrow — frontend 1.8.2

## Approved contract
- Red navigation dart on selected runner only, aligned to forward ordered course segment at supplied server progress.
- Course direction, not measured heading, body orientation, current movement or a new location estimate.
- Keep selected source-styled center/red ring and separate chartreuse recorded-checkpoint square intact.
- ON COURSE only; supported split estimates or route-matched GPS. Invalid/missing progress, unsupported mapping, malformed/degenerate route or endpoint has no arrow; original marker remains.
- Route order and cumulative distance resolve loops/crossings, not nearest coordinates. Duplicate coordinates skipped; no bridging malformed track points.
- Source/stale/held/snapshot labels retain their meaning. Frozen arrows describe course direction at the displayed position, not evidence of movement.
- No backend, estimation, source data, polling, dependencies or privacy changes.

## Verification gates
- Baseline: clean151f5cd;95Node and141Python passed.
- RED-first geometry/render regressions completed; combined126Node/159Python passed, including source-specific live/stale styling and selected-tooltip clearance.
- Independent geometry oracle:50 synthetic progress samples across all5 real course tracks matched Leaflet distance accumulation/projected screen angles; maximum angle difference1.16e-8degrees. No real participant progress changed.
- Combined independent spec and quality/security reviews passed; no release blockers.
- Browser: initial north-arrow tooltip overlap fixed. Reloaded phone expanded Map confirms42px offset, label ends342px and arrow starts355px; red fill/white stroke visible. Nine isolated browser cases passed north/east/south, stale/held, GPS live/stale, missing progress, finished and unmatched GPS; unknown direction removes arrow only. Combined layout/public checks passed; selected arrow transfers correctly in1.9.1 and retains source interior/recorded square.
- Public startup snapshot and live recovery passed while preserving selected key and expanded Info/Elevation.
- Included in1.9.0, then1.9.1 selection fix: Pages34715300430 success; public HTML/app/rules/CSS byte-match tested source.
- Physical iPhone/Safari is not claimed.
