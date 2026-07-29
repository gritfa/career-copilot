"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { api, API_BASE_URL } from "@/lib/api";
import { handleApiError } from "@/lib/api-error";
import ConfirmDialog from "@/components/ConfirmDialog";
import {
  type Consent,
  PROVIDER_NAMES,
  SCOPE_NAMES,
  fmtTime,
  toItems,
} from "@/lib/types";

interface DataExport {
  id: string;
  status: string;
  error_code: string | null;
  size_bytes: number | null;
  download_url: string | null;
  expires_at: string | null;
  created_at: string;
}

export default function PrivacySettingsPage() {
  const [consents, setConsents] = useState<Consent[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [revokeTarget, setRevokeTarget] = useState<Consent | null>(null);
  const [revoking, setRevoking] = useState(false);
  const [exports, setExports] = useState<DataExport[] | null>(null);
  const [exportError, setExportError] = useState<string | null>(null);
  const [exporting, setExporting] = useState(false);

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

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const data = await api.get<DataExport[]>("/privacy/data-exports");
        if (!cancelled) setExports(data);
      } catch {
        if (!cancelled) setExports([]);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [reloadKey]);

  async function requestExport() {
    setExporting(true);
    setExportError(null);
    try {
      await api.post<DataExport>("/privacy/data-exports");
      setReloadKey((k) => k + 1);
    } catch (err) {
      setExportError(handleApiError(err));
    } finally {
      setExporting(false);
    }
  }

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

      <h2>我的数据导出</h2>
      <div className="card">
        <p style={{ lineHeight: 1.7 }}>
          导出你的全部个人数据（事实库、求职方案、推荐与反馈、分析报告、简历版本及上传原件）
          为一个 ZIP 文件。异步生成，下载链接短时有效，过期自动清理。
          发起导出需要近期登录；若提示需要重新认证，请重新登录后再试。
        </p>
        {exportError && <p className="error-text">{exportError}</p>}
        <button
          type="button"
          className="btn"
          disabled={exporting}
          onClick={requestExport}
        >
          {exporting ? "发起中…" : "导出我的数据"}
        </button>
        {exports !== null && exports.length > 0 && (
          <ul style={{ marginTop: 12, fontSize: 14, lineHeight: 1.9 }}>
            {exports.map((e) => (
              <li key={e.id}>
                {fmtTime(e.created_at)} ·{" "}
                {e.status === "succeeded"
                  ? e.download_url
                    ? "已就绪"
                    : "已过期（文件已清理）"
                  : e.status === "failed"
                    ? "失败"
                    : "生成中…"}
                {e.download_url && (
                  <>
                    {" · "}
                    <a className="link" href={`${API_BASE_URL}${e.download_url}`}>
                      下载 ZIP
                    </a>
                    {e.expires_at && <span className="muted">（{fmtTime(e.expires_at)} 前有效）</span>}
                  </>
                )}
              </li>
            ))}
          </ul>
        )}
      </div>

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
