# 004 — Selected-runner checkpoint forecast timeline

Status: user-approved implementation, not yet published.

## Decision

Show one selected-runner checkpoint timeline with recorded past crossings and clearly separate forecasts for all remaining timing checkpoints, including Finish. Promote the next checkpoint arrival/countdown and last-recorded→next segment in the Info card. Keep the existing course-direction arrow and recorded-checkpoint square distinct.

## Data and model contract

- Observed `checkpoint_passages` stay unchanged: `{split_index, elapsed_seconds, passed_at}`.
- New position `checkpoint_forecasts` contains only future `{split_index, estimated_at}` rows in original timing-index order. `checkpoint_forecast_basis` describes the existing model used.
- Terrain forecasts use the same current effort-normalized pace and last confirmed elapsed time as existing next/finish predictions. The fallback uses the existing remaining-segment weights and projected finish, not a new naive distance-speed model.
- The next forecast agrees with `next_checkpoint_at`; the final forecast agrees with supported `eta_at`. Actual event start plus verified runner chip offset defines wall clocks.
- Forecasts are not observations, location evidence, organizer schedules or statistical confidence intervals. Later forecasts are labeled less certain, without invented error ranges.
- Preserve the existing location model and next-checkpoint hold boundary in ADR003. Forecasting later clocks does not move the runner past the next checkpoint or assert passage.

## Fail-closed behavior

Suppress actionable future forecasts for stale/degraded/held/overdue data, invalid clocks/geometry/route support, before adequate recorded pace, terminal runners or closed/pre-start races. Once the next predicted arrival expires without a new timing read, show awaiting-read rather than sliding all clocks forward. Missing earlier recorded passages remain unknown even if a later checkpoint has a read.

Snapshots use the same selected runner's immutable capture row, explicit capture-time provenance, and existing expiry; current GPS aging cannot renew or mislabel observations. Recorded evidence remains independently available.

## Deployment and verification

Existing Vercel Hobby API and GitHub Pages only; no new services, dependencies or upstream writes. RED-first backend/frontend tests, independent spec and quality reviews, phone/desktop visual checks, source/privacy/snapshot expiry checks, then exact public API/assets readback. Keep only synthetic fixtures and aggregate results in Git.
