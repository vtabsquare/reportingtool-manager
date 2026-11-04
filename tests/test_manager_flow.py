from __future__ import annotations

import unittest
import uuid
from pathlib import Path

from fastapi import HTTPException

from manager.database import Database
from manager import main
from manager.contracts import (
    AgentCheckIn,
    ClientCreate,
    DeploymentApproval,
    DeploymentCreate,
    GitHubSettingsUpdate,
    RepositorySettingsUpdate,
    ReleaseCreate,
    ReleaseValidation,
    TicketEscalation,
    WizardPreflightRequest,
)
from manager.security import OperatorIdentity


class ManagerFlowTests(unittest.TestCase):
    def setUp(self):
        from dataclasses import replace
        runtime = Path(__file__).resolve().parent / "_runtime"
        runtime.mkdir(parents=True, exist_ok=True)
        self.database_path = runtime / f"manager-{uuid.uuid4().hex}.db"
        main.db = Database(self.database_path)
        main.db.initialize()
        self.orig_settings = main.settings
        main.settings = replace(main.settings, github_repository="", github_repository_services="", github_repository_admin="")

    def tearDown(self):
        main.settings = self.orig_settings
        if self.database_path.exists():
            self.database_path.unlink()

    def test_client_release_deployment_and_ticket_flow(self):
        created = main.create_client(
            ClientCreate(name="Example Client", code="example-client", environment="production", database_provider="supabase"),
            actor="test_operator",
        )
        client_id = created["client"]["id"]
        token = created["connectorBootstrapToken"]

        check_in = main.agent_check_in(
            AgentCheckIn(agent_version="0.1.0", services_version="6.1.7", admin_version="1.0.1", health="healthy"),
            x_vtab_client_id=client_id,
            x_vtab_agent_token=token,
        )
        self.assertTrue(check_in["ok"])

        release = main.create_release(
            ReleaseCreate(
                component="services", version="6.1.8", channel="stable",
                artifact_url="https://example.com/releases/services-6.1.8.zip",
                checksum_sha256="a" * 64, release_notes="Compatibility update",
                repository_url="https://github.com/vtab/reporting-services", git_commit_sha="1"*40,
            ),
            actor="test_operator",
        )
        release_id = release["release"]["id"]
        main.validate_release(release_id,ReleaseValidation(approved=True,ci_status="success",checks={"build":"success","test":"success","security":"success"}),actor=OperatorIdentity("test-release-manager","release_manager"))
        main.publish_release(release_id, actor="test_operator")

        main.db.execute("INSERT INTO client_infrastructure(id,client_id,provider,component,status,created_at,updated_at) VALUES(?,?,?,?,?,?,?)",("infra-1",client_id,"render","services","active",main.utc_now(),main.utc_now()))

        deployment = main.create_deployment(
            DeploymentCreate(client_id=client_id, release_id=release_id),
            actor=OperatorIdentity("test_operator","operator"),
        )
        deployment_id = deployment["deployment"]["id"]
        approval = main.approve_deployment(
            deployment_id,
            DeploymentApproval(approved_by="client-admin@example.com"),
            actor=OperatorIdentity("test_operator","operator"),
        )
        self.assertEqual(approval["deployment"]["status"], "scheduled")
        pending = main.agent_next_deployment(x_vtab_client_id=client_id, x_vtab_agent_token=token)
        self.assertIsNone(pending["deployment"])

        ticket = main.agent_escalate_ticket(
            TicketEscalation(
                external_number="SR-1001", title="Chart fails to render",
                summary="The chart shows a controlled rendering error.", severity="high", component="services",
                client_approved=True, approved_by="client-admin@example.com",
                diagnostics={"error": "renderer timeout", "version": "6.1.7", "rows": [{"private": "removed"}], "credentials": "removed"},
            ),
            x_vtab_client_id=client_id,
            x_vtab_agent_token=token,
        )
        diagnostics = ticket["ticket"]["diagnostics"]
        self.assertNotIn("rows", diagnostics)
        self.assertNotIn("credentials", diagnostics)

    def test_manager_settings_lock_and_frontend_cancel_contract(self):
        saved = main.save_github_settings(
            GitHubSettingsUpdate(repository="https://github.com/vtab/example-releases"),
            actor="test_operator",
        )
        self.assertTrue(saved["github"]["locked"])
        self.assertEqual(saved["github"]["repository"], "https://github.com/vtab/example-releases")
        with self.assertRaises(HTTPException) as locked:
            main.save_github_settings(
                GitHubSettingsUpdate(repository="https://github.com/vtab/another-repository"),
                actor="test_operator",
            )
        self.assertEqual(locked.exception.status_code, 409)

        repositories = main.save_repository_settings(
            RepositorySettingsUpdate(
                services_repository="https://github.com/vtab/reporting-services",
                admin_repository="https://github.com/vtab/admin-console",
            ),
            actor="test_operator",
        )
        self.assertTrue(repositories["repositories"]["locked"])
        with self.assertRaises(HTTPException) as repository_locked:
            main.save_repository_settings(
                RepositorySettingsUpdate(
                    services_repository="https://github.com/vtab/services-two",
                    admin_repository="https://github.com/vtab/admin-two",
                ), actor="test_operator",
            )
        self.assertEqual(repository_locked.exception.status_code, 409)

        html = (Path(__file__).resolve().parents[1] / "manager" / "static" / "index.html").read_text(encoding="utf-8")
        self.assertIn("saved||'dark'", html)
        self.assertIn('type="button" data-close-dialog', html)
        self.assertIn('class="wizard-dialog"', html)
        self.assertEqual(html.count('data-wizard-step="'), 7)
        self.assertNotIn("${icon(", html)

    def test_centralized_preflight_checks_releases_repositories_and_secrets(self):
        client = main.create_client(
            ClientCreate(name="Preflight Client", code="preflight-client", contact_email="ops@example.com"),
            actor="test_operator",
        )["client"]
        release = main.create_release(
            ReleaseCreate(
                component="services", version="7.0.0", channel="stable",
                artifact_url="https://example.com/releases/services-7.0.0.zip",
                checksum_sha256="b" * 64,
                repository_url="https://github.com/vtab/reporting-services",
                git_commit_sha="1" * 40,
            ), actor="test_operator",
        )["release"]
        main.validate_release(release["id"],ReleaseValidation(approved=True,ci_status="success",checks={"build":"success","test":"success","security":"success"}),actor=OperatorIdentity("test-release-manager","release_manager"))
        main.publish_release(release["id"], actor="test_operator")
        result = main.wizard_preflight(
            WizardPreflightRequest(
                client_id=client["id"], application="services", provider="render",
                release_ids={"services": release["id"]},
                repositories={"services": "https://github.com/vtab/reporting-services"},
                provider_config={"region": "oregon", "owner_id": "tea-example"},
                environment_variables={
                    "VITE_SUPABASE_URL": "https://example.supabase.co",
                    "SUPABASE_SERVICE_ROLE_KEY": "test-secret",
                    "VTAB_WEB_URL": "https://reports.example.com",
                },
                verify_provider=False,
            ),
            actor=OperatorIdentity("test_operator","operator"),
        )
        self.assertTrue(result["approved"])
        keys = {check["key"] for check in result["checks"]}
        self.assertIn("release_services", keys)
        self.assertIn("repository_services", keys)

        tables = {row["name"] for row in main.db.all("SELECT name FROM sqlite_master WHERE type='table'")}
        self.assertIn("client_infrastructure", tables)
        self.assertIn("deployment_logs", tables)


if __name__ == "__main__":
    unittest.main()

