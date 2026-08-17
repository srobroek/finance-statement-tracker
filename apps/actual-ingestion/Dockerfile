FROM node:22-bookworm-slim@sha256:d649c27dae7ba0137b3cef5dd75baa422c08dc3d9e3fc0c23dfb172dc3cc6436

ARG FINANCE_PIPELINE_REVISION

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    FINANCE_INGEST_HOST=0.0.0.0 \
    FINANCE_INGEST_PORT=5020 \
    FINANCE_INGEST_DATA=/var/lib/finance-ingestion \
    ACTUAL_DATA_DIR=/var/lib/finance-ingestion/actual-cache \
    FINANCE_PIPELINE_REVISION=${FINANCE_PIPELINE_REVISION}

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends python3 python3-pip python3-venv \
    && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml README.md ./
COPY finance_tracker ./finance_tracker
RUN python3 -m pip install --break-system-packages --no-cache-dir ".[statements]"

COPY integrations/actual/package.json integrations/actual/package-lock.json ./integrations/actual/
RUN cd integrations/actual && npm ci --omit=dev
COPY integrations/actual/*.mjs ./integrations/actual/

COPY config ./config
COPY browser_adapters ./browser_adapters
COPY apps/actual-ingestion ./apps/actual-ingestion

RUN mkdir -p /var/lib/finance-ingestion/inbox /var/lib/finance-ingestion/jobs /var/lib/finance-ingestion/actual-cache \
    && useradd --uid 10002 --create-home ingestion \
    && chown -R ingestion:ingestion /var/lib/finance-ingestion /app

USER ingestion
EXPOSE 5020
VOLUME ["/var/lib/finance-ingestion"]

CMD ["python3", "apps/actual-ingestion/server.py"]
