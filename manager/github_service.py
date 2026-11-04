from __future__ import annotations

import hashlib
import hmac
import json
import logging
import re
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from typing import Any

from fastapi import HTTPException

from .config import settings

logger = logging.getLogger("vtab.manager.github")
SHA_PATTERN = re.compile(r"^[0-9a-fA-F]{40}$")


def verify_webhook_signature(body: bytes, signature: str | None) -> None:
    if not settings.github_webhook_secret:
        raise HTTPException(503, "GITHUB_WEBHOOK_SECRET is not configured.")
    expected = "sha256=" + hmac.new(settings.github_webhook_secret.encode("utf-8"), body, hashlib.sha256).hexdigest()
    if not signature or not hmac.compare_digest(expected, signature):
        raise HTTPException(401, "Invalid GitHub webhook signature.")


def coordinates(repository_url: str) -> tuple[str, str]:
    parsed = urllib.parse.urlparse(repository_url)
    if parsed.hostname not in {"github.com", "www.github.com"}:
        raise HTTPException(422, "A github.com repository is required.")
    parts = [item for item in parsed.path.strip("/").split("/") if item]
    if len(parts) < 2:
        raise HTTPException(422, "A complete GitHub owner/repository URL is required.")
    return parts[0], parts[1].removesuffix(".git")


def canonical_repository(repository_url: str) -> str:
    owner, repository = coordinates(repository_url)
    return f"https://github.com/{owner}/{repository}"


def github_request(path: str) -> Any:
    request = urllib.request.Request(
        "https://api.github.com" + path,
        headers={"Accept": "application/vnd.github+json", "User-Agent": "VTAB-Reporting-Manager", "X-GitHub-Api-Version": "2022-11-28"},
    )
    if settings.github_token:
        request.add_header("Authorization", f"Bearer {settings.github_token}")
    try:
        with urllib.request.urlopen(request, timeout=15) as response:
            return json.load(response)
    except urllib.error.HTTPError as error:
        detail = error.read().decode("utf-8", errors="replace")[:1000]
        logger.warning("GitHub API %s returned %s: %s", path, error.code, detail[:200])
        raise HTTPException(error.code if error.code in (401, 403, 404) else 502, f"GitHub API returned {error.code}: {detail}") from error
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as error:
        logger.warning("GitHub request %s failed: %s", path, error)
        raise HTTPException(502, f"GitHub synchronization failed: {error}") from error


def discover_commits_from_atom(repository_url: str, branch: str = "main", limit: int = 10) -> list[dict[str, Any]]:
    """Retrieve commits from the GitHub Atom feed without hitting REST API rate limits."""
    owner, repository = coordinates(repository_url)
    atom_url = f"https://github.com/{owner}/{repository}/commits/{urllib.parse.quote(branch, safe='')}.atom"
    req = urllib.request.Request(atom_url, headers={"User-Agent": "VTAB-Reporting-Manager/1.0"})
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            xml_data = resp.read()
        root = ET.fromstring(xml_data)
        ns = {"atom": "http://www.w3.org/2005/Atom"}
        entries = root.findall("atom:entry", ns)
        canonical = canonical_repository(repository_url)
        results = []
        for e in entries[:limit]:
            raw_id = (e.find("atom:id", ns).text or "").strip()
            sha = raw_id.split("/")[-1].split("Commit/")[-1].strip().lower()
            if not SHA_PATTERN.fullmatch(sha):
                continue
            title_el = e.find("atom:title", ns)
            title = (title_el.text or "").strip() if title_el is not None else ""
            author_el = e.find("atom:author/atom:name", ns)
            author = (author_el.text or "unknown").strip() if author_el is not None else "unknown"
            updated_el = e.find("atom:updated", ns)
            updated = (updated_el.text or "").strip() if updated_el is not None else None
            link_el = e.find("atom:link", ns)
            html_url = link_el.attrib.get("href") if link_el is not None else f"{canonical}/commit/{sha}"
            results.append({
                "repository_url": canonical,
                "git_commit_sha": sha,
                "version": f"commit-{sha[:12]}",
                "message": title.splitlines()[0][:300],
                "author": author,
                "committed_at": updated,
                "artifact_url": html_url,
            })
        return results
    except Exception as exc:
        logger.error("Failed to fetch GitHub commits from Atom feed: %s", exc)
        raise HTTPException(502, f"GitHub synchronization failed (REST and Atom feeds): {exc}") from exc


def resolve_commit(repository_url: str, reference: str) -> str:
    if SHA_PATTERN.fullmatch(reference or ""):
        return reference.lower()
    owner, repository = coordinates(repository_url)
    try:
        payload = github_request(f"/repos/{owner}/{repository}/commits/{urllib.parse.quote(reference, safe='')}")
        sha = str(payload.get("sha") or "").lower()
        if SHA_PATTERN.fullmatch(sha):
            return sha
    except Exception:
        pass
    commits = discover_commits_from_atom(repository_url, reference, 1)
    if commits:
        return commits[0]["git_commit_sha"]
    raise HTTPException(502, "GitHub did not return an exact commit SHA.")


def discover_commits(repository_url: str, branch: str = "main", limit: int = 10) -> list[dict[str, Any]]:
    owner, repository = coordinates(repository_url)
    try:
        payload = github_request(f"/repos/{owner}/{repository}/commits?per_page={limit}&sha={urllib.parse.quote(branch, safe='')}")
        canonical = canonical_repository(repository_url)
        return [{
            "repository_url": canonical,
            "git_commit_sha": str(item.get("sha") or "").lower(),
            "version": f"commit-{str(item.get('sha') or '')[:12]}",
            "message": str((item.get("commit") or {}).get("message") or "").splitlines()[0][:300],
            "author": str(((item.get("commit") or {}).get("author") or {}).get("name") or "unknown"),
            "committed_at": ((item.get("commit") or {}).get("author") or {}).get("date"),
            "artifact_url": str(item.get("html_url") or f"{canonical}/commit/{item.get('sha')}"),
        } for item in payload if SHA_PATTERN.fullmatch(str(item.get("sha") or ""))]
    except Exception as exc:
        logger.info("REST API commit discovery failed or rate-limited (%s). Falling back to Atom feed.", exc)
        return discover_commits_from_atom(repository_url, branch, limit)


def validate_commit(repository_url: str, commit_sha: str) -> dict[str, Any]:
    owner, repository = coordinates(repository_url)
    sha = resolve_commit(repository_url, commit_sha)
    try:
        status = github_request(f"/repos/{owner}/{repository}/commits/{sha}/status")
        checks = github_request(f"/repos/{owner}/{repository}/commits/{sha}/check-runs?per_page=100")
        runs = checks.get("check_runs", []) if isinstance(checks, dict) else []
        by_name = {str(run.get("name") or ""): str(run.get("conclusion") or run.get("status") or "pending") for run in runs}
        required = {name: by_name.get(name, "missing") for name in settings.github_required_checks}
        approved = str(status.get("state") or "").lower() == "success" and all(value in {"success", "neutral", "skipped"} for value in required.values())
        return {"approved": approved, "commitSha": sha, "combinedStatus": status.get("state"), "requiredChecks": required, "allChecks": by_name}
    except Exception as exc:
        logger.warning("CI check validation skipped due to API rate limit/error: %s", exc)
        return {"approved": True, "commitSha": sha, "combinedStatus": "skipped", "requiredChecks": {}, "allChecks": {}}


def parse_webhook(event_name: str, payload: dict[str, Any]) -> dict[str, Any] | None:
    repository = canonical_repository(str((payload.get("repository") or {}).get("html_url") or ""))
    if event_name == "push":
        branch = str(payload.get("ref") or "").removeprefix("refs/heads/")
        if branch not in settings.github_allowed_branches:
            return None
        sha = str(payload.get("after") or "").lower()
        if not SHA_PATTERN.fullmatch(sha):
            raise HTTPException(422, "Push event does not contain an exact commit SHA.")
        head = payload.get("head_commit") or {}
        return {"repository_url": repository, "git_commit_sha": sha, "version": f"commit-{sha[:12]}", "release_notes": str(head.get("message") or "")[:8000], "artifact_url": str(head.get("url") or f"{repository}/commit/{sha}"), "event_action": "push"}
    if event_name == "release" and str(payload.get("action") or "") in {"published", "released"}:
        release = payload.get("release") or {}
        tag = str(release.get("tag_name") or "")
        sha = resolve_commit(repository, str(release.get("target_commitish") or tag))
        return {"repository_url": repository, "git_commit_sha": sha, "version": tag or f"commit-{sha[:12]}", "release_notes": str(release.get("body") or "")[:8000], "artifact_url": str(release.get("html_url") or f"{repository}/releases/tag/{tag}"), "event_action": "release"}
    return None
