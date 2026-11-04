from __future__ import annotations

from typing import Any

from .base import DeploymentAdapter
from ..profiles import ApplicationProfile


class DigitalOceanDeploymentAdapter(DeploymentAdapter):
    provider = "digitalocean"
    base_url = "https://api.digitalocean.com/v2"

    def validate(self) -> dict[str, Any]:
        account = self.request("GET", f"{self.base_url}/account")
        regions = self.request("GET", f"{self.base_url}/regions?per_page=200")
        sizes = self.request("GET", f"{self.base_url}/sizes?per_page=200")
        return {"authenticated": account.get("account", {}).get("status") == "active", "regions": regions.get("regions", []), "sizes": sizes.get("sizes", [])}

    def provision(self, profile: ApplicationProfile, config: dict[str, Any], repository: str, environment: dict[str, str]) -> dict[str, Any]:
        user_data = self.cloud_init(profile, repository, environment, config)
        payload = {
            "name": config["service_name"], "region": config["region"],
            "size": config.get("instance_size", "s-2vcpu-4gb"), "image": config.get("image", "ubuntu-24-04-x64"),
            "ssh_keys": config.get("ssh_keys", []), "backups": True, "ipv6": True, "monitoring": True,
            "tags": ["vtab", profile.key], "user_data": user_data,
        }
        response = self.request("POST", f"{self.base_url}/droplets", payload)
        droplet = response.get("droplet", {})
        return {"resource_id": str(droplet.get("id", "")), "url": None, "dashboard_url": f"https://cloud.digitalocean.com/droplets/{droplet.get('id')}", "raw_status": droplet.get("status", "accepted")}

    def update_environment(self,resource_id:str,environment:dict[str,str])->dict[str,Any]:
        # Self-hosted configuration is applied by the authenticated outbound
        # connector using an atomic environment-file swap, never by cloud-init.
        return {"resource_id":resource_id,"status":"connector_sync_required","keys":sorted(environment)}

    @staticmethod
    def cloud_init(profile: ApplicationProfile, repository: str, environment: dict[str, str], config: dict[str, Any]) -> str:
        secret_names = ", ".join(sorted(environment))
        return f"""#cloud-config
users:
  - name: vtab
    groups: [sudo]
    shell: /bin/bash
package_update: true
packages: [nginx, ufw, python3, python3-venv, python3-pip, build-essential, git, curl]
write_files:
  - path: /opt/vtab/deployment-receipt.yml
    permissions: '0600'
    content: |
      component: {profile.key}
      repository: {repository}
      port: {profile.port}
      required_secret_names: {secret_names}
runcmd:
  - [bash, -lc, 'ufw allow 22 && ufw allow 80 && ufw allow 443 && ufw --force enable']
  - [bash, -lc, 'mkdir -p /opt/vtab && chown -R vtab:vtab /opt/vtab']
final_message: 'VTAB infrastructure bootstrap accepted; the outbound connector retrieves protected values from the client vault and completes installation.'
"""
