"""Bounded immutable artifacts, published durably before a database reference."""

import hashlib
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path

from hearth.residents.models import Refused, identifier

MAX_ARTIFACT = 512 * 1024


@dataclass(frozen=True)
class Artifact:
    id: str
    run_id: str
    relative_path: str
    sha256: str
    size: int
    simulated: bool = True


def sync_directory(path: Path) -> None:
    fd = os.open(path, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


class Artifacts:
    def __init__(self, root: Path):
        self.root = root
        root.mkdir(parents=True, exist_ok=True)

    def publish(self, run_id: str, content: str, *, simulated: bool = True) -> Artifact:
        identifier(run_id)
        data = content.encode("utf-8")
        if not data or len(data) > MAX_ARTIFACT:
            raise Refused("invalid_artifact_size")
        name = run_id + ".md"
        destination = self.root / name
        fd, temporary = tempfile.mkstemp(prefix=".publish-", dir=self.root)
        try:
            with os.fdopen(fd, "wb") as file:
                file.write(data)
                file.flush()
                os.fsync(file.fileno())
            try:
                os.link(temporary, destination)
            except FileExistsError:
                if destination.is_symlink() or destination.read_bytes() != data:
                    raise Refused("artifact_conflict") from None
            sync_directory(self.root)
        finally:
            Path(temporary).unlink(missing_ok=True)
        return Artifact(
            run_id, run_id, name, hashlib.sha256(data).hexdigest(), len(data), simulated
        )

    def read(self, artifact: Artifact) -> str:
        if artifact.relative_path != artifact.run_id + ".md":
            raise Refused("invalid_artifact_path")
        identifier(artifact.run_id)
        path = self.root / artifact.relative_path
        if path.is_symlink():
            raise Refused("invalid_artifact_path")
        try:
            with path.open("rb") as file:
                data = file.read(MAX_ARTIFACT + 1)
        except FileNotFoundError:
            raise Refused("artifact_missing") from None
        if len(data) != artifact.size or hashlib.sha256(data).hexdigest() != artifact.sha256:
            raise Refused("artifact_corrupt")
        return data.decode("utf-8")
