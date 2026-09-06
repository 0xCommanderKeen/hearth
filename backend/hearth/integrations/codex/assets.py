"""Install only the integrity-pinned offline CLI fixture, without installers or login."""

import base64
import hashlib
import json
import shutil
import tarfile
import tempfile
from pathlib import Path

from hearth.integrations.codex import events as codex_events
from hearth.integrations.codex import pricing as codex_pricing
from hearth.integrations.codex import usage as codex_usage
from hearth.integrations.mock.container import container_lock
from hearth.residents.models import Refused

ARCHIVE_SHA512 = (
    "8OLcPXaAol/FOrRoDxWhIiHIFa73KRsM41EKocjRZOwiT4TcelzJWn3dHyiuSb7teWF25rrslvSPyvhULYRRCQ=="
)


def prepare(root: Path, archive: Path | None) -> str:
    root.parent.mkdir(parents=True, exist_ok=True)
    with container_lock(root.parent / ".codex-assets.lock"):
        if not root.exists():
            if archive is None:
                raise Refused("codex_mock_archive_required")
            data = archive.read_bytes()
            if hashlib.sha512(data).digest() != base64.b64decode(ARCHIVE_SHA512):
                raise Refused("codex_mock_archive_invalid")
            temporary = Path(tempfile.mkdtemp(prefix=".codex-assets-", dir=root.parent))
            try:
                pinned = temporary / "archive.tgz"
                pinned.write_bytes(data)
                with tarfile.open(pinned) as bundle:
                    bundle.extractall(temporary / "unpacked", filter="data")
                shutil.move(
                    temporary / "unpacked/package/vendor/aarch64-unknown-linux-musl",
                    temporary / "runtime",
                )
                shutil.rmtree(temporary / "unpacked")
                pinned.unlink()
                write_collector(temporary / "app/hearth")
                (temporary / "fixture.py").write_bytes(fixture_source())
                files = _files(temporary)
                codex_usage.publish(
                    temporary / "manifest.json", {"archive": ARCHIVE_SHA512, "files": files}
                )
                temporary.rename(root)
            finally:
                if temporary.exists():
                    shutil.rmtree(temporary)
        manifest = codex_usage.read(root / "manifest.json")
        if manifest != {"archive": ARCHIVE_SHA512, "files": _files(root)}:
            raise Refused("codex_mock_assets_changed")
        for name, source in collector_sources().items():
            if (root / "app/hearth" / name).read_bytes() != source:
                raise Refused("codex_mock_assets_changed")
        if (root / "fixture.py").read_bytes() != fixture_source():
            raise Refused("codex_mock_assets_changed")
        return hashlib.sha256(json.dumps(manifest, sort_keys=True).encode()).hexdigest()


def _files(root: Path) -> dict:
    files = {}
    for path in sorted(root.rglob("*")):
        if path.is_symlink():
            raise Refused("codex_mock_assets_unsafe")
        if path.is_file() and path.name != "manifest.json":
            files[str(path.relative_to(root))] = hashlib.sha256(path.read_bytes()).hexdigest()
    return files


def collector_sources() -> dict[str, bytes]:
    """Build the stable isolated collector package from the owning provider sources.

    This bundle is mounted into the offline container, not imported by Hearth.
    Its existing paths and bytes are integrity-pinned in persisted runtime assets.
    Moving host modules must not change that independent bundle's import namespace.
    """
    sources = {"__init__.py": b""}
    for module in (codex_events, codex_pricing, codex_usage):
        name = "codex_" + Path(module.__file__).name
        sources[name] = standalone_source(Path(module.__file__))
    return sources


def write_collector(package: Path) -> None:
    package.mkdir(parents=True)
    for name, source in collector_sources().items():
        (package / name).write_bytes(source)


def standalone_source(path: Path) -> bytes:
    source = path.read_text()
    for component in ("events", "pricing", "usage"):
        source = source.replace(
            "from hearth.integrations.codex." + component + " import",
            "from hearth.codex_" + component + " import",
        )
    return source.encode()


def fixture_source() -> bytes:
    return standalone_source(Path(__file__).with_name("fixture.py"))
