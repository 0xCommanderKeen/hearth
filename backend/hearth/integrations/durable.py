"""Reading untrusted provider JSON, and writing small documents that survive a crash.

Nothing provider-specific is in here: a JSON reader that refuses the shapes an
attacker or a corrupt stream can smuggle past `json.loads` (duplicate keys, `NaN`,
`Infinity`), a publish/read pair that fsyncs the file and its directory so a receipt
recovered after a crash is a receipt that was really written, and the two folder
locks a detached worker is launched under.

**The Codex copies of the reader and the document pair are deliberately not deleted.**
`codex/events.py`, `codex/pricing.py` and `codex/usage.py` are copied verbatim into
the offline collector bundle (`codex/assets.py`), which is mounted into a container,
imported under `-I` with a rewritten namespace, and integrity-pinned byte for byte.
A module those three import has to exist inside that bundle, so pointing them at this
one would either break the bundle or drag Hearth's own imports into it. The locks are
shared, because no bundled module uses them.
"""

import fcntl
import hashlib
import json
import math
import os
import stat
from contextlib import contextmanager
from pathlib import Path

from hearth.residents.models import Refused

# One provider document -- a receipt, a request, a usage file -- never exceeds this.
MAX_DOCUMENT = 4 * 1024 * 1024


def unique_object(pairs: list[tuple[str, object]]) -> dict:
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON key")
        result[key] = value
    return result


def reject_constant(value: str) -> None:
    raise ValueError("non-finite JSON number")


def finite_float(value: str) -> float:
    result = float(value)
    if not math.isfinite(result):
        raise ValueError("non-finite JSON number")
    return result


def short_string(value: object) -> bool:
    return isinstance(value, str) and bool(value.strip()) and len(value) <= 256


def canonical(value) -> bytes:
    """One JSON encoding of a value, so two providers digest it identically.

    Byte for byte what `codex/app_server_config.canonical` produces: a tool list
    admitted on either runtime has to hash to the same `tools_sha256`, or the pin
    would mean one thing on Codex and another on Claude.
    """
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), allow_nan=False, ensure_ascii=False
    ).encode()


def digest(value) -> str:
    return hashlib.sha256(canonical(value)).hexdigest()


def read(path: Path):
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    with os.fdopen(fd, "rb") as stream:
        info = os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
            raise ValueError("unsafe usage file")
        raw = stream.read(MAX_DOCUMENT + 1)
        if len(raw) > MAX_DOCUMENT:
            raise ValueError("oversized usage file")
        value = json.loads(raw, object_pairs_hook=unique_object)
        # A readable file may be left by an unsuccessful publish/fsync. Reconcile
        # both file and directory durability before relying on recovered evidence.
        os.fsync(stream.fileno())
        sync_directory(path.parent)
    return value


def publish(path: Path, value):
    raw = json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    if len(raw) > MAX_DOCUMENT:
        raise ValueError("oversized usage file")
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, "wb") as stream:
        stream.write(raw)
        stream.flush()
        os.fsync(stream.fileno())
    sync_directory(path.parent)


def sync_directory(path: Path):
    directory = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(directory)
    finally:
        os.close(directory)


def same_document(left, right) -> bool:
    # JSON scalar types matter: 0 != false and 30 != 30.0 for token evidence.
    try:
        return json.dumps(left, sort_keys=True, allow_nan=False) == json.dumps(
            right, sort_keys=True, allow_nan=False
        )
    except TypeError, ValueError:
        return False


@contextmanager
def folder_lock(path: Path, refusal: str):
    """Exclusive ownership of one durable runtime folder, following no link."""
    flags = os.O_RDWR | os.O_NOFOLLOW | os.O_NONBLOCK
    try:
        fd = os.open(path, flags | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        fd = os.open(path, flags)
    with os.fdopen(fd, "rb") as lock:
        info = os.fstat(lock.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
            raise Refused(refusal)
        fcntl.flock(lock, fcntl.LOCK_EX)
        yield


@contextmanager
def transferable_lock(path: Path, refusal: str, inherited_fd: int | None = None):
    """Transfer one flock open-file description to a worker with no unlocked interval."""
    fd = (
        inherited_fd
        if inherited_fd is not None
        else os.open(path, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_NONBLOCK, 0o600)
    )
    with os.fdopen(fd, "rb") as lock:
        actual = os.fstat(fd)
        expected = path.stat(follow_symlinks=False)
        if (
            not stat.S_ISREG(actual.st_mode)
            or actual.st_nlink != 1
            or (actual.st_dev, actual.st_ino) != (expected.st_dev, expected.st_ino)
        ):
            raise Refused(refusal)
        os.set_inheritable(fd, False)
        fcntl.flock(fd, fcntl.LOCK_EX)
        # Closing this copy must not explicitly unlock the child's inherited description.
        yield lock.fileno()
