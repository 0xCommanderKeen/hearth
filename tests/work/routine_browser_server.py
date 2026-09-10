"""Loopback-only synthetic API fixture for check-routine-retries-browser.mjs."""

import socket
from pathlib import Path
from tempfile import TemporaryDirectory

import uvicorn
from hearth.app import create_app
from hearth.work.routines import Routines

from tests.fake_runtime import fake_runtime
from tests.support import seed_reader


def main():
    with TemporaryDirectory(prefix="hearth-routine-browser-") as data:
        app = create_app(
            Path(data), "synthetic-routine-browser-token", supervise=False, runtime=fake_runtime()
        )
        now = [1_788_652_800]
        hearth = app.state.hearth
        hearth.clock = lambda: now[0]
        seed_reader(hearth)

        @app.post("/__test/tick")
        def tick():
            with hearth.database.transaction() as db:
                now[0] = db.execute("SELECT min(next_at) FROM routines").fetchone()[0]
            scheduler = Routines(hearth)
            tasks = scheduler.tick()
            assert scheduler.tick() == []
            return {"tasks": tasks}

        with socket.socket() as listener:
            listener.bind(("127.0.0.1", 0))
            print(listener.getsockname()[1], flush=True)
            uvicorn.Server(uvicorn.Config(app, log_level="error")).run(sockets=[listener])


if __name__ == "__main__":
    main()
