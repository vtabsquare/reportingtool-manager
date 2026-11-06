# VTAB Reporting Manager — Implemented Update Checklist

## Included updates

1. **Inbound GitHub commit approval gate**
   - GitHub commits are registered as `pending_approval` / `pending`.
   - Pending commits appear in Deployments with commit, message, author, date, NEW status, Approve, and Reject actions.
   - Only approved commits enter the deployable Release Catalog.

2. **Audit trail CSV export**
   - Settings includes an Export CSV action.
   - Output uses UTF-8 BOM, CRLF rows, RFC 4180 quoting, and a date-stamped filename.

3. **Client environment management**
   - Cards/Table view toggle is persisted per browser.
   - Both views include Edit and Delete actions.
   - Deletion is role-protected, confirmed in the UI, audited, and uses existing database cascades.

4. **Multi-client release offers**
   - Offer dialog supports searchable checkbox selection.
   - Select All selects all current search matches.
   - Clear Selection resets the selection.
   - One deployment offer is created per selected client with a stable idempotency key.

5. **Offer dialog cleanup**
   - Category/status pill filters are absent.
   - The dialog focuses on client search and explicit selection.

6. **Single LIVE release per component**
   - LIVE is derived from the newest successful/completed deployment.
   - Client-reported installed versions provide a fallback.
   - At most one release per component receives the LIVE tag.

7. **One-click rollback target**
   - The immediately previous successful release is identified per component.
   - A Rollback action opens the same controlled multi-client offer workflow.

8. **NEW release indicator**
   - The newest synchronized release per component receives a NEW tag in pending and release views.

9. **Self-repairing Python dependency startup**
   - `run_manager.py` validates FastAPI, Uvicorn, dotenv, Pydantic, Psycopg, and Cryptography before importing the application.
   - An existing but incomplete `.manager-venv` is automatically repaired through `start_manager.py --reinstall`.
   - The ready marker is trusted only after an actual import probe succeeds.

## Safety and compatibility

- Existing `/api/v1/agent/*` endpoints were not changed.
- Existing client webhook payload construction was not modified.
- No destructive database migration or schema change was introduced.
- Deployment offers again require the existing approval gate before becoming scheduled.
- Prefilled provider and application secrets were removed from the HTML source.

## Verification completed

- JavaScript syntax check: passed.
- Python compile check: passed.
- Automated suite: **19 tests passed**.
- Local API/static smoke test: HTML and JavaScript returned HTTP 200; `/api/v1/health` reported healthy.

