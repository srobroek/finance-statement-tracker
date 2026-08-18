# AI enrichment

AI enrichment is a separate stage after deterministic rules. Policies live in `config/ai-policies.json`; enforcement lives in `finance_tracker.ai_rules`.

The model receives a constrained request containing source facts, existing derived values, unresolved target fields, allowed values, allowed tags, and a response contract. It returns proposals rather than transaction mutations. The engine validates every proposal before applying it.

AI may propose:

- vendor, category, and subcategory for unresolved transactions;
- approved reporting tags;
- subscription state;
- property or rental-unit metadata when explicit evidence supports it;
- an evidence-search policy for the evidence worker.

AI cannot change transaction IDs, dates, amounts, currency, card/account identity, raw merchant text, source IDs, transaction type, reconciliation state, or cashback arithmetic. It may fill an unknown channel or reward bucket only when the scoped policy allows the value and explicit evidence supports it; an already populated or locked value is never offered to the model. Category and tag proposals can be restricted to configured values. Proposals below the policy confidence threshold are rejected and sent to review.

When no configured category fits after static and history matching, AI may emit
a `category_recommendation` object. This never creates or assigns a category.
It adds the semantic `#category-review` marker and keeps the transaction in the
review gate until an owner accepts a category design and reruns classification.

Each proposal produces an `AITrace` recording the policy, field, value, confidence, acceptance decision, reason, rationale, and source references. The trace is appended to transaction metadata for auditability.

The engine is provider-neutral. In the current deployment, the card-specific
monthly `gpt-5.6-sol` Codex tasks implement the resolver after all static rule
stages and history matching. The Actual ingestion worker returns
constrained `ai_requests`; a task submits proposal JSON on a second idempotent
stage call, and the container validates it before regenerating the manifest.
Codex is not available inside the container, so neither continuous service
assumes a local Codex runtime. A future OpenAI-compatible API worker could
replace the Codex policy stage without changing the policy or validation layer.

Policies also declare `trigger_fields`. A policy runs only when at least one
trigger remains unresolved; optional companion fields such as reporting tags
may be included in that request but cannot trigger a model call by themselves.
Conditions further restrict subscription, purchase-evidence, property, and
cashback policies to relevant transactions and cards.

The job API returns a compact `ai_handoff`: shared policy definitions live in
`policies`, deduplicated transaction snapshots live in `transactions`, and each
request identifies its exact snapshot with `transaction_ref` alongside
`transaction_id`, `policy_id`, and unresolved `allowed_fields`. If an accepted
proposal changes the context seen by a later policy, both context variants are
retained rather than collapsed. This preserves every validation boundary while
avoiding repeated instructions and allowlists in scheduled-task context.
