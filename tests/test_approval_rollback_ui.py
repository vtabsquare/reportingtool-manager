"""Regression tests for approval-only releases and immediate-predecessor rollback."""
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

import pytest
from fastapi import HTTPException

from manager import main
from manager.contracts import ClientCreate, DeploymentCreate, DeploymentApproval, ReleaseVersionUpdate
from manager.database import Database
from manager.security import OperatorIdentity


@pytest.fixture
def manager_db(tmp_path):
    old_db, old_settings = main.db, main.settings
    main.db = Database(tmp_path / "release-workflow.sqlite")
    main.db.initialize()
    main.settings = replace(old_settings, github_repository="", github_repository_services="", github_repository_admin="")
    try:
        yield
    finally:
        main.db, main.settings = old_db, old_settings


def candidate(version, sha_char, date):
    return {
        "repository_url": "https://github.com/example/reporting-services",
        "git_commit_sha": sha_char * 40,
        "version": version,
        "artifact_url": f"https://github.com/example/reporting-services/commit/{sha_char * 40}",
        "release_notes": f"Release {version}",
        "author": "Release Team",
        "committed_at": date,
    }


def make_release(version, sha_char, date, actor):
    release = main.register_release_candidate("services", candidate(version, sha_char, date), f"candidate-{version}")
    return main.approve_release_candidate(release["id"], actor=actor)["release"]


def test_release_is_not_activated_or_shown_as_new_before_approval(manager_db):
    actor = OperatorIdentity("release-owner", "release_manager")
    old = make_release("1.5", "a", "2026-11-02T12:00:00+00:00", actor)
    candidate18 = main.register_release_candidate("services", candidate("1.8", "b", "2026-11-06T12:00:00+00:00"))
    rows = {r["id"]: r for r in main.list_releases(actor)["releases"]}
    assert rows[candidate18["id"]]["status"] == "pending_approval"
    assert rows[candidate18["id"]]["is_new"] is False
    assert rows[candidate18["id"]]["is_active"] is False
    assert rows[candidate18["id"]]["rollback_eligible_client_ids"] == []
    assert rows[old["id"]]["is_new"] is True

    main.approve_release_candidate(candidate18["id"], actor=actor)
    rows = {r["id"]: r for r in main.list_releases(actor)["releases"]}
    assert rows[candidate18["id"]]["status"] == "available"
    assert rows[candidate18["id"]]["is_new"] is True
    assert rows[old["id"]]["is_new"] is False


def test_approve_reject_active_and_strict_previous_version_rollback(manager_db):
    admin = OperatorIdentity("release-owner", "release_manager")
    operator = OperatorIdentity("deploy-owner", "operator")
    rel14 = make_release("1.4", "a", "2026-10-22T12:00:00+00:00", admin)
    rel15 = make_release("1.5", "b", "2026-11-02T12:00:00+00:00", admin)
    rel18 = make_release("1.8", "c", "2026-11-06T12:00:00+00:00", admin)
    client = main.create_client(ClientCreate(name="Production Client", code="production-client"), actor="tester")["client"]
    client_id = client["id"]
    now = main.utc_now()
    main.db.execute("UPDATE clients SET installed_services_version='1.5' WHERE id=?", (client_id,))
    main.db.execute("""INSERT INTO client_infrastructure(id,client_id,provider,component,status,created_at,updated_at)
        VALUES(?,?,?,?,?,?,?)""", (str(uuid4()), client_id, "render", "services", "active", now, now))

    first = main.create_deployment(DeploymentCreate(client_id=client_id, release_id=rel18["id"]), actor=operator)["deployment"]
    assert first["target_version"] == "1.8"
    assert first["status"] in ("scheduled", "offered", "pending_approval")
    rejected = main.reject_deployment(first["id"], actor=operator)["deployment"]
    assert rejected["status"] == "rejected"
    assert main.db.one("SELECT 1 FROM deployment_locks WHERE deployment_id=?", (first["id"],)) is None
    with pytest.raises(HTTPException) as error:
        main.approve_deployment(first["id"], DeploymentApproval(approved_by="Owner"), actor=operator)
    assert error.value.status_code == 409

    approved_job = main.create_deployment(DeploymentCreate(client_id=client_id, release_id=rel18["id"]), actor=operator)["deployment"]
    with patch("manager.main.dispatch_client_update_webhook"):
        offered = main.approve_deployment(approved_job["id"], DeploymentApproval(approved_by="Owner"), actor=operator)["deployment"]
    assert offered["status"] == "scheduled"

    assert next(r for r in main.list_releases(operator)["releases"] if r["id"] == rel18["id"])["is_active"] is False

    # Simulate connector-verified success to test the display and rollback metadata.
    main.db.execute("UPDATE deployments SET status='succeeded',finished_at=?,updated_at=? WHERE id=?", (now, now, approved_job["id"]))
    main.db.execute("UPDATE clients SET installed_services_version='1.8' WHERE id=?", (client_id,))
    main.release_deployment_lock(approved_job["id"])
    catalog = {r["id"]: r for r in main.list_releases(operator)["releases"]}
    assert catalog[rel18["id"]]["is_active"] is True
    assert catalog[rel15["id"]]["is_active"] is False
    assert catalog[rel15["id"]]["rollback_eligible_client_ids"] == [client_id]
    assert catalog[rel14["id"]]["rollback_eligible_client_ids"] == []
    assert catalog[rel18["id"]]["rollback_eligible_client_ids"] == []

    with pytest.raises(HTTPException) as downgrade_error:
        main.create_deployment(DeploymentCreate(client_id=client_id, release_id=rel14["id"]), actor=operator)
    assert downgrade_error.value.status_code == 409

    for release_id in (rel14["id"], rel18["id"]):
        with pytest.raises(HTTPException) as error:
            main.create_deployment(DeploymentCreate(client_id=client_id, release_id=release_id, deployment_type="rollback"), actor=operator)
        assert error.value.status_code == 409

    rollback = main.create_deployment(DeploymentCreate(client_id=client_id, release_id=rel15["id"], deployment_type="rollback"), actor=operator)["deployment"]
    assert rollback["deployment_type"] == "rollback"
    assert rollback["previous_version"] == "1.8"
    assert rollback["target_version"] == "1.5"
    assert rollback["status"] in ("scheduled", "offered", "pending_approval")


def test_frontend_approval_controls_have_icons_and_not_manual_complete(manager_db):
    js = (Path(__file__).resolve().parents[1] / "manager" / "static" / "app.js").read_text(encoding="utf-8")
    html = (Path(__file__).resolve().parents[1] / "manager" / "static" / "index.html").read_text(encoding="utf-8")
    assert "Application version" in js
    assert "data-reject-deployment" in js
    assert "rollback_eligible_client_ids" in js
    assert "deployment_type:state.offerMode" in js
    assert "data-complete-deployment" not in js
    assert "data-cancel-deployment" not in js
    assert "data-supersede-release" not in js
    for symbol in ("i-trash-2", "i-settings", "i-rotate-ccw", "i-check-circle", "i-x-circle", "i-send"):
        assert symbol in html


def test_assign_real_application_version_before_approval_only(manager_db):
    actor = OperatorIdentity("release-owner", "release_manager")
    incoming = main.register_release_candidate("services", candidate("commit-aaaaaaaaaaaa", "a", "2026-11-06T12:00:00+00:00"))
    assert incoming["version"].startswith("commit-")
    renamed = main.assign_release_version(incoming["id"], ReleaseVersionUpdate(version="v1.8"), actor=actor)["release"]
    assert renamed["version"] == "1.8"
    assert renamed["status"] == "pending_approval"
    assert renamed["validation_status"] == "pending"
    approved = main.approve_release_candidate(incoming["id"], actor=actor)["release"]
    assert approved["version"] == "1.8"
    with pytest.raises(HTTPException) as error:
        main.assign_release_version(incoming["id"], ReleaseVersionUpdate(version="1.9"), actor=actor)
    assert error.value.status_code == 409
