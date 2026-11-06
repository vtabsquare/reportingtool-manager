from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field, HttpUrl, SecretStr, field_validator


class ClientCreate(BaseModel):
    name: str = Field(min_length=2, max_length=160)
    code: str = Field(min_length=2, max_length=48, pattern=r"^[a-z0-9][a-z0-9-]*$")
    environment: Literal["development", "test", "staging", "production"] = "production"
    services_url: HttpUrl | None = None
    admin_url: HttpUrl | None = None
    database_provider: Literal["supabase", "azure", "aws", "postgresql", "other"] = "supabase"
    contact_email: str | None = Field(default=None, max_length=254, pattern=r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


class ReleaseCreate(BaseModel):
    component: Literal["services", "administration", "manager", "connector"]
    version: str = Field(min_length=1, max_length=64)
    channel: Literal["preview", "stable", "critical"] = "stable"
    artifact_url: HttpUrl
    checksum_sha256: str = Field(pattern=r"^[a-fA-F0-9]{64}$")
    min_connector_version: str | None = None
    release_notes: str = Field(default="", max_length=8000)
    manifest: dict[str, Any] = Field(default_factory=dict)
    git_commit_sha: str | None = Field(default=None, max_length=64)
    repository_url: HttpUrl | None = None
    migration_index: int = Field(default=0, ge=0)


class ReleaseVersionUpdate(BaseModel):
    # Assigned from an application tag/manifest, never guessed from a Git SHA.
    version: str = Field(min_length=3, max_length=64, pattern=r"^v?\d+(?:\.\d+){1,3}(?:[-+][A-Za-z0-9.-]+)?$")


class DeploymentCreate(BaseModel):
    client_id: str
    release_id: str
    scheduled_for: str | None = None
    deployment_type: Literal["update", "rollback"] = "update"


class DeploymentApproval(BaseModel):
    approved_by: str = Field(min_length=2, max_length=200)


class AgentCheckIn(BaseModel):
    agent_version: str = Field(min_length=1, max_length=64)
    services_version: str | None = None
    admin_version: str | None = None
    health: Literal["healthy", "degraded", "offline"] = "healthy"
    capabilities: list[str] = Field(default_factory=list)


class AgentDeploymentReport(BaseModel):
    status: Literal["preflight", "deploying", "validating", "succeeded", "failed", "rolled_back"]
    message: str = Field(default="", max_length=4000)
    previous_version: str | None = None
    provider_deploy_id: str | None = Field(default=None, max_length=200)
    installed_version: str | None = Field(default=None, max_length=64)
    installed_commit_sha: str | None = Field(default=None, pattern=r"^[a-fA-F0-9]{40}$")
    health_verified: bool = False
    diagnostics: dict[str, Any] = Field(default_factory=dict)


class TicketEscalation(BaseModel):
    external_number: str = Field(min_length=2, max_length=100)
    title: str = Field(min_length=3, max_length=300)
    summary: str = Field(min_length=5, max_length=8000)
    severity: Literal["low", "medium", "high", "critical"] = "medium"
    component: Literal["services", "administration", "connector", "deployment"] = "services"
    client_approved: bool
    approved_by: str = Field(min_length=2, max_length=200)
    diagnostics: dict[str, Any] = Field(default_factory=dict)

    @field_validator("client_approved")
    @classmethod
    def require_approval(cls, value: bool) -> bool:
        if not value:
            raise ValueError("The client administrator must approve escalation.")
        return value


class TicketUpdate(BaseModel):
    status: Literal["escalated", "triaged", "in_progress", "fix_available", "deployed", "closed"]


class GitHubSettingsUpdate(BaseModel):
    repository: HttpUrl


class RepositorySettingsUpdate(BaseModel):
    services_repository: HttpUrl
    admin_repository: HttpUrl


class ClientConfigurationUpdate(BaseModel):
    infrastructure_id: str
    provider_config: dict[str, Any] = Field(default_factory=dict)
    environment_variables: dict[str, str] = Field(default_factory=dict)
    change_summary: str = Field(default="Configuration updated",min_length=3,max_length=500)
    sync_provider: bool = False
    provider_token: SecretStr | None = None


class ReleaseValidation(BaseModel):
    approved: bool
    ci_status: Literal["success","failure","pending"]
    checks: dict[str,str] = Field(default_factory=dict)
    notes: str = Field(default="",max_length=2000)


class ReleaseNotesUpdate(BaseModel):
    release_notes: str = Field(max_length=8000)


class GitHubSyncRequest(BaseModel):
    component: Literal["services","administration"]
    repository_url: HttpUrl | None = None
    branch: str = Field(default="main",pattern=r"^[A-Za-z0-9._/-]+$")
    limit: int = Field(default=10,ge=1,le=50)


class AgentUpdateAcceptance(BaseModel):
    confirmed: bool
    requested_by: str = Field(min_length=2,max_length=254)

    @field_validator("confirmed")
    @classmethod
    def require_update_confirmation(cls,value:bool)->bool:
        if not value:raise ValueError("Explicit update confirmation is required.")
        return value


class DeploymentRetry(BaseModel):
    reason: str = Field(min_length=3,max_length=1000)


class WizardPlan(BaseModel):
    client_id: str
    application: Literal["services", "administration", "both"]
    provider: Literal["render", "digitalocean"]
    release_ids: dict[str, str] = Field(default_factory=dict)
    repositories: dict[str, str] = Field(default_factory=dict)
    provider_config: dict[str, Any] = Field(default_factory=dict)
    environment_variables: dict[str, str] = Field(default_factory=dict)


class WizardPreflightRequest(WizardPlan):
    provider_token: SecretStr | None = None
    verify_provider: bool = True


class WizardExecuteRequest(WizardPlan):
    provider_token: SecretStr | None = None
    confirm_provisioning: bool

    @field_validator("confirm_provisioning")
    @classmethod
    def require_confirmation(cls, value: bool) -> bool:
        if not value:
            raise ValueError("Explicit provisioning confirmation is required.")
        return value


class AgentUpdatePreflight(BaseModel):
    current_health: Literal["healthy", "degraded", "offline"]
    free_disk_bytes: int = Field(ge=0)
    release_archive_bytes: int = Field(ge=0)
    working_tree_clean: bool
    installed_migration_index: int = Field(ge=0)
    rollback_manifest_ready: bool


class RollbackRequest(BaseModel):
    reason: str = Field(min_length=3, max_length=1000)
    confirmed: bool

    @field_validator("confirmed")
    @classmethod
    def require_rollback_confirmation(cls, value: bool) -> bool:
        if not value:
            raise ValueError("Explicit rollback confirmation is required.")
        return value


class ConnectionTestRequest(BaseModel):
    provider: Literal["digitalocean", "render"] = "digitalocean"
    host: str | None = None
    ssh_port: int = 22
    ssh_user: str = "root"
    ssh_key: str | None = None
    provider_token: SecretStr | None = None
    client_id: str | None = None
    infrastructure_id: str | None = None


class ServerSettingsUpdate(BaseModel):
    host: str = Field(default="", max_length=255)
    ssh_port: int = Field(default=22, ge=1, le=65535)
    ssh_user: str = Field(default="root", max_length=64)
    services_target_dir: str = Field(default="/var/www/reportingtool-webservice", max_length=500)
    admin_target_dir: str = Field(default="/var/www/reportingtool-admin", max_length=500)
    digitalocean_token: str = Field(default="", max_length=500)


