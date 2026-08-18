# Historical import audit — 2026-08-18

## Scope

- ADCB credit-card statements received from October 2024 through July 2026.
- Wio credit statements received from March 2025 through August 2026.
- FAB card statements were inventoried but not imported in this run because the
  statement PDFs use CID-encoded text and the saved portal login was rejected.
  The configured official CSV adapter remains the approved import route.

## Evidence and parsing

| Source | Statements | Parsed rows | Arithmetic result | Actual account |
|---|---:|---:|---|---|
| ADCB | 22 | 3,093 | 22/22 tied | `ADCB Credit Card · 8833 / 6838` |
| Wio | 18 | 192 | 18/18 tied, including one zero-row statement | `Wio Credit Card · 4113 / 5009` |

All 40 statement artifacts were archived through the evidence catalogue. The
historical backfill intentionally answered unresolved AI-policy requests with
empty proposals; no category, vendor, property, subscription, or evidence fact
was guessed.

## Actual commit result

| Source | New rows | Required statement IDs verified | Missing IDs | Duplicate IDs | Cross-source rows suppressed |
|---|---:|---:|---:|---:|---:|
| ADCB | 2,948 | 3,090/3,090 | 0 | 0 | 3 |
| Wio | 172 | 192/192 | 0 | 0 | 0 |

The ADCB guard suppressed only unique exact matches against prior browser rows:
same Actual account, date, minor-unit amount, and normalized imported merchant.
Six older exact browser/statement duplicate candidates already existed before
this run and were not deleted or otherwise mutated.

Every one of the 39 non-empty committed jobs was replayed and returned
`idempotent_replay: true` (ADCB 22/22, Wio 17/17).

## Backups

- Pre-import: `20260818T053200Z`, archive SHA-256
  `c5d9659be9f9bd3791faebd998348aa65c5048ba769b0cf20c70df3db3b6ebd4`.
- Post-import: `20260818T053847Z`, archive SHA-256
  `e530107dbf59a25c247a098c4bb06e7fa118854b9fcb49ead99789ce581d649f`.

Both archives passed `SHA256SUMS`, safe extraction, JSON parsing, and SQLite
integrity verification.

## Post-import snapshot

- Actual transaction rows in the audited period: 3,610.
- ADCB account: 3,393 rows, covering 2024-09-14 through 2026-07-13.
- Wio account: 192 rows, covering 2025-01-31 through 2026-08-01.
- Uncategorised rows remain eligible for later classification: ADCB 2,141;
  Wio 34.

## FAB continuation

Outlook contains 22 card-statement messages for card suffix 6031 between July
2024 and August 2026. The PDFs must not be imported from unverified OCR. Resume
from the authenticated FAB portal, download the official transaction CSV, and
route it through `fab_csv_v1` using the same STAGE → AI handoff → PREFLIGHT →
COMMIT workflow.
