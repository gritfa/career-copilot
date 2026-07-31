"use client";

import { useState } from "react";
import Link from "next/link";
import { api } from "@/lib/api";
import { handleApiError } from "@/lib/api-error";
import Button from "@/components/ui/Button";
import { Field, Input } from "@/components/ui/form";
import { IconLogo, IconMail } from "@/components/ui/icons";

const EMAIL_RE = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;

/** 认证页统一外壳：全屏渐变背景 + 居中品牌卡片 */
function AuthCard({ children }: { children: React.ReactNode }) {
  return (
    <main className="flex min-h-screen flex-col items-center justify-center bg-gradient-to-b from-indigo-50 via-slate-50 to-slate-50 px-4 py-12">
      <div className="w-full max-w-md">
        <div className="mb-6 flex items-center justify-center gap-2.5">
          <span className="flex h-10 w-10 items-center justify-center rounded-xl bg-primary text-white shadow-sm">
            <IconLogo className="h-6 w-6" />
          </span>
          <span className="text-xl font-bold tracking-tight text-slate-900">
            CareerCopilot
          </span>
        </div>
        <div className="overflow-hidden rounded-xl border border-slate-200 bg-white shadow-card">
          <div className="h-1.5 bg-gradient-to-r from-indigo-500 via-primary to-violet-500" />
          <div className="p-8">{children}</div>
        </div>
        <p className="mt-6 text-center text-xs text-slate-400">
          AI 求职助手：方案、推荐与简历工作台
        </p>
      </div>
    </main>
  );
}

export default function LoginPage() {
  const [inviteCode, setInviteCode] = useState("");
  const [email, setEmail] = useState("");
  const [agreed, setAgreed] = useState(false);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [sent, setSent] = useState(false);

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    setError(null);

    const code = inviteCode.trim();
    const mail = email.trim();
    if (!code) {
      setError("请输入邀请码");
      return;
    }
    if (!EMAIL_RE.test(mail)) {
      setError("请输入有效的邮箱地址");
      return;
    }
    if (!agreed) {
      setError("请先确认已年满 18 周岁并同意用户协议与隐私政策");
      return;
    }

    setLoading(true);
    try {
      // 1) 校验邀请码（不暴露剩余次数）；后端契约字段为 code（docs/04）
      await api.post("/auth/invites/validate", { code });
      // 2) 发送邮箱免密登录链接（需年龄确认与邀请码）；后端契约字段为 age_attested
      await api.post("/auth/magic-links", {
        email: mail,
        invite_code: code,
        age_attested: true,
      });
      setSent(true);
    } catch (err) {
      setError(handleApiError(err, { redirectOn401: false }));
    } finally {
      setLoading(false);
    }
  }

  if (sent) {
    return (
      <AuthCard>
        <div className="mb-4 flex h-12 w-12 items-center justify-center rounded-full bg-success-soft text-success">
          <IconMail className="h-6 w-6" />
        </div>
        <h1 className="text-2xl font-bold tracking-tight text-slate-900">
          登录链接已发送到邮箱
        </h1>
        <p className="mt-3 text-sm leading-relaxed text-slate-600">
          我们已向该邮箱发送了一封包含登录链接的邮件，链接短时有效且只能使用一次。
          请打开邮件并点击链接完成登录；如几分钟内未收到，请检查垃圾邮件或稍后重试。
        </p>
        <Button
          variant="secondary"
          className="mt-6 w-full"
          onClick={() => {
            setSent(false);
            setError(null);
          }}
        >
          返回重新填写
        </Button>
      </AuthCard>
    );
  }

  return (
    <AuthCard>
      <h1 className="text-2xl font-bold tracking-tight text-slate-900">
        登录 CareerCopilot
      </h1>
      <p className="mt-1.5 text-sm text-slate-600">
        使用邀请码和邮箱免密链接登录，无需设置密码。
      </p>

      <form onSubmit={handleSubmit} className="mt-6 space-y-4" noValidate>
        <Field label="邀请码">
          <Input
            type="text"
            value={inviteCode}
            onChange={(e) => setInviteCode(e.target.value)}
            placeholder="请输入邀请码"
            autoComplete="off"
            disabled={loading}
          />
        </Field>

        <Field label="邮箱">
          <Input
            type="email"
            value={email}
            onChange={(e) => setEmail(e.target.value)}
            placeholder="you@example.com"
            autoComplete="email"
            disabled={loading}
          />
        </Field>

        <label className="flex items-start gap-2.5 text-sm leading-relaxed text-slate-600">
          <input
            type="checkbox"
            checked={agreed}
            onChange={(e) => setAgreed(e.target.checked)}
            disabled={loading}
            className="mt-1 h-4 w-4 shrink-0 accent-indigo-600"
          />
          <span>
            我已年满 18 周岁，并同意
            <Link href="/legal/terms" className="link" target="_blank">
              《用户协议》
            </Link>
            与
            <Link href="/legal/privacy" className="link" target="_blank">
              《隐私政策》
            </Link>
          </span>
        </label>

        {error && <p className="text-sm text-danger">{error}</p>}

        <Button type="submit" variant="primary" className="w-full" loading={loading}>
          {loading ? "提交中…" : "发送登录链接"}
        </Button>
      </form>
    </AuthCard>
  );
}
