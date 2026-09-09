"""Authenticated operator controls for resident readiness and configuration."""

from collections.abc import Iterable

from fastapi import FastAPI, Header
from fastapi.responses import JSONResponse

from hearth.residents.bundle import Bundles, file_name
from hearth.residents.maintenance import (
    ConfigurationChange,
    LifecycleChange,
    Maintenance,
    ManagerChange,
)
from hearth.work.service import Hearth


def mount_maintenance(app: FastAPI, hearth: Hearth, protected: Iterable[str] = ()) -> None:
    """`protected` is what this installation refuses to let an imported grant mount."""
    maintenance = Maintenance(hearth)
    bundles = Bundles(hearth, protected)

    @app.post("/api/residents/import", status_code=201)
    def import_bundle(body: dict, idempotency_key: str = Header(min_length=1, max_length=128)):
        return bundles.import_(idempotency_key, body)

    @app.get("/api/residents/{resident_id}/export")
    def export_bundle(resident_id: str):
        bundle = bundles.export(resident_id)
        name = file_name(bundle["resident"]["name"])
        return JSONResponse(
            bundle, headers={"Content-Disposition": f'attachment; filename="{name}"'}
        )

    @app.get("/api/residents/{resident_id}/lifecycle")
    def read(resident_id: str):
        return maintenance.lifecycle(resident_id)

    @app.get("/api/residents/{resident_id}/configuration")
    def configuration(resident_id: str):
        return maintenance.configuration(resident_id)

    @app.put("/api/residents/{resident_id}/configuration")
    def configure(
        resident_id: str,
        body: ConfigurationChange,
        idempotency_key: str = Header(min_length=1, max_length=128),
    ):
        return maintenance.configure(idempotency_key, resident_id, body)

    @app.put("/api/residents/{resident_id}/manager")
    def transfer(
        resident_id: str,
        body: ManagerChange,
        idempotency_key: str = Header(min_length=1, max_length=128),
    ):
        return maintenance.transfer(idempotency_key, resident_id, body)

    @app.put("/api/residents/{resident_id}/lifecycle")
    def change(
        resident_id: str,
        body: LifecycleChange,
        idempotency_key: str = Header(min_length=1, max_length=128),
    ):
        return maintenance.change_lifecycle(idempotency_key, resident_id, body)
