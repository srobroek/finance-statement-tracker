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

docker network create "$network" >/dev/null
docker volume create "$volume" >/dev/null
docker run --rm \
  --network "$network" \
  --volume "$volume:/home/node/.n8n" \
  --volume "$fixture:/fixtures/protocol-smoke.json:ro" \
  --env N8N_ENCRYPTION_KEY=finance-task-runners-protocol-smoke-only \
  "$n8n_image" import:workflow --input=/fixtures/protocol-smoke.json >/dev/null

docker run --detach --name "$runner" \
  --network "$network" \
  --read-only \
  --tmpfs /tmp:size=16m,mode=1770,uid=1000,gid=1000 \
  --tmpfs /home/runner:size=16m,mode=0700,uid=1000,gid=1000 \
  --env N8N_RUNNERS_TASK_BROKER_URI=http://broker:5679 \
  --env N8N_RUNNERS_AUTH_TOKEN="$auth_token" \
  "$runner_image" >/dev/null

i=0
until docker exec "$runner" node -e "fetch('http://127.0.0.1:5680/healthz').then(r=>{if(!r.ok)process.exit(1)}).catch(()=>process.exit(1))"; do
  i=$((i + 1))
  if [ "$i" -ge 30 ]; then
    docker logs "$runner"
    exit 1
  fi
  sleep 1
done

docker run --detach --name "$broker" \
  --network "$network" \
  --volume "$volume:/home/node/.n8n" \
  --env N8N_ENCRYPTION_KEY=finance-task-runners-protocol-smoke-only \
  --env N8N_RUNNERS_MODE=external \
  --env N8N_RUNNERS_BROKER_LISTEN_ADDRESS=0.0.0.0 \
  --env N8N_RUNNERS_AUTH_TOKEN="$auth_token" \
  --env N8N_NATIVE_PYTHON_RUNNER=true \
  "$n8n_image" execute --id=finance-task-runners-protocol-smoke --rawOutput >/dev/null

exit_code="$(docker wait "$broker")"
docker logs "$broker" > /tmp/finance-task-runners-protocol-smoke.log 2>&1
if [ "$exit_code" != "0" ]; then
  cat /tmp/finance-task-runners-protocol-smoke.log
  exit 1
fi
grep -F 'js_runner' /tmp/finance-task-runners-protocol-smoke.log >/dev/null
grep -F 'python_runner' /tmp/finance-task-runners-protocol-smoke.log >/dev/null
grep -E '"value"[[:space:]]*:[[:space:]]*42' /tmp/finance-task-runners-protocol-smoke.log >/dev/null

echo "Verified n8n 2.36.2 external broker protocol with JavaScript and native Python runners"
