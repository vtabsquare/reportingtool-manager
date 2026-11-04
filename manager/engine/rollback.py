from __future__ import annotations

from datetime import datetime, timezone


class RollbackManager:
    """Creates auditable rollback metadata; restoration is executed by the client connector."""

    @staticmethod
    def snapshot_reference(client_id: str, component: str, previous_version: str | None) -> str:
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        version = previous_version or "initial"
        return f"vtab-backup://{client_id}/{component}/{version}/{stamp}"

    @staticmethod
    def requirements(archive_size: int = 0) -> dict:
        return {
            "minimumFreeBytes": archive_size * 3 + 2 * 1024**3,
            "maximumRestoreSeconds": 30,
            "requiresManifest": True,
        }
