# n8n package, template, and Data Table audit

Snapshot: 2026-08-19  
Target: n8n 2.36.2, self-hosted, Postgres-backed, all finance workflows inactive and `SPEC_ONLY`  
Method: read-only npm registry metadata, exact npm tarball review, official n8n documentation/integration pages, official template library, package repositories, direct OSV package queries, and a structural scan of all 21 workflow exports. No package was installed and no production workflow or service was changed by the package/template research.

## Decision summary

- Prefer built-in Microsoft Outlook, OneDrive, SharePoint, S3, Textract, Data Table, Postgres, Extract/Convert From File, MCP, HTTP Request, Pushover, and Pushcut nodes.
- Do not install a community Outlook, OneDrive, PDF cloud, OpenAI, MCP, or ETL package in the production finance plane.
- Permit only two isolated pilots after a lockfile/SBOM/0-high/0-critical review: `n8n-nodes-actual@26.8.13` for read-only Actual comparison, and `@open-banking-io/n8n-nodes-open-banking-io@0.2.2` if UAE coverage, consent, residency, and deletion terms pass review.
- Keep statement PDF validation/unlock/extraction in the existing networkless, resource-limited PDF utility and narrow custom nodes. Do not process untrusted financial PDFs in the main n8n process.
- Use the two reviewed, integrity-pinned subscription community nodes only inside the provider-neutral adapter workflow. They remain inactive until exact-image registration, subscription login, no-tool/no-write behavior, and schema-bound receipts are proven.
- Reuse patterns from the official workflow library, not complete templates. The finance pipeline requires content hashes, immutable archive identity, exact readback, two-phase cursors, fenced Actual writes, and no spreadsheet second ledger.

## Built-in baseline

| Capability | Built-in choice | Decision |
|---|---|---|
| Outlook/Exchange | [Microsoft Outlook node](https://docs.n8n.io/integrations/builtin/app-nodes/n8n-nodes-base.microsoftoutlook/) and trigger | Adopt. Use least-privilege delegated Graph credentials and trusted server-side folder/sender/subject contracts. |
| OneDrive/SharePoint | [Microsoft OneDrive](https://docs.n8n.io/integrations/builtin/app-nodes/n8n-nodes-base.microsoftonedrive/) and [Microsoft SharePoint](https://docs.n8n.io/integrations/builtin/app-nodes/n8n-nodes-base.microsoftsharepoint/) | Adopt. Archive by content hash, retain item ID and eTag, download and hash-verify critical artifacts. |
| Object storage | [AWS S3](https://docs.n8n.io/integrations/builtin/app-nodes/n8n-nodes-base.awss3/) | Optional landing alternative. OneDrive remains the current evidence store. |
| OCR | [AWS Textract](https://docs.n8n.io/integrations/builtin/app-nodes/n8n-nodes-base.awstextract/) | Conditional fallback only after data-residency approval. Textract supports receipt/invoice analysis but sends content to AWS. |
| File conversion | [Extract From File](https://docs.n8n.io/integrations/builtin/core-nodes/n8n-nodes-base.extractfromfile/) and [Convert to File](https://docs.n8n.io/integrations/builtin/core-nodes/n8n-nodes-base.converttofile/) | Adopt for trusted structured files and proposal artifacts. PDF decompression/extraction stays sandboxed. |
| Durable state | [Data Tables](https://docs.n8n.io/data/data-tables/) plus Postgres | Adopt. Data Tables hold operational state; fixed Postgres functions retain atomic writer fencing. |
| ETL | Edit Fields, Merge, Aggregate, Compare Datasets, Remove Duplicates, Code, Data Table, Postgres | Adopt. No community ETL package is needed. |
| MCP | MCP Client, MCP Server Trigger, tool subworkflows | Keep the checked-in bounded facade only; instance MCP remains disabled. |
| Unsupported integrations | [HTTP Request](https://docs.n8n.io/integrations/builtin/core-nodes/n8n-nodes-base.httprequest/) | Adopt only for fixed internal endpoints and fixed operations. No caller URL/path/command. |
| Notifications | [Pushover](https://n8n.io/integrations/pushover/) or [Pushcut](https://n8n.io/integrations/pushcut/) | Adopt for redacted operational alerts if required. Cashback declarative web push remains owned by the cashback app. |

## Community package audit

“Verified” below means listed as a verified integration by n8n, not merely published on npm. Download counts are the npm last-month count observed on 2026-08-19. Direct OSV queries found no direct advisory record for the exact package/version entries below. That is not dependency-closure proof: installation remains blocked until an exact lockfile, SBOM, license review, and 0-high/0-critical image scan exist.

| Package | Signals | Access and exposure | Replace custom code? | Decision |
|---|---|---|---|---|
| [`n8n-nodes-outlook-subscription@0.1.49`](https://www.npmjs.com/package/n8n-nodes-outlook-subscription) | Unverified; modified 2026-08-05; 3,362 downloads; one maintainer; MIT; [source](https://github.com/boaz-lai/n8n-nodes-outlook-subscription) | Delegated Graph mail scopes, webhook callback, subscription renewal, shared-mailbox delegation. | No. Built-in polling already supports the frozen cursor-minus-overlap model without public webhook lifecycle risk. | Reject. |
| [`@trg-admin/n8n-nodes-ms-sharepoint@0.1.8`](https://www.npmjs.com/package/@trg-admin/n8n-nodes-ms-sharepoint) | Unverified; modified 2026-08-12; 1,254 downloads; one maintainer; no repository URL exposed in npm metadata. | SharePoint content and tenant credentials enter unverified code. | No; built-in SharePoint/Graph is sufficient. | Reject. |
| [`n8n-nodes-ms-onedrive-business@0.1.17`](https://www.npmjs.com/package/n8n-nodes-ms-onedrive-business) | Unverified; modified 2026-05-20; 255 downloads; one maintainer; MIT; [source](https://github.com/wtyeung/n8n-nodes-ms-onedrive-business) | Requests broad delegated `Files.ReadWrite.All`, `Sites.ReadWrite.All`, `offline_access`, and admin consent; can access drives, SharePoint, shared links, and Excel. | No; built-ins cover the required archive/readback path. | Reject. |
| [`n8n-nodes-pdfco@1.0.13`](https://www.npmjs.com/package/n8n-nodes-pdfco) | [n8n verified](https://n8n.io/integrations/pdfco-api/); modified 2026-07-24; 103,924 downloads; one maintainer; MIT; [source](https://github.com/pdfdotco/n8n-nodes-pdfco) | Sends statements to PDF.co or presigned cloud storage for unlock/OCR/parse/convert. | Technically yes, but it weakens the financial-document boundary. | Reject for finance statements. |
| [`n8n-nodes-pdf-api-hub@4.0.27`](https://www.npmjs.com/package/n8n-nodes-pdf-api-hub) | Unverified; modified 2026-06-24; 6,014 downloads; one maintainer; MIT; [source](https://github.com/PdfApiHub/n8n-nodes-pdf-api-hub) | Financial PDFs leave the controlled runtime for a third-party API. | No acceptable security improvement. | Reject. |
| [`n8n-nodes-tesseractjs7@2.6.0`](https://www.npmjs.com/package/n8n-nodes-tesseractjs7) | Unverified; modified 2026-08-07; 1,469 downloads; one maintainer; native canvas plus PDF.js/Tesseract dependencies; [source](https://github.com/privat655/n8n-nodes-tesseractjs7) | Local content, but CPU/memory-heavy untrusted PDF/image parsing occurs inside the main n8n worker. | It could replace OCR code but removes the current sandbox boundary. | Reject for production; disposable isolated benchmark only. |
| [`n8n-nodes-actual@26.8.13`](https://www.npmjs.com/package/n8n-nodes-actual) | Unverified; modified 2026-08-19; 4,644 downloads; one maintainer; MIT; [source](https://github.com/TheFehr/n8n-nodes-actual) | Actual URL/password and local budget data; bundles `@actual-app/api` and native SQLite dependencies. Operations are budget month, transactions, import, and budget set. | Not for the writer. It lacks the repository’s fenced lease, immutable artifact recovery, execution-context guard, and exact economic readback. | Pilot read-only in an isolated image; reject for authoritative writes. |
| [`n8n-nodes-mcp@0.1.37`](https://www.npmjs.com/package/n8n-nodes-mcp) | Unverified; modified 2026-01-02; 134,479 downloads; one maintainer; LangChain/MCP/Zod dependency set; [source](https://github.com/nerding-io/n8n-nodes-mcp) | Adds another MCP implementation and credential surface. | No; n8n 2.36.2 has built-in MCP nodes. | Reject. |
| [`n8n-nodes-openai-advanced@0.1.25`](https://www.npmjs.com/package/n8n-nodes-openai-advanced) | Unverified; modified 2026-03-31; 39,209 downloads; one maintainer; wildcard LangChain/n8n AI dependencies; npm repository metadata is not a usable source URL. | OpenAI API key and finance prompts/data enter unverified code. | No. It cannot provide the required ChatGPT-subscription cached-login boundary. | Reject. |
| [`@open-banking-io/n8n-nodes-open-banking-io@0.2.2`](https://www.npmjs.com/package/@open-banking-io/n8n-nodes-open-banking-io) | [n8n verified](https://n8n.io/integrations/open-banking-io/); modified 2026-07-18; 270 downloads; one maintainer; MIT; [source](https://github.com/open-banking-io/n8n) | Provider receives consent/account connectivity; node reads accounts, balances, and transactions and performs client-side zero-knowledge decryption. | It may reduce statement acquisition for supported banks, but cannot replace statement evidence or reconciliation. | Pilot only after UAE coverage, residency, consent, deletion, and outage review. |
| [`n8n-mcp@2.73.0`](https://www.npmjs.com/package/n8n-mcp) | Not a community node; modified 2026-08-19; 636,070 downloads; large dependency/artifact footprint; [source](https://github.com/czlonkowski/n8n-mcp) | Exposes workflow/node knowledge and potentially broad n8n control to an MCP client. | No. It conflicts with the narrow fixed operation-code facade. | Reject for runtime finance plane. |
| [`@openai/codex@0.148.0`](https://www.npmjs.com/package/@openai/codex) | Official OpenAI CLI; modified 2026-08-19; Apache-2.0; [source](https://github.com/openai/codex) | Cached ChatGPT login on the private runner host; output is schema-bound and redacted. | This is not an n8n node. The fixed internal runner is the intended boundary. | Adopt only in the locked private runner image. |

### 1Password and notifications

No credible current n8n community node for 1Password was found. Official packages such as [`@1password/sdk`](https://www.npmjs.com/package/@1password/sdk) and [`@1password/connect`](https://www.npmjs.com/package/@1password/connect) are libraries, not n8n nodes. Keep 1Password CLI/Connect at runtime injection boundaries; never accept a vault path, item ID, field name, or secret value from a workflow caller.

No community Web Push package improved on the current cashback service. Use built-in Pushover/Pushcut only for redacted n8n operational alerts. Bucket/routing push remains in the cashback app so there is one notification truth.

### Subscription-agent authentication boundary

[Official Codex authentication documentation](https://developers.openai.com/codex/auth/) supports ChatGPT-subscription login and cached CLI login state, including `forced_login_method="chatgpt"`; it also warns that cached login material is sensitive. The n8n OpenAI credential path is API-key based and does not satisfy this project’s subscription-auth requirement.

The final adapter decision supersedes the earlier private-runner-only recommendation. `n8n-nodes-prodex@0.5.1` is selected for ChatGPT/Codex subscription execution because it provides device login and SDK `outputSchema`; `@ggomez91npm/n8n-nodes-claude-code@0.8.0` is selected for Claude Pro/Max execution followed by mandatory downstream JSON Schema validation. Exact npm integrity values live in `integrations/n8n/community-node-lock.json`. The main AI workflow never exposes community-node parameters: workflow 21 owns the fixed model, prompt template, sandbox, thread mode, timeout, and schema. Callers cannot select provider, model, prompt, command, path, credential, working directory, sandbox, or URL.

The package review also found material activation gates. ProDex exposes general coding-agent controls in its UI, so this project uses only its fixed read-only/new-thread/schema-bound configuration. The Claude package invokes the CLI as a subprocess and does not itself pass `--no-session-persistence` or native `--json-schema`; it therefore remains blocked until an exact runtime proves no retained session or a reviewed fork supplies that guarantee. Neither package may be installed into production merely because the export references its node type.

## Official workflow-template audit

Templates are examples, not reviewed dependencies. “Adapt” means copy the pattern into checked-in workflows while retaining the finance invariants.

| Template | Nodes/credentials | Useful pattern | Unsafe or out-of-scope assumption | Decision |
|---|---|---|---|---|
| [Automatic Outlook attachment storage to OneDrive with Excel logging](https://n8n.io/workflows/10602-automatic-microsoft-outlook-attachment-storage-to-onedrive-with-excel-logging/) | Outlook, OneDrive, Excel, optional Teams; Microsoft credentials | Iterate binary attachments and land them in OneDrive. | Unread state is used as a cursor; Excel becomes a second log; no content hash/dedupe/readback. | Adapt attachment iteration only. |
| [Save and organize Outlook attachments in OneDrive](https://n8n.io/workflows/6938-automatically-save-and-organize-outlook-email-attachments-in-onedrive-folders/) | Outlook, OneDrive, Split Out, Merge | Split attachment arrays and upload. | Subject/timestamp filenames are mutable/collision-prone; no verified receipt. | Adapt split/upload only; retain canonical hash filenames. |
| [Extract Outlook invoices to OneDrive/Excel with GPT-4.1 mini](https://n8n.io/workflows/15723-extract-and-log-outlook-invoices-to-onedrive-excel-with-gpt-41-mini/) | Outlook/Graph HTTP, OneDrive, Excel, OpenAI API | Shows message/attachment traversal. | AI occurs before durable archive; broad Graph/API credentials; Excel second ledger. | Reject whole template. |
| [Process WhatsApp PDFs with S3 and Textract](https://n8n.io/workflows/13504-process-whatsapp-pdfs-with-aws-textract-ocr-via-s3/) | HTTP/WhatsApp, S3, Textract | S3 landing followed by OCR and structured response. | Public messaging and AWS data exposure; no finance evidence invariants. | Adapt S3→Textract fallback only after policy approval. |
| [Send an S3 file to Textract](https://n8n.io/workflows/1282-send-a-file-from-s3-to-aws-textract/) | S3, Textract | Minimal managed OCR handoff. | No password removal, archive hash, schema validation, or reconciliation. | Adapt only as optional OCR fallback. |
| [Parse Outlook invoices with AI document understanding](https://n8n.io/workflows/3396-parse-incoming-invoices-from-outlook-using-ai-document-understanding/) | Outlook, Gemini, Excel | Illustrates invoice field extraction. | Nondeterministic extraction and spreadsheet persistence. | Reject whole template. |
| [Postgres data-quality monitor](https://n8n.io/workflows/14035-monitor-postgresql-data-quality-and-generate-remediation-alerts-with-slack/) | Schedule, Postgres, Code, Aggregate, IF, Slack | Read-only schema/null/drift monitoring. | AI-generated/remediation SQL and arbitrary target schemas are unsafe. | Adapt fixed read-only checks only. |
| [Error handling with Postgres logging and rate-limited notifications](https://n8n.io/workflows/3882-error-handling-system-with-postgresql-logging-and-rate-limited-notifications/) | Error workflow, Postgres, email/push | Durable error receipt and notification throttling. | Raw stacks/payloads may leak credentials or document data. | Adapt redacted receipt/rate limit; current workflow 16 does this. |
| [Normalize and validate CSV with AI, Postgres, Slack, and Sheets](https://n8n.io/workflows/14273-normalize-and-validate-csv-data-with-anthropicopenai-postgres-slack-and-sheets/) | Webhook, AI, Postgres, Slack, Sheets | Type/missing-value validation stages. | AI and spreadsheets can mutate authoritative data. | Reject whole; adapt deterministic validation only. |
| [Prevent duplicate webhooks with AARI idempotency gate](https://n8n.io/workflows/13863-prevent-duplicate-webhook-executions-with-aari-idempotency-gate/) | Webhook and external AARI service | Idempotency check before side effects. | Adds an external state service and trust boundary. | Reject service; keep the pattern internally. |
| [Build and operate n8n workflows with Claude, Gemini, and MCP](https://n8n.io/workflows/16165-build-and-operate-n8n-workflows-from-claude-with-gemini-and-mcp-tools/) | MCP Server Trigger, tool subworkflows, Gemini, n8n Public API | Demonstrates MCP tool routing. | Allows workflow creation/activation/control, far beyond finance’s bounded facade. | Reject. |
| [Personal budget and expense tracker with Sheets and MCP](https://n8n.io/workflows/4612-personal-budget-and-expense-tracker-with-google-sheets-and-alerts-mcp/) | Sheets, MCP, notification nodes | Presentation and alert ideas. | Sheets becomes a second ledger. | Reject whole. |
| [Telegram/Gemini personal finance tracker](https://n8n.io/workflows/10871-personal-finance-tracker-with-telegram-bot-google-gemini-vision-and-sheets/) | Telegram, Gemini Vision, Sheets | Mobile capture pattern. | AI direct categorization/persistence and a second ledger. | Reject whole. |
| [Receipt photo expense tracking with AI, Sheets, Slack](https://n8n.io/workflows/10970-track-expenses-from-receipt-photos-with-ai-google-sheets-and-slack-reports/) | AI vision, Sheets, Slack | Receipt intake UX. | No immutable evidence/readback and AI writes finance fields. | Reject whole. |
| [Budget variance with Sheets, Gemini, Slack, Gmail](https://n8n.io/workflows/16583-monitor-budget-variance-with-google-sheets-gemini-slack-and-gmail/) | Sheets, Gemini, Slack, Gmail | Variance calculation and alert presentation. | Wrong ledger and notification providers; AI overreach. | Adapt formulas/presentation only inside Actual/cashback reports. |
| [Send selected Gmail PDF attachments to Drive using OpenAI](https://n8n.io/workflows/1897-send-specific-pdf-attachments-from-gmail-to-google-drive-using-openai/) | Gmail, Google Drive, OpenAI | Eligibility classification after message acquisition. | Wrong providers and AI/cloud data exposure. | Reject whole; evidence eligibility must follow durable archive. |

No official template was found that safely implements the complete combination of cursor-minus-overlap acquisition, immutable evidence archive, deterministic statement parsing, fenced Actual import, exact economic readback, reconciliation, and cashback close. The repository workflows remain the correct orchestration source.

## Data Table audit

### Authority boundaries

- Actual Budget: posted accounts, transactions, payees, categories, budgets, schedules, ordinary reports.
- Cashback SQLite: live notification events, live routing/bucket state, period state, cashback-owned cursor.
- OneDrive: statements, receipts, bills, warranties, normalized delta artifacts, AI proposal artifacts.
- n8n Data Tables: redacted operational state, hashes, pointers, receipts, policy/config fingerprints, review state.
- Fixed Postgres functions: process-independent Actual writer lease and fencing token. A Data Table upsert is not a substitute for atomic fencing.

### 15-table v4 contract

| Table | Durable purpose | Owner/use after audit |
|---|---|---|
| `finance_source_contracts` | Trusted mailbox/archive/source config | Read by acquisition, monthly, browser, and AI artifact workflows. No secrets. |
| `finance_source_cursors` | Authoritative cursor for non-cashback acquisition | Workflow 12 `COMMIT` uses `source_code + cursor_version` update and exact readback. Cashback cursors remain in cashback SQLite. |
| `finance_acquisition_receipts` | Frozen enumerated window, valid-empty heartbeat, downstream proof | Workflow 12 writes `ENUMERATED`; cursor commit requires a SHA-256 downstream receipt and writes `DOWNSTREAM_VERIFIED`. |
| `finance_archive_receipts` | Immutable OneDrive message/attachment identity | Workflow 01 content-addresses, archives, and reads back the receipt. |
| `finance_document_operations` | Redacted document state | Workflows 13/14 retain only pointers/hashes/state; decrypted data and extracted text are ephemeral. |
| `finance_pipeline_runs` | Workflow terminal receipt | Monthly/shared/browser flows use verified terminal markers. |
| `finance_actual_outbox` | Recoverable Actual write intent | Only pointers/hashes/config/parser/state; exact normalized delta is in OneDrive. |
| `finance_actual_verifications` | Independent economic readback | Expected/observed hashes, counts, sums, fields, and optional balance. |
| `finance_reconciliations` | Statement/Actual/cashback-close state | Statement evidence is required before close. |
| `finance_config_versions` | Active configuration fingerprints | Workflow 19 seeds and exact-readback verifies eight Git-canonical config hashes. |
| `finance_provider_circuits` | Provider backoff without source payloads | Provider-facing Outlook/OneDrive/Codex paths gate `OPEN`, allow one `HALF_OPEN` probe, close on success; error workflow opens on retryable failure. |
| `finance_execution_failures` | Redacted workflow failure | Error workflow writes, reads, compares, marks verified; provider code is non-secret. |
| `finance_mcp_requests` | Bounded MCP audit | Workflow 10 writes request hash `ACCEPTED`, dispatches a fixed operation, writes redacted terminal result/error hash, then exact-readback verifies. |
| `finance_agent_jobs` | Agent job/proposal/review state | Workflow 09 archives exact proposal JSON to OneDrive, downloads/hash-verifies it, stores pointer/eTag/schema/hash, and leaves review `PENDING`. |
| `finance_ai_policy_contracts` | Server-owned active AI policy domains/hashes | Workflow 19 seeds and exact-readback verifies; runner validates the same contract. |

Every declared table is now referenced by at least one connected executable node. The test suite enforces this bijection.

### Retention, idempotency, and indexes

`integrations/n8n/data-tables.json` v4 carries an explicit policy for every table. Operational 400-day defaults are proposed for runs, acquisitions, failures, MCP jobs, and agent jobs. Active config/cursor/circuit rows are retained indefinitely. Evidence/outbox/verification/reconciliation rows default to co-retention with OneDrive evidence (provisionally seven years) and require owner/legal confirmation before production.

The `idempotency_key` and `index_semantics` entries are logical contracts. n8n Data Table filters do not themselves prove database uniqueness under concurrent writers. Production promotion therefore requires:

1. disposable concurrent execution for each mutating logical key;
2. exact readback after every terminal transition;
3. serialized execution or database-enforced uniqueness where Data Tables cannot prove it;
4. continued use of fixed Postgres CAS functions for the Actual writer lease;
5. a retention cleanup workflow that deletes only terminal rows older than the approved threshold and never deletes evidence pointers still referenced by Actual/reconciliation.

### Remaining promotion blockers

- Re-run workflow 19 in the exact disposable n8n 2.36.2 image and prove both policy and config fingerprint seed readbacks. The earlier baseline proved all 15 table creates, then exposed a single-brace expression serialization defect before any seed row persisted; the generator now emits exact `={{ $json.field }}` expressions and has a regression test.
- Seed/activate trusted `finance_source_contracts` rows outside caller control, including `AI_PROPOSAL_ARCHIVE`; no workflow may accept a OneDrive parent ID from a caller.
- Seed initial non-cashback cursor rows before using workflow 12 `COMMIT`, then run concurrent stale-version fixtures. No production cursor is changed by this audit.
- Publish the MCP facade only after bearer/service auth is proven in the disposable environment and negative operation/field tests pass. Instance MCP remains disabled.
- Execute real OneDrive proposal upload/download/hash readback and confirm Convert-to-File binary behavior in the exact image.
- Add an approved retention cleanup workflow only after the owner confirms retention periods. No cleanup is implemented by this audit.

## Recommended adoption order

1. Prove the checked-in built-in-node workflows and 15-table bootstrap in the disposable stack.
2. Prove Outlook/OneDrive acquisition, provider circuit transitions, zero-message heartbeat, 101+ pagination, downstream-proof cursor commit, and stale-version rejection.
3. Prove bounded MCP accepted/completed/failed receipts without publishing production MCP.
4. Prove Luna and gated Sol proposal artifact upload/download/hash plus `PENDING` review state.
5. Run a read-only isolated `n8n-nodes-actual` comparison if useful; do not replace the fenced writer.
6. Evaluate Open Banking IO only as a separate acquisition pilot. Its output must still enter the immutable archive/normalization/reconciliation path.
