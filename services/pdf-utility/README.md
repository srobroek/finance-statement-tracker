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
version-pinned. Build output still needs an SBOM and vulnerability scan before
promotion.
