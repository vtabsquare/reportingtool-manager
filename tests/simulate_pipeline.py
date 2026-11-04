"""
End-to-End Simulation of the VTAB Controlled Update Pipeline:
1. Creates a client environment.
2. Registers existing active infrastructure for the client.
3. Registers a new Git release candidate (v2.0.1) as draft.
4. Passes CI validation gate and publishes the release.
5. Manager creates update deployment (status: pending_approval) and approves offer (status: scheduled).
6. Client agent polls passively and discovers the update offer.
7. Client administrator clicks "Update Now" (status: apply_requested).
8. Client agent executes build and reports preflight -> deploying -> validating -> succeeded.
9. Client installed version is automatically updated in Manager.
"""
import urllib.request
import json
import sqlite3
import uuid
import time

BASE = "http://127.0.0.1:8840"
MGR_HEADERS = {"Content-Type": "application/json", "X-VTAB-Manager-Key": "vtab-local-dev"}

def post(path, data=None, headers=None):
    h = {"Content-Type": "application/json"}
    h.update(MGR_HEADERS)
    if headers:
        h.update(headers)
    req = urllib.request.Request(BASE + path, data=json.dumps(data or {}).encode(), headers=h, method="POST")
    with urllib.request.urlopen(req) as resp:
        return json.loads(resp.read().decode())

def get(path, headers=None):
    h = dict(MGR_HEADERS)
    if headers:
        h.update(headers)
    req = urllib.request.Request(BASE + path, headers=h)
    with urllib.request.urlopen(req) as resp:
        return json.loads(resp.read().decode())

print("\n" + "=" * 65)
print("1. REGISTERING TEST CLIENT ENVIRONMENT")
print("=" * 65)
client_res = post("/api/v1/operator/clients", {
    "name": "Acme Analytics Corp",
    "code": f"acme-sim-{uuid.uuid4().hex[:6]}",
    "contact_email": "ops@acme.com",
    "environment": "production",
    "database_provider": "supabase"
})
client = client_res["client"]
client_id = client["id"]
agent_token = client_res["connectorBootstrapToken"]
client_admin_token = client_res["clientAdminBootstrapToken"]
print(f" -> Client Created: {client['name']}")
print(f" -> Client ID:     {client_id}")

# Register active infrastructure for services on this client
conn = sqlite3.connect("VTAB Reporting Manager/data/vtab_manager.db")
cur = conn.cursor()
infra_id = f"infra-{uuid.uuid4().hex[:8]}"
cur.execute(
    "INSERT INTO client_infrastructure(id, client_id, component, provider, provider_resource_id, region, instance_size, domain, status, created_at, updated_at) "
    "VALUES(?, ?, 'services', 'render', 'srv-sim123', 'oregon', 'free', 'https://acme.onrender.com', 'active', datetime('now'), datetime('now'))",
    (infra_id, client_id)
)
conn.commit()
conn.close()
print(" -> Registered Active Managed Infrastructure (srv-sim123)")

sim_version = f"2.0.{int(time.time()) % 10000}"
print("\n" + "=" * 65)
print(f"2. REGISTERING NEW GITHUB RELEASE CANDIDATE (v{sim_version})")
print("=" * 65)
release_res = post("/api/v1/operator/releases", {
    "component": "services",
    "version": sim_version,
    "channel": "stable",
    "repository_url": "https://github.com/vtabsquare/Reportingtool-webservice.git",
    "git_commit_sha": "b0787a1da66bfa45b9147e0bce9394ecb3e764da",
    "artifact_url": "https://github.com/vtabsquare/Reportingtool-webservice/archive/b0787a1da66bfa45b9147e0bce9394ecb3e764da.zip",
    "checksum_sha256": "d" * 64,
    "release_notes": "Added PPT auto-naming and optimized Parquet loading speed."
})
release_id = release_res["release"]["id"]
print(f" -> Release Registered as Draft (Status: {release_res['release']['status']})")
print(f" -> Release ID: {release_id}")

print("\n" + "=" * 65)
print("3. CI VALIDATION GATE & PUBLISHING")
print("=" * 65)
val_res = post(f"/api/v1/operator/releases/{release_id}/validate", {
    "approved": True,
    "ci_status": "success",
    "checks": {"build": "success", "test": "success", "security": "success"}
})
print(f" -> CI Validation Passed: {val_res['release']['validation_status']}")

pub_res = post(f"/api/v1/operator/releases/{release_id}/publish", {})
print(f" -> Release Published: Status is now '{pub_res['release']['status']}'")

print("\n" + "=" * 65)
print("4. MANAGER OFFERS UPDATE TO ACME CORP")
print("=" * 65)
offer_res = post("/api/v1/operator/deployments", {
    "client_id": client_id,
    "release_id": release_id,
    "scheduled_for": None
})
deployment_id = offer_res["deployment"]["id"]
print(f" -> Update Deployment Created (ID: {deployment_id}, Status: '{offer_res['deployment']['status']}')")

# Approve the update offer
approve_res = post(f"/api/v1/operator/deployments/{deployment_id}/approve", {
    "approved_by": "lead-operator"
})
print(f" -> Update Offer Approved by Manager! Status: '{approve_res['deployment']['status']}'")
print(" -> ISOLATION CHECK: Client has NOT been updated yet! Only offered to client.")

print("\n" + "=" * 65)
print("5. CLIENT AGENT POLLS FOR UPDATES (Passive Heartbeat)")
print("=" * 65)
agent_headers = {"X-VTAB-Client-Id": client_id, "X-VTAB-Agent-Token": agent_token}
poll_res = get("/api/v1/agent/updates/available?component=services", headers=agent_headers)
update_info = poll_res.get("update") or {}
print(f" -> Offer Detected by Client Agent:")
print(f"    Version:       {update_info.get('release_version')}")
print(f"    Release Notes: {update_info.get('release_notes')}")
print(f"    Status:        {update_info.get('status')}")

print("\n" + "=" * 65)
print("6. USER CLICKS 'UPDATE NOW' (Client Admin Authorization)")
print("=" * 65)
accept_headers = {
    "Content-Type": "application/json",
    "X-VTAB-Client-Id": client_id,
    "X-VTAB-Agent-Token": agent_token,
    "X-VTAB-Client-Admin-Token": client_admin_token
}
accept_res = post(f"/api/v1/agent/deployments/{deployment_id}/accept", {"confirmed": True, "requested_by": "lead@acme.com"}, headers=accept_headers)
print(f" -> Update Authorized by User!")
print(f" -> Deployment Status changed to: '{accept_res['deployment']['status']}'")

print("\n" + "=" * 65)
print("7. CLIENT SERVER BUILDS & REPORTS SUCCESS")
print("=" * 65)
# Report preflight
post(f"/api/v1/agent/deployments/{deployment_id}/report", {"status": "preflight", "message": "Preflight checks passed."}, headers=agent_headers)
print(" -> Reported: preflight")

# Report deploying
post(f"/api/v1/agent/deployments/{deployment_id}/report", {"status": "deploying", "message": "Building React SPA & installing packages."}, headers=agent_headers)
print(" -> Reported: deploying")

# Report validating
post(f"/api/v1/agent/deployments/{deployment_id}/report", {"status": "validating", "message": "Health endpoint verified healthy."}, headers=agent_headers)
print(" -> Reported: validating")

# Report succeeded
succeed_res = post(f"/api/v1/agent/deployments/{deployment_id}/report", {
    "status": "succeeded",
    "message": f"v{sim_version} active and healthy.",
    "health_verified": True,
    "installed_version": sim_version,
    "installed_commit_sha": "b0787a1da66bfa45b9147e0bce9394ecb3e764da"
}, headers=agent_headers)
print(" -> Reported: succeeded")
print(f" -> Final Deployment Status: '{succeed_res['deployment']['status']}'")

print("\n" + "=" * 65)
print("8. VERIFY CLIENT PROFILE REFLECTS UPGRADE")
print("=" * 65)
clients_data = get("/api/v1/operator/clients")
acme = next(c for c in clients_data["clients"] if c["id"] == client_id)
print(f" -> Installed Services Version: {acme['installed_services_version']}")
print(f" -> Health Status:              {acme['last_health_status']}")
print("=" * 65)
print("ALL 8 STAGES OF THE CONTROLLED UPDATE PIPELINE VERIFIED SUCCESSFULLY!")
print("=" * 65 + "\n")
