# Complete ledger projection

The monthly statement pipeline predicts all canonical FULL_LEDGER stages before
projecting trusted Actual category/payee IDs. Its separately compiled
FULL_LEDGER_PROJECTION snapshot retains every rule's original execution owner.
The existing disjoint N8N and Actual exports are unchanged; no native Actual rule
is installed, removed or reassigned by this change.

Previously the packaged projection omitted seven Actual-owned classifications.
For example UNRWA and iSTYLE had no projected category. An installed native rule
could then classify the imported row and cause exact verification to reject the
unexpected category after persistence. Without that native rule, the category
remained missing. Category-dependent later stages also lacked the classification.

The projection-only evaluator admits Actual-owned rules only for non-overwriting
category/subcategory classification. The ordinary runtime validator still rejects
Actual-owned rules. Manual locks, exact economic readback, replay exclusion and
deletion handling remain enforced. An Actual set-if-empty rule has no effect on
an already classified projected row.

Regression evidence includes UNRWA/iSTYLE classification, category-dependent
tagging and locked categories. The native Actual SQLite integration installs and
proves an active classification rule with a control import, then verifies a
projected statement row, changes its manual fields and replays without another
row or balance mutation. These are disposable tests, not production receipts.

Deployment requires rebuilding the matching finance custom-node image. Existing
monthly activation gates and current-budget mapping/readback checks still apply.
