"""Authenticated operator controls for resident readiness and configuration."""

from fastapi import FastAPI, Header

from hearth.residents.maintenance import (
    ConfigurationChange,
    LifecycleChange,
    Maintenance,
    ManagerChange,
)
from hearth.work.service import Hearth


def mount_maintenance(app: FastAPI, hearth: Hearth) -> None:
    maintenance = Maintenance(hearth)

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
