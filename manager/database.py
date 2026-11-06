from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator


SCHEMA = """
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS clients (
    id TEXT PRIMARY KEY,
    code TEXT NOT NULL UNIQUE,
    name TEXT NOT NULL,
    contact_email TEXT,
    status TEXT NOT NULL DEFAULT 'active',
    environment TEXT NOT NULL DEFAULT 'production',
    services_url TEXT,
    admin_url TEXT,
    database_provider TEXT NOT NULL DEFAULT 'supabase',
    installed_services_version TEXT,
    installed_admin_version TEXT,
    last_health_status TEXT NOT NULL DEFAULT 'unknown',
    last_health_at TEXT,
    client_admin_token_hash TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS client_agents (
    id TEXT PRIMARY KEY,
    client_id TEXT NOT NULL REFERENCES clients(id) ON DELETE CASCADE,
    name TEXT NOT NULL,
    token_hash TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'registered',
    version TEXT,
    capabilities_json TEXT NOT NULL DEFAULT '[]',
    last_seen_at TEXT,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS releases (
    id TEXT PRIMARY KEY,
    component TEXT NOT NULL,
    version TEXT NOT NULL,
    channel TEXT NOT NULL DEFAULT 'stable',
    status TEXT NOT NULL DEFAULT 'draft',
    artifact_url TEXT NOT NULL,
    checksum_sha256 TEXT NOT NULL,
    min_connector_version TEXT,
    release_notes TEXT NOT NULL DEFAULT '',
    manifest_json TEXT NOT NULL DEFAULT '{}',
    git_commit_sha TEXT,
    repository_url TEXT,
    migration_index INTEGER NOT NULL DEFAULT 0,
    validation_status TEXT NOT NULL DEFAULT 'pending',
    validation_details_json TEXT NOT NULL DEFAULT '{}',
    validated_by TEXT,
    validated_at TEXT,
    source_event_id TEXT,
    superseded_by_release_id TEXT,
    created_at TEXT NOT NULL,
    UNIQUE(component, version, channel)
);

CREATE TABLE IF NOT EXISTS deployments (
    id TEXT PRIMARY KEY,
    client_id TEXT NOT NULL REFERENCES clients(id) ON DELETE CASCADE,
    release_id TEXT NOT NULL REFERENCES releases(id),
    status TEXT NOT NULL DEFAULT 'pending_approval',
    approved_by TEXT,
    scheduled_for TEXT,
    started_at TEXT,
    finished_at TEXT,
    previous_version TEXT,
    target_version TEXT NOT NULL,
    error_message TEXT,
    rollback_available INTEGER NOT NULL DEFAULT 1,
    deployment_type TEXT NOT NULL DEFAULT 'update',
    provider TEXT NOT NULL DEFAULT 'agent',
    stage TEXT NOT NULL DEFAULT 'pending_approval',
    progress_percent INTEGER NOT NULL DEFAULT 0,
    rollback_snapshot_ref TEXT,
    agent_notes TEXT,
    idempotency_key TEXT,
    provider_deploy_id TEXT,
    installed_version TEXT,
    installed_commit_sha TEXT,
    health_verified_at TEXT,
    cancelled_at TEXT,
    retry_count INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS support_tickets (
    id TEXT PRIMARY KEY,
    client_id TEXT NOT NULL REFERENCES clients(id) ON DELETE CASCADE,
    external_number TEXT NOT NULL,
    title TEXT NOT NULL,
    summary TEXT NOT NULL,
    severity TEXT NOT NULL DEFAULT 'medium',
    component TEXT NOT NULL DEFAULT 'services',
    status TEXT NOT NULL DEFAULT 'escalated',
    submitted_by_role TEXT NOT NULL DEFAULT 'client_admin',
    approved_by TEXT NOT NULL,
    diagnostics_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(client_id, external_number)
);

CREATE TABLE IF NOT EXISTS client_infrastructure (
    id TEXT PRIMARY KEY,
    client_id TEXT NOT NULL REFERENCES clients(id) ON DELETE CASCADE,
    provider TEXT NOT NULL,
    component TEXT NOT NULL,
    provider_resource_id TEXT,
    region TEXT,
    instance_size TEXT,
    domain TEXT,
    ip_address TEXT,
    credentials_ref TEXT,
    config_json TEXT NOT NULL DEFAULT '{}',
    environment_variables_json TEXT NOT NULL DEFAULT '{}',
    config_version INTEGER NOT NULL DEFAULT 1,
    sync_status TEXT NOT NULL DEFAULT 'pending',
    last_synced_at TEXT,
    config_updated_at TEXT,
    config_updated_by TEXT,
    status TEXT NOT NULL DEFAULT 'provisioning',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS deployment_logs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    deployment_id TEXT NOT NULL REFERENCES deployments(id) ON DELETE CASCADE,
    stage TEXT NOT NULL,
    level TEXT NOT NULL DEFAULT 'info',
    message TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS audit_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    actor TEXT NOT NULL,
    action TEXT NOT NULL,
    object_type TEXT NOT NULL,
    object_id TEXT,
    client_id TEXT,
    details_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS manager_settings (
    key TEXT PRIMARY KEY,
    value_json TEXT NOT NULL DEFAULT '{}',
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS client_configuration_versions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    infrastructure_id TEXT NOT NULL REFERENCES client_infrastructure(id) ON DELETE CASCADE,
    client_id TEXT NOT NULL REFERENCES clients(id) ON DELETE CASCADE,
    version INTEGER NOT NULL,
    config_json TEXT NOT NULL DEFAULT '{}',
    environment_variables_json TEXT NOT NULL DEFAULT '{}',
    sync_status TEXT NOT NULL DEFAULT 'pending',
    changed_by TEXT NOT NULL,
    change_summary TEXT,
    created_at TEXT NOT NULL,
    UNIQUE(infrastructure_id, version)
);

CREATE TABLE IF NOT EXISTS github_webhook_events (
    delivery_id TEXT PRIMARY KEY,
    event_name TEXT NOT NULL,
    payload_sha256 TEXT NOT NULL,
    repository_url TEXT,
    action TEXT,
    processed_status TEXT NOT NULL DEFAULT 'received',
    result_json TEXT NOT NULL DEFAULT '{}',
    received_at TEXT NOT NULL,
    processed_at TEXT
);

CREATE TABLE IF NOT EXISTS deployment_locks (
    client_id TEXT NOT NULL REFERENCES clients(id) ON DELETE CASCADE,
    component TEXT NOT NULL,
    deployment_id TEXT NOT NULL REFERENCES deployments(id) ON DELETE CASCADE,
    lock_token TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    created_at TEXT NOT NULL,
    PRIMARY KEY(client_id, component)
);

CREATE INDEX IF NOT EXISTS idx_deployments_client ON deployments(client_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_tickets_client ON support_tickets(client_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_audit_created ON audit_events(created_at DESC);
"""

POSTGRES_SCHEMA = [
    """CREATE TABLE IF NOT EXISTS clients (
        id TEXT PRIMARY KEY, code TEXT NOT NULL UNIQUE, name TEXT NOT NULL, contact_email TEXT,
        status TEXT NOT NULL DEFAULT 'active', environment TEXT NOT NULL DEFAULT 'production',
        services_url TEXT, admin_url TEXT, database_provider TEXT NOT NULL DEFAULT 'supabase',
        installed_services_version TEXT, installed_admin_version TEXT,
        last_health_status TEXT NOT NULL DEFAULT 'unknown', last_health_at TEXT, client_admin_token_hash TEXT,
        created_at TEXT NOT NULL, updated_at TEXT NOT NULL
    )""",
    """CREATE TABLE IF NOT EXISTS client_agents (
        id TEXT PRIMARY KEY, client_id TEXT NOT NULL REFERENCES clients(id) ON DELETE CASCADE,
        name TEXT NOT NULL, token_hash TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'registered',
        version TEXT, capabilities_json TEXT NOT NULL DEFAULT '[]', last_seen_at TEXT, created_at TEXT NOT NULL
    )""",
    """CREATE TABLE IF NOT EXISTS releases (
        id TEXT PRIMARY KEY, component TEXT NOT NULL, version TEXT NOT NULL,
        channel TEXT NOT NULL DEFAULT 'stable', status TEXT NOT NULL DEFAULT 'draft',
        artifact_url TEXT NOT NULL, checksum_sha256 TEXT NOT NULL, min_connector_version TEXT,
        release_notes TEXT NOT NULL DEFAULT '', manifest_json TEXT NOT NULL DEFAULT '{}',
        git_commit_sha TEXT, repository_url TEXT, migration_index INTEGER NOT NULL DEFAULT 0,
        validation_status TEXT NOT NULL DEFAULT 'pending', validation_details_json TEXT NOT NULL DEFAULT '{}',
        validated_by TEXT, validated_at TEXT, source_event_id TEXT, superseded_by_release_id TEXT, created_at TEXT NOT NULL,
        UNIQUE(component, version, channel)
    )""",
    """CREATE TABLE IF NOT EXISTS deployments (
        id TEXT PRIMARY KEY, client_id TEXT NOT NULL REFERENCES clients(id) ON DELETE CASCADE,
        release_id TEXT NOT NULL REFERENCES releases(id), status TEXT NOT NULL DEFAULT 'pending_approval',
        approved_by TEXT, scheduled_for TEXT, started_at TEXT, finished_at TEXT, previous_version TEXT,
        target_version TEXT NOT NULL, error_message TEXT, rollback_available BOOLEAN NOT NULL DEFAULT TRUE,
        deployment_type TEXT NOT NULL DEFAULT 'update', provider TEXT NOT NULL DEFAULT 'agent',
        stage TEXT NOT NULL DEFAULT 'pending_approval', progress_percent INTEGER NOT NULL DEFAULT 0,
        rollback_snapshot_ref TEXT, agent_notes TEXT, idempotency_key TEXT, provider_deploy_id TEXT,
        installed_version TEXT, installed_commit_sha TEXT, health_verified_at TEXT, cancelled_at TEXT,
        retry_count INTEGER NOT NULL DEFAULT 0, created_at TEXT NOT NULL, updated_at TEXT NOT NULL
    )""",
    """CREATE TABLE IF NOT EXISTS support_tickets (
        id TEXT PRIMARY KEY, client_id TEXT NOT NULL REFERENCES clients(id) ON DELETE CASCADE,
        external_number TEXT NOT NULL, title TEXT NOT NULL, summary TEXT NOT NULL,
        severity TEXT NOT NULL DEFAULT 'medium', component TEXT NOT NULL DEFAULT 'services',
        status TEXT NOT NULL DEFAULT 'escalated', submitted_by_role TEXT NOT NULL DEFAULT 'client_admin',
        approved_by TEXT NOT NULL, diagnostics_json TEXT NOT NULL DEFAULT '{}',
        created_at TEXT NOT NULL, updated_at TEXT NOT NULL, UNIQUE(client_id, external_number)
    )""",
    """CREATE TABLE IF NOT EXISTS client_infrastructure (
        id TEXT PRIMARY KEY, client_id TEXT NOT NULL REFERENCES clients(id) ON DELETE CASCADE,
        provider TEXT NOT NULL, component TEXT NOT NULL, provider_resource_id TEXT, region TEXT,
        instance_size TEXT, domain TEXT, ip_address TEXT, credentials_ref TEXT, config_json TEXT NOT NULL DEFAULT '{}',
        environment_variables_json TEXT NOT NULL DEFAULT '{}', config_version INTEGER NOT NULL DEFAULT 1,
        sync_status TEXT NOT NULL DEFAULT 'pending', last_synced_at TEXT, config_updated_at TEXT, config_updated_by TEXT,
        status TEXT NOT NULL DEFAULT 'provisioning', created_at TEXT NOT NULL, updated_at TEXT NOT NULL
    )""",
    """CREATE TABLE IF NOT EXISTS deployment_logs (
        id BIGSERIAL PRIMARY KEY, deployment_id TEXT NOT NULL REFERENCES deployments(id) ON DELETE CASCADE,
        stage TEXT NOT NULL, level TEXT NOT NULL DEFAULT 'info', message TEXT NOT NULL, created_at TEXT NOT NULL
    )""",
    """CREATE TABLE IF NOT EXISTS audit_events (
        id BIGSERIAL PRIMARY KEY, actor TEXT NOT NULL, action TEXT NOT NULL,
        object_type TEXT NOT NULL, object_id TEXT, client_id TEXT,
        details_json TEXT NOT NULL DEFAULT '{}', created_at TEXT NOT NULL
    )""",
    """CREATE TABLE IF NOT EXISTS manager_settings (
        key TEXT PRIMARY KEY, value_json TEXT NOT NULL DEFAULT '{}', updated_at TEXT NOT NULL
    )""",
    """CREATE TABLE IF NOT EXISTS client_configuration_versions (
        id BIGSERIAL PRIMARY KEY, infrastructure_id TEXT NOT NULL REFERENCES client_infrastructure(id) ON DELETE CASCADE,
        client_id TEXT NOT NULL REFERENCES clients(id) ON DELETE CASCADE, version INTEGER NOT NULL,
        config_json TEXT NOT NULL DEFAULT '{}', environment_variables_json TEXT NOT NULL DEFAULT '{}',
        sync_status TEXT NOT NULL DEFAULT 'pending', changed_by TEXT NOT NULL, change_summary TEXT, created_at TEXT NOT NULL,
        UNIQUE(infrastructure_id,version)
    )""",
    """CREATE TABLE IF NOT EXISTS github_webhook_events (
        delivery_id TEXT PRIMARY KEY,event_name TEXT NOT NULL,payload_sha256 TEXT NOT NULL,repository_url TEXT,
        action TEXT,processed_status TEXT NOT NULL DEFAULT 'received',result_json TEXT NOT NULL DEFAULT '{}',
        received_at TEXT NOT NULL,processed_at TEXT
    )""",
    """CREATE TABLE IF NOT EXISTS deployment_locks (
        client_id TEXT NOT NULL REFERENCES clients(id) ON DELETE CASCADE,component TEXT NOT NULL,
        deployment_id TEXT NOT NULL REFERENCES deployments(id) ON DELETE CASCADE,lock_token TEXT NOT NULL,
        expires_at TEXT NOT NULL,created_at TEXT NOT NULL,PRIMARY KEY(client_id,component)
    )""",
    "CREATE INDEX IF NOT EXISTS idx_deployments_client ON deployments(client_id, created_at DESC)",
    "CREATE INDEX IF NOT EXISTS idx_tickets_client ON support_tickets(client_id, created_at DESC)",
    "CREATE INDEX IF NOT EXISTS idx_audit_created ON audit_events(created_at DESC)",
]


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


class Database:
    def __init__(self, path: Path, database_url: str = ""):
        self.path = path
        self.database_url = database_url.strip()

    @property
    def backend(self) -> str:
        return "supabase-postgresql" if self.database_url else "sqlite"

    @contextmanager
    def connect(self) -> Iterator[Any]:
        if self.database_url:
            try:
                import psycopg
                from psycopg.rows import dict_row
            except ImportError as error:
                raise RuntimeError("PostgreSQL storage requires psycopg. Run the automatic setup again.") from error
            connection = psycopg.connect(self.database_url, row_factory=dict_row)
            try:
                yield connection
                connection.commit()
            except Exception:
                connection.rollback()
                raise
            finally:
                connection.close()
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self.path, timeout=30)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        try:
            yield connection
            connection.commit()
        finally:
            connection.close()

    def initialize(self) -> None:
        if self.database_url:
            with self.connect() as connection:
                for statement in POSTGRES_SCHEMA:
                    connection.execute(statement)
                self._migrate_postgres(connection)
            return
        with self.connect() as connection:
            connection.executescript(SCHEMA)
            self._migrate_sqlite(connection)

    @staticmethod
    def _migrate_sqlite(connection: sqlite3.Connection) -> None:
        additions = {
            "clients": {"contact_email": "TEXT", "client_admin_token_hash":"TEXT"},
            "releases": {"git_commit_sha": "TEXT", "repository_url": "TEXT", "migration_index": "INTEGER NOT NULL DEFAULT 0", "validation_status":"TEXT NOT NULL DEFAULT 'pending'", "validation_details_json":"TEXT NOT NULL DEFAULT '{}'", "validated_by":"TEXT", "validated_at":"TEXT", "source_event_id":"TEXT", "superseded_by_release_id":"TEXT"},
            "deployments": {
                "deployment_type": "TEXT NOT NULL DEFAULT 'update'", "provider": "TEXT NOT NULL DEFAULT 'agent'",
                "stage": "TEXT NOT NULL DEFAULT 'pending_approval'", "progress_percent": "INTEGER NOT NULL DEFAULT 0",
                "rollback_snapshot_ref": "TEXT",
                "idempotency_key":"TEXT","provider_deploy_id":"TEXT","installed_version":"TEXT","installed_commit_sha":"TEXT","health_verified_at":"TEXT","cancelled_at":"TEXT","retry_count":"INTEGER NOT NULL DEFAULT 0",
            },
            "client_infrastructure":{"config_json":"TEXT NOT NULL DEFAULT '{}'","environment_variables_json":"TEXT NOT NULL DEFAULT '{}'","config_version":"INTEGER NOT NULL DEFAULT 1","sync_status":"TEXT NOT NULL DEFAULT 'pending'","last_synced_at":"TEXT","config_updated_at":"TEXT","config_updated_by":"TEXT"},
        }
        for table, columns in additions.items():
            existing = {row[1] for row in connection.execute(f"PRAGMA table_info({table})").fetchall()}
            for column, definition in columns.items():
                if column not in existing:
                    connection.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")
        connection.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_deployments_idempotency ON deployments(idempotency_key) WHERE idempotency_key IS NOT NULL")
        connection.execute("CREATE INDEX IF NOT EXISTS idx_releases_commit ON releases(component,repository_url,git_commit_sha)")
        connection.execute("CREATE INDEX IF NOT EXISTS idx_config_versions ON client_configuration_versions(infrastructure_id,version DESC)")
        Database._migrate_configuration_rows(connection,False)

    @staticmethod
    def _migrate_postgres(connection: Any) -> None:
        additions = {
            "clients": {"contact_email": "TEXT", "client_admin_token_hash":"TEXT"},
            "releases": {"git_commit_sha": "TEXT", "repository_url": "TEXT", "migration_index": "INTEGER NOT NULL DEFAULT 0", "validation_status":"TEXT NOT NULL DEFAULT 'pending'", "validation_details_json":"TEXT NOT NULL DEFAULT '{}'", "validated_by":"TEXT", "validated_at":"TEXT", "source_event_id":"TEXT", "superseded_by_release_id":"TEXT"},
            "deployments": {
                "deployment_type": "TEXT NOT NULL DEFAULT 'update'", "provider": "TEXT NOT NULL DEFAULT 'agent'",
                "stage": "TEXT NOT NULL DEFAULT 'pending_approval'", "progress_percent": "INTEGER NOT NULL DEFAULT 0",
                "rollback_snapshot_ref": "TEXT",
                "idempotency_key":"TEXT","provider_deploy_id":"TEXT","installed_version":"TEXT","installed_commit_sha":"TEXT","health_verified_at":"TEXT","cancelled_at":"TEXT","retry_count":"INTEGER NOT NULL DEFAULT 0",
            },
            "client_infrastructure":{"config_json":"TEXT NOT NULL DEFAULT '{}'","environment_variables_json":"TEXT NOT NULL DEFAULT '{}'","config_version":"INTEGER NOT NULL DEFAULT 1","sync_status":"TEXT NOT NULL DEFAULT 'pending'","last_synced_at":"TEXT","config_updated_at":"TEXT","config_updated_by":"TEXT"},
        }
        for table, columns in additions.items():
            for column, definition in columns.items():
                connection.execute(f"ALTER TABLE {table} ADD COLUMN IF NOT EXISTS {column} {definition}")
        connection.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_deployments_idempotency ON deployments(idempotency_key) WHERE idempotency_key IS NOT NULL")
        connection.execute("CREATE INDEX IF NOT EXISTS idx_releases_commit ON releases(component,repository_url,git_commit_sha)")
        connection.execute("CREATE INDEX IF NOT EXISTS idx_config_versions ON client_configuration_versions(infrastructure_id,version DESC)")
        Database._migrate_configuration_rows(connection,True)

    @staticmethod
    def _migrate_configuration_rows(connection:Any,postgres:bool)->None:
        """Preserve legacy config_json rows and move embedded env values to the canonical encrypted column."""
        rows=connection.execute("SELECT id,config_json,environment_variables_json FROM client_infrastructure").fetchall()
        marker="%s" if postgres else "?"
        for raw in rows:
            row=dict(raw) if not isinstance(raw,sqlite3.Row) else dict(raw)
            try:config=json.loads(row.get("config_json") or "{}")
            except (TypeError,json.JSONDecodeError):config={}
            environment=config.pop("environment_variables",None) or config.pop("environmentVariables",None)
            existing=row.get("environment_variables_json") or "{}"
            if environment and existing in ("","{}",None):
                from .configuration import encrypt_environment
                connection.execute(f"UPDATE client_infrastructure SET config_json={marker},environment_variables_json={marker},config_updated_at=COALESCE(config_updated_at,{marker}) WHERE id={marker}",(json.dumps(config),encrypt_environment(environment),utc_now(),row["id"]))

    def _query(self, query: str) -> str:
        return query.replace("?", "%s") if self.database_url else query

    def all(self, query: str, params: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
        with self.connect() as connection:
            return [dict(row) for row in connection.execute(self._query(query), params).fetchall()]

    def one(self, query: str, params: tuple[Any, ...] = ()) -> dict[str, Any] | None:
        with self.connect() as connection:
            row = connection.execute(self._query(query), params).fetchone()
            return dict(row) if row else None

    def execute(self, query: str, params: tuple[Any, ...] = ()) -> None:
        with self.connect() as connection:
            connection.execute(self._query(query), params)

    def setting(self, key: str, fallback: Any = None) -> Any:
        row = self.one("SELECT value_json FROM manager_settings WHERE key=?", (key,))
        if not row:
            return fallback
        try:
            return json.loads(row["value_json"])
        except (TypeError, json.JSONDecodeError):
            return fallback

    def set_setting(self, key: str, value: Any) -> None:
        self.execute(
            """INSERT INTO manager_settings(key,value_json,updated_at) VALUES(?,?,?)
               ON CONFLICT(key) DO UPDATE SET value_json=excluded.value_json,updated_at=excluded.updated_at""",
            (key, json.dumps(value), utc_now()),
        )

    def audit(
        self,
        actor: str,
        action: str,
        object_type: str,
        object_id: str | None = None,
        client_id: str | None = None,
        details: dict[str, Any] | None = None,
    ) -> None:
        self.execute(
            "INSERT INTO audit_events(actor,action,object_type,object_id,client_id,details_json,created_at) VALUES(?,?,?,?,?,?,?)",
            (actor, action, object_type, object_id, client_id, json.dumps(details or {}), utc_now()),
        )

