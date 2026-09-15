# Hidden map selection — frontend1.9.1

## Reproduction
Published1.9.0: expand Info on phone with Map selected, then select a located runner. Leaflet reports0×0 map size and flyTo raises `Invalid LatLng object: (NaN, NaN)`. The selection key changes but camera update fails. Captured in isolated public browser; no upstream write.

## Approved fix contract
Update the hidden map target without animation; preserve newest selected target/zoom for reveal, manual/Auto/follow, red arrow and checkpoint timeline. Visible Map retains animation; Elevation remains nonanimated. No backend or estimator change.

## Verification
- RED: five expected zero-size crash failures and two controls passed before the fix. GREEN: seven regressions passed; full133Node/159Python and syntax/diff checks pass.
- Independent precommit review PASS: no security/logic concerns or suggestions; actual selection/reveal implementation plus focused/timeline tests checked.
- Local actual pointer search with Info expanded passes: hidden target update, manual/follow and next ETA intact. Nine browser checks cover repeated Map/Elevation selections, hidden Auto, newest-target reveal (≤1px Leaflet precision), one selected red arrow, no overflow, no captured JS errors. Visible-map actual search click completes animation to the target with0px error; timeline opens with recorded and forecast rows. The first programmatic-only flight probe was background-frame throttled; actual input/capture verified completion. Hosted replay completed below.
- Published67396fc20282c4ed9199ffd1e17e627e7e3dfdc1; Pages34715300430 succeeded. HTML/app/rules/CSS bytes equal tested source. Public pointer search while Info expanded passed at0px target error, no JSerrors; subsequent hidden selections, Map/Elevation reveal, Auto, arrow transfer and recorded square passed. Public four recorded/two forecast rows and next highlight checked.
- Public startup snapshot under blocked API displayed dated capture-time remaining; real refreshLive recovered live without changing selected key, expanded Info or Elevation view. Mid-session API failure holds/degrades the existing live payload rather than switching to a snapshot; initial QA incorrectly expected startup behavior during an ordinary refresh.
- Browser selectors verified: runnerNextArrival, mapCanvas, .recorded-pin and __rutApp.refreshLive (no refreshButton). Raw probe-selector failures were test-harness errors, not product regressions.
- Physical iPhone/Safari not tested.
