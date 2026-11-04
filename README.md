# VTAB Reporting Manager

The Reporting Manager is an operational control plane for VTAB client environments. It tracks installed versions, offers signed updates, records client-approved support tickets, and keeps an audit trail. It does not store report definitions, dashboard data, source rows, or connection credentials.

## Run locally

```powershell
cd "D:\Python\Project reporting tool\VTAB Reporting Manager"
Copy-Item .env.example .env
python start_manager.py
```

`start_manager.py` creates an isolated Python environment, installs the required packages once, and launches `run_manager.py`. After installation, `python run_manager.py` can also be used from that environment.

For convenience, running `python run_manager.py` directly from a new extraction also redirects to the automatic setup when its dependencies are missing.

The default local address is `http://127.0.0.1:8840`.

## First build scope

- Client and environment registry
- Release catalog with SHA-256 manifests
- Client-approved deployment jobs
- Connector check-in and update polling APIs
- Sanitized, client-approved ticket escalation
- Operator audit history
- Browser-based manager interface

## Manager interface 0.2

- Responsive operations dashboard with persistent light and dark themes
- Deployment-first workflow with existing-client selection or inline client registration
- Separate release, client-environment, deployment, and support workspaces
- Settings workspace containing appearance controls, authentication planning, data-boundary information, and the operator audit trail
- Authentication screen intentionally reserved for the next security phase

## Manager interface 0.3

- Dark theme is the default, with persistent light-theme switching
- RGB accent lighting, richer operational icons, and clearer information density
- Wide deployment workspace designed to avoid nested desktop scrollbars
- Cancel and close actions bypass validation and always dismiss the dialog
- Dedicated Supabase PostgreSQL storage through `VTAB_MANAGER_DATABASE_URL`
- GitHub repository configured and permanently locked from Settings
- Recent GitHub releases discovered automatically from the configured repository

## Centralized deployment control plane 0.4

- Professional seven-stage first-time deployment studio: client, applications, provider, configuration, preflight, authorization, and completion
- Independent locked repositories for Reporting Services and the Administration Tool
- Application profiles defining runtime versions, ports, build/start commands, health routes, and required secret names
- Render and DigitalOcean provider adapters with request-scoped credentials
- Strict preflight gates for release integrity, repositories, secrets, database configuration, region, server capacity, DNS, and provider authentication
- Additive infrastructure inventory and detailed deployment-stage logs
- Rollback snapshot references and preserved previous-version metadata
- Selective component update discovery and client-side update preflight endpoints
- Outbound connector model: client environments poll the manager; the control plane does not connect directly to client databases
- SQLite development support and dedicated Supabase/PostgreSQL production storage

The manager never persists cloud API tokens or application secret values. Provider provisioning is initiated only after preflight approval and explicit operator confirmation. Test real provider execution in a staging account before production use because it can create billable cloud resources.

## Controlled update pipeline 0.5

- Non-destructive SQLite/PostgreSQL migrations and canonical separation of provider configuration from encrypted environment variables
- AES-256-GCM secret storage, masked configuration reads, version history, sync state, and operator audit history
- GitHub HMAC webhook validation, delivery-ID deduplication, exact repository/commit registration, manual synchronization, CI approval, notes, publishing, update offers, and superseding
- Role-aware operator authorization plus separate client connector and client-administrator credentials
- Deployment idempotency, per-client/component locks, cancellation, logs, retries, rollback jobs, provider deployment IDs, exact installed SHA/version checks, and health verification
- Read-only update discovery: polling never starts deployment; client `Update Now` consent is mandatory
- Render auto-deployment disabled and exact-SHA activation; self-hosted integration uses isolated SHA folders and atomic rollback
- Independent Reporting Services and Administration component versions
- Drop-in client application integration assets under `integrations/`

Copy `.env.example` to `.env` and replace every placeholder. Production startup intentionally fails if `VTAB_CONFIG_ENCRYPTION_KEY` is missing.

## Dedicated Supabase source

For production, create a dedicated Supabase project and copy its direct connection or Session pooler string from **Connect**. Put it in `.env` and retain `sslmode=require`:

```text
VTAB_MANAGER_DATABASE_URL=postgresql://.../postgres?sslmode=require
```

The manager creates its schema during first startup. The same schema is available at `supabase/manager_schema.sql` for controlled/manual deployment. If the variable is empty, the application deliberately uses local SQLite for development.

For a private GitHub repository, store a read-only token only in `.env` as `VTAB_GITHUB_TOKEN`. Public repositories do not require a token. The repository itself can be entered once under **Settings → GitHub release source** and is then locked.

## Security boundary

Each client environment remains authoritative for report data, credentials, Administration Tool access, and production deployment approval. The connector uses an outbound API connection and receives a client-specific bootstrap token. Production deployments should place the operator key and connector tokens in a secret vault.

## Integration endpoints

- `POST /api/v1/agent/check-in`
- `GET /api/v1/agent/deployments/next`
- `POST /api/v1/agent/deployments/{id}/report`
- `POST /api/v1/agent/tickets/escalate`
- `GET /api/v1/agent/updates/available?component=services|administration`
- `POST /api/v1/agent/deployments/{id}/preflight`
- `POST /api/v1/agent/deployments/{id}/accept`

Operator deployment endpoints:

- `GET /api/v1/operator/wizard/profiles`
- `POST /api/v1/operator/wizard/preflight`
- `POST /api/v1/operator/wizard/execute`
- `GET /api/v1/operator/wizard/deployments/{id}/status`
- `GET /api/v1/operator/clients/{id}/infrastructure`
- `GET|PUT /api/v1/operator/clients/{id}/configuration`
- `POST /api/v1/operator/releases/sync-github`
- `POST /api/v1/operator/releases/{id}/validate`
- `POST /api/v1/operator/deployments/{id}/cancel`
- `POST /api/v1/operator/deployments/{id}/retry`
- `GET /api/v1/operator/deployments/{id}/logs`

GitHub sends signed events to `POST /api/v1/webhooks/github` using the configured webhook secret.

Agent calls require `X-VTAB-Client-ID` and `X-VTAB-Agent-Token` headers.

