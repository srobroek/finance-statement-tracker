# Finance Cloudflared image

This artifact rebuilds Cloudflared 2026.7.3 from the exact upstream commit in
`source-lock.json`. The source archive, Go 1.26.6 builder image, module vendor
tree, build timestamp, version linker metadata, and target platform are locked.
The runtime is `scratch`, contains only the statically linked binary and CA
bundle, runs as numeric non-root UID/GID 65532, has entrypoint `cloudflared`,
and deliberately has no default command.

The tagged source carried gRPC `1.81.1`, which is affected by
`GHSA-hrxh-6v49-42gf`. The checked-in `grpc-1.82.1-security.patch` is the exact
`go get`, `go mod tidy`, and `go mod vendor` delta for gRPC `1.82.1` and its two
resolved genproto modules. Repository tests verify the patch hash. The build
verifies the patched `go.mod`/`go.sum`/`vendor/modules.txt` hashes and vendored
module graph before compiling; it does not download dependencies or waive the
image scan.
Because the digest-pinned Go Alpine builder deliberately has no package manager
additions, CI consumes the reproducible, manifest-bearing overlay produced by
`generate_security_overlay.py` instead of installing a patch utility. The
source patch remains checked in for review, and the build verifies the overlay
and every resulting module-control-file hash.

The deployed Compose command remains:

```text
tunnel --no-autoupdate run
```

CI checks the exact version, help/argument parsing, missing-token fail-closed
behaviour, zero HIGH/CRITICAL image findings, SPDX generation, immutable GHCR
publication, and a digest receipt. The image contains no tunnel token or
configuration.
