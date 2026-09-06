"""Install only the integrity-pinned offline CLI fixture, without installers or login."""

import base64
import hashlib
import json
import shutil
import tarfile
import tempfile
from pathlib import Path

from hearth import codex_events, codex_pricing, codex_usage
from hearth.container_rehearsal import container_lock
from hearth.models import Refused

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
                package = temporary / "app/hearth"
                package.mkdir(parents=True)
                (package / "__init__.py").write_text("")
                for module in (codex_events, codex_pricing, codex_usage):
                    source = Path(module.__file__)
                    shutil.copyfile(source, package / source.name)
                shutil.copyfile(
                    Path(__file__).with_name("codex_fixture.py"), temporary / "fixture.py"
                )
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
        for module in (codex_events, codex_pricing, codex_usage):
            if (root / "app/hearth" / Path(module.__file__).name).read_bytes() != Path(
                module.__file__
            ).read_bytes():
                raise Refused("codex_mock_assets_changed")
        if (root / "fixture.py").read_bytes() != Path(__file__).with_name(
            "codex_fixture.py"
        ).read_bytes():
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
