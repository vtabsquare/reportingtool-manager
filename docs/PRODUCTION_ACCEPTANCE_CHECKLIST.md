# Production acceptance checklist

- [ ] Back up the existing manager database and run the additive migration in staging.
- [ ] Confirm existing clients, infrastructure records, releases, tickets and audits remain present.
- [ ] Configure operator roles, encryption key, webhook secret and exact component repositories.
- [ ] Send valid, invalid and duplicate GitHub webhook deliveries.
- [ ] Verify a push creates only a draft release and does not deploy or restart anything.
- [ ] Validate required CI checks, publish, offer, cancel and supersede a release.
- [ ] Confirm polling is read-only and Update Now requires a client-administrator credential.
- [ ] Test simultaneous offers and confirm the component lock rejects the duplicate.
- [ ] Test successful deployment and exact installed version/SHA evidence.
- [ ] Force a failed build and failed health check, then test retry and rollback.
- [ ] Confirm Render auto deploy is off for every managed service.
- [ ] Confirm self-hosted installations use isolated SHA directories and atomic activation.
- [ ] Test Reporting Services and Admin Console independently and together.
- [ ] Regression-test DuckDB, Parquet, measures, dashboards, authentication and exports in the client apps.
- [ ] Test an offline connector, stale release, unauthorized request and Windows desktop compatibility.
- [ ] Review monitoring and audit output for accidental secret or client-data exposure.
