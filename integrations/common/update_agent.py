"""VTAB client update connector.

Discovery is read-only.  An update runs only after the client administrator
explicitly confirms it and the Manager returns an apply_requested deployment.
The connector never follows a branch or deploys an unspecified latest commit.
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import tempfile
import urllib.request
from pathlib import Path

MANAGER_URL = os.environ["VTAB_MANAGER_URL"].rstrip("/")
CLIENT_ID = os.environ["VTAB_CLIENT_ID"]
AGENT_TOKEN = os.environ["VTAB_AGENT_TOKEN"]
CLIENT_ADMIN_TOKEN = os.environ.get("VTAB_CLIENT_ADMIN_TOKEN", "")
COMPONENT = os.environ.get("VTAB_COMPONENT", "services")
INSTALL_ROOT = Path(os.environ.get("VTAB_RELEASE_ROOT", "/opt/vtab/releases")) / COMPONENT
ACTIVE_LINK = Path(os.environ.get("VTAB_ACTIVE_RELEASE", f"/opt/vtab/current/{COMPONENT}"))
HEALTH_URL = os.environ.get("VTAB_HEALTH_URL", "")


def _request(path: str, method: str = "GET", body: dict | None = None, admin: bool = False) -> dict:
    headers = {"X-VTAB-Client-Id": CLIENT_ID, "X-VTAB-Agent-Token": AGENT_TOKEN, "Content-Type": "application/json"}
    if admin:
        headers["X-VTAB-Client-Admin-Token"] = CLIENT_ADMIN_TOKEN
    request = urllib.request.Request(MANAGER_URL + path, data=json.dumps(body).encode() if body is not None else None, headers=headers, method=method)
    with urllib.request.urlopen(request, timeout=30) as response:
        return json.loads(response.read().decode())


def discover() -> dict:
    """Safe polling operation: show an approved offer but never apply it."""
    return _request(f"/api/v1/agent/updates/available?component={COMPONENT}")


def accept(deployment_id: str, requested_by: str) -> dict:
    if not CLIENT_ADMIN_TOKEN:
        raise RuntimeError("VTAB_CLIENT_ADMIN_TOKEN is required for Update Now.")
    return _request(f"/api/v1/agent/deployments/{deployment_id}/accept", "POST", {"confirmed": True, "requested_by": requested_by}, admin=True)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def apply_self_hosted(deployment: dict) -> dict:
    """Install into an immutable SHA folder, health-check, then atomically activate."""
    release = deployment["release"]
    sha = release["git_commit_sha"]
    if len(sha) != 40:
        raise RuntimeError("Manager did not provide an exact commit SHA.")
    destination = INSTALL_ROOT / sha
    previous = ACTIVE_LINK.resolve() if ACTIVE_LINK.exists() else None
    INSTALL_ROOT.mkdir(parents=True, exist_ok=True)
    ACTIVE_LINK.parent.mkdir(parents=True, exist_ok=True)
    archive = Path(tempfile.mkstemp(suffix=".zip")[1])
    try:
        urllib.request.urlretrieve(release["artifact_url"], archive)
        if _sha256(archive) != release["checksum_sha256"].lower():
            raise RuntimeError("Release checksum verification failed.")
        staging = destination.with_name(destination.name + ".staging")
        if staging.exists():
            shutil.rmtree(staging)
        shutil.unpack_archive(str(archive), str(staging))
        staging.replace(destination)
        temporary_link = ACTIVE_LINK.with_name(ACTIVE_LINK.name + ".next")
        temporary_link.unlink(missing_ok=True)
        temporary_link.symlink_to(destination, target_is_directory=True)
        temporary_link.replace(ACTIVE_LINK)
        if HEALTH_URL:
            with urllib.request.urlopen(HEALTH_URL, timeout=30) as response:
                if response.status >= 300:
                    raise RuntimeError(f"Health endpoint returned {response.status}.")
        return {"installed_version": release["version"], "installed_commit_sha": sha, "health_verified": True}
    except Exception:
        if previous:
            fallback = ACTIVE_LINK.with_name(ACTIVE_LINK.name + ".rollback")
            fallback.unlink(missing_ok=True)
            fallback.symlink_to(previous, target_is_directory=True)
            fallback.replace(ACTIVE_LINK)
        raise
    finally:
        archive.unlink(missing_ok=True)


def apply_render(deployment: dict) -> dict:
    """Ask Render for the approved commit only; credentials remain in the client vault."""
    token=os.environ.get("RENDER_API_KEY","");service=os.environ.get("RENDER_SERVICE_ID","")
    if not token or not service: raise RuntimeError("RENDER_API_KEY and RENDER_SERVICE_ID are required in the client vault.")
    release=deployment["release"];sha=release["git_commit_sha"]
    request=urllib.request.Request(f"https://api.render.com/v1/services/{service}/deploys",data=json.dumps({"commitId":sha,"clearCache":"do_not_clear"}).encode(),headers={"Authorization":f"Bearer {token}","Content-Type":"application/json"},method="POST")
    with urllib.request.urlopen(request,timeout=30) as response: payload=json.loads(response.read().decode())
    return {"provider_deploy_id":str(payload.get("id") or ""),"installed_version":release["version"],"installed_commit_sha":sha,"health_verified":False}


def apply_approved(deployment:dict)->dict:
    provider=str(deployment.get("provider") or os.environ.get("VTAB_DEPLOYMENT_PROVIDER","self_hosted"))
    if provider=="render": return apply_render(deployment)
    if provider in {"agent","digitalocean","self_hosted"}: return apply_self_hosted(deployment)
    raise RuntimeError(f"Unsupported controlled deployment provider: {provider}")


def report(deployment_id: str, status: str, **details: object) -> dict:
    return _request(f"/api/v1/agent/deployments/{deployment_id}/report", "POST", {"status": status, **details})

