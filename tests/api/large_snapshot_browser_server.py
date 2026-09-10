"""Synthetic SQLite and production SSE route for check-large-snapshots-browser.mjs."""

import socket
from pathlib import Path
from tempfile import TemporaryDirectory

import uvicorn
from hearth.app import create_app

from tests.fake_runtime import fake_runtime
from tests.support import seed_reader


def main():
    with TemporaryDirectory(prefix="hearth-large-snapshot-browser-") as data:
        app = create_app(
            Path(data), "synthetic-large-snapshot-token", supervise=False, runtime=fake_runtime()
        )
        hearth = app.state.hearth
        seed_reader(hearth)
        count = 0

        def add(unit, amount):
            nonlocal count
            for _ in range(amount):
                count += 1
                hearth.submit(
                    f"large-{count}",
                    "reader",
                    (unit * 32_000)[:32_000],
                    expires_at=int(hearth.clock()) + 3600,
                )
            return {"count": count}

        add("a", 65)

        @app.post("/__test/add")
        def more(unicode: bool = False, amount: int = 1):
            assert 1 <= amount <= 100
            return add("🦔漢\n" if unicode else "a", amount)

        with socket.socket() as listener:
            listener.bind(("127.0.0.1", 0))
            print(listener.getsockname()[1], flush=True)
            uvicorn.Server(uvicorn.Config(app, log_level="error")).run(sockets=[listener])


if __name__ == "__main__":
    main()
