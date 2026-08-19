ARG GO_BUILDER_IMAGE=docker.io/library/golang:1.26.6-alpine3.23@sha256:e57c41c1d5864341031181b0db34b9a537bb5773eb6428e4e5bdaea0f9135406
ARG CLOUDFLARED_SOURCE_URL=https://github.com/cloudflare/cloudflared/archive/3a2b45c2a511fcdd81b68c190938e4ffadbea5dc.tar.gz

FROM ${GO_BUILDER_IMAGE} AS build

ARG CLOUDFLARED_SOURCE_URL
ARG CLOUDFLARED_VERSION=2026.7.3
ARG CLOUDFLARED_SOURCE_COMMIT=3a2b45c2a511fcdd81b68c190938e4ffadbea5dc
ARG SOURCE_DATE_EPOCH=1784737966

ENV CGO_ENABLED=0 \
    GOOS=linux \
    GOARCH=amd64 \
    GOFLAGS=-mod=vendor \
    GOTOOLCHAIN=local \
    SOURCE_DATE_EPOCH=${SOURCE_DATE_EPOCH}

WORKDIR /src
COPY grpc-1.82.1-security-overlay.tar.gz /tmp/grpc-1.82.1-security-overlay.tar.gz
ADD --checksum=sha256:07a0fd9879fba3dee7affd2f8571f8759b31e226a70a91ae7a108f88d4881811 \
    ${CLOUDFLARED_SOURCE_URL} \
    /tmp/cloudflared-source.tar.gz
RUN tar -xzf /tmp/cloudflared-source.tar.gz --strip-components=1 -C /src \
    && test "$(go version | awk '{print $3}')" = "go1.26.6" \
    && test "$(awk '$1 == "go" { print $2 }' go.mod)" = "1.26" \
    && test -s go.sum \
    && test -s vendor/modules.txt \
    && printf '%s\n' "${CLOUDFLARED_SOURCE_COMMIT}" > /src/.source-commit
RUN test "$(sha256sum /tmp/grpc-1.82.1-security-overlay.tar.gz | awk '{print $1}')" = "24770fdedc94e4695d48933b221faa202e01279d3b2267547b6b67ced2dc10cb" \
    && mkdir -p /tmp/security-overlay \
    && tar -xzf /tmp/grpc-1.82.1-security-overlay.tar.gz -C /tmp/security-overlay \
    && test -s /tmp/security-overlay/overlay-manifest.json \
    && rm -rf \
      vendor/google.golang.org/grpc \
      vendor/google.golang.org/genproto/googleapis/api \
      vendor/google.golang.org/genproto/googleapis/rpc \
    && cp -R /tmp/security-overlay/vendor/google.golang.org/grpc vendor/google.golang.org/ \
    && cp -R /tmp/security-overlay/vendor/google.golang.org/genproto/googleapis/api vendor/google.golang.org/genproto/googleapis/ \
    && cp -R /tmp/security-overlay/vendor/google.golang.org/genproto/googleapis/rpc vendor/google.golang.org/genproto/googleapis/ \
    && cp /tmp/security-overlay/go.mod go.mod \
    && cp /tmp/security-overlay/go.sum go.sum \
    && cp /tmp/security-overlay/vendor/modules.txt vendor/modules.txt \
    && test "$(sha256sum go.mod | awk '{print $1}')" = "abb8cc486ac0deac4a31142e58a3a507bbc2b54ffce99dc5b4f6a3f56f9fc629" \
    && test "$(sha256sum go.sum | awk '{print $1}')" = "b8cd67798a462f596e8e7676471d02d5814e83a1e8973f542c342329b651cf25" \
    && test "$(sha256sum vendor/modules.txt | awk '{print $1}')" = "acbaccc28686e8f7d3576ccf561eab2c64706b73a97c8042d3fb3f578985366f" \
    && go list -mod=vendor -m -f '{{.Path}} {{.Version}}' \
      google.golang.org/grpc \
      google.golang.org/genproto/googleapis/api \
      google.golang.org/genproto/googleapis/rpc \
      > /tmp/module-graph.txt \
    && grep -Fx "google.golang.org/grpc v1.82.1" /tmp/module-graph.txt \
    && grep -Fx "google.golang.org/genproto/googleapis/api v0.0.0-20260414002931-afd174a4e478" /tmp/module-graph.txt \
    && grep -Fx "google.golang.org/genproto/googleapis/rpc v0.0.0-20260414002931-afd174a4e478" /tmp/module-graph.txt \
    && test "$(wc -l < /tmp/module-graph.txt)" -eq 3 \
    && ! grep -F "google.golang.org/grpc v1.81.1" /tmp/module-graph.txt
RUN mkdir -p /out \
    && go build \
      -trimpath \
      -buildvcs=false \
      -ldflags="-s -w -buildid= -X main.Version=${CLOUDFLARED_VERSION} -X main.BuildTime=2026-07-22T16:32:46Z -X github.com/cloudflare/cloudflared/metrics.Runtime=virtual" \
      -o /out/cloudflared \
      ./cmd/cloudflared \
    && test "$(/out/cloudflared version | awk '{print $3}')" = "${CLOUDFLARED_VERSION}"

FROM scratch

LABEL org.opencontainers.image.source="https://github.com/srobroek/finance-statement-tracker" \
      org.opencontainers.image.title="Finance Cloudflared" \
      org.opencontainers.image.version="2026.7.3" \
      org.opencontainers.image.revision="3a2b45c2a511fcdd81b68c190938e4ffadbea5dc"

COPY --from=build /etc/ssl/certs/ca-certificates.crt /etc/ssl/certs/ca-certificates.crt
COPY --from=build --chown=65532:65532 /out/cloudflared /usr/local/bin/cloudflared

USER 65532:65532
ENTRYPOINT ["cloudflared"]
