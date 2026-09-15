# Course Signal — Active-running energy model

## What the number means
Course Signal estimates **active transport energy** for the mapped race distance. It excludes resting metabolism, recovery, and calories outside recorded race sections. It is an engineering estimate, not a wearable reading, medical measurement, or confidence interval.

## Scientific basis
The route calculation starts from the grade-dependent running-cost polynomial reported by Minetti et al. The study measured ten elite male mountain runners on a treadmill over grades from −0.45 to +0.45; reported level running cost was 3.40 ± 0.24 J·kg⁻¹·m⁻¹, while the fitted polynomial used by the project has a 3.6 J·kg⁻¹·m⁻¹ level intercept.[1][2]

For fractional grade `g`, the fitted cost is:

```text
C(g) = 155.4g⁵ − 30.4g⁴ − 43.3g³ + 46.3g² + 19.5g + 3.6
```

The paper also reports that downhill speed estimates were substantially faster than observed downhill competition speeds, so Course Signal does not treat this metabolic relationship as a technical-trail speed predictor.[1][2]

## Course Signal processing
1. Use only provider course points with finite, verified meter elevation and valid route order.
2. Reject gaps over 500 m, routes shorter than 100 m, and incomplete or mismatched event geometry.
3. Resample the route every 50 m and smooth elevation over a centered 150 m distance window.
4. Compute each sample grade from smoothed rise over horizontal distance and constrain it to the study domain, −0.45 through +0.45.
5. Apply an engineering cost-ratio floor/cap of 1× through 6× flat cost. This bound is a Course Signal safeguard, not a result from the paper.
6. Integrate cost over each official adjacent section only when strict split-to-course progress is available. Missing checkpoints remain missing; no equal spacing is invented.
7. Convert the integrated value to kilocalories per kilogram using 3.6 J·kg⁻¹·m⁻¹ and 4,184 J/kcal.
8. In the browser, multiply the returned per-kilogram factors by `Total weight (including gear)`.

## Displayed range
The midpoint receives a ±25% **communication band**. That band is a transparent Course Signal product choice; it is not a statistical confidence interval from Minetti et al.

The range acknowledges unobserved differences such as individual running economy, technical terrain, walking transitions, weather, stops, and source elevation error. It does not make those variables measured.

## Privacy boundary
The backend returns only per-kilogram factors. Total weight is entered and multiplied in the page, is not sent or saved, and is cleared on refresh. Age, sex/gender, and height are not collected because they are not inputs to this active transport calculation; a separate resting-metabolism model would be a different feature and is explicitly excluded.

## Unsupported cases
Course Signal shows energy as unavailable when any required evidence is missing or invalid: running eligibility, event-matched route, complete elevation, strict checkpoint progress, or a recorded adjacent section. A Start/Finish-only event without a mapped route may still show official finish results, but it does not receive invented distance, grade, or calories.

## Sources

[1] https://doi.org/10.1152/japplphysiol.01177.2001 — Energy cost of walking and running at extreme uphill and downhill slopes
[2] https://iris.unibs.it/bitstream/11379/540545/1/Minetti%20JAP%202002.pdf — Minetti et al. full paper PDF
