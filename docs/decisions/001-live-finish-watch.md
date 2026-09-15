# Decision 001: independent live feed, stable GitHub presentation

Status: accepted, deployed and browser-verified. Public Pages frontend consumes the independently hosted managed API.

## Context
The original GitHub Pages build publishes a five-minute snapshot. The user now requires a near-real-time, selected-race view of up to ten unfinished runners closest to their finish. A snapshot scheduler cannot satisfy the latency goal. Cross-origin browser access to the timing source was rejected in prior tests.

## Decision
Keep GitHub Pages as the public frontend. Add a read-only Python API on managed hosting, reusing the existing normalization/estimation module. Candidate: Vercel personal Hobby plan; verify actual account/plan and do not upgrade or incur fees without consent. GPS source cache is 15 seconds, timing source cache 30 seconds; client requests every 15 seconds, with bounded short edge caching. This is a refresh target, not a guarantee that participants have new observations.

Rank only within the selected course, by remaining route distance rather than straight-line proximity or cross-course percentage. Surface GPS vs estimate and observation age. Exclude finished, held, stale, unmatched and otherwise indefensible positions from confident top-ten ranking; never pad the list. A stale source pauses finish-watch ranking rather than inventing updates.

## Compatibility and fallback
Retain manual selection, auto director, search and local launcher. Static snapshots remain a clearly dated fallback when a managed API is not reachable. They do not acquire a new freshness timestamp merely by being downloaded again. Pipeline failures must not replace good data with empty fresh data.

## Privacy
Honor anonymity from either input source. Publish only necessary race fields. Never store location snapshots in Git or expose credentials in frontend configuration or build logs.

## Rejected alternatives
- Faster GitHub scheduled rebuilds: not a suitable seconds-level live service.
- A Mac-hosted tunnel: depends on laptop/network uptime, contrary to the selected approach.
- Calling raw upstream endpoints from the browser: currently fails origin checks and bypasses the data-minimization boundary.

## Verification gates
Unit tests for distance/ranking/freshness/privacy; real HTTP adapter tests; desktop/mobile and failure-recovery browser tests; verified hosted health and data freshness; deployed GitHub frontend reads the exact managed endpoint successfully. Do not mark live hosting complete based only on a user authorization message.
