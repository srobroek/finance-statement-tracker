---
id: J45
title: Build/attest pinned images
version: 1
status: draft
last_reviewed: 2026-09-17
actors: [release-operator]
surfaces: [container-images]
interfaces: [RW-O]
trace:
  - PLATFORM-002
  - .github/workflows/phase1-finance-artifacts.yml
  - .github/workflows/cashback-image.yml
  - orc-n2q.379.46
  - orc-rxxz
  - 4f115c64351b24554ec9b2ada6b0166786fda727
---
# J45 -- Build/attest pinned images

## Goal
The release operator promotes five reviewed Finance runtime images by immutable digest and retains evidence that deployment and rollback use those digests.

## Image scope
| Image | Candidate workflow | Deployment gate |
|---|---|---|
| `n8n` | `phase1-finance-artifacts.yml` | Fresh exact-head receipt |
| `task_runners` | `phase1-finance-artifacts.yml` | Fresh exact-head receipt |
| `postgres` | `phase1-finance-artifacts.yml` | Fresh exact-head receipt |
| `pdf_utility` | `phase1-finance-artifacts.yml` | Fresh exact-head receipt |
| `cashback` | `cashback-image.yml` | Separate digest-resolved prerequisite and fresh exact-head receipt |

The independently scoped n8n platform-images PostgreSQL candidate is not a sixth Finance image.

## Preconditions
- P1: The named workflow builds each candidate from its reviewed source commit and frozen build inputs in an uncontended release lane.
- P2: Registry, signing, and verification access is available without exposing credentials.
- P2a: Deployment and protected-state readback access is available without exposing credentials.
- P3: A fresh pre-state receipt records each running image's `Config.Image` and complete `RepoDigests`.
- P3a: The receipt also records the protected-state hash and repository-qualified rollback digest.
- P4: Publication needs an approval before the registry write.
- P4a: Deployment needs a separate approval before the runtime write.
- P4b: Rollback needs another approval before the restore write.
- P5: The Cashback image remains blocked until `cashback-image.yml` emits its immutable digest and the evidence required by S1-S6.
- P6: The rollback environment is mode `0600` and identifies only a digest derived from the running image and verified against its complete `RepoDigests` membership.

## Steps
### S1 -- Build the five candidates {#S1}
- **Do:** Run each image's named workflow from the reviewed source commit and frozen inputs.
- **Expect:** Each candidate receipt records its image name and source commit.
- **Expect:** The receipt records the engine image ID and immutable `repository@sha256:<64hex>` reference.
- **Expect (negative):** Dirty inputs or mutable tags fail closed.
- **Expect (negative):** An image outside the five-image scope fails closed.

### S2 -- Attest and verify each digest {#S2}
- **Do:** Generate the required evidence for each candidate digest and verify it.
- **Expect:** Each receipt records the attestation subject and source.
- **Expect:** It records the attestation status.
- **Expect:** It records the SBOM hash and scan hash.
- **Expect:** It records the scan result and engine image ID.
- **Expect:** The attestation subject equals the registry digest. Its source commit equals the candidate commit.
- **Expect (negative):** `SPEC_ONLY` or `UNVERIFIED` metadata blocks publication.
- **Expect (negative):** SBOM-only or candidate-only evidence blocks publication.
- **Expect (negative):** Missing or mismatched evidence blocks publication.

### S3 -- Publish immutable candidates {#S3}
- **Do:** After publication approval, publish each verified candidate and record the registry response.
- **Expect:** The publication receipt maps the reviewed source commit and engine image ID to one exact repository-qualified digest.
- **Expect (negative):** A tag, including `main` or `latest`, never becomes deployment authority.

### S4 -- Deploy exact digests {#S4}
- **Do:** After deployment approval, pull and compose the exact approved digest.
- **Expect:** The generated environment or lock contains that exact reference.
- **Expect:** Deployment uses the same repository-qualified digest recorded by S3.
- **Expect:** After the P5 prerequisite passes, Cashback can reach this step.
- **Expect (negative):** An unresolved or mutable reference blocks deployment.

### S5 -- Read back deployment identity {#S5}
- **Do:** After startup, inspect every deployed image and its protected state.
- **Expect:** `Config.Image` names the approved digest.
- **Expect:** Exactly one repository-qualified `RepoDigests` member matches that digest.
- **Expect:** The receipt preserves the candidate identity and the before-and-after identities.
- **Expect:** The protected-state hash remains equal to its pre-state value.
- **Expect (negative):** A missing or duplicate identity blocks acceptance and enters S6.
- **Expect (negative):** A tag-only or mismatched identity blocks acceptance and enters S6.

### S6 -- Restore the verified rollback digest {#S6}
- **Do:** After rollback approval, restore only the digest captured by P3 and repeat the identity and protected-state readback.
- **Expect:** The operator derives the rollback digest from the running image.
- **Expect:** The digest belongs to the image's complete repository-qualified `RepoDigests` set.
- **Expect:** The operator loads the digest from the mode-`0600` rollback environment.
- **Expect:** Post-rollback image evidence matches the pre-state receipt.
- **Expect:** Post-rollback protected-state evidence matches the pre-state receipt.
- **Expect (negative):** Array position or a mutable tag never selects the rollback image.
- **Expect (negative):** An unverified digest never selects the rollback image.

## Success criteria
- SC1: S1-S3 produce five receipts that bind each reviewed commit to its engine image ID and registry digest.
- SC2: Each receipt includes the required attestation and SBOM evidence.
- SC2a: Each receipt includes the required scan and publication evidence.
- SC3: S4-S5 prove that every promoted image runs the approved digest with one matching repository-qualified readback.
- SC4: S4-S5 prove that protected state remains equal to its pre-state value.
- SC5: S6 restores only the verified prior digest without changing protected state.
- SC6: All four phase1 images need a fresh exact-head receipt before J45 can pass.
- SC7: The separately gated Cashback image also needs a fresh exact-head receipt before J45 can pass.
- SC8: Workflow reports or unsigned metadata alone never count as production acceptance.
- SC9: An SBOM, source test, or candidate build alone never counts as production acceptance.

## Evidence
- E1: Candidate receipt for the named image and workflow.
- E2: Publication receipt for the exact registry digest.
- E3: Attestation subject, source, and status for that digest.
- E4: SBOM hash and vulnerability-scan hash for that digest.
- E5: Vulnerability-scan result for that digest.
- E6: Generated environment or lock and the pull/compose output.
- E7: Post-start `Config.Image` and complete `RepoDigests` readback.
- E8: Candidate identity and before-and-after identities.
- E9: Protected-state receipt and mode-`0600` rollback receipt.

Existing phase1 and Cashback reports establish workflow contracts only. J45 requires fresh exact-head production receipts and does not treat those reports as deployment acceptance.

## Known gaps
- None. P3-P6 retain the unresolved runtime evidence as explicit prerequisites, so this journey remains `draft` until validation records a passing run.

## Delta log
- None.
