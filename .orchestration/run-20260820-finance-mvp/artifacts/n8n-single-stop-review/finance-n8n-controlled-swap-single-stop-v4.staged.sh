#!/usr/bin/env bash
set -Eeuo pipefail

DOCKER=/usr/bin/docker
PG=n8n-postgres-1
N8N=n8n
RUNNER=n8n-task-runners-1
ART=/opt/backups/finance-n8n-recovery/20260917T190300Z-w12-w01-overlap
PROD=n8n
CAND=n8n_recovery_20260917
ROLL=n8n_incident_20260917T190300Z
EXPECTED_IMAGE='ghcr.io/srobroek/finance-n8n@sha256:3991f3b0157dbf954bca61ef203569c6d516d8e1fb51e7ae0765acea549917e7'
EXPECTED_CANON='755230edf158d47a22199e28a3a8b754da5f14d9708ba1510dcd52fecd76a96e'
EXPECTED_CATALOG='c00c9be919318d930a122b2556e6eb4ba41a7905a04d1fde322122a286a4a13f'
LP_SQL=/tmp/w12-w01-lp-candidate.sql
INC_SQL=/tmp/w12-w01-lp-incident.sql
CAT_SQL=/tmp/w12-w01-core-catalog-final.sql
DUPE_SQL=/tmp/w12-w01-dupe-gate.sql
phase=preflight
last_gate=init
STAGE_LOG="$ART/single-stop-controller.log"
started_at="$(date -u +%Y-%m-%dT%H:%M:%SZ)"

if [[ "${DISPOSABLE_SWAP_SELF_TEST:-0}" == 1 ]]; then
  exec 9>/tmp/finance-n8n-recovery-v4-selftest.lock
else
  exec 9>/run/finance-n8n-recovery.lock
fi
flock -n 9
umask 077

q() {
  local database=$1 sql=$2
  "$DOCKER" exec "$PG" psql -v ON_ERROR_STOP=1 -U n8n -d "$database" -Atqc "$sql"
}
q_tx() {
  local database=$1 sql=$2
  "$DOCKER" exec "$PG" psql -v ON_ERROR_STOP=1 --single-transaction -U n8n -d "$database" -Atqc "$sql"
}
counts() {
  q "$1" 'SELECT (SELECT count(*) FROM workflow_entity),(SELECT count(*) FROM workflow_entity WHERE active),(SELECT count(*) FROM workflow_entity WHERE "activeVersionId" IS NOT NULL),(SELECT count(*) FROM execution_entity),(SELECT count(*) FROM credentials_entity);'
}
canonical_digest() {
  local database=$1 script=$2
  "$DOCKER" exec "$PG" psql -qAt -v ON_ERROR_STOP=1 -U n8n -d "$database" -f "$script" | sha256sum | cut -d' ' -f1
}
catalog_digest() {
  "$DOCKER" exec "$PG" psql -qAt -v ON_ERROR_STOP=1 -U n8n -d "$1" -f "$CAT_SQL" | sha256sum | cut -d' ' -f1
}
dupe_stats() {
  "$DOCKER" exec "$PG" psql -qAt -v ON_ERROR_STOP=1 -U n8n -d "$1" -f "$DUPE_SQL"
}
container_state() {
  "$DOCKER" inspect -f '{{.State.Status}}' "$1"
}
runtime_snapshot() {
  "$DOCKER" inspect -f '{{.State.Status}}|{{.State.Running}}|{{.State.Pid}}|{{.State.ConmonPid}}|{{.State.Error}}' "$1" 2>/dev/null || printf 'missing|false|0|0|inspect_failed\n'
}
log_stage() {
  local stage=$1 name=$2 rc=$3 snapshot=$4
  printf '%s|stage=%s|container=%s|rc=%s|state=%s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$stage" "$name" "$rc" "$snapshot" >>"$STAGE_LOG"
}
log_database_stage() {
  local stage=$1 rc=$2 detail=$3
  printf '%s|stage=%s|rc=%s|database_state=%s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$stage" "$rc" "$detail" >>"$STAGE_LOG"
}
database_identity() {
  if [[ "${DISPOSABLE_SWAP_SELF_TEST:-0}" == 1 ]]; then
    if [[ "${TEST_DISCONNECT_IDENTITY:-0}" == 1 ]]; then
      return 70
    fi
    printf '%s\n' "$TEST_DATABASE_IDENTITY"
    return 0
  fi
  q_tx postgres "SELECT count(*) FILTER (WHERE datname='$PROD'),count(*) FILTER (WHERE datname='$CAND'),count(*) FILTER (WHERE datname='$ROLL') FROM pg_database;"
}
verify_database_identity() {
  local expected=$1 stage=$2 observed rc
  if observed="$(database_identity)"; then
    rc=0
  else
    rc=$?
    observed=identity_query_failed
  fi
  if [[ "$rc" == 0 && "$observed" == "$expected" ]]; then
    log_database_stage "$stage" 0 "$observed"
    return 0
  fi
  [[ "$rc" != 0 ]] || rc=1
  log_database_stage "$stage" "$rc" "$observed"
  return 1
}
terminate_known_sessions() {
  local database=$1 result rc
  case "$database" in
  "$PROD" | "$CAND" | "$ROLL") ;;
  *)
    log_database_stage terminate_rejected 64 "$database"
    return 64
    ;;
  esac
  if [[ "${DISPOSABLE_SWAP_SELF_TEST:-0}" == 1 ]]; then
    if [[ "${TEST_FAIL_TERMINATE:-}" == "$database" ]]; then
      log_database_stage "terminate_${database}" 70 disconnected
      return 70
    fi
    log_database_stage "terminate_${database}" 0 known_n8n_sessions_only
    return 0
  fi
  if result="$(q_tx postgres "SELECT COALESCE(bool_and(pg_terminate_backend(pid)), true) FROM pg_stat_activity WHERE datname='$database' AND usename='n8n' AND pid<>pg_backend_pid();")"; then
    rc=0
  else
    rc=$?
    result=termination_query_failed
  fi
  if [[ "$rc" != 0 || "$result" != t ]]; then
    log_database_stage "terminate_${database}" "${rc:-1}" "$result"
    return 1
  fi
  log_database_stage "terminate_${database}" 0 known_n8n_sessions_only
  if result="$(q_tx postgres "SELECT count(*) FROM pg_stat_activity WHERE datname='$database' AND pid<>pg_backend_pid();")"; then
    rc=0
  else
    rc=$?
    result=session_verification_failed
  fi
  log_database_stage "terminate_verify_${database}" "$rc" "$result"
  [[ "$rc" == 0 && "$result" == 0 ]]
}
rename_database() {
  local source=$1 target=$2 operation rc
  operation="${source}:${target}"
  case "$operation" in
  "$PROD:$ROLL" | "$CAND:$PROD" | "$PROD:$CAND" | "$ROLL:$PROD") ;;
  *)
    log_database_stage rename_rejected 64 "$operation"
    return 64
    ;;
  esac
  if [[ "${DISPOSABLE_SWAP_SELF_TEST:-0}" == 1 ]]; then
    if [[ "${TEST_FAIL_RENAME:-}" == "$operation" ]]; then
      log_database_stage "rename_${source}_to_${target}" 71 injected_failure
      return 71
    fi
    case "$operation:$TEST_DATABASE_IDENTITY" in
    "$PROD:$ROLL:1|1|0") TEST_DATABASE_IDENTITY='0|1|1' ;;
    "$CAND:$PROD:0|1|1") TEST_DATABASE_IDENTITY='1|0|1' ;;
    "$PROD:$CAND:1|0|1") TEST_DATABASE_IDENTITY='0|1|1' ;;
    "$ROLL:$PROD:0|1|1") TEST_DATABASE_IDENTITY='1|1|0' ;;
    *)
      log_database_stage "rename_${source}_to_${target}" 72 "$TEST_DATABASE_IDENTITY"
      return 72
      ;;
    esac
    log_database_stage "rename_${source}_to_${target}" 0 "$TEST_DATABASE_IDENTITY"
    return 0
  fi
  if q postgres "ALTER DATABASE $source RENAME TO $target;" >/dev/null; then
    rc=0
  else
    rc=$?
  fi
  log_database_stage "rename_${source}_to_${target}" "$rc" command_complete
  return "$rc"
}
prove_candidate_active_identity() {
  if ! verify_database_identity '1|0|1' candidate_active_identity; then
    return 1
  fi
  if [[ "${DISPOSABLE_SWAP_SELF_TEST:-0}" == 1 ]]; then
    return 0
  fi
  if [[ "$(counts "$PROD")" == '69|6|6|0|12' ]] &&
    [[ "$(canonical_digest "$PROD" "$LP_SQL")" == "$EXPECTED_CANON" ]] &&
    [[ "$(catalog_digest "$PROD")" == "$EXPECTED_CATALOG" ]]; then
    log_database_stage candidate_active_semantic 0 proven
    return 0
  fi
  log_database_stage candidate_active_semantic 1 proof_failed
  return 1
}
prove_rollback_identity() {
  if ! verify_database_identity '1|1|0' rollback_identity; then
    return 1
  fi
  if [[ "${DISPOSABLE_SWAP_SELF_TEST:-0}" == 1 ]]; then
    return 0
  fi
  if [[ "$(counts "$PROD")" == '138|12|12|0|24' ]] &&
    [[ "$(canonical_digest "$PROD" "$INC_SQL")" == "$EXPECTED_CANON" ]]; then
    log_database_stage rollback_semantic 0 proven
    return 0
  fi
  log_database_stage rollback_semantic 1 proof_failed
  return 1
}
forward_database_swap() {
  verify_database_identity '1|1|0' forward_initial_identity || return
  terminate_known_sessions "$PROD" || return
  terminate_known_sessions "$CAND" || return
  last_gate=rename_incident
  rename_database "$PROD" "$ROLL" || return
  verify_database_identity '0|1|1' forward_incident_identity || return
  phase=incident_renamed
  last_gate=rename_candidate
  rename_database "$CAND" "$PROD" || return
  verify_database_identity '1|0|1' forward_candidate_identity || return
  phase=swapped
}
restore_database_identity() {
  local observed
  if ! observed="$(database_identity)"; then
    log_database_stage rollback_identity_read 70 identity_query_failed
    return 1
  fi
  log_database_stage rollback_identity_read 0 "$observed"
  case "$observed" in
  '1|1|0')
    prove_rollback_identity || return
    ;;
  '0|1|1')
    terminate_known_sessions "$ROLL" || return
    rename_database "$ROLL" "$PROD" || return
    prove_rollback_identity || return
    ;;
  '1|0|1')
    terminate_known_sessions "$PROD" || return
    terminate_known_sessions "$ROLL" || return
    rename_database "$PROD" "$CAND" || return
    verify_database_identity '0|1|1' rollback_candidate_restored || return
    rename_database "$ROLL" "$PROD" || return
    prove_rollback_identity || return
    ;;
  *)
    log_database_stage rollback_identity_unsupported 73 "$observed"
    return 1
    ;;
  esac
}
restart_runtime_for_identity() {
  local identity=$1
  case "$identity" in
  candidate) prove_candidate_active_identity || return ;;
  rollback) prove_rollback_identity || return ;;
  *) return 64 ;;
  esac
  if [[ "${DISPOSABLE_SWAP_SELF_TEST:-0}" == 1 ]]; then
    TEST_RESTARTS=$((TEST_RESTARTS + 1))
    log_database_stage "restart_gate_${identity}" 0 proven
    return 0
  fi
  start_if_startable "$RUNNER" || return
  start_if_startable "$N8N"
}
stop_once() {
  local name=$1 rc running status snapshot
  snapshot="$(runtime_snapshot "$name")"
  log_stage stop_before "$name" 0 "$snapshot"
  set +e
  "$DOCKER" stop --time 30 "$name" >/dev/null
  rc=$?
  set -e
  snapshot="$(runtime_snapshot "$name")"
  log_stage stop_return "$name" "$rc" "$snapshot"
  [[ "$rc" == 0 ]] || return "$rc"
  for ((settle_try = 0; settle_try < 50; settle_try++)); do
    running="$($DOCKER inspect -f '{{.State.Running}}' "$name" 2>/dev/null || printf unknown)"
    status="$(container_state "$name" 2>/dev/null || printf unknown)"
    if [[ "$running" == false ]]; then
      if [[ "${DISPOSABLE_SELF_TEST:-0}" == 1 ]]; then
        log_stage sync_skipped_selftest "$name" 0 "$(runtime_snapshot "$name")"
      else
        /usr/bin/podman ps --sync --all >/dev/null 2>&1 || true
      fi
      status="$(container_state "$name" 2>/dev/null || printf unknown)"
      if [[ "$status" == exited || "$status" == stopped ]]; then
        log_stage stop_settled "$name" 0 "$(runtime_snapshot "$name")"
        return 0
      fi
    fi
    sleep 0.2
  done
  log_stage stop_unsettled "$name" 1 "$(runtime_snapshot "$name")"
  return 1
}
stop_if_running() {
  local name=$1 running
  running="$($DOCKER inspect -f '{{.State.Running}}' "$name" 2>/dev/null || printf unknown)"
  if [[ "$running" == true ]]; then
    stop_once "$name"
  else
    log_stage rollback_stop_skipped "$name" 0 "$(runtime_snapshot "$name")"
  fi
}
start_if_startable() {
  local name=$1 rc status running
  running="$($DOCKER inspect -f '{{.State.Running}}' "$name" 2>/dev/null || printf unknown)"
  if [[ "$running" == true ]]; then
    log_stage start_already_running "$name" 0 "$(runtime_snapshot "$name")"
    return 0
  fi
  if [[ "${DISPOSABLE_SELF_TEST:-0}" == 1 ]]; then
    log_stage start_sync_skipped_selftest "$name" 0 "$(runtime_snapshot "$name")"
  else
    /usr/bin/podman ps --sync --all >/dev/null 2>&1 || true
  fi
  status="$(container_state "$name" 2>/dev/null || printf unknown)"
  if [[ "$status" != exited && "$status" != stopped && "$status" != created && "$status" != configured ]]; then
    log_stage start_not_startable "$name" 1 "$(runtime_snapshot "$name")"
    return 1
  fi
  set +e
  "$DOCKER" start "$name" >/dev/null
  rc=$?
  set -e
  log_stage start_return "$name" "$rc" "$(runtime_snapshot "$name")"
  return "$rc"
}
pre_rename_startability_probe() {
  local primary=$1 secondary=$2 primary_running secondary_running
  if [[ "${DISPOSABLE_SELF_TEST:-0}" != 1 ]]; then
    prove_rollback_identity || return
    log_database_stage pre_rename_restart_gate 0 rollback_identity_proven
  fi
  start_if_startable "$secondary"
  start_if_startable "$primary"
  for ((start_try = 0; start_try < 50; start_try++)); do
    primary_running="$($DOCKER inspect -f '{{.State.Running}}' "$primary" 2>/dev/null || printf unknown)"
    secondary_running="$($DOCKER inspect -f '{{.State.Running}}' "$secondary" 2>/dev/null || printf unknown)"
    if [[ "$primary_running" == true && "$secondary_running" == true ]]; then
      log_stage startability_running "$primary" 0 "$(runtime_snapshot "$primary")"
      log_stage startability_running "$secondary" 0 "$(runtime_snapshot "$secondary")"
      stop_once "$primary"
      stop_once "$secondary"
      log_stage pre_rename_startability_pass "$primary" 0 "$(runtime_snapshot "$primary")"
      log_stage pre_rename_startability_pass "$secondary" 0 "$(runtime_snapshot "$secondary")"
      return 0
    fi
    sleep 0.1
  done
  log_stage pre_rename_startability_failed "$primary" 1 "$(runtime_snapshot "$primary")"
  log_stage pre_rename_startability_failed "$secondary" 1 "$(runtime_snapshot "$secondary")"
  return 1
}
write_failure_receipt() {
  local rc=$1 rollback_verified=$2 runtime_restarted=$3 status
  if [[ "$rollback_verified" == true ]]; then
    status=FAILED_ROLLED_BACK
  else
    status=FAILED_UNSAFE_STATE
  fi
  {
    printf 'schema_version=2\n'
    printf 'status=%s\n' "$status"
    printf 'phase=%s\n' "$phase"
    printf 'last_gate=%s\n' "$last_gate"
    printf 'exit_code=%s\n' "$rc"
    printf 'rollback_verified=%s\n' "$rollback_verified"
    printf 'runtime_restarted=%s\n' "$runtime_restarted"
    printf 'database_identity_order=production|candidate|rollback\n'
    printf 'started_at=%s\n' "$started_at"
    printf 'finished_at=%s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
    printf 'sensitive_values_recorded=false\n'
    printf 'provider_calls_performed=false\n'
    printf 'stage_log=%s\n' "$STAGE_LOG"
  } >"$ART/swap-failure-receipt.txt"
  chmod 600 "$ART/swap-failure-receipt.txt"
}
rollback() {
  local rc=${1:-1}
  local rollback_verified=false runtime_quiesced=true runtime_restarted=false
  ((rc == 0)) && rc=1
  trap - ERR INT TERM
  set +e
  if [[ "$phase" != preflight ]]; then
    if ! stop_if_running "$N8N"; then
      runtime_quiesced=false
      log_database_stage rollback_stop_n8n_failed 1 "$(runtime_snapshot "$N8N")"
    fi
    if ! stop_if_running "$RUNNER"; then
      runtime_quiesced=false
      log_database_stage rollback_stop_runner_failed 1 "$(runtime_snapshot "$RUNNER")"
    fi
    if [[ "$runtime_quiesced" == true ]]; then
      if restore_database_identity; then
        rollback_verified=true
        if restart_runtime_for_identity rollback; then
          runtime_restarted=true
        else
          log_database_stage rollback_restart_failed 1 identity_proven
        fi
      else
        log_database_stage rollback_restore_failed 1 restart_forbidden
      fi
    else
      log_database_stage rollback_restore_skipped 1 runtime_not_quiesced
    fi
  fi
  write_failure_receipt "$rc" "$rollback_verified" "$runtime_restarted"
  exit "$rc"
}
disposable_self_test() {
  local stable=n8n-single-stop-selftest-stable crash=n8n-single-stop-selftest-crash missing=n8n-single-stop-selftest-missing
  local selftest_log=/tmp/finance-n8n-single-stop-v4-container-selftest.log
  local unstartable_rc
  STAGE_LOG=$selftest_log
  : >"$STAGE_LOG"
  disposable_cleanup() (
    set +e
    for fixture in "$stable" "$crash"; do
      if [[ "$($DOCKER inspect -f '{{.State.Running}}' "$fixture" 2>/dev/null || printf false)" == true ]]; then
        "$DOCKER" stop --time 1 "$fixture" >/dev/null 2>&1 || true
      fi
      "$DOCKER" rm -f "$fixture" >/dev/null 2>&1 || true
    done
    "$DOCKER" rm -f "$missing" >/dev/null 2>&1 || true
  )
  trap disposable_cleanup EXIT
  disposable_cleanup
  "$DOCKER" run -d --name "$stable" --network none --restart unless-stopped --health-cmd /bin/false --health-interval 1s --health-timeout 1s --health-retries 1 --entrypoint /bin/sh "$EXPECTED_IMAGE" -c 'trap "exit 0" TERM; while :; do sleep 1; done' >/dev/null
  "$DOCKER" run -d --name "$crash" --network none --restart unless-stopped --entrypoint /bin/sh "$EXPECTED_IMAGE" -c 'sleep 2; exit 1' >/dev/null
  sleep 1
  stop_once "$stable"
  stop_once "$crash"
  pre_rename_startability_probe "$stable" "$crash"
  [[ "$(container_state "$stable")" == exited ]]
  [[ "$(container_state "$crash")" == exited ]]
  start_if_startable "$stable"
  start_if_startable "$crash"
  [[ "$($DOCKER inspect -f '{{.State.Running}}' "$stable")" == true ]]
  [[ "$($DOCKER inspect -f '{{.State.Running}}' "$crash")" == true ]]
  set +e
  start_if_startable "$missing"
  unstartable_rc=$?
  set -e
  [[ "$unstartable_rc" == 1 ]]
  grep -F 'stage=pre_rename_startability_pass|container=n8n-single-stop-selftest-stable|rc=0' "$selftest_log" >/dev/null
  grep -F 'stage=pre_rename_startability_pass|container=n8n-single-stop-selftest-crash|rc=0' "$selftest_log" >/dev/null
  grep -F 'stage=start_not_startable|container=n8n-single-stop-selftest-missing|rc=1' "$selftest_log" >/dev/null
  stop_once "$stable"
  stop_once "$crash"
  disposable_cleanup
  trap - EXIT
  printf 'selftest=PASS log=%s\n' "$selftest_log"
}
database_state_self_test() {
  local selftest_log=/tmp/finance-n8n-single-stop-v4-state-selftest.log
  STAGE_LOG=$selftest_log
  : >"$STAGE_LOG"
  TEST_FAIL_RENAME=
  TEST_FAIL_TERMINATE=
  TEST_DISCONNECT_IDENTITY=0

  TEST_DATABASE_IDENTITY='1|1|0'
  TEST_RESTARTS=0
  phase=dumped
  forward_database_swap
  [[ "$TEST_DATABASE_IDENTITY" == '1|0|1' ]]
  restart_runtime_for_identity candidate
  [[ "$TEST_RESTARTS" == 1 ]]
  log_database_stage scenario_normal_forward 0 PASS

  TEST_DATABASE_IDENTITY='1|0|1'
  TEST_RESTARTS=0
  TEST_FAIL_RENAME=
  restore_database_identity
  restart_runtime_for_identity rollback
  [[ "$TEST_DATABASE_IDENTITY" == '1|1|0' && "$TEST_RESTARTS" == 1 ]]
  log_database_stage scenario_normal_rollback 0 PASS

  TEST_DATABASE_IDENTITY='1|1|0'
  TEST_RESTARTS=0
  TEST_FAIL_RENAME="$CAND:$PROD"
  phase=dumped
  if forward_database_swap; then
    return 1
  fi
  [[ "$TEST_DATABASE_IDENTITY" == '0|1|1' ]]
  TEST_FAIL_RENAME=
  restore_database_identity
  restart_runtime_for_identity rollback
  [[ "$TEST_DATABASE_IDENTITY" == '1|1|0' && "$TEST_RESTARTS" == 1 ]]
  log_database_stage scenario_partial_forward 0 PASS

  TEST_DATABASE_IDENTITY='1|0|1'
  TEST_RESTARTS=0
  TEST_FAIL_RENAME="$ROLL:$PROD"
  if restore_database_identity; then
    return 1
  fi
  [[ "$TEST_DATABASE_IDENTITY" == '0|1|1' ]]
  if restart_runtime_for_identity rollback; then
    return 1
  fi
  [[ "$TEST_RESTARTS" == 0 ]]
  log_database_stage scenario_partial_rollback 0 PASS

  TEST_DATABASE_IDENTITY='1|1|0'
  TEST_RESTARTS=0
  TEST_FAIL_RENAME=
  TEST_FAIL_TERMINATE=$PROD
  phase=dumped
  if forward_database_swap; then
    return 1
  fi
  [[ "$TEST_DATABASE_IDENTITY" == '1|1|0' ]]
  if restart_runtime_for_identity candidate; then
    return 1
  fi
  [[ "$TEST_RESTARTS" == 0 ]]
  log_database_stage scenario_disconnected_session 0 PASS

  grep -F 'stage=scenario_normal_forward|rc=0|database_state=PASS' "$selftest_log" >/dev/null
  grep -F 'stage=scenario_normal_rollback|rc=0|database_state=PASS' "$selftest_log" >/dev/null
  grep -F 'stage=scenario_partial_forward|rc=0|database_state=PASS' "$selftest_log" >/dev/null
  grep -F 'stage=scenario_partial_rollback|rc=0|database_state=PASS' "$selftest_log" >/dev/null
  grep -F 'stage=scenario_disconnected_session|rc=0|database_state=PASS' "$selftest_log" >/dev/null
  printf 'state_selftest=PASS log=%s\n' "$selftest_log"
}
if [[ "${DISPOSABLE_SWAP_SELF_TEST:-0}" == 1 ]]; then
  database_state_self_test
  exit 0
fi
if [[ "${DISPOSABLE_SELF_TEST:-0}" == 1 ]]; then
  disposable_self_test
  exit 0
fi
trap 'rollback $?' ERR INT TERM

[[ -d "$ART" ]]
[[ "$(stat -c '%a|%U:%G' "$ART")" == '700|root:root' ]]
[[ ! -e "$ART/incident-quiesced-r3.dump" ]]
[[ ! -e "$ART/swap-receipt.txt" ]]
[[ ! -e "$ART/swap-failure-receipt.txt" ]]
[[ ! -e "$ART/post-rename-identity-final.txt" ]]
[[ ! -e "$STAGE_LOG" ]]
sha256sum -c "$ART/SHA256SUMS" >/dev/null
[[ "$($DOCKER inspect -f '{{.Config.Image}}' "$N8N")" == "$EXPECTED_IMAGE" ]]
last_gate=offline_image_version
observed_version="$($DOCKER run --rm --network none --entrypoint n8n "$EXPECTED_IMAGE" --version)"
[[ "$observed_version" == '2.37.10' ]]
[[ "$(q postgres "SELECT count(*) FROM pg_database WHERE datname='$PROD';")" == 1 ]]
[[ "$(q postgres "SELECT count(*) FROM pg_database WHERE datname='$CAND';")" == 1 ]]
[[ "$(q postgres "SELECT count(*) FROM pg_database WHERE datname='$ROLL';")" == 0 ]]
[[ "$(q postgres "SELECT count(*) FROM pg_stat_activity WHERE datname IN ('$PROD','$CAND');")" == 0 ]]
[[ "$(counts "$CAND")" == '69|6|6|0|12' ]]
[[ "$(q "$CAND" 'SELECT count(*),count(DISTINCT id) FROM data_table;')" == '12|12' ]]
[[ "$(q "$CAND" 'SELECT count(*),count(DISTINCT id) FROM project;')" == '1|1' ]]
[[ "$(q "$CAND" 'SELECT count(*),count(DISTINCT id) FROM "user";')" == '1|1' ]]
[[ "$(q "$CAND" 'SELECT count(*),count(DISTINCT id),max(id) FROM migrations;')" == '253|253|253' ]]
IFS='|' read -r cand_pop cand_dup cand_not2x <<<"$(dupe_stats "$CAND")"
[[ "$cand_pop" -gt 0 && "$cand_dup" == 0 ]]
IFS='|' read -r prod_pop prod_dup prod_not2x <<<"$(dupe_stats "$PROD")"
[[ "$prod_pop" -gt 0 && "$prod_not2x" == 0 ]]
[[ "$(canonical_digest "$CAND" "$LP_SQL")" == "$EXPECTED_CANON" ]]
[[ "$(canonical_digest "$PROD" "$INC_SQL")" == "$EXPECTED_CANON" ]]
[[ "$(catalog_digest "$CAND")" == "$EXPECTED_CATALOG" ]]
[[ "$(q "$CAND" "SELECT count(*) FROM execution_entity WHERE status IN ('new','running','waiting');")" == 0 ]]
if pgrep -f '/tmp/run-w12-live-acceptance.sh' >/dev/null 2>&1; then
  exit 75
fi
if [[ "${PREFLIGHT_ONLY:-0}" == 1 ]]; then
  trap - ERR INT TERM
  printf 'preflight=PASS\n'
  exit 0
fi

last_gate=stop_runtime
phase=stopping
stop_once "$N8N"
stop_once "$RUNNER"
last_gate=pre_rename_startability
pre_rename_startability_probe "$N8N" "$RUNNER"
last_gate=quiescence_after_startability
[[ "$(q postgres "SELECT count(*) FROM pg_stat_activity WHERE datname IN ('$PROD','$CAND');")" == 0 ]]
[[ "$(q "$PROD" "SELECT count(*) FROM execution_entity WHERE status IN ('new','running','waiting');")" == 0 ]]
[[ "$(q "$CAND" "SELECT count(*) FROM execution_entity WHERE status IN ('new','running','waiting');")" == 0 ]]
[[ "$(canonical_digest "$CAND" "$LP_SQL")" == "$EXPECTED_CANON" ]]
[[ "$(canonical_digest "$PROD" "$INC_SQL")" == "$EXPECTED_CANON" ]]

"$DOCKER" exec "$PG" rm -f /tmp/n8n-incident-quiesced-r3.dump
"$DOCKER" exec "$PG" pg_dump --format=custom --no-owner --no-acl -U n8n -d "$PROD" --file=/tmp/n8n-incident-quiesced-r3.dump
"$DOCKER" exec "$PG" pg_restore --list /tmp/n8n-incident-quiesced-r3.dump >/dev/null
"$DOCKER" cp "$PG:/tmp/n8n-incident-quiesced-r3.dump" "$ART/incident-quiesced-r3.dump" >/dev/null
"$DOCKER" exec "$PG" rm -f /tmp/n8n-incident-quiesced-r3.dump
chmod 600 "$ART/incident-quiesced-r3.dump"
sha256sum "$ART/incident-quiesced-r3.dump" >"$ART/incident-quiesced-r3.dump.sha256"
chmod 600 "$ART/incident-quiesced-r3.dump.sha256"
sha256sum -c "$ART/incident-quiesced-r3.dump.sha256" >/dev/null
chattr +i "$ART/incident-quiesced-r3.dump" "$ART/incident-quiesced-r3.dump.sha256"
phase=dumped

forward_database_swap
last_gate=post_rename_identity
if identity_output="$(database_identity)"; then
  identity_rc=0
else
  identity_rc=$?
  identity_output=identity_query_failed
fi
{
  printf 'output=%s\n' "$identity_output"
  printf 'exit_code=%s\n' "$identity_rc"
  printf 'order=production|candidate|rollback\n'
  printf 'connection_database=postgres\n'
} >"$ART/post-rename-identity-final.txt"
chmod 600 "$ART/post-rename-identity-final.txt"
[[ "$identity_rc" == 0 && "$identity_output" == '1|0|1' ]]

last_gate=start_runtime
restart_runtime_for_identity candidate
phase=started
last_gate=health_wait
healthy=false
for ((i = 0; i < 120; i++)); do
  if [[ "$($DOCKER inspect -f '{{if .State.Health}}{{.State.Health.Status}}{{end}}' "$N8N" 2>/dev/null || true)" == healthy ]]; then
    healthy=true
    break
  fi
  sleep 2
done
[[ "$healthy" == true ]]
runner_healthy=false
for ((i = 0; i < 30; i++)); do
  if [[ "$($DOCKER inspect -f '{{if .State.Health}}{{.State.Health.Status}}{{end}}' "$RUNNER" 2>/dev/null || true)" == healthy ]]; then
    runner_healthy=true
    break
  fi
  sleep 2
done
[[ "$runner_healthy" == true ]]
last_gate=post_swap_semantic
[[ "$($DOCKER inspect -f '{{.Config.Image}}' "$N8N")" == "$EXPECTED_IMAGE" ]]
[[ "$($DOCKER exec "$N8N" n8n --version)" == '2.37.10' ]]
healthz_sha256="$($DOCKER exec "$N8N" wget -qO- http://127.0.0.1:5678/healthz | sha256sum | cut -d' ' -f1)"
[[ "$healthz_sha256" =~ ^[0-9a-f]{64}$ ]]
[[ "$(counts "$PROD")" == '69|6|6|0|12' ]]
[[ "$(q "$PROD" 'SELECT count(*),count(DISTINCT id) FROM data_table;')" == '12|12' ]]
[[ "$(q "$PROD" 'SELECT count(*),count(DISTINCT id) FROM project;')" == '1|1' ]]
[[ "$(q "$PROD" 'SELECT count(*),count(DISTINCT id) FROM "user";')" == '1|1' ]]
[[ "$(q "$PROD" 'SELECT count(*),count(DISTINCT id),max(id) FROM migrations;')" == '253|253|253' ]]
[[ "$(q "$PROD" 'SELECT count(*),count(DISTINCT "workflowId") FROM shared_workflow;')" == '69|69' ]]
[[ "$(q "$PROD" "SELECT count(*) FROM execution_entity WHERE status IN ('new','running','waiting');")" == 0 ]]
IFS='|' read -r post_pop post_dup post_not2x <<<"$(dupe_stats "$PROD")"
[[ "$post_pop" -gt 0 && "$post_dup" == 0 ]]
[[ "$(canonical_digest "$PROD" "$LP_SQL")" == "$EXPECTED_CANON" ]]
[[ "$(catalog_digest "$PROD")" == "$EXPECTED_CATALOG" ]]
"$DOCKER" exec "$N8N" n8n export:credentials --all --decrypted --output=/dev/null >/dev/null 2>&1
"$DOCKER" exec "$N8N" n8n export:workflow --all --output=/dev/null >/dev/null 2>&1
[[ "$(q "$PROD" "SELECT count(*) FROM \"user\" WHERE \"roleSlug\"='global:owner' AND disabled=false;")" == 1 ]]

final_canon="$(canonical_digest "$PROD" "$LP_SQL")"
final_catalog="$(catalog_digest "$PROD")"
{
  printf 'schema_version=1\n'
  printf 'status=PASS\n'
  printf 'started_at=%s\n' "$started_at"
  printf 'finished_at=%s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
  printf 'production_database=%s\n' "$PROD"
  printf 'rollback_database=%s\n' "$ROLL"
  printf 'n8n_version=2.37.10\n'
  printf 'image=%s\n' "$EXPECTED_IMAGE"
  printf 'counts=69|6|6|0|12\n'
  printf 'data_table=12|12\n'
  printf 'project=1|1\n'
  printf 'user=1|1\n'
  printf 'migrations=253|253|253\n'
  printf 'active_executions=0\n'
  printf 'credential_decryptions=12\n'
  printf 'canonical_sha256=%s\n' "$final_canon"
  printf 'catalog_sha256=%s\n' "$final_catalog"
  printf 'healthz_sha256=%s\n' "$healthz_sha256"
  printf 'quiesced_dump=%s\n' "$ART/incident-quiesced-r3.dump"
  printf 'post_rename_identity=%s|%s\n' "$identity_output" "$identity_rc"
  printf 'exact_database_poststate=true\n'
  printf 'rollback_database_retained=true\n'
  printf 'sensitive_values_recorded=false\n'
  printf 'provider_calls_performed=false\n'
  printf 'stage_log=%s\n' "$STAGE_LOG"
} >"$ART/swap-receipt.txt"
chmod 600 "$ART/swap-receipt.txt"
sha256sum "$ART/incident-quiesced-r3.dump" "$ART/post-rename-identity-final.txt" "$ART/single-stop-controller.log" "$ART/swap-receipt.txt" >"$ART/swap-SHA256SUMS"
chmod 600 "$ART/swap-SHA256SUMS"
sha256sum -c "$ART/swap-SHA256SUMS" >/dev/null
chattr +i "$ART/post-rename-identity-final.txt" "$ART/single-stop-controller.log" "$ART/swap-receipt.txt" "$ART/swap-SHA256SUMS"
phase=verified
trap - ERR INT TERM
printf 'status=PASS receipt=%s\n' "$ART/swap-receipt.txt"
