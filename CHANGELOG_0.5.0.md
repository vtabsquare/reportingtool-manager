# VTAB Reporting Manager 0.5.0

## Added

- Encrypted, masked, versioned client configuration with non-destructive legacy migration
- Operator role authorization and separate client-administrator update consent
- Signed/deduplicated GitHub webhooks, manual commit synchronization and required CI validation
- Exact repository, component, version and commit-SHA release registration
- Release notes, publishing, update offering and superseding controls
- Idempotent deployment offers, component locks, cancellation, logs, retry and rollback lifecycle
- Provider deployment IDs and strict installed version/SHA/health verification
- Render exact-SHA deployment with auto-deploy verification
- Isolated self-hosted release folders with atomic activation and rollback
- Reporting Services and Admin Console integration kit
- Updated additive PostgreSQL/Supabase schema and expanded automated regression tests

## Security notes

- No production secret should be committed to `.env.example`.
- Set a unique `VTAB_CONFIG_ENCRYPTION_KEY` before production startup.
- Use distinct operator, connector and client-administrator credentials.
- A GitHub push only registers a draft candidate; it never deploys or restarts a client service.

