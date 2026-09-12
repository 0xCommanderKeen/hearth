# Village miniature models

`art.js` reuses the original code-built miniature art kit from the public
[Warren repository](https://github.com/0xCommanderKeen/warren/blob/main/arcadia/src/world/art.js).
Source Git blob: `fa65df360f7faef9358a48475cfa8cad187379d2`, verified against the
public GitHub contents API on 2026-09-06. Hearth applies its own formatting.

Only the visual model kit is reused. Hearth's renderer and operational state are
separate; no Warren residents, credentials or runtime configuration are copied.

`layout.ts` owns the street rectangles and routes through their centres. `activity.ts`
consumes snapshot events before rendering: initial/connection/reset baselines do not
travel, and hidden or reduced-motion events are consumed in place. The queue holds
at most 12 waiting journeys and the renderer shows at most three for 4.2 seconds each.
A timestamp watermark and at most 256 keys in that second prevent overflow or older
retained events replaying. A saturated second, older out-of-order events and missing
endpoints stay text-only; animation is deliberately not a complete audit stream.

`Client.watch` associates every snapshot with its connection/reset baseline through
an ephemeral WeakMap. This survives React batching without putting observation
metadata in the wire schema, local storage or operational records. Recent post keeps
recorded event times and the current connection context regardless of travel.

Run `node scripts/check-hamlet-activity-browser.mjs` from the repo with Playwright
available (or `PLAYWRIGHT_MODULE` set to its module path) for synthetic full-App/WebGL
checks. Evidence goes to `docs/evidence/hamlet-activity-2026-09-09/`.

## Activity places and visual preview

`places.ts` maps a resident's recorded running state and optional run action to the
Workshop, Research House, Post Office or Townhall. Idle and ready residents are
inside their homes. Starting work is represented as preparation at home until a
running observation arrives. Unknown/stopping work does not create a journey.
The observation backend supplies only the latest recorded tool's place, label,
time and audit sequence; task text never chooses a building. See ADR 0023.

Civic buildings reserve three unused ordinary plots through the existing allocator;
existing homes do not move. The renderer shows residents only during bounded
transitions, and uses separate envelope-carrying postal couriers for letters.
Letter routes go through the Post Office. Both kinds of journey share the
three-figure rendering bound; resident transitions have a separate twelve-entry
queue. Snapshot baselines, reduced motion and hidden views consume changes indoors.

`visualIdentity` coordinates a resident's accent color, character silhouette,
independent roof color and decorative motif across exterior and interior.

Run `node scripts/preview-hamlet.mjs` from the repository, then visit
`http://127.0.0.1:5197/__hamlet-preview`. **Play activity** walks a fictional resident
through each work building and home, with a separate letter delivery. The fixture
lives outside the shipped application and uses no backend, account or live source.

Run `node scripts/check-hamlet-living-browser.mjs` with Playwright available (or
`PLAYWRIGHT_MODULE` set to its ESM entry). `CHROMIUM_BINARY` optionally selects an
installed Chromium executable. This harness uses software WebGL and writes to
`docs/evidence/hamlet-living-2026-09-12/` or `HAMLET_EVIDENCE_DIR`.
