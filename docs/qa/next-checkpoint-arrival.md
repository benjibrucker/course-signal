# Next-checkpoint arrival — frontend 1.7.0 QA

Base: `5db5106092953d11311745a86e08e7b2b55499ab` (published phone split).

## Approved scope

Each selected runner Info card gains the next timing-checkpoint name, estimated Mountain arrival and approximate time remaining. Use the existing server `next_checkpoint_at`, not a new forecast formula. API 1.5.0, terrain model, route data, ranking and recorded passage history remain unchanged. Publish to the existing GitHub Pages site after review.

## Evidence collected locally

- Baseline: 64 Node / 141 Python passed.
- New suite: 12 expected RED failures, then full 77 Node / 141 Python passed. Syntax and diff whitespace passed. Added-line scan: no credential, shell-injection, eval/exec or pickle findings.
- Fixed deterministic-clock test boundary by passing `Date.now()` explicitly from the renderer; updated existing version assertion to1.7.0.
- Static build: five events, zero source errors. Production API1.5.0 already supplies next-checkpoint predictions; no backend deployment necessary.
- Real local feed supported arrival display, not just synthetic data. Full roster presentation rules checked all4238entries at observation:561estimates,167awaiting,3510unavailable, with no unsupported countdowns/errors. These are observation-specific aggregate counts.
- Browser viewport checks:390×675,320×568,844×390,1280×800,1200×500, both Map/Elevation. No horizontal overflow. Phone cards remain in Info in both views. Existing desktop rules continue hiding the runner card in Elevation/short-height layouts; no desktop layout redesign is part of this slice.
- Expanded390×675 Info visually inspected: checkpoint, arrival, remaining and estimate disclaimer readable; original stats/source explanation retained. Info scroll is required for lower card content in the default split.
- Real pointer search replaces the previous arrival with an unavailable state for a non-active runner; selection/manual lock, Elevation view and stats-reveal scroll behavior retained. Checkpoint times opens and still contains recorded passage rows only, not this arrival estimate.

## Independent review

Initial review identified one integration gap: GPS fields in a snapshot were aged as current live data before reaching the ETA card, preventing the documented capture-time presentation. Targeted RED-first fix completed: ETA alone uses the immutable selected capture row, GPS eligibility at capture, and current GPS badge remains aged. Final independent review APPROVED:80Node/141Python plus21integration cases including actual mergePayload/aging/card flow. No security concerns reported.

## Release gate

Final80Node/141Python, syntax/diff/static build passed. Isolated browser fixture (prominently SYNTHETIC QA ONLY) verifies stale-GPS current badge alongside a labeled capture-time estimate, then transitions to Awaiting checkpoint read without a negative countdown. Public verification complete: runtime58803f4de2e0ac25c9606cdcc8456eff2ea23158, Pages34705140063 success. Hosted HTML/app/rules/CSS bytes match source. Real supported arrival card visually verified in expanded phone Info;320×568,390×675 and1280×800 checked in both views without horizontal overflow. API-blocked startup shows Snapshot pace estimate/not live and remaining at capture; unblocking recovers live, retaining Info expansion and Elevation selection.

Strict camera-coordinate equality diagnostic failed on fractional-pixel rounding during one phone pane switch. Read-only comparison reproduced a1pixel Display-loop shift on the already-published1.6.0 baseline; controlled final1.7.0 loops retained pixel center and zoom. No workspace/camera code was modified for this feature, and exact subpixel preservation is not claimed. No physical iPhone/Safari claim. All arrival predictions remain unvalidated pilot estimates, not safety guidance or confirmed passages.
