# Hamlet contextual panels

Synthetic browser data only. No resident runtime, personal source, deployment or
real-host acceptance was involved.

`node scripts/check-hamlet-panels-browser.mjs` runs the full App with synthetic
fetch responses through its real Client/watch interfaces and actual Three WebGL.
It verifies building focus, exact camera/focus restoration, on-demand results
outside the task window, record return with one retained renderer, stopped draws
while hidden, archive while selected, disconnect/reconnect, result-load failure,
journal navigation, Townhall, and phone sheet scrolling. `results.json` names the
checks and Chromium version. Screenshots were visually inspected.

`node scripts/check-hamlet-browser.mjs` reruns the earlier camera, gestures,
placement, label/directory, 0–100 resident, reduced-motion, context-loss and disposal
journeys, with fresh evidence under `regressions/`. Original plot evidence is kept.
Set `PLAYWRIGHT_MODULE` to the installed Playwright ESM entry when it is not locally
installed. Both scripts start a temporary loopback Vite server and close it.

This slice adds no interiors or activity interpretation. Latest available results
come from dated snapshot records, which are bounded and prioritize active and
unresolved work; absence is not a complete historical claim. Camera is held only
in the mounted session, never persisted. Phone captures include the page above the
scrolled viewport; the sheet remains fixed at the viewport bottom.

Final verification: `make web` passed formatting, 153 tests, TypeScript, production
build and packaged assets. The existing Three shadow deprecation and bundle-size
advisory remain non-failing. Independent Standards review found no violations;
Spec review's two focus/Escape findings were fixed, regression-tested and re-reviewed
with no remaining findings. No backend source changed, so `make check` was not run.
