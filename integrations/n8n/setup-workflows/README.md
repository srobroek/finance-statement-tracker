# Explicit n8n setup workflows

These exports are deliberately outside `integrations/n8n/workflows/`. They are
not part of the regular 21-workflow import or activation set and must be
imported one file at a time only for a reviewed setup action.

`22-onedrive-finance-evidence-root-setup.json` is manual-only and inactive. It
uses the bound `Finance OneDrive` OAuth credential to list the drive root,
reuse the exact top-level `Finance Evidence` folder when present, or create
that single folder at the drive root when absent. It then reads the root back,
checks that there is exactly one exact match, inspects its children, and fails
if `Finance Evidence/Finance Evidence` exists.

The final execution item is a redacted receipt: it confirms the exact root and
whether the folder was created or reused, but omits the OneDrive item ID, drive
metadata, URLs, credential values, and file contents. The workflow must remain
inactive and unscheduled. Import it into `90 Platform & Admin`, bind only the
existing `Finance OneDrive` credential, run it once manually, retain the
redacted output, and remove the setup export from n8n if it is no longer
needed.

`23-microsoft-oauth-refresh-proof.json` is a separate manual-only, inactive,
read-only proof for the two Microsoft OAuth credentials. Its Outlook operation
uses a frozen seven-day window, the server-side `isDraft eq false` filter, and
a maximum of one result. The Graph projection requests only the message `id`,
which is discarded before the next node. Its OneDrive operation lists the drive
root once. It does not download content and contains no provider-write node.
The final item retains only result counts, the bounded time window, execution
ID, safety booleans, and verification timestamp; it discards message fields,
file fields, credential values, and token values.

For the restart proof, import this file alone into `90 Platform & Admin`, bind
the existing `Finance Outlook` and `Finance OneDrive` credentials, and keep the
workflow inactive. Then use this exact reviewed sequence:

1. Run the workflow manually and retain its redacted terminal receipt.
2. Capture a metadata-only readback for both bound credentials containing only
   credential ID, credential type, `updatedAt`, and token expiry time when the
   guarded readback can derive it. Never print or persist encrypted credential
   data, access tokens, refresh tokens, client secrets, or response bodies.
3. Restart only the n8n service and wait for its health check to pass.
4. Run the same inactive workflow again and retain the second redacted receipt.
5. Repeat the same metadata-only readback. Accept the proof only when both
   executions are `VERIFIED`, both provider reads succeeded, each Outlook count
   is at most one, the credential IDs/types remain stable, and expiry/updatedAt
   metadata shows no invalid regression. A refresh is proven only if the
   metadata demonstrates a later token lifetime or credential update; two
   successful reads alone prove restart persistence, not refresh.

The two workflow receipts and two metadata-only snapshots form the reviewed
evidence set. Remove the setup workflow from n8n afterward if it is no longer
needed.
