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
- P3: Capture before/after state and execution evidence.

## Steps
### S1 -- Query known tags {#S1}
- **Do:** Query a known tag set.
- **Expect:** Only matching records are returned with stable ordering and totals.
### S2 -- Query multiple and empty tags {#S2}
- **Do:** Query multiple tags and an empty/unknown tag.
- **Expect:** Documented intersection/selection semantics and an explicit empty result are returned.
### S3 -- Repeat report query {#S3}
- **Do:** Repeat the same query.
- **Expect:** Identical output is returned for unchanged data.
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
- G1: Beads author `orc-n2q.379.18` establishes title and RO profile.
- G2: Historical/source corpus refs `2cd7612`/`161de41` are unavailable; exact report command and final evidence remain draft and must not be marked pass.

## Delta log
