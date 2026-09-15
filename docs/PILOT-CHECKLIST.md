# Saturday 28K terrain-pace pilot

Pilot date: **2026-09-12**, Mountain Time. Saturday 28K waves are scheduled 7:20–8:30 a.m.; a schedule alone never starts predictions.

Public map: https://benjibrucker.github.io/rut-live-map/?v=1.3.0
API: https://rut-live-api.vercel.app

## What to check
1. Open the map before the race. It should preview the 28K, show the terrain-pilot message, and have no fabricated live finish order.
2. Choose a runner by name/bib. Before a confirmed timed checkpoint beyond the start, do not expect a checkpoint-based moving dot. Fresh measured GPS may appear independently.
3. After a valid checkpoint, check the source label: **TERRAIN EST.** is a pilot estimate, not GPS. A single cumulative interval is explicitly limited history.
4. After additional check-ins, the card should show recent observed-segment smoothing. Missing or stale optional history falls back to the cumulative observed interval rather than inventing splits.
5. Use the next actual checkpoint as the reality check. A new chip read corrects the position and pace. If the expected next check-in is overdue, the estimate must stop before that checkpoint and leave Finish watch. Official finished/DNS/DNF/DQ status also excludes it.
6. Check a later-wave runner: the runner's chip offset must be included. Unknown offsets stay unknown. Fifteen-second polling is not a new fifteen-second observation.
7. If the basemap fails, tap **↻**. Course lines and the explicit tile-error notice should remain available.

## Evidence and limits
- Model is an engineering pilot. Elevation cannot reveal waiting, injury, withdrawal, trail conditions, weather, or every technical obstacle.
- No future field accuracy is claimed. Historical replay overall improved but some legs worsened; see `qa/terrain-28k-2025.json` and `qa/terrain-21k-2026.json`.
- Historical replay only reads prior checkpoints before predicting the withheld next one. It does not apply 2025 segment times to changed 2026 checkpoint geometry.
- Hosted API + Pages operate without the Mac. No morning monitor or notification job is scheduled by this release.
- Parent Chromium phone-size checks do not constitute verification on your actual iPhone/Safari.

## Maintainer checks
```bash
cd "/Users/benjibook/Documents/Rut Live Map"
python3 -m unittest discover -v
node --test test_app.cjs test_race_logic.cjs
python3 evaluate_terrain.py --event the-rut-28k-2026 --output /tmp/rut-28k-pilot-summary.json
```
The evaluation command needs qualifying completed runners and fails plainly when there is no usable sample; do not run it before finishers exist expecting a score.

## Reversible off switch
Set `TERRAIN_PILOT_ENABLED = False` in `server.py`, rerun tests, and redeploy the existing Vercel project. This retains the conservative earlier checkpoint-average model, measured GPS, source-age safeguards, and public hosting. Do not change account/billing settings or delete the API project.
