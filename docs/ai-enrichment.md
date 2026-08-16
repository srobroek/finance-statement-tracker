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

Each proposal produces an `AITrace` recording the policy, field, value, confidence, acceptance decision, reason, rationale, and source references. The trace is appended to transaction metadata for auditability.

The engine is provider-neutral. In the current deployment, the end-of-day and
card-specific `gpt-5.6-sol` Codex tasks implement the resolver after all static
rule stages and history matching. The Actual ingestion worker returns
constrained `ai_requests`; a task submits proposal JSON on a second idempotent
stage call, and the container validates it before regenerating the manifest.
Codex is not available inside the container, so neither continuous service
assumes a local Codex runtime. A future OpenAI-compatible API worker could
replace the Codex policy stage without changing the policy or validation layer.
