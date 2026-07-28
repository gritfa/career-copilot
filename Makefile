# CareerCopilot 常用开发命令
# macOS + colima 场景：先 `colima start` 再执行 make up
.PHONY: help up down restart logs ps rebuild backend-test backend-lint frontend-build frontend-lint dev clean

help: ## 显示所有可用目标
	@grep -E '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-16s\033[0m %s\n", $$1, $$2}'

# ---------- 基础服务 (Docker Compose) ----------

up: ## 启动 postgres / redis / mailpit（后台）
	docker compose up -d

down: ## 停止并移除容器（保留数据 volume）
	docker compose down

restart: down up ## 重启所有基础服务

logs: ## 跟踪查看所有服务日志
	docker compose logs -f

ps: ## 查看服务状态与健康检查
	docker compose ps

rebuild: ## 彻底重建：删除容器和数据 volume 后重新启动（会清空数据库！）
	docker compose down -v
	docker compose up -d

# ---------- 后端 (backend/, uv) ----------

backend-test: ## 运行后端测试
	cd backend && uv run pytest

backend-lint: ## 后端 lint (ruff)
	cd backend && uv run ruff check .

# ---------- 前端 (frontend/, npm) ----------

frontend-build: ## 前端生产构建
	cd frontend && npm run build

frontend-lint: ## 前端 lint
	cd frontend && npm run lint

# ---------- 开发 ----------

dev: up ## 启动基础服务，并提示前后端 dev 命令（前后端由各自终端启动）
	@echo ""
	@echo "基础服务已启动。请在两个终端分别运行："
	@echo "  后端: cd backend && uv run fastapi dev"
	@echo "  前端: cd frontend && npm run dev"
	@echo "Mailpit UI: http://localhost:8025"

clean: ## 停止服务并删除数据 volume（等同 docker compose down -v）
	docker compose down -v
