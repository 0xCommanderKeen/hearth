# Resident portraits — 2026-09-13

Synthetic Chromium/SwiftShader checks render twelve distinct portraits from the
same art kit used by village and room figures. Screenshots show the gallery,
resident selection panel and 320px layout. The harness verifies identical images
across placements, stability after renaming/activity changes, and initials plus
navigation when WebGL cannot initialize. No real resident records or runtime are used.

`portraits.test.ts` checks batch deduplication, cancellation before render, cache
reuse/eviction at 128 identities, and disposal/context release after every batch.

Run `node scripts/check-hamlet-portraits-browser.mjs` with `PLAYWRIGHT_MODULE` and
`CHROMIUM_BINARY` set if needed. For interactive review, start
`node scripts/preview-hamlet.mjs`, visit `http://127.0.0.1:5197/__hamlet-preview`,
and select **Character portraits**. **Show shared workrooms** shows the same models
at their desks. These checks do not establish physical-phone GPU performance.
