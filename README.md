# Course Signal · The Rut 2026

Course Signal is a focused results recap for all five **The Rut 2026** distances, with the inherited 28K live-course view.

## Public version

- Page: `https://benjibrucker.github.io/course-signal/`
- API: `https://course-signal-api.vercel.app`
- Public repository: `https://github.com/benjibrucker/course-signal`

These targets are separate from the existing Rut Live Map and `rut-live-api` deployment.

## What it does

- Opens directly to the Rut 28K 2026 results recap.
- Incrementally searches the published 50K, 28K, 21K, 11K, and VK results together by name or bib.
- Routes a selected runner to that runner's event and course.
- Shows official finish facts and recorded split sections.
- Adds section pace, terrain context, field comparison, rank movement, and a descriptive summary only where the source evidence supports them.
- Keeps the inherited Rut 28K Live map behind the visible **Live** switch.
- Supports public direct-runner links containing only race, event, mode, and runner identifiers.

Other race series are intentionally deferred.

## Active race energy

Course Signal can estimate **active race energy only** from published course geometry and one browser-local **Total weight (including gear)** value.

- Weight may be entered in pounds or kilograms.
- Weight and personalized calorie output are never added to URLs, API requests, cookies, browser storage, analytics, or server data.
- Output is an engineering estimate range, not a wearable reading or medical measurement.
- Age, sex, gender, and height are not inputs.

See [`docs/energy-model.md`](docs/energy-model.md) and [`docs/privacy.md`](docs/privacy.md).

## Data and safety boundary

Course Signal reads public Competitive Timing data through fixed-host, read-only adapters. Completed results are minimized and anonymity evidence is unioned before search or analysis. Raw participant payloads are not written to the repository, published as static fixtures, or returned by the API.

This is a spectator visualization, not an emergency, medical, official timing, or course-safety system. Official race sources and race staff remain authoritative.

## Public routes

- Recap: `/course-signal/`
- Live: `/course-signal/?race=the-rut&event=the-rut-28k-2026&mode=live`
- Results: `/course-signal/?race=the-rut&event=<supported-event-id>&mode=results`
- Supported events: `the-rut-50k-2026`, `the-rut-28k-2026`, `the-rut-21k-2026`, `the-rut-11k-2026`, and `the-rut-vk-2026`.
- Runner: add `runner=<public-result-id>` to the matching event's Results route.

Every other race/event route fails closed.

## Run locally

1. Double-click `/Users/benjibook/Documents/Course Signal/Start Course Signal.command`.
2. Leave Terminal open.
3. Open `http://127.0.0.1:8765/` if the browser does not open.
4. Press **Control-C** to stop.

Manual start:

```bash
cd "/Users/benjibook/Documents/Course Signal"
python3 server.py --open
```

The local server binds only to `127.0.0.1`.

## Verification

```bash
cd "/Users/benjibook/Documents/Course Signal"
python3 -m unittest discover -q
node --test test_*.cjs
node --check app.js
node --check course_signal_shell.js
node --check results_model.js
node --check results_view.js
python3 -m py_compile server.py api/index.py competitive_catalog.py completed_results.py result_analysis.py
python3 build_static.py
```

## Main files

- `server.py` — localhost API, fixed-host source adapters, and static serving.
- `api/index.py` — separate Vercel adapter.
- `completed_results.py` — sanitized completed-result cache.
- `result_analysis.py` — pure runner search and split/field/terrain analysis.
- `course_signal_shell.js` — supported Rut event routing and Live/Results mode switch.
- `results_view.js` — completed Results interface and all-distance typeahead.
- `results_model.js` — browser-only weight conversion and energy scaling.
- `app.js` — inherited Live map and tracking interface.
- `build_static.py` — strict GitHub Pages artifact builder.
- `.github/workflows/deploy-pages.yml` — test/build/Pages deployment workflow.
