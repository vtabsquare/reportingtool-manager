# Apply the 0.5.0 controlled-update release

1. Back up the existing application folder and manager database.
2. Copy the update package over the existing VTAB Reporting Manager folder.
3. Compare `.env.example` with the existing `.env`; do not replace real values with placeholders.
4. Add the new encryption, GitHub webhook, repository, role and timing variables.
5. Install/update dependencies with `python start_manager.py` or `pip install -r requirements.txt`.
6. Start the Manager once. Its schema upgrade is additive and migrates legacy environment variables into the encrypted canonical column.
7. Configure the two component repositories, add the GitHub webhook endpoint, and validate the first release before publishing it.
8. Merge `integrations/common/update_agent.py` and the workspace banner into Reporting Services and Admin Console.
9. Complete `docs/PRODUCTION_ACCEPTANCE_CHECKLIST.md` in staging before production rollout.

Do not copy an old `.env`, local database, runtime cache or provider token into a shared package.
