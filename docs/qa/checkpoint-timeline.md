# Full checkpoint timeline — frontend1.9.0 / API1.6.0

## Approved scope

- Preserve actual recorded checkpoint clocks including evidenced Start; missing historical reads remain missing.
- Promote next arrival/countdown and last-recorded→next segment after the recorded block.
- Extend the existing single checkpoint dialog with every remaining checkpoint/Finish forecast. No repeated hidden identity-bearing timeline.
- Later forecasts explicitly less certain. No invented accuracy intervals or confirmed arrival claims.
- Next/Finish forecasts must agree with existing model outputs. All future times are backend outputs based on current observed pace, not frontend extrapolation.
- Keep next-checkpoint location hold boundary, chip-start timestamps, freshness/privacy, snapshot capture semantics and all current Map/Elevation/workspace behavior.
- Include red course-direction arrow from the prior approved local slice.

## Verification

- Pre-extension baseline:114Node/141Python; arrow not published separately.
- RED-first regressions completed; final126Node/159Python pass. Backend36event/48terrain differential probes preserved existing fields exactly.
- Independent spec PASS and final quality/security PASS. Additional backend→frontend clocks/expiry, wave and malformed/numeric-boundary probes passed. Optional internal-helper numeric-string hardening is nonblocking because production paths normalize inputs.
- Restarted localAPI1.6.0: all5course feeds,0source errors;408supported28Kforecast runners (256withmultiple future checkpoints) at verification. Every nonempty array checked contiguous futureindexes, strict monotonic clocks, next/finish exactagreement, eligibility and no overlap with recordedpassages. No participant records persisted.
- Eight isolated browser cases passed: full schedule, expired next, held estimate, stale feed, GPS fresh-at-snapshot/currently stale, expired snapshot, malformed future date, new official read. New read advances current segment and converts that row to recorded without guessing skipped reads. QA fixture initially appended a read twice through an aliased array; parser correctly rejected duplicates. Corrected fixture uses independent array assignment and a new feed timestamp. Privacy and layout checks completed below.
- First390px synthetic browser pass: promoted current segment/next arrival/countdown visible in Info content; actual pointer on new CTA opens one dialog with2recorded rows (including Start) and3forecast rows, next highlighted and later rows labeled Less certain. Recorded clocks retain seconds/date, forecasts show~minute precision. No horizontal overflow. Final20-case portrait/landscape/desktop × pane/view matrix passed; last row reachable. Landscape camera has the same ≤1px rounding reproduced on published1.8.1. Actual pointer Close clears hidden timeline; anonymous refresh removes old identity from open/hidden DOM while retaining selected arrow.
- API1.6.0/public1.9.0 verified, followed by1.9.1 hidden-map selection fix (see hidden-map-selection.md). Four runtime files byte-match. Public recorded/next/later timeline, startup snapshot capture-time forecasts, and live recovery preserving selected key/expanded Info/Elevation passed.
- Physical iPhone/Safari and predictive field accuracy are not claimed.
