# Automation lifecycle feasibility audit — 2026-08-19

## Outcome

Automatic cleanup is feasible through a persistent controller, but self-archival from a standalone run is not reliable in the current desktop app.

Two completed RAKBANK runs were inspected:

| Run | Finance result | Original self-archive | Explicit external archive | Independent confirmation |
| --- | --- | --- | --- | --- |
| `01a0130d-0c6f-71d3-a24b-05d13eaaf2e3` | Success; task complete | Hung for more than eight minutes | Returned immediately | Exact rollout moved to `archived_sessions` |
| `01a01832-d35e-7f10-9eed-d28c625ad484` | Success; task complete | Archive and archived-list calls hung | Returned immediately | Exact rollout moved to `archived_sessions` |

The observed difference is target resolution: both failed calls omitted `threadId` and targeted the calling run. Explicit calls from another live task supplied the completed thread ID and `hostId="local"`; each returned an acknowledgement in under one second. Both rollouts then existed exactly once under the archived-session root and no longer existed under the active session root.

This proves external exact-ID archival and local archived-state confirmation. It does **not** prove that a standalone task can archive itself by supplying its own ID. The task must finish before the controller considers it eligible.

## Product boundary

OpenAI's scheduled-task documentation says standalone scheduled tasks start a new chat for every run. It also supports scheduling inside an existing chat when recurring work should return to the same context. The page recommends archiving scheduled runs that are no longer needed, but it does not document an ephemeral/no-history run mode or automatic archival guarantee.

Current app capabilities expose an explicit background archive operation. They do not expose an archive-operation status object. The current app build also did not provide a reliable archived-list call, and unfiltered thread listing remained unresponsive during the experiment. Therefore a returned mutation acknowledgement plus exact lifecycle-location verification is the strongest currently demonstrated confirmation.

Official source: https://learn.chatgpt.com/docs/automations?surface=app

## Safe design

1. Each finance run writes a machine-readable `SUCCESS_VERIFIED` receipt only after its domain verification gates pass.
2. The run finishes normally and emits `task_complete`; it does not archive itself.
3. One persistent-chat lifecycle controller discovers only configured finance automation runs.
4. The controller requires the receipt, final answer, and task-complete event.
5. It archives the exact explicit thread ID.
6. It confirms the rollout moved to archived storage. Failures remain visible.

`finance_tracker.automation_lifecycle` implements the fail-closed audit primitive. `agents/automations/task-lifecycle-controller.md` defines the controller contract.

## Unavoidable limitation

The archive operation is a desktop-app capability, not a repository or host API. A container cannot archive Codex runs. If the desktop app is not running or its task service is unavailable, finance ingestion can still complete, but chat cleanup waits for the next successful controller pass.
