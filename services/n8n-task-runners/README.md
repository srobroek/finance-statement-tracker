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
