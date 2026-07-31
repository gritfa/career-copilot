"use client";

import { useEffect, useState } from "react";
import { api, ApiError } from "@/lib/api";
import { handleApiError } from "@/lib/api-error";

/**
 * 单模型标准分析（阶段 6，ADR-001 裁剪版；LangGraph 多 Agent 推迟）。
 *
 * - 手动触发（每日 3 次），进行中轮询 GET /agent-runs/{id}；
 * - 只展示排队/分析中/校验中/完成/失败与最终结构化报告（无内部推理）；
 * - 每条结论展示事实引用与岗位原文片段；无证据 → 显式「信息不足」；
 * - 合成/未经真实模型验证的结果必须醒目标注（不虚标能力）。
 */

const INSUFFICIENT = "信息不足";

interface AnalysisClaim {
  claim: string;
  profile_fact_ids: string[];
  job_span: string;
  strength: string;
  uncertainty: string | null;
}

interface AnalysisGap {
  description: string;
  job_span: string;
  severity: string;
  uncertainty: string | null;
}

interface AnalysisRisk {
  code: string;
  evidence: string;
  confidence: number | null;
}

interface ResumeSuggestion {
  suggestion: string;
  based_on_fact_ids: string[];
  uncertainty: string | null;
}

interface AnalysisReport {
  schema_version: string;
  overall_summary: string;
  strengths: AnalysisClaim[];
  gaps: AnalysisGap[];
  risks: AnalysisRisk[];
  resume_suggestions: ResumeSuggestion[];
}

interface AgentRun {
  id: string;
  recommendation_id: string;
  status: "queued" | "analyzing" | "validating" | "completed" | "failed";
  trigger: string;
  provider: string;
  model_id: string | null;
  verified: boolean;
  graph_version: string;
  output_schema_version: string;
  error_code: string | null;
  created_at: string;
  completed_at: string | null;
  report: AnalysisReport | null;
}

const STATUS_LABELS: Record<AgentRun["status"], string> = {
  queued: "排队中",
  analyzing: "分析中",
  validating: "校验中",
  completed: "已完成",
  failed: "失败",
};

const ERROR_LABELS: Record<string, string> = {
  MODEL_UNAVAILABLE: "分析模型暂时不可用，基础匹配结果不受影响，可稍后重试",
  SCHEMA_INVALID: "模型输出未通过结构校验（已按规则拒绝，不展示不完整报告）",
  EVIDENCE_VALIDATION_FAILED: "分析结果未通过证据校验（拒绝无证据结论），未生成报告",
  CONSENT_REQUIRED: "缺少模型服务商授权，请先在隐私设置中完成授权",
};

const RISK_NAMES: Record<string, string> = {
  POSSIBLE_OUTSOURCING: "疑似外包/驻场",
};

const SEVERITY_NAMES: Record<string, string> = {
  minor: "轻微",
  major: "较大",
  unknown: "未知",
};

function isActive(run: AgentRun | null): boolean {
  return (
    run != null &&
    (run.status === "queued" || run.status === "analyzing" || run.status === "validating")
  );
}

export function AnalysisSection({ recommendationId }: { recommendationId: string }) {
  const [run, setRun] = useState<AgentRun | null>(null);
  const [loading, setLoading] = useState(true);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [pollTick, setPollTick] = useState(0);

  // 初始加载：最近一次分析
  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const data = await api.get<{ items: AgentRun[] }>(
          `/recommendations/${encodeURIComponent(recommendationId)}/analyses`,
        );
        if (!cancelled) setRun(data.items[0] ?? null);
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

  // 进行中：轮询任务状态（只显示排队/分析/校验/完成/失败）
  useEffect(() => {
    if (!run || !isActive(run)) return;
    const timer = setTimeout(async () => {
      try {
        const latest = await api.get<AgentRun>(
          `/agent-runs/${encodeURIComponent(run.id)}`,
        );
        setRun(latest);
      } catch {
        // 单次轮询失败不覆盖已有状态，计数递增触发下一轮重试
        setPollTick((t) => t + 1);
      }
    }, 2000);
    return () => clearTimeout(timer);
  }, [run, pollTick]);

  async function trigger() {
    setSubmitting(true);
    setError(null);
    try {
      const created = await api.post<AgentRun>(
        `/recommendations/${encodeURIComponent(recommendationId)}/deep-analysis`,
      );
      setRun(created);
    } catch (err) {
      if (err instanceof ApiError && err.status === 429) {
        setError("今日手动分析次数已用完（每天 3 次），明天再试。");
      } else {
        setError(handleApiError(err));
      }
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <>
      <h2>标准分析</h2>
      <div className="card">
        {loading ? <p className="muted">加载中…</p> : null}

        {!loading ? (
          <div style={{ display: "flex", gap: 8, alignItems: "center", flexWrap: "wrap" }}>
            <button
              type="button"
              className="btn btn-primary"
              onClick={() => void trigger()}
              disabled={submitting || isActive(run)}
            >
              {run ? "重新分析" : "开始分析"}
            </button>
            {run ? (
              <span
                className={
                  run.status === "completed"
                    ? "badge badge-ok"
                    : run.status === "failed"
                      ? "badge badge-fail"
                      : "badge badge-busy"
                }
              >
                {STATUS_LABELS[run.status]}
              </span>
            ) : (
              <span className="muted">
                基于你的已确认事实与岗位原文生成结构化分析（每日 3 次）。
              </span>
            )}
            {run && !run.verified ? (
              <span className="badge badge-busy" title="当前为确定性合成分析实现">
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

        {run?.status === "failed" ? (
          <p className="muted" style={{ marginTop: 8 }}>
            {ERROR_LABELS[run.error_code ?? ""] ??
              "分析失败，基础匹配结果不受影响，可稍后重试。"}
          </p>
        ) : null}

        {run?.status === "completed" && run.report ? <ReportBody report={run.report} /> : null}
      </div>
      <p className="muted" style={{ marginTop: 6, fontSize: 13 }}>
        多 Agent 深度分析将在后续版本开放（当前为单模型标准分析）。
      </p>
    </>
  );
}

function EvidencePair({
  factIds,
  jobSpan,
  uncertainty,
}: {
  factIds: string[];
  jobSpan: string;
  uncertainty: string | null;
}) {
  return (
    <>
      <p className="muted" style={{ marginTop: 4, fontSize: 13 }}>
        事实引用：
        {factIds.length > 0 ? (
          <span>{factIds.map((id) => `#${id.slice(0, 8)}`).join("、")}</span>
        ) : (
          <span>{INSUFFICIENT}</span>
        )}
      </p>
      {jobSpan ? (
        <div className="evidence">{jobSpan}</div>
      ) : (
        <p className="muted" style={{ fontSize: 13 }}>
          岗位原文证据{INSUFFICIENT}。
        </p>
      )}
      {uncertainty ? (
        <p className="muted" style={{ marginTop: 4, fontSize: 13 }}>
          不确定性：{uncertainty === "insufficient_evidence" ? "证据不足" : uncertainty}
        </p>
      ) : null}
    </>
  );
}

function ReportBody({ report }: { report: AnalysisReport }) {
  return (
    <div style={{ marginTop: 12 }}>
      <p style={{ fontSize: 14 }}>{report.overall_summary}</p>

      <h3 style={{ marginTop: 14 }}>匹配亮点</h3>
      {report.strengths.length === 0 ? (
        <p className="muted">未识别到有证据支撑的匹配亮点。</p>
      ) : (
        report.strengths.map((claim, i) => (
          <div
            key={i}
            style={{
              borderTop: i > 0 ? "1px solid var(--border)" : undefined,
              marginTop: 10,
              paddingTop: i > 0 ? 10 : 0,
            }}
          >
            <p style={{ fontSize: 14 }}>{claim.claim}</p>
            <EvidencePair
              factIds={claim.profile_fact_ids}
              jobSpan={claim.job_span}
              uncertainty={claim.uncertainty}
            />
          </div>
        ))
      )}

      <h3 style={{ marginTop: 14 }}>关键缺口</h3>
      {report.gaps.length === 0 ? (
        <p className="muted">未识别到关键缺口。</p>
      ) : (
        <ul style={{ margin: "0 0 0 18px", lineHeight: 1.8, fontSize: 14 }}>
          {report.gaps.map((gap, i) => (
            <li key={i}>
              {gap.description}
              {SEVERITY_NAMES[gap.severity] ? (
                <span className="muted">（{SEVERITY_NAMES[gap.severity]}）</span>
              ) : null}
              {gap.job_span ? <div className="evidence">{gap.job_span}</div> : null}
              {gap.uncertainty ? (
                <span className="muted">（证据不足，仅供参考）</span>
              ) : null}
            </li>
          ))}
        </ul>
      )}

      <h3 style={{ marginTop: 14 }}>风险信号</h3>
      {report.risks.length === 0 ? (
        <p className="muted">分析未发现新的风险信号。</p>
      ) : (
        report.risks.map((risk, i) => (
          <div key={i} style={{ marginTop: i > 0 ? 8 : 0 }}>
            <p style={{ fontSize: 14 }}>
              <span className="badge badge-busy">{RISK_NAMES[risk.code] ?? risk.code}</span>
              {risk.confidence != null && risk.confidence < 0.8 ? (
                <span className="muted"> · 该判断存在不确定性，仅供参考</span>
              ) : null}
            </p>
            {risk.evidence ? (
              <div className="evidence">{risk.evidence}</div>
            ) : (
              <p className="muted">原文证据{INSUFFICIENT}。</p>
            )}
          </div>
        ))
      )}

      <h3 style={{ marginTop: 14 }}>简历建议</h3>
      {report.resume_suggestions.length === 0 ? (
        <p className="muted">暂无基于已确认事实的简历建议。</p>
      ) : (
        <ul style={{ margin: "0 0 0 18px", lineHeight: 1.8, fontSize: 14 }}>
          {report.resume_suggestions.map((s, i) => (
            <li key={i}>
              {s.suggestion}
              {s.based_on_fact_ids.length > 0 ? (
                <span className="muted">
                  （基于事实 {s.based_on_fact_ids.map((id) => `#${id.slice(0, 8)}`).join("、")}）
                </span>
              ) : s.uncertainty ? (
                <span className="muted">（无事实证据，请勿据此虚构简历内容）</span>
              ) : null}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
