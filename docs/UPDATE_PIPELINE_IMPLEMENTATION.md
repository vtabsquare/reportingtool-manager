# VTAB controlled update pipeline — implementation map

This release implements the supplied update-pipeline specification without changing client report data. Existing databases are upgraded additively on startup.

| Area | Delivered implementation |
|---|---|
| Persistent configuration | Canonical `config_json` plus AES-GCM `environment_variables_json`, legacy migration, masked GET/PUT, version and audit history, provider sync status, client configuration editor |
| GitHub releases | HMAC SHA-256 validation, delivery deduplication, approved repository mapping, exact 40-character SHA, manual sync, required CI checks, release notes, publish/offer/supersede controls |
| Deployment security | Operator RBAC, separate agent/client-admin tokens, client/component ownership checks, idempotency keys, deployment locks, state machine, provider ID, installed SHA/version and health verification |
| Recovery | Cancellation, logs, failed-job retry, prior-version rollback job, immutable rollback reference |
| Client applications | Shared update connector and Reporting Services banner; discovery is passive, Update Now requires explicit client-admin authorization |
| Render | Auto deploy disabled, exact `commitId` deployment, provider status support, health and installed-version evidence required before success |
| Self-hosted | Release extracted to isolated component/SHA directory, checksum verified, atomic active link, failed-health rollback |
| Admin Console | Same component lifecycle and connector with `VTAB_COMPONENT=administration`; separate version and release notes |
| Storage | SQLite for development; dedicated PostgreSQL/Supabase supported. `supabase/manager_schema.sql` is additive and rerunnable |

## Acceptance boundaries

Automated tests cover non-destructive migration, encryption/masking/versioning, signed webhooks and duplicate deliveries, release approval gates, repository locking, deployment flow, preflight contracts, UI JavaScript syntax, and Python compilation. Live Render/DigitalOcean creation, real GitHub checks, browser notification permission, Windows service installation, and production monitoring require customer-scoped credentials and must be executed in staging before rollout.

The integration kit is source code to merge into Reporting Services and Admin Console. Those two complete application repositories were not part of the supplied archive, so the manager package cannot silently modify their application shells.
