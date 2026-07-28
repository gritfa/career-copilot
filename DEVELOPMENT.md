# CareerCopilot 本地开发指南

本文档说明如何在本地（macOS + colima）启动、停止、重建基础设施并运行测试。

## 前置条件

- macOS，Docker runtime 使用 [colima](https://github.com/abiosoft/colima)（或 Docker Desktop / OrbStack）
- Python 3.12 + [uv](https://docs.astral.sh/uv/)（后端）
- Node.js 20 + npm（前端）
- GNU Make

## 1. 启动基础服务

```bash
# macOS + colima：先启动 Docker runtime
colima start

# （可选）创建 .env，不建则使用开发默认值
cp .env.example .env

# 启动 PostgreSQL(pgvector) / Redis / Mailpit
make up            # 等价于 docker compose up -d

# 查看状态与健康检查
make ps
```

服务与端口：

| 服务 | 镜像 | 端口 | 说明 |
|---|---|---|---|
| postgres | pgvector/pgvector:pg16 | 5432 | 库名 `careercopilot`，用户/密码见 `.env.example` |
| redis | redis:7-alpine | 6379 | 开发默认无密码 |
| mailpit | axllent/mailpit | 1025 / 8025 | SMTP 捕获 / Web UI: http://localhost:8025 |

## 2. 停止 / 重启 / 重建

```bash
make down       # 停止并移除容器（保留数据 volume）
make restart    # 重启
make rebuild    # 彻底重建：删除数据 volume 后重启（会清空数据库！）
make logs       # 跟踪日志
```

## 3. 运行应用（前后端各开一个终端）

```bash
make dev        # 启动基础服务并打印下面两条命令

# 终端 A：后端
cd backend && uv sync && uv run fastapi dev

# 终端 B：前端
cd frontend && npm ci && npm run dev
```

## 4. 测试与检查

```bash
# 后端
make backend-lint     # cd backend && uv run ruff check .
make backend-test     # cd backend && uv run pytest

# 前端
make frontend-lint    # cd frontend && npm run lint
make frontend-build   # cd frontend && npm run build
```

CI（`.github/workflows/ci.yml`）跑同样的命令，另加 gitleaks 密钥扫描；backend job 内已挂 pgvector/pg16 与 redis 服务容器，供后续阶段的迁移检查和集成测试使用。

## 5. 常见问题

- **`docker compose` 报 `Cannot connect to the Docker daemon`**：colima 未启动，先 `colima start`。
- **端口被占用**：在 `.env` 里改 `POSTGRES_PORT` / `REDIS_PORT` / `MAILPIT_*_PORT`。
- **想换数据库密码**：改 `.env` 后需要 `make rebuild`（Postgres 只在初始化空 volume 时读取密码）。

## 6. 本机验证状态

诚实标注：以下条目区分「已验证」和「not_verified」，未验证的不要当作可用。

| 项目 | 状态 | 说明 |
|---|---|---|
| `docker-compose.yml` YAML 语法 | 已验证 | 编写机器上无 docker，改用 `python3 + pyyaml` 解析通过（见下方命令） |
| `.github/workflows/ci.yml` YAML 语法 | 已验证 | `python3 + pyyaml` 解析通过 |
| `docker compose config -q` | not_verified | 编写机器上 docker 不可用，未执行；请在装有 docker 的机器上运行确认 |
| `docker compose up -d` 从空 volume 启动 | not_verified | 同上 |
| postgres/redis/mailpit healthcheck 转 healthy | not_verified | 同上 |
| CI 三个 job 在 GitHub Actions 实际运行 | not_verified | 依赖 backend/、frontend/ 由并行 agent 创建完成后推送触发 |
| `uv sync / ruff / pytest`、`npm ci / lint / build` 本地执行 | not_verified | backend/、frontend/ 由其他 agent 并行创建，本文档只引用其标准命令 |

本机已执行的校验命令：

```bash
python3 -c "import yaml; yaml.safe_load(open('docker-compose.yml')); print('compose yaml ok')"
python3 -c "import yaml; yaml.safe_load(open('.github/workflows/ci.yml')); print('ci yaml ok')"
```

## 本机端口占用说明（2026-07-28）

本机 5432 被 Homebrew postgresql@16（税务项目在用，勿动）、6379 被 ssh 隧道占用。
career-copilot 通过根目录 `.env` 改用：PostgreSQL → **55432**，Redis → **56379**。
已实测通过：alembic upgrade head、/health/live 200、/health/ready 200（DB+迁移+Redis 全 ok）、pgvector 0.8.5。
