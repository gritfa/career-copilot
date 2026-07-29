import { execSync } from "node:child_process";
import { mkdirSync, writeFileSync } from "node:fs";
import path from "node:path";

import { E2E_DATABASE_URL } from "../playwright.config";

const BACKEND_DIR = path.resolve(__dirname, "../../backend");

/**
 * E2E 前置：重建 careercopilot_e2e 空库 → Alembic 迁移 head → CLI 创建邀请码。
 * 邀请码明文写入 e2e/.state/invite.json 供测试用例读取（不入库、不提交）。
 */
export default async function globalSetup(): Promise<void> {
  const env = { ...process.env, DATABASE_URL: E2E_DATABASE_URL };

  const recreate = [
    "import psycopg",
    "conn = psycopg.connect('host=localhost port=55432 user=careercopilot" +
      " password=careercopilot_dev dbname=careercopilot', autocommit=True)",
    "conn.execute('DROP DATABASE IF EXISTS careercopilot_e2e WITH (FORCE)')",
    "conn.execute('CREATE DATABASE careercopilot_e2e OWNER careercopilot')",
  ].join("; ");
  execSync(`uv run python -c "${recreate}"`, { cwd: BACKEND_DIR, stdio: "inherit" });
  execSync("uv run alembic upgrade head", { cwd: BACKEND_DIR, env, stdio: "inherit" });

  const out = execSync("uv run python -m app.cli invite create --max-uses 10", {
    cwd: BACKEND_DIR,
    env,
  }).toString();
  const lines = out.trim().split("\n");
  const invite = JSON.parse(lines[lines.length - 1]);
  if (!invite.code) {
    throw new Error(`CLI invite create 未返回邀请码: ${out}`);
  }

  const stateDir = path.resolve(__dirname, ".state");
  mkdirSync(stateDir, { recursive: true });
  writeFileSync(path.join(stateDir, "invite.json"), JSON.stringify(invite));
}
