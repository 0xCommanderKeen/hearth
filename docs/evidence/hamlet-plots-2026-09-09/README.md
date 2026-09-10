# Hamlet plots browser evidence — 2026-09-09

Run `scripts/check-hamlet-browser.mjs` with Node 22 and an installed Playwright
module (`PLAYWRIGHT_MODULE` may point to its ESM entry). This serves the actual
Hamlet React component and Three.js scene through a temporary Vite server, with
synthetic residents only. `results.json` records Chromium version and assertions;
PNGs show rendered empty, five, 25 and 100-home villages at desktop/phone widths.

The journey observes actual model coordinates across roster arrival, reorder,
archive and reload, rejects corrupt local preferences, switches snapshot epochs,
checks shared label/directory selection, archive holds and history access, and
preserves the existing camera, touch, drag, keyboard, reduced-motion and WebGL
failure/disposal checks. Dense labels are hidden on collision; the complete
scrollable directory remains available. Camera controls reveal closer detail.

Manual visual inspection: desktop five-home tags identify every building, streets
meet each door and the central lane; phone 100-home tags do not overlap or overflow.
The existing profile-link list remains beneath the selection directory for direct
record access and graphics fallback. Consolidation with contextual panels belongs
to later epic slices. This is component-level synthetic browser evidence, not an
integrated operational journey or real-host/daily-observation acceptance.
