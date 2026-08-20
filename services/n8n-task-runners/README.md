# Source-rebuilt n8n task runners

This SPEC_ONLY image reproduces the JavaScript and native Python runners from
the pinned n8n `2.36.2` source tree. It rebuilds the official launcher `1.4.7`
from its tagged source with one explicit security-only dependency override:
`golang.org/x/text` `v0.39.0`.

The CI job checks out both upstream repositories by full commit, applies the
hash-locked source transformation, builds the JavaScript dependency closure from the n8n lockfile,
runs the upstream launcher tests, builds the image from digest-pinned bases,
and executes a JavaScript-to-Python workflow through a real n8n `2.36.2`
external task broker. The image is scanned with no HIGH/CRITICAL waiver before
an SBOM and immutable digest receipt are published.

The image contains no finance credentials or writable finance data. Production
deployment is outside this repository-only phase.
