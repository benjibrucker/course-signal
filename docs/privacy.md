# Course Signal — Privacy and data boundary

## Public provider data
Course Signal reads public Competitive Timing endpoints through its own fixed-host, read-only adapter. A public upstream response is not automatically safe to forward: catalog and result payloads are minimized before any response leaves the backend.

## Catalog output
Only the fields needed to choose a race/event may leave the backend: provider identifiers, race/event labels, date/year, location/timezone, race type, status, distance, and public capability signals. Registration, storage, organizer, custom-field, and other configuration values are dropped.

## Runner search output
Search returns only the public result identifier, display name (or `Anonymous Participant`), bib when public, official status, finish time, and finish place. It does not return email, phone, date of birth, age, gender/sex, address/location, question responses, custom fields, or source configuration. Free-text `q` values are request-scoped only: they are never stored, included in share URLs, or written to local access logs; the local handler logs only the Results route path and status.

## Runner analysis output
The detailed endpoint emits only the selected public runner plus aggregate field comparisons. Other runners’ identities and individual split histories are never included in the selected runner’s response. Anonymous evidence from any matched source wins over named evidence.

## Raw result handling
Raw provider result bodies may contain private-shaped fields. They are processed only in backend memory, never written to this repository, a static build, a browser cache artifact, an application log, or a committed participant snapshot. Error responses use generic messages and never include upstream bodies or exception text.

## Total weight and calories
`Total weight (including gear)` is transient client-side state. The value:

- is not sent in a request;
- is not added to a URL or share link;
- is not stored in cookies, localStorage, sessionStorage, IndexedDB, or server state;
- is cleared by a page refresh;
- is multiplied locally against sanitized per-kilogram active-energy factors.

Course Signal does not collect age, sex/gender, or height for the approved active-running calculation.

## Share links
Share links may contain only `race`, `event`, `mode`, and `runner` public identifiers. Free-text searches, name, bib, weight, and calorie output are excluded. Unknown or duplicate query fields fail the privacy gate.

## Operational boundary
The application is read-only. It has no account system, form submission, participant editing, social posting, purchase, or provider write action.
