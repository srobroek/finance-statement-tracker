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

- **Stable ID:** J17
- **Profile:** RO
- **Status:** draft / blocked
- **Owner scope:** `orc-n2q.379.18`

## Goal
Generate reports filtered by tags with deterministic inclusion, clear empty results, and no state mutation.

## Prerequisites and surfaces
Use a fixed report period/filter and report/read-only surfaces. Capture before/after state and execution evidence.

## Steps and assertions
1. Query a known tag set; expect only matching records and stable ordering/ totals.
2. Query multiple tags and an empty/unknown tag; expect documented intersection/selection semantics and an explicit empty result.
3. Repeat the same query; expect identical output for unchanged data.
4. Negative: malformed or unauthorized filter; expect a clear error and no mutation.
5. Verify before/after state; expect zero writes, changed records, or side effects.

## Evidence and gaps
Beads author `orc-n2q.379.18` establishes title and RO profile. Historical/source corpus refs `2cd7612`/`161de41` are unavailable; exact report command and final evidence remain draft and must not be marked pass.
