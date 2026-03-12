#!/usr/bin/env bash
set -u

APP_DIR="$(cd "$(dirname "$0")/.." && pwd)"
RUN_DIR="$APP_DIR/run"
LOG_DIR="$APP_DIR/logs"
SUPERVISOR_PID_FILE="$RUN_DIR/supervisor.pid"
APP_PID_FILE="$RUN_DIR/app.pid"
ENV_FILE="$APP_DIR/.env"
HOST=""
PORT=""
RESTART_DELAY=""
UVICORN_BIN=""

mkdir -p "$RUN_DIR" "$LOG_DIR"

is_pid_running() {
  local pid="$1"
  [[ -n "$pid" ]] && kill -0 "$pid" 2>/dev/null
}

format_mb_from_kb() {
  local kb="$1"
  if [[ -z "$kb" ]]; then
    echo "0.0"
    return 0
  fi
  awk "BEGIN { printf \"%.1f\", $kb / 1024 }"
}

pid_cpu_mem() {
  local pid="$1"
  ps -p "$pid" -o %cpu= -o rss= 2>/dev/null | awk '{print $1, $2}'
}

read_pid() {
  local file="$1"
  [[ -f "$file" ]] || return 1
  local pid
  pid="$(cat "$file" 2>/dev/null || true)"
  [[ "$pid" =~ ^[0-9]+$ ]] || return 1
  echo "$pid"
}

load_env() {
  if [[ -f "$ENV_FILE" ]]; then
    set -a
    # shellcheck disable=SC1090
    source "$ENV_FILE"
    set +a
  fi
}

apply_runtime_defaults() {
  HOST="${HOST:-0.0.0.0}"
  PORT="${PORT:-8000}"
  RESTART_DELAY="${RESTART_DELAY:-2}"
}

resolve_uvicorn() {
  if [[ -x "$APP_DIR/.venv/bin/uvicorn" ]]; then
    UVICORN_BIN="$APP_DIR/.venv/bin/uvicorn"
    return 0
  fi

  if command -v uvicorn >/dev/null 2>&1; then
    UVICORN_BIN="$(command -v uvicorn)"
    return 0
  fi

  echo "uvicorn not found (checked .venv/bin/uvicorn and system PATH)" >> "$LOG_DIR/supervisor.log"
  return 1
}

run_supervisor() {
  cd "$APP_DIR" || exit 1
  load_env
  apply_runtime_defaults
  resolve_uvicorn || exit 1

  local should_stop=0

  on_term() {
    should_stop=1
    local app_pid
    app_pid="$(read_pid "$APP_PID_FILE" || true)"
    if [[ -n "${app_pid:-}" ]] && is_pid_running "$app_pid"; then
      kill "$app_pid" 2>/dev/null || true
    fi
  }

  trap on_term INT TERM

  echo "[$(date '+%F %T')] supervisor started" >> "$LOG_DIR/supervisor.log"

  while [[ "$should_stop" -eq 0 ]]; do
    "$UVICORN_BIN" app.main:app --host "$HOST" --port "$PORT" >> "$LOG_DIR/app.log" 2>&1 &
    local child_pid=$!
    echo "$child_pid" > "$APP_PID_FILE"
    echo "[$(date '+%F %T')] app started pid=$child_pid" >> "$LOG_DIR/supervisor.log"

    set +e
    wait "$child_pid"
    local code=$?
    set -e

    rm -f "$APP_PID_FILE"
    echo "[$(date '+%F %T')] app exited code=$code" >> "$LOG_DIR/supervisor.log"

    if [[ "$should_stop" -eq 1 ]]; then
      break
    fi

    sleep "$RESTART_DELAY"
  done

  rm -f "$SUPERVISOR_PID_FILE"
  echo "[$(date '+%F %T')] supervisor stopped" >> "$LOG_DIR/supervisor.log"
}

start_service() {
  local pid
  pid="$(read_pid "$SUPERVISOR_PID_FILE" || true)"
  if [[ -n "${pid:-}" ]] && is_pid_running "$pid"; then
    echo "service already running (supervisor pid=$pid)"
    return 0
  fi

  nohup "$0" __supervise >> "$LOG_DIR/supervisor.log" 2>&1 &
  local new_pid=$!
  echo "$new_pid" > "$SUPERVISOR_PID_FILE"
  sleep 0.3

  if is_pid_running "$new_pid"; then
    echo "service started (supervisor pid=$new_pid)"
  else
    echo "service failed to start, check $LOG_DIR/supervisor.log"
    return 1
  fi
}

stop_service() {
  local sup_pid
  sup_pid="$(read_pid "$SUPERVISOR_PID_FILE" || true)"

  if [[ -z "${sup_pid:-}" ]] || ! is_pid_running "$sup_pid"; then
    rm -f "$SUPERVISOR_PID_FILE"
    local app_pid
    app_pid="$(read_pid "$APP_PID_FILE" || true)"
    if [[ -n "${app_pid:-}" ]] && is_pid_running "$app_pid"; then
      kill "$app_pid" 2>/dev/null || true
      rm -f "$APP_PID_FILE"
      echo "app process stopped (pid=$app_pid)"
    else
      echo "service already stopped"
    fi
    return 0
  fi

  kill "$sup_pid" 2>/dev/null || true

  for _ in {1..20}; do
    if ! is_pid_running "$sup_pid"; then
      break
    fi
    sleep 0.2
  done

  if is_pid_running "$sup_pid"; then
    kill -9 "$sup_pid" 2>/dev/null || true
  fi

  rm -f "$SUPERVISOR_PID_FILE"

  local app_pid
  app_pid="$(read_pid "$APP_PID_FILE" || true)"
  if [[ -n "${app_pid:-}" ]] && is_pid_running "$app_pid"; then
    kill "$app_pid" 2>/dev/null || true
    rm -f "$APP_PID_FILE"
  fi

  echo "service stopped"
}

status_service() {
  local sup_pid app_pid
  sup_pid="$(read_pid "$SUPERVISOR_PID_FILE" || true)"
  app_pid="$(read_pid "$APP_PID_FILE" || true)"

  if [[ -n "${sup_pid:-}" ]] && is_pid_running "$sup_pid"; then
    echo "supervisor: running (pid=$sup_pid)"
  else
    echo "supervisor: stopped"
  fi

  if [[ -n "${app_pid:-}" ]] && is_pid_running "$app_pid"; then
    echo "app: running (pid=$app_pid)"
    local stats cpu rss_kb mem_mb
    stats="$(pid_cpu_mem "$app_pid" || true)"
    cpu="$(awk '{print $1}' <<< "$stats")"
    rss_kb="$(awk '{print $2}' <<< "$stats")"
    mem_mb="$(format_mb_from_kb "${rss_kb:-0}")"
    echo "app resources: cpu=${cpu:-0.0}% mem=${mem_mb}MB"
  else
    echo "app: stopped"
  fi

  echo "logs: $LOG_DIR/app.log"
}

health_check() {
  if command -v curl >/dev/null 2>&1; then
    if curl -fsS "http://127.0.0.1:${PORT}/api/health" >/dev/null; then
      echo "health: ok"
      return 0
    fi
  fi
  echo "health: failed"
  return 1
}

ensure_started() {
  local sup_pid
  sup_pid="$(read_pid "$SUPERVISOR_PID_FILE" || true)"
  if [[ -n "${sup_pid:-}" ]] && is_pid_running "$sup_pid"; then
    echo "service already running"
    return 0
  fi
  start_service
}

reset_db() {
  stop_service
  rm -f "$APP_DIR/data/resumes.db" "$APP_DIR/data/resumes.db-shm" "$APP_DIR/data/resumes.db-wal"
  echo "database cleared"
  start_service
  status_service
}

case "${1:-}" in
  start)
    load_env
    apply_runtime_defaults
    start_service
    ;;
  stop)
    load_env
    apply_runtime_defaults
    stop_service
    ;;
  restart)
    load_env
    apply_runtime_defaults
    stop_service
    start_service
    ;;
  status)
    load_env
    apply_runtime_defaults
    status_service
    ;;
  logs)
    load_env
    apply_runtime_defaults
    tail -n 120 -f "$LOG_DIR/app.log"
    ;;
  health)
    load_env
    apply_runtime_defaults
    health_check
    ;;
  ensure-started)
    load_env
    apply_runtime_defaults
    ensure_started
    ;;
  reset-db)
    load_env
    apply_runtime_defaults
    reset_db
    ;;
  __supervise)
    run_supervisor
    ;;
  *)
    echo "Usage: $0 {start|stop|restart|status|logs|health|ensure-started|reset-db}"
    exit 1
    ;;
esac
