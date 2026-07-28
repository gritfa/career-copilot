# 阶段 1：脚手架、本地基础设施与 CI

请先阅读 `agent-prompts/00-master.md`、`docs/02-architecture.md`、`docs/09-testing-evaluation.md` 和 `docs/11-delivery-plan.md`。

## 目标

在 `D:\个人项目\career-copilot` 建立可复现的 monorepo：FastAPI 后端、Next.js/TypeScript 前端、Docker Compose 基础服务、迁移、健康端点和 GitHub Actions。

## 必须交付

- `backend/`、`frontend/` 的最小可运行应用和清晰模块边界。
- PostgreSQL + pgvector、Redis、Mailpit、开发存储 Adapter 的 Compose 配置。
- SQLAlchemy/Alembic 初始迁移；禁止 SQLite fallback。
- `/health/live`、`/health/ready`、`/health/capabilities`，未验证能力必须为 `not_verified`。
- 类型化配置、`.env.example`、`.gitignore`、结构化安全日志和 request ID。
- CI：后端格式/lint/类型/测试、前端 lint/类型/构建、迁移检查、密钥扫描。
- 本地启动、停止、重建和测试说明。

## 边界

本阶段不实现认证、简历、岗位、模型或 Agent；只建立真实基础设施和端口。不要填写假的“DeepSeek ready”。

## 验证

- 从空 volume 启动 Compose。
- PostgreSQL 扩展和迁移版本可查询。
- Redis 连接通过。
- readiness 在依赖断开时失败，liveness 仍能正确反映进程。
- 前后端生产构建成功。
- CI 工作流语法和本地等价命令通过。

按总控格式交付证据，不要部署云端。
