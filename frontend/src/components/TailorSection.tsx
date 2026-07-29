"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { api, ApiError } from "@/lib/api";
import { handleApiError } from "@/lib/api-error";
import {
  RESUME_VERSION_STATUS_LABELS,
  TAILOR_ERROR_LABELS,
  type ResumeVersion,
  isGeneratingVersion,
} from "@/lib/resume-types";

/**
 * 岗位定制简历入口（阶段 7，ADR-001 裁剪版：1 套标准单栏模板）。
 *
 * - 生成（每日 3 个新版本）→ 轮询状态 → 跳转版本页预览/确认/导出；
 * - 内容只来自已确认事实；合成实现产出醒目标注 not_verified；
 * - 生成失败展示稳定错误码文案，不展示半成品内容。
 */

export function TailorSection({ recommendationId }: { recommendationId: string }) {
  const [version, setVersion] = useState<ResumeVersion | null>(null);
  const [loading, setLoading] = useState(true);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [pollTick, setPollTick] = useState(0);

  // 初始加载：该推荐最近一个定制版本
  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const data = await api.get<{ items: ResumeVersion[] }>(
          `/resume-versions?recommendation_id=${encodeURIComponent(recommendationId)}`,
        );
        if (!cancelled) setVersion(data.items[0] ?? null);
      } catch (err) {
        if (!cancelled) setError(handleApiError(err));
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [recommendationId]);

  // 生成中：轮询版本状态
  useEffect(() => {
    if (!version || !isGeneratingVersion(version)) return;
    const timer = setTimeout(async () => {
      try {
        const latest = await api.get<ResumeVersion>(
          `/resume-versions/${encodeURIComponent(version.id)}`,
        );
        setVersion(latest);
      } catch {
        setPollTick((t) => t + 1);
      }
    }, 2000);
    return () => clearTimeout(timer);
  }, [version, pollTick]);

  async function generate() {
    setSubmitting(true);
    setError(null);
    try {
      const created = await api.post<ResumeVersion>(
        `/recommendations/${encodeURIComponent(recommendationId)}/resume-drafts`,
      );
      setVersion(created);
    } catch (err) {
      if (err instanceof ApiError && err.status === 429) {
        setError("今日新建定制简历数量已用完（每天 3 个），明天再试；已生成版本仍可编辑和导出。");
      } else {
        setError(handleApiError(err));
      }
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <>
      <h2>定制简历</h2>
      <div className="card">
        {loading ? <p className="muted">加载中…</p> : null}

        {!loading ? (
          <div style={{ display: "flex", gap: 8, alignItems: "center", flexWrap: "wrap" }}>
            <button
              type="button"
              className="btn btn-primary"
              onClick={() => void generate()}
              disabled={submitting || isGeneratingVersion(version)}
            >
              {version ? "重新生成定制简历" : "生成定制简历"}
            </button>
            {version ? (
              <span
                className={
                  version.status === "draft" || version.status === "confirmed"
                    ? "badge badge-ok"
                    : version.status === "failed"
                      ? "badge badge-fail"
                      : "badge badge-busy"
                }
              >
                {RESUME_VERSION_STATUS_LABELS[version.status]}
              </span>
            ) : (
              <span className="muted">
                内容只来自你已确认的事实：只做筛选、排序与措辞调整，绝不虚构（每日 3 个新版本）。
              </span>
            )}
            {version && !version.verified ? (
              <span className="badge badge-busy" title="当前为确定性合成定制实现">
                合成结果 · 模型未真实验证
              </span>
            ) : null}
          </div>
        ) : null}

        {error ? (
          <p className="error-text" style={{ marginTop: 8 }}>
            {error}
          </p>
        ) : null}

        {version?.status === "failed" ? (
          <p className="muted" style={{ marginTop: 8 }}>
            {TAILOR_ERROR_LABELS[version.error_code ?? ""] ??
              "定制简历生成失败，可稍后重试。"}
          </p>
        ) : null}

        {version && (version.status === "draft" || version.status === "confirmed") ? (
          <p style={{ marginTop: 10 }}>
            <Link className="btn" href={`/resume-versions/${version.id}`}>
              查看内容、调整明细与导出 →
            </Link>
          </p>
        ) : null}
      </div>
    </>
  );
}
