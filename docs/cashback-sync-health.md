# Scheduled transaction-check health

Cashback health follows the active source's daily times and timezone in
`config/ingestion.json`. The deployed RAK slot is 08:05 Asia/Dubai.
`CASHBACK_STALE_AFTER_MINUTES` is the grace period after a scheduled check,
now 90 minutes in deployment configuration; it is not a rolling age limit.

A successful scan receipt at or after the most recent scheduled run whose
grace period has expired satisfies that check. A scan accepting zero events
is a successful check. The time of the last purchase does not control health.
With yesterday's successful check, today's status remains healthy before
08:05 and during grace. A missing check becomes overdue at 09:35 Dubai; a
later successful check clears it. A successful 08:06 scan stays healthy that
night and until the next due check's grace expires.

Dashboard and push notifications use the same `data_status.is_stale` result.
`last_successful_check_at` aliases the existing successful-ingest timestamp;
`check_status`, `expected_due_at`, `next_scheduled_check_at`, timezone and
`check_grace_minutes` make the decision inspectable. Existing ingestion
cursors, transaction dates and financial records are unchanged.

Unknown/paused sources and malformed schedule data do not silently claim a
healthy configured schedule. Explicit per-source requests are required if
multiple banks are active. Custom daily source times/timezones can be tested
with `check_schedule_config_path`; the deployment uses the versioned file.
