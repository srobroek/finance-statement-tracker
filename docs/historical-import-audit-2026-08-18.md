# Historical import audit — 2026-08-18

## Scope

- ADCB credit-card statements received from October 2024 through July 2026.
- The new ADCB statement received on 18 August 2026.
- Wio credit statements received from March 2025 through August 2026.
- The latest 100 visible FAB credit-card rows and 100 visible FAB current-account
  rows captured from the authenticated portal. FAB's displayed CSV action did
  not produce a browser download, so this is an explicitly owner-approved
  visible-row capture rather than a claim of complete account history.

## Evidence and parsing

| Source | Statements | Parsed rows | Arithmetic result | Actual account |
|---|---:|---:|---|---|
| ADCB | 22 | 3,093 | 22/22 tied | `ADCB Credit Card · 8833 / 6838` |
| Wio | 18 | 192 | 18/18 tied, including one zero-row statement | `Wio Credit Card · 4113 / 5009` |

The additional ADCB statement parsed and tied at 132 rows. The FAB portal
captures are not statement arithmetic sources; source direction, stable capture
identity, and exact visible-row approval are retained in their manifests.

All 40 statement artifacts were archived through the evidence catalogue. The
historical backfill intentionally answered unresolved AI-policy requests with
empty proposals; no category, vendor, property, subscription, or evidence fact
was guessed.

## Actual commit result

| Source | New rows | Required statement IDs verified | Missing IDs | Duplicate IDs | Cross-source rows suppressed |
|---|---:|---:|---:|---:|---:|
| ADCB | 2,948 | 3,090/3,090 | 0 | 0 | 3 |
| Wio | 172 | 192/192 | 0 | 0 | 0 |
| FAB credit card | 100 | 100/100 | 0 | 0 | 0 |
| FAB current account | 100 | 100/100 | 0 | 0 | 0 |

The ADCB guard suppressed only unique exact matches against prior browser rows:
same Actual account, date, minor-unit amount, and normalized imported merchant.
Six older exact browser/statement duplicate candidates already existed before
this run and were not deleted or otherwise mutated.

Every one of the 39 historical non-empty statement jobs was replayed and
returned `idempotent_replay: true` (ADCB 22/22, Wio 17/17). The new ADCB job
was separately committed, read back, and replayed idempotently. Both FAB
captures were read back with all 100 imported IDs present exactly once.

An implementation defect initially lost the captured FAB direction when
serializing deterministic credit types. Actual intentionally deduplicated the
same imported IDs instead of updating them. A versioned repair command then
matched the exact imported ID, account, date, and old amount for 29 affected
rows, allowed only an exact sign reversal, and re-read every row after sync.
The repair changed 27 card credits and two current-account credits. Its replay
changed zero rows and recognized all 29 as already corrected. The full 200-row
FAB readback has zero missing IDs, duplicates, sign mismatches, or amount
mismatches.

## Backups

- Pre-import: `20260818T053200Z`, archive SHA-256
  `c5d9659be9f9bd3791faebd998348aa65c5048ba769b0cf20c70df3db3b6ebd4`.
- Post-import: `20260818T053847Z`, archive SHA-256
  `e530107dbf59a25c247a098c4bb06e7fa118854b9fcb49ead99789ce581d649f`.
- Pre-FAB-repair: `20260818T100451Z`, archive SHA-256
  `db2c31ba67f2f7e824e654d3b2dce39d0db291fd06e0e7ac8cc5b9cb4f18160e`.
- Post-FAB-repair: `20260818T100919Z`, archive SHA-256
  `e694a54bea3c103d1c38a6d87c6917e10d80672c33c883a5645a9e3d66518fc3`.

All four archives passed `SHA256SUMS`, safe extraction, JSON parsing, and SQLite
integrity verification. Each FAB repair-boundary archive validated all five
SQLite databases.

## Post-import snapshot

- Actual transaction rows in the audited 2024–2026 readback: 3,942.
- ADCB account: 3,525 rows, covering 2024-09-14 through 2026-08-14.
- Wio account: 192 rows, covering 2025-01-31 through 2026-08-01.
- FAB card: 100 rows, covering 2024-07-18 through 2026-08-15, with a readback
  balance of AED -203.51 for the captured history.
- FAB current: 100 rows, covering 2026-05-06 through 2026-08-17, with a readback
  balance of AED 109,956.72 for the captured history. This is not the portal
  balance because the portal exposed only the latest 100 rows and no opening
  balance transaction was invented.
- Uncategorised rows remain eligible for later classification: ADCB 2,151;
  Wio 34; FAB card 1; FAB current 0.

## Additional authenticated captures

Amazon produced a structured 137-order capture covering 2026-01-01 through
2026-08-17. It is purchase-evidence input, not a second transaction ledger;
orders with missing or zero displayed totals remain unmatched. Sarwa produced a
holdings snapshot across four Invest accounts plus Trade and Protection. The
authenticated view did not expose stable activity rows, so no synthetic Sarwa
transactions or opening balances were written to Actual.

Future FAB history should prefer an official CSV export when the portal makes
one downloadable. Otherwise each visible-row capture must retain its explicit
coverage limitation and exact owner approval before the standard guarded
pipeline can commit it.
