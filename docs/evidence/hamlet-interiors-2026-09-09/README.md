# Hamlet interiors — synthetic browser evidence

Issue #206, epic #201. Run from the repository root with Node 22 and locked web
dependencies installed:

```sh
PLAYWRIGHT_MODULE=/absolute/path/to/playwright/index.mjs node scripts/check-hamlet-interiors-browser.mjs
```

The script serves the full App at loopback port 5196 with synthetic fetch responses
through the real Client/watch. Chromium renders actual WebGL. Furniture is selected
by projecting reachable mesh centers, then sending real pointer clicks to the canvas;
the script asserts the resulting record route and returns through the App link.
No fixture method bypasses the scene's picking handler.

`results.json` records the checked journeys. Desktop (1440 × 1050) and phone
(390 × 844) screenshots show both furnished rooms. Archive history and actual
`WEBGL_lose_context` loss retain accessible records and return. A fresh page refusing
WebGL creation verifies both room types still work through their record controls.
Recorded status changes, rename, disconnect/reconnect and record routes preserve
room identity; exiting retains the original exterior renderer and restores its camera.
Townhall retains unknown-usage money and unresolved archived work even with zero
active runs. Separate standards and spec reviews are clear after correcting that
accounting omission and naming the furniture-to-record targets. The desk focuses the existing resident Tasks & results section via `panel=work`.

`make web` passed formatting, 158 frontend tests, TypeScript, production build and
packaged assets. Expected console diagnostics occur when the failure fixture refuses
WebGL. The existing Three shadow deprecation and bundle-size warning remain.

This is synthetic UI evidence only. No runtime, credentials, personal sources,
Warren, NAS or deployment was used. No real-host, daily-observation or native
accessibility gate is completed.
