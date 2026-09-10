# Large task activity stream regression

Measured with Chromium 151.0.7922.34 using
`scripts/check-large-snapshots-browser.mjs`; retained observations are in
[results.json](results.json). The fixture uses real temporary SQLite, production
HTTP/SSE routes, browser `Client.watch` and the full-instruction component, with
synthetic tasks and an injected fake runtime. No provider was called.

- 65 accepted 32,000-character ASCII tasks received two successive live updates.
- Replacing the recent window with 100 accepted 32,000-code-point Unicode tasks
  received another live update. Both ASCII and Unicode full-content reads matched
  the original instructions exactly and occurred only on demand.
- Real SSE bytes were fragmented into 12,359 chunks, including single-byte pieces.
- One deliberate connection failure led to one refetch/reconnect, then two more
  live updates. The connection observations were `[true, false, true]`, with exactly
  two SSE requests. No browser page errors occurred.

The parser suite separately verifies a 2.7-million-character network chunk with
multiple valid frames, rejection of oversized complete/incomplete frames, and an
exactly-at-limit frame whose blank-line terminator is split. This synthetic browser
regression does not complete real-host, deployment or daily-observation gates.
