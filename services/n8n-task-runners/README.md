# Source-rebuilt n8n task runners

This SPEC_ONLY image reproduces the JavaScript and native Python runners from
the pinned stable n8n `2.37.10` source tree. It rebuilds the official launcher `1.4.7`
from its tagged source with one explicit security-only dependency override:
`golang.org/x/text` `v0.39.0`.

The CI job checks out both upstream repositories by full commit, applies the
hash-locked source transformation, builds the JavaScript dependency closure from the n8n lockfile,
runs the upstream launcher tests, builds the image from digest-pinned bases,
and executes a JavaScript-to-Python workflow through a real n8n `2.37.10`
external task broker. The image is scanned with no HIGH/CRITICAL waiver before
an SBOM and immutable digest receipt are published.

The runtime also installs the fixed Alpine `v3.23` revisions for `libcrypto3`
and `libssl3` (`3.5.8-r0`) and `sqlite-libs` (`3.53.4-r0`). These pins address
the current OpenSSL and SQLite findings recorded in `upstream.lock.json` and
are kept alongside the digest-pinned base images so a rebuild cannot retain an
older vulnerable base-layer package.

The image contains no finance credentials or writable finance data. Production
deployment is outside this repository-only phase.

### Canonical notification normalization

The Python runner image includes the repository's `finance_tracker` package,
versioned configuration and provenance fixtures under `/opt/finance`. A venv
`.pth` entry loads that immutable package even with Python isolated mode (`-I`).
Only `finance_tracker,jsonschema_specifications` is added to `N8N_RUNNERS_EXTERNAL_ALLOW`; the second package contains the schema resources loaded by jsonschema. Transitive imports remain disabled and existing runner
security settings remain in effect. If deployment mounts a launcher JSON file,
set the Python runner's `env-overrides.N8N_RUNNERS_EXTERNAL_ALLOW` to the same
`finance_tracker,jsonschema_specifications` value before using W02.

W02 imports `normalize_archived_mailbox` and emits compact transaction records
plus one disposition per archived message. It sends each accepted event to the
Cashback API separately, verifies every child receipt, then persists an aggregate
scan receipt before committing the cursor. A quiet scan follows an explicit
zero-event path and records the same durable heartbeat. The protocol smoke now
requires the canonical parser import and a successful zero-event normalization;
the disposable Cashback harness separately verifies individual HTTP ingestion,
replay, restart and a quiet heartbeat without changing stored buckets.
