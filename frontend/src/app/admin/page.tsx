"use client";

/**
 * 只读管理监控页（阶段 8，ADR-001 减配：只读视图 + CLI，无任何写操作 UI）。
 *
 * - 仅 admin 角色可见数据；非管理员后端返回 403，前端提示无权限。
 * - 红线：本页不展示简历正文、联系方式明文（邮箱由后端脱敏）、token。
 * - 管理操作（封禁/额度/重跑）请使用后端 CLI：`uv run python -m app.cli --help`。
 */

import { useEffect, useState } from "react";
import { api, ApiError } from "@/lib/api";
import { fmtTime } from "@/lib/types";

interface Overview {
  users: Record<string, number>;
  recommendations: Record<string, number>;
  feedback: Record<string, number>;
  tasks: Record<string, Record<string, number>>;
  queue: { redis_ok: boolean; celery_queue_depth: number | null };
  usage_30d: {
    by_provider: Record<
      string,
      { calls: number; tokens_in: number; tokens_out: number; amount_estimated: number }
    >;
    total_amount_estimated: number;
  };
}

interface AdminUser {
  id: string;
  email_masked: string;
  role: string;
  status: string;
  created_at: string;
  last_active_at: string | null;
  purge_after: string | null;
  suspended_at: string | null;
}

interface UserList {
  items: AdminUser[];
  total: number;
}

const STATUS_LABEL: Record<string, string> = {
  active: "正常",
  suspended: "已封禁",
  deletion_pending: "注销恢复期",
};

export default function AdminPage() {
  const [overview, setOverview] = useState<Overview | null>(null);
  const [users, setUsers] = useState<UserList | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [forbidden, setForbidden] = useState(false);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const [o, u] = await Promise.all([
          api.get<Overview>("/admin/overview"),
          api.get<UserList>("/admin/users"),
        ]);
        if (cancelled) return;
        setOverview(o);
        setUsers(u);
      } catch (err) {
        if (cancelled) return;
        if (err instanceof ApiError && (err.status === 403 || err.status === 401)) {
          setForbidden(true);
        } else {
          setError(err instanceof Error ? err.message : "加载失败");
        }
      }
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  if (forbidden) {
    return (
      <main className="page">
        <h1>管理监控</h1>
        <p className="error-text">没有访问该页面的权限（仅管理员）。</p>
      </main>
    );
  }

  return (
    <main className="page">
      <h1>管理监控（只读）</h1>
      <p className="muted">
        本页只读。封禁/解封、额度调整、任务重跑等管理操作请使用后端 CLI（
        <code>uv run python -m app.cli --help</code>）。
      </p>
      {error && <p className="error-text">{error}</p>}
      {!overview ? (
        <p className="muted">加载中…</p>
      ) : (
        <>
          <h2>用户</h2>
          <div className="card">
            <p>
              总数 {overview.users.total ?? 0} · 正常 {overview.users.active ?? 0} · 已封禁{" "}
              {overview.users.suspended ?? 0} · 注销恢复期{" "}
              {overview.users.deletion_pending ?? 0} · 管理员 {overview.users.admins ?? 0}
            </p>
          </div>

          <h2>推荐与反馈</h2>
          <div className="card">
            <p>
              推荐总量 {overview.recommendations.total} · 今日 {overview.recommendations.today}
              {" · "}感兴趣 {overview.feedback.interested ?? 0} · 不感兴趣{" "}
              {overview.feedback.not_interested ?? 0}
            </p>
          </div>

          <h2>任务队列健康</h2>
          <div className="card">
            <p>
              Redis：{overview.queue.redis_ok ? "正常" : "异常"} · 队列积压：
              {overview.queue.celery_queue_depth ?? "未知"}
            </p>
            {Object.entries(overview.tasks).map(([name, counts]) => (
              <p key={name} style={{ fontSize: 14 }}>
                {name}：
                {Object.entries(counts).length === 0
                  ? "无记录"
                  : Object.entries(counts)
                      .map(([s, n]) => `${s} ${n}`)
                      .join(" · ")}
              </p>
            ))}
          </div>

          <h2>模型费用（近 30 天）</h2>
          <div className="card">
            <p>估算总额：¥{overview.usage_30d.total_amount_estimated.toFixed(4)}</p>
            {Object.entries(overview.usage_30d.by_provider).map(([provider, row]) => (
              <p key={provider} style={{ fontSize: 14 }}>
                {provider}：{row.calls} 次 · in {row.tokens_in} / out {row.tokens_out} tokens ·
                ¥{row.amount_estimated.toFixed(4)}
              </p>
            ))}
          </div>
        </>
      )}

      <h2>用户列表（邮箱脱敏）</h2>
      {!users ? (
        <p className="muted">加载中…</p>
      ) : (
        <div className="card" style={{ overflowX: "auto" }}>
          <table style={{ width: "100%", fontSize: 14, borderCollapse: "collapse" }}>
            <thead>
              <tr style={{ textAlign: "left" }}>
                <th style={{ padding: 4 }}>邮箱</th>
                <th style={{ padding: 4 }}>角色</th>
                <th style={{ padding: 4 }}>状态</th>
                <th style={{ padding: 4 }}>注册时间</th>
                <th style={{ padding: 4 }}>最近活跃</th>
              </tr>
            </thead>
            <tbody>
              {users.items.map((u) => (
                <tr key={u.id}>
                  <td style={{ padding: 4 }}>{u.email_masked}</td>
                  <td style={{ padding: 4 }}>{u.role}</td>
                  <td style={{ padding: 4 }}>{STATUS_LABEL[u.status] ?? u.status}</td>
                  <td style={{ padding: 4 }}>{fmtTime(u.created_at)}</td>
                  <td style={{ padding: 4 }}>{u.last_active_at ? fmtTime(u.last_active_at) : "—"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </main>
  );
}
