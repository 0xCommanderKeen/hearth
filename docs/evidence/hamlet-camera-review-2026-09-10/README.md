# Camera restoration review correction — 2026-09-10

Fresh review of epic #201 found that closing a Hamlet selection restored an overview's
old projection height even after the viewport became narrower. It also restored a
custom target outside the remaining village after residents disappeared.

Restoring an overview now recomputes its fit using the current bounds and aspect.
A custom camera preserves its orientation and zoom; its saved target is clamped only
when the current village no longer contains the saved bounds. Deliberately panning
outside an unchanged village still returns to the exact saved position.

## Verification

Four added camera regressions first reproduced the faulty behavior, then passed:
narrow overview restoration, changed-bounds overview restoration, custom restoration
after a shrink, and exact outside-pan restoration when bounds are unchanged. The
existing exact return test remains unchanged. All 15 camera tests and `make web`
passed: frozen lock, Prettier, **177 frontend tests**, TypeScript, production build and
packaged assets. No backend behavior changed.

The actual WebGL harness also passed the full integrated matrix and two added paths:

- Overview → select Reader → resize from 1440 to 320 CSS pixels → close: all village
  corners fit, with maximum absolute projected x 0.892857 and y 0.634536. The focused
  directory opener and visible village are retained in `restored-narrow-overview.png`.
- An actual Shift-drag pan in a 25-resident village → select → remove residents →
  close: the saved target moved from approximately `(26.849, 1.16, -24.686)` to
  `(5, 1.16, -11)`, inside the remaining bounds. Direction and 1.25 zoom stayed fixed;
  target error was below 1e-8. See `restored-shrunken-village.png` and the exact vectors
  in `results.json` → `cameraRestoration`.

The new run also retains all 16 population/width screenshots, keyboard/touch and
record-return journeys, graphics-failure paths, real background-tab checks and eight
resource-disposal cycles. Earlier 2026-09-09 evidence is unchanged. Both fresh review
axes independently cleared the final camera correction.

## Reproduce

```sh
make web
HAMLET_EVIDENCE_DIR="$PWD/docs/evidence/hamlet-camera-review-2026-09-10/" PLAYWRIGHT_MODULE=/path/to/playwright/index.mjs node scripts/check-hamlet-journeys-browser.mjs
```

The harness owns an isolated headed Chromium profile and loopback fixture. It checks
foreground visibility before measuring camera motion and fails with a bounded wait if
frames stop, rather than hanging when the browser is hidden. Its explicit background
tab test still uses real visibility transitions without focus emulation.

This rerun used Chromium 151.0.7922.34, WebGL 2 / ANGLE Metal Apple M3 Max, 36 GiB RAM;
exact host metadata and warm CPU submission/frame-interval summaries are in
`results.json`. Those timings are desktop observations, not direct GPU execution time
or physical-phone validation. No real runtime, deployment, native screen reader,
long-duration load or daily-observation gate is asserted. Epic #201 tracks merge status.
