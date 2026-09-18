---
id: J17
title: Tag-filtered reports
version: 1
status: draft
last_reviewed: 2026-09-17
actors: [finance-operator]
surfaces: [finance-core]
interfaces: [RO]
trace: []
---
# J17 -- Tag-filtered reports

## Goal
Generate reports filtered by tags with deterministic inclusion, clear empty results, and no state mutation.

## Preconditions
- P1: Use a fixed report period and filter.
- P2: Use report/read-only surfaces.
- P3: Use the tag-report contract in `tests/test_reporting.py`.
  - Command: `python -m unittest -v tests.test_reporting.ReportingTests`
  - Fixture: the in-memory `Transaction` rows defined by `ReportingTests`.
  - State check: run `git diff --exit-code -- finance_tracker/reporting.py tests/test_reporting.py` before and after the command.
  - Evidence context: record the commit and exact commands.
  - Evidence output: record every exit status. Save complete stdout and stderr.
  - Pass contract: both state checks and the command exit 0. The command runs three named tests and ends with `OK`.

## Steps
### S1 -- Query known tags {#S1}
- **Do:** Query a known tag set.
- **Expect:** Only matching records are returned with stable ordering and totals.
- **Expect (negative):** Records without a requested tag are not returned, and the query does not change any source record.
### S2 -- Query multiple and empty tags {#S2}
- **Do:** Query multiple tags and an empty/unknown tag.
- **Expect:** Documented intersection/selection semantics and an explicit empty result are returned.
- **Expect (negative):** Any, all, and none semantics are not conflated, and an unknown tag does not produce a placeholder row.
### S3 -- Repeat report query {#S3}
- **Do:** Repeat the same query.
- **Expect:** Identical output is returned for unchanged data.
- **Expect (negative):** No ordering or aggregate values change between identical runs. No repository-tracked report state changes.
### S4 -- Reject malformed or unauthorized filter {#S4}
- **Do:** Submit a malformed or unauthorized filter.
- **Expect:** A clear error occurs and no mutation occurs.
### S5 -- Verify no side effects {#S5}
- **Do:** Verify before/after state.
- **Expect:** Zero writes, changed records, or side effects are evidenced.

## Success criteria
- SC1: S1-S5: Tag-filtered reports include exactly matching records with deterministic output and explicit empty results.
- SC2: S1-S5: Repeated and invalid queries do not mutate state.

## Known gaps
- G1: The repository has no dedicated end-user tag-report command. P3 uses the supported unittest surface. Accepting that surface as the product journey requires user confirmation.
- G2: No final J17 validation evidence exists. Keep this journey in draft until a validator executes P3 and records its evidence contract in a run file.

## Delta log
