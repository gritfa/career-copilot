"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { api } from "@/lib/api";
import { handleApiError } from "@/lib/api-error";
import ConfirmDialog from "@/components/ConfirmDialog";
import {
  type Consent,
  PROVIDER_NAMES,
  SCOPE_NAMES,
  fmtTime,
  toItems,
} from "@/lib/types";

export default function PrivacySettingsPage() {
  const [consents, setConsents] = useState<Consent[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [revokeTarget, setRevokeTarget] = useState<Consent | null>(null);
  const [revoking, setRevoking] = useState(false);

  // 变更成功后自增触发重新加载（避免在 effect 内同步调用会 setState 的函数）
  const [reloadKey, setReloadKey] = useState(0);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const data = await api.get<unknown>("/consents");
        if (cancelled) return;
        setConsents(toItems<Consent>(data));
        setError(null);
      } catch (err) {
        if (cancelled) return;
        setConsents([]);
        setError(handleApiError(err));
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [reloadKey]);

  async function confirmRevoke() {
    if (!revokeTarget) return;
    setRevoking(true);
    try {
      await api.delete(`/consents/${revokeTarget.id}`);
      setRevokeTarget(null);
      setReloadKey((k) => k + 1);
    } catch (err) {
      setError(handleApiError(err));
      setRevokeTarget(null);
    } finally {
      setRevoking(false);
    }
  }

  const isActive = (c: Consent) =>
    !c.revoked_at && c.status !== "revoked" && c.status !== "withdrawn";

  return (
    <main className="page">
      <h1>隐私设置</h1>
      <p className="muted">
        管理模型服务商授权。撤回后新的模型调用立即停止（不追溯已合法发生的调用）。
      </p>

      {error && <p className="error-text">{error}</p>}

      <h2>模型服务商授权</h2>

      {consents === null ? (
        <p className="muted">加载中…</p>
      ) : consents.length === 0 ? (
        <div className="card">
          <p style={{ lineHeight: 1.7 }}>
            暂无授权记录。未授权时仅可使用脱敏/本地规则的受限功能，可前往
            <Link href="/onboarding" className="link">
              建档流程
            </Link>
            完成授权。
          </p>
        </div>
      ) : (
        consents.map((c) => (
          <div className="card" key={c.id}>
            <h3 style={{ fontSize: 16, marginBottom: 8 }}>
              {PROVIDER_NAMES[c.provider] ?? c.provider}
              <span
                style={{
                  fontSize: 13,
                  fontWeight: "normal",
                  marginLeft: 8,
                  color: isActive(c) ? "#16a34a" : "#dc2626",
                }}
              >
                {isActive(c) ? "已授权" : "已撤回"}
              </span>
            </h3>
            <p style={{ fontSize: 14, lineHeight: 1.7 }}>
              数据范围：{SCOPE_NAMES[c.scope] ?? c.scope}
              <br />
              告知版本：{c.notice_version ?? "—"}
              <br />
              授权时间：{fmtTime(c.granted_at)}
              {!isActive(c) && (
                <>
                  <br />
                  撤回时间：{fmtTime(c.revoked_at)}
                </>
              )}
            </p>
            {isActive(c) && (
              <button
                type="button"
                className="btn btn-danger"
                style={{ marginTop: 8 }}
                onClick={() => setRevokeTarget(c)}
              >
                撤回授权
              </button>
            )}
          </div>
        ))
      )}

      <ConfirmDialog
        open={revokeTarget !== null}
        title="撤回授权"
        danger
        loading={revoking}
        confirmText="确认撤回"
        onConfirm={confirmRevoke}
        onCancel={() => setRevokeTarget(null)}
      >
        <p>
          确定撤回对
          {revokeTarget
            ? PROVIDER_NAMES[revokeTarget.provider] ?? revokeTarget.provider
            : ""}
          的授权吗？
        </p>
        <p style={{ marginTop: 8 }}>
          撤回后，新的模型调用将立即停止，相关功能降级为脱敏/本地规则的受限模式；
          已合法发生的调用不受追溯。你之后可以随时重新授权。
        </p>
      </ConfirmDialog>
    </main>
  );
}
