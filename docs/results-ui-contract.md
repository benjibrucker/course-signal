# Course Signal — Results UI Contract

## Product posture
Course Signal should feel like a clear race record, not a medical dashboard or coaching app. Recorded evidence comes first; derived interpretation is visibly secondary. Reuse the Rut product’s dark, high-contrast map language, but make the landing/results surfaces quieter and race-neutral.

## Route and privacy
- Race: `/?race=<slug>&event=<catalog-event-id>&mode=results`
- Runner: append only `runner=<public-result-id>`.
- Never put runner name, bib, total weight, calories, or free-text search in the route.
- Never send the total-weight value in any fetch, form submit, beacon, analytics event, log payload, or persisted server state.
- Keep weight transient in page memory; refresh clears it.

## Empty completed-results view
1. Race header: race series, selected event/distance, date, location when published.
2. Capability row: explicit badges for Results, Intermediate splits, Course map, Elevation, Live GPS.
3. Runner lookup: `Search by name or bib`, fetch after two normalized characters, bounded results, clear duplicate disambiguation.
4. Honest unavailable states:
   - No completed results.
   - Finish time only.
   - No official course map; map/grade/energy unavailable.
   - No intermediate splits; overall result only.

## Selected runner evidence hierarchy
1. Public identity: name or `Anonymous Participant`, bib only when public, official status.
2. Official headline facts: chip/gun total as published, overall place and field count when supported, last recorded checkpoint for incomplete records.
3. Recorded section table/cards: checkpoint pair, segment time, cumulative time, distance, pace, ascent/descent, net grade, segment place, cumulative place, rank change, and field comparison only where each value is supported.
4. Derived summary: reproducible strongest/weakest section and rank-movement facts; no coaching prescriptions.
5. Course map/elevation: route and section boundaries only when official geometry exists. Never invent positions for missing splits.
6. Energy card: model estimate after user enters `Total weight (including gear)`; pounds default with kilograms option.

## Energy card
- Initial: no calorie number and no default body value.
- Label: `Total weight (including gear)`.
- Helper: `Used only in this page. It is not sent or saved.`
- Output: active-calorie low–high range and section values calculated in client JavaScript from server-provided kcal/kg factors.
- Method disclosure: Minetti graded-running transport model; link to local methodology and DOI.
- Required caveat: running-only estimate from smoothed route grades; technical terrain, weather, walking, stops, and individual economy are not observed; range is a product uncertainty band, not a medical confidence interval.

## Summary rules
- Name the overall field as the comparison cohort.
- Strongest/weakest uses the documented valid-section field percentile rule.
- Rank gain is previous cumulative place minus current cumulative place; positive means positions gained.
- Exclude malformed/missing sections from comparisons and say how many valid sections were analyzed.
- Do not generate advice, diagnosis, training recommendations, or causal claims.

## Responsive layout
### Desktop (1440×900)
- Header and mode switch remain compact.
- Two-column top: result/summary at left, course visualization at right.
- Section evidence gets full-width table below.
- No required control or key label below an internal clipped panel.

### Phone (390×844)
- One column with compact result header and sticky-safe mode/back controls.
- Search and weight inputs at least 16px; primary touch targets at least 44px.
- Sections become cards or a horizontally scrollable semantic table with visible column labels.
- Map and elevation must not initialize while hidden at zero size.

## Accessibility and safety
- Native labels for every input; clear focus states; status/loading announcements via `aria-live`.
- Escaped upstream/user text before HTML insertion.
- No color-only capability or rank meaning.
- Null/empty numeric values stay unavailable; never coerce to zero.

## Browser acceptance paths
1. Landing → `The Rut` → `28K` → automatic Results → runner search → selected profile → add weight → calories update locally.
2. Direct Rut runner URL restores the same public runner profile without any weight value.
3. Landing → Beaverhead 55K → completed profile with map, elevation, intermediate splits, and energy.
4. Landing → Rock the Run 5K → result profile with Start/Finish only and explicit no-map/no-energy messaging.
5. Live/Results switch reloads safely; Results mode does not poll live data.
