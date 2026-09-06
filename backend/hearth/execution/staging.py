"""Trusted worker input staging; filesystem permissions are not a sandbox."""

import fcntl
import hashlib
import json
import os
import stat
import uuid
from pathlib import Path

from hearth.execution.context import read_context
from hearth.residents.memory import MemoryFiles
from hearth.residents.models import Refused, identifier
from hearth.storage.artifacts import sync_directory
from hearth.storage.database import Database

MAX_INPUT = 512 * 1024


def stage_run(database: Database, run_id: str, root: Path) -> Path:
    """Publish only the admitted context under a private, trusted worker root.

    This internal operation grants no launch authority. The caller must keep root
    and its ancestors outside untrusted writers and recheck launch authority later.
    A completed directory contains exactly context.json; retries verify its bytes.
    """
    identifier(run_id)
    with database.transaction() as db:
        row = db.execute("SELECT input_digest FROM runs WHERE id=?", (run_id,)).fetchone()
        if row is None:
            raise Refused("run_not_found")
        context = read_context(db, run_id, MemoryFiles(database.path.parent / "memory"))
        data = json.dumps(context, sort_keys=True, separators=(",", ":")).encode()
        if len(data) > MAX_INPUT:
            raise Refused("staged_input_too_large")
        if hashlib.sha256(data).hexdigest() != row["input_digest"]:
            raise Refused("staged_input_digest_mismatch")
    # Root is caller-selected, not derived from any model-controlled input.
    try:
        root.mkdir(mode=0o700, exist_ok=True)
        sync_directory(root.parent)
        directory = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            info = os.fstat(directory)
            if info.st_uid != os.getuid() or info.st_mode & 0o077:
                raise Refused("staged_input_unsafe")
            flags = os.O_RDWR | os.O_NOFOLLOW | os.O_NONBLOCK
            try:
                lock = os.open(
                    ".stage.lock", flags | os.O_CREAT | os.O_EXCL, 0o600, dir_fd=directory
                )
            except FileExistsError:
                lock = os.open(".stage.lock", flags, dir_fd=directory)
            try:
                info = os.fstat(lock)
                if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
                    raise Refused("staged_input_unsafe")
                fcntl.flock(lock, fcntl.LOCK_EX)
                _publish(directory, run_id, data)
            finally:
                os.close(lock)
        finally:
            os.close(directory)
    except OSError:
        raise Refused("staged_input_unsafe") from None
    return root / run_id / "context.json"


def _verify(directory: int, run_id: str, data: bytes) -> None:
    folder = os.open(run_id, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=directory)
    try:
        info = os.fstat(folder)
        if info.st_uid != os.getuid() or info.st_mode & 0o077:
            raise Refused("staged_input_unsafe")
        if set(os.listdir(folder)) != {"context.json"}:
            raise Refused("staged_input_conflict")
        fd = os.open("context.json", os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=folder)
        with os.fdopen(fd, "rb") as file:
            info = os.fstat(file.fileno())
            if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_mode & 0o377:
                raise Refused("staged_input_unsafe")
            if file.read(MAX_INPUT + 1) != data:
                raise Refused("staged_input_conflict")
    finally:
        os.close(folder)


def _publish(directory: int, run_id: str, data: bytes) -> None:
    try:
        os.stat(run_id, dir_fd=directory, follow_symlinks=False)
    except FileNotFoundError:
        pass
    else:
        _verify(directory, run_id, data)
        os.fsync(directory)
        return
    temporary = ".stage-" + uuid.uuid4().hex
    os.mkdir(temporary, mode=0o700, dir_fd=directory)
    folder = os.open(temporary, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=directory)
    published = False
    try:
        fd = os.open("context.json", os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o400, dir_fd=folder)
        with os.fdopen(fd, "wb") as file:
            file.write(data)
            file.flush()
            os.fsync(file.fileno())
        os.fsync(folder)
        os.rename(temporary, run_id, src_dir_fd=directory, dst_dir_fd=directory)
        published = True
        os.fsync(directory)
    finally:
        if not published:
            try:
                os.unlink("context.json", dir_fd=folder)
            except FileNotFoundError:
                pass
            os.rmdir(temporary, dir_fd=directory)
        os.close(folder)
