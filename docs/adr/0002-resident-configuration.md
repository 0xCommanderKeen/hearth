# Store revisioned resident declarations with operational state

The live database owns resident declarations and their immutable revisions. Saves
compare the expected revision and validate before committing. The CLI and browser use the same declaration interface; repositories can provide
templates but do not synchronize bidirectionally with live declarations. This trades Git-native editing for one
configuration authority, transactional revision checks, and exact run provenance.
Memory and larger artifacts remain files with explicitly coordinated backups.

