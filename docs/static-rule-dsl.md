# Static rule DSL

Static rules are ordered AutoCat-style records. A rule owns its conditions and actions; conditions and actions are not independent engines. The normalized JSON form is validated by `config/static-rule-schema-v1.json` and executed by `finance_tracker.rules`.

## Evaluation

1. Stages run in this order: transaction normalization, vendor normalization, classification, tagging, evidence, cashback.
2. Lower priority numbers run first within a stage.
3. A rule matches when any condition group matches.
4. A condition group matches when every condition in the group matches.
5. `stop_on_match` skips only later rules in the same stage.
6. Manual field locks always win. `set_if_empty` also preserves an existing value.

```json
{
  "schema_version": 1,
  "rule_id": "class-groceries",
  "name": "Known supermarkets",
  "stage": "CLASSIFICATION",
  "priority": 30,
  "match": {
    "any": [
      {"all": [{"field": "vendor", "operator": "in", "value": ["Carrefour", "Farzana Trading", "GMG Consumer", "Instashop", "Kibsons", "LuLu Hypermarket", "Spinneys", "Urban Foods", "Waitrose"]}]}
    ]
  },
  "actions": [
    {"action": "set_if_empty", "field": "category", "value": "Groceries"},
    {"action": "add_tags", "value": ["grocery", "shared"], "sequence": 20}
  ],
  "stop_on_match": true
}
```

## Condition fields

- Source facts: `transaction_at`, `card`, `account`, `institution`, `account_last4`, `merchant_raw`, `amount_aed`, `amount_original`, `currency`, `channel`, `source_type`, `reference`, `mcc`
- Derived fields: `spend_aed`, `vendor`, `category`, `subcategory`, `transaction_type`, `reward_bucket`, `tags`, `owner`, `property_code`, `rental_unit`
- Workflow fields: `evidence_policy`, `evidence_status`, `review_required`, `is_refund`, `is_foreign`, `is_subscription`

## Operators

- Text and lists: `equals`, `not_equals`, `contains`, `contains_any`, `not_contains`, `starts_with`, `ends_with`, `regex`, `in`, `not_in`
- Amounts: `numeric_equals`, `gt`, `gte`, `lt`, `lte`, `between`, `polarity`
- Dates: `date_on`, `date_before`, `date_after`, `date_between`
- State: `is_empty`, `not_empty`, `is_true`, `is_false`, `has_tag`

All text comparisons are case-insensitive by default. Set `case_sensitive` only when required. `between` and `date_between` are inclusive.

## Actions

- `set`: set a writable derived field unless manually locked.
- `set_if_empty`: set only when the target is blank.
- `add_tag`, `add_tags`, `remove_tag`: mutate reporting tags.
- `request_evidence`: set the evidence policy and mark evidence requested.
- `require_review`: send the transaction to review.

Static rules cannot write transaction amounts, source IDs, deduplication keys, reconciliation state, or other protected facts. AI enrichment runs only after these rules and has a narrower permission set.

The starter library is `config/static-rules.seed.json`. It is a new taxonomy inspired by the previous Tiller and legacy worker behavior; it is not a direct import of either ruleset.

## Actual-native compilation

`config/actual-bootstrap.json` selects an explicit subset of canonical rule IDs for native Actual compilation. The compiler maps imported description, payee, account, and category conditions plus payee/category/tag actions only when Actual can preserve the semantics. It refuses unsupported stages, actions, negation, case-sensitive behavior, and OR-of-AND groups. `set_if_empty` becomes an explicit empty-field guard. The bootstrap result reports compiled and skipped rules, and a second run must be clean.

Actual then runs the compiled subset in three stages:

1. `pre`: vendor-normalization rules inspect `imported_payee` and set the clean Actual payee. This is the native equivalent of the first AutoCat pass.
2. `default`: classification rules inspect that normalized payee and set category/tags. Actual represents this stage as `null` through its API.
3. `post`: final tag or enforcement rules run after all default rules. Use this only for intentional final overrides, not ordinary categorization.

Actual automatically ranks broad matches before more-specific matches inside each stage, and the last matching action wins. Canonical numeric priorities and `stop_on_match` remain deterministic-worker features; a rule is compiled to Actual only when it does not depend on those features for correctness.

This keeps Actual useful for manual/native imports without weakening the deterministic worker's ordering, manual-lock, evidence, trace, AI, or cashback guarantees.
