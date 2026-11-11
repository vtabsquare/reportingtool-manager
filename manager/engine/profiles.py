from __future__ import annotations

from dataclasses import asdict, dataclass


@dataclass(frozen=True)
class ApplicationProfile:
    key: str
    name: str
    repository_setting: str
    python_version: str
    node_version: str
    backend_entry: str
    port: int
    frontend_build: str
    health_path: str
    required_secrets: tuple[str, ...]
    build_command: str
    start_command: str

    def public(self) -> dict:
        value = asdict(self)
        value["required_secrets"] = list(self.required_secrets)
        return value


application_profiles = {
    "services": ApplicationProfile(
        key="services", name="Reporting Services", repository_setting="services_repository",
        python_version="3.11+", node_version="18.18+", backend_entry="api/cloud_backend.py", port=8820,
        frontend_build="npm ci && npm run build:web", health_path="/api/v1/health",
        required_secrets=("VITE_SUPABASE_URL", "SUPABASE_SERVICE_ROLE_KEY", "VTAB_WEB_URL"),
        build_command="pip install -r api/requirements.txt && npm ci && npm run build:web",
        start_command="python api/cloud_backend.py",
    ),
    "administration": ApplicationProfile(
        key="administration", name="VTAB Admin Console", repository_setting="admin_repository",
        python_version="3.10+", node_version="18.18+", backend_entry="backend/main.py", port=8835,
        frontend_build="npm ci && npm run build", health_path="/api/v1/health",
        required_secrets=("SUPABASE_URL", "SUPABASE_SERVICE_ROLE_KEY", "BREVO_API_KEY", "ADMIN_EMAIL"),
        build_command="pip install -r backend/requirements.txt && npm ci && npm run build",
        start_command="uvicorn backend.main:app --host 0.0.0.0 --port 8835",
    ),
}


def selected_profiles(application: str) -> list[ApplicationProfile]:
    if application == "both":
        return [application_profiles["services"], application_profiles["administration"]]
    return [application_profiles[application]]
