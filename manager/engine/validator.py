from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any
from urllib.parse import urlparse

from .profiles import selected_profiles


@dataclass
class PreflightCheck:
    key: str
    category: str
    label: str
    status: str
    message: str
    resolution: str = ""

    def public(self) -> dict[str, Any]:
        return asdict(self)


class DeploymentValidator:
    SUPPORTED_REGIONS = {"nyc1", "nyc3", "sfo3", "fra1", "blr1", "oregon", "ohio", "frankfurt", "singapore"}
    SUPPORTED_DO_IMAGES = {"ubuntu-22-04-x64", "ubuntu-24-04-x64"}

    def validate_static(self, plan: Any) -> list[PreflightCheck]:
        checks: list[PreflightCheck] = []
        profiles = selected_profiles(plan.application)
        checks.append(PreflightCheck("client", "Scope", "Client environment", "passed" if plan.client_id else "failed", "Client selected." if plan.client_id else "No client environment selected.", "Select or register a client."))
        checks.append(PreflightCheck("release", "Artifact", "Verified release", "passed" if plan.release_ids else "failed", f"{len(plan.release_ids)} release selection(s)." if plan.release_ids else "No release selected.", "Publish and select the required component release."))
        repository_missing = [profile.name for profile in profiles if not plan.repositories.get(profile.key)]
        checks.append(PreflightCheck("repositories", "Artifact", "Source repositories", "failed" if repository_missing else "passed", "Missing: " + ", ".join(repository_missing) if repository_missing else "Repository references are configured.", "Configure repositories in Manager Settings."))
        missing_secrets = sorted({secret for profile in profiles for secret in profile.required_secrets if not plan.environment_variables.get(secret)})
        checks.append(PreflightCheck("secrets", "Database & secrets", "Application secrets", "failed" if missing_secrets else "passed", "Missing: " + ", ".join(missing_secrets) if missing_secrets else "Required secret names are present.", "Provide all profile secrets; values remain request-scoped and are not audited."))
        database_values = [value for key, value in plan.environment_variables.items() if "SUPABASE" in key or "DATABASE_URL" in key]
        checks.append(PreflightCheck("database", "Database & secrets", "Database configuration", "passed" if database_values else "failed", "Database configuration supplied." if database_values else "No database source was supplied.", "Provide the client Supabase/PostgreSQL configuration."))
        region = str(plan.provider_config.get("region", "")).lower()
        checks.append(PreflightCheck("region", "Cloud provider", "Target region", "passed" if region in self.SUPPORTED_REGIONS else "failed", f"Region: {region or 'not selected'}", "Choose an available supported region."))
        if plan.provider == "digitalocean":
            image = str(plan.provider_config.get("image", "ubuntu-24-04-x64"))
            checks.append(PreflightCheck("os", "Server hardware", "Operating system", "passed" if image in self.SUPPORTED_DO_IMAGES else "failed", image, "Use Ubuntu 22.04 or 24.04 LTS."))
            size = str(plan.provider_config.get("instance_size", ""))
            adequate = size not in {"", "s-1vcpu-512mb", "s-1vcpu-1gb", "s-1vcpu-2gb"}
            checks.append(PreflightCheck("capacity", "Server hardware", "Minimum capacity", "passed" if adequate else "failed", size or "No Droplet size selected.", "Use at least 2 vCPU and 4 GB RAM."))
            ssl = bool(plan.provider_config.get("ssl", True));domain = str(plan.provider_config.get("domain", ""))
            checks.append(PreflightCheck("domain", "Network & DNS", "Domain for SSL", "passed" if (not ssl or self._domain(domain)) else "failed", domain or "SSL disabled; no domain required.", "Provide a valid FQDN before requesting Let's Encrypt."))
        else:
            owner = plan.provider_config.get("owner_id")
            checks.append(PreflightCheck("workspace", "Cloud provider", "Render workspace", "passed" if owner else "failed", "Workspace selected." if owner else "Render workspace ID is missing.", "Select the Render workspace that owns the service."))
        return checks

    @staticmethod
    def _domain(value: str) -> bool:
        parsed = urlparse(value if "://" in value else f"https://{value}")
        return bool(parsed.hostname and "." in parsed.hostname)

    @staticmethod
    def approved(checks: list[PreflightCheck]) -> bool:
        return not any(check.status == "failed" for check in checks)
