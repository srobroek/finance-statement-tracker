# Bounded scheduled production acceptance

`integrations/n8n/setup-workflows/runner/generate-production-acceptance-workflow.py`
generates an inactive external workflow from a clean exact reviewed checkout.
It does not import, publish or execute anything. Keep generated files and their
hashes in the existing private deployment evidence directory outside the image.

For `rak`, `ei`, or `wio`, it preserves the corresponding canonical workflow's
entire acquisition and persistence graph. Only its schedule changes, and an
absolute time guard is inserted before the original first operation. RAK still
reads its durable Cashback cursor, sweeps the complete window, archives every
source, normalizes, persists exact receipts and commits/readbacks the cursor.
No source payload, scan boundary, cursor, receipt or execution mode is injected.
EI/Wio retain their configured monthly windows and call the shared monthly
workflow normally. This avoids calling a schedule-only graph as a subworkflow:
n8n requires an Execute Workflow Trigger or legacy manual start for that path.

For `maintenance`, the parent calls fixed workflow
`10000000-0000-4000-8000-000000000025` with empty input. W25 owns all reviewed
plan/account/backup bindings. It performs bounded chunks of at most 100 rows.
The parent waits for each call, stops immediately on `complete: true`, and fails
at the configured maximum call count if work remains. Errors fail the execution;
an operator can schedule a fresh bounded replay using W25's durable receipts.

Example generation, with the exact future UTC minute chosen only after all gates:

```sh
python integrations/n8n/setup-workflows/runner/generate-production-acceptance-workflow.py \
  --source-root "$reviewed_source" --source-commit "$reviewed_sha" \
  --kind rak --at "$acceptance_utc_minute" --output "$private_acceptance_json"
```

Deployment must first verify backups, immutable matching images, mounted profile,
typed credential bindings, enabled source contracts and account mappings. Bind
generated credential/table placeholders against the same verified live inventory
as the canonical corpus. Preserve its generated ID and exact bound file hash.
Keep the corresponding canonical schedule inactive while acceptance is published.
For maintenance, no other Actual writer/recovery schedule may run concurrently.

Import using native `import:workflow --input=... --projectId=...`, verify the
inactive stored source/bindings, then deliberately `publish:workflow --id=...`
through the reviewed stop/import/publish/restart lifecycle. Observe the natural
scheduled execution, including its child executions and downstream readbacks.
Do not use CLI execution or change its execution mode. The absolute window
rejects early execution, manual execution and the next year's cron occurrence.

After the result, unpublish the acceptance ID and restart using that same
lifecycle. Verify its inactive state before publishing canonical schedules.
Generate another finite schedule for exact replay when needed. A successful
parent alone is insufficient: retain source/archive, Actual/Cashback persistence,
receipt/cursor and replay evidence, then observe the next canonical schedule and
restart survival. Tests of the generator establish source/graph and time bounds;
they do not constitute production ingestion or runtime scheduling evidence.
