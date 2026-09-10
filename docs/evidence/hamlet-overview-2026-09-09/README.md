# Hamlet overview browser evidence

Issue #203, slice 1 of epic #201. All residents here are synthetic; the harness mounts
Hearth's actual Hamlet component, styles, Three renderer and art kit in a local Vite
page. It does not contact a backend, open a data store or execute a runtime.

Reproduce from the checkout with Node >=22.13 and the pinned frontend dependencies:

```sh
cd web
pnpm install --frozen-lockfile
cd ..
PLAYWRIGHT_MODULE=/absolute/path/to/playwright/index.mjs node scripts/check-hamlet-browser.mjs
```

Install Playwright and its Chromium browser in a separate tooling directory if they
are not already available (`npm install playwright`, then `npx playwright install chromium`).
`PLAYWRIGHT_MODULE` may be omitted when `playwright` resolves from the script's directory.
The script owns its loopback Vite server (port 5193) and headless browser and closes both.

`results.json` records the Chromium version, actual orthographic-camera projections,
canvas sizes, renderer identity counts and passed journeys. The instrumentation observes
Three's real draw hook; it does not substitute a renderer or WebGL implementation.

- Desktop (1440 × 1050) and phone (390 × 844), empty/4/24-resident framing and resizing;
  every world-bound corner fits inside the camera's viewport margin.
- Keyboard-activated zoom/rotation; Overview; wheel zoom; pointer drag out and back
  without selecting, followed by a real home click.
- CDP touch pan and pinch/rotation, without selecting a building.
- Repeated snapshot/disconnect and roster updates retain the same camera and renderer.
- Reduced-motion camera position is stable after an immediate control action.
- React unmount calls renderer disposal once, removes the canvas and stops actual draws.
- Real WebGL context loss and forced initial context failure leave record links available.

Screenshots retain the miniature art, visible facades, fitted settlement and camera
controls. Labels, stable plots, interiors and reconnect/activity acceptance belong to
later slices. This is component-level Chromium evidence, not a real-device, runtime,
production rollout or daily-observation gate.
