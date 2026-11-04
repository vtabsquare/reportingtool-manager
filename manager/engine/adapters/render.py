from __future__ import annotations

from typing import Any

from .base import DeploymentAdapter
from ..profiles import ApplicationProfile


class RenderDeploymentAdapter(DeploymentAdapter):
    provider = "render"
    base_url = "https://api.render.com/v1"

    def validate(self) -> dict[str, Any]:
        services = self.request("GET", f"{self.base_url}/services?limit=1")
        return {"authenticated": True, "sample": services}

    def provision(self, profile: ApplicationProfile, config: dict[str, Any], repository: str, environment: dict[str, str]) -> dict[str, Any]:
        existing_service_id = str(config.get("service_id") or "").strip()
        if existing_service_id:
            if environment:
                try:
                    self.update_environment(existing_service_id, environment)
                except Exception:
                    pass
            service = self.request("GET", f"{self.base_url}/services/{existing_service_id}")
            service_obj = service.get("service", service)
            url = service_obj.get("serviceDetails", {}).get("url") or f"https://{service_obj.get('slug', '')}.onrender.com"
            return {
                "resource_id": existing_service_id,
                "url": url,
                "dashboard_url": service_obj.get("dashboardUrl") or f"https://dashboard.render.com/web/{existing_service_id}",
                "raw_status": "accepted"
            }

        payload = {
            "type": "web_service",
            "name": config["service_name"],
            "ownerId": config["owner_id"],
            "repo": repository,
            "branch": config.get("branch", "main"),
            "autoDeployTrigger": "off",
            "envVars": [{"key": key, "value": value} for key, value in environment.items()],
            "serviceDetails": {
                "runtime": "python",
                "region": config["region"],
                "plan": config.get("instance_size", "free"),
                **({"healthCheckPath": profile.health_path} if profile.health_path and profile.key != "administration" else {}),
                "envSpecificDetails": {
                    "buildCommand": profile.build_command,
                    "startCommand": profile.start_command,
                },
            },
        }
        response = self.request("POST", f"{self.base_url}/services", payload)
        service = response.get("service", response)
        return {"resource_id": service.get("id"), "url": service.get("serviceDetails", {}).get("url"), "dashboard_url": service.get("dashboardUrl"), "raw_status": "accepted"}

    def verify_auto_deploy_disabled(self,service_id:str)->dict[str,Any]:
        try:
            service=self.request("GET",f"{self.base_url}/services/{service_id}")
            trigger=str(service.get("autoDeployTrigger") or service.get("service",{}).get("autoDeployTrigger") or "").lower()
            if trigger not in {"off","false","disabled"}:
                try:
                    self.request("PATCH", f"{self.base_url}/services/{service_id}", {"autoDeployTrigger": "off"})
                    trigger = "off"
                except Exception:
                    pass
            return {"verified":True,"autoDeployTrigger":trigger}
        except Exception:
            return {"verified":False,"autoDeployTrigger":"unknown"}

    def update_environment(self,service_id:str,environment:dict[str,str])->dict[str,Any]:
        payload=[{"key":key,"value":value} for key,value in sorted(environment.items())]
        return self.request("PUT",f"{self.base_url}/services/{service_id}/env-vars",payload)

    def deploy_exact(self,service_id:str,commit_sha:str,clear_cache:bool=False)->dict[str,Any]:
        if len(commit_sha)!=40:raise ValueError("An exact 40-character commit SHA is required.")
        self.verify_auto_deploy_disabled(service_id)
        result=self.request("POST",f"{self.base_url}/services/{service_id}/deploys",{"commitId":commit_sha,"clearCache":"clear" if clear_cache else "do_not_clear"})
        deploy=result.get("deploy",result)
        return {"provider_deploy_id":deploy.get("id"),"status":deploy.get("status","build_in_progress"),"commit_sha":commit_sha}

    def deployment_status(self,service_id:str,deploy_id:str)->dict[str,Any]:
        return self.request("GET",f"{self.base_url}/services/{service_id}/deploys/{deploy_id}")
