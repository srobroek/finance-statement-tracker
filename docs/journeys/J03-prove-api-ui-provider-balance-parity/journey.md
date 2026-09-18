---
id: J03
title: Prove API/UI/provider balance parity
version: 1
status: draft
last_reviewed: 2026-09-17
actors: [finance-operator]
surfaces: [finance-core]
interfaces: [RO]
trace: []
---
# J03 -- Prove API/UI/provider balance parity

## Goal
As an operator, prove that the signed FAB and Sarwa provider values equal the signed Actual API and UI net worth at one valuation instant, without changing any source.

## Preconditions
- P1: Select the wealth-net-worth scope, its configured base currency, and one valuation instant `t`.
- P2: Capture the provider account-to-Actual account mapping. Each FAB cash account and each Sarwa holding account maps to exactly one Actual account.
- P3: Capture signed FAB closing cash and each signed Sarwa holding quantity and price in their provider-native currencies at `t`.
- P4: Capture one immutable authoritative FX snapshot at `t` for every non-base currency. Each snapshot records the pair, rate, timestamp, source, and evidence hash.
- P5: Capture the signed Actual API net worth and UI net worth in the configured base currency at `t`.
- P6: Record the configured transient-read retry bound before starting. An absent bound blocks the journey.
- P7: Fingerprint the checkout, provider captures, API and UI captures, cursor and ledger state, and source artifacts before S1.
- P8: Redact credentials, tokens, account numbers, and transaction content from retained evidence.

## Steps
### S1 -- Read provider balance {#S1}
- **Do:** Read signed FAB cash and signed Sarwa quantities and prices at `t`. Record their account ids and native currencies.
- **Expect:** Every provider account id maps to exactly one Actual account owned by the same provider.
- **Expect:** The evidence identifies each component's sign, native currency, value, and timestamp.
- **Expect (negative):** Missing, duplicate, unknown, or cross-provider mappings block parity. The validator does not infer a mapping.
- **Expect (negative):** Missing components or prices and unsupported currencies block parity.
- **Expect (negative):** Non-finite values and provider-disallowed negative quantities block parity. The validator does not zero-fill or omit them.
- **Expect (negative):** Reading provider evidence changes no provider record, source artifact, checkout file, cursor, or ledger entry.
### S2 -- Read API balance {#S2}
- **Do:** Read the signed Actual API net worth, base currency, account identities, and valuation timestamp.
- **Expect:** The API value and every mapped Actual account are explicit and use valuation instant `t`.
- **Expect (negative):** Missing accounts, identity mismatches, pagination errors, or a timestamp other than `t` block parity.
- **Expect (negative):** Reading the API advances no cursor and changes no API, provider, ledger, or source state.
### S3 -- Read UI balance {#S3}
- **Do:** Read the signed UI net worth, selected account scope, base currency, and valuation timestamp.
- **Expect:** The UI shows that its mapped account scope, base currency, and valuation instant `t` match the API evidence.
- **Expect (negative):** A stale view, different account scope, different currency, or different timestamp blocks parity.
- **Expect (negative):** Reading the UI triggers no repair, refresh write, sync, cursor advancement, or ledger change.
### S4 -- Compare balances {#S4}
- **Do:** Verify each non-base component uses its captured FX pair, rate, timestamp, source, and evidence hash at `t`.
- **Expect:** The validator rejects live FX, inferred rates, provider-native USD substitutes, and FX snapshots from another timestamp.
- **Do:** Compute `expected_base(t) = FAB_cash_native(t) * FX(FAB_native->base,t) + sum_sarwa(quantity_native(t) * price_native(t) * FX(asset_currency->base,t))`, preserving every sign.
- **Expect:** Component arithmetic retains full decimal or rational precision.
- **Expect:** The validator rounds only the final expected total and each observed API/UI total to two decimal places.
- **Do:** Compare each rounded observed total with the rounded expected total.
- **Expect:** When each absolute difference is at most `0.01` base-currency unit, parity passes. Otherwise, retain the signed totals and difference.
- **Expect (negative):** A missing, stale, mismatched, unsupported, or non-finite input produces a blocked parity result, never PASS.
- **Expect (negative):** Comparison performs no repair, write, sync, cursor advancement, or mutation of captured evidence.
### S5 -- Bound transient reread {#S5}
- **Do:** After a transient read failure, re-read only within the configured bound from P6 and retain every attempt's timestamp.
- **Expect:** A successful reread uses the same account scope, valuation instant `t`, and captured FX snapshots.
- **Expect:** Exhausting the configured bound produces a blocked result with the attempts retained.
- **Expect (negative):** A reread never substitutes current FX, changes `t`, mutates a source, or continues past the configured bound.
- **Do:** After the final read, recompute the P7 fingerprints.
- **Expect:** Pre-read and post-read fingerprints match for the checkout, provider captures, API and UI captures, cursor and ledger state, and source artifacts.

## Success criteria
- SC1: S1 proves a one-to-one, same-provider mapping for every FAB cash and Sarwa holding account, with no missing input.
- SC2: S2-S3 prove that API and UI observations use the mapped account scope, configured base currency, and valuation instant `t`.
- SC3: S4 applies the recorded signed equation, immutable as-of FX evidence, final-only two-decimal rounding, and `0.01` tolerance.
- SC4: S4 reports PASS only when both API and UI differences are at most `0.01`. Every invalid or incomplete input produces a blocked result.
- SC5: S1-S5 and matching P7 fingerprints prove that no checkout, provider, API, UI, cursor, ledger, capture, or source artifact changed.
- SC6: S5 stops at the configured retry bound and retains timestamped evidence for every attempt.

## Evidence
- E1: Redacted provider component table with provider account id, mapped Actual account id, sign, native currency, value, and timestamp `t`.
- E2: Redacted API and UI captures with signed net worth, mapped account scope, base currency, and timestamp `t`.
- E3: Immutable FX records with pair, rate, timestamp, source, and evidence hash.
- E4: Calculation record containing every full-precision component, the signed expected total, both signed observed totals, rounded totals, differences, and result.
- E5: Retry-bound record, timestamped attempt log, and P7 pre-read and post-read fingerprints.

## Known gaps
- G1: Live FAB, Sarwa, Actual API, and Actual UI evidence is unavailable, so this journey remains draft.
- G2: The journey cannot pass when a required contract input is unavailable. Required inputs are the retry bound, account mapping, valuation instant, FX snapshot, component value, and provider quantity rule.
- G3: Source references are `finance_tracker/actual_pipeline.py`, `finance_tracker/actual_snapshot.py`, and provider integration, API, and UI surfaces.

## Delta log
- No behavior delta. This correction records the executable parity contract.
