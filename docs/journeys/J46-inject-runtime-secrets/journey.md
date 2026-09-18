---
id: J46
title: Inject runtime secrets
version: 1
status: draft
last_reviewed: 2026-09-17
actors: [finance-operator]
surfaces: [n8n, finance-config, codex-runner]
interfaces: [RW-O]
trace: [orc-n2q.379.47, orc-n2q.379.99, orc-bncd]
---
# J46 -- Inject runtime secrets

## Goal
The finance operator injects approved secret values into one named n8n runtime without storing plaintext outside its authorized secret boundary. Done means the runtime passes health and credential-decryption checks, the redacted receipt identifies the operation, and negative scans find no secret values.

## Preconditions
- P1: The target n8n runtime, workflow or run, reviewed image and configuration, and supported injection mode are identified.
- P2: The finance operator has completed MFA and approved one batch fetch from the dedicated `FinanceAutomation` vault.
- P3: A least-privilege, read-only service account is scoped to `n8n-runtime`; its exact token or session fingerprint is known.
- P4: The vault item and the separately protected stable `N8N_ENCRYPTION_KEY` are the only durable secret states. Statement, Microsoft, and Actual credentials remain encrypted n8n credentials, and `N8N_BLOCK_ENV_ACCESS_IN_NODE=true`.
- P5: The finance operator holds the exclusive write lane. A protected receipt records the reviewed image and configuration. It records identifiers and fingerprints. It records health and credential decryptability without values.
- P6: Controls are available to stop the process and revoke the exact service-account token or session. Other controls rotate the affected credential and restore the prior reviewed image and configuration.

## Steps
### S1 -- Bind the target and pre-state {#S1}
- **Do:** Inspect the named runtime and approved vault identifiers. Compare the reviewed image and configuration against P5. Check the account scope, injection mode, and receipt.
- **Expect:** The target identity and `n8n-runtime` scope match the approved operation. Pre-state health and credential decryptability match P5.
- **Expect (negative):** This inspection does not fetch a secret or mutate the runtime. It does not expose a secret value.
- **Expect (negative):** Missing identity or stale pre-state stops before mutation.
- **Expect (negative):** An unsupported injection mode or broader scope stops before mutation.
- **Trace:** Research contract `orc-bncd`; `docs/bellwether-onepassword-runner-validation-2026-08-19.md:22-36`.

### S2 -- Approve and fetch one batch {#S2}
- **Do:** Fetch the required vault items once for the bound operation, but only after finance-operator approval. Use `op run` or the reviewed mounted-secret path.
- **Expect:** The batch is available only to the target process. Use `op run` or mode-0600 `/run/secrets` files with supported `_FILE` inputs.
- **Expect:** If `.env.bootstrap` is required, it contains only the mode-0600 bootstrap token. The operator removes it immediately after injection.
- **Expect (negative):** Never source `.env.bootstrap` as shell code or pass its token to a container.
- **Expect (negative):** Do not leave the bootstrap token set after injection.
- **Expect (negative):** No resolved `.env` or Compose render contains a secret value. Shell history and logs also contain no secret values.
- **Expect (negative):** No chat or artifact contains a secret value. Execution payloads and backups also contain no secret values.
- **Trace:** Research contract `orc-bncd`; `docs/bellwether-onepassword-runner-validation-2026-08-19.md:7-36`.

### S3 -- Inject into the bound runtime {#S3}
- **Do:** In the exclusive lane, inject the approved batch into only the target process with the S1 injection mode.
- **Expect:** n8n reads statement, Microsoft, and Actual credentials from its encrypted credential store and retains the stable, separately protected `N8N_ENCRYPTION_KEY`.
- **Expect:** The operation emits a redacted receipt with item identifiers and token or key fingerprints. It also records timestamps, actor, and result or error code.
- **Expect (negative):** The receipt contains no secret value. Workflow code cannot read credential values through environment access.
- **Expect (negative):** The operation creates no new listener, credential, or runner.
- **Expect (negative):** The operation creates no duplicate service, durable plaintext file, or inspectable container environment.
- **Trace:** Research contract `orc-bncd`; `docs/end-to-end-project-plan-red-team-2026-08-19.md:180-203`.

### S4 -- Read back without values {#S4}
- **Do:** Re-read the target identity, process health, and credential decryptability. Check item identifiers and scope. Check permissions and the redacted receipt.
- **Expect:** The intended runtime is healthy, and each required credential decrypts. Exact scope matches S1, and unrelated state matches P5.
- **Expect:** Git, Compose output, and container inspection contain zero secret values.
- **Expect:** n8n executions and logs contain zero secret values. Artifacts and backups also contain zero secret values.
- **Expect (negative):** Readback does not request or print plaintext. It does not compare or persist plaintext.
- **Expect (negative):** Any mismatch or secret occurrence fails closed and proceeds to S5.

### S5 -- Revoke and restore {#S5}
- **Do:** If validation fails or approval is withdrawn, stop the affected process before changing its ephemeral secret mount. Take the same action for rotation or rollback.
- **Do:** Revoke the exact `FinanceAutomation` service-account token or session used for the batch, then rotate the affected vault item credential.
- **Do:** Restore only the prior reviewed image and configuration, then restart through a fresh `op run` or reviewed mounted-secret injection.
- **Expect:** The restored process uses the reviewed configuration and newly injected credential; no secret value is restored from a file.
- **Expect (negative):** Do not substitute a broader account, rotate an unrelated credential, or treat either revocation or rotation alone as compensation.

### S6 -- Prove revocation and compensation {#S6}
- **Do:** Attempt negative authentication with the revoked token, repeat health and credential-decryption readback, and repeat every S4 negative scan.
- **Expect:** The authentication attempt rejects the old token. The restored runtime is healthy, and each required credential decrypts. Every scan reports zero secret values.
- **Expect:** The receipt records the revocation, rotation, and restore. It also records the actor and timestamps. It records fingerprints and each result or error code.
- **Expect (negative):** If revocation or rotation fails, keep the service stopped. Apply the same response to a failed readback or scan.
- **Expect (negative):** Retain only the redacted receipt, and do not report rollback success.

## Success criteria
- SC1: S1-S4 produce one receipt with the target and item identifiers. It records the actor and timestamps. It records fingerprints and the result or error code without secret values.
- SC2: After S4, the vault item is the only durable credential state. The stable `N8N_ENCRYPTION_KEY` remains separately protected.
- SC3: After S4, the named runtime is healthy, and each required credential decrypts. Exact scope matches S1, and unrelated state matches P5.
- SC4: If S5 runs, S6 shows that the exact old token fails authentication. Each named scan surface contains zero secret values.
- SC5: A failed approval, fetch, or injection leaves the affected service stopped. Failed revocation or rotation has the same result. The same rule applies to failed readback or scanning.

## Known gaps
- G1: No production execution is claimed. Validation requires the named runtime, an authorized finance operator, and the exclusive write lane.
- G2: Vault item identifiers, token or session fingerprints, and the supported injection mode are deployment-specific inputs. S1 must bind them from the approved inventory before any fetch.

## Delta log
- No behavior delta; this correction applies the runtime-secret retention and revocation contract recorded in `orc-bncd`.
