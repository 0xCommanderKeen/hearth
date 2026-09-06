# Supervisor ownership and shutdown

The API lifespan starts a dedicated supervisor thread only after acquiring a
nonblocking file lock beside the canonical database path. A second supervising API
process fails startup before its background work begins. Path aliases resolve to
the same lock. Restored copies cannot acquire supervision and remain read-only.

The worker owns routine ticks, queued routine admission, runtime supervision and
mock notification delivery. Errors expose their class through authenticated health,
not their private text. Notification errors remain separate from execution errors.
Existing per-operation locks and durable run ownership tokens remain necessary for
standalone mock operations, backup coordination, and stale-evidence rejection.

Shutdown sets a stop event, prevents later stages/passes from beginning, and joins
the worker. The worker itself releases the ownership lock after in-flight work
finishes. Cancelling the API's asynchronous wait does not free that lock. A stuck
adapter keeps supervision visibly stopping; there is no timeout that silently
permits a replacement to overlap it. Process termination and runtime evidence
reconciliation remain distinct recovery operations.

Tests use actual threads, an independent Python process, canonical path aliases,
blocked-operation barriers, API startup/shutdown, fatal worker exit and restart.
This proves local mock process coordination. It does not prove real runtime
termination or host isolation.
