# Integrated Hamlet acceptance — 2026-09-09

Issue #208, final implementation slice of epic #201, on the six-PR stack. Synthetic
responses run the full App, real Client/watch and actual Three.js WebGL. No backend,
real resident, personal source, credential or deployment is involved.

## Reproduce

From the repository root with the pinned Node/pnpm and Python tooling available:

```sh
make web
PLAYWRIGHT_MODULE=/path/to/playwright/index.mjs node scripts/check-hamlet-journeys-browser.mjs
HAMLET_EVIDENCE_DIR="$PWD/docs/evidence/hamlet-journeys-2026-09-09/activity-regression/" PLAYWRIGHT_MODULE=/path/to/playwright/index.mjs node scripts/check-hamlet-activity-browser.mjs
```

Playwright and its Chromium must already be installed. The integrated harness starts
an isolated **headed** Chromium profile, serves only synthetic responses on loopback
port 5198, then removes its profile and closes its owned browser/server. It connects
with `noDefaults:true`: ordinary Playwright focus emulation otherwise makes both
same-window tabs report visible. This checks real tab visibility; it neither freezes
the page nor mocks `document.hidden`. The activity regression uses its separate
loopback fixture and writes here, preserving earlier slice evidence.

`results.json` records the matrix, timing summaries, exact browser/WebGL renderer and
checks. `activity-regression/results.json` retains the independent letter checks.
Screenshots named `village-COUNT-WIDTH.png` cover every matrix cell, alongside desktop
home/Townhall, phone rooms, touch/reduced-motion, archive and graphics-loss evidence.

## Acceptance paths

- **Overview → building → home → records → same overview:** actual exterior mesh
  picking, the three furniture routes, keyboard record navigation and return, selection
  and camera identity/position across updates and graphics-mode changes. Touch journeys
  at 320 and 390 CSS pixels use actual touch events, DPR 2 and reduced motion.
- **Townhall:** the household table, ledger and letter cabinet open tasks/results,
  allowances and inbox through existing routes. Unknown usage and archived unresolved
  work stay visible; archive history and return remain reachable.
- **Directory and layouts:** all 101 building entries at 100 residents are traversed
  by Tab/Enter; Escape restores each opener. Every combination of 0/5/25/100 residents
  and 320/390/768/1440 CSS pixels fits without horizontal document overflow. Captured
  terrain/tree pixels are asserted so projected labels alone cannot pass as a scene.
  Dense overview labels deliberately thin out; the scrollable directory keeps every
  identity available, and zoom/selection exposes the miniature in detail.
- **Continuity and truthful activity:** arrival/reorder/rename/archive and snapshot
  updates preserve spatial identity; disconnect/reconnect preserves room context.
  The activity regression covers bounded street journeys and no replay after initial,
  reset, refresh or reconnect baselines. Switching reduced motion during a journey
  clears both the courier geometry and its last displayed pixels.
- **Graphics failure:** real null WebGL initialization and `WEBGL_lose_context` loss
  in exterior and rooms leave directory, contextual details, records and return paths
  usable. A closed room disposes its geometry/materials, renderer, owned WebGL context
  and handlers. Eight room/records/view cycles preserve one exterior canvas, add only
  one room canvas, release every closed room context and restore the handler count.
- **Rendering lifecycle:** static exterior frames do not submit renders; hidden
  record views and genuinely backgrounded tabs stop exterior loops and room draws.
  Hidden-tab updates do not draw, foregrounding resumes. Resize draws immediately
  after sizing, before the backing buffer can be presented blank; this draw-only path
  does not advance letter activity.

The first five functional epic acceptance paths are verified on this stack. The epic's
“all six children merged and closed” requirement is still open: these are review PRs,
not merged delivery. No deployment, runtime isolation or daily-observation gate changes.

## Desktop measurements

Hardware: Mac15,10, Apple M3 Max, 36 GiB RAM, macOS 26.5.2 (25F84).
Chromium 151.0.7922.34 reports WebGL 2 and
`ANGLE (Apple, ANGLE Metal Renderer: Apple M3 Max, Unspecified Version)`.
This is the native desktop GPU run, not the earlier exploratory SwiftShader run.

At a 1440×1050 viewport and DPR 1, each population/mode performs 70 visible camera
rotations; the first ten samples are excluded. The remaining 60 samples measure CPU
submission time inside `renderer.render`, actual intervals between render starts, and
Three.js draw calls (including shadows). They are **not GPU execution timings**, and
include no idle/no-draw frames. Frame intervals include browser scheduling and label
projection/layout work; the roughly 16.7 ms intervals are display-paced observations,
not a claim that every device can sustain 60 fps.

| Residents | Mode    | CPU submission ms, median / p95 | Frame interval ms, median / p95 | Median draw calls |
| --------- | ------- | ------------------------------- | ------------------------------- | ----------------- |
| 0         | Normal  | 0.4 / 1.0                       | 16.7 / 17.7                     | 122               |
| 0         | Lighter | 0.3 / 0.8                       | 16.6 / 18.5                     | 64                |
| 5         | Normal  | 1.0 / 1.2                       | 16.6 / 18.6                     | 496               |
| 5         | Lighter | 0.7 / 1.4                       | 16.7 / 18.5                     | 255               |
| 25        | Normal  | 2.4 / 2.6                       | 16.7 / 17.7                     | 1824              |
| 25        | Lighter | 1.5 / 2.0                       | 16.7 / 18.2                     | 1018              |
| 100       | Normal  | 5.4 / 5.8                       | 16.7 / 17.6                     | 5012              |
| 100       | Lighter | 4.4 / 4.8                       | 16.7 / 17.7                     | 3872              |

Lighter graphics removes village shadows and caps pixel ratio at 1 (normal caps at 2),
without changing identity, plots, camera, furniture targets, records or authority.
At DPR 1 these measurements compare shadow cost; the touch journeys separately verify
the DPR 2 → 1 setting. The choice is reversible and session-only, with no new browser
storage. A 100-resident village still submits thousands of calls even in lighter mode.

## Checks and remaining limits

`make web` passed frozen-lock installation, Prettier, **173 frontend tests**, TypeScript,
production build and packaged assets. Both actual browser harnesses passed; independent
Standards and Spec reviews are recorded on the PR. No backend behavior changed, so a
local backend `make check` was not required; CI retains its full repository check.

This is one desktop hardware/browser combination and short synthetic runs. Viewport
and touch emulation do **not** validate a physical phone GPU, Safari/Firefox, battery
use, thermal behavior, prolonged memory pressure or a native screen reader. Initial
shader compilation and sustained high-rate updates are not characterized by the warm
rotation samples. Label projection and the unbatched miniature meshes remain scaling
costs. The existing production bundle-size warning remains. No deployment, real-host
runtime isolation, real work quality or daily-observation acceptance is asserted.
