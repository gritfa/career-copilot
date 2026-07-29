import { defineConfig } from "@playwright/test";

/**
 * 阶段 9 E2E（ADR-001 裁剪：核心旅程 1-2 条，Chromium 单浏览器）。
 *
 * 拓扑：真实浏览器 → Next.js(:3901) → FastAPI uvicorn(:8901) → 真实 PG/Redis/Mailpit。
 * - 独立库 careercopilot_e2e（globalSetup 每次重建），不碰 dev 库；Redis 用 db3；
 * - 后端 Celery 走 eager（同一任务代码同步执行），无需独立 worker 进程；
 * - 端口避开 8000/3000，防止与本机 dev 服务冲突。
 *
 * 运行：npm run test:e2e（需 docker compose 基础服务已启动）
 */

export const BACKEND_PORT = 8901;
export const FRONTEND_PORT = 3901;
export const E2E_DATABASE_URL =
  "postgresql+asyncpg://careercopilot:careercopilot_dev@localhost:55432/careercopilot_e2e";

export default defineConfig({
  testDir: "./e2e",
  globalSetup: "./e2e/global-setup.ts",
  timeout: 60_000,
  fullyParallel: false,
  retries: 0,
  reporter: [["list"]],
  use: {
    baseURL: `http://localhost:${FRONTEND_PORT}`,
    trace: "retain-on-failure",
  },
  webServer: [
    {
      command: `uv run uvicorn app.main:app --port ${BACKEND_PORT}`,
      cwd: "../backend",
      url: `http://localhost:${BACKEND_PORT}/health/live`,
      reuseExistingServer: false,
      timeout: 60_000,
      env: {
        DATABASE_URL: E2E_DATABASE_URL,
        REDIS_URL: "redis://localhost:56379/3",
        SMTP_HOST: "localhost",
        SMTP_PORT: "1025",
        ENV: "test",
        STORAGE_DIR: "/tmp/cc-e2e-storage",
        CELERY_TASK_ALWAYS_EAGER: "1",
        FRONTEND_BASE_URL: `http://localhost:${FRONTEND_PORT}`,
        LOG_LEVEL: "WARNING",
      },
    },
    {
      command: `npm run dev -- -p ${FRONTEND_PORT}`,
      url: `http://localhost:${FRONTEND_PORT}/login`,
      reuseExistingServer: false,
      timeout: 120_000,
      env: {
        NEXT_PUBLIC_API_BASE_URL: `http://localhost:${BACKEND_PORT}/api/v1`,
      },
    },
  ],
});
