# Finance platform evaluation

Decision date: 2026-08-16

## Recommendation

Use **Actual Budget as the primary ledger, budgeting, rules, and reporting application**. Keep the deterministic worker for statement PDF parsing, transaction normalization, evidence matching, and bank-specific adapters. Use the dedicated cashback companion for live routing and cycle status. Store evidence originals in OneDrive and its portable JSON catalogue. Store configuration and documentation in Git.

## Platform fit

| Platform | Best fit | Important strengths | Main gaps for this project | Decision |
| --- | --- | --- | --- | --- |
| Actual Budget | Daily ledger, envelope budgeting, deterministic rules, reports | Modern UI, unlimited reports dashboards, schedules, tags, official Node API/CLI/ActualQL, reconciliation-aware imports | No native PDF statement ingestion, evidence vault, or MCP | **Primary POC** |
| Sure | Wealth, AI, document ingestion, agent access | Statement Vault, OpenAI-compatible models, OpenRouter/local models, OAuth MCP, investments and net worth | Younger project; budgeting/rule automation is less mature than Actual | **Second pilot** if PDF/MCP outweigh budgeting maturity |
| Firefly III | Traditional finance database and integrations | Very mature, double entry, budgets, tags, rules, piggy banks, broad REST API | More traditional interface; weaker match to the desired dashboard experience | Fallback for API-first reliability |
| MoneyMatter | Polished all-in-one UI | Strong visual design, budgets, net worth, pivot/cash-flow reports, tags, BYO AI, OAuth MCP | Explicitly early access and a much smaller project; integration contracts need hardening | Watch and test with sample data only |

## PDF ingestion

The evidence layout is OneDrive for durable originals, with a portable `Finance Evidence/catalogue.json` record containing the transaction, source-message, hash, and document metadata. Uploading a file to another application would not replace decryption, extraction, normalization, validation, or reconciliation.

Sure is the only evaluated platform that currently provides a cohesive built-in statement workflow: its Statement Vault retains source files and its MCP `import_bank_statement` tool creates a reviewable import from an already-uploaded PDF.

## AI and Codex boundary

Sure and MoneyMatter can both use OpenAI-compatible endpoints, including OpenRouter or local models. A ChatGPT/Codex subscription is not an API credential: OpenAI documents ChatGPT and API billing separately. Sure's built-in AI therefore needs an OpenAI API key, OpenRouter key, or a local endpoint.

Codex can connect directly to Sure's OAuth MCP endpoint as a client. That lets Codex query and update Sure interactively using the user's Codex allowance, but it does not make Codex a background LLM provider for Sure's own scheduled auto-categorization or PDF pipeline.

## Actual implementation path

1. Run Actual in a disposable Dockge stack on the existing server.
2. Create the real account/category/tag structure manually or from a reviewed export.
3. Export normalized transactions from the worker through `ActualBudgetAdapter`.
4. Import with the official Node API bridge in dry-run mode; verify signs, deduplication, rules, and balances.
5. Recreate AutoCat-inspired rules natively in Actual; retain only unsupported enrichment rules in the worker.
6. Validate reports, schedules, owner tags, rental tags, and net worth.
7. Run live cashback routing in the companion and link its records to OneDrive evidence catalogue entries.

## Actual ecosystem assessment

- `actual-ai` is the most relevant add-on: scheduled AI categorization, OpenRouter/local-provider support, dry-run, merchant lookup, and category suggestions.
- `actual-http-api` is a useful REST wrapper when non-Node consumers require HTTP, but it is community-maintained. Prefer the official Node API for the ingestion write path.
- `actualpy` is mature enough for read/query experiments and Python integrations, but it reimplements the official API; keep it optional rather than foundational.
- `Actuali` is a promising iOS companion with Shortcuts/SMS guidance, offline sync, dashboards, and native transaction entry.
- `actual-helpers` is useful for scheduled bank sync, investments, crypto, and property values.
- `actual_task` demonstrates payee cleanup and Ghostfolio/Wealthfolio synchronization but has a small maintainer footprint.
- `actualbudget-backup` can send scheduled backups to OneDrive through rclone.
- `ha-actualbudget` exposes account and current-budget sensors in Home Assistant but explicitly describes itself as a work in progress.

## Primary sources

- [Actual API](https://actualbudget.org/docs/api/)
- [Actual CLI](https://actualbudget.org/docs/api/cli/)
- [Actual reports](https://actualbudget.org/docs/reports/)
- [Actual rules](https://actualbudget.org/docs/budgeting/rules/)
- [Firefly III](https://github.com/firefly-iii/firefly-iii)
- [Sure LLM support](https://docs.sure.am/llm-support)
- [Sure MCP](https://docs.sure.am/development/mcp)
- [Sure Statement Vault](https://docs.sure.am/guides/app-features/statement-vault)
- [MoneyMatter](https://moneymatter.app/)
- [OpenAI billing separation](https://help.openai.com/en/articles/9039756-managing-billing-settings-on-chatgpt-web-and-platform)
