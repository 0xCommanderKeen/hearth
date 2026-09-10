# Activity snapshot transport

`GET /api/state` and `/api/events` project the same recent state. At most 100 tasks
are included, preserving the existing priority for active work and unresolved
usage. Each task carries the first 240 Unicode code points of `instruction` and
an explicit `instruction_truncated` boolean. This is a display preview; execution
still reads the complete, immutable task in SQLite.

Townhall marks truncated previews with an ellipsis and offers **Read full
instructions**. The authenticated, no-store `GET /api/tasks/{task_id}` returns the
complete task, including tasks outside the snapshot window. Hamlet marks the
same previews and links to Townhall's resident work view. Instructions are loaded
only on demand; opening one does not enlarge subsequent snapshots. HTTP request
body limits and instruction acceptance rules are unchanged.

Each SSE event is an LF-delimited `event: snapshot` or `event: reset` followed by
one JSON `data:` line and a blank line. JSON is UTF-8 without ASCII escaping;
embedded newlines remain JSON escapes. The client retains a streaming UTF-8 decoder
across network reads. The limit is 2,000,000 decoded JavaScript UTF-16 code units
per frame, excluding the final blank-line separator, not per network chunk.
Complete frames are processed separately before applying the same limit to the
unfinished remainder. Oversized complete or unfinished frames cancel the reader
and reconnect. A network chunk containing several individually valid frames can
exceed that limit. This is a frame safety bound, not a total household capacity
claim for every other projection field.

Reconnect starts from a fresh HTTP snapshot and uses its epoch, audit cursor and
budget revision for the stream. Reset and budget-only changes retain their existing
semantics. No transport read changes audit history or runtime authority.

`tests/api/test_large_snapshots.py` exercises 100 accepted 32,000-character ASCII
and Unicode tasks through real temporary SQLite, previews, protected full reads
and history outside the snapshot window. `web/src/shared/watch.test.ts` covers
combined frames, oversized frames and bytewise Unicode/separator fragmentation.
`scripts/check-large-snapshots-browser.mjs` joins the real server serializer,
browser `Client.watch`, and full-instruction component in Chromium with temporary
SQLite. It deliberately fragments real SSE bytes, disconnects once, and verifies
successive updates before and after reconnect. The fixture injects the test
runtime and never calls a provider.
