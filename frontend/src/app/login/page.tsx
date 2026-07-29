"use client";

import { useState } from "react";
import Link from "next/link";
import { api } from "@/lib/api";
import { handleApiError } from "@/lib/api-error";

const EMAIL_RE = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;

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
      <main className="page">
        <h1>登录链接已发送到邮箱</h1>
        <p style={{ margin: "12px 0", lineHeight: 1.7 }}>
          我们已向该邮箱发送了一封包含登录链接的邮件，链接短时有效且只能使用一次。
          请打开邮件并点击链接完成登录；如几分钟内未收到，请检查垃圾邮件或稍后重试。
        </p>
        <button
          type="button"
          className="btn"
          onClick={() => {
            setSent(false);
            setError(null);
          }}
        >
          返回重新填写
        </button>
      </main>
    );
  }

  return (
    <main className="page" style={{ maxWidth: 480 }}>
      <h1>登录 CareerCopilot</h1>
      <p className="muted">使用邀请码和邮箱免密链接登录，无需设置密码。</p>

      <form onSubmit={handleSubmit} style={{ marginTop: 24 }} noValidate>
        <label className="field">
          <span>邀请码</span>
          <input
            className="input"
            type="text"
            value={inviteCode}
            onChange={(e) => setInviteCode(e.target.value)}
            placeholder="请输入邀请码"
            autoComplete="off"
            disabled={loading}
          />
        </label>

        <label className="field">
          <span>邮箱</span>
          <input
            className="input"
            type="email"
            value={email}
            onChange={(e) => setEmail(e.target.value)}
            placeholder="you@example.com"
            autoComplete="email"
            disabled={loading}
          />
        </label>

        <label
          style={{
            display: "flex",
            gap: 8,
            alignItems: "flex-start",
            fontSize: 14,
            lineHeight: 1.6,
            margin: "16px 0",
          }}
        >
          <input
            type="checkbox"
            checked={agreed}
            onChange={(e) => setAgreed(e.target.checked)}
            disabled={loading}
            style={{ marginTop: 3 }}
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

        {error && <p className="error-text">{error}</p>}

        <button
          type="submit"
          className="btn btn-primary"
          disabled={loading}
          style={{ width: "100%" }}
        >
          {loading ? "提交中…" : "发送登录链接"}
        </button>
      </form>
    </main>
  );
}
