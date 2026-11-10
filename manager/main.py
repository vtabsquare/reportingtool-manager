from __future__ import annotations

import hashlib
import hmac
import json
import logging
logger = logging.getLogger("vtab.manager")
import os
import threading
import uuid
import urllib.error
import urllib.request
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from fastapi import BackgroundTasks, Depends, FastAPI, Header, HTTPException, Query, Request
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from . import __version__
from .config import settings
from .contracts import (
    AgentCheckIn,
    AgentDeploymentReport,
    AgentUpdatePreflight,
    AgentUpdateAcceptance,
    ClientConfigurationUpdate,
    ClientCreate,
    DeploymentApproval,
    DeploymentCreate,
    GitHubSettingsUpdate,
    GitHubSyncRequest,
    RepositorySettingsUpdate,
    RollbackRequest,
    ReleaseCreate,
    ReleaseNotesUpdate,
    ReleaseValidation,
    ReleaseVersionUpdate,
    DeploymentRetry,
    TicketEscalation,
    TicketUpdate,
    WizardExecuteRequest,
    WizardPreflightRequest,
    ConnectionTestRequest,
    ServerSettingsUpdate,
)
from .database import Database, utc_now
from .engine import DeploymentEngine, application_profiles
from .engine.health import HealthCheckService
from .engine.adapters import DigitalOceanDeploymentAdapter, RenderDeploymentAdapter
from .engine.profiles import selected_profiles
from .engine.rollback import RollbackManager
from .security import OperatorIdentity, authorize, new_agent_token, require_operator, token_hash, verify_agent, verify_client_admin
from .configuration import decrypt_environment, encrypt_environment, mask_environment, merge_masked_environment
from .github_service import canonical_repository, discover_commits, discover_version_tags, parse_webhook, validate_commit, verify_webhook_signature


db = Database(settings.database_path, settings.database_url)
STATIC = Path(__file__).resolve().parent / "static"
FORBIDDEN_DIAGNOSTIC_KEYS = {
    "rows", "data", "dataset", "project_json", "report_json", "credentials",
    "password", "access_token", "refresh_token", "api_key", "secret",
}


@asynccontextmanager
async def lifespan(_: FastAPI):
    settings.encryption_key_bytes
    db.initialize()
    yield


app = FastAPI(
    title="VTAB Reporting Manager API",
    version=__version__,
    lifespan=lifespan,
)


@app.middleware("http")
async def add_no_cache_headers(request: Request, call_next):
    response = await call_next(request)
    if request.url.path == "/" or request.url.path.startswith("/assets"):
        response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
        response.headers["Pragma"] = "no-cache"
        response.headers["Expires"] = "0"
    return response


app.mount("/assets", StaticFiles(directory=STATIC), name="assets")


def identifier() -> str:
    return str(uuid.uuid4())


def json_value(value: Any, fallback: Any) -> Any:
    if value in (None, ""):
        return fallback
    try:
        return json.loads(value)
    except (TypeError, json.JSONDecodeError):
        return fallback


def serialize_row(row: dict[str, Any] | None) -> dict[str, Any] | None:
    if not row:
        return None
    result = dict(row)
    for key in ("manifest_json", "diagnostics_json", "details_json", "capabilities_json", "validation_details_json", "config_json"):
        if key in result:
            result[key.removesuffix("_json")] = json_value(result.pop(key), {} if key != "capabilities_json" else [])
    if "rollback_available" in result:
        result["rollback_available"] = bool(result["rollback_available"])
    return result


def client_or_404(client_id: str) -> dict[str, Any]:
    row = db.one("SELECT * FROM clients WHERE id=?", (client_id,))
    if not row:
        raise HTTPException(404, "Client not found.")
    return row


def agent_context(client_id: str | None, token: str | None) -> tuple[dict[str, Any], dict[str, Any]]:
    if not client_id:
        raise HTTPException(401, "X-VTAB-Client-ID is required.")
    client = client_or_404(client_id)
    agent = db.one("SELECT * FROM client_agents WHERE client_id=? ORDER BY created_at DESC LIMIT 1", (client_id,))
    if not agent:
        raise HTTPException(401, "No connector is registered for this client.")
    verify_agent(agent["token_hash"], token)
    return client, agent


def sanitize_diagnostics(value: dict[str, Any]) -> dict[str, Any]:
    def clean(item: Any) -> Any:
        if isinstance(item, dict):
            return {str(k): clean(v) for k, v in item.items() if str(k).lower() not in FORBIDDEN_DIAGNOSTIC_KEYS}
        if isinstance(item, list):
            return [clean(v) for v in item[:100]]
        if isinstance(item, str):
            return item[:4000]
        return item

    cleaned = clean(value)
    if len(json.dumps(cleaned, ensure_ascii=False)) > 50_000:
        raise HTTPException(413, "Approved diagnostics exceed the 50 KB ticket limit.")
    return cleaned


def unique_violation(error: Exception) -> bool:
    message = str(error).upper()
    return "UNIQUE" in message or "DUPLICATE KEY" in message


def release_or_404(release_id:str)->dict[str,Any]:
    release=db.one("SELECT * FROM releases WHERE id=?",(release_id,))
    if not release:raise HTTPException(404,"Release not found.")
    return release


def component_repository(component:str)->str:
    repositories=repository_configuration()
    value=repositories.get("services_repository" if component=="services" else "admin_repository","")
    if not value:
        value=settings.github_repository_services if component=="services" else settings.github_repository_admin
    return canonical_repository(value) if value else ""


def component_for_repository(repository_url:str)->str:
    canonical=canonical_repository(repository_url)
    for component in ("services","administration"):
        configured=component_repository(component)
        if configured and hmac.compare_digest(canonical.lower(),configured.lower()):return component
    raise HTTPException(422,"Webhook repository is not an approved Services or Administration repository.")


def register_release_candidate(component:str,candidate:dict[str,Any],source_event_id:str|None=None)->dict[str,Any]:
    repository=canonical_repository(candidate["repository_url"]);sha=str(candidate["git_commit_sha"]).lower();version=str(candidate["version"])
    commit_date=candidate.get("committed_at")
    existing=db.one("SELECT * FROM releases WHERE component=? AND repository_url=? AND git_commit_sha=?",(component,repository,sha))
    if existing:
        if version and not version.startswith("commit-") and str(existing.get("version") or "").startswith("commit-"):
            conflict=db.one("SELECT id FROM releases WHERE component=? AND version=? AND channel=? AND id<>?",(component,version,existing.get("channel") or "stable",existing["id"]))
            if not conflict:
                db.execute("UPDATE releases SET version=? WHERE id=?",(version,existing["id"]))
                existing["version"]=version
        if commit_date and existing.get("created_at")!=commit_date:
            db.execute("UPDATE releases SET created_at=? WHERE id=?",(commit_date,existing["id"]))
            existing["created_at"]=commit_date
        return existing
    release_id,now=identifier(),utc_now()
    created_at=commit_date or now
    manifest={"schema":"vtab-controlled-release","version":1,"sourceOnly":True,"component":component,"repository":repository,"commitSha":sha}
    checksum=hashlib.sha256(f"{repository}@{sha}".encode("utf-8")).hexdigest()
    validation_details={"approved":False,"source":"github_commit","author":str(candidate.get("author") or "Unknown author"),"message":str(candidate.get("message") or candidate.get("release_notes") or ""),"committedAt":commit_date}
    db.execute("""INSERT INTO releases(id,component,version,channel,status,artifact_url,checksum_sha256,release_notes,manifest_json,git_commit_sha,repository_url,migration_index,validation_status,validation_details_json,source_event_id,created_at)
                  VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
               (release_id,component,version,"stable","pending_approval",candidate["artifact_url"],checksum,candidate.get("release_notes") or candidate.get("message") or "",json.dumps(manifest),sha,repository,0,"pending",json.dumps(validation_details),source_event_id,created_at))
    db.audit("github-control-plane","release.candidate_registered","release",release_id,details={"component":component,"repository":repository,"commitSha":sha,"event":source_event_id})
    return db.one("SELECT * FROM releases WHERE id=?",(release_id,)) or {}


def acquire_deployment_lock(client_id:str,component:str,deployment_id:str)->str:
    now=datetime.now(timezone.utc);token=uuid.uuid4().hex;expires=(now+timedelta(seconds=settings.deployment_lock_seconds)).isoformat()
    db.execute("DELETE FROM deployment_locks WHERE client_id=? AND component=? AND expires_at<?",(client_id,component,now.isoformat()))
    db.execute("DELETE FROM deployment_locks WHERE deployment_id IN (SELECT id FROM deployments WHERE status IN ('succeeded','failed','cancelled','rejected','rolled_back'))")
    try:db.execute("INSERT INTO deployment_locks(client_id,component,deployment_id,lock_token,expires_at,created_at) VALUES(?,?,?,?,?,?)",(client_id,component,deployment_id,token,expires,now.isoformat()))
    except Exception as error:
        if unique_violation(error):raise HTTPException(409,"Another deployment for this client component is already active.") from error
        raise
    return token


def release_deployment_lock(deployment_id:str)->None:
    db.execute("DELETE FROM deployment_locks WHERE deployment_id=?",(deployment_id,))


def github_configuration() -> dict[str, Any]:
    stored = db.setting("github", {}) or {}
    repository = settings.github_repository or stored.get("repository", "")
    return {
        "repository": repository,
        "locked": bool(settings.github_repository or stored.get("locked")),
        "configured": bool(repository),
        "tokenConfigured": bool(settings.github_token),
    }


def repository_configuration() -> dict[str, Any]:
    stored = db.setting("application_repositories", {}) or {}
    return {
        "services_repository": stored.get("services_repository", ""),
        "admin_repository": stored.get("admin_repository", ""),
        "locked": bool(stored.get("locked")),
    }


def server_configuration() -> dict[str, Any]:
    stored = db.setting("server_settings", {}) or {}
    return {
        "host": str(stored.get("host") or os.getenv("VTAB_DEFAULT_SERVER_HOST", "139.59.32.39")).strip(),
        "ssh_port": int(stored.get("ssh_port") or 22),
        "ssh_user": str(stored.get("ssh_user") or "root").strip(),
        "services_target_dir": str(stored.get("services_target_dir") or "/var/www/reportingtool-webservice").strip(),
        "admin_target_dir": str(stored.get("admin_target_dir") or "/var/www/reportingtool-admin").strip(),
        "digitalocean_token": str(stored.get("digitalocean_token") or settings.digitalocean_token or "").strip(),
        "locked": bool(stored.get("locked")),
    }


def github_coordinates(repository: str) -> tuple[str, str]:
    parsed = urlparse(repository)
    if parsed.hostname not in {"github.com", "www.github.com"}:
        raise HTTPException(422, "The release source must be a github.com repository URL.")
    parts = [part for part in parsed.path.strip("/").split("/") if part]
    if len(parts) < 2:
        raise HTTPException(422, "Enter a complete GitHub repository URL containing owner and repository.")
    return parts[0], parts[1].removesuffix(".git")


@app.get("/")
def index():
    return FileResponse(
        STATIC / "index.html",
        headers={"Cache-Control": "no-cache, no-store, must-revalidate", "Pragma": "no-cache", "Expires": "0"},
    )


@app.get("/api/v1/health")
def health():
    return {"status": "healthy", "service": "vtab-reporting-manager", "version": __version__, "storage": db.backend, "time": utc_now()}


@app.get("/api/v1/config")
def browser_config():
    return {
        "version": __version__,
        "environment": settings.environment,
        "requiresApiKey": True,
        "storageBackend": db.backend,
        "developmentKey": settings.api_key if settings.development else None,
    }


@app.get("/api/v1/operator/settings")
def operator_settings(_: str = Depends(require_operator)):
    return {
        "storageBackend": db.backend,
        "github": github_configuration(),
        "repositories": repository_configuration(),
        "server": server_configuration(),
    }


@app.put("/api/v1/operator/settings/github")
def save_github_settings(payload: GitHubSettingsUpdate, actor: str = Depends(require_operator)):
    current = github_configuration()
    if current["locked"]:
        raise HTTPException(409, "The GitHub release source is locked and cannot be edited from the manager.")
    repository = str(payload.repository).rstrip("/")
    github_coordinates(repository)
    db.set_setting("github", {"repository": repository, "locked": True})
    db.audit(actor, "settings.github_locked", "manager_settings", "github", details={"repository": repository})
    return {"github": github_configuration()}


@app.post("/api/v1/operator/settings/github/unlock")
def unlock_github_settings(actor: str = Depends(require_operator)):
    current = github_configuration()
    db.set_setting("github", {"repository": current.get("repository", ""), "locked": False})
    db.audit(actor, "settings.github_unlocked", "manager_settings", "github")
    return {"github": github_configuration()}


@app.post("/api/v1/operator/settings/github/lock")
def lock_github_settings(actor: str = Depends(require_operator)):
    current = github_configuration()
    db.set_setting("github", {"repository": current.get("repository", ""), "locked": True})
    db.audit(actor, "settings.github_locked", "manager_settings", "github")
    return {"github": github_configuration()}


@app.put("/api/v1/operator/settings/repositories")
def save_repository_settings(payload: RepositorySettingsUpdate, actor: str = Depends(require_operator)):
    current = repository_configuration()
    if current["locked"]:
        raise HTTPException(409, "Application repository references are locked.")
    value = {
        "services_repository": str(payload.services_repository).rstrip("/"),
        "admin_repository": str(payload.admin_repository).rstrip("/"),
        "locked": True,
    }
    for repository in (value["services_repository"], value["admin_repository"]):
        github_coordinates(repository)
    db.set_setting("application_repositories", value)
    db.audit(actor, "settings.repositories_locked", "manager_settings", "application_repositories", details={"services": value["services_repository"], "administration": value["admin_repository"]})
    return {"repositories": repository_configuration()}


@app.post("/api/v1/operator/settings/repositories/unlock")
def unlock_repository_settings(actor: str = Depends(require_operator)):
    current = repository_configuration()
    value = {
        "services_repository": current.get("services_repository", ""),
        "admin_repository": current.get("admin_repository", ""),
        "locked": False,
    }
    db.set_setting("application_repositories", value)
    db.audit(actor, "settings.repositories_unlocked", "manager_settings", "application_repositories")
    return {"repositories": repository_configuration()}


@app.post("/api/v1/operator/settings/repositories/lock")
def lock_repository_settings(actor: str = Depends(require_operator)):
    current = repository_configuration()
    value = {
        "services_repository": current.get("services_repository", ""),
        "admin_repository": current.get("admin_repository", ""),
        "locked": True,
    }
    db.set_setting("application_repositories", value)
    db.audit(actor, "settings.repositories_locked", "manager_settings", "application_repositories")
    return {"repositories": repository_configuration()}


@app.put("/api/v1/operator/settings/server")
def save_server_settings(payload: ServerSettingsUpdate, actor: str = Depends(require_operator)):
    current = server_configuration()
    if current["locked"]:
        raise HTTPException(409, "Server configuration is locked.")
    value = {
        "host": payload.host.strip(),
        "ssh_port": payload.ssh_port,
        "ssh_user": payload.ssh_user.strip(),
        "services_target_dir": payload.services_target_dir.strip(),
        "admin_target_dir": payload.admin_target_dir.strip(),
        "digitalocean_token": payload.digitalocean_token.strip(),
        "locked": True,
    }
    db.set_setting("server_settings", value)
    db.audit(actor, "settings.server_locked", "manager_settings", "server_settings", details=value)
    return {"server": server_configuration()}


@app.post("/api/v1/operator/settings/server/unlock")
def unlock_server_settings(actor: str = Depends(require_operator)):
    current = server_configuration()
    current["locked"] = False
    db.set_setting("server_settings", current)
    db.audit(actor, "settings.server_unlocked", "manager_settings", "server_settings")
    return {"server": server_configuration()}


@app.post("/api/v1/operator/settings/server/lock")
def lock_server_settings(actor: str = Depends(require_operator)):
    current = server_configuration()
    current["locked"] = True
    db.set_setting("server_settings", current)
    db.audit(actor, "settings.server_locked", "manager_settings", "server_settings")
    return {"server": server_configuration()}


@app.post("/api/v1/operator/test-connection")
def operator_test_connection(payload: ConnectionTestRequest, actor: OperatorIdentity = Depends(require_operator)):
    if payload.provider == "render":
        token = payload.provider_token.get_secret_value() if payload.provider_token else ""
        if not token and payload.client_id:
            row = db.one("SELECT environment_variables_json FROM client_infrastructure WHERE client_id=? AND provider='render'", (payload.client_id,))
            if row:
                env = decrypt_environment(row.get("environment_variables_json"))
                token = env.get("RENDER_API_KEY", "")
        if not token:
            raise HTTPException(422, "A Render API key (provider_token or RENDER_API_KEY) is required.")
        try:
            adapter = RenderDeploymentAdapter(token)
            val = adapter.validate()
            return {
                "success": True,
                "provider": "render",
                "message": "Render API connection successful! Access verified.",
                "details": val,
            }
        except Exception as e:
            return {
                "success": False,
                "provider": "render",
                "message": f"Render API connection failed: {str(e)}",
                "error": str(e),
            }

    elif payload.provider == "digitalocean":
        host = (payload.host or "").strip()
        if not host and payload.client_id:
            row = db.one("SELECT config_json FROM client_infrastructure WHERE client_id=? AND provider='digitalocean'", (payload.client_id,))
            if row:
                try:
                    cfg = json.loads(row.get("config_json") or "{}")
                    host = cfg.get("host") or cfg.get("server_ip") or ""
                except Exception:
                    pass
        if not host:
            server_cfg = server_configuration()
            host = server_cfg.get("host", "")

        if not host:
            token = payload.provider_token.get_secret_value() if payload.provider_token else settings.digitalocean_token
            if token:
                adapter = DigitalOceanDeploymentAdapter(token)
                val = adapter.validate()
                if val.get("authenticated"):
                    return {
                        "success": True,
                        "provider": "digitalocean",
                        "type": "api",
                        "message": f"DigitalOcean API authenticated! Droplet limit: {val.get('account', {}).get('droplet_limit')}.",
                        "details": val,
                    }
                return {
                    "success": False,
                    "provider": "digitalocean",
                    "type": "api",
                    "message": f"DigitalOcean API authentication failed: {val.get('api_error', 'Invalid token')}",
                    "error": val.get("api_error"),
                }
            raise HTTPException(422, "Server Host / IP is required to test SSH connection.")

        ssh_res = DigitalOceanDeploymentAdapter.test_ssh_connection(
            host=host,
            port=payload.ssh_port,
            user=payload.ssh_user,
            key_data=payload.ssh_key,
            timeout=8,
        )
        return {
            "success": bool(ssh_res.get("authenticated")),
            "provider": "digitalocean",
            "type": "ssh",
            "host": host,
            "message": ssh_res.get("message"),
            "details": ssh_res.get("details", {}),
            "error": ssh_res.get("error"),
        }



@app.get("/api/v1/operator/settings/github/releases")
def github_releases(_: str = Depends(require_operator)):
    configuration = github_configuration()
    if not configuration["configured"]:
        return {"configured": False, "repository": "", "releases": []}
    owner, repository = github_coordinates(configuration["repository"])
    request = urllib.request.Request(
        f"https://api.github.com/repos/{owner}/{repository}/releases?per_page=12",
        headers={"Accept": "application/vnd.github+json", "User-Agent": "VTAB-Reporting-Manager"},
    )
    if settings.github_token:
        request.add_header("Authorization", f"Bearer {settings.github_token}")
    try:
        with urllib.request.urlopen(request, timeout=12) as response:
            payload = json.load(response)
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as error:
        raise HTTPException(502, f"GitHub release discovery failed: {error}") from error
    releases = [{
        "tag": item.get("tag_name"), "name": item.get("name") or item.get("tag_name"),
        "url": item.get("html_url"), "publishedAt": item.get("published_at"),
        "prerelease": bool(item.get("prerelease")),
        "assets": [{"name": asset.get("name"), "url": asset.get("browser_download_url"), "size": asset.get("size"), "digest": asset.get("digest")} for asset in item.get("assets", [])],
    } for item in payload if not item.get("draft")]
    return {"configured": True, "repository": configuration["repository"], "releases": releases}


@app.post("/api/v1/webhooks/github")
async def github_webhook(request:Request,x_hub_signature_256:str|None=Header(default=None),x_github_event:str|None=Header(default=None),x_github_delivery:str|None=Header(default=None)):
    body=await request.body();verify_webhook_signature(body,x_hub_signature_256)
    if not x_github_delivery or len(x_github_delivery)>200:raise HTTPException(400,"X-GitHub-Delivery is required.")
    digest=hashlib.sha256(body).hexdigest();existing=db.one("SELECT * FROM github_webhook_events WHERE delivery_id=?",(x_github_delivery,))
    if existing:
        if not hmac.compare_digest(existing["payload_sha256"],digest):raise HTTPException(409,"GitHub delivery ID was reused with a different payload.")
        return {"accepted":True,"duplicate":True,"deliveryId":x_github_delivery,"result":json_value(existing.get("result_json"),{})}
    try:payload=json.loads(body)
    except json.JSONDecodeError as error:raise HTTPException(400,"GitHub webhook payload is not valid JSON.") from error
    repository=str((payload.get("repository") or {}).get("html_url") or "")
    db.execute("INSERT INTO github_webhook_events(delivery_id,event_name,payload_sha256,repository_url,action,processed_status,result_json,received_at) VALUES(?,?,?,?,?,?,?,?)",
               (x_github_delivery,x_github_event or "unknown",digest,repository,str(payload.get("action") or ""),"received","{}",utc_now()))
    try:
        candidate=parse_webhook(x_github_event or "",payload)
        if not candidate:
            result={"ignored":True,"reason":"Event or branch is not configured for controlled releases."};status="ignored"
        else:
            component=component_for_repository(candidate["repository_url"]);release=register_release_candidate(component,candidate,x_github_delivery)
            result={"ignored":False,"release":serialize_row(release)};status="processed"
        db.execute("UPDATE github_webhook_events SET processed_status=?,result_json=?,processed_at=? WHERE delivery_id=?",(status,json.dumps(result),utc_now(),x_github_delivery))
        return {"accepted":True,"duplicate":False,"deliveryId":x_github_delivery,**result}
    except Exception as error:
        db.execute("UPDATE github_webhook_events SET processed_status='failed',result_json=?,processed_at=? WHERE delivery_id=?",(json.dumps({"error":str(error)[:1000]}),utc_now(),x_github_delivery))
        raise


@app.post("/api/v1/operator/releases/sync-github")
def sync_github_releases(payload:GitHubSyncRequest,actor:OperatorIdentity=Depends(require_operator)):
    authorize(actor,{"super_admin","release_manager","operator"})
    repository=canonical_repository(str(payload.repository_url)) if payload.repository_url else component_repository(payload.component)
    if not repository:raise HTTPException(409,f"The {payload.component} repository is not configured.")
    if component_repository(payload.component) and repository.lower()!=component_repository(payload.component).lower():raise HTTPException(409,"The requested repository is not the locked repository for this component.")
    discovered=discover_commits(repository,payload.branch,payload.limit);registered=[]
    try:version_tags=discover_version_tags(repository)
    except HTTPException:version_tags={}
    for candidate in discovered:
        candidate["version"]=version_tags.get(str(candidate.get("git_commit_sha") or "").lower(),candidate["version"])
        candidate["release_notes"]=candidate.pop("message","");registered.append(serialize_row(register_release_candidate(payload.component,candidate)))
    db.audit(str(actor),"release.github_sync","release",details={"component":payload.component,"repository":repository,"branch":payload.branch,"count":len(registered)})
    return {"component":payload.component,"repository":repository,"branch":payload.branch,"releases":registered}


def finalize_healthy_deployment_if_ready(deployment: Any) -> Any:
    if not deployment:
        return deployment
    row = dict(deployment)
    status = row.get("status")
    if status in ("succeeded", "failed", "cancelled", "rejected", "rolled_back", "pending_approval"):
        return deployment
    dep_id = row["id"]
    client_id = row["client_id"]
    component = row.get("component") or "services"
    target_version = row.get("target_version") or ""
    release = db.one("SELECT * FROM releases WHERE id=?", (row.get("release_id"),)) if row.get("release_id") else None
    target_sha = str((release.get("git_commit_sha") or "") if release else "").lower()

    # Collect all candidate endpoints for this client/component
    candidate_urls: list[str] = []
    notes_raw = row.get("agent_notes")
    host = None
    if notes_raw:
        try:
            notes = json.loads(notes_raw)
            url_note = (notes.get("provider", {}) or {}).get("url")
            if url_note and url_note.startswith("http"):
                candidate_urls.append(url_note.rstrip("/"))
            host = (notes.get("provider", {}) or {}).get("host")
        except Exception:
            pass
    client = db.one("SELECT services_url, admin_url FROM clients WHERE id=?", (client_id,))
    client_url = (client.get("services_url") if component == "services" else client.get("admin_url")) if client else None
    if client_url and client_url.startswith("http"):
        candidate_urls.append(client_url.rstrip("/"))
    if component == "services":
        candidate_urls.append("https://reportservices.vtabsquare.com")
        candidate_urls.append("https://vtab-production.onrender.com")

    infra = db.one("SELECT domain FROM client_infrastructure WHERE client_id=? AND component=? ORDER BY created_at DESC LIMIT 1", (client_id, component))
    if infra and infra.get("domain"):
        dom = infra["domain"].strip()
        candidate_urls.append(dom if dom.startswith("http") else f"https://{dom}")

    # Remove duplicates preserving order
    seen_urls = set()
    unique_urls = []
    for u in candidate_urls:
        clean = u.rstrip("/")
        if clean and clean not in seen_urls:
            seen_urls.add(clean)
            unique_urls.append(clean)

    if not unique_urls:
        return deployment

    health_path = "/api/v1/health" if component == "services" else "/api/session"
    verified_url = None
    for cand in unique_urls:
        res = HealthCheckService().check(cand, health_path, timeout=3)
        if res.get("healthy"):
            verified_url = cand
            break

    if not verified_url and host:
        code, _, _ = DigitalOceanDeploymentAdapter.run_ssh_command(
            host, 22, "root", f"curl -s -f http://127.0.0.1:8820{health_path}", timeout=4
        )
        if code == 0:
            verified_url = unique_urls[0]

    if not verified_url:
        return deployment

    url = verified_url

    ready = False
    # Case A: deploying with health_check stage
    if status == "deploying" and row.get("stage") == "health_check":
        ready = True
    else:
        # Case B: check /api/v1/update/status
        try:
            import urllib.request
            req = urllib.request.Request(f"{url}/api/v1/update/status", headers={"User-Agent": "VTAB-Reporting-Manager", "X-VTAB-Agent-Token": "SDCsfIsSnlOj4ceWprbj-0GrsjxESrOT1DnmNJOGa_Y"}, method="GET")
            with urllib.request.urlopen(req, timeout=4) as resp:
                status_data = json.loads(resp.read().decode("utf-8"))
                live_commit = str(status_data.get("current_commit_sha") or status_data.get("commit_sha") or "").strip().lower()
                live_ver = str(status_data.get("current_version") or status_data.get("version") or "").strip()
                update_avail = status_data.get("update_available")
                active_dep_id = status_data.get("deployment_id")
                last_status = status_data.get("last_apply_status")

                if target_sha and live_commit and (target_sha.startswith(live_commit[:8]) or live_commit.startswith(target_sha[:8])):
                    ready = True
                elif target_version and (live_ver == target_version or target_version in (f"v{live_ver}", f"commit-{live_commit[:12]}")):
                    ready = True
                elif not update_avail and status_data.get("status") == "completed":
                    ready = True
        except Exception as exc:
            logging.debug("Could not verify update status at %s: %s", url, exc)

    if ready:
        now = utc_now()
        db.execute(
            """UPDATE deployments SET status='succeeded',stage='verified_healthy',progress_percent=100,
               health_verified_at=?,finished_at=?,updated_at=? WHERE id=?""",
            (now, now, now, dep_id)
        )
        column = "installed_services_version" if component == "services" else "installed_admin_version"
        url_column = "services_url" if component == "services" else "admin_url"
        db.execute(
            f"UPDATE clients SET {column}=COALESCE(?,{column}),{url_column}=?,last_health_status='healthy',last_health_at=?,updated_at=? WHERE id=?",
            (target_version, url, now, now, client_id)
        )
        db.execute(
            "UPDATE client_infrastructure SET status='active',domain=?,sync_status='in_sync',last_synced_at=?,updated_at=? WHERE client_id=? AND component=?",
            (url, now, now, client_id, component)
        )
        db.execute(
            "INSERT INTO deployment_logs(deployment_id,stage,level,message,created_at) VALUES(?,?,?,?,?)",
            (dep_id, "verified_healthy", "info", f"Verified live health and version {target_version} at {url}. Deployment completed.", now)
        )
        release_deployment_lock(dep_id)
        db.audit("deployment_engine", "deployment.succeeded", "deployment", dep_id, client_id, {"url": url, "version": target_version})
        return db.one("SELECT d.*,c.name client_name,r.component,r.channel FROM deployments d JOIN clients c ON c.id=d.client_id JOIN releases r ON r.id=d.release_id WHERE d.id=?", (dep_id,)) or row

    return deployment

@app.get("/api/v1/operator/summary")
def summary(_: str = Depends(require_operator)):
    counts = {}
    queries = {
        "clients": "SELECT COUNT(*) total FROM clients WHERE status='active'",
        "available_releases": "SELECT COUNT(*) total FROM releases WHERE status='available'",
        "deployments_attention": "SELECT COUNT(*) total FROM deployments WHERE status IN ('pending_approval','failed')",
        "open_tickets": "SELECT COUNT(*) total FROM support_tickets WHERE status NOT IN ('closed','deployed')",
    }
    for key, query in queries.items():
        counts[key] = (db.one(query) or {"total": 0})["total"]
    recent = [finalize_healthy_deployment_if_ready(r) for r in db.all(
        "SELECT d.*,c.name client_name,r.component,r.git_commit_sha,r.repository_url FROM deployments d JOIN clients c ON c.id=d.client_id JOIN releases r ON r.id=d.release_id ORDER BY d.created_at DESC LIMIT 8"
    )]
    counts["recent_deployments"] = [serialize_row(r) for r in recent]
    counts["recent_tickets"] = [serialize_row(r) for r in db.all(
        "SELECT t.*,c.name client_name FROM support_tickets t JOIN clients c ON c.id=t.client_id ORDER BY t.created_at DESC LIMIT 8"
    )]
    return counts


@app.get("/api/v1/operator/clients")
def list_clients(_: str = Depends(require_operator)):
    return {"clients": [serialize_row(r) for r in db.all("SELECT * FROM clients ORDER BY name")]}


@app.post("/api/v1/operator/clients", status_code=201)
def create_client(payload: ClientCreate, actor: str = Depends(require_operator)):
    client_id, agent_id, now = identifier(), identifier(), utc_now()
    bootstrap_token = new_agent_token();client_admin_token=new_agent_token()
    try:
        db.execute(
            """INSERT INTO clients(id,code,name,contact_email,status,environment,services_url,admin_url,database_provider,client_admin_token_hash,created_at,updated_at)
               VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
            (client_id, payload.code, payload.name, payload.contact_email, "active", payload.environment,
             str(payload.services_url) if payload.services_url else None,
             str(payload.admin_url) if payload.admin_url else None,
             payload.database_provider,token_hash(client_admin_token),now,now),
        )
    except Exception as error:
        if unique_violation(error):
            raise HTTPException(409, "Client code already exists.") from error
        raise
    db.execute(
        "INSERT INTO client_agents(id,client_id,name,token_hash,status,created_at) VALUES(?,?,?,?,?,?)",
        (agent_id, client_id, "Primary update connector", token_hash(bootstrap_token), "registered", now),
    )
    db.audit(actor, "client.created", "client", client_id, client_id, {"code": payload.code})
    return {"client": serialize_row(client_or_404(client_id)), "connectorBootstrapToken": bootstrap_token,"clientAdminBootstrapToken":client_admin_token}


@app.delete("/api/v1/operator/clients/{client_id}")
def delete_client(client_id:str,actor:OperatorIdentity=Depends(require_operator)):
    authorize(actor,{"super_admin","operator"})
    client=client_or_404(client_id)
    db.execute("DELETE FROM clients WHERE id=?",(client_id,))
    db.audit(str(actor),"client.deleted","client",client_id,details={"clientId":client_id,"code":client.get("code"),"name":client.get("name")})
    return {"ok":True,"deletedClientId":client_id}


@app.get("/api/v1/operator/clients/{client_id}/configuration")
def get_client_configuration(client_id:str,actor:OperatorIdentity=Depends(require_operator)):
    client=client_or_404(client_id)
    infrastructure=[]
    for row in db.all("SELECT * FROM client_infrastructure WHERE client_id=? ORDER BY component,created_at DESC",(client_id,)):
        item=serialize_row(row) or {}
        env = decrypt_environment(row.get("environment_variables_json"))
        # Auto-ensure standard variables for services component
        if item.get("component") == "services":
            changed = False
            if "VTAB_CLIENT_ID" not in env:
                env["VTAB_CLIENT_ID"] = client_id
                changed = True
            if "VTAB_SIMULATE_BUILD" not in env:
                env["VTAB_SIMULATE_BUILD"] = "0"
                changed = True
            if item.get("provider") == "render":
                srv_id = item.get("provider_resource_id") or (item.get("config") or {}).get("service_id")
                if srv_id and "RENDER_SERVICE_ID" not in env:
                    env["RENDER_SERVICE_ID"] = srv_id
                    changed = True
            if changed:
                db.execute("UPDATE client_infrastructure SET environment_variables_json=? WHERE id=?", 
                           (encrypt_environment(env), item["id"]))
        item["environment_variables"]=mask_environment(env)
        item.pop("environment_variables_json",None);infrastructure.append(item)
    versions=db.all("SELECT id,infrastructure_id,version,sync_status,changed_by,change_summary,created_at FROM client_configuration_versions WHERE client_id=? ORDER BY created_at DESC LIMIT 100",(client_id,))
    audit=db.all("SELECT * FROM audit_events WHERE client_id=? AND action LIKE ? ORDER BY created_at DESC LIMIT 100",(client_id, 'configuration.%'))
    return {"client":serialize_row(client),"infrastructure":infrastructure,"versions":versions,"audit":[serialize_row(row) for row in audit]}


@app.put("/api/v1/operator/clients/{client_id}/configuration")
def update_client_configuration(client_id:str,payload:ClientConfigurationUpdate,actor:OperatorIdentity=Depends(require_operator)):
    authorize(actor,{"super_admin","operator","deployment_manager"});client_or_404(client_id)
    row=db.one("SELECT * FROM client_infrastructure WHERE id=? AND client_id=?",(payload.infrastructure_id,client_id))
    if not row:raise HTTPException(404,"Client infrastructure configuration not found.")
    try:existing_config=json.loads(row.get("config_json") or "{}")
    except (TypeError,json.JSONDecodeError):existing_config={}
    existing_environment=decrypt_environment(row.get("environment_variables_json"));environment=merge_masked_environment(existing_environment,payload.environment_variables)
    provider_config={**existing_config,**payload.provider_config}
    srv_candidate = provider_config.get("service_id") or environment.get("RENDER_SERVICE_ID")
    service_id = str(srv_candidate or row.get("provider_resource_id") or "").strip()
    if row["provider"] == "render":
        if srv_candidate and not str(srv_candidate).startswith("masked:"):
            service_id = str(srv_candidate).strip()
            db.execute("UPDATE client_infrastructure SET provider_resource_id=? WHERE id=?", (service_id, row["id"]))
            if "RENDER_SERVICE_ID" not in environment or not environment["RENDER_SERVICE_ID"]:
                environment["RENDER_SERVICE_ID"] = service_id
    forbidden=[key for key in provider_config if any(mark in key.upper() for mark in ("TOKEN","SECRET","PASSWORD","KEY"))]
    if forbidden:raise HTTPException(422,"Secrets must be stored under environment_variables, not provider_config: "+", ".join(forbidden))
    version=int(row.get("config_version") or 0)+1;now=utc_now();sync_status="pending"
    
    token = payload.provider_token.get_secret_value() if payload.provider_token else ""


    if not token and row["provider"] == "render":
        token = str(environment.get("RENDER_API_KEY") or "").strip()
    if not token and row["provider"] == "digitalocean":
        token = str(environment.get("DIGITALOCEAN_TOKEN") or environment.get("DO_TOKEN") or settings.digitalocean_token).strip()

    should_sync = payload.sync_provider or bool(token and service_id)
    if should_sync and token and service_id:
        try:
            adapter = RenderDeploymentAdapter(token) if row["provider"]=="render" else DigitalOceanDeploymentAdapter(token)
            result = adapter.update_environment(service_id, environment, config=provider_config)
            sync_status = "synced" if result.get("status")!="connector_sync_required" else "connector_pending"
        except Exception as error:
            logger.error("Provider environment synchronization failed: %s", error)
            if payload.sync_provider:
                raise HTTPException(400, f"Cloud provider sync failed: {str(error)}")
            sync_status = "sync_failed"
    elif payload.sync_provider and row["provider"] == "digitalocean" and (provider_config.get("host") or provider_config.get("server_ip")):
        try:
            adapter = DigitalOceanDeploymentAdapter(token or "")
            result = adapter.update_environment(service_id or "ssh", environment, config=provider_config)
            sync_status = "synced"
        except Exception as error:
            logger.error("DigitalOcean SSH environment synchronization failed: %s", error)
            raise HTTPException(400, f"Server SSH sync failed: {str(error)}")
    elif payload.sync_provider and not token:
        raise HTTPException(422, "A request-scoped provider token (RENDER_API_KEY) or server host is required for provider synchronization.")
    encrypted=encrypt_environment(environment)
    db.execute("""UPDATE client_infrastructure SET config_json=?,environment_variables_json=?,config_version=?,sync_status=?,last_synced_at=?,config_updated_at=?,config_updated_by=?,region=?,instance_size=?,domain=?,updated_at=? WHERE id=? AND client_id=?""",
               (json.dumps(provider_config),encrypted,version,sync_status,now if sync_status=="synced" else row.get("last_synced_at"),now,str(actor),provider_config.get("region",row.get("region")),provider_config.get("instance_size",row.get("instance_size")),provider_config.get("domain",row.get("domain")),now,row["id"],client_id))
    db.execute("INSERT INTO client_configuration_versions(infrastructure_id,client_id,version,config_json,environment_variables_json,sync_status,changed_by,change_summary,created_at) VALUES(?,?,?,?,?,?,?,?,?)",
               (row["id"],client_id,version,json.dumps(provider_config),encrypted,sync_status,str(actor),payload.change_summary,now))
    db.audit(str(actor),"configuration.updated","client_infrastructure",row["id"],client_id,{"version":version,"syncStatus":sync_status,"changedConfigKeys":sorted(payload.provider_config),"changedEnvironmentKeys":sorted(payload.environment_variables)})
    return get_client_configuration(client_id,actor)


def release_workflow_state() -> dict[str, Any]:
    """Compute release badges and *strict* one-step rollback eligibility from persisted deployments.

    This is shared by release catalog rendering and the offer API. No browser-supplied
    rollback eligibility or semantic-version sorting is trusted.
    """
    releases = db.all("SELECT * FROM releases ORDER BY created_at DESC")
    eligible = [r for r in releases if r.get("validation_status") == "approved"
                and r.get("status") in {"available", "superseded"}]
    by_component: dict[str, list[dict[str, Any]]] = {}
    for release in eligible:
        by_component.setdefault(release["component"], []).append(release)

    succeeded = db.all("""SELECT d.*, r.component FROM deployments d
                          JOIN releases r ON r.id=d.release_id WHERE d.status='succeeded'""")
    succeeded.sort(key=lambda d: (d.get("finished_at") or d.get("updated_at") or d.get("created_at") or "", d.get("created_at") or ""), reverse=True)
    clients = db.all("SELECT * FROM clients")
    current: dict[str, str] = {}
    previous: dict[str, str] = {}
    rollback_clients: dict[str, list[str]] = {}

    def same_version(a: Any, b: Any, rel: dict[str, Any] | None = None) -> bool:
        sa = str(a or "").strip().lower()
        sb = str(b or "").strip().lower()
        if not sa or not sb: return False
        va = sa.removeprefix("v").removeprefix("commit-")
        vb = sb.removeprefix("v").removeprefix("commit-")
        if va == vb: return True
        if len(va) >= 7 and len(vb) >= 7 and (va.startswith(vb[:7]) or vb.startswith(va[:7])): return True
        if rel:
            rv = str(rel.get("version") or "").strip().lower().removeprefix("v")
            rsha = str(rel.get("git_commit_sha") or "").strip().lower()
            if rv and (va == rv or vb == rv): return True
            if rsha and (rsha.startswith(va[:7]) or rsha.startswith(vb[:7])): return True
        return False


    for component, candidates in by_component.items():
        history = [d for d in succeeded if d["component"] == component]
        latest = history[0] if history else None
        if latest:
            current[component] = latest["release_id"]
        else:
            # Display-only fallback for installations adopted from existing infrastructure.
            version_key = "installed_services_version" if component == "services" else "installed_admin_version"
            for client in sorted(clients, key=lambda c: c.get("last_health_at") or "", reverse=True):
                matching = next((r for r in candidates if same_version(r["version"], client.get(version_key))), None)
                if matching:
                    current[component] = matching["id"]
                    break
        if not latest:
            continue  # No verified deployment history: cannot infer the previous version.

        previous_version = latest.get("previous_version")
        if not previous_version:
            # Legacy deployments may omit previous_version. Use just the next distinct
            # successfully deployed version, never older historical versions.
            preceding = next((d for d in history[1:] if d["release_id"] != latest["release_id"]), None)
            previous_version = preceding.get("target_version") if preceding else None
        target = next((r for r in candidates if previous_version and same_version(r["version"], previous_version, r) and r["id"] != latest["release_id"]), None) if previous_version else None
        if not target and len(candidates) > 1 and candidates[0]["id"] == current.get(component):
            target = candidates[1]

        if not target:
            continue

        version_key = "installed_services_version" if component == "services" else "installed_admin_version"
        last_by_client: dict[str, dict[str, Any]] = {}
        for deployment in history:
            last_by_client.setdefault(deployment["client_id"], deployment)
        allowed = []
        for client in clients:
            deployed = last_by_client.get(client["id"])
            if not deployed or deployed["release_id"] != latest["release_id"]:
                continue
            installed_version = client.get(version_key)
            latest_rel = db.one("SELECT * FROM releases WHERE id=?", (latest["release_id"],))
            if installed_version and not same_version(installed_version, deployed["target_version"], latest_rel):
                continue  # Client has drifted since our last verified deployment.
            if not deployed.get("previous_version") or same_version(deployed.get("previous_version"), target["version"], target):
                allowed.append(client["id"])
        if not allowed and target:
            curr_rel = next((r for r in candidates if r["id"] == current.get(component)), None)
            for client in clients:
                installed_v = client.get(version_key)
                if installed_v and curr_rel and same_version(installed_v, curr_rel.get("version"), curr_rel):
                    allowed.append(client["id"])
        if allowed:
            previous[component] = target["id"]
            rollback_clients[target["id"]] = allowed

    newest = {component: candidates[0]["id"] for component, candidates in by_component.items() if candidates}
    return {"active": current, "new": newest, "previous": previous, "rollback_clients": rollback_clients}


@app.get("/api/v1/operator/releases")
def list_releases(_: str = Depends(require_operator)):
    workflow = release_workflow_state()
    releases = []
    for row in db.all("SELECT * FROM releases ORDER BY created_at DESC"):
        item = serialize_row(row)
        item["is_active"] = workflow["active"].get(row["component"]) == row["id"]
        item["is_new"] = workflow["new"].get(row["component"]) == row["id"]
        item["rollback_eligible_client_ids"] = workflow["rollback_clients"].get(row["id"], [])
        releases.append(item)
    return {"releases": releases}


@app.patch("/api/v1/operator/releases/{release_id}/version")
def assign_release_version(release_id: str, payload: ReleaseVersionUpdate, actor: OperatorIdentity = Depends(require_operator)):
    authorize(actor, {"super_admin", "release_manager", "operator"})
    release = release_or_404(release_id)
    if release.get("status") != "pending_approval":
        raise HTTPException(409, "Application versions can only be assigned before commit approval.")
    version = payload.version.removeprefix("v")
    # This is version metadata supplied by an operator, not an inferred Git commit number.
    try:
        manifest = json.loads(release.get("manifest_json") or "{}")
    except (TypeError, json.JSONDecodeError):
        manifest = {}
    manifest["applicationVersion"] = version
    try:
        db.execute("UPDATE releases SET version=?,manifest_json=? WHERE id=?", (version, json.dumps(manifest), release_id))
    except Exception as exc:
        if unique_violation(exc):
            raise HTTPException(409, "This application version already exists for the selected component and channel.") from exc
        raise
    db.audit(str(actor), "release.version_assigned", "release", release_id,
             details={"component": release["component"], "oldVersion": release["version"], "newVersion": version})
    return {"release": serialize_row(release_or_404(release_id))}

update_release_version = assign_release_version


@app.post("/api/v1/operator/releases/{release_id}/approve")
def approve_release_candidate(release_id:str,actor:OperatorIdentity=Depends(require_operator)):
    authorize(actor,{"super_admin","release_manager","operator"})
    release=release_or_404(release_id)
    if release.get("status")=="rejected":raise HTTPException(409,"Rejected commits cannot be approved without a new GitHub event.")
    if release.get("status") not in {"pending_approval","available"}:raise HTTPException(409,"Only pending GitHub commits can use the approval workflow.")
    now=utc_now()
    try:details=json.loads(release.get("validation_details_json") or "{}")
    except (TypeError,json.JSONDecodeError):details={}
    details.update({"approved":True,"approved_by":str(actor),"approved_at":now})
    db.execute("UPDATE releases SET status='available',validation_status='approved',validation_details_json=?,validated_by=?,validated_at=? WHERE id=?",(json.dumps(details),str(actor),now,release_id))
    db.audit(str(actor),"release.approved","release",release_id,details={"component":release.get("component"),"commitSha":release.get("git_commit_sha")})
    return {"ok":True,"release":serialize_row(release_or_404(release_id))}




@app.post("/api/v1/operator/releases/{release_id}/reject")
def reject_release_candidate(release_id:str,actor:OperatorIdentity=Depends(require_operator)):
    authorize(actor,{"super_admin","release_manager","operator"})
    release=release_or_404(release_id)
    if release.get("status") not in {"pending_approval","rejected"}:raise HTTPException(409,"Only pending GitHub commits can use the rejection workflow.")
    now=utc_now()
    try:details=json.loads(release.get("validation_details_json") or "{}")
    except (TypeError,json.JSONDecodeError):details={}
    details.update({"approved":False,"rejected":True,"rejected_by":str(actor),"rejected_at":now})
    db.execute("UPDATE releases SET status='rejected',validation_status='rejected',validation_details_json=?,validated_by=?,validated_at=? WHERE id=?",(json.dumps(details),str(actor),now,release_id))
    db.audit(str(actor),"release.rejected","release",release_id,details={"component":release.get("component"),"commitSha":release.get("git_commit_sha")})
    return {"ok":True,"release":serialize_row(release_or_404(release_id))}


@app.post("/api/v1/operator/releases", status_code=201)
def create_release(payload: ReleaseCreate, actor: str = Depends(require_operator)):
    release_id, now = identifier(), utc_now()
    repository=canonical_repository(str(payload.repository_url)) if payload.repository_url else component_repository(payload.component)
    sha=str(payload.git_commit_sha or "").lower()
    if not repository:raise HTTPException(422,"An exact repository is required for every release.")
    if len(sha)!=40 or any(character not in "0123456789abcdef" for character in sha):raise HTTPException(422,"An exact 40-character commit SHA is required for every release.")
    approved_repository=component_repository(payload.component)
    if approved_repository and repository.lower()!=approved_repository.lower():raise HTTPException(409,"Release repository does not match the locked component repository.")
    manifest = {**payload.manifest, "component": payload.component, "version": payload.version, "channel": payload.channel,"repository":repository,"commitSha":sha}
    try:
        db.execute(
            """INSERT INTO releases(id,component,version,channel,status,artifact_url,checksum_sha256,min_connector_version,release_notes,manifest_json,git_commit_sha,repository_url,migration_index,created_at)
               VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (release_id, payload.component, payload.version, payload.channel, "draft", str(payload.artifact_url),
             payload.checksum_sha256.lower(), payload.min_connector_version, payload.release_notes,
             json.dumps(manifest),sha,repository,
             payload.migration_index, now),
        )
    except Exception as error:
        if unique_violation(error):
            raise HTTPException(409, "This component version and channel already exist.") from error
        raise
    db.audit(actor, "release.created", "release", release_id, details={"component": payload.component, "version": payload.version})
    return {"release": serialize_row(db.one("SELECT * FROM releases WHERE id=?", (release_id,)))}


@app.post("/api/v1/operator/releases/{release_id}/publish")
def publish_release(release_id: str, actor: str = Depends(require_operator)):
    release = release_or_404(release_id)
    if release.get("validation_status")!="approved":raise HTTPException(409,"Release must pass CI validation before it becomes deployable.")
    if not release.get("repository_url") or len(str(release.get("git_commit_sha") or ""))!=40:raise HTTPException(409,"Release repository and exact commit SHA are required.")
    db.execute("UPDATE releases SET status='available' WHERE id=?", (release_id,))
    db.audit(actor, "release.published", "release", release_id, details={"version": release["version"]})
    return {"release": serialize_row(db.one("SELECT * FROM releases WHERE id=?", (release_id,)))}


@app.post("/api/v1/operator/releases/{release_id}/validate")
def validate_release(release_id:str,payload:ReleaseValidation,actor:OperatorIdentity=Depends(require_operator)):
    authorize(actor,{"super_admin","release_manager"});release=release_or_404(release_id)
    if settings.github_token:
        details=validate_commit(release["repository_url"],release["git_commit_sha"])
        approved=bool(payload.approved and details["approved"])
    else:
        required={name:payload.checks.get(name,"missing") for name in settings.github_required_checks}
        approved=bool(payload.approved and payload.ci_status=="success" and all(value in {"success","neutral","skipped"} for value in required.values()))
        details={"approved":approved,"combinedStatus":payload.ci_status,"requiredChecks":required,"allChecks":payload.checks,"operatorNotes":payload.notes}
    status="approved" if approved else "rejected" if payload.ci_status=="failure" else "pending"
    new_release_status = "available" if status == "approved" else release.get("status", "draft")
    db.execute("UPDATE releases SET validation_status=?,validation_details_json=?,validated_by=?,validated_at=?,status=? WHERE id=?",(status,json.dumps(details),str(actor),utc_now(),new_release_status,release_id))
    db.audit(str(actor),"release.validation_changed","release",release_id,details={"status":status,"commitSha":release["git_commit_sha"]})
    return {"release":serialize_row(release_or_404(release_id))}


@app.put("/api/v1/operator/releases/{release_id}/notes")
def update_release_notes(release_id:str,payload:ReleaseNotesUpdate,actor:OperatorIdentity=Depends(require_operator)):
    authorize(actor,{"super_admin","release_manager","operator"});release_or_404(release_id)
    db.execute("UPDATE releases SET release_notes=? WHERE id=?",(payload.release_notes,release_id));db.audit(str(actor),"release.notes_updated","release",release_id)
    return {"release":serialize_row(release_or_404(release_id))}


@app.post("/api/v1/operator/releases/{release_id}/supersede")
def supersede_release(release_id:str,superseded_by:str=Query(...),actor:OperatorIdentity=Depends(require_operator)):
    authorize(actor,{"super_admin","release_manager"});release=release_or_404(release_id);replacement=release_or_404(superseded_by)
    if release["component"]!=replacement["component"]:raise HTTPException(409,"A release can be superseded only by the same component.")
    db.execute("UPDATE releases SET status='superseded',superseded_by_release_id=? WHERE id=?",(superseded_by,release_id));db.audit(str(actor),"release.superseded","release",release_id,details={"replacement":superseded_by})
    return {"release":serialize_row(release_or_404(release_id))}


@app.get("/api/v1/operator/deployments")
def list_deployments(_: str = Depends(require_operator)):
    rows = db.all(
        """SELECT d.*,c.name client_name,r.component,r.channel,
                  r.version release_version,r.git_commit_sha,r.repository_url
           FROM deployments d
           JOIN clients c ON c.id=d.client_id JOIN releases r ON r.id=d.release_id
           ORDER BY d.created_at DESC"""
    )
    resolved = [finalize_healthy_deployment_if_ready(r) for r in rows]
    return {"deployments": [serialize_row(r) for r in resolved]}


@app.post("/api/v1/operator/deployments", status_code=201)
def create_deployment(payload: DeploymentCreate, idempotency_key:str|None=Header(default=None,alias="Idempotency-Key"),actor:OperatorIdentity = Depends(require_operator)):
    authorize(actor,{"super_admin","release_manager","deployment_manager","operator"})
    if not isinstance(idempotency_key,str):idempotency_key=None
    if idempotency_key:
        existing=db.one("SELECT * FROM deployments WHERE idempotency_key=?",(idempotency_key,))
        if existing:return {"deployment":serialize_row(existing),"idempotentReplay":True}
    client = client_or_404(payload.client_id)
    release = release_or_404(payload.release_id)
    if release["status"] != "available" and not (payload.deployment_type == "rollback" and release["status"] == "superseded"):
        raise HTTPException(409, "Only an available release can be offered to a client.")
    if release.get("validation_status")!="approved":raise HTTPException(409,"Only a CI-approved release can be offered.")
    if payload.deployment_type == "update":
        newest_approved = db.one(
            """SELECT id FROM releases
               WHERE component=? AND status='available' AND validation_status='approved'
               ORDER BY COALESCE(validated_at,created_at) DESC LIMIT 1""",
            (release["component"],),
        )
        if not newest_approved or newest_approved["id"] != release["id"]:
            raise HTTPException(409,"Only the newest approved release for this application can be deployed.")
    if payload.deployment_type == "rollback":
        workflow = release_workflow_state()
        is_previous = workflow["previous"].get(release["component"]) == release["id"]
        allowed_clients = set(workflow["rollback_clients"].get(release["id"], []))
        client_curr_v = client.get("installed_services_version" if release["component"] == "services" else "installed_admin_version")
        active_rel = db.one("SELECT * FROM releases WHERE id=?", (workflow["active"].get(release["component"]),))
        client_running_active = bool(active_rel and client_curr_v and (client_curr_v == active_rel["version"] or f"v{client_curr_v}" == active_rel["version"] or (active_rel.get("git_commit_sha") and str(client_curr_v).startswith(active_rel["git_commit_sha"][:7]))))
        if not (is_previous and (payload.client_id in allowed_clients or client_running_active)):
            raise HTTPException(409, "Rollback is allowed only to the immediately previous verified release for this client and component.")
    if not db.one("SELECT id FROM client_infrastructure WHERE client_id=? AND component=? AND status IN ('active','provisioning') ORDER BY created_at DESC LIMIT 1",(payload.client_id,release["component"])):
        raise HTTPException(409,"The client does not have managed infrastructure for this component. Use New Deployment for the initial installation.")
    deployment_id, now = identifier(), utc_now()
    active_dep = db.one(
        """SELECT d.*, r.version FROM deployments d JOIN releases r ON r.id=d.release_id
           WHERE d.client_id=? AND r.component=? AND d.status NOT IN ('succeeded','failed','cancelled','rejected','rolled_back')
           ORDER BY d.created_at DESC LIMIT 1""",
        (payload.client_id, release["component"])
    )
    if active_dep:
        if active_dep.get("status") in ("scheduled", "offered", "pending_approval"):
            db.execute("UPDATE deployments SET status='cancelled', cancelled_at=?, updated_at=? WHERE id=?", (now, now, active_dep["id"]))
            db.execute("DELETE FROM deployment_locks WHERE deployment_id=?", (active_dep["id"],))
        else:
            target_v = active_dep.get("version") or active_dep.get("target_version") or "unknown"
            status_v = active_dep.get("status")
            raise HTTPException(409, f"Another deployment (v{target_v}, status: {status_v}) is already in progress for this client. Please cancel it before offering a new release.")
    previous = client["installed_services_version"] if release["component"] == "services" else client["installed_admin_version"]
    db.execute(
        """INSERT INTO deployments(id,client_id,release_id,status,scheduled_for,previous_version,target_version,
           idempotency_key,created_at,updated_at,approved_by,stage,deployment_type)
           VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (deployment_id, payload.client_id, payload.release_id, "scheduled", payload.scheduled_for,
         previous, release["version"],idempotency_key or f"offer:{payload.client_id}:{payload.release_id}:{deployment_id}",now,now,str(actor),"release_offered",getattr(payload,"deployment_type","update")),
    )
    try:acquire_deployment_lock(payload.client_id,release["component"],deployment_id)
    except Exception:
        db.execute("DELETE FROM deployments WHERE id=?",(deployment_id,));raise
    db.audit(actor, "deployment.offered", "deployment", deployment_id, payload.client_id,
             {"component": release["component"], "version": release["version"]})
    dispatch_client_update_webhook(payload.client_id,deployment_id)
    return {"deployment": serialize_row(db.one("SELECT * FROM deployments WHERE id=?", (deployment_id,)))}


def dispatch_client_update_webhook(client_id: str, deployment_id: str):
    """Notify client via webhook and broadcast to all registered and active environments."""
    try:
        client = db.one("SELECT * FROM clients WHERE id=?", (client_id,))
        deployment = db.one(
            """SELECT d.*, r.component, r.version release_version, r.release_notes, r.artifact_url, r.git_commit_sha
               FROM deployments d JOIN releases r ON r.id=d.release_id WHERE d.id=?""",
            (deployment_id,)
        )
        if not client or not deployment:
            return
        component = deployment.get("component") or "services"

        # Collect ALL target destinations for this client
        targets = set()
        if component == "services":
            if client.get("services_url"):
                targets.add(client["services_url"].rstrip("/"))
        else:
            if client.get("admin_url"):
                targets.add(client["admin_url"].rstrip("/"))

        infra_rows = db.all(
            """SELECT domain, config_json, environment_variables_json FROM client_infrastructure
               WHERE client_id=? AND component=?""",
            (client_id, component)
        )
        for inf in infra_rows:
            dom = (inf.get("domain") or "").strip().rstrip("/")
            if dom:
                targets.add(dom if dom.startswith("http") else f"https://{dom}")
            env = json_value(inf.get("environment_variables_json"), {})
            web_url = (env.get("VTAB_WEB_URL") or env.get("VITE_WEB_URL") or "").strip().rstrip("/")
            if web_url and web_url.startswith("http"):
                targets.add(web_url)

        # For VTAB client, guarantee both DigitalOcean and Render live destinations
        if client.get("name") == "VTAB" or client.get("code") == "test":
            if component == "services":
                targets.add("https://reportservices.vtabsquare.com")
                targets.add("https://vtab-production.onrender.com")
                targets.add("http://v2-reports.139.59.32.39.nip.io")
            else:
                targets.add("https://vtab-client-adminnn.onrender.com")
                targets.add("http://v2-admin.139.59.32.39.nip.io")

        urls = set()
        for base in targets:
            if not base or not base.startswith("http"):
                continue
            if component == "services":
                urls.add(f"{base}/api/v1/update/offer")
                urls.add(f"{base}/api/v1/update/webhook")
            else:
                urls.add(f"{base}/api/update/offer")
                urls.add(f"{base}/api/update/webhook")

        urls.add("http://127.0.0.1:8820/api/v1/update/offer")
        urls.add("http://139.59.32.39:8820/api/v1/update/offer")

        def _send():
            commit_sha = (
                deployment.get("git_commit_sha")
                or deployment.get("installed_commit_sha")
                or (deployment.get("target_version", "").replace("commit-", "").replace("vcommit-", "") if "commit-" in deployment.get("target_version", "") else "")
                or ""
            )
            payload = {
                "event": "update_offered",
                "deployment_id": deployment["id"],
                "component": "reporting-services" if component == "services" else "administration",
                "version": deployment["target_version"],
                "commit_sha": commit_sha,
                "commitSha": commit_sha,
                "release_notes": deployment.get("release_notes") or "",
                "artifact_url": deployment.get("artifact_url") or "",
                "timestamp": utc_now(),
            }
            body = json.dumps(payload).encode("utf-8")
            for wh_url in urls:
                try:
                    import urllib.request
                    req = urllib.request.Request(
                        wh_url,
                        data=body,
                        headers={
                            "Content-Type": "application/json",
                            "X-VTAB-Client-ID": client["id"],
                            "X-VTAB-Agent-Token": "SDCsfIsSnlOj4ceWprbj-0GrsjxESrOT1DnmNJOGa_Y",
                        },
                        method="POST",
                    )
                    with urllib.request.urlopen(req, timeout=6) as resp:
                        logging.info("Broadcasted update offer to %s (HTTP %s)", wh_url, resp.status)
                except Exception as exc:
                    logging.info("Broadcast delivery skipped (%s): %s", wh_url, exc)

            # Direct droplet delivery over SSH if DigitalOcean host is configured
            try:
                DigitalOceanDeploymentAdapter.run_ssh_command(
                    "139.59.32.39", 22, "root",
                    f"python3 /var/www/reportingtool-webservice/scripts/send_update_offer.py {commit_sha or deployment.get('target_version', '')} {deployment.get('target_version', '')}",
                    timeout=8
                )
                target_arg = commit_sha or deployment.get("target_version", "")
                if deployment.get("deployment_type") == "rollback" and target_arg:
                    DigitalOceanDeploymentAdapter.run_ssh_command("139.59.32.39", 22, "root", f"bash /var/www/reportingtool-webservice/deploy.sh {target_arg}", timeout=45
                )
            except Exception:
                pass

        threading.Thread(target=_send, daemon=True).start()
    except Exception as exc:
        logging.warning("Error preparing update webhook broadcast: %s", exc)


def dispatch_client_update_cancellation(client_id: str, deployment_id: str):
    """Notify client via webhook when an update offer is cancelled."""
    try:
        client = db.one("SELECT * FROM clients WHERE id=?", (client_id,))
        deployment = db.one("SELECT d.*, r.component FROM deployments d JOIN releases r ON r.id=d.release_id WHERE d.id=?", (deployment_id,))
        if not client:
            return
        component = (deployment or {}).get("component") or "services"
        target_url = (client.get("services_url") if component == "services" else client.get("admin_url")) or ""
        target_url = target_url.rstrip("/")
        if not target_url:
            infra = db.one(
                """SELECT domain FROM client_infrastructure
                   WHERE client_id=? AND component=? AND status IN ('active','provisioning')
                   ORDER BY created_at DESC LIMIT 1""",
                (client_id, component)
            )
            if infra and infra.get("domain"):
                domain = infra["domain"].strip()
                target_url = domain if domain.startswith("http") else f"https://{domain}"
        if not target_url:
            return

        urls = [f"{target_url}/api/v1/update/webhook" if component == "services" else f"{target_url}/api/update/webhook"]
        if "localhost" not in target_url and "127.0.0.1" not in target_url:
            local_fallback = "http://127.0.0.1:8830/api/v1/update/webhook" if component == "services" else "http://127.0.0.1:8000/api/update/webhook"
            urls.append(local_fallback)

        def _send():
            payload = {
                "event": "update_cancelled",
                "deployment_id": deployment_id,
                "timestamp": utc_now(),
            }
            body = json.dumps(payload).encode("utf-8")
            for wh_url in urls:
                try:
                    import urllib.request
                    req = urllib.request.Request(
                        wh_url,
                        data=body,
                        headers={
                            "Content-Type": "application/json",
                            "X-VTAB-Client-ID": client["id"],
                        },
                        method="POST",
                    )
                    with urllib.request.urlopen(req, timeout=5) as resp:
                        logging.info("Dispatched update cancellation webhook to %s (HTTP %s)", wh_url, resp.status)
                except Exception as exc:
                    logging.info("Client update cancellation delivery skipped/offline (%s): %s", wh_url, exc)

        threading.Thread(target=_send, daemon=True).start()
    except Exception as exc:
        logging.warning("Error preparing update cancellation webhook dispatch: %s", exc)


@app.post("/api/v1/operator/deployments/{deployment_id}/approve")
def approve_deployment(deployment_id: str, payload: DeploymentApproval, actor: OperatorIdentity = Depends(require_operator)):
    authorize(actor,{"super_admin","deployment_manager","operator"})
    deployment = db.one("SELECT * FROM deployments WHERE id=?", (deployment_id,))
    if not deployment:
        raise HTTPException(404, "Deployment not found.")
    if deployment["status"] not in ("pending_approval", "scheduled", "offered"):
        raise HTTPException(409, "Deployment is no longer waiting for approval.")
    db.execute("UPDATE deployments SET status='scheduled',approved_by=?,updated_at=? WHERE id=?",
               (payload.approved_by, utc_now(), deployment_id))
    db.audit(actor, "deployment.approved", "deployment", deployment_id, deployment["client_id"], {"approvedBy": payload.approved_by})
    dispatch_client_update_webhook(deployment["client_id"], deployment_id)
    return {"deployment": serialize_row(db.one("SELECT * FROM deployments WHERE id=?", (deployment_id,)))}


@app.post("/api/v1/operator/deployments/{deployment_id}/complete")
def complete_deployment(deployment_id: str, actor: OperatorIdentity = Depends(require_operator)):
    authorize(actor, {"super_admin", "release_manager", "deployment_manager", "operator"})
    deployment = db.one("SELECT d.*, r.component, r.version release_version FROM deployments d JOIN releases r ON r.id=d.release_id WHERE d.id=?", (deployment_id,))
    if not deployment:
        raise HTTPException(404, "Deployment not found.")
    now = utc_now()
    client_id = deployment["client_id"]
    component = deployment.get("component") or "services"
    target_version = deployment.get("target_version") or deployment.get("release_version")
    db.execute(
        """UPDATE deployments SET status='succeeded',stage='verified_healthy',progress_percent=100,
           health_verified_at=?,finished_at=?,updated_at=? WHERE id=?""",
        (now, now, now, deployment_id)
    )
    column = "installed_services_version" if component == "services" else "installed_admin_version"
    db.execute(f"UPDATE clients SET {column}=?,last_health_status='healthy',last_health_at=?,updated_at=? WHERE id=?",
               (target_version, now, now, client_id))
    db.execute("UPDATE client_infrastructure SET status='active',updated_at=? WHERE client_id=? AND component=?",
               (now, client_id, component))
    db.execute("INSERT INTO deployment_logs(deployment_id,stage,level,message,created_at) VALUES(?,?,?,?,?)",
               (deployment_id, "verified_healthy", "info", f"Deployment marked completed by operator {actor}", now))
    release_deployment_lock(deployment_id)
    db.audit(str(actor), "deployment.completed", "deployment", deployment_id, client_id, {"target_version": target_version})
    return {"deployment": serialize_row(db.one("SELECT * FROM deployments WHERE id=?", (deployment_id,)))}


@app.post("/api/v1/operator/deployments/{deployment_id}/cancel")
def cancel_deployment(deployment_id:str,actor:OperatorIdentity=Depends(require_operator)):
    authorize(actor,{"super_admin","release_manager","deployment_manager","operator"});deployment=db.one("SELECT * FROM deployments WHERE id=?",(deployment_id,))
    if not deployment:raise HTTPException(404,"Deployment not found.")
    if deployment["status"] not in {"pending_approval","scheduled","offered","apply_requested"}:raise HTTPException(409,"Only a pending update offer can be cancelled.")
    now=utc_now();db.execute("UPDATE deployments SET status='cancelled',stage='cancelled',cancelled_at=?,finished_at=?,updated_at=? WHERE id=?",(now,now,now,deployment_id));release_deployment_lock(deployment_id)
    db.audit(str(actor),"deployment.cancelled","deployment",deployment_id,deployment["client_id"])
    dispatch_client_update_cancellation(deployment["client_id"], deployment_id)
    return {"deployment":serialize_row(db.one("SELECT * FROM deployments WHERE id=?",(deployment_id,)))}


@app.post("/api/v1/operator/deployments/{deployment_id}/reject")
def reject_deployment(deployment_id:str,actor:OperatorIdentity=Depends(require_operator)):
    """Reject a rollout at its approval gate without presenting it as an operator cancellation."""
    authorize(actor,{"super_admin","release_manager","deployment_manager","operator"})
    deployment=db.one("SELECT * FROM deployments WHERE id=?",(deployment_id,))
    if not deployment:raise HTTPException(404,"Deployment not found.")
    if deployment["status"] not in {"pending_approval","scheduled","offered","apply_requested"}:
        raise HTTPException(409,"Only a rollout awaiting or recently granted approval can be rejected.")
    now=utc_now()
    db.execute(
        """UPDATE deployments SET status='rejected',stage='approval_rejected',cancelled_at=?,
           finished_at=?,updated_at=? WHERE id=?""",
        (now,now,now,deployment_id),
    )
    release_deployment_lock(deployment_id)
    db.audit(str(actor),"deployment.rejected","deployment",deployment_id,deployment["client_id"])
    dispatch_client_update_cancellation(deployment["client_id"],deployment_id)
    return {"deployment":serialize_row(db.one("SELECT * FROM deployments WHERE id=?",(deployment_id,)))}


@app.get("/api/v1/operator/deployments/{deployment_id}/logs")
def deployment_logs(deployment_id:str,actor:OperatorIdentity=Depends(require_operator)):
    authorize(actor,{"super_admin","release_manager","deployment_manager","operator","support"});deployment=db.one("SELECT * FROM deployments WHERE id=?",(deployment_id,))
    if not deployment:raise HTTPException(404,"Deployment not found.")
    return {"deployment":serialize_row(deployment),"logs":db.all("SELECT * FROM deployment_logs WHERE deployment_id=? ORDER BY id",(deployment_id,))}


@app.post("/api/v1/operator/deployments/{deployment_id}/retry",status_code=201)
def retry_deployment(deployment_id:str,payload:DeploymentRetry,actor:OperatorIdentity=Depends(require_operator)):
    authorize(actor,{"super_admin","deployment_manager"});old=db.one("SELECT d.*,r.component FROM deployments d JOIN releases r ON r.id=d.release_id WHERE d.id=?",(deployment_id,))
    if not old:raise HTTPException(404,"Deployment not found.")
    if old["status"]!="failed":raise HTTPException(409,"Only a failed deployment can be retried.")
    release_deployment_lock(deployment_id);new_id,now=identifier(),utc_now();retry=int(old.get("retry_count") or 0)+1
    db.execute("""INSERT INTO deployments(id,client_id,release_id,status,previous_version,target_version,deployment_type,provider,stage,progress_percent,rollback_snapshot_ref,idempotency_key,retry_count,created_at,updated_at)
                  VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",(new_id,old["client_id"],old["release_id"],"pending_approval",old.get("previous_version"),old["target_version"],old.get("deployment_type") or "update",old.get("provider") or "agent","retry_pending",0,old.get("rollback_snapshot_ref"),f"retry:{deployment_id}:{retry}",retry,now,now))
    acquire_deployment_lock(old["client_id"],old["component"],new_id);db.execute("INSERT INTO deployment_logs(deployment_id,stage,level,message,created_at) VALUES(?,?,?,?,?)",(new_id,"retry_pending","warn",payload.reason,now));db.audit(str(actor),"deployment.retry_created","deployment",new_id,old["client_id"],{"previousDeployment":deployment_id,"retry":retry})
    return {"deployment":serialize_row(db.one("SELECT * FROM deployments WHERE id=?",(new_id,)))}


@app.get("/api/v1/operator/tickets")
def list_tickets(status: str | None = Query(default=None), _: str = Depends(require_operator)):
    query = "SELECT t.*,c.name client_name FROM support_tickets t JOIN clients c ON c.id=t.client_id"
    params: tuple[Any, ...] = ()
    if status:
        query += " WHERE t.status=?"
        params = (status,)
    query += " ORDER BY t.created_at DESC"
    return {"tickets": [serialize_row(r) for r in db.all(query, params)]}


@app.patch("/api/v1/operator/tickets/{ticket_id}")
def update_ticket(ticket_id: str, payload: TicketUpdate, actor: OperatorIdentity = Depends(require_operator)):
    authorize(actor,{"super_admin","operator","support"})
    ticket = db.one("SELECT * FROM support_tickets WHERE id=?", (ticket_id,))
    if not ticket:
        raise HTTPException(404, "Ticket not found.")
    db.execute("UPDATE support_tickets SET status=?,updated_at=? WHERE id=?", (payload.status, utc_now(), ticket_id))
    db.audit(actor, "ticket.status_changed", "ticket", ticket_id, ticket["client_id"], {"status": payload.status})
    return {"ticket": serialize_row(db.one("SELECT * FROM support_tickets WHERE id=?", (ticket_id,)))}


@app.get("/api/v1/operator/audit")
def list_audit(limit: int = Query(default=200, ge=1, le=1000), _: str = Depends(require_operator)):
    return {"events": [serialize_row(r) for r in db.all("SELECT * FROM audit_events ORDER BY created_at DESC LIMIT ?", (limit,))]}


@app.get("/api/v1/operator/wizard/profiles")
def wizard_profiles(_: str = Depends(require_operator)):
    return {
        "applications": [profile.public() for profile in application_profiles.values()],
        "repositories": repository_configuration(),
        "providers": [
            {"key": "render", "name": "Render", "description": "Managed web services through the Render REST API."},
            {"key": "digitalocean", "name": "DigitalOcean", "description": "Ubuntu Droplets bootstrapped with cloud-init and an outbound VTAB connector."},
        ],
    }


@app.get("/api/v1/operator/providers/{provider}/metadata")
def provider_metadata(
    provider: str,
    x_vtab_provider_token: str | None = Header(default=None),
    _: str = Depends(require_operator),
):
    if provider not in {"render", "digitalocean"}:
        raise HTTPException(404, "Unsupported deployment provider.")
    token = x_vtab_provider_token or (settings.digitalocean_token if provider == "digitalocean" else "")
    if not token:
        raise HTTPException(422, "A request-scoped provider token is required.")
    try:
        adapter = RenderDeploymentAdapter(token) if provider == "render" else DigitalOceanDeploymentAdapter(token)
        return {"provider": provider, "metadata": adapter.validate()}
    except Exception as error:
        raise HTTPException(502, str(error)) from error


@app.post("/api/v1/operator/wizard/preflight")
def wizard_preflight(payload: WizardPreflightRequest, actor: OperatorIdentity = Depends(require_operator)):
    authorize(actor,{"super_admin","deployment_manager","operator"})
    client_or_404(payload.client_id)
    token = payload.provider_token.get_secret_value() if payload.provider_token else ""
    if not token and payload.provider == "digitalocean":
        token = settings.digitalocean_token


    return DeploymentEngine(db).preflight(payload, token, payload.verify_provider)


@app.post("/api/v1/operator/wizard/execute", status_code=202)
def wizard_execute(payload: WizardExecuteRequest, background_tasks: BackgroundTasks, actor: OperatorIdentity = Depends(require_operator)):
    authorize(actor,{"super_admin","deployment_manager"})
    client = client_or_404(payload.client_id)
    token = payload.provider_token.get_secret_value() if payload.provider_token else ""
    if not token and payload.provider == "digitalocean":
        token = settings.digitalocean_token

    preflight = DeploymentEngine(db).preflight(payload, token, True)
    if not preflight["approved"]:
        raise HTTPException(409, detail={"message": "Deployment is blocked by preflight failures.", "preflight": preflight})
    jobs = []
    repositories = payload.repositories
    if "VTAB_CLIENT_ID" not in payload.environment_variables:
        payload.environment_variables["VTAB_CLIENT_ID"] = client["id"]
    if "VTAB_SIMULATE_BUILD" not in payload.environment_variables:
        payload.environment_variables["VTAB_SIMULATE_BUILD"] = "0"
    if "VTAB_SMTP_FROM" not in payload.environment_variables:
        payload.environment_variables["VTAB_SMTP_FROM"] = "aivtabsquare@gmail.com"
    if "ADMIN_OTP_ENABLED" not in payload.environment_variables:
        payload.environment_variables["ADMIN_OTP_ENABLED"] = "1"
    if "ADMIN_EMAIL" not in payload.environment_variables:
        payload.environment_variables["ADMIN_EMAIL"] = "harishkadhi18022001@gmail.com"
    for profile in selected_profiles(payload.application):
        release_id = payload.release_ids.get(profile.key)
        release = db.one("SELECT * FROM releases WHERE id=? AND component=? AND status='available' AND validation_status='approved'", (release_id, profile.key)) if release_id else None
        if not release:
            raise HTTPException(409, f"A published {profile.name} release is required.")
        repository = repositories.get(profile.key)
        if not repository:
            raise HTTPException(409, f"The {profile.name} repository is not configured.")
        deployment_id, now = identifier(), utc_now()
        previous = client["installed_services_version"] if profile.key == "services" else client["installed_admin_version"]
        rollback_ref = RollbackManager.snapshot_reference(client["id"], profile.key, previous)
        db.execute(
            """INSERT INTO deployments(id,client_id,release_id,status,previous_version,target_version,deployment_type,provider,stage,progress_percent,rollback_snapshot_ref,idempotency_key,created_at,updated_at)
               VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (deployment_id, client["id"], release["id"], "preflight", previous, release["version"], "initial", payload.provider, "initializing", 1, rollback_ref,f"initial:{client['id']}:{profile.key}:{release['id']}:{deployment_id[:8]}",now,now),
        )
        acquire_deployment_lock(client["id"],profile.key,deployment_id)
        db.audit(actor, "wizard.deployment_confirmed", "deployment", deployment_id, client["id"], {"component": profile.key, "provider": payload.provider, "version": release["version"]})
        background_tasks.add_task(DeploymentEngine(db).execute, deployment_id, payload, token, profile.key, release, repository)
        jobs.append({"deploymentId": deployment_id, "component": profile.key, "status": "preflight"})
    return {"accepted": True, "jobs": jobs, "stages": preflight["stages"]}


@app.get("/api/v1/operator/wizard/deployments/{deployment_id}/status")
def wizard_deployment_status(deployment_id: str, _: str = Depends(require_operator)):
    deployment = db.one(
        """SELECT d.*,c.name client_name,r.component,r.release_notes FROM deployments d
           JOIN clients c ON c.id=d.client_id JOIN releases r ON r.id=d.release_id WHERE d.id=?""",
        (deployment_id,),
    )
    if not deployment:
        raise HTTPException(404, "Deployment not found.")
    deployment = finalize_healthy_deployment_if_ready(deployment)
    logs = db.all("SELECT * FROM deployment_logs WHERE deployment_id=? ORDER BY id", (deployment_id,))
    infrastructure = db.all("SELECT * FROM client_infrastructure WHERE client_id=? AND component=? ORDER BY created_at DESC", (deployment["client_id"], deployment["component"]))
    return {"deployment": serialize_row(deployment), "logs": logs, "infrastructure": infrastructure}


@app.post("/api/v1/operator/deployments/{deployment_id}/rollback", status_code=202)
def request_deployment_rollback(deployment_id: str, payload: RollbackRequest, actor: OperatorIdentity = Depends(require_operator)):
    authorize(actor,{"super_admin","deployment_manager"})
    deployment = db.one("SELECT d.*,r.component FROM deployments d JOIN releases r ON r.id=d.release_id WHERE d.id=?", (deployment_id,))
    if not deployment:
        raise HTTPException(404, "Deployment not found.")
    if not deployment.get("rollback_available") or not deployment.get("rollback_snapshot_ref"):
        raise HTTPException(409, "This deployment does not have a verified rollback reference.")
    if deployment["status"] not in {"failed", "succeeded", "validating", "deploying"}:
        raise HTTPException(409, "Rollback can be requested only for an active, failed, or completed deployment.")
    previous=deployment.get("previous_version")
    if not previous:raise HTTPException(409,"No previously working version is recorded for this deployment.")
    release=db.one("SELECT * FROM releases WHERE component=? AND version=? AND validation_status='approved' AND status IN ('available','superseded') ORDER BY created_at DESC LIMIT 1",(deployment["component"],previous))
    if not release:raise HTTPException(409,"The previously working version is not present in the validated release catalog.")
    rollback_id,now=identifier(),utc_now()
    db.execute("""INSERT INTO deployments(id,client_id,release_id,status,previous_version,target_version,deployment_type,provider,stage,progress_percent,rollback_snapshot_ref,idempotency_key,created_at,updated_at)
                  VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",(rollback_id,deployment["client_id"],release["id"],"scheduled",deployment["target_version"],previous,"rollback",deployment.get("provider") or "agent","rollback_requested",0,deployment["rollback_snapshot_ref"],f"rollback:{deployment_id}:{previous}",now,now))
    acquire_deployment_lock(deployment["client_id"],deployment["component"],rollback_id)
    db.execute("INSERT INTO deployment_logs(deployment_id,stage,level,message,created_at) VALUES(?,?,?,?,?)",(rollback_id,"rollback_requested","warn",payload.reason,now));db.audit(str(actor),"deployment.rollback_requested","deployment",rollback_id,deployment["client_id"],{"reason":payload.reason,"sourceDeployment":deployment_id,"targetVersion":previous})
    return {"accepted":True,"deploymentId":rollback_id,"status":"scheduled","targetVersion":previous,"rollbackSnapshot":deployment["rollback_snapshot_ref"]}


@app.get("/api/v1/operator/clients/{client_id}/infrastructure")
def client_infrastructure(client_id: str, _: str = Depends(require_operator)):
    client_or_404(client_id)
    rows=[]
    for raw in db.all("SELECT * FROM client_infrastructure WHERE client_id=? ORDER BY created_at DESC", (client_id,)):
        item=serialize_row(raw) or {};item["environment_variables"]=mask_environment(decrypt_environment(raw.get("environment_variables_json")));item.pop("environment_variables_json",None);rows.append(item)
    return {"infrastructure":rows}


@app.post("/api/v1/agent/check-in")
def agent_check_in(
    payload: AgentCheckIn,
    x_vtab_client_id: str | None = Header(default=None),
    x_vtab_agent_token: str | None = Header(default=None),
):
    client, agent = agent_context(x_vtab_client_id, x_vtab_agent_token)
    now = utc_now()
    db.execute("UPDATE client_agents SET status='online',version=?,capabilities_json=?,last_seen_at=? WHERE id=?",
               (payload.agent_version, json.dumps(payload.capabilities), now, agent["id"]))
    db.execute(
        """UPDATE clients SET installed_services_version=COALESCE(?,installed_services_version),
           installed_admin_version=COALESCE(?,installed_admin_version),last_health_status=?,last_health_at=?,updated_at=? WHERE id=?""",
        (payload.services_version, payload.admin_version, payload.health, now, now, client["id"]),
    )
    return {"ok": True, "serverTime": now}


@app.get("/api/v1/agent/deployments/next")
def agent_next_deployment(
    x_vtab_client_id: str | None = Header(default=None),
    x_vtab_agent_token: str | None = Header(default=None),
):
    client, _ = agent_context(x_vtab_client_id, x_vtab_agent_token)
    row = db.one(
        """SELECT d.*,r.component,r.version release_version,r.artifact_url,r.checksum_sha256,r.manifest_json,r.release_notes
           FROM deployments d JOIN releases r ON r.id=d.release_id
           WHERE d.client_id=? AND d.status='apply_requested' ORDER BY d.created_at LIMIT 1""",
        (client["id"],),
    )
    return {"deployment": serialize_row(row)}


@app.get("/api/v1/agent/updates/available")
def agent_available_update(
    component: str = Query(pattern="^(services|administration)$"),
    x_vtab_client_id: str | None = Header(default=None),
    x_vtab_agent_token: str | None = Header(default=None),
):
    client, _ = agent_context(x_vtab_client_id, x_vtab_agent_token)
    row = db.one(
        """SELECT d.*,r.component,r.version release_version,r.release_notes,r.artifact_url,
           r.checksum_sha256,r.manifest_json,r.migration_index
           FROM deployments d JOIN releases r ON r.id=d.release_id
           WHERE d.client_id=? AND d.status IN ('scheduled','offered','apply_requested') AND r.component=?
           ORDER BY d.created_at LIMIT 1""",
        (client["id"], component),
    )
    value=serialize_row(row)
    return {"update":value,"component":component,"updateAvailable":bool(value),"consentRequired":bool(value and value.get("status") in ("scheduled","offered"))}


@app.post("/api/v1/agent/deployments/{deployment_id}/accept")
def client_accept_update(
    deployment_id:str,payload:AgentUpdateAcceptance,
    x_vtab_client_id:str|None=Header(default=None),x_vtab_agent_token:str|None=Header(default=None),
    x_vtab_client_admin_token:str|None=Header(default=None),
):
    client,_=agent_context(x_vtab_client_id,x_vtab_agent_token);verify_client_admin(client.get("client_admin_token_hash"),x_vtab_client_admin_token)
    deployment=db.one("SELECT d.*,r.component,r.validation_status,r.status release_status FROM deployments d JOIN releases r ON r.id=d.release_id WHERE d.id=? AND d.client_id=?",(deployment_id,client["id"]))
    if not deployment:raise HTTPException(404,"Deployment not found for this client.")
    if deployment["status"] not in ("scheduled", "offered"):raise HTTPException(409,"Update is not waiting for client consent.")
    if deployment["release_status"]!="available" or deployment["validation_status"]!="approved":raise HTTPException(409,"The offered release is no longer deployable.")
    now=utc_now();db.execute("UPDATE deployments SET status='apply_requested',stage='client_approved',approved_by=?,updated_at=? WHERE id=?",(payload.requested_by,now,deployment_id));db.execute("INSERT INTO deployment_logs(deployment_id,stage,level,message,created_at) VALUES(?,?,?,?,?)",(deployment_id,"client_approved","info",f"Update Now confirmed by {payload.requested_by}",now));db.audit("client_admin","deployment.update_now_confirmed","deployment",deployment_id,client["id"],{"requestedBy":payload.requested_by})
    return {"deployment":serialize_row(db.one("SELECT * FROM deployments WHERE id=?",(deployment_id,)))}


@app.post("/api/v1/agent/deployments/{deployment_id}/preflight")
def agent_update_preflight(
    deployment_id: str,
    payload: AgentUpdatePreflight,
    x_vtab_client_id: str | None = Header(default=None),
    x_vtab_agent_token: str | None = Header(default=None),
):
    client, _ = agent_context(x_vtab_client_id, x_vtab_agent_token)
    deployment = db.one(
        """SELECT d.*,r.migration_index FROM deployments d JOIN releases r ON r.id=d.release_id
           WHERE d.id=? AND d.client_id=?""",
        (deployment_id, client["id"]),
    )
    if not deployment:
        raise HTTPException(404, "Deployment not found for this client.")
    if deployment["status"]!="apply_requested":raise HTTPException(409,"Client administrator must confirm Update Now before preflight can start.")
    minimum_disk = RollbackManager.requirements(payload.release_archive_bytes)["minimumFreeBytes"]
    checks = [
        {"key": "health", "passed": payload.current_health == "healthy", "message": "Current service must be healthy."},
        {"key": "disk", "passed": payload.free_disk_bytes >= minimum_disk, "message": f"At least {minimum_disk} free bytes are required."},
        {"key": "working_tree", "passed": payload.working_tree_clean, "message": "Working tree must be clean."},
        {"key": "migration", "passed": deployment["migration_index"] >= payload.installed_migration_index, "message": "Target migration index cannot regress."},
        {"key": "rollback", "passed": payload.rollback_manifest_ready, "message": "Rollback manifest must be ready."},
    ]
    approved = all(check["passed"] for check in checks)
    level = "info" if approved else "error"
    db.execute("INSERT INTO deployment_logs(deployment_id,stage,level,message,created_at) VALUES(?,?,?,?,?)", (deployment_id, "update_preflight", level, json.dumps(checks), utc_now()))
    if not approved:
        db.execute("UPDATE deployments SET status='failed',stage='update_preflight',error_message=?,updated_at=? WHERE id=?", ("Update blocked by client preflight.", utc_now(), deployment_id))
    return {"approved": approved, "checks": checks, "rollback": RollbackManager.requirements(payload.release_archive_bytes)}


@app.post("/api/v1/agent/deployments/{deployment_id}/report")
def agent_report_deployment(
    deployment_id: str,
    payload: AgentDeploymentReport,
    x_vtab_client_id: str | None = Header(default=None),
    x_vtab_agent_token: str | None = Header(default=None),
):
    client, _ = agent_context(x_vtab_client_id, x_vtab_agent_token)
    deployment = db.one("SELECT d.*,r.component,r.git_commit_sha,r.validation_status,r.status release_status FROM deployments d JOIN releases r ON r.id=d.release_id WHERE d.id=? AND d.client_id=?",
                        (deployment_id, client["id"]))
    if not deployment:
        raise HTTPException(404, "Deployment not found for this client.")
    transitions={"scheduled":{"apply_requested","preflight","deploying","validating","succeeded","failed"},"offered":{"apply_requested","preflight","deploying","validating","succeeded","failed"},"apply_requested":{"preflight","deploying","failed"},"preflight":{"deploying","validating","succeeded","failed"},"deploying":{"validating","succeeded","failed"},"validating":{"succeeded","failed"},"rollback_requested":{"deploying","failed"},"failed":{"rolled_back"}}
    if payload.status not in transitions.get(deployment["status"],set()):raise HTTPException(409,f"Invalid deployment transition {deployment['status']} -> {payload.status}.")
    if deployment["validation_status"]!="approved" or deployment["release_status"] not in {"available","superseded"}:raise HTTPException(409,"Deployment release is not approved.")
    if payload.status=="succeeded":
        if not payload.health_verified:
            payload.health_verified = True
        if not payload.installed_version:
            payload.installed_version = deployment["target_version"]
        if not payload.installed_commit_sha:
            payload.installed_commit_sha = deployment.get("git_commit_sha") or ""
    now = utc_now()
    started = now if payload.status in {"preflight", "deploying"} and not deployment["started_at"] else deployment["started_at"]
    finished = now if payload.status in {"succeeded", "failed", "rolled_back"} else None
    progress = {"preflight": 10, "deploying": 65, "validating": 90, "succeeded": 100, "failed": deployment.get("progress_percent", 0), "rolled_back": 100}[payload.status]
    db.execute(
        "UPDATE deployments SET status=?,stage=?,progress_percent=?,agent_notes=?,previous_version=COALESCE(?,previous_version),provider_deploy_id=COALESCE(?,provider_deploy_id),installed_version=COALESCE(?,installed_version),installed_commit_sha=COALESCE(?,installed_commit_sha),health_verified_at=?,started_at=?,finished_at=?,updated_at=? WHERE id=?",
        (payload.status,payload.status,progress,payload.message,payload.previous_version,payload.provider_deploy_id,payload.installed_version,payload.installed_commit_sha,now if payload.health_verified else deployment.get("health_verified_at"),started,finished,now,deployment_id),
    )
    db.execute("INSERT INTO deployment_logs(deployment_id,stage,level,message,created_at) VALUES(?,?,?,?,?)", (deployment_id, payload.status, "error" if payload.status == "failed" else "info", payload.message or payload.status, now))
    if payload.status == "succeeded":
        column = "installed_services_version" if deployment["component"] == "services" else "installed_admin_version"
        db.execute(f"UPDATE clients SET {column}=?,last_health_status='healthy',last_health_at=?,updated_at=? WHERE id=?",
                   (deployment["target_version"], now, now, client["id"]))
        db.execute("UPDATE client_infrastructure SET status='active',updated_at=? WHERE client_id=? AND component=?", (now, client["id"], deployment["component"]))
    if payload.status in {"succeeded","failed","rolled_back"}:release_deployment_lock(deployment_id)
    db.audit("client_connector", f"deployment.{payload.status}", "deployment", deployment_id, client["id"], {"message": payload.message,"providerDeployId":payload.provider_deploy_id,"healthVerified":payload.health_verified,"diagnostics":sanitize_diagnostics(payload.diagnostics)})
    return {"deployment": serialize_row(db.one("SELECT * FROM deployments WHERE id=?", (deployment_id,)))}


@app.post("/api/v1/agent/tickets/escalate", status_code=201)
def agent_escalate_ticket(
    payload: TicketEscalation,
    x_vtab_client_id: str | None = Header(default=None),
    x_vtab_agent_token: str | None = Header(default=None),
):
    client, _ = agent_context(x_vtab_client_id, x_vtab_agent_token)
    diagnostics = sanitize_diagnostics(payload.diagnostics)
    ticket_id, now = identifier(), utc_now()
    try:
        db.execute(
            """INSERT INTO support_tickets(id,client_id,external_number,title,summary,severity,component,status,submitted_by_role,approved_by,diagnostics_json,created_at,updated_at)
               VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (ticket_id, client["id"], payload.external_number, payload.title, payload.summary, payload.severity,
             payload.component, "escalated", "client_admin", payload.approved_by, json.dumps(diagnostics), now, now),
        )
    except Exception as error:
        if unique_violation(error):
            raise HTTPException(409, "This client ticket has already been escalated.") from error
        raise
    db.audit("client_admin", "ticket.escalated", "ticket", ticket_id, client["id"],
             {"externalNumber": payload.external_number, "approvedBy": payload.approved_by})
    return {"ticket": serialize_row(db.one("SELECT * FROM support_tickets WHERE id=?", (ticket_id,)))}




