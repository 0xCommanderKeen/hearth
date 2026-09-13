# Noticeboard and working poses — 2026-09-13

Synthetic Chromium/SwiftShader checks cover the completed-run link, unknown task
outcome, retained letter and 320px noticeboard layout. All four interiors have
distinct static arm poses and held props. Browser checks also verify mesh selection,
paging, live departures, motion, reduced-motion/disconnected pauses and graphics loss.
Screenshots and `checks.json` record the results. No real residents or runtime are used;
this does not establish physical-phone GPU or real-runtime acceptance.

Component tests cover current task status versus historical failed attempts, archived
unresolved holds, runs whose tasks are outside the snapshot, bounded letter sorting,
Townhall links, disconnection and replacement of old records.

Reproduce with `node scripts/check-hamlet-noticeboard-browser.mjs`, setting
`PLAYWRIGHT_MODULE` and `CHROMIUM_BINARY` when needed. Interactive preview:
`node scripts/preview-hamlet.mjs`, then **Noticeboard demo** at
`http://127.0.0.1:5197/__hamlet-preview`.
