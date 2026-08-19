# Finance Cloudflared image

This artifact rebuilds Cloudflared 2026.7.3 from the exact upstream commit in
`source-lock.json`. The source archive, Go 1.26.6 builder image, module vendor
tree, build timestamp, version linker metadata, and target platform are locked.
The runtime is `scratch`, contains only the statically linked binary and CA
bundle, runs as numeric non-root UID/GID 65532, has entrypoint `cloudflared`,
and deliberately has no default command.

The deployed Compose command remains:

```text
tunnel --no-autoupdate run
```

CI checks the exact version, help/argument parsing, missing-token fail-closed
behaviour, zero HIGH/CRITICAL image findings, SPDX generation, immutable GHCR
publication, and a digest receipt. The image contains no tunnel token or
configuration.
