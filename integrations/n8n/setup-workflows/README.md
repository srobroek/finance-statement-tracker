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
