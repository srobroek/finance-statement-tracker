FROM python:3.12-slim@sha256:dd29372629eeba2dd003fd9e9d35a5b8236c44727875a0364254b5127af88e65

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    CASHBACK_HOST=0.0.0.0 \
    CASHBACK_PORT=5010 \
    CASHBACK_DB_PATH=/var/lib/cashback-control/cashback-events.sqlite3 \
    CASHBACK_DASHBOARD_PATH=/var/lib/cashback-control/cashback-dashboard.json

WORKDIR /app

COPY pyproject.toml README.md ./
COPY finance_tracker ./finance_tracker
COPY config/actual-note-contract.json ./config/actual-note-contract.json
COPY config/cashback-programs.json ./config/cashback-programs.json
COPY config/actual-bootstrap.json ./config/actual-bootstrap.json
COPY config/static-rules.seed.json ./config/static-rules.seed.json
COPY config/transaction-email-sources.json ./config/transaction-email-sources.json
RUN pip install --no-cache-dir .

COPY apps/cashback-control ./apps/cashback-control

RUN mkdir -p /var/lib/cashback-control \
    && useradd --uid 10001 --create-home cashback \
    && chown -R cashback:cashback /var/lib/cashback-control /app

USER cashback
EXPOSE 5010
VOLUME ["/var/lib/cashback-control"]

CMD ["python", "apps/cashback-control/server.py"]
