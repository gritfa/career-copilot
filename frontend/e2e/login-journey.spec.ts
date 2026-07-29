import { readFileSync } from "node:fs";
import path from "node:path";

import { expect, test } from "@playwright/test";

/**
 * 核心旅程 E2E（真实浏览器 + 真实前后端进程 + Mailpit 真实收信）：
 * 1. 邀请码 + 邮箱免密登录 → 建档引导（含模型服务商授权卡片渲染）
 * 2. 失效 magic link 的失败路径
 *
 * 更长的旅程（上传→事实→方案→推荐→定制→导出→注销）由
 * backend/scripts/acceptance.py 在 API 层全量覆盖（见 docs/acceptance-report.md）。
 */

const MAILPIT = "http://localhost:8025/api/v1";

interface MailpitAddress {
  Address: string;
}
interface MailpitMessage {
  ID: string;
  To: MailpitAddress[];
}

async function fetchMagicLinkToken(email: string): Promise<string | null> {
  for (let i = 0; i < 50; i++) {
    const res = await fetch(`${MAILPIT}/messages`);
    const data = (await res.json()) as { messages?: MailpitMessage[] };
    for (const msg of data.messages ?? []) {
      const hit = msg.To?.some(
        (a) => a.Address.toLowerCase() === email.toLowerCase(),
      );
      if (hit) {
        const detail = (await (
          await fetch(`${MAILPIT}/message/${msg.ID}`)
        ).json()) as { Text: string };
        const match = /token=([A-Za-z0-9_-]+)/.exec(detail.Text);
        if (match) return match[1];
      }
    }
    await new Promise((r) => setTimeout(r, 200));
  }
  return null;
}

test("邀请码+邮箱免密登录 → 首次建档引导（核心旅程）", async ({ page }) => {
  const invite = JSON.parse(
    readFileSync(path.resolve(__dirname, ".state/invite.json"), "utf-8"),
  ) as { code: string };
  const email = `e2e-${Date.now()}@cc-e2e.dev`;

  // 登录页：填邀请码/邮箱，勾选年龄与协议
  await page.goto("/login");
  await expect(
    page.getByRole("heading", { name: "登录 CareerCopilot" }),
  ).toBeVisible();
  await page.getByPlaceholder("请输入邀请码").fill(invite.code);
  await page.getByPlaceholder("you@example.com").fill(email);
  await page.getByRole("checkbox").check();
  await page.getByRole("button", { name: "发送登录链接" }).click();
  await expect(
    page.getByRole("heading", { name: "登录链接已发送到邮箱" }),
  ).toBeVisible();

  // Mailpit 真实收信取 token（同一 SMTP 通道）
  const token = await fetchMagicLinkToken(email);
  expect(token, "Mailpit 应收到 magic link 邮件").toBeTruthy();

  // 点击邮件链接（等价导航）→ 验证 → 会话 cookie → 建档引导
  await page.goto(`/auth/verify?token=${token}`);
  await page.waitForURL("**/onboarding", { timeout: 15_000 });
  await expect(page.getByRole("heading", { name: "首次建档" })).toBeVisible();
  // 步骤①登录时已确认年龄协议 → 可进入步骤②模型授权（默认不授权文案可见）
  await expect(page.getByText("✓ 已完成：你在登录时已确认年满 18 周岁")).toBeVisible();
  await page.getByRole("button", { name: "下一步" }).click();
  await expect(
    page.getByRole("heading", { name: "模型服务商授权" }),
  ).toBeVisible();
  await expect(page.getByText("DeepSeek（深度求索）")).toBeVisible();
});

test("失效 magic link → 明确失败提示，不进入系统", async ({ page }) => {
  await page.goto("/auth/verify?token=invalid-token-e2e-0123456789");
  await expect(
    page.getByRole("heading", { name: "链接已失效，请重新获取" }),
  ).toBeVisible();
  await expect(page.getByRole("link", { name: "返回登录页" })).toBeVisible();
});
