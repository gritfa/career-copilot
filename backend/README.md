# CareerCopilot Backend

FastAPI 模块化单体后端。依赖真实 PostgreSQL + pgvector 与 Redis（禁止 SQLite fallback）。

## 环境要求

- [uv](https://docs.astral.sh/uv/)（管理 Python 3.12+ 与依赖，本机系统 Python 版本无关）
- PostgreSQL 16 + pgvector、Redis 7（本地用 Docker Compose，见仓库根目录规划）

## 安装

```bash
cd backend
uv sync            # 自动创建 .venv 并安装含 dev 组的全部依赖
cp .env.example .env   # 按需修改，不要提交 .env
```

## 运行

```bash
uv run uvicorn app.main:app --reload --port 8000
```

健康端点：

- `GET /health/live` — 进程存活，不查依赖
- `GET /health/ready` — DB / Redis / Alembic 迁移版本，任一失败返回 503 + 结构化原因
- `GET /health/capabilities` — 能力矩阵；未真实验证的能力一律 `not_verified`

## 数据库迁移

```bash
uv run alembic upgrade head        # 应用迁移（需要可连接的 PostgreSQL）
uv run alembic revision -m "..."   # 新建迁移
uv run alembic current             # 查看当前版本
```

DSN 由 `app/core/config.py` 从环境变量 `DATABASE_URL` 读取。

## 测试与检查

```bash
uv run pytest              # 单元测试（健康端点用 httpx ASGI，不需要真实 DB）
uv run ruff check .        # lint
uv run ruff format .       # 格式化
uv run mypy app            # 类型检查
```

## 目录结构

```
app/
  api/           REST 路由（当前仅健康端点）
  core/          config / errors / logging 横切基础设施
  db/            SQLAlchemy 声明基类
  tasks/         Celery 任务（占位）
  integrations/  供应商适配器（占位；业务层禁止直接引供应商 SDK）
  ...            其余业务模块占位，见 docs/02-architecture.md 第 4 节
alembic/         迁移环境与版本（当前仅空基线迁移）
tests/           pytest 测试
```
