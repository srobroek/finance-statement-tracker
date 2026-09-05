# Finance PDF utility

This is a deliberately narrow document sandbox. It listens only on the Unix
socket `/run/platform-pdf/pdf.sock`, has no TCP listener, and accepts exactly:

- `POST /v1/validate`
- `POST /v1/unlock`
- `POST /v1/profile` (fixed `statement-v1` text extraction inside the sandbox)
- `GET /health`

The request body is the PDF. Unlock receives a base64-encoded password in the
non-logged `X-Statement-Password` header. Profile is fixed to
`X-Pdf-Profile: statement-v1`. The service exposes no filesystem path, URL,
command, parser name, output destination, or finance credential.

Run with `network_mode: none`, a read-only root filesystem, a tmpfs `/tmp`, a
shared socket volume, and the resource/security limits in `compose.example.yml`.
The n8n container mounts only the socket volume. The utility must never mount
OneDrive, Actual data, n8n data, Docker socket, or 1Password material.

`pikepdf` embeds the QPDF library. The base image and every Python dependency are
version-pinned. The Chainguard Python build image uses the immutable
`latest-dev@sha256:afdbadf8d697739ab8e10a4d355d0850daa439cba3e6f0e39a73f7f2d3d839b7`
index and the minimal runtime uses
`latest@sha256:eca30c0ac647bf28beaec7442388609d14fd100984fa63397e6015eaffe22aa1`.
These indexes were resolved from the successful Phase 1 image build on
2026-09-04 and include the Wolfi OpenSSL fix for CVE-2026-14456. Build output
still needs an SBOM and vulnerability scan before promotion; HIGH and CRITICAL
findings remain release blockers.
