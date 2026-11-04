# Client application integration kit

These files are intentionally separate from the Manager. Copy the shared agent into both Reporting Services and Admin Console, expose its read-only discovery endpoint to the signed-in client administrator, and mount the banner in the workspace shell.

Required environment values: `VTAB_MANAGER_URL`, `VTAB_CLIENT_ID`, `VTAB_AGENT_TOKEN`, `VTAB_CLIENT_ADMIN_TOKEN`, `VTAB_COMPONENT`, `VTAB_RELEASE_ROOT`, `VTAB_ACTIVE_RELEASE`, and `VTAB_HEALTH_URL`.

The polling path only discovers an approved offer. `Update Now` requires the separate client-administrator credential. Self-hosted installation is extracted into an immutable commit-SHA directory and activated with an atomic link; a failed health check restores the previous link. Admin Console uses the same agent with `VTAB_COMPONENT=administration`.

