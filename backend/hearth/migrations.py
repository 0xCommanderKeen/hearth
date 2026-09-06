"""Add execution outcomes without discarding foundation identities or receipts."""

import sqlite3
import uuid


def execution_schema(db: sqlite3.Connection) -> None:
    """Rebuild constrained tables inside the caller's migration transaction."""
    db.execute("""CREATE TABLE tasks_next (
        id TEXT PRIMARY KEY, resident_id TEXT NOT NULL REFERENCES residents(id),
        instruction TEXT NOT NULL,
        status TEXT NOT NULL CHECK (status IN
            ('queued','starting','running','stopping','interrupted','succeeded','failed','cancelled')),
        created_at INTEGER NOT NULL
    )""")
    db.execute("INSERT INTO tasks_next SELECT * FROM tasks")
    db.execute("""CREATE TABLE runs_next (
        id TEXT PRIMARY KEY, task_id TEXT NOT NULL REFERENCES tasks(id),
        resident_id TEXT NOT NULL, resident_revision INTEGER NOT NULL,
        owner_token TEXT NOT NULL UNIQUE,
        status TEXT NOT NULL CHECK (status IN
            ('starting','running','stopping','interrupted','succeeded','failed','cancelled')),
        reserved INTEGER NOT NULL CHECK (reserved >= 0),
        budget_day TEXT NOT NULL, created_at INTEGER NOT NULL,
        actual_cost INTEGER CHECK (actual_cost >= 0),
        usage_known INTEGER NOT NULL DEFAULT 0 CHECK (usage_known IN (0,1)),
        finished_at INTEGER,
        artifact_id TEXT,
        cancellation_requested INTEGER NOT NULL DEFAULT 0 CHECK (cancellation_requested IN (0,1)),
        launch_attempted INTEGER NOT NULL DEFAULT 0 CHECK (launch_attempted IN (0,1)),
        FOREIGN KEY(resident_id,resident_revision) REFERENCES declarations(resident_id,revision)
    )""")
    db.execute("""INSERT INTO runs_next
        (id,task_id,resident_id,resident_revision,owner_token,status,reserved,budget_day,created_at)
        SELECT id,task_id,resident_id,resident_revision,owner_token,status,
            reserved,budget_day,created_at
        FROM runs""")
    db.execute("DROP TABLE runs")
    db.execute("DROP TABLE tasks")
    db.execute("ALTER TABLE tasks_next RENAME TO tasks")
    db.execute("ALTER TABLE runs_next RENAME TO runs")
    active = "status IN ('starting','running','stopping','interrupted')"
    db.execute(f"CREATE UNIQUE INDEX active_resident ON runs(resident_id) WHERE {active}")
    db.execute(f"CREATE UNIQUE INDEX active_task ON runs(task_id) WHERE {active}")
    db.execute("""CREATE TABLE pauses (
        resident_id TEXT PRIMARY KEY REFERENCES residents(id), reason TEXT NOT NULL,
        run_id TEXT NOT NULL REFERENCES runs(id), created_at INTEGER NOT NULL
    )""")
    db.execute("""CREATE TABLE artifacts (
        id TEXT PRIMARY KEY, run_id TEXT NOT NULL UNIQUE REFERENCES runs(id),
        relative_path TEXT NOT NULL UNIQUE, sha256 TEXT NOT NULL,
        size INTEGER NOT NULL CHECK (size >= 0), simulated INTEGER NOT NULL CHECK (simulated = 1)
    )""")


def observation_schema(db: sqlite3.Connection) -> None:
    db.execute("CREATE TABLE system_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
    db.execute("INSERT INTO system_meta VALUES (?, ?)", ("epoch", str(uuid.uuid4())))
