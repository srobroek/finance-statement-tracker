# Cashback companion validation report

Date: 2026-08-17

Test target: isolated scenario container at `http://172.20.10.20:5011/`

Production target: `http://172.20.10.20:5010/` — not changed or restarted

Overall result: **PASS**

## Executive result

- Python regression suite: **120/120 passed** in 1.622 seconds.
- Focused routing matrix: **8/8 passed**.
- Live isolated API and browser scenarios: **13/13 passed**.
- JavaScript syntax, Python compilation, JSON parsing, whitespace, and live health checks: **8/8 passed**.
- No statement period was finalized with synthetic notification data.
- The isolated feed ended healthy and fresh with 9 provisional events, 1 durable correction, RAK total AED 10,300, and SC total AED 15,200 at tier 10.

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
| Card positions | PASS | RAK, SC, and EI cards showed totals, cycle/tier state, every bucket fill level, provisional/confirmed counts, and refunds. |
| SC tier ladder | PASS | SC displayed 3% at AED 2.5k, 5% at AED 7.5k, and 10% at AED 15k instead of a single unexplained AED 15k target. |
| Previous-period state | PASS | With no evidence-finalized cycles, the UI correctly showed no finalized cycles rather than inventing history. |
| Mobile-first CSS structure | PASS | Base styles use a single-column card grid and compact routing table; wider multi-column layouts activate only at 600px and 980px breakpoints. The narrowest breakpoint further compresses routing columns. |

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

Result: **120 tests passed; 0 failed; 0 errors; 0 skipped.**

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
4. The live scenario uses synthetic normalized events. Each real sender/subject/body format still needs a captured fixture and parser test.
5. Notification totals can differ from statements because of reversals, FX settlement, tips, fees, and delayed postings. The UI correctly labels the live ledger provisional.
6. No synthetic statement was used to create fake finalized history. Finalized-period UI requires a real or purpose-built disposable statement-evidence fixture.
7. Mobile behaviour was validated from mobile-first CSS structure and semantic browser rendering. A final device-width screenshot should be captured on the deployed public hostname after production deployment.

## Deployment recommendation

The isolated POC passed user review. Promotion should use the tested GHCR image and preserve the existing cashback-data volume. Card-programme assumptions remain tentative until issuer terms are verified.
