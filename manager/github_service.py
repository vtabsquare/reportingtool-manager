from __future__ import annotations

import hashlib
import hmac
import json
import re
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

from fastapi import HTTPException

from .config import settings


SHA_PATTERN=re.compile(r"^[0-9a-fA-F]{40}$")


def verify_webhook_signature(body:bytes,signature:str|None)->None:
    if not settings.github_webhook_secret:
        raise HTTPException(503,"GITHUB_WEBHOOK_SECRET is not configured.")
    expected="sha256="+hmac.new(settings.github_webhook_secret.encode("utf-8"),body,hashlib.sha256).hexdigest()
    if not signature or not hmac.compare_digest(expected,signature):
        raise HTTPException(401,"Invalid GitHub webhook signature.")


def coordinates(repository_url:str)->tuple[str,str]:
    parsed=urllib.parse.urlparse(repository_url)
    if parsed.hostname not in {"github.com","www.github.com"}:raise HTTPException(422,"A github.com repository is required.")
    parts=[item for item in parsed.path.strip("/").split("/") if item]
    if len(parts)<2:raise HTTPException(422,"A complete GitHub owner/repository URL is required.")
    return parts[0],parts[1].removesuffix(".git")


def canonical_repository(repository_url:str)->str:
    owner,repository=coordinates(repository_url)
    return f"https://github.com/{owner}/{repository}"


def github_request(path:str)->Any:
    request=urllib.request.Request(
        "https://api.github.com"+path,
        headers={"Accept":"application/vnd.github+json","User-Agent":"VTAB-Reporting-Manager","X-GitHub-Api-Version":"2022-11-28"},
    )
    if settings.github_token:request.add_header("Authorization",f"Bearer {settings.github_token}")
    try:
        with urllib.request.urlopen(request,timeout=20) as response:return json.load(response)
    except urllib.error.HTTPError as error:
        detail=error.read().decode("utf-8",errors="replace")[:1000]
        raise HTTPException(502,f"GitHub API returned {error.code}: {detail}") from error
    except (urllib.error.URLError,TimeoutError,json.JSONDecodeError) as error:
        raise HTTPException(502,f"GitHub synchronization failed: {error}") from error


def resolve_commit(repository_url:str,reference:str)->str:
    if SHA_PATTERN.fullmatch(reference or ""):return reference.lower()
    owner,repository=coordinates(repository_url)
    payload=github_request(f"/repos/{owner}/{repository}/commits/{urllib.parse.quote(reference,safe='')}")
    sha=str(payload.get("sha") or "").lower()
    if not SHA_PATTERN.fullmatch(sha):raise HTTPException(502,"GitHub did not return an exact commit SHA.")
    return sha


def discover_commits(repository_url:str,branch:str="main",limit:int=10)->list[dict[str,Any]]:
    owner,repository=coordinates(repository_url)
    payload=github_request(f"/repos/{owner}/{repository}/commits?per_page={limit}&sha={urllib.parse.quote(branch,safe='')}")
    canonical=canonical_repository(repository_url)
    return [{
        "repository_url":canonical,"git_commit_sha":str(item.get("sha") or "").lower(),
        "version":f"commit-{str(item.get('sha') or '')[:12]}","message":str((item.get("commit") or {}).get("message") or "").splitlines()[0][:300],
        "author":str(((item.get("commit") or {}).get("author") or {}).get("name") or "unknown"),
        "committed_at":((item.get("commit") or {}).get("author") or {}).get("date"),
        "artifact_url":str(item.get("html_url") or f"{canonical}/commit/{item.get('sha')}")
    } for item in payload if SHA_PATTERN.fullmatch(str(item.get("sha") or ""))]


def validate_commit(repository_url:str,commit_sha:str)->dict[str,Any]:
    owner,repository=coordinates(repository_url);sha=resolve_commit(repository_url,commit_sha)
    status=github_request(f"/repos/{owner}/{repository}/commits/{sha}/status")
    checks=github_request(f"/repos/{owner}/{repository}/commits/{sha}/check-runs?per_page=100")
    runs=checks.get("check_runs",[]) if isinstance(checks,dict) else []
    by_name={str(run.get("name") or ""):str(run.get("conclusion") or run.get("status") or "pending") for run in runs}
    required={name:by_name.get(name,"missing") for name in settings.github_required_checks}
    approved=str(status.get("state") or "").lower()=="success" and all(value in {"success","neutral","skipped"} for value in required.values())
    return {"approved":approved,"commitSha":sha,"combinedStatus":status.get("state"),"requiredChecks":required,"allChecks":by_name}


def parse_webhook(event_name:str,payload:dict[str,Any])->dict[str,Any]|None:
    repository=canonical_repository(str((payload.get("repository") or {}).get("html_url") or ""))
    if event_name=="push":
        branch=str(payload.get("ref") or "").removeprefix("refs/heads/")
        if branch not in settings.github_allowed_branches:return None
        sha=str(payload.get("after") or "").lower()
        if not SHA_PATTERN.fullmatch(sha):raise HTTPException(422,"Push event does not contain an exact commit SHA.")
        head=payload.get("head_commit") or {}
        return {"repository_url":repository,"git_commit_sha":sha,"version":f"commit-{sha[:12]}","release_notes":str(head.get("message") or "")[:8000],"artifact_url":str(head.get("url") or f"{repository}/commit/{sha}"),"event_action":"push"}
    if event_name=="release" and str(payload.get("action") or "") in {"published","released"}:
        release=payload.get("release") or {};tag=str(release.get("tag_name") or "")
        sha=resolve_commit(repository,str(release.get("target_commitish") or tag))
        return {"repository_url":repository,"git_commit_sha":sha,"version":tag or f"commit-{sha[:12]}","release_notes":str(release.get("body") or "")[:8000],"artifact_url":str(release.get("html_url") or f"{repository}/releases/tag/{tag}"),"event_action":"release"}
    return None
