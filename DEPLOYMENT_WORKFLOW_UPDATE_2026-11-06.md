# VTAB Reporting Manager — Deployment Workflow Update

## Implemented

- Deployment history now separates **Application version** from the immutable **Commit SHA**.
- GitHub tags are mapped to their commit SHA during synchronization, so tagged builds display their real application version instead of a synthetic commit label.
- Incoming commits remain outside the Release catalog until an operator approves them.
- Incoming commit decisions use professional **Approve** and **Reject** actions with icons.
- Client deployment requests use **Approve** and **Reject**; manual **Complete** and **Cancel** actions were removed from the UI.
- Rejected deployments become a terminal state, release their deployment lock, and cannot reach a client connector.
- Release rows display **Active** for the latest verified installed release.
- The **NEW** tag is calculated from approved deployable releases only.
- **Offer update** was renamed to **Deploy release** and uses a deployment icon.
- Supersede was removed from the Release table.
- **Roll back** is shown only for the immediately previous successfully deployed release for that component.
- Client Edit and Delete actions now use consistent modern icon buttons and tooltips.
- Browser asset versions were increased to prevent stale cached JavaScript and CSS.

## Approval flow

1. GitHub commit or release is received as `pending_approval`.
2. Operator approves or rejects it in **Incoming commits**.
3. Only an approved artifact enters **Releases**.
4. **Deploy release** creates a client deployment request.
5. The client rollout is approved or rejected.
6. Successful connector health verification marks the installed release **Active**.
7. Only the version immediately preceding Active is eligible for rollback.

## Verification

- JavaScript syntax check passed.
- Python compilation passed.
- All 19 Manager regression tests passed.
- The Windows ZIP was extracted and verified with 50 files.
