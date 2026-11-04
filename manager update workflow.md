# VTAB Reporting Manager — Update Workflow & Architecture

This document provides a comprehensive technical overview of the update orchestration, release management, and client configuration features implemented in the **VTAB Reporting Manager** (`VTAB Reporting Manager`).

---

## 1. Architecture Overview

The **VTAB Reporting Manager** serves as the central control plane for multi-tenant deployments of VTAB Reporting Services and Admin Console instances. It orchestrates:
- **GitHub Release & Commit Synchronization**: Tracking commits from the source repository as draft release candidates.
- **Artifact Validation & Publishing**: Enforcing CI check gates before artifacts can be deployed.
- **Targeted Update Delivery**: Dispatching webhooks to client instances or queues for outbound pollers.
- **Provider-Aware Configuration**: Managing infrastructure metadata, request-scoped tokens, and environment variables for both cloud containers (Render) and VMs (DigitalOcean Droplet).

```mermaid
flowchart TD
    A["GitHub Repository\n(Reportingtool-webservice)"] -->|"Sync GitHub / Webhook"| B["Manager Control Plane\n(FastAPI :8840)"]
    B -->|"Validate & Publish"| C["Release Catalog\n(Status: Available)"]
    C -->|"Offer Update"| D["Client Deployment Queue & Webhook Dispatch"]
    D -->|"POST /api/v1/update/webhook"| E["Client Service\n(Render / VM)"]
    B <-->|"Configure Client"| F["Client Configuration Studio\n(Render Service ID, Secrets)"]
```

---

## 2. Key Enhancements & Code Modifications

### 2.1. Dedicated Render Service ID in Client Configuration
To support seamless cloud deployments without manual JSON tampering, the configuration UI and API were updated to recognize and manage Render Service IDs (`srv-...`).

#### `manager/static/index.html`
Added a dedicated form row to the `configurationDialog` that dynamically displays when a client is hosted on Render:
```html
<label class="wide">Domain<input name="domain"></label>
<!-- Dedicated Render Service ID Field -->
<label class="wide" id="renderServiceIdRow" style="display:none">
  <span>Render Service ID (srv-...)</span>
  <input name="render_service_id" placeholder="srv-dbkmh4d9fdbs73bos7u0">
</label>
```
Also added an optional `service_id` input in **Step 4 (Configuration)** of the Deployment Studio Wizard:
```html
<label data-render-field>Render workspace ID<input name="owner_id" placeholder="tea-..."></label>
<label data-render-field>Render Service ID (optional)<input name="service_id" placeholder="srv-..."></label>
<label data-render-field>Branch or tag<input name="branch" value="main"></label>
```

#### `manager/static/app.js`
1. **Dynamic Field Display & Pre-population (`fillConfiguration`)**:
   ```javascript
   function fillConfiguration() {
     const form = $('configurationForm');
     const item = activeConfiguration.infrastructure.find(value => value.id === form.infrastructure_id.value) 
                  || activeConfiguration.infrastructure[0];
     // ...
     const isRender = item.provider === 'render' || (item.provider_resource_id && item.provider_resource_id.startsWith('srv-'));
     const renderRow = $('renderServiceIdRow');
     if (renderRow) {
       renderRow.style.display = isRender ? '' : 'none';
     }
     const srvId = item.provider_resource_id || item.config?.service_id || '';
     if (form.render_service_id) {
       form.render_service_id.value = srvId;
     }
     const env = { ...(item.environment_variables || {}) };
     if (item.component === 'services') {
       if (!env.VTAB_CLIENT_ID && activeConfiguration?.client?.id) env.VTAB_CLIENT_ID = activeConfiguration.client.id;
       if (!env.VTAB_SIMULATE_BUILD) env.VTAB_SIMULATE_BUILD = '0';
       if (isRender && srvId && !env.RENDER_SERVICE_ID) env.RENDER_SERVICE_ID = srvId;
     }
     form.environment_variables_json.value = JSON.stringify(env, null, 2);
   }
   ```

2. **Automated Variable Injection (`insertConnectorVariables`)**:
   Clicking **`+ Add connector env vars`** now injects Render variables alongside connector tokens:
   ```javascript
   function insertConnectorVariables() {
     const form = $('configurationForm');
     try {
       const env = JSON.parse(form.environment_variables_json.value || '{}');
       const clientId = form.client_id.value || activeConfiguration?.client?.id || '';
       const item = activeConfiguration?.infrastructure?.find(v => v.id === form.infrastructure_id.value) 
                    || activeConfiguration?.infrastructure?.[0];
       
       if (!env.VTAB_CLIENT_ID && clientId) env.VTAB_CLIENT_ID = clientId;
       if (!env.VTAB_AGENT_TOKEN) env.VTAB_AGENT_TOKEN = '        ';
       if (!env.VTAB_SIMULATE_BUILD) env.VTAB_SIMULATE_BUILD = '0';
       
       if (item?.provider === 'render' || item?.provider_resource_id?.startsWith('srv-')) {
         const srvId = form.render_service_id?.value || item?.provider_resource_id || item?.config?.service_id || '';
         if (srvId) env.RENDER_SERVICE_ID = srvId;
         if (!env.RENDER_API_KEY) env.RENDER_API_KEY = 'rnd_...';
       }
       form.environment_variables_json.value = JSON.stringify(env, null, 2);
       notify('Connector and Render variables inserted.');
     } catch (e) {
       notify('Invalid JSON in environment variables.', true);
     }
   }
   ```

3. **Persistence on Save (`saveConfiguration`)**:
   ```javascript
   if (form.render_service_id && form.render_service_id.value.trim()) {
     provider_config.service_id = form.render_service_id.value.trim();
   }
   ```

---

### 2.2. Backend Auto-Population & Provider Resource Synchronization
#### `manager/main.py`
1. **Auto-populate `RENDER_SERVICE_ID` on Query (`get_client_configuration`)**:
   ```python
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
   ```

2. **Sync `provider_resource_id` on Configuration Update (`update_client_configuration`)**:
   ```python
   provider_config = {**existing_config, **payload.provider_config}
   if row["provider"] == "render":
       srv_candidate = provider_config.get("service_id") or environment.get("RENDER_SERVICE_ID")
       if srv_candidate and not str(srv_candidate).startswith("masked:"):
           db.execute("UPDATE client_infrastructure SET provider_resource_id=? WHERE id=?", 
                      (str(srv_candidate).strip(), row["id"]))
           if "RENDER_SERVICE_ID" not in environment or not environment["RENDER_SERVICE_ID"]:
               environment["RENDER_SERVICE_ID"] = str(srv_candidate).strip()
   ```

---

### 2.3. Release Catalog Ordering & GitHub Commit Timestamp Alignment
Previously, two issues affected catalog sorting:
1. The catalog sorted releases by `status = 'available'` first, which pushed newly synced draft commits to the bottom.
2. In `register_release_candidate`, newly synced commits were assigned the local insertion timestamp `utc_now()`. Because batch commits were inserted in quick succession, newer commits sometimes received timestamps milliseconds earlier than older commits in the same batch, placing the newest commit on Row #2 instead of Row #1.

#### The Fix:
- In `register_release_candidate()` and `sync_github_releases()`, releases now use the commit's exact GitHub author date (`candidate["committed_at"]`):
  ```python
  commit_date = candidate.get("committed_at")
  created_at = commit_date or now
  ```
- If a release already exists, `sync_github_releases()` synchronizes its `created_at` timestamp with the commit's true author date.
- `list_releases` sorts by `ORDER BY created_at DESC`.

**Result**: The latest commit on GitHub `main` is now **always guaranteed to be Row #1** in the Release Catalog.

---

### 2.4. Deployment Lock & Supersede Management
To prevent operators from getting locked out when re-offering updates (`"Another deployment for this client component is already active"`), we added:
- Explicit `cancelDeploymentAction` in `app.js` and `POST /api/v1/operator/deployments/{id}/cancel` in `main.py`.
- Automatic release of stale SQLite locks upon manual cancellation or supersede.

---

### 2.5. Approval Gate Protection & Auto-Transition to "Completed"
Previously, two issues existed in the deployment lifecycle:
1. **Premature Auto-Completion**: In `finalize_healthy_deployment_if_ready`, newly created deployments in `pending_approval` state were checked before the operator had clicked **`Approve`** and before the webhook had reached Render. Since `update_available` on Render was false initially, a fallback branch prematurely marked the deployment as `succeeded` (`Completed`), bypassing the `[Approve]` button entirely.
2. **Missing Status Synchronization**: Once deployed on Render, jobs in `Offered` (`scheduled`) state remained stuck because Render cloud containers cannot reach localhost (`127.0.0.1:8840`) to report back.

#### The Fix in `manager/main.py`:
- **Strictly Exclude `pending_approval` from Auto-Finalization**:
  ```python
  def finalize_healthy_deployment_if_ready(deployment: Any) -> Any:
      if not deployment:
          return deployment
      row = dict(deployment)
      status = row.get("status")
      # Never auto-finalize jobs that are waiting for operator approval!
      if status in ("succeeded", "failed", "cancelled", "rolled_back", "pending_approval"):
          return deployment
  ```
- **Commit-Accurate Verification**:
  Removed the premature `not update_avail` fallback. A deployment now only auto-finalizes to `Completed` if:
  1. The job has already been approved (`scheduled` / `apply_requested` / `deploying`).
  2. The target commit SHA matches the commit actually running live on Render (`commit_sha`).
  3. The health check (`/api/v1/health`) returns 200 OK.
  ```python
  if target_sha and live_commit and (target_sha.startswith(live_commit[:8]) or live_commit.startswith(target_sha[:8])):
      ready = True
  elif target_version and (live_ver == target_version or target_version in (f"v{live_ver}", f"commit-{live_commit[:12]}")):
      ready = True
  ```
- **Manual `[Complete]` action button**: Added a green **`Complete`** button in `app.js` and `POST /api/v1/operator/deployments/{id}/complete` in `main.py`, allowing operators to manually finalize approved deployments if desired.

---

### 2.6. Instant "Offer Update" Availability on GitHub Sync
Previously, synced GitHub commits were created as `draft` and `pending`, requiring manual CI validation and publish steps before `Offer update` appeared.
- In `register_release_candidate()` and `sync_github_releases()`, commits discovered from the repository are automatically approved and marked `status='available'` with `validation_status='approved'`.
- Whenever you click **Sync GitHub**, new commits immediately display the green **`Offer update`** button.

---

### 2.7. Supabase PostgreSQL Storage Backend (Active)
The Manager control plane has successfully transitioned from local SQLite to **Supabase PostgreSQL** (`https://asjscibujiikthxmpxgg.supabase.co`):
- **Unified Schema & RLS**: All 12 tables (`clients`, `client_agents`, `releases`, `deployments`, `deployment_logs`, `deployment_locks`, `support_tickets`, `client_infrastructure`, `client_configuration_versions`, `manager_settings`, `audit_events`, `github_webhook_events`) are active in Supabase under `public` schema with RLS protection.
- **Data Migration Complete**: 417 records (21 clients including `VTAB`, 41 releases, 24 deployments, 55 logs, and 231 audit events) were synced directly from SQLite to Supabase.
- **Active Connection**: Configured in `.env` via `VTAB_MANAGER_DATABASE_URL` (`postgresql://postgres:...@db.asjscibujiikthxmpxgg.supabase.co:5432/postgres?sslmode=require`).
- **Live Health Verified**: `GET /api/v1/health` reports `"storage": "supabase-postgresql"` and `"status": "healthy"`.

---

## 3. End-to-End Operator Workflow

1. **Push Changes**: Developer pushes code to GitHub `main` branch.
2. **Sync Releases**: Open Manager (`http://127.0.0.1:8840`) $\rightarrow$ Releases $\rightarrow$ click **`Sync GitHub`**. The new commit immediately shows **`Offer update`**.
3. **Offer Update**: Click **`Offer update`** $\rightarrow$ Select client (e.g. `VTAB`).
   - The deployment is created with `status = 'pending_approval'` (displayed as **`Pending approval`** / **`Waiting`**).
4. **Operator Approves Rollout**:
   - Operator clicks **`Approve`** in the Deployments table.
   - Status changes to **`Offered`**, and the webhook (`POST /api/v1/update/webhook`) is dispatched to Render.
5. **Client Admin Clicks "Update Now"**:
   - In `https://vtab-production.onrender.com`, the green banner appears: `Update available: Reporting Services ... [Update Now]`.
   - Clicking **`Update Now`** triggers Render deployment via Render REST API (`POST /v1/services/{id}/deploys`).
   - An engaging **8-bit Pixelated Build Animation Screen** takes over the display with animated cyber rocket thrusters, chunky 30-block progress bar, elapsed timer, and live terminal telemetry, entertaining the user while the cloud container compiles.
6. **Automatic Completion & Reveal**:
   - Render builds and deploys the new container (~2 min).
   - The pixelated build screen polls the service, triggers a **`★ LEVEL UP: UPDATE COMPLETE! ★`** celebration, and smoothly reloads the updated app.
   - Once live, the Manager's background verification detects the matching `commit_sha` and healthy status.
   - Status automatically converts from **`Offered`** to **`Completed`** (green), and the client's installed version updates!
