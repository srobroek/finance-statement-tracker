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
ADD --checksum=sha256:07a0fd9879fba3dee7affd2f8571f8759b31e226a70a91ae7a108f88d4881811 \
    ${CLOUDFLARED_SOURCE_URL} \
    /tmp/cloudflared-source.tar.gz
RUN tar -xzf /tmp/cloudflared-source.tar.gz --strip-components=1 -C /src \
    && test "$(go version | awk '{print $3}')" = "go1.26.6" \
    && test "$(awk '$1 == "go" { print $2 }' go.mod)" = "1.26" \
    && test -s go.sum \
    && test -s vendor/modules.txt \
    && printf '%s\n' "${CLOUDFLARED_SOURCE_COMMIT}" > /src/.source-commit
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
