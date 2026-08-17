# Cashback companion validation report

Date: 2026-08-17

Test target: isolated scenario container at `http://172.20.10.20:5011/`

Production targets: `https://cashback.vxsan.com/`, `https://actual.vxsan.com/`, and the host-local ingestion API

Overall result: **PASS**

## Executive result

- Python regression suite: **167/167 passed** in the final run.
- Actual bridge JavaScript suite: **10/10 passed**, including the offline idempotency integration.
- Focused routing matrix: **8/8 passed**.
- Live isolated API and browser scenarios: **13/13 passed**.
- JavaScript syntax, Python compilation, JSON parsing, whitespace, and live health checks: **8/8 passed**.
- No statement period was finalized with synthetic notification data.
- The isolated feed ended healthy and fresh with 9 provisional events, 1 durable correction, RAK total AED 10,300, and SC total AED 15,200 at tier 10.
- Production UI labels render as `RAK World`, `SC Platinum X`, and `EI Amazon` without clipping at 430 px or 370 px widths.
- The exact Outlook Emirates Islamic statements for the June and July periods passed parsing, balance checks, constrained Sol enrichment, Actual preflight, commit verification, and idempotent replay.
- A quiesced production backup completed with a valid checksum and contained Actual, cashback, ingestion, and secret-free configuration payloads; all three application health endpoints returned HTTP 200 afterward.
- The production dashboard rebuilds its time-sensitive state every 60 seconds without requiring an ingest event.
- A production-equivalent stale-feed push reached both registered endpoints, and the host watchdog recovered a deliberately stopped cashback container without disturbing Actual or ingestion.
- The 18:05 Dubai hourly run committed its cursor exactly to `2026-08-17T14:07:37.571291Z`, scanned 4 RAKBANK messages, accepted 3 provisional purchases, and inserted the new AED 6.00 GMG purchase.

## Production pipeline validation

| Test | Result | Observed result |
|---|:---:|---|
| Latest-message selection | PASS | Outlook search selected the newest Emirates Islamic statement message received 2026-08-01, not the older 2026-07-01 message. |
| Exact evidence identity | PASS | The July-period statement used its exact Outlook message ID and attachment ID. Its SHA-256 matched the archived OneDrive statement (`738cee40…`) byte-for-byte, and the catalogue now links the Outlook evidence. |
| Bank-neutral parse | PASS | The latest statement normalized 7 rows for card suffix 0082, period 2026-07-01 through 2026-07-31, statement date 2026-07-31, and due date 2026-08-25. |
| Statement arithmetic | PASS | Opening AED 1,043.29 and closing AED 285.70 tied with AED 0.00 difference and no warnings. |
| Static plus AI classification | PASS | Static rules resolved Amazon/vendor/category/bucket fields; Sol answered only 4 explicit online-channel gaps. All 4 proposals met the 0.95 policy threshold and none were rejected. |
| Actual preflight | PASS | Actual reported no errors. All 7 imported IDs already existed, so the preflight proposed no additions or updates. |
| Actual commit verification | PASS | The guarded commit verified 7/7 expected imported IDs and reported 0 duplicates. The payment reminder for 2026-08-25 and AED 285.70 was unchanged because it already existed. |
| Durable replay | PASS | Repeating the identical COMMIT returned the same job ID with `idempotent_replay=true`; Actual was not written again. |
| Older real statement | PASS | The June-period statement tied at AED 0.00; 15/15 scoped channel proposals were accepted; 18 transactions were committed and verified with 0 duplicates. Its already-past 2026-07-25 reminder was safely skipped. |
| Placeholder fail-closed | PASS | Outlook contains no recurring RAKBANK or Standard Chartered monthly statement yet. RAKBANK has only a key-facts document, so both statement adapters remain non-importing placeholders. |
| Production backup | PASS | Backup `20260817T131913Z` passed `sha256sum -c`; the archive includes `actual-data`, `cashback-data`, `ingestion-data`, and `configuration`. The timer is enabled and active. |
| Container-side refresh | PASS | Production dashboard `generated_at` advanced by exactly 60 seconds with no ingest or user mutation, proving periodic pace, close-window, and stale-feed recalculation. |
| Stale-feed push | PASS | An isolated copy of production state triggered the native stale-feed candidate and sent it to both registered production push endpoints; production state was not modified. |
| Host watchdog recovery | PASS | The cashback container was deliberately stopped; the five-minute watchdog detected the failed health probe, restarted only that service, and verified recovery in 2 seconds. Actual and ingestion remained HTTP 200. |
| Latest hourly ingestion | PASS | Durable production state records cursor `2026-08-17T14:07:37.571291+00:00`, 4 scanned messages, and 3 accepted provisional transactions. Actual was not written and no period was finalized. |
| Live source identity | PASS | SHA-256 hashes for `app.js`, `styles.css`, `server.py`, and `web_push.py` match byte-for-byte between the repository and the running production container. |

## Live isolated scenario results

| # | Test | Result | Observed result |
|---:|---|:---:|---|
| 1 | RAK category buckets at/near cap | PASS | Grocery and dining reached AED 3,000; travel reached AED 3,950. Full/near-full alerts appeared. |
| 2 | SC online and wallet caps | PASS | Online reached AED 4,000 and wallet reached AED 2,000; routing moved to eligible alternatives. |
| 3 | SC target secured | PASS | At AED 15,300, SC showed tier 10 secured and the zero-reward tier-filler route disappeared while eligible open reward buckets remained available. |
| 4 | All card targets secured | PASS | RAK reached AED 10,300 and SC AED 15,300; filler intent became inactive with zero candidates. |
| 5 | Refund reopens headroom | PASS | AED 100 SC wallet refund reduced SC to AED 15,200, reopened wallet to AED 1,900/AED 2,000, and made wallet eligible again. |
| 6 | Routing after refund | PASS | Grocery, dining, travel, Apple Pay, and filler recommendations selected SC wallet for the remaining AED 100 of headroom. |
| 7 | Same source-event replay | PASS | Replay reported 0 inserted, 1 updated, 0 duplicates; event count and totals did not increase. |
| 8 | Different source ID with same normalized identity | PASS | Reported 0 inserted, 0 updated, 1 duplicate; event count remained 9. |
| 9 | Manual correction | PASS | First correction applied with `idempotent_replay=false`; SC online classification and evidence trace were stored. |
| 10 | Correction replay | PASS | Identical correction returned `idempotent_replay=true`; correction count remained 1. |
| 11 | Empty successful scan heartbeat | PASS | A zero-message heartbeat made the feed fresh, stored 0 scanned/0 accepted and the validation cursor, without changing transaction totals. |
| 12 | Unsafe period finalization guard | PASS | Finalization without statement evidence returned HTTP 400: statement reference, evidence reference, and document URL are required. |
| 13 | Live service state | PASS | `/api/health` returned `ok`; dashboard reported 9 events, all provisional, and `is_stale=false`. |

## Interactive browser results

| Test | Result | Observed result |
|---|:---:|---|
| Compact routing list | PASS | All nine spend intents rendered in a dense three-column list with recommendation, reward/threshold context, and avoid card visible without disclosure controls. |
| Dynamic decision tree | PASS | Grocery rendered four ranked routes with method, bucket fill/headroom, card threshold, reward value, and strategy reasoning. |
| Whole-purchase headroom | PASS | A capped route is considered preferred only when its remaining bucket headroom can fit the representative purchase. |
| Alert acknowledgement | PASS | Hiding the RAK grocery alert moved it to “1 hidden alert.” Reloading preserved the acknowledgement. |
| Alert restoration | PASS | Re-enabling the hidden alert restored it to the visible Needs attention list. |
| Transaction review action | PASS | A review-required state renders a `Review` action and modal queue with one approval control per event. Approval clears only `review_required`; it does not change the provisional status or claim statement reconciliation. |
| Card positions | PASS | RAK, SC, and EI cards showed totals, cycle/tier state, every bucket fill level, provisional/confirmed counts, and refunds. |
| SC tier ladder | PASS | SC displayed 3% at AED 2.5k, 5% at AED 7.5k, and 10% at AED 15k instead of a single unexplained AED 15k target. |
| Previous-period state | PASS | With no evidence-finalized cycles, the UI correctly showed no finalized cycles rather than inventing history. |
| Mobile-first CSS structure | PASS | Base styles use a single-column card grid and compact routing table; wider multi-column layouts activate only at 600px and 980px breakpoints. The narrowest breakpoint further compresses routing columns. |
| Narrow label fit | PASS | At a live 363 px viewport, `RAK World`, `SC Platinum X`, and `EI Amazon` all fit their routing cells; no label or page-level horizontal overflow was present. |

## Focused routing matrix

Every test below passed:

1. `test_empty_cycle_defaults_preserve_card_roles` — preserves default portfolio roles before spend accumulates.
2. `test_rak_over_and_sc_under_moves_discretionary_spend_to_sc` — moves discretionary spend away from RAK when RAK is over pace and SC still needs tier spend.
3. `test_sc_online_full_rolls_to_wallet_filler_and_amazon_specialist` — uses wallet or filler after online cap and keeps Amazon on EI when appropriate.
4. `test_sc_reward_buckets_full_roll_to_tier_filler` — uses an explicit zero-reward SC filler lane when reward buckets are unavailable and the SC tier target remains unmet.
5. `test_sc_target_secured_removes_filler_but_keeps_open_reward_buckets` — removes filler after the SC target while retaining economically useful open buckets.
6. `test_all_targets_secured_disables_filler_profile` — disables filler when all target thresholds are secured.
7. `test_every_preferred_capped_bucket_fits_the_decision_amount` — prevents recommendations that cannot fit the full representative purchase.
8. `test_avoid_cards_never_include_the_preferred_card` — keeps the preferred card out of the avoid list.

## Full automated regression suite

Command:

```powershell
python -m unittest discover -s tests -v
```

Result: **167 tests passed; 0 failed; 0 errors; 0 skipped.**

Final module totals:

| Module | Passed | Module | Passed |
|---|---:|---|---:|
| `test_actual_pipeline` | 14 | `test_ai_rules` | 7 |
| `test_browser_cli` | 2 | `test_browser_exports` | 8 |
| `test_browser_ingestion` | 9 | `test_browser_recipes` | 5 |
| `test_cashback_events` | 17 | `test_cashback_profiles` | 10 |
| `test_cashback_routing_matrix` | 8 | `test_cashback_server` | 1 |
| `test_cashback` | 9 | `test_cli` | 1 |
| `test_deployment_scripts` | 4 | `test_evidence` | 5 |
| `test_history` | 2 | `test_ingestion_jobs` | 7 |
| `test_ingestion` | 2 | `test_mail_ingestion` | 4 |
| `test_notification_sources` | 3 | `test_notifications` | 8 |
| `test_platforms` | 4 | `test_reporting` | 3 |
| `test_reports` | 2 | `test_rule_seed` | 7 |
| `test_rules` | 9 | `test_savings` | 2 |
| `test_statement_sources` | 3 | `test_statements` | 4 |
| `test_subscriptions` | 2 | `test_web_push` | 5 |

The selected per-test inventory below is the earlier scenario-run snapshot retained for traceability. The final module table above is the authoritative inventory for the 167-test run and includes the newer profile, ingestion-job, mail, source-registry, deployment-monitor, and web-push coverage.

### `test_actual_pipeline` — 13/13 PASS

- PASS `test_account_map_rejects_duplicate_suffixes`
- PASS `test_builds_auditable_actual_manifest`
- PASS `test_filler_route_is_inactive_after_all_card_thresholds_are_met`
- PASS `test_grocery_graph_considers_channels_and_reorders_after_rak_cap`
- PASS `test_grocery_graph_prioritizes_under_pace_sc_over_rak_tier_unlock`
- PASS `test_grocery_graph_returns_to_rak_after_sc_target_is_secured`
- PASS `test_grocery_graph_uses_sc_filler_when_reward_buckets_are_full`
- PASS `test_late_cycle_does_not_assume_an_unreachable_target_tier`
- PASS `test_loads_canonical_rule_json`
- PASS `test_rejects_unmapped_card_suffix`
- PASS `test_sc_online_cap_routes_wallet_amazon_and_filler_to_open_buckets`
- PASS `test_snapshot_drives_cashback_without_a_second_ledger`
- PASS `test_snapshot_treats_tagged_card_payment_as_transfer`

### `test_ai_rules` — 7/7 PASS

- PASS `test_ai_accepts_allowed_unresolved_category_and_tag`
- PASS `test_ai_can_enrich_unresolved_channel_and_bucket_without_reward_math`
- PASS `test_ai_cannot_modify_protected_facts`
- PASS `test_ai_does_not_overwrite_static_or_manual_category`
- PASS `test_human_correction_is_recorded_and_locked`
- PASS `test_low_confidence_proposal_is_rejected_for_review`
- PASS `test_openai_compatible_resolver_uses_runtime_secret_and_validates_json`

### `test_browser_cli` — 2/2 PASS

- PASS `test_official_export_command_builds_capture_for_configured_account`
- PASS `test_status_and_recipe_commands_write_machine_readable_results`

### `test_browser_exports` — 8/8 PASS

- PASS `test_adcb_multicard_csv_preserves_card_identity`
- PASS `test_adcb_rejects_a_malformed_candidate_instead_of_partial_import`
- PASS `test_email_pdf_routes_to_statement_pipeline`
- PASS `test_emirates_islamic_xlsx_rejects_an_unparsed_candidate`
- PASS `test_emirates_islamic_xlsx_retains_pending_status_for_review`
- PASS `test_fab_csv_preserves_debit_credit_and_account_last4`
- PASS `test_foreign_export_without_aed_equivalent_is_rejected`
- PASS `test_generic_csv_detects_common_columns`

### `test_browser_ingestion` — 9/9 PASS

- PASS `test_account_snapshot_never_creates_a_balance_transaction`
- PASS `test_duplicate_portal_source_ids_are_rejected`
- PASS `test_export_writes_portable_actual_handoff`
- PASS `test_sensitive_browser_state_is_rejected`
- PASS `test_source_url_rejects_embedded_credentials`
- PASS `test_statement_pdf_routes_to_existing_statement_pipeline`
- PASS `test_tied_official_statement_rows_are_authoritative_and_cleared`
- PASS `test_unbalanced_statement_rows_are_blocked_from_partial_import`
- PASS `test_visible_rows_are_stable_reviewable_and_do_not_keep_url_secrets`

### `test_browser_recipes` — 5/5 PASS

- PASS `test_account_last4_must_be_exactly_four_digits`
- PASS `test_migrated_registry_is_valid`
- PASS `test_recipe_rendering_rejects_missing_and_secret_parameters`
- PASS `test_recipe_rendering_substitutes_only_declared_parameters`
- PASS `test_source_coverage_includes_accounts_and_supplemental_sources`

### `test_cashback` — 7/7 PASS

- PASS `test_amazon_overflow_returns_to_ei_after_sc_online_is_full`
- PASS `test_empty_period_routes_match_portfolio_strategy`
- PASS `test_pace_status`
- PASS `test_program_version_is_selected_by_period_without_back_application`
- PASS `test_refund_reduces_reward`
- PASS `test_sc_tier_jump_values_existing_spend`
- PASS `test_statement_period_supports_card_specific_close_day`

### `test_cashback_events` — 14/14 PASS

- PASS `test_ai_correction_endpoint_rejects_protected_facts`
- PASS `test_alert_acknowledgements_are_durable_and_reversible`
- PASS `test_correction_is_idempotent_and_recalculates_bucket`
- PASS `test_different_source_ids_with_same_normalized_identity_are_deduplicated`
- PASS `test_events_are_idempotent_and_drive_live_bucket`
- PASS `test_finalization_opens_the_next_configured_card_cycle`
- PASS `test_ingest_heartbeat_controls_feed_freshness_even_when_scan_is_empty`
- PASS `test_low_confidence_event_requires_review`
- PASS `test_period_finalization_requires_statement_evidence_and_verified_actual_import`
- PASS `test_period_with_variances_requires_explicit_acknowledgement`
- PASS `test_refund_reduces_live_bucket_and_ignored_event_does_not_count`
- PASS `test_reversal_requires_reference_and_reduces_spend`
- PASS `test_statement_reconciliation_replaces_provisional_variances_with_authoritative_rows`
- PASS `test_third_week_and_near_full_bucket_alerts_are_calculated`

### `test_cashback_routing_matrix` — 8/8 PASS

- PASS `test_all_targets_secured_disables_filler_profile`
- PASS `test_avoid_cards_never_include_the_preferred_card`
- PASS `test_empty_cycle_defaults_preserve_card_roles`
- PASS `test_every_preferred_capped_bucket_fits_the_decision_amount`
- PASS `test_rak_over_and_sc_under_moves_discretionary_spend_to_sc`
- PASS `test_sc_online_full_rolls_to_wallet_filler_and_amazon_specialist`
- PASS `test_sc_reward_buckets_full_roll_to_tier_filler`
- PASS `test_sc_target_secured_removes_filler_but_keeps_open_reward_buckets`

### `test_cashback_server` — 1/1 PASS

- PASS `test_health_and_ingest_authorization`

### `test_cli` — 1/1 PASS

- PASS `test_actual_export_writes_bridge_envelopes`

### `test_evidence` — 5/5 PASS

- PASS `test_archive_is_structured_hashed_and_idempotent`
- PASS `test_explicit_currency_mismatch_is_rejected`
- PASS `test_low_confidence_candidate_is_not_linked`
- PASS `test_statement_catalogue_uses_card_period_as_its_entity`
- PASS `test_utility_pdf_matches_and_gets_structured_path`

### `test_history` — 2/2 PASS

- PASS `test_ambiguous_history_does_not_select_a_category`
- PASS `test_consistent_reviewed_history_enriches_only_unresolved_fields`

### `test_ingestion` — 2/2 PASS

- PASS `test_balanced_statement_stages_without_claiming_reconciliation`
- PASS `test_unknown_card_requires_review`

### `test_notifications` — 3/3 PASS

- PASS `test_adcb_authorization_is_provisional_traceable_and_classified`
- PASS `test_cli_batch_shape_is_json_serializable`
- PASS `test_foreign_authorization_without_aed_equivalent_is_not_ingested`

### `test_platforms` — 4/4 PASS

- PASS `test_groups_transactions_by_account_and_uses_imported_id`
- PASS `test_requires_an_account_mapping`
- PASS `test_statement_rows_are_cleared_and_tags_are_actual_safe`
- PASS `test_tied_browser_statement_rows_are_cleared_but_portal_rows_are_not`

### `test_reporting` — 3/3 PASS

- PASS `test_refunds_reduce_net_but_not_positive_spend`
- PASS `test_shared_owner_report_reuses_canonical_transactions`
- PASS `test_tag_filters_support_any_all_and_none_case_insensitively`

### `test_reports` — 2/2 PASS

- PASS `test_month_close_contains_static_mermaid_and_table`
- PASS `test_month_close_waits_for_period_end_and_statements`

### `test_rule_seed` — 7/7 PASS

- PASS `test_amazon_purchase_gets_ei_bucket_and_receipt_search`
- PASS `test_live_cashback_rule_set_is_small_and_contains_all_bucket_rules`
- PASS `test_manual_category_lock_survives_seed_rules`
- PASS `test_sc_physical_spend_is_tracked_as_tier_filler`
- PASS `test_sc_wallet_rule_is_distinct_from_online`
- PASS `test_seed_is_broad_and_every_rule_validates`
- PASS `test_utility_rule_normalizes_classifies_tags_and_requests_evidence`

### `test_rules` — 9/9 PASS

- PASS `test_between_requires_upper_bound`
- PASS `test_date_range_operator`
- PASS `test_invalid_field_is_rejected_before_evaluation`
- PASS `test_manual_locked_field_wins`
- PASS `test_or_groups_of_and_conditions`
- PASS `test_set_if_empty_and_multi_tag_actions_preserve_existing_classification`
- PASS `test_static_rule_cannot_overwrite_protected_amount`
- PASS `test_stop_on_match_only_stops_same_stage`
- PASS `test_tiller_style_account_amount_and_institution_conditions`

### `test_savings` — 2/2 PASS

- PASS `test_negative_reserve_is_rejected`
- PASS `test_reserved_goals_reduce_safe_cash_without_moving_money`

### `test_statements` — 4/4 PASS

- PASS `test_adcb_statement_parses_card_sections_and_foreign_currency`
- PASS `test_emirates_islamic_statement_reconciles`
- PASS `test_registry_is_the_bank_extension_boundary`
- PASS `test_wio_credit_statement_parses_signed_transactions_and_account_suffixes`

### `test_subscriptions` — 2/2 PASS

- PASS `test_monthly_non_utility_subscription_is_detected`
- PASS `test_utilities_are_not_promoted_to_subscriptions`

## Additional quality checks

| Check | Result |
|---|:---:|
| `node --check apps/cashback-control/web/app.js` | PASS |
| `python -m compileall -q finance_tracker apps/cashback-control tests` | PASS |
| `git diff --check` | PASS; only Git line-ending conversion warnings were emitted |
| Parse `config/cashback-programs.json` | PASS |
| Parse `config/ai-policies.json` | PASS |
| Parse `config/static-rules.seed.json` | PASS |
| Isolated `/api/health` | PASS (`ok`) |
| Isolated dashboard rebuild after all scenarios | PASS |
| Linux CI reproduction on the deployment host | PASS: 167 Python tests, 10 JavaScript tests, Actual offline integration, cashback image build, and four fictional programme profiles |
| Production service watchdog | PASS: unhealthy cashback service recovered and verified without restarting Actual or ingestion |

## Continuous delivery status

Commit `5e0dc45` was pushed to `main`. Both GitHub Actions workflows started during a GitHub-wide incident affecting Actions, API requests, and webhooks and therefore did not publish their images. The exact commit was independently reproduced on the Linux deployment host, then deployed with `--pull never` from the locally built image. Production validation above is therefore tied to the committed source even though registry publication remains pending until GitHub recovers.

This is an external CI availability issue, not a failed application test. Re-run both workflow-dispatch jobs after GitHub reports Actions operational.

## What is verified by this run

- Routing is strategic and dynamic rather than a simple maximum-cashback sort.
- Bucket caps, card thresholds, current pace, decision amount, purchase channel, and configured portfolio order participate in the decision.
- RAK category spend is avoided when the category is capped and SC tier-building is the better allocation.
- SC filler is explicit, earns no direct cashback, contributes to total tier spend, and disappears after the target is secured.
- Refunds and reversals reduce live totals and can reopen bucket headroom.
- Live notifications remain provisional; statement reconciliation is authoritative.
- Finalization requires statement evidence and verified Actual import, and variances require explicit acknowledgement.
- Browser ingestion, PDF/CSV/XLSX staging, Actual handoff, evidence matching, static rules, AI constraints, reporting, subscriptions, and savings regressions remain green.

## Assumptions and remaining production risks

1. Card-programme terms are still tentative POC configuration. Issuer terms must be verified and versioned before production routing is treated as financial advice.
2. SC filler assumes eligible non-reward spend counts toward the monthly tier threshold. Confirm this with the card terms.
3. RAK enhanced reward treatment and any retroactive tier behaviour must be confirmed against the live programme terms.
4. RAKBANK live notification parsing is now proven with real mailbox messages, including overlap replay and a new AED 6.00 GMG purchase. Other real sender/subject/body formats still require captured fixtures and parser tests.
5. Notification totals can differ from statements because of reversals, FX settlement, tips, fees, and delayed postings. The UI correctly labels the live ledger provisional.
6. Real EI statement ingestion and Actual verification are proven, but its July period predates the configured cashback programme's effective date. It was therefore not back-applied or used to invent companion history.
7. The deployed public cashback hostname was visually verified at 430 px and 370 px widths with no routing-label clipping.

## Deployment recommendation

The validated commit is already live and preserves the existing cashback-data volume. Once GitHub Actions recovers, re-run both image workflows and let the normal independent Compose deployments replace the locally reproduced image. Card-programme assumptions remain tentative until issuer terms are verified.
