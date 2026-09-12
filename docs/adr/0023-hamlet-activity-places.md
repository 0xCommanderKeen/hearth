# 0023 — Hamlet places represent recorded work

Accepted 2026-09-12 after the operator requested a Workshop, Research House and
Post Office, residents moving to buildings for their actions, and idle/ready
residents staying inside their homes.

## Decision

Hamlet remains an observation view. It does not introduce physical locations,
resident intentions, new runtime tools, task classification or scheduling authority.

The snapshot adds an optional, body-free `action` to each retained run. Observation
projects the latest native tool receipt from existing audit facts in the same read
transaction. A finite map of exact Hearth tool names supplies a place and readable
label. No task text, skill title, provider transcript, arguments or result bodies
are inspected. Refused and unrecognized latest actions have no place-specific
projection; a previous action is not silently substituted. A communications request
is described as requested/queued, never as confirmed delivery. The projection
changes neither the store schema nor its audit cursor.

Only a resident with recorded running presence and a matching running,
non-cancelling run is placed at a work building. Recorded reads map to the Research
House; authoring and general running work to the Workshop; correspondence to the
Post Office; resident/work administration to Townhall. A place describes the running
work and its last recorded action, not proof of what the model is doing now. Idle,
ready and paused residents are represented at home. Uncertain, starting, stopping,
missing or disconnected evidence is not used to invent useful movement. Panels
retain the recorded execution status and label disconnected information as last known.

Residents appear outdoors only during finite transitions between these places.
Initial, reset, reconnect, hidden and reduced-motion observations place them indoors
without replay. A changed destination invalidates an obsolete journey. At most 12
resident journeys wait, with at most three total moving figures; overflow is
consumed without animation. Letter journeys retain their separate bounded history
consumer. They use distinct envelope-carrying postal couriers through the Post Office,
so a letter never creates another copy of its sender. Animations are illustrations,
not a complete history. Skipped events remain available in records.

The three civic identities use invalid-for-residents `@` prefixes and reserve unused
slots through the existing browser-local plot allocator (ADR 0017). Existing homes
keep their slots. Civic plots are included in the same street network and fitting
bounds. Directory, accessible building panels and owning record links remain usable
when graphics are unavailable. Home color and decoration derive from identity and
are shared by the character and interior; they grant no capability or role.

Shared work buildings have enterable cutaway interiors. The same membership rule
as the exterior panel supplies named residents and their recorded run/task details.
Six work spots are rendered per page; further residents remain accessible by paging.
Resident, desk and name selection inspect that resident rather than navigating away.
Small working motions illustrate recorded running work, stop while disconnected,
hidden or under reduced motion, and never create audit facts. Live membership changes
update figures without recreating the room. Textual residents and owning record links
remain available after graphics failure. Furniture varies by building function.

## Verification

Use real temporary SQLite and the native bridge to verify successful/refused calls,
replay, concurrent replay, window independence and absence of snapshot writes. Browser
checks use synthetic fixtures outside the shipped application to verify placement,
finite walks, post routes, keyboard search, room appearance, reconnect/reduced motion
and desktop/mobile views. They do not complete real-runtime or physical-phone gates.
