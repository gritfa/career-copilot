#!/usr/bin/env bash
# =============================================================================
# CareerCopilot 一键 Demo（macOS / Linux）—— 阶段 11 P1
#
# 用法：
#   ./demo.sh          # 启动全套：基础设施 + 迁移 + seed + api/worker/beat/前端
#   ./demo.sh stop     # 停止 api/worker/beat/前端（基础设施容器保留）
#   ./demo.sh status   # 查看各进程/容器状态
#
# 说明（docs/15 P1 第 2 条）：编排采用双脚本（demo.sh + demo.ps1）而非
# docker compose --profile demo 全栈镜像化。原因：Next.js 前端容器化成本过高
# （NEXT_PUBLIC_API_BASE_URL 在构建期固化 + 镜像构建数分钟），与「5 分钟出 demo」
# 目标冲突；基础设施仍由 docker compose 管理。
#
# 前置依赖：docker（macOS 推荐 colima）、uv、node/npm。
# 脚本幂等：重复执行会先停掉旧进程再拉起，seed 不产生重复数据。
# =============================================================================
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DEMO_DIR="$ROOT/.demo"
LOG_DIR="$DEMO_DIR/logs"
PID_DIR="$DEMO_DIR/pids"
API_PORT="${DEMO_API_PORT:-8000}"
FRONTEND_PORT="${DEMO_FRONTEND_PORT:-3000}"

info()  { printf '\033[36m[demo]\033[0m %s\n' "$*"; }
fail()  { printf '\033[31m[demo] 失败：%s\033[0m\n' "$*" >&2; exit 1; }

require_cmd() {
  command -v "$1" >/dev/null 2>&1 || fail "缺少依赖：$1（$2）"
}

# ---------------- 进程管理（幂等；macOS 无 setsid，用进程树递归清理） ----------------

kill_tree() { # $1=pid
  local pid="$1" child
  for child in $(pgrep -P "$pid" 2>/dev/null || true); do
    kill_tree "$child"
  done
  kill "$pid" 2>/dev/null || true
}

stop_proc() { # $1=name
  local pid_file="$PID_DIR/$1.pid"
  if [ -f "$pid_file" ]; then
    local pid
    pid="$(cat "$pid_file")"
    if kill -0 "$pid" 2>/dev/null; then
      info "停止旧进程 $1 (pid $pid)"
      kill_tree "$pid"
      for _ in $(seq 1 20); do kill -0 "$pid" 2>/dev/null || break; sleep 0.5; done
      kill -9 "$pid" 2>/dev/null || true
    fi
    rm -f "$pid_file"
  fi
}

free_port() { # $1=port（幂等兜底：清掉演示端口上的残留监听进程）
  local pids
  pids="$(lsof -ti tcp:"$1" 2>/dev/null || true)"
  if [ -n "$pids" ]; then
    info "端口 $1 被占用（pid: ${pids}），停止残留进程以保证可重复执行"
    for p in $pids; do kill_tree "$p"; done
    sleep 1
  fi
}

start_proc() { # $1=name $2=workdir $3...=command
  local name="$1" workdir="$2"; shift 2
  stop_proc "$name"
  info "启动 ${name}：$*"
  ( cd "$workdir" && exec nohup "$@" >"$LOG_DIR/$name.log" 2>&1 ) &
  echo $! > "$PID_DIR/$name.pid"
}

wait_http() { # $1=url $2=描述 $3=最大秒数
  local url="$1" desc="$2" max="$3" i=0
  until curl -fsS -o /dev/null "$url" 2>/dev/null; do
    i=$((i + 1))
    [ "$i" -ge "$max" ] && fail "$desc 在 ${max}s 内未就绪（${url}），日志见 $LOG_DIR/"
    sleep 1
  done
}

# ---------------- 子命令 ----------------

cmd_status() {
  (cd "$ROOT" && docker compose ps) || true
  for name in api worker beat frontend; do
    local_pid_file="$PID_DIR/$name.pid"
    if [ -f "$local_pid_file" ] && kill -0 "$(cat "$local_pid_file")" 2>/dev/null; then
      echo "$name: running (pid $(cat "$local_pid_file"))"
    else
      echo "$name: stopped"
    fi
  done
}

cmd_stop() {
  for name in frontend beat worker api; do stop_proc "$name"; done
  info "应用进程已停止；基础设施容器保留（需要时执行：docker compose down）"
}

cmd_up() {
  require_cmd curl "系统自带工具"
  require_cmd docker "https://docs.docker.com（macOS 推荐 brew install colima docker）"
  require_cmd uv "https://docs.astral.sh/uv/"
  require_cmd npm "https://nodejs.org（Node 20+）"

  mkdir -p "$LOG_DIR" "$PID_DIR"

  # 0) docker daemon（macOS colima 场景自动拉起）
  if ! docker info >/dev/null 2>&1; then
    if command -v colima >/dev/null 2>&1; then
      info "docker daemon 未运行，尝试 colima start ..."
      colima start
    else
      fail "docker daemon 未运行，请先启动 Docker"
    fi
  fi

  # 1) 环境变量：首次运行从 example 生成（端口等保持一致）
  [ -f "$ROOT/.env" ] || { cp "$ROOT/.env.example" "$ROOT/.env"; info "已生成 .env（来自 .env.example）"; }
  [ -f "$ROOT/backend/.env" ] || { cp "$ROOT/backend/.env.example" "$ROOT/backend/.env"; info "已生成 backend/.env（来自 example）"; }

  # 2) 基础设施：postgres / redis / mailpit
  info "启动基础服务（docker compose up -d）..."
  (cd "$ROOT" && docker compose up -d)
  info "等待容器健康检查 ..."
  for c in careercopilot-postgres careercopilot-redis careercopilot-mailpit; do
    i=0
    until [ "$(docker inspect -f '{{.State.Health.Status}}' "$c" 2>/dev/null)" = "healthy" ]; do
      i=$((i + 1)); [ "$i" -ge 60 ] && fail "容器 $c 未在 60s 内变为 healthy"
      sleep 1
    done
  done

  # 3) 后端依赖 + 数据库迁移
  info "安装后端依赖（uv sync）并执行迁移 ..."
  (cd "$ROOT/backend" && uv sync >/dev/null && uv run alembic upgrade head)

  # 4) 种子数据（幂等；演示账号走正常邀请码 + Mailpit magic link 注册，无后门）
  info "写入演示种子数据（幂等）..."
  (cd "$ROOT/backend" && uv run python scripts/seed_demo.py)

  # 5) 应用进程：API + Celery worker + Celery beat + 前端
  free_port "$API_PORT"
  free_port "$FRONTEND_PORT"
  start_proc api "$ROOT/backend" uv run uvicorn app.main:app --host 127.0.0.1 --port "$API_PORT"
  start_proc worker "$ROOT/backend" uv run celery -A app.tasks.celery_app worker --loglevel info
  start_proc beat "$ROOT/backend" uv run celery -A app.tasks.celery_app beat --loglevel info \
    --schedule "$DEMO_DIR/celerybeat-schedule"
  if [ ! -d "$ROOT/frontend/node_modules" ]; then
    info "安装前端依赖（npm install，首次较慢）..."
    (cd "$ROOT/frontend" && npm install >"$LOG_DIR/npm-install.log" 2>&1)
  fi
  start_proc frontend "$ROOT/frontend" npm run dev -- --port "$FRONTEND_PORT"

  info "等待 API 与前端就绪 ..."
  wait_http "http://localhost:$API_PORT/health/ready" "后端 API" 60
  wait_http "http://localhost:$FRONTEND_PORT" "前端" 120

  cat <<EOF

============================ CareerCopilot Demo 已就绪 ============================
前端        : http://localhost:$FRONTEND_PORT
后端 API    : http://localhost:${API_PORT}（健康检查 /health/ready）
Mailpit     : http://localhost:8025（demo 登录邮件在这里收）

演示账号    : demo@careercopilot-demo.dev
登录邀请码  : DEMO-LOGIN-2026

登录步骤（正常流程，无认证后门）：
  1. 打开 http://localhost:$FRONTEND_PORT/login，填演示邮箱和邀请码，请求登录链接
  2. 打开 Mailpit http://localhost:8025，点开最新邮件里的登录链接
  3. 登录后到「推荐」页查看种子推荐（岗位均带「合成示例」徽标）

日志        : $LOG_DIR/
停止        : ./demo.sh stop
===================================================================================
EOF
}

case "${1:-up}" in
  up) cmd_up ;;
  stop) cmd_stop ;;
  status) cmd_status ;;
  *) fail "未知子命令：$1（支持 up / stop / status）" ;;
esac
