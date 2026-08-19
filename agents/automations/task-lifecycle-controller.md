# Finance automation task lifecycle controller

Run this as a scheduled task **inside one persistent operations chat**, not as a standalone task. Standalone tasks create a new chat per run; the controller must not create the same accumulation it is meant to prevent.

1. Inspect only finance automation thread IDs from the configured project and known automation IDs. Never archive the calling operations chat, an active task, an unrelated task, or the current root implementation task.
2. Require an exact `task_complete` event, a final answer, and a matching `SUCCESS_VERIFIED` receipt. Use `python -m finance_tracker.automation_lifecycle` against the local Codex session and archived-session roots. Missing or ambiguous state fails closed.
3. Keep failed, partial, blocked, review-required, and unreceipted runs active. Do not decide success from prose alone.
4. For each eligible completed run, call `codex_app__set_thread_archived` with the exact explicit `threadId` and `hostId="local"`. Never omit `threadId`: self-targeting archive calls were observed to hang for several minutes.
5. Confirm the exact rollout moved from the active session root to the archived-session root by rerunning the lifecycle audit. A returned archive acknowledgement is not sufficient without this state confirmation.
6. If the archive call or confirmation fails, stop after the bounded attempt and leave the run visible. Report the exact thread ID and evidence; do not retry in a loop.

The current app exposes no archive-operation status endpoint and no reliable archived-list operation. This controller therefore uses the explicit archive API for mutation and the exact local lifecycle location for confirmation. The official scheduled-task documentation does not define an ephemeral/no-history mode. It documents that standalone runs create new chats and that scheduled work can instead return to an existing chat.
