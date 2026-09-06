# Commit operational state and its audit record together

Hearth starts as one backend application using one SQLite database on local storage.
Each operational change and its audit record share a transaction; external delivery
is queued in that transaction when needed. This avoids reconciling an operational database with separately authoritative
event history, at the cost of one host's availability and serialized writes. The database owns current state;
audit history explains it and is not required to rebuild it through event replay.

