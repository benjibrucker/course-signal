# Phone split workspace — verified release 1.6.0

Runtime commit: `1ec781d57a54b3f0d0218ff168914c42c7867b92`.
Pages release run: `34702579262` — success.
Public site: https://benjibrucker.github.io/rut-live-map/?v=1.6.0
Backend/API remains 1.5.0; no estimator, timing, ranking or data-contract changes.

## Approved behavior

Phone Info occupies the upper half and scrolls independently. Map/Elevation occupies the lower half without info cards covering it. Either pane can expand to fill the webpage; Back to split restores both. Browser chrome may remain. Selection, race, manual/Auto state, view and map camera are retained. Desktop keeps its established presentation.

## Verification

- Baseline 5f80f38: 141 Python / 53 Node passed. Final: 141 Python / 64 Node passed; syntax, whitespace, added-line security scans and static build passed.
- Independent spec PASS and final quality APPROVED. Deferred-camera tests and 200 extra reviewer probes verify that newer camera movement cannot be overwritten by queued layout work.
- Local pointer tests: 390×675, 320×568 and 844×390, both Map/Elevation and Info/Display expansion; center/zoom unchanged. Expanded Info and Elevation visually inspected.
- Additional 360×640, 390×450 and 844×390 checks: primary pane/view targets44px; no horizontal overflow. Reduced viewport is not actual software-keyboard testing.
- Search real roster through pointer input; 16px search field. Explicit search and Finish selections reveal selected stats, but live refresh retains the user's scroll. Finish selection preserves the primary view.
- Checkpoint history including Start and separate guide still work. Original guide image loads1606px; Escape closes the dialog before returning the expanded pane to split and restores the opener.
- Synthetic isolated anonymity refresh removes old identity from visible and hidden body markup while either pane is hidden. Fixture never published or persisted as participant data.
- Fourteen Tab steps never enter hidden Info from expanded Display. Desktop1280×800 baseline frame geometry matches exactly;1200×500 Elevation Finish watch is reachable after single-toggle reparenting.
- CDP metrics changes sometimes did not deliver resize; explicit resize events were used for deterministic breakpoint tests. Feature-checked matchMedia change handling is also covered by regression tests. This is not a physical-device orientation claim.

## Public readback

- Hosted HTML/CSS/JS bytes match final source. API health stays1.5.0.
- At390×675, each main pane is314.5px tall beneath the compact global header. Actual public expand/return clicks preserve camera, selection and manual state in both primary views.
- Public search reveals stats; checkpoint Start/history opens.320×568,844×390,1280×800 and1200×500 controls have no overlap/horizontal overflow or observed JS errors.
- All five profiles render; checkpoint counts8/6/5/3/3 match their source split-name counts.
- API-blocked startup labels dated snapshot fallback. Unblocking recovers live while retaining expanded Elevation.

## Corrections and limits

Review caught a short-desktop Finish toggle hidden by its new parent; the same node now returns to its original desktop toolbar. Review also caught delayed camera restoration overriding newer movement; pane sizing now invalidates/restores synchronously with deferred-callback regressions. Initial diagnostic errors were a nonexistent checkpoint CSS selector and accidental whole-map serialization; corrected probes passed.

Chromium phone-size/browser tests are not physical iPhone/Safari verification. The user should confirm comfort and browser chrome behavior on their device. Terrain positions remain an unvalidated estimation pilot.
