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
        self.assertEqual(deployment["deployment"]["status"], "scheduled")
        self.assertEqual(deployment["deployment"]["approved_by"], "test_operator")
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

    def test_only_newest_approved_release_can_be_deployed(self):
        client = main.create_client(
            ClientCreate(name="Release Gate Client", code="release-gate-client"), actor="test_operator"
        )["client"]
        now = main.utc_now()
        main.db.execute(
            "INSERT INTO client_infrastructure(id,client_id,provider,component,status,created_at,updated_at) VALUES(?,?,?,?,?,?,?)",
            ("release-gate-infra",client["id"],"render","services","active",now,now),
        )

        release_ids = []
        for version, sha in (("7.0.0","2"*40),("7.1.0","3"*40)):
            release = main.create_release(
                ReleaseCreate(
                    component="services",version=version,channel="stable",
                    artifact_url=f"https://example.com/releases/services-{version}.zip",
                    checksum_sha256=("a" if version=="7.0.0" else "b")*64,repository_url="https://github.com/vtab/reporting-services",
                    git_commit_sha=sha,
                ),
                actor="test_operator",
            )["release"]
            main.validate_release(
                release["id"],ReleaseValidation(approved=True,ci_status="success",checks={"build":"success","test":"success","security":"success"}),
                actor=OperatorIdentity("release-owner","release_manager"),
            )
            main.publish_release(release["id"],actor="test_operator")
            release_ids.append(release["id"])

        with self.assertRaises(HTTPException) as stale:
            main.create_deployment(
                DeploymentCreate(client_id=client["id"],release_id=release_ids[0]),
                actor=OperatorIdentity("test_operator","operator"),
            )
        self.assertEqual(stale.exception.status_code,409)
        self.assertIn("newest approved release",stale.exception.detail)

        newest = main.create_deployment(
            DeploymentCreate(client_id=client["id"],release_id=release_ids[1]),
            actor=OperatorIdentity("test_operator","operator"),
        )["deployment"]
        self.assertEqual(newest["status"],"scheduled")
        self.assertEqual(newest["stage"],"release_offered")

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

    def test_github_candidates_require_explicit_approval_or_rejection(self):
        candidate = {
            "repository_url": "https://github.com/vtab/reporting-services",
            "git_commit_sha": "a" * 40,
            "version": "commit-aaaaaaaaaaaa",
            "artifact_url": "https://github.com/vtab/reporting-services/commit/" + "a" * 40,
            "message": "Improve deployment health checks",
            "author": "Release Engineer",
            "committed_at": "2026-11-06T09:30:00+00:00",
        }
        pending = main.register_release_candidate("services", candidate, "delivery-1")
        self.assertEqual(pending["status"], "pending_approval")
        self.assertEqual(pending["validation_status"], "pending")
        approved = main.approve_release_candidate(
            pending["id"], actor=OperatorIdentity("release-owner", "release_manager")
        )["release"]
        self.assertEqual(approved["status"], "available")
        self.assertEqual(approved["validation_status"], "approved")

        rejected_candidate = dict(candidate, git_commit_sha="b" * 40, version="commit-bbbbbbbbbbbb")
        rejected = main.register_release_candidate("services", rejected_candidate, "delivery-2")
        result = main.reject_release_candidate(
            rejected["id"], actor=OperatorIdentity("release-owner", "release_manager")
        )["release"]
        self.assertEqual(result["status"], "rejected")

    def test_client_delete_and_manager_update_frontend_contracts(self):
        client = main.create_client(
            ClientCreate(name="Disposable Client", code="disposable-client"), actor="test_operator"
        )["client"]
        result = main.delete_client(
            client["id"], actor=OperatorIdentity("manager-owner", "super_admin")
        )
        self.assertTrue(result["ok"])
        self.assertIsNone(main.db.one("SELECT id FROM clients WHERE id=?", (client["id"],)))

        root = Path(__file__).resolve().parents[1]
        script = (root / "manager" / "static" / "app.js").read_text(encoding="utf-8")
        html = (root / "manager" / "static" / "index.html").read_text(encoding="utf-8")
        for marker in (
            "data-approve-release", "data-reject-release", "Export CSV", "data-delete-client",
            "data-client-view", "offerSelectAllBtn", "Roll back", "release-tag live",
            "Application version", "Deploy release", "data-toggle-deployment-history",
            "Only the newest approved release can be deployed", "Read-only rollout",
        ):
            self.assertIn(marker, script + html)
        self.assertNotIn("offerClientSelect", html)
        self.assertNotIn("data-complete-deployment", script)
        self.assertNotIn("data-approve=", script)
        self.assertNotIn("data-reject-deployment=", script)
        self.assertNotIn("workflow-banner\"><", script)
        self.assertNotIn("Offer update", script)
        self.assertNotIn("Supersede</button>", script)
        self.assertNotIn("dop_v1_", html)
        self.assertNotIn("xkeysib-", html)

        launcher = (root / "run_manager.py").read_text(encoding="utf-8")
        bootstrap = (root / "start_manager.py").read_text(encoding="utf-8")
        self.assertIn('"cryptography": "cryptography"', launcher)
        self.assertIn('"--reinstall"', launcher)
        self.assertIn("dependencies_healthy", bootstrap)


if __name__ == "__main__":
    unittest.main()

