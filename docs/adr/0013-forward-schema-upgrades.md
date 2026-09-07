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

**Also decided.** A store's runtime kind may change on start when no run is unfinished;
finished runs keep the runtime pins they were admitted with. The container process
boundary stays immutable. The requirement to update `docs/implementation.md` at every
milestone is replaced by an ADR for decisions and a `CHANGELOG.md` line per PR.

**Kept.** ADR 0010 bundles remain the only cross-system path. Backups still pin the
schema version and a backup from an older release is still refused at restore; the
upgrade runs on live stores only until an older backup actually needs restoring.
