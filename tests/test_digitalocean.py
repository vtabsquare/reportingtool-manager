from __future__ import annotations

import io
import json
import unittest
import urllib.error
import uuid
from pathlib import Path
from unittest.mock import MagicMock, patch

from manager.database import Database
from manager.engine.adapters.digitalocean import DigitalOceanDeploymentAdapter
from manager.engine.engine import DeploymentEngine
from manager.engine.profiles import application_profiles
from manager.engine.validator import DeploymentValidator
from manager.contracts import WizardPreflightRequest
from manager import main


class DigitalOceanAdapterTests(unittest.TestCase):
    def setUp(self):
        self.token = "dop_v1_mock_test_token_12345"
        self.adapter = DigitalOceanDeploymentAdapter(self.token)

    @patch("urllib.request.urlopen")
    def test_validate_success(self, mock_urlopen):
        # Mock responses for account, regions, sizes
        account_resp = io.BytesIO(json.dumps({"account": {"status": "active", "email": "test@vtab.com"}}).encode())
        regions_resp = io.BytesIO(json.dumps({"regions": [{"slug": "blr1", "name": "Bangalore 1", "available": True}, {"slug": "nyc1", "name": "New York 1", "available": True}]}).encode())
        sizes_resp = io.BytesIO(json.dumps({"sizes": [{"slug": "s-2vcpu-4gb", "memory": 4096, "vcpus": 2, "available": True}]}).encode())

        mock_urlopen.side_effect = [account_resp, regions_resp, sizes_resp]

        result = self.adapter.validate()
        self.assertTrue(result["authenticated"])
        self.assertEqual(len(result["regions"]), 2)
        self.assertEqual(result["regions"][0]["slug"], "blr1")
        self.assertEqual(len(result["sizes"]), 1)
        self.assertEqual(result["sizes"][0]["slug"], "s-2vcpu-4gb")

    @patch("urllib.request.urlopen")
    def test_validate_inactive_account(self, mock_urlopen):
        account_resp = io.BytesIO(json.dumps({"account": {"status": "locked"}}).encode())
        regions_resp = io.BytesIO(json.dumps({"regions": []}).encode())
        sizes_resp = io.BytesIO(json.dumps({"sizes": []}).encode())
        mock_urlopen.side_effect = [account_resp, regions_resp, sizes_resp]

        result = self.adapter.validate()
        self.assertFalse(result["authenticated"])

    def test_cloud_init_services(self):
        profile = application_profiles["services"]
        cloud_init = self.adapter.cloud_init(
            profile,
            "https://github.com/vtab/reporting-services",
            {"VITE_SUPABASE_URL": "https://example.supabase.co", "SUPABASE_SERVICE_ROLE_KEY": "key"},
            {"domain": "reports.vtabsquare.com", "region": "blr1"},
        )
        self.assertIn("#cloud-config", cloud_init)
        self.assertIn("component: services", cloud_init)
        self.assertIn("port: 8820", cloud_init)
        self.assertIn("server_name reports.vtabsquare.com;", cloud_init)
        self.assertIn("proxy_pass http://127.0.0.1:8820;", cloud_init)
        self.assertIn("ufw allow 80/tcp", cloud_init)
        self.assertIn("ufw allow 443/tcp", cloud_init)

    def test_cloud_init_administration(self):
        profile = application_profiles["administration"]
        cloud_init = self.adapter.cloud_init(
            profile,
            "https://github.com/vtab/vtab-admin-console",
            {"SUPABASE_URL": "https://example.supabase.co", "ADMIN_EMAIL": "admin@example.com"},
            {"domain": "admin.vtabsquare.com", "region": "blr1"},
        )
        self.assertIn("#cloud-config", cloud_init)
        self.assertIn("component: administration", cloud_init)
        self.assertIn("port: 8000", cloud_init)
        self.assertIn("server_name admin.vtabsquare.com;", cloud_init)
        self.assertIn("proxy_pass http://127.0.0.1:8000;", cloud_init)

    @patch("urllib.request.urlopen")
    def test_provision_droplet_payload(self, mock_urlopen):
        profile = application_profiles["services"]
        droplet_resp = io.BytesIO(json.dumps({
            "droplet": {
                "id": 987654321,
                "name": "vtab-services-blr",
                "status": "new",
                "region": {"slug": "blr1"},
                "size_slug": "s-2vcpu-4gb",
            }
        }).encode())
        mock_urlopen.return_value = droplet_resp

        config = {
            "service_name": "vtab-services-blr",
            "region": "blr1",
            "instance_size": "s-2vcpu-4gb",
            "image": "ubuntu-24-04-x64",
            "domain": "reports.vtabsquare.com",
            "ssl": True,
        }
        res = self.adapter.provision(profile, config, "https://github.com/vtab/reporting-services", {"VITE_SUPABASE_URL": "x"})
        self.assertEqual(res["resource_id"], "987654321")
        self.assertEqual(res["url"], "https://reports.vtabsquare.com")
        self.assertIn("cloud.digitalocean.com/droplets/987654321", res["dashboard_url"])

        # Check payload passed to urlopen
        called_req = mock_urlopen.call_args[0][0]
        body = json.loads(called_req.data.decode("utf-8"))
        self.assertEqual(body["name"], "vtab-services-blr")
        self.assertEqual(body["region"], "blr1")
        self.assertEqual(body["size"], "s-2vcpu-4gb")
        self.assertEqual(body["image"], "ubuntu-24-04-x64")
        self.assertIn("vtab", body["tags"])
        self.assertIn("services", body["tags"])

    @patch("urllib.request.urlopen")
    def test_get_droplet_ip(self, mock_urlopen):
        droplet_resp = io.BytesIO(json.dumps({
            "droplet": {
                "id": 12345,
                "networks": {
                    "v4": [
                        {"ip_address": "10.0.0.1", "type": "private"},
                        {"ip_address": "159.65.1.100", "type": "public"},
                    ]
                }
            }
        }).encode())
        mock_urlopen.return_value = droplet_resp
        ip = self.adapter.get_droplet_ip(12345)
        self.assertEqual(ip, "159.65.1.100")


class DigitalOceanValidationTests(unittest.TestCase):
    def setUp(self):
        self.validator = DeploymentValidator()

    def test_digitalocean_static_checks(self):
        plan = MagicMock()
        plan.client_id = "client-1"
        plan.application = "services"
        plan.provider = "digitalocean"
        plan.release_ids = {"services": "rel-1"}
        plan.repositories = {"services": "https://github.com/vtab/reporting-services"}
        plan.environment_variables = {
            "VITE_SUPABASE_URL": "https://example.supabase.co",
            "SUPABASE_SERVICE_ROLE_KEY": "secret",
            "VTAB_WEB_URL": "https://reports.client.com",
        }
        plan.provider_config = {
            "region": "blr1",
            "instance_size": "s-2vcpu-4gb",
            "image": "ubuntu-24-04-x64",
            "domain": "reports.client.com",
            "ssl": True,
        }

        checks = self.validator.validate_static(plan)
        passed = {c.key: c.status for c in checks}
        self.assertEqual(passed["os"], "passed")
        self.assertEqual(passed["capacity"], "passed")
        self.assertEqual(passed["region"], "passed")
        self.assertEqual(passed["domain"], "passed")
        self.assertEqual(passed["secrets"], "passed")
        self.assertTrue(self.validator.approved(checks))

    def test_digitalocean_invalid_size_rejected(self):
        plan = MagicMock()
        plan.client_id = "client-1"
        plan.application = "services"
        plan.provider = "digitalocean"
        plan.release_ids = {"services": "rel-1"}
        plan.repositories = {"services": "https://github.com/vtab/reporting-services"}
        plan.environment_variables = {
            "VITE_SUPABASE_URL": "https://example.supabase.co",
            "SUPABASE_SERVICE_ROLE_KEY": "secret",
            "VTAB_WEB_URL": "https://reports.client.com",
        }
        plan.provider_config = {
            "region": "blr1",
            "instance_size": "s-1vcpu-1gb",  # Below requirement
            "image": "ubuntu-24-04-x64",
            "domain": "reports.client.com",
            "ssl": True,
        }

        checks = self.validator.validate_static(plan)
        capacity_check = next(c for c in checks if c.key == "capacity")
        self.assertEqual(capacity_check.status, "failed")
        self.assertFalse(self.validator.approved(checks))


class DigitalOceanPreflightIntegrationTests(unittest.TestCase):
    def setUp(self):
        runtime = Path(__file__).resolve().parent / "_runtime"
        runtime.mkdir(parents=True, exist_ok=True)
        self.database_path = runtime / f"manager-do-{uuid.uuid4().hex}.db"
        self.db = Database(self.database_path)
        self.db.initialize()
        self.engine = DeploymentEngine(self.db)

    def tearDown(self):
        if self.database_path.exists():
            self.database_path.unlink()

    @patch("urllib.request.urlopen")
    def test_preflight_digitalocean_end_to_end(self, mock_urlopen):
        # Register a release in the test DB
        self.db.execute(
            """INSERT INTO releases(id, component, version, channel, status, artifact_url, checksum_sha256,
               validation_status, repository_url, git_commit_sha, created_at)
               VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            ("rel-serv-1", "services", "6.2.0", "stable", "available", "https://example.com/art.zip",
             "a" * 64, "approved", "https://github.com/vtab/reporting-services", "1" * 40, "2026-11-04T12:00:00Z")
        )

        # Mock DO API calls for verify_provider=True
        account_resp = io.BytesIO(json.dumps({"account": {"status": "active"}}).encode())
        regions_resp = io.BytesIO(json.dumps({"regions": [{"slug": "blr1", "available": True}]}).encode())
        sizes_resp = io.BytesIO(json.dumps({"sizes": [{"slug": "s-2vcpu-4gb", "available": True}]}).encode())
        mock_urlopen.side_effect = [account_resp, regions_resp, sizes_resp]

        req = WizardPreflightRequest(
            client_id="client-test-do",
            application="services",
            provider="digitalocean",
            release_ids={"services": "rel-serv-1"},
            repositories={"services": "https://github.com/vtab/reporting-services"},
            provider_config={
                "region": "blr1",
                "instance_size": "s-2vcpu-4gb",
                "image": "ubuntu-24-04-x64",
                "domain": "reports.vtabsquare.com",
                "ssl": True,
            },
            environment_variables={
                "VITE_SUPABASE_URL": "https://example.supabase.co",
                "SUPABASE_SERVICE_ROLE_KEY": "test-key",
                "VTAB_WEB_URL": "https://reports.vtabsquare.com",
            },
            verify_provider=True,
        )

        res = self.engine.preflight(req, provider_token="dop_v1_mock_token", verify_provider=True)
        self.assertTrue(res["approved"])
        keys = {c["key"]: c["status"] for c in res["checks"]}
        self.assertEqual(keys["provider_auth"], "passed")
        self.assertEqual(keys["provider_region_quota"], "passed")
        self.assertEqual(keys["provider_size_quota"], "passed")
        self.assertEqual(keys["release_services"], "passed")

    @patch("subprocess.run")
    def test_ssh_connection_mock(self, mock_run):
        mock_proc = MagicMock()
        mock_proc.returncode = 0
        mock_proc.stdout = "Linux 6.8.0-142-generic x86_64\nup 27 days\n/dev/vda1 24G 20G 3.2G 87% /\nDocker version 27.0.3"
        mock_proc.stderr = ""
        mock_run.return_value = mock_proc

        result = DigitalOceanDeploymentAdapter.test_ssh_connection("139.59.32.39", 22, "root")
        self.assertTrue(result["authenticated"])
        self.assertIn("139.59.32.39", result["message"])
        self.assertEqual(result["details"]["disk_available"], "3.2G")

    @patch("subprocess.run")
    def test_provision_with_ssh_host(self, mock_run):
        mock_proc = MagicMock()
        mock_proc.returncode = 0
        mock_proc.stdout = "Linux 6.8.0\nup 1 day\n/dev/vda1 24G 20G 3.2G 87% /\nDocker version 27"
        mock_proc.stderr = ""
        mock_run.return_value = mock_proc

        profile = application_profiles["services"]
        adapter = DigitalOceanDeploymentAdapter("dop_v1_mock")
        config = {
            "host": "139.59.32.39",
            "ssh_port": 22,
            "ssh_user": "root",
            "target_dir": "/var/www/reportingtool-v2-services",
            "domain": "v2-reports.139.59.32.39.nip.io",
            "approved_commit_sha": "a" * 40,
        }
        res = adapter.provision(profile, config, "https://github.com/vtab/repo", {"PORT": "8825"})
        self.assertEqual(res["raw_status"], "deployed")
        self.assertEqual(res["deploy_type"], "ssh_direct")
        self.assertEqual(res["host"], "139.59.32.39")
        self.assertEqual(res["url"], "http://v2-reports.139.59.32.39.nip.io")

    @patch("subprocess.run")
    def test_operator_test_connection_endpoint(self, mock_run):
        mock_proc = MagicMock()
        mock_proc.returncode = 0
        mock_proc.stdout = "Linux 6.8.0\nup 2 days\n/dev/vda1 24G 20G 3.2G 87% /\nDocker version 27"
        mock_proc.stderr = ""
        mock_run.return_value = mock_proc

        from manager.contracts import ConnectionTestRequest
        req = ConnectionTestRequest(
            provider="digitalocean",
            host="139.59.32.39",
            ssh_port=22,
            ssh_user="root"
        )
        res = main.operator_test_connection(req, "operator")
        self.assertTrue(res["success"])
        self.assertEqual(res["type"], "ssh")
        self.assertEqual(res["host"], "139.59.32.39")
        self.assertIn("139.59.32.39", res["message"])


if __name__ == "__main__":
    unittest.main()
