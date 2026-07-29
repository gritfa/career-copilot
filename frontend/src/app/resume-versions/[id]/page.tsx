"use client";

import { useCallback, useEffect, useState } from "react";
import Link from "next/link";
import { useParams } from "next/navigation";
import { API_BASE_URL, api, ApiError } from "@/lib/api";
import { handleApiError } from "@/lib/api-error";
import {
  CHANGE_TYPE_LABELS,
  EXPORT_ERROR_LABELS,
  RESUME_VERSION_STATUS_LABELS,
  SECTION_KIND_LABELS,
  TAILOR_ERROR_LABELS,
  type ResumeExport,
  type ResumeVersion,
  isActiveExport,
  shortFactRef,
} from "@/lib/resume-types";

/**
 * 定制简历版本页（阶段 7）：预览 → 确认 → DOCX/PDF 导出 → 限时下载。
 *
 * - 每条内容展示其事实引用（fact_ids）；每处 AI 调整展示理由与岗位证据；
 * - 合成实现产出醒目标注 not_verified；
 * - 下载链接短时有效，过期后文件即被清理，需要重新导出；
 * - 完整逐项编辑器按 ADR-001 推迟（可通过 API PATCH 编辑）。
 */

type PageState =
  | { kind: "loading" }
  | { kind: "error"; reason: string }
  | { kind: "ready"; version: ResumeVersion };

export default function ResumeVersionPage() {
  const params = useParams<{ id: string }>();
  const id = typeof params?.id === "string" ? params.id : "";
  const [state, setState] = useState<PageState>({ kind: "loading" });

  const reload = useCallback(async () => {
    try {
      const version = await api.get<ResumeVersion>(
        `/resume-versions/${encodeURIComponent(id)}`,
      );
      setState({ kind: "ready", version });
    } catch (err) {
      setState({ kind: "error", reason: handleApiError(err) });
    }
  }, [id]);

  useEffect(() => {
    if (id) void reload();
  }, [id, reload]);

  return (
    <main className="page">
      <p style={{ marginBottom: 12 }}>
        {state.kind === "ready" && state.version.recommendation_id ? (
          <Link
            href={`/recommendations/${state.version.recommendation_id}`}
            className="link"
          >
            ← 返回岗位详情
          </Link>
        ) : (
          <Link href="/recommendations" className="link">
            ← 返回每日推荐
          </Link>
        )}
      </p>

      {state.kind === "loading" ? <p className="muted">加载中…</p> : null}
      {state.kind === "error" ? <p className="error-text">{state.reason}</p> : null}
      {state.kind === "ready" ? (
        <VersionBody version={state.version} onChanged={() => void reload()} />
      ) : null}
    </main>
  );
}

function VersionBody({
  version,
  onChanged,
}: {
  version: ResumeVersion;
  onChanged: () => void;
}) {
  return (
    <>
      <div
        style={{
          display: "flex",
          justifyContent: "space-between",
          alignItems: "flex-start",
          gap: 8,
          flexWrap: "wrap",
        }}
      >
        <h1>定制简历</h1>
        <span
          className={
            version.status === "confirmed"
              ? "badge badge-ok"
              : version.status === "failed"
                ? "badge badge-fail"
                : "badge badge-busy"
          }
        >
          {RESUME_VERSION_STATUS_LABELS[version.status]}
        </span>
      </div>
      <p className="muted" style={{ marginTop: 4 }}>
        模板：标准单栏 · 版本 {shortFactRef(version.id)}
        {version.edited_by_user_at ? " · 已人工编辑" : ""}
        {!version.verified ? " · 合成结果（模型未真实验证）" : ""}
      </p>

      {version.status === "failed" ? (
        <div className="card">
          <p className="error-text">
            {TAILOR_ERROR_LABELS[version.error_code ?? ""] ??
              "定制简历生成失败，可回到岗位详情重试。"}
          </p>
        </div>
      ) : null}

      {version.content ? <ContentPreview version={version} /> : null}
      {version.changes.length > 0 ? <ChangesList version={version} /> : null}

      <ActionsSection version={version} onChanged={onChanged} />
    </>
  );
}

/** 内容预览：每条内容都带事实引用（无引用的内容不可能通过服务端校验） */
function ContentPreview({ version }: { version: ResumeVersion }) {
  const content = version.content;
  if (!content) return null;
  return (
    <>
      <h2>内容预览</h2>
      <div className="card">
        {content.target_job_title ? (
          <p style={{ fontSize: 14 }}>
            目标岗位：
            {content.target_company ? `${content.target_company} · ` : ""}
            {content.target_job_title}
          </p>
        ) : null}
        {content.sections.map((section) => (
          <div key={section.kind} style={{ marginTop: 12 }}>
            <strong style={{ fontSize: 14 }}>
              {section.title || SECTION_KIND_LABELS[section.kind] || section.kind}
            </strong>
            {section.items.length === 0 ? (
              <p className="muted" style={{ marginTop: 4 }}>
                本节暂无已确认内容。
              </p>
            ) : (
              <ul style={{ margin: "4px 0 0 18px", lineHeight: 1.8, fontSize: 14 }}>
                {section.items.map((item, i) => (
                  <li key={i}>
                    {item.text}
                    <span className="muted" style={{ fontSize: 12 }}>
                      （事实 {item.fact_ids.map(shortFactRef).join("、")}）
                    </span>
                  </li>
                ))}
              </ul>
            )}
          </div>
        ))}
        <p className="muted" style={{ marginTop: 12, fontSize: 13 }}>
          全部内容均来自你已确认的事实；逐项编辑器将在后续版本开放（当前可通过
          API 编辑草稿）。
        </p>
      </div>
    </>
  );
}

/** 调整明细：每处调整的类型、理由、事实引用与岗位原文证据（可追溯） */
function ChangesList({ version }: { version: ResumeVersion }) {
  return (
    <>
      <h2>调整明细</h2>
      <div className="card">
        {version.changes.map((change, i) => (
          <div
            key={i}
            style={{
              borderTop: i > 0 ? "1px solid rgba(127,127,127,0.25)" : undefined,
              marginTop: i > 0 ? 10 : 0,
              paddingTop: i > 0 ? 10 : 0,
            }}
          >
            <p style={{ fontSize: 14 }}>
              <span className="badge badge-ok">
                {CHANGE_TYPE_LABELS[change.change_type] ?? change.change_type}
              </span>{" "}
              {SECTION_KIND_LABELS[change.section] ?? change.section}
              {change.after ? `：${change.after}` : ""}
            </p>
            <p className="muted" style={{ marginTop: 4, fontSize: 13 }}>
              理由：{change.reason}
              {change.fact_ids.length > 0
                ? `（事实 ${change.fact_ids.map(shortFactRef).join("、")}）`
                : ""}
            </p>
            {change.job_span ? <div className="evidence">{change.job_span}</div> : null}
          </div>
        ))}
      </div>
    </>
  );
}

/** 确认 + 导出 + 限时下载 */
function ActionsSection({
  version,
  onChanged,
}: {
  version: ResumeVersion;
  onChanged: () => void;
}) {
  const [confirming, setConfirming] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [exportJob, setExportJob] = useState<ResumeExport | null>(null);
  const [exporting, setExporting] = useState(false);
  const [pollTick, setPollTick] = useState(0);

  // 导出进行中：轮询导出状态
  useEffect(() => {
    if (!isActiveExport(exportJob)) return;
    const timer = setTimeout(async () => {
      try {
        const latest = await api.get<ResumeExport>(
          `/resume-exports/${encodeURIComponent(exportJob!.id)}`,
        );
        setExportJob(latest);
      } catch {
        setPollTick((t) => t + 1);
      }
    }, 2000);
    return () => clearTimeout(timer);
  }, [exportJob, pollTick]);

  async function confirm() {
    setConfirming(true);
    setError(null);
    try {
      await api.post<ResumeVersion>(
        `/resume-versions/${encodeURIComponent(version.id)}/confirm`,
      );
      onChanged();
    } catch (err) {
      if (err instanceof ApiError && err.code === "FACT_REFERENCE_INVALID") {
        setError("内容引用的事实已变更，无法确认；请重新生成。");
      } else {
        setError(handleApiError(err));
      }
    } finally {
      setConfirming(false);
    }
  }

  async function doExport(format: "docx" | "pdf") {
    setExporting(true);
    setError(null);
    try {
      const created = await api.post<ResumeExport>(
        `/resume-versions/${encodeURIComponent(version.id)}/exports`,
        { format },
      );
      setExportJob(created);
    } catch (err) {
      setError(handleApiError(err));
    } finally {
      setExporting(false);
    }
  }

  if (version.status !== "draft" && version.status !== "confirmed") return null;

  return (
    <>
      <h2>确认与导出</h2>
      <div className="card">
        {version.status === "draft" ? (
          <div style={{ display: "flex", gap: 8, alignItems: "center", flexWrap: "wrap" }}>
            <button
              type="button"
              className="btn btn-primary"
              onClick={() => void confirm()}
              disabled={confirming}
            >
              确认最终内容
            </button>
            <span className="muted">确认后才能导出 DOCX/PDF；确认前会再次校验事实引用。</span>
          </div>
        ) : (
          <div style={{ display: "flex", gap: 8, alignItems: "center", flexWrap: "wrap" }}>
            <button
              type="button"
              className="btn btn-primary"
              onClick={() => void doExport("docx")}
              disabled={exporting || isActiveExport(exportJob)}
            >
              导出 DOCX
            </button>
            <button
              type="button"
              className="btn btn-primary"
              onClick={() => void doExport("pdf")}
              disabled={exporting || isActiveExport(exportJob)}
            >
              导出 PDF
            </button>
            <span className="muted">下载链接短时有效，过期后文件自动清理，可重新导出。</span>
          </div>
        )}

        {exportJob ? <ExportStatus exportJob={exportJob} /> : null}

        {error ? (
          <p className="error-text" style={{ marginTop: 8 }}>
            {error}
          </p>
        ) : null}
      </div>
    </>
  );
}

function ExportStatus({ exportJob }: { exportJob: ResumeExport }) {
  if (exportJob.status === "queued" || exportJob.status === "running") {
    return (
      <p className="muted" style={{ marginTop: 8 }}>
        正在生成 {exportJob.format.toUpperCase()} 文件…
      </p>
    );
  }
  if (exportJob.status === "failed") {
    return (
      <p className="error-text" style={{ marginTop: 8 }}>
        {EXPORT_ERROR_LABELS[exportJob.error_code ?? ""] ?? "导出失败，可稍后重试。"}
      </p>
    );
  }
  if (!exportJob.download_url) {
    return (
      <p className="muted" style={{ marginTop: 8 }}>
        下载链接已过期，文件已清理；请重新导出。
      </p>
    );
  }
  return (
    <p style={{ marginTop: 10 }}>
      <a className="btn" href={`${API_BASE_URL}${exportJob.download_url}`}>
        下载 {exportJob.format.toUpperCase()} 文件 ↓
      </a>
      {exportJob.expires_at ? (
        <span className="muted" style={{ marginLeft: 8, fontSize: 13 }}>
          链接有效期至 {new Date(exportJob.expires_at).toLocaleTimeString()}
        </span>
      ) : null}
    </p>
  );
}
