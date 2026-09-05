# syntax=docker/dockerfile:1.7
ARG NODE_IMAGE=docker.io/library/node:26.5.1-alpine3.24@sha256:233761595746769ebfdb6090f44fc7cdf818ae0ce62d2b37e0367723b9823e36
ARG PYTHON_IMAGE=docker.io/library/python@sha256:7ea3f82de8ea6d4fb7e5d2bbe3fe3c9d931700b7a529f1fe5769e42abe514ca1
ARG GO_IMAGE=docker.io/library/golang@sha256:42fc3368d1c50170a452f2bf4a1dfd292a065870c3f258d799aad4316671cb69

FROM ${NODE_IMAGE} AS javascript-runner
COPY .upstream/n8n/dist/task-runner-javascript /opt/runners/task-runner-javascript
WORKDIR /opt/runners/task-runner-javascript
RUN test "$(node -p "require('./package.json').version")" = "2.37.5" \
    && test -f dist/start.js \
    && test -f finance-closure-manifest.json \
    && test -f finance-closure-relinks.json \
    && test -f finance-workspace-packages.json \
    && node --check dist/start.js \
    && node finance-verify-workspace-packages.mjs .

FROM ${PYTHON_IMAGE} AS python-runner
ARG UV_VERSION=0.8.14
RUN python -m pip install --no-cache-dir "uv==${UV_VERSION}"
WORKDIR /opt/runners/task-runner-python
COPY .upstream/n8n/packages/@n8n/task-runner-python/pyproject.toml \
     .upstream/n8n/packages/@n8n/task-runner-python/uv.lock \
     .upstream/n8n/packages/@n8n/task-runner-python/.python-version ./
RUN uv venv \
    && uv sync --frozen --no-editable --no-install-project --no-dev --all-extras
COPY .upstream/n8n/packages/@n8n/task-runner-python/ ./
RUN uv sync --frozen --no-editable --no-dev --all-extras \
    && uv pip install . \
    && rm -rf src \
    && .venv/bin/python -I -B -X disable_remote_debug -c "import src.main, urllib3, websockets"

# Finance notification parsing reuses the canonical Python implementation.
# Runtime dependency versions mirror the repository uv.lock.
RUN uv pip install --no-deps attrs==26.1.0 jsonschema==4.26.0 \
        jsonschema-specifications==2025.9.1 referencing==0.37.0 rpds-py==2026.6.3 \
    && .venv/bin/python -c "import site; from pathlib import Path; Path(site.getsitepackages()[0], 'finance-runtime.pth').write_text('/opt/finance\\n')"

FROM ${GO_IMAGE} AS launcher
WORKDIR /src
COPY .upstream/task-runner-launcher/ ./
RUN test "$(go list -m -f '{{.Version}}' golang.org/x/text)" = "v0.39.0" \
    && go mod verify \
    && go test ./... \
    && CGO_ENABLED=0 go build -trimpath -ldflags="-s -w -buildid=" \
       -o /out/task-runner-launcher ./cmd/launcher \
    && go version -m /out/task-runner-launcher | grep -F 'golang.org/x/text' | grep -F 'v0.39.0'

FROM ${PYTHON_IMAGE} AS runtime
ARG N8N_VERSION=2.37.10
ARG N8N_SOURCE_COMMIT=5542b8b6419cb6925cca8f11b270c9bfbe09d85e
ARG LAUNCHER_SOURCE_COMMIT=fad71fc0dab52a6b6558765d1431c7846cfd816f
ARG FINANCE_SOURCE_COMMIT=UNVERIFIED

ENV NODE_ENV=production \
    N8N_VERSION=${N8N_VERSION} \
    SHELL=/bin/sh \
    PYTHONDONTWRITEBYTECODE=1

# Keep the runtime's Alpine security fixes explicit.  These revisions contain
# the fixes for CVE-2026-14456, CVE-2026-11822, and CVE-2026-11824.
RUN apk add --no-cache \
        ca-certificates \
        tini \
        libstdc++ \
        libgcc \
        libcrypto3=3.5.8-r0 \
        libssl3=3.5.8-r0 \
        sqlite-libs=3.53.4-r0 \
    && python -m pip uninstall --yes pip setuptools \
    && apk del apk-tools \
    && addgroup -g 1000 -S runner \
    && adduser -u 1000 -S -G runner -h /home/runner -D runner

WORKDIR /home/runner
COPY --from=javascript-runner /usr/local/bin/node /usr/local/bin/node
COPY --from=javascript-runner --chown=root:root /opt/runners/task-runner-javascript /opt/runners/task-runner-javascript
COPY --from=python-runner --chown=root:root /opt/runners/task-runner-python /opt/runners/task-runner-python
COPY --from=launcher /out/task-runner-launcher /usr/local/bin/task-runner-launcher
COPY --chown=root:root .upstream/n8n/docker/images/runners/n8n-task-runners.json /etc/n8n-task-runners.json
COPY --chown=root:root services/n8n-task-runners/upstream.lock.json /opt/runners/upstream.lock.json

COPY --chown=root:root finance_tracker /opt/finance/finance_tracker
COPY --chown=root:root config /opt/finance/config
COPY --chown=root:root docs /opt/finance/docs
COPY --chown=root:root tests/fixtures /opt/finance/tests/fixtures
COPY --chown=root:root services/n8n-task-runners/configure_finance_python.py /opt/runners/configure_finance_python.py
RUN python /opt/runners/configure_finance_python.py /etc/n8n-task-runners.json \
    && /opt/runners/task-runner-python/.venv/bin/python -I -B -c "from finance_tracker.n8n_notifications import normalize_archived_mailbox; assert callable(normalize_archived_mailbox)"

RUN node --version | grep -Fx 'v26.5.1' \
    && /opt/runners/task-runner-python/.venv/bin/python --version | grep -E '^Python 3\.13\.' \
    && /opt/runners/task-runner-python/.venv/bin/python -I -B -X disable_remote_debug -c "import src.main, urllib3, websockets" \
    && test "$(stat -c '%u:%g' /usr/local/bin/task-runner-launcher)" = '0:0'

USER runner
EXPOSE 5680/tcp
HEALTHCHECK --interval=10s --timeout=3s --retries=6 \
  CMD ["node", "-e", "fetch('http://127.0.0.1:5680/healthz').then(r=>{if(!r.ok)process.exit(1)}).catch(()=>process.exit(1))"]
ENTRYPOINT ["tini", "--", "/usr/local/bin/task-runner-launcher"]
CMD ["javascript", "python"]

LABEL org.opencontainers.image.title="Finance n8n task runners" \
      org.opencontainers.image.description="Source-rebuilt n8n JavaScript/Python task runners and launcher" \
      org.opencontainers.image.version="${N8N_VERSION}" \
      org.opencontainers.image.source="https://github.com/n8n-io/n8n/tree/${N8N_SOURCE_COMMIT}" \
      finance.finance_source_commit="${FINANCE_SOURCE_COMMIT}" \
      finance.n8n.source_commit="${N8N_SOURCE_COMMIT}" \
      finance.launcher.source_commit="${LAUNCHER_SOURCE_COMMIT}" \
      finance.launcher.security_override="golang.org/x/text@v0.39.0"
