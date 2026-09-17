---
id: J45
title: Build/attest pinned images
version: 1
status: draft
last_reviewed: 2026-09-17
actors: [release-operator]
surfaces: [container-images]
interfaces: [RW-O]
trace: [orc-n2q.379.46, 4f115c64351b24554ec9b2ada6b0166786fda727]
---
# J45 -- Build/attest pinned images

## Goal
Build release images from reviewed inputs and attest immutable digests so deployment consumes exactly the approved artifacts.

## Preconditions
- P1: Source revision, dependency lock, build recipe, and target platforms are recorded.
- P2: Registry and signing/verifying identities are available; deployment activation is a separate approved step.

## Steps
### S1 -- Build candidate images {#S1}
- **Do:** Run the reproducible build from the pinned source and lock inputs.
- **Expect:** Images are produced with immutable digests and build provenance.
- **Expect (negative):** Unpinned or dirty inputs fail closed.

### S2 -- Attest and verify {#S2}
- **Do:** Sign the approved digests and verify signatures/provenance against the release record.
- **Expect:** Verification succeeds only for exact digests, issuer, and source.
- **Expect (negative):** Mismatches or missing attestations block release.

### S3 -- Record handoff {#S3}
- **Do:** Store the digest/attestation receipt for the deployment handoff.
- **Expect:** A reviewer can reproduce the mapping from source to image.
- **Expect (negative):** No deployment mutation occurs in this journey.

## Success criteria
- SC1: S1-S3 establish reproducible pinned images and independently verifiable attestations.

## Known gaps
- G1: Build registry/signing runtime evidence is unavailable; this remains a draft readiness contract.

## Delta log
- No behavior delta; structural normalization only.
