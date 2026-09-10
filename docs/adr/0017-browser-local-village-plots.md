# ADR 0017: Browser-local village plots

Accepted 2026-09-09 for #204, within epic #201.

Hamlet remembers only resident identity-to-plot allocations in browser localStorage.
This is a disposable visual preference, never operational authority or shared state.
The snapshot epoch identifies the store; restore creates a new epoch. A single
versioned key holds one epoch and at most 2048 identity/slot pairs, with a 256 KiB UTF-8
read/write limit, bounded strings and unique integer slots below 8192. Switching stores
replaces the preference instead of accumulating household identifiers. Names,
records, credentials, presence and camera state are never saved.

An allocator retains departed identities so surviving homes and returning residents
keep their places. The rendered bounds include current buildings only. Above the
cache bound the session still allocates normally but does not persist; continuity
then lasts for the mounted session. Invalid or unsupported data is ignored as a
whole. Denied reads/writes and quota failures leave an in-memory allocator working.
A reload without a usable preference assigns deterministic identity-sorted plots.

Townhall and the square have reserved fixed plots. New residents take the next
unoccupied plot on expanding square rings, with streets between plots. Reordering
or archiving cannot repack surviving homes. Archived history and unresolved holds
remain accessible independently of visual placement. No database or authority
change, and no real-host acceptance gate, follows from these preferences.
