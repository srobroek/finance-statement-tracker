# Statement adapter and Phase 1 runtime evidence checklist

Status: **SPEC_ONLY / NO PRODUCTION WRITES**

This checklist records what can be proven from the repository and existing
runtime artifacts as of 2026-08-19. It must not be used to infer an issuer
layout from notification emails, marketing material, terms, or another bank's
statement.

## Current evidence inventory

| Source | Adapter state | Evidence-supported conclusion |
|---|---|---|
| ADCB | `adcb_v1` active | Real statement corpus and tied manifests exist. |
| Emirates Islamic | `emirates_islamic_v1` active | Real statement corpus and tied manifests exist. |
| Wio Credit | `wio_credit_v1` active | Real statement corpus and tied manifests exist. |
| RAKBANK World | Placeholder | Transaction-notification fixtures exist, but no statement PDF, extracted statement text, attachment contract, or tied statement manifest exists. |
| Standard Chartered Platinum X | Placeholder | No verified statement PDF, extracted statement text, attachment contract, or tied statement manifest exists. The live email source is also still a placeholder. |

The TypeScript package test now requires its packaged issuer profiles to equal
the `ACTIVE` adapters in `config/statement-sources.json`. A placeholder with an
adapter name fails the test. This prevents a speculative parser from becoming
callable merely because a workflow exists. `PLACEHOLDER` is an intentional,
card-scoped interim state: RAKBANK and Standard Chartered statement close and
finalization remain paused until their first real fixtures pass, while RAK live
notification cashback and ACTIVE sources such as ADCB continue independently.

## Evidence required for each missing statement adapter

Acquire the following separately for RAKBANK World and Standard Chartered
Platinum X. Secrets stay in the credential system and are never copied into a
fixture, receipt, command line, workflow export, or commit.

1. Original issuer PDF attachment archived immutably with SHA-256, byte count,
   archive receipt, and acquisition timestamp.
2. Redacted mail-envelope receipt containing mailbox source code, exact sender,
   exact subject, received timestamp, immutable message identity, attachment
   filename, attachment size, and attachment SHA-256. Do not store the message
   body unless it is itself required evidence.
3. Password requirement recorded as a credential reference only. Record
   encrypted versus plain; never record the password value.
4. Networkless PDF utility receipt for `validate`, `unlock` when needed, and
   fixed `profile`. Record image digest, source SHA-256, page count, output
   SHA-256 for unlocked bytes, and extracted-text SHA-256. Do not retain the
   unlocked PDF or extracted text in CI artifacts.
5. A reviewed redacted text fixture that preserves layout tokens needed to
   parse: statement and period dates, payment due date, opening and closing
   balances, minimum and total due, card suffix placement, transaction/posting
   dates, descriptions, debit/credit markers, AED amounts, and foreign amount,
   currency, and exchange-rate markers when present.
6. Reviewer-authored expected canonical manifest with transaction count, debit
   total, credit total, opening and closing balances, calculated balance,
   balance difference, card suffixes, deterministic transaction identities,
   and expected source line for every row.
7. Statement arithmetic must tie within AED 0.01. A non-tying or partially
   parsed statement is a negative fixture and cannot activate the adapter.
8. Include evidenced examples of purchase and payment. Include refund,
   reversal, reward, fee, and foreign purchase only when they genuinely occur;
   absence of an example remains an explicit untested topic rather than a
   fabricated row.
9. Prefer two consecutive periods so header/layout stability and period
   boundaries can be checked. At minimum, one complete statement plus one
   independently reviewed negative or layout-variation fixture is required.
10. Add the adapter to both canonical implementations, activate the exact mail
    contract in `config/statement-sources.json`, and prove detection is unique.
    Parser, projection, outbox preflight, exact Actual verification, replay,
    and malformed/partial-input tests must all pass before removing blockers.

## Linux PDF utility acceptance receipt

The GitHub workflow is the first legitimate Linux gate. A successful receipt
must bind all evidence to one source commit and immutable image digest and show:

- the Unix-domain socket server tests ran on Linux, including health, content
  type, length, chunking, unknown operation, timeout/OOM redaction, shell-like
  password text, cleanup after failure, and fixed-profile enforcement;
- worker fixtures passed for encrypted, corrupt, active-content, polyglot,
  oversize/page-bomb, invalid CLI, and valid profile inputs;
- the container ran with `--network none`, read-only root, tmpfs scratch, and a
    network probe that could not establish an outbound connection;
- dependency audit, HIGH/CRITICAL image scan, and SBOM generation passed; and
- the pushed GHCR image receipt contains the immutable digest and exact source
  SHA. A green unit test on Windows is not a substitute for this receipt.

The receipt is valid only when the Linux job itself is green. A repository
configuration, local Docker build, or historical image is not equivalent.

## Direct Actual node disposable acceptance receipt

Existing historical rebuild receipts prove the older Actual tooling, not the
new `n8n-nodes-finance.actualBudget` node. The node remains `SPEC_ONLY` until a
fresh disposable Actual server and budget demonstrate all of the following:

1. Receipt binds Actual server version, disposable sync ID, source commit,
   custom n8n image digest, node package version, and test start/end time. Store
   only credential references, never passwords.
2. `doctor` reads the expected open account, categories, and initial integer
   balance through `@actual-app/api`.
3. `preflight` accepts one immutable `PREPARED` outbox only after authoritative
   lease acquisition/readback and rejects a closed account or unknown category.
4. `import` is executed through a scheduled/subworkflow/recovery context with a
   live positive fencing token and `reimportDeleted:false`. Capture returned
   errors and balance before/after.
5. `verify` observes exactly equal account, imported ID, date, integer amount,
   imported payee, category, notes, and cleared state, with equal canonical
   expected/observed SHA-256 values and the expected account balance.
6. Replay of the identical outbox creates no duplicate and preserves the same
   observed fields and balance.
7. An existing imported ID with one altered field fails verification. Expired
   lease, mutation-disabled credential, manual/MCP mode, unknown category,
   duplicate ID, and returned Actual error are negative fixtures.
8. Kill after Actual mutation but before outbox commit, then run recovery. The
   terminal state must be one Actual row and one `COMMITTED` outbox with no
   second mutation.
9. Shut down and delete only the disposable environment after receipts and
   redacted logs are archived. No production Actual file or ledger is touched.

## Promotion decision

Until the missing issuer evidence and disposable receipts above exist:

- RAKBANK and Standard Chartered statement workflows remain blocked and cannot
  reconcile or close cashback periods;
- that block is scoped to those two statement sources and does not block RAK
  live notification cashback, ADCB historical statement ingestion, or other
  ACTIVE source workflows;
- the PDF utility and custom Actual node remain `IMPLEMENTED_NOT_DEPLOYED`; and
- historical bridge/tooling receipts cannot be relabelled as n8n custom-node
  evidence.
