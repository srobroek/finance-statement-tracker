# syntax=docker/dockerfile:1.7
ARG NODE_IMAGE=public.ecr.aws/docker/library/node:22.22.3-bookworm-slim@sha256:16d364eebf6b62da439dc993d9b80940c78b0ca38438452f011ab9a25c752644

FROM ${NODE_IMAGE} AS build
WORKDIR /src
COPY services/codex-agent-runner/package.json services/codex-agent-runner/package-lock.json ./
RUN --mount=type=cache,target=/root/.npm npm ci
COPY services/codex-agent-runner/tsconfig.json ./
COPY services/codex-agent-runner/src ./src
RUN npm run build && npm prune --omit=dev

FROM ${NODE_IMAGE} AS codex-download
ARG CODEX_VERSION=0.148.0
ARG CODEX_ARCHIVE_SHA256=1a36f762f6b3bef533bb86345ad9517661c2d84d53996a250cf2ca89d2cfee5a
RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates curl \
    && rm -rf /var/lib/apt/lists/*
RUN curl --fail --show-error --location --proto '=https' --tlsv1.2 \
      "https://github.com/openai/codex/releases/download/rust-v${CODEX_VERSION}/codex-x86_64-unknown-linux-musl.tar.gz" \
      --output /tmp/codex.tar.gz \
    && printf '%s  %s\n' "${CODEX_ARCHIVE_SHA256}" /tmp/codex.tar.gz | sha256sum --check --strict \
    && tar -xzf /tmp/codex.tar.gz -C /tmp \
    && install -m 0755 /tmp/codex-x86_64-unknown-linux-musl /usr/local/bin/codex \
    && /usr/local/bin/codex --version | grep -Fx "codex-cli ${CODEX_VERSION}"

FROM ${NODE_IMAGE} AS runtime
ENV NODE_ENV=production \
    PORT=5090 \
    CODEX_BIN=/usr/local/bin/codex \
    CODEX_HOME=/home/node/.codex \
    SSL_CERT_FILE=/etc/ssl/certs/ca-certificates.crt \
    AI_POLICY_CONFIG_PATH=/app/config/ai-policies.json \
    AI_POLICY_CONTRACT_PATH=/app/contracts/ai-policy-contracts.seed.json \
    AI_OUTPUT_SCHEMA_PATH=/app/contracts/ai-proposal-v1.schema.json
WORKDIR /app
COPY --from=codex-download /usr/local/bin/codex /usr/local/bin/codex
COPY --from=codex-download /etc/ssl/certs/ca-certificates.crt /etc/ssl/certs/ca-certificates.crt
COPY --from=build /src/dist ./dist
COPY --from=build /src/node_modules ./node_modules
COPY --from=build /src/package.json ./package.json
COPY config/ai-policies.json ./config/ai-policies.json
COPY integrations/n8n/generated/ai-policy-contracts.seed.json ./contracts/ai-policy-contracts.seed.json
COPY integrations/n8n/contracts/ai-proposal-v1.schema.json ./contracts/ai-proposal-v1.schema.json
USER node
EXPOSE 5090
CMD ["node", "dist/server.js"]
