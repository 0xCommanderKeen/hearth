"""One process-lifetime supervisor; ownership outlives every in-flight operation."""

import fcntl
import threading
from typing import IO

from hearth.execution import Executor
from hearth.models import Refused
from hearth.notifications import Notifications
from hearth.routines import Routines


class Supervisor:
    def __init__(self, executor: Executor, routines: Routines, notifications: Notifications):
        self.executor = executor
        self.routines = routines
        self.notifications = notifications
        self._guard = threading.Lock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._health: dict = {
            "supervisor": "stopped",
            "executor_error": None,
            "notification_error": None,
        }

    def health(self) -> dict:
        with self._guard:
            return dict(self._health)

    def start(self) -> None:
        with self._guard:
            if self._thread is not None and self._thread.is_alive():
                raise Refused("supervisor_already_started")
            database = self.executor.execution.hearth.database
            if database.restored():
                raise Refused("restored_copy_read_only")
            path = database.path.resolve().with_suffix(".supervisor.lock")
            lock = path.open("a")
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                lock.close()
                raise Refused("supervisor_busy") from None
            self._stop.clear()
            self._health.update(supervisor="running", executor_error=None, notification_error=None)
            self._thread = threading.Thread(
                target=self._run, args=(lock,), name="hearth-supervisor"
            )
            try:
                self._thread.start()
            except BaseException:
                lock.close()
                self._health["supervisor"] = "stopped"
                raise

    def stop(self) -> None:
        with self._guard:
            worker = self._thread
            if worker is None or not worker.is_alive():
                return
            if self._health["supervisor"] == "running":
                self._health["supervisor"] = "stopping"
            self._stop.set()
        # No timeout releases ownership early. A stuck adapter means a visibly
        # stopping supervisor; process termination is a separate recovery event.
        worker.join()

    def _set(self, key: str, value: str | None) -> None:
        with self._guard:
            self._health[key] = value

    def _run(self, lock: IO[str]) -> None:
        failed = False
        try:
            while not self._stop.is_set():
                try:
                    self.routines.tick()
                    if self._stop.is_set():
                        break
                    self.routines.admit_queued()
                    if self._stop.is_set():
                        break
                    self.executor.step()
                    self._set("executor_error", None)
                except Exception as error:
                    self._set("executor_error", type(error).__name__)
                if self._stop.is_set():
                    break
                try:
                    self.notifications.step()
                    self._set("notification_error", None)
                except Exception as error:
                    self._set("notification_error", type(error).__name__)
                self._stop.wait(0.5)
        except BaseException as error:
            failed = True
            self._set("executor_error", type(error).__name__)
        finally:
            self._set("supervisor", "failed" if failed else "stopped")
            lock.close()
