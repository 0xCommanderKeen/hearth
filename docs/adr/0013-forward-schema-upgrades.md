# Hearth stores upgrade forward

Status: accepted, 2026-09-07.

Issue #44 (2026-09-06) removed the warren-to-hearth migration machinery and wrote the
rule as "do not add migration"; `Database.initialize` refused any store whose layout
differed from the current schema. That rule was aimed at foreign data but it also
covered Hearth's own releases: the memory epic (#116, #117, #119) changed four tables
and every existing store, including the live Karen instance, became unopenable. The
operator had to recreate residents by hand after each schema change. Miha reviewed the
rules on 2026-09-07 and decided the rule, not the code, was wrong.

**Decision.** Hearth never imports from other systems, and Hearth always opens its own
older stores. `SCHEMA_VERSION` is bumped whenever `SCHEMA` changes. On start, a store
with a lower version is rebuilt: a fresh file is created from the current schema, every
row of every current table is copied, columns the old layout lacked are filled from
`storage/migration.FILLS`, references and layout are verified, an audit fact records the
upgrade, and only then does the new file replace the old one. The original stays beside
it as `hearth.db.before-v<N>`. A file that is not a Hearth store, or one from a newer
release, is refused without being touched. A rebuild that would drop a column or has no
fill for a new required column fails before replacing anything.

**Amended 2026-09-08** (mock-removal slice #153). A release sometimes has to remove a
column or rename a stored value, and the blanket refusal made that impossible without
abandoning existing stores. A rebuild now drops a column only when the release named it
in `storage/migration.DROPS`, and rewrites a value the new layout no longer admits only
through an expression in `storage/migration.REWRITES`. Every other drop still fails
before replacing anything, so a column cannot be lost by an accidental edit to `SCHEMA`.

**Amended 2026-09-08** (mock-removal slice #155). Removing a feature removes tables,
not only columns, and renaming one is how a table earns a truthful name. A rebuild now
copies a table from the name it had when `storage/migration.RENAMES` says a later
version renamed it — the entry names that version, so it stops applying to stores that
already carry the new name instead of skipping the table they have — and drops a table
only when the release names it in
`storage/migration.DROPPED_TABLES`; every other table the old store carries and the new
layout lacks fails the upgrade before anything is replaced. Data a removed feature makes
meaningless is deleted by an explicit versioned step (`_drop_approval_notifications`),
in the same rebuild, with an audit fact naming what went and why — never silently
through a table the copy loop happens to skip.

**Amended 2026-09-08** (mock-removal slice #156). A column, like a table, sometimes has
to earn a truthful name, and a rename is not a drop: the values move. A rebuild now reads
a column from the name it had when `storage/migration.COLUMN_RENAMES` says a later version
renamed it — version-scoped exactly like `RENAMES` — and does not count the old name as a
dropped column. Where a rename moves what a stored digest covers, the versioned step that
performs the release's other repairs recomputes it, over a field list frozen in
`migration.py` so a later edit to the live definition cannot silently change what an old
store is rebuilt into.

**Also decided.** A store's runtime kind may change on start when no run is unfinished;
finished runs keep the runtime pins they were admitted with. The container process
boundary stays immutable. The requirement to update `docs/implementation.md` at every
milestone is replaced by an ADR for decisions and a `CHANGELOG.md` line per PR.

**Kept.** ADR 0010 bundles remain the only cross-system path. Backups still pin the
schema version and a backup from an older release is still refused at restore; the
upgrade runs on live stores only until an older backup actually needs restoring.
