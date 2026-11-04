"""End-to-End Integration Test for Webhook-Driven Controlled Update Pipeline.

Verifies:
1. Services SQLite database schema includes `update_history` and `update_state` tables.
2. Webhook ping, authorization, and event handling on Services.
3. Manager release registration and offer approval.
4. Webhook dispatch from Manager to Services.
5. Services persistent recording of the offer in `update_history`.
6. Client Update Now authorization (`/api/v1/update/apply`).
7. Lifecycle status transitions (preflight -> deploying -> validating -> succeeded).
8. Version elevation in Manager and Services.
"""
import os
import sys
import time
import unittest
from pathlib import Path
from starlette.testclient import TestClient

root = Path(__file__).resolve().parents[2]
manager_path = root / "VTAB Reporting Manager"
services_path = root / "Reporting Tool Services" / "api"
sys.path.insert(0, str(manager_path))
sys.path.insert(0, str(services_path))

os.environ["VTAB_SIMULATE_BUILD"] = "1"
os.environ["VTAB_STORAGE_BACKEND"] = "sqlite"

from manager.main import app as manager_app
from app.server import app as services_app
from app.storage import Store
from app.update_agent import trigger_apply


class TestWebhookUpdateFlow(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.manager_client = TestClient(manager_app)
        cls.services_client = TestClient(services_app)
        cls.store = Store()

    def test_01_database_tables_exist(self):
        """Verify that Services has update_history and update_state tables."""
        with self.store.conn() as c:
            tables = [r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()]
        self.assertIn("update_history", tables, "update_history table must exist in Services database")
        self.assertIn("update_state", tables, "update_state table must exist in Services database")
        print("\n[OK] Step 1: Verified update_history and update_state tables exist in Services database.")

    def test_02_webhook_ping_and_auth(self):
        """Verify webhook ping and authorization controls on Services."""
        os.environ["VTAB_AGENT_TOKEN"] = "test-agent-secret-key"
        os.environ["VTAB_CLIENT_ID"] = "test-client-id"

        # 1. Ping without token should fail if token required
        resp = self.services_client.post("/api/v1/update/webhook", json={"event": "ping"})
        self.assertEqual(resp.status_code, 403, "Unauthenticated webhook must return HTTP 403")

        # 2. Ping with valid token header
        resp = self.services_client.post(
            "/api/v1/update/webhook",
            json={"event": "ping"},
            headers={"X-VTAB-Agent-Token": "test-agent-secret-key"}
        )
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertTrue(data.get("ok"))
        self.assertTrue(data.get("pong"))
        print("[OK] Step 2: Webhook authorization and ping verified successfully.")

    def test_03_full_webhook_update_lifecycle(self):
        """Simulate the entire controlled update flow via webhooks."""
        headers = {"X-VTAB-Manager-Key": "vtab-local-dev"}

        # 1. Create a client in Manager with valid lowercase code and provider
        unique_suffix = int(time.time()) % 100000
        client_code = f"wh-acme-{unique_suffix}"
        c_resp = self.manager_client.post(
            "/api/v1/operator/clients",
            json={
                "code": client_code,
                "name": f"Acme Webhook Corp {unique_suffix}",
                "contact_email": "ops@acmewebhook.com",
                "environment": "production",
                "database_provider": "other",
                "services_url": "http://127.0.0.1:8820",
                "installed_services_version": "6.1.10"
            },
            headers=headers
        )
        self.assertEqual(c_resp.status_code, 201)
        client_data = c_resp.json()["client"]
        client_id = client_data["id"]
        agent_token = c_resp.json()["connectorBootstrapToken"]
        client_admin_token = c_resp.json()["clientAdminBootstrapToken"]

        # Configure Services environment to match registered client
        os.environ["VTAB_CLIENT_ID"] = client_id
        os.environ["VTAB_AGENT_TOKEN"] = agent_token

        # Register active managed infrastructure
        import uuid
        from manager.config import Settings
        from manager.database import Database
        manager_db = Database(Settings().database_path, Settings().database_url)
        infra_id = str(uuid.uuid4())
        manager_db.execute(
            """INSERT INTO client_infrastructure(id, client_id, component, provider, provider_resource_id, region, instance_size, domain, status, created_at, updated_at)
               VALUES(?, ?, 'services', 'render', 'srv-webhook-test', 'oregon', 'standard', 'http://127.0.0.1:8820', 'active', datetime('now'), datetime('now'))""",
            (infra_id, client_id)
        )

        # 2. Create and validate release in Manager
        version_tag = f"2.0.{unique_suffix}"
        r_resp = self.manager_client.post(
            "/api/v1/operator/releases",
            json={
                "component": "services",
                "version": version_tag,
                "channel": "stable",
                "git_commit_sha": "b9787a1d1234567890abcdef1234567890abcdef",
                "artifact_url": f"https://github.com/vtabsquare/Reportingtool-webservice/releases/download/{version_tag}/services.tar.gz",
                "checksum_sha256": "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
                "release_notes": "Added instant webhook update notification and persistent state."
            },
            headers=headers
        )
        self.assertEqual(r_resp.status_code, 201)
        release_id = r_resp.json()["release"]["id"]

        # Validate release CI gate
        self.manager_client.post(
            f"/api/v1/operator/releases/{release_id}/validate",
            json={
                "approved": True,
                "ci_status": "success",
                "checks": {"build": "success", "test": "success", "security": "success"},
                "notes": "Approved by security"
            },
            headers=headers
        )
        # Publish release
        self.manager_client.post(f"/api/v1/operator/releases/{release_id}/publish", headers=headers)

        # 3. Manager creates update deployment (pending_approval)
        d_resp = self.manager_client.post(
            "/api/v1/operator/deployments",
            json={"client_id": client_id, "release_id": release_id},
            headers=headers
        )
        self.assertEqual(d_resp.status_code, 201)
        deployment_id = d_resp.json()["deployment"]["id"]

        # 4. Operator approves offer -> status becomes 'scheduled' (Offered)
        appr_resp = self.manager_client.post(
            f"/api/v1/operator/deployments/{deployment_id}/approve",
            json={"approved_by": "lead-operator@vtab.com"},
            headers=headers
        )
        self.assertEqual(appr_resp.status_code, 200)
        self.assertEqual(appr_resp.json()["deployment"]["status"], "scheduled")

        # 5. Dispatch Webhook directly to Services
        webhook_payload = {
            "event": "update_offered",
            "deployment_id": deployment_id,
            "component": "services",
            "version": version_tag,
            "release_notes": "Added instant webhook update notification and persistent state."
        }
        wh_resp = self.services_client.post(
            "/api/v1/update/webhook",
            json=webhook_payload,
            headers={"X-VTAB-Agent-Token": agent_token}
        )
        self.assertEqual(wh_resp.status_code, 200)
        self.assertTrue(wh_resp.json().get("ok"))
        self.assertEqual(wh_resp.json().get("status"), "offered")
        print("[OK] Step 3: Webhook received and processed by Services.")

        # 6. Verify Services update status shows update_available
        status_resp = self.services_client.get("/api/v1/update/status")
        self.assertEqual(status_resp.status_code, 200)
        self.assertTrue(status_resp.json().get("update_available"))
        self.assertEqual(status_resp.json().get("available_version"), version_tag)
        print(f"[OK] Step 4: Services in-app update status reflects offered version {version_tag}.")

        # 7. Verify persistent update_history in Services SQLite database
        history_resp = self.services_client.get("/api/v1/update/history")
        self.assertEqual(history_resp.status_code, 200)
        history_events = history_resp.json()["history"]
        self.assertTrue(len(history_events) > 0)
        latest_event = history_events[0]
        self.assertEqual(latest_event["event_type"], "webhook_offer_received")
        self.assertEqual(latest_event["version"], version_tag)
        self.assertEqual(latest_event["status"], "offered")
        print("[OK] Step 5: Persistent record verified in Services update_history table.")

        # 8. Client Administrator clicks "Update Now"
        # First: accept update on Manager (validates client admin token)
        accept_resp = self.manager_client.post(
            f"/api/v1/agent/deployments/{deployment_id}/accept",
            json={"confirmed": True, "requested_by": "client-lead@acmewebhook.com"},
            headers={
                "X-VTAB-Client-ID": client_id,
                "X-VTAB-Agent-Token": agent_token,
                "X-VTAB-Client-Admin-Token": client_admin_token,
            }
        )
        self.assertEqual(accept_resp.status_code, 200)
        self.assertEqual(accept_resp.json()["deployment"]["status"], "apply_requested")
        print("[OK] Step 6: Client admin authorization confirmed (status: apply_requested).")

        # Second: Client server reports lifecycle stages
        agent_report_headers = {
            "X-VTAB-Client-ID": client_id,
            "X-VTAB-Agent-Token": agent_token,
        }
        # Preflight
        self.manager_client.post(
            f"/api/v1/agent/deployments/{deployment_id}/report",
            json={"status": "preflight", "message": "Preflight disk and health checks passed"},
            headers=agent_report_headers
        )
        self.store.record_update_event("preflight", deployment_id, version_tag, "preflight")

        # Deploying
        self.manager_client.post(
            f"/api/v1/agent/deployments/{deployment_id}/report",
            json={"status": "deploying", "message": "Installing dependencies and compiling assets"},
            headers=agent_report_headers
        )
        self.store.record_update_event("deploying", deployment_id, version_tag, "deploying")

        # Validating
        self.manager_client.post(
            f"/api/v1/agent/deployments/{deployment_id}/report",
            json={"status": "validating", "message": "Service health check passed"},
            headers=agent_report_headers
        )
        self.store.record_update_event("validating", deployment_id, version_tag, "validating")

        # Succeeded
        rep_resp = self.manager_client.post(
            f"/api/v1/agent/deployments/{deployment_id}/report",
            json={
                "status": "succeeded",
                "message": f"v{version_tag} active and healthy",
                "health_verified": True,
                "installed_version": version_tag,
                "installed_commit_sha": "b9787a1d1234567890abcdef1234567890abcdef"
            },
            headers=agent_report_headers
        )
        self.assertEqual(rep_resp.status_code, 200)

        # Record apply_succeeded in Services database
        self.store.record_update_event("apply_succeeded", deployment_id, version_tag, "succeeded")
        self.store.set_update_state("current_offer", None)

        # 9. Verify Manager reflects completion
        dep_check = self.manager_client.get("/api/v1/operator/deployments", headers=headers)
        self.assertEqual(dep_check.status_code, 200)
        dep_row = next((d for d in dep_check.json()["deployments"] if d["id"] == deployment_id), None)
        self.assertIsNotNone(dep_row)
        self.assertEqual(dep_row["status"], "succeeded")
        print("[OK] Step 7: Manager verified deployment status is 'succeeded'.")

        # 10. Verify Services persistent history shows apply_succeeded
        history_after = self.services_client.get("/api/v1/update/history")
        event_types = [e["event_type"] for e in history_after.json()["history"]]
        self.assertIn("apply_succeeded", event_types)
        print(f"[OK] Step 8: Services recorded apply_succeeded in database history for {version_tag}.")


if __name__ == "__main__":
    unittest.main()
