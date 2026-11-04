from __future__ import annotations

import os
import json
import base64
import hashlib
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv


ROOT = Path(__file__).resolve().parents[1]
load_dotenv(ROOT / ".env")


def _path(value: str) -> Path:
    path = Path(value)
    return path if path.is_absolute() else (ROOT / path).resolve()


@dataclass(frozen=True)
class Settings:
    host: str = os.getenv("HOST") or os.getenv("VTAB_MANAGER_HOST", "0.0.0.0" if os.getenv("PORT") else "127.0.0.1")
    port: int = int(os.getenv("PORT") or os.getenv("VTAB_MANAGER_PORT", "8840"))
    environment: str = os.getenv("VTAB_MANAGER_ENV", "development").lower()
    api_key: str = os.getenv("VTAB_MANAGER_API_KEY", "vtab-local-dev")
    database_path: Path = _path(os.getenv("VTAB_MANAGER_DB", "./data/vtab_manager.db"))
    database_url: str = os.getenv("VTAB_MANAGER_DATABASE_URL", "").strip()
    supabase_url: str = os.getenv("SUPABASE_URL", os.getenv("VITE_SUPABASE_URL", "")).strip()
    supabase_anon_key: str = os.getenv("SUPABASE_ANON_KEY", os.getenv("VITE_SUPABASE_ANON_KEY", "")).strip()
    supabase_service_role_key: str = os.getenv("SUPABASE_SERVICE_ROLE_KEY", "").strip()
    github_token: str = os.getenv("VTAB_GITHUB_TOKEN", "").strip()
    github_repository: str = os.getenv("VTAB_GITHUB_REPOSITORY", "").strip()
    github_repository_services: str = os.getenv("GITHUB_REPOSITORY_SERVICES", "").strip()
    github_repository_admin: str = os.getenv("GITHUB_REPOSITORY_ADMIN", "").strip()
    github_webhook_secret: str = os.getenv("GITHUB_WEBHOOK_SECRET", "").strip()
    github_allowed_branches: tuple[str, ...] = tuple(item.strip() for item in os.getenv("VTAB_GITHUB_ALLOWED_BRANCHES", "main").split(",") if item.strip())
    github_required_checks: tuple[str, ...] = tuple(item.strip() for item in os.getenv("VTAB_GITHUB_REQUIRED_CHECKS", "build,test,security").split(",") if item.strip())
    operator_keys_json: str = os.getenv("VTAB_MANAGER_OPERATOR_KEYS_JSON", "").strip()
    config_encryption_key: str = os.getenv("VTAB_CONFIG_ENCRYPTION_KEY", "").strip()
    config_key_id: str = os.getenv("VTAB_CONFIG_KEY_ID", "manager-config-v1").strip()
    webhook_max_age_seconds: int = int(os.getenv("VTAB_WEBHOOK_MAX_AGE_SECONDS", "300"))
    deployment_lock_seconds: int = int(os.getenv("VTAB_DEPLOYMENT_LOCK_SECONDS", "1800"))
    health_timeout_seconds: int = int(os.getenv("VTAB_DEPLOYMENT_HEALTH_TIMEOUT_SECONDS", "600"))
    update_poll_interval_seconds: int = int(os.getenv("VTAB_UPDATE_POLL_INTERVAL_SECONDS", "300"))
    open_browser: bool = os.getenv("VTAB_MANAGER_OPEN_BROWSER", "1") == "1"

    @property
    def development(self) -> bool:
        return self.environment == "development"

    @property
    def storage_backend(self) -> str:
        if self.database_url.startswith(("postgres://", "postgresql://")):
            return "supabase-postgresql"
        return "sqlite"

    @property
    def operator_keys(self) -> dict[str, dict[str, str]]:
        if self.operator_keys_json:
            try:
                value=json.loads(self.operator_keys_json)
                if isinstance(value,dict):
                    return {str(key):{"actor":str((meta or {}).get("actor") or "operator"),"role":str((meta or {}).get("role") or "operator")} for key,meta in value.items() if key}
            except (TypeError,ValueError,json.JSONDecodeError):
                pass
        return {self.api_key:{"actor":"bootstrap-operator","role":"super_admin"}}

    @property
    def encryption_key_bytes(self) -> bytes:
        if self.config_encryption_key:
            try:
                raw=base64.urlsafe_b64decode(self.config_encryption_key.encode("ascii"))
            except Exception as error:
                raise RuntimeError("VTAB_CONFIG_ENCRYPTION_KEY must be URL-safe base64.") from error
            if len(raw)!=32:
                raise RuntimeError("VTAB_CONFIG_ENCRYPTION_KEY must decode to exactly 32 bytes.")
            return raw
        if not self.development:
            raise RuntimeError("VTAB_CONFIG_ENCRYPTION_KEY is required outside development.")
        return hashlib.sha256((self.api_key+":development-config-key").encode("utf-8")).digest()


settings = Settings()

