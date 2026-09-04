# n8n end-state refactor requirements and evidence

Snapshot: 2026-08-19  
Scope: repository contracts and inactive disposable import only; no production writes

| Requirement | Repository evidence | Automated evidence | Runtime status |
|---|---|---|---|
| Readable workflow authoring | `refactor_workflow_ui.py` formats every Code node, creates finance-specific sticky notes and native `nodeGroups` | `test_workflow_ui_renderer_is_current_readable_and_idempotent`, `test_canvas_groups_are_native_valid_and_exclude_triggers` | Disposable visual readback pending |
| Outlook server filtering | Workflows 01/12 use official v2 nested `filtersUI.values.filters`, bounded received window, trusted sender/subject filter and local defense-in-depth | `test_outlook_and_onedrive_nodes_use_exact_binary_and_server_filter_contracts`; zero/101+/late/duplicate fixtures | OAuth and live Graph replay pending |
| OneDrive binary correctness | Every upload is v1.1 with `binaryData=true`, `binaryPropertyName=data`; critical artifacts download and hash-verify | same structural test plus archive/proposal tests | OAuth/live readback pending; >4 MiB upload session blocked |
| Attachment diversity | Workflow 01 retains PDF, non-PDF, and inline attachments; attachmentless message body is archived and hash verified | structural nodes/connections and document-state tests | Raw MIME/EML plus trusted HTML-to-PDF renderer still blocked |
| Durable operational state | Data Tables v4 define 15 legacy schemas and four canonical migration targets; W19 creates/reuses the seven legacy tables still referenced by workflow nodes plus all four targets, without deleting existing tables, and seeds disabled source templates | connected-reference scan; bootstrap generator/currentness tests; cutover-feasibility analysis | Source-derived W19 fixture passed twice on pinned n8n 2.36.2 ([run 33915709749](https://github.com/srobroek/finance-statement-tracker/actions/runs/33915709749/job/101162262917)); independent seed-row readback, production-host validation, and semantic four-table cutover remain pending |
| Single Actual writer | Workflow 20 alone owns fenced preflight/import/verify/outbox transitions; workflows 03/17 call it | shared/recovery/writer tests | Disposable kill/concurrency/recovery replay pending |
| Provider-neutral AI | Workflow 09 derives active policy/provider; workflow 21 isolates ProDex/Claude; schemas and npm lock are versioned | adapter lock/server-control tests; runner 16/16 compatibility suite | Community registration/login/proposal receipts pending |
| Foldered UI | `workflow-folders.json` maps all 19 workflows to six folders and defines four tags; inactive exports carry the three inactive-export tags; placement SQL is inactive/project guarded | folder completeness/readback-guard test | Post-import disposable folder/API readback pending |
| Human-readable subworkflow references | All Execute Sub-workflow and Tool Workflow selectors are `mode=list` with stable ID and cached readable name | `test_execute_subworkflow_references_use_from_list` | Exact n8n UI readback pending |
| Explicit credentials | Every BIND placeholder is recorded as `configured=false/action_required=true`; setup checklist is versioned | workflow sanitization and metadata tests | Outlook/OneDrive credentials are absent by design |

## Language decision

- Native n8n nodes and readable JavaScript Code nodes own orchestration, small
  validation, redaction, and item shaping because they remain visible in the UI.
- Fixed TypeScript custom nodes own sensitive PDF, statement, rule projection,
  and Actual operations with narrow parameter-free contracts.
- Python is build-time only for config compilers, export generation, structural
  tests, and the networkless PDF utility. No Python Code node is used in n8n.
- Arbitrary shell/Execute Command/SSH nodes remain forbidden.

## Honest blockers

The exports remain inactive and internally marked `SPEC_ONLY`; user-facing names
say `Setup Required`. Repository tests do not prove import, OAuth, node
registration, folder placement, or external provider behavior. Promotion needs
an exact n8n 2.36.2 disposable replay, direct Data Table/Postgres readback, and
browser visual inspection. Workflow 11 also needs a complete server-side source
contract assembly before a reviewed browser artifact can enter workflow 03.
