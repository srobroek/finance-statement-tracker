#!/bin/sh
set -eu

runner_image="${1:?runner image is required}"
n8n_image="${2:?n8n image is required}"
network="finance-runners-smoke-${GITHUB_RUN_ID:-local}-$$"
volume="finance-runners-smoke-state-${GITHUB_RUN_ID:-local}-$$"
runner="finance-runners-smoke-runner-$$"
broker="finance-runners-smoke-broker-$$"
auth_token="finance-task-runners-protocol-smoke-only"
fixture="$(cd "$(dirname "$0")" && pwd)/protocol-smoke.json"

cleanup() {
  docker rm -f "$broker" "$runner" >/dev/null 2>&1 || true
  docker volume rm "$volume" >/dev/null 2>&1 || true
  docker network rm "$network" >/dev/null 2>&1 || true
}
trap cleanup EXIT INT TERM

safe_logs() {
  docker logs "$1" 2>&1 | sed "s/$auth_token/[REDACTED]/g"
}

docker network create "$network" >/dev/null
docker volume create "$volume" >/dev/null
docker run --rm \
  --network "$network" \
  --volume "$volume:/home/node/.n8n" \
  --volume "$fixture:/fixtures/protocol-smoke.json:ro" \
  --env N8N_ENCRYPTION_KEY=finance-task-runners-protocol-smoke-only \
  "$n8n_image" import:workflow --input=/fixtures/protocol-smoke.json >/dev/null

docker run --rm \
  --volume "$volume:/home/node/.n8n" \
  --env N8N_ENCRYPTION_KEY=finance-task-runners-protocol-smoke-only \
  "$n8n_image" publish:workflow --id=finance-task-runners-protocol-smoke >/dev/null

docker run --detach --name "$broker" \
  --network "$network" \
  --network-alias broker \
  --volume "$volume:/home/node/.n8n" \
  --env N8N_ENCRYPTION_KEY=finance-task-runners-protocol-smoke-only \
  --env N8N_RUNNERS_ENABLED=true \
  --env N8N_RUNNERS_MODE=external \
  --env N8N_RUNNERS_BROKER_LISTEN_ADDRESS=0.0.0.0 \
  --env N8N_RUNNERS_AUTH_TOKEN="$auth_token" \
  --env N8N_NATIVE_PYTHON_RUNNER=true \
  "$n8n_image" start >/dev/null

i=0
until docker exec "$broker" node -e "Promise.all([fetch('http://127.0.0.1:5678/healthz'),fetch('http://127.0.0.1:5679/healthz')]).then(rs=>{if(rs.some(r=>!r.ok))process.exit(1)}).catch(()=>process.exit(1))"; do
  i=$((i + 1))
  if [ "$i" -ge 60 ]; then
    safe_logs "$broker"
    exit 1
  fi
  sleep 1
done

docker run --detach --name "$runner" \
  --network "$network" \
  --read-only \
  --tmpfs /tmp:size=16m,mode=1770,uid=1000,gid=1000 \
  --tmpfs /home/runner:size=16m,mode=0700,uid=1000,gid=1000 \
  --env N8N_RUNNERS_TASK_BROKER_URI=http://broker:5679 \
  --env N8N_RUNNERS_AUTH_TOKEN="$auth_token" \
  --env N8N_RUNNERS_LAUNCHER_LOG_LEVEL=debug \
  "$runner_image" >/dev/null

i=0
until docker logs "$runner" 2>&1 | grep -F 'Connected: ws://broker:5679/' >/dev/null; do
  i=$((i + 1))
  if [ "$i" -ge 30 ]; then
    safe_logs "$runner"
    safe_logs "$broker"
    exit 1
  fi
  sleep 1
done

if ! docker exec "$broker" node -e "fetch('http://127.0.0.1:5678/webhook/finance-task-runners-protocol-smoke').then(async r=>{const body=await r.text(); console.log(body); if(!r.ok)process.exit(1)}).catch(e=>{console.error(e);process.exit(1)})" \
  > /tmp/finance-task-runners-protocol-smoke.log 2>&1; then
  cat /tmp/finance-task-runners-protocol-smoke.log
  safe_logs "$runner"
  safe_logs "$broker"
  exit 1
fi
grep -F 'js_runner' /tmp/finance-task-runners-protocol-smoke.log >/dev/null
grep -F 'python_runner' /tmp/finance-task-runners-protocol-smoke.log >/dev/null
grep -E '"value"[[:space:]]*:[[:space:]]*42' /tmp/finance-task-runners-protocol-smoke.log >/dev/null

echo "Verified n8n 2.36.2 external broker protocol with JavaScript and native Python runners"
