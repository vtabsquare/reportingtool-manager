# VTAB Centralized Deployment Implementation

This file maps the centralized deployment plan to the implementation delivered in VTAB Reporting Manager 0.4.0.

## Control-plane boundaries

- The Manager stores client operational metadata, releases, deployment state, infrastructure references, sanitized ticket data, and audit events.
- It does not store report definitions, client source rows, dashboard data, cloud-provider tokens, or application secret values.
- Client connectors communicate outbound over HTTPS. The Manager does not open inbound database access to a client environment.
- Cloud-provider and application secrets are request-scoped. They are removed from the browser form after an accepted deployment and excluded from audit events.

## Implemented architecture

| Plan area | Implementation |
|---|---|
| Application profiles | `manager/engine/profiles.py` |
| Preflight gatekeeper | `manager/engine/validator.py` and `manager/engine/engine.py` |
| Render REST adapter | `manager/engine/adapters/render.py` |
| DigitalOcean v2 adapter | `manager/engine/adapters/digitalocean.py` |
| Health-check helper | `manager/engine/health.py` |
| Rollback references | `manager/engine/rollback.py` |
| Seven-stage operator wizard | `manager/static/index.html`, `app.js`, and `styles.css` |
| Additive SQLite/PostgreSQL schema | `manager/database.py` and `supabase/manager_schema.sql` |
| Selective client update discovery | `GET /api/v1/agent/updates/available` |
| Update safety checks | `POST /api/v1/agent/deployments/{id}/preflight` |
| Operator rollback request | `POST /api/v1/operator/deployments/{id}/rollback` |
| Execution status and logs | `GET /api/v1/operator/wizard/deployments/{id}/status` |

## Hard deployment gates

The wizard blocks execution when any required check fails:

- client target and component-specific published release;
- SHA-256 release manifest integrity;
- Reporting Services and Admin Console repository references;
- required secret names for the selected application profile;
- database configuration;
- supported provider region;
- Render workspace selection;
- DigitalOcean Ubuntu image, minimum instance class, and SSL domain;
- provider token authentication;
- DigitalOcean region and instance-size availability;
- explicit provisioning authorization.

The client update preflight separately enforces current health, free-disk capacity, clean working tree, migration monotonicity, and rollback-manifest readiness.

## Provider execution model

Render receives an application-specific build command, start command, health route, branch, region, plan, and request-scoped environment values through its API.

DigitalOcean receives a hardened base bootstrap through cloud-init. The bootstrap installs the operating-system prerequisites and firewall rules but does not embed application secrets. The outbound VTAB connector is responsible for retrieving protected values from the client's vault and completing the allow-listed application procedure.

## Production acceptance required

Local automated tests verify schema migration, API contracts, release/client/deployment compatibility, repository locking, update preflight, and wizard markup. Real cloud provisioning is deliberately not executed during packaging because it requires customer-scoped credentials and may create billable resources.

Before production rollout, run controlled staging acceptance for each provider:

1. use a least-privilege test token and a disposable account/project;
2. verify provider resource creation and regional capacity;
3. validate DNS, TLS, health routes, and connector check-in;
4. test an application update and forced health-check failure;
5. confirm rollback within the required recovery window;
6. review provider and Manager audit logs for secret leakage;
7. delete the disposable resources after sign-off.

