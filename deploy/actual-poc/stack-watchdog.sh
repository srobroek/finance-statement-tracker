#!/usr/bin/env bash
set -euo pipefail

stack=/opt/stacks/finance-actual-poc

healthy() {
  curl -fsS --max-time 10 http://127.0.0.1:5006/ >/dev/null &&
    curl -fsS --max-time 10 http://127.0.0.1:5010/api/health | grep -q '"status": "ok"' &&
    curl -fsS --max-time 10 http://127.0.0.1:5020/api/health | grep -q '"status": "ok"'
}

if healthy; then
  exit 0
fi

cd "$stack"
docker compose up -d --no-deps --pull never \
  actual actual-proxy cashback-control actual-ingestion

for _ in $(seq 1 30); do
  if healthy; then
    exit 0
  fi
  sleep 2
done

docker compose ps
exit 1
