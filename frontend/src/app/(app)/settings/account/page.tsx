"use client";

import { useEffect, useState } from "react";
import { api } from "@/lib/api";
import { handleApiError } from "@/lib/api-error";
import ConfirmDialog from "@/components/ConfirmDialog";
import {
  type AccountDeletionStatus,
  type AuthSession,
  type SessionInfo,
  fmtTime,
  toItems,
} from "@/lib/types";

export default function AccountSettingsPage() {
  const [sessions, setSessions] = useState<AuthSession[] | null>(null);
  const [deletion, setDeletion] = useState<AccountDeletionStatus | null>(null);
  const [error, setError] = useState<string | null>(null);

  const [revokeTarget, setRevokeTarget] = useState<AuthSession | null>(null);
  const [revoking, setRevoking] = useState(false);
  const [loggingOut, setLoggingOut] = useState(false);

  const [deleteDialogOpen, setDeleteDialogOpen] = useState(false);
  const [deleting, setDeleting] = useState(false);
  const [cancelingDeletion, setCancelingDeletion] = useState(false);

  // 变更成功后自增触发重新加载（避免在 effect 内同步调用会 setState 的函数）
  const [reloadKey, setReloadKey] = useState(0);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const [sessionList, current] = await Promise.all([
          api.get<unknown>("/auth/sessions"),
          api.get<SessionInfo>("/auth/session").catch(() => null),
        ]);
        if (cancelled) return;
        setSessions(toItems<AuthSession>(sessionList));
        setDeletion(current?.account_deletion ?? null);
        setError(null);
      } catch (err) {
        if (cancelled) return;
        setSessions([]);
        setError(handleApiError(err));
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [reloadKey]);

  async function confirmRevokeSession() {
    if (!revokeTarget) return;
    setRevoking(true);
    try {
      await api.delete(`/auth/sessions/${revokeTarget.id}`);
      setRevokeTarget(null);
      setReloadKey((k) => k + 1);
    } catch (err) {
      setError(handleApiError(err));
      setRevokeTarget(null);
    } finally {
      setRevoking(false);
    }
  }

  async function logout() {
    setLoggingOut(true);
    try {
      await api.delete("/auth/session");
      window.location.assign("/login");
    } catch (err) {
      setError(handleApiError(err));
      setLoggingOut(false);
    }
  }

  async function requestDeletion() {
    setDeleting(true);
    try {
      const res = await api.post<AccountDeletionStatus | undefined>(
        "/privacy/account-deletion",
      );
      setDeletion(res ?? { status: "pending" });
      setDeleteDialogOpen(false);
    } catch (err) {
      setError(handleApiError(err));
      setDeleteDialogOpen(false);
    } finally {
      setDeleting(false);
    }
  }

  async function cancelDeletion() {
    setCancelingDeletion(true);
    try {
      await api.delete("/privacy/account-deletion");
      setDeletion(null);
      setReloadKey((k) => k + 1);
    } catch (err) {
      setError(handleApiError(err));
    } finally {
      setCancelingDeletion(false);
    }
  }

  const deletionPending = deletion !== null && deletion.status !== "canceled";

  const isCurrent = (s: AuthSession) => s.current ?? s.is_current ?? false;

  return (
    <main className="page">
      <h1>账号设置</h1>
      <p className="muted">管理登录会话与账号。</p>

      {error && <p className="error-text">{error}</p>}

      {deletionPending && (
        <div className="notice">
          <p style={{ lineHeight: 1.7 }}>
            <strong>账号已申请注销。</strong>
            推荐与模型任务已停止；恢复期内（申请后 7 天，至
            {fmtTime(deletion?.recoverable_until)}
            ）可撤销注销，逾期数据将被物理清理且无法恢复。
          </p>
          <button
            type="button"
            className="btn btn-primary"
            style={{ marginTop: 8 }}
            disabled={cancelingDeletion}
            onClick={cancelDeletion}
          >
            {cancelingDeletion ? "处理中…" : "撤销注销"}
          </button>
        </div>
      )}

      <h2>活跃会话</h2>
      {sessions === null ? (
        <p className="muted">加载中…</p>
      ) : sessions.length === 0 ? (
        <p className="muted">暂无会话数据。</p>
      ) : (
        sessions.map((s) => (
          <div className="card" key={s.id}>
            <p style={{ fontSize: 14, lineHeight: 1.7 }}>
              <strong>{isCurrent(s) ? "当前会话" : "其他设备会话"}</strong>
              {isCurrent(s) && (
                <span style={{ color: "var(--color-success)", marginLeft: 8, fontSize: 13 }}>
                  ● 本设备
                </span>
              )}
              <br />
              创建时间：{fmtTime(s.created_at)}
              <br />
              最近活跃：{fmtTime(s.last_seen_at)}
              {s.ip && (
                <>
                  <br />
                  IP：{s.ip}
                </>
              )}
              {s.user_agent && (
                <>
                  <br />
                  设备：<span className="muted">{s.user_agent}</span>
                </>
              )}
            </p>
            {!isCurrent(s) && (
              <button
                type="button"
                className="btn btn-danger"
                style={{ marginTop: 8 }}
                onClick={() => setRevokeTarget(s)}
              >
                撤销此会话
              </button>
            )}
          </div>
        ))
      )}

      <h2>登出</h2>
      <button
        type="button"
        className="btn"
        disabled={loggingOut}
        onClick={logout}
      >
        {loggingOut ? "登出中…" : "登出当前会话"}
      </button>

      <h2>注销账号</h2>
      <div className="card">
        <p style={{ fontSize: 14, lineHeight: 1.7 }}>
          注销后账号进入 <strong>7 天恢复期</strong>
          ：期间推荐与模型任务立即停止，你可以随时撤销注销；恢复期结束后，简历、事实库、推荐等全部数据将被物理清理，无法恢复。
        </p>
        <button
          type="button"
          className="btn btn-danger"
          style={{ marginTop: 8 }}
          disabled={deletionPending}
          onClick={() => setDeleteDialogOpen(true)}
        >
          {deletionPending ? "已申请注销" : "注销账号"}
        </button>
      </div>

      <ConfirmDialog
        open={revokeTarget !== null}
        title="撤销会话"
        danger
        loading={revoking}
        confirmText="确认撤销"
        onConfirm={confirmRevokeSession}
        onCancel={() => setRevokeTarget(null)}
      >
        <p>
          确定撤销该设备的登录会话吗？该设备将立即退出登录，需重新通过邮箱链接登录。
        </p>
      </ConfirmDialog>

      <ConfirmDialog
        open={deleteDialogOpen}
        title="确认注销账号"
        danger
        loading={deleting}
        confirmText="确认注销"
        onConfirm={requestDeletion}
        onCancel={() => setDeleteDialogOpen(false)}
      >
        <p>提交后：</p>
        <ul style={{ paddingLeft: 20, margin: "8px 0" }}>
          <li>推荐与模型任务立即停止；</li>
          <li>
            账号进入 <strong>7 天恢复期</strong>，期间可在本页撤销注销；
          </li>
          <li>恢复期结束后所有数据将被物理清理，无法恢复。</li>
        </ul>
        <p>确定要注销账号吗？</p>
      </ConfirmDialog>
    </main>
  );
}
