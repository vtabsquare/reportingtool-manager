from __future__ import annotations

import json
from typing import Any

from .adapters import DigitalOceanDeploymentAdapter, RenderDeploymentAdapter
from .profiles import selected_profiles
from .rollback import RollbackManager
from .validator import DeploymentValidator, PreflightCheck
from ..configuration import encrypt_environment


EXECUTION_STAGES = [
    "initializing", "provisioning_infrastructure", "preparing_runtime", "fetching_artifact",
    "installing_dependencies", "building_frontend", "configuring_web_server",
    "starting_backend", "health_check", "finalizing_receipt",
]


class DeploymentEngine:
    def __init__(self, database: Any):
        self.db = database
        self.validator = DeploymentValidator()

    def public_plan(self, plan: Any) -> dict[str, Any]:
        return {"profiles": [profile.public() for profile in selected_profiles(plan.application)], "stages": EXECUTION_STAGES, "provider": plan.provider}

    def preflight(self, plan: Any, provider_token: str = "", verify_provider: bool = False) -> dict[str, Any]:
        checks = self.validator.validate_static(plan)
        for profile in selected_profiles(plan.application):
            release_id = plan.release_ids.get(profile.key)
            release = None
            if release_id:
                release = self.db.one("SELECT * FROM releases WHERE id=? AND component=? AND status='available' AND validation_status='approved'", (release_id, profile.key))
            if not release:
                release = self.db.one("SELECT * FROM releases WHERE component=? AND status='available' AND validation_status='approved' ORDER BY created_at DESC LIMIT 1", (profile.key,))
            checksum_ok = bool(release and len(str(release.get("checksum_sha256", ""))) == 64 and len(str(release.get("git_commit_sha", "")))==40 and release.get("repository_url"))
            checks.append(PreflightCheck(
                f"release_{profile.key}", "Artifact", f"{profile.name} release integrity",
                "passed" if checksum_ok else "failed",
                f"v{release['version']} is published with a SHA-256 manifest." if checksum_ok else "A matching published release with a valid checksum was not found.",
                f"Publish and select a verified {profile.name} release.",
            ))
            repository = str(plan.repositories.get(profile.key, ""))
            repository_ok = repository.startswith("https://github.com/") and len(repository.rstrip("/").split("/")) >= 5
            checks.append(PreflightCheck(
                f"repository_{profile.key}", "Artifact", f"{profile.name} repository",
                "passed" if repository_ok else "failed", repository or "Repository is not configured.",
                "Lock the application repository in Manager Settings.",
            ))
            checks.append(PreflightCheck(
                f"runtime_{profile.key}", "Runtime & tools", f"{profile.name} application profile", "passed",
                f"Python {profile.python_version}; Node {profile.node_version}; port {profile.port}; health {profile.health_path}.",
                "Application profiles are allow-listed and cannot execute arbitrary commands.",
            ))
        ports = [profile.port for profile in selected_profiles(plan.application)]
        checks.append(PreflightCheck(
            "ports", "Network & DNS", "Application port allocation", "passed" if len(ports) == len(set(ports)) else "failed",
            "Reserved ports: " + ", ".join(map(str, ports)), "Resolve profile port conflicts before deployment.",
        ))
        checks.append(PreflightCheck(
            "rollback", "Recovery", "Rollback metadata", "passed", "Previous-version and snapshot references will be recorded before provisioning.",
            "The client connector must confirm its local snapshot before an update is activated.",
        ))
        if verify_provider:
            has_ssh_host = plan.provider == "digitalocean" and bool(plan.provider_config.get("host"))
            if has_ssh_host:
                host = str(plan.provider_config.get("host")).strip()
                port = int(plan.provider_config.get("ssh_port") or 22)
                user = str(plan.provider_config.get("ssh_user") or "root").strip()
                ssh_res = DigitalOceanDeploymentAdapter.test_ssh_connection(host, port, user)
                auth_ok = bool(ssh_res.get("authenticated"))
                checks.append(PreflightCheck(
                    "provider_ssh", "Infrastructure target", "Target server SSH connection",
                    "passed" if auth_ok else "failed",
                    ssh_res.get("message", ""),
                    "Ensure target server IP is reachable and SSH credentials are correct." if not auth_ok else ""
                ))
            if not provider_token:
                if not has_ssh_host:
                    checks.append(PreflightCheck("provider_auth", "Cloud provider", "API authentication", "failed", "No provider token supplied.", "Supply a scoped provider token for this preflight request."))
            else:
                try:
                    result = self.adapter(plan.provider, provider_token).validate(host=plan.provider_config.get("host"))
                    authenticated = bool(result.get("authenticated", True))
                    checks.append(PreflightCheck("provider_auth", "Cloud provider", "API authentication", "passed" if authenticated else "failed", "Provider API token verified." if authenticated else "Provider account is not active.", "Use a token with the required scopes."))
                    if plan.provider == "digitalocean" and authenticated and not has_ssh_host:
                        region = str(plan.provider_config.get("region", ""))
                        size = str(plan.provider_config.get("instance_size", ""))
                        region_ok = any(item.get("slug") == region and item.get("available", True) for item in result.get("regions", []))
                        size_ok = any(item.get("slug") == size and item.get("available", True) for item in result.get("sizes", []))
                        checks.append(PreflightCheck("provider_region_quota", "Cloud provider", "Region availability", "passed" if region_ok else "failed", f"{region} is available." if region_ok else f"{region} is unavailable for this account.", "Choose an available region."))
                        checks.append(PreflightCheck("provider_size_quota", "Cloud provider", "Droplet size availability", "passed" if size_ok else "failed", f"{size} is available." if size_ok else f"{size} is unavailable for this account or region.", "Choose an available size with at least 2 vCPU and 4 GB RAM."))
                except Exception as error:
                    checks.append(PreflightCheck("provider_auth", "Cloud provider", "API authentication", "failed", str(error), "Verify token scope, account status, and outbound HTTPS access."))
        return {"approved": self.validator.approved(checks), "checks": [check.public() for check in checks], **self.public_plan(plan)}

    def execute(self, deployment_id: str, plan: Any, provider_token: str, component: str, release: dict[str, Any], repository: str) -> dict[str, Any]:
        profile = selected_profiles(component)[0]
        adapter = self.adapter(plan.provider, provider_token)
        previous = self.db.one("SELECT previous_version,client_id FROM deployments WHERE id=?", (deployment_id,)) or {}
        rollback_ref = RollbackManager.snapshot_reference(previous.get("client_id", plan.client_id), component, previous.get("previous_version"))
        self.db.execute("UPDATE deployments SET status='preflight',stage=?,progress_percent=?,rollback_snapshot_ref=?,updated_at=? WHERE id=?", (EXECUTION_STAGES[0], 5, rollback_ref, self.now(), deployment_id))
        self.log(deployment_id, EXECUTION_STAGES[0], "info", "Deployment engine accepted the confirmed plan.")
        try:
            self.db.execute("UPDATE deployments SET status='deploying',stage=?,progress_percent=?,updated_at=? WHERE id=?", (EXECUTION_STAGES[1], 15, self.now(), deployment_id))
            provider_config = {**plan.provider_config, "approved_commit_sha": release.get("git_commit_sha")}
            component_env = dict(plan.environment_variables)
            if plan.application == "both":
                if component == "services":
                    if not provider_config.get("target_dir") or provider_config.get("target_dir") == "/var/www/reportingtool-admin":
                        provider_config["target_dir"] = "/var/www/reportingtool-webservice"
                    if not provider_config.get("domain") or provider_config.get("domain") == "admin-reporting.vtabsquare.com":
                        provider_config["domain"] = "reportservices.vtabsquare.com"
                        provider_config["ssl"] = True
                    if not provider_config.get("service_name") or provider_config.get("service_name") == "reportingtool-admin":
                        provider_config["service_name"] = "vtab-reporting-v2"
                    component_env["PORT"] = str(profile.port)
                else:
                    if not provider_config.get("target_dir") or provider_config.get("target_dir") == "/var/www/reportingtool-webservice":
                        provider_config["target_dir"] = "/var/www/reportingtool-admin"
                    if not provider_config.get("domain") or provider_config.get("domain") == "reportservices.vtabsquare.com":
                        provider_config["domain"] = "admin-reporting.vtabsquare.com"
                        provider_config["ssl"] = True
                    if not provider_config.get("service_name") or provider_config.get("service_name") == "vtab-reporting-v2":
                        provider_config["service_name"] = "reportingtool-admin"
                    component_env["PORT"] = str(profile.port)
            resource = adapter.provision(profile, provider_config, repository, component_env)
            if plan.provider=="render":
                exact=adapter.deploy_exact(str(resource.get("resource_id") or ""),str(release.get("git_commit_sha") or ""))
                resource={**resource,**exact}
            self.log(deployment_id, EXECUTION_STAGES[1], "info", f"{plan.provider} accepted resource {resource.get('resource_id') or 'pending'}.")
            infrastructure_id = self.identifier()
            self.db.execute(
                """INSERT INTO client_infrastructure(id,client_id,provider,component,provider_resource_id,region,instance_size,domain,ip_address,credentials_ref,config_json,environment_variables_json,config_version,sync_status,config_updated_at,config_updated_by,status,created_at,updated_at)
                   VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (infrastructure_id, plan.client_id, plan.provider, component, resource.get("resource_id"), provider_config.get("region"), provider_config.get("instance_size"), provider_config.get("domain"), None, "provider-vault", json.dumps(provider_config), encrypt_environment(component_env), 1, "provider_pending", self.now(), "deployment-engine", "provisioning", self.now(), self.now()),
            )
            self.db.execute("INSERT INTO client_configuration_versions(infrastructure_id,client_id,version,config_json,environment_variables_json,sync_status,changed_by,change_summary,created_at) VALUES(?,?,?,?,?,?,?,?,?)",(infrastructure_id,plan.client_id,1,json.dumps(provider_config),encrypt_environment(component_env),"provider_pending","deployment-engine","Initial deployment configuration",self.now()))
            if resource.get("provider_deploy_id"):
                self.db.execute("UPDATE deployments SET provider_deploy_id=? WHERE id=?",(resource["provider_deploy_id"],deployment_id))
            for index, stage in enumerate(EXECUTION_STAGES[2:8], start=2):
                progress = 20 + index * 8
                self.db.execute("UPDATE deployments SET stage=?,progress_percent=?,updated_at=? WHERE id=?", (stage, progress, self.now(), deployment_id))
                self.log(deployment_id, stage, "info", "Stage delegated to the provider build or outbound VTAB connector.")
            self.db.execute("UPDATE deployments SET stage='health_check',progress_percent=85,agent_notes=?,updated_at=? WHERE id=?", (json.dumps({"provider": resource}), self.now(), deployment_id))
            self.log(deployment_id, "health_check", "info", "Waiting for provider health verification or connector report.")
            url = resource.get("url")
            if url:
                from .health import HealthCheckService
                import time
                health_checker = HealthCheckService()
                for _ in range(60):
                    time.sleep(5)
                    if resource.get("deploy_type") == "ssh_direct" and resource.get("host"):
                        host = resource.get("host")
                        ssh_user = provider_config.get("ssh_user") or "root"
                        ssh_port = int(provider_config.get("ssh_port") or 22)
                        key_data = provider_config.get("ssh_key") or provider_config.get("ssh_key_data")
                        port = profile.port
                        ssh_code, ssh_out, _ = DigitalOceanDeploymentAdapter.run_ssh_command(
                            host, ssh_port, ssh_user,
                            f"curl -s -f http://127.0.0.1:{port}{profile.health_path}",
                            key_data=key_data,
                            timeout=5
                        )
                        if ssh_code == 0:
                            check = {"healthy": True, "status": "ok"}
                        else:
                            check = {"healthy": False, "status": "unhealthy"}
                    else:
                        check = health_checker.check(url, profile.health_path, timeout=5)
                    if check.get("healthy"):
                        now = self.now()
                        self.db.execute(
                            """UPDATE deployments SET status='succeeded',stage='verified_healthy',progress_percent=100,
                               health_verified_at=?,finished_at=?,updated_at=? WHERE id=?""",
                            (now, now, now, deployment_id)
                        )
                        column = "installed_services_version" if component == "services" else "installed_admin_version"
                        url_column = "services_url" if component == "services" else "admin_url"
                        self.db.execute(
                            f"UPDATE clients SET {column}=?,{url_column}=?,last_health_status='healthy',last_health_at=?,updated_at=? WHERE id=?",
                            (release["version"], url, now, now, plan.client_id)
                        )
                        self.db.execute(
                            "UPDATE client_infrastructure SET status='active',domain=?,sync_status='in_sync',last_synced_at=?,updated_at=? WHERE client_id=? AND component=?",
                            (url, now, now, plan.client_id, component)
                        )
                        self.db.execute("DELETE FROM deployment_locks WHERE deployment_id=?", (deployment_id,))
                        self.log(deployment_id, "verified_healthy", "info", f"Verified provider health at {url}. Deployment succeeded.")
                        return {"deploymentId": deployment_id, "status": "succeeded", "progress": 100, "providerResource": resource, "rollbackSnapshot": rollback_ref}
            return {"deploymentId": deployment_id, "status": "deploying", "progress": 85, "providerResource": resource, "rollbackSnapshot": rollback_ref}
        except Exception as error:
            self.db.execute("UPDATE deployments SET status='failed',stage='provisioning_infrastructure',error_message=?,progress_percent=15,updated_at=? WHERE id=?", (str(error)[:2000], self.now(), deployment_id))
            self.log(deployment_id, "provisioning_infrastructure", "error", str(error)[:2000])
            self.db.execute("DELETE FROM deployment_locks WHERE deployment_id=?",(deployment_id,))
            raise

    def log(self, deployment_id: str, stage: str, level: str, message: str) -> None:
        self.db.execute("INSERT INTO deployment_logs(deployment_id,stage,level,message,created_at) VALUES(?,?,?,?,?)", (deployment_id, stage, level, message, self.now()))

    @staticmethod
    def adapter(provider: str, token: str):
        return RenderDeploymentAdapter(token) if provider == "render" else DigitalOceanDeploymentAdapter(token)

    @staticmethod
    def now() -> str:
        from ..database import utc_now
        return utc_now()

    @staticmethod
    def identifier() -> str:
        import uuid
        return str(uuid.uuid4())
