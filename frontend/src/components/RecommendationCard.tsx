"use client";

import { useState } from "react";
import Link from "next/link";
import { api } from "@/lib/api";
import { handleApiError } from "@/lib/api-error";
import ConfirmDialog from "@/components/ConfirmDialog";
import {
  FEEDBACK_REASONS,
  type FeedbackReason,
  type FeedbackState,
  type Recommendation,
  type RiskSignal,
  RISK_SEVERITY_NAMES,
  feedbackReasonLabel,
  fmtTime,
  gradeBadgeClass,
  gradeName,
  isSyntheticSeed,
  parseFeedback,
  recCity,
  recCompany,
  recKeyGap,
  recMatchPoints,
  recSalaryText,
  recSourceText,
  recTitle,
  riskName,
  riskUncertain,
  workModeLabel,
} from "@/lib/types";

/**
 * 推荐反馈操作（列表卡片与详情页共用，docs/05 第 11 节）：
 * - 感兴趣：POST /recommendations/{id}/feedback
 * - 不感兴趣：先弹原因选择（9 类 + 可选备注）再提交
 * - 已反馈：显示当前状态 + 撤回按钮（DELETE，撤回后重算学习偏好）
 */
export function FeedbackActions({
  recommendationId,
  initial,
}: {
  recommendationId: string;
  initial: FeedbackState | null;
}) {
  const [feedback, setFeedback] = useState<FeedbackState | null>(initial);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [dialogOpen, setDialogOpen] = useState(false);
  const [reasons, setReasons] = useState<FeedbackReason[]>([]);
  const [note, setNote] = useState("");
  const [dialogError, setDialogError] = useState<string | null>(null);

  const encodedId = encodeURIComponent(recommendationId);

  async function markInterested() {
    setBusy(true);
    setError(null);
    try {
      await api.post(`/recommendations/${encodedId}/feedback`, {
        feedback: "interested",
      });
      setFeedback({ kind: "interested", reasons: [] });
    } catch (err) {
      setError(handleApiError(err));
    } finally {
      setBusy(false);
    }
  }

  async function submitNotInterested() {
    if (reasons.length === 0) {
      setDialogError("请至少选择一个原因");
      return;
    }
    setBusy(true);
    setDialogError(null);
    try {
      await api.post(`/recommendations/${encodedId}/feedback`, {
        feedback: "not_interested",
        reasons,
        ...(note.trim() ? { note: note.trim() } : {}),
      });
      setFeedback({ kind: "not_interested", reasons, note: note.trim() || undefined });
      setDialogOpen(false);
      setReasons([]);
      setNote("");
      setError(null);
    } catch (err) {
      setDialogError(handleApiError(err));
    } finally {
      setBusy(false);
    }
  }

  async function withdraw() {
    setBusy(true);
    setError(null);
    try {
      await api.delete(`/recommendations/${encodedId}/feedback`);
      setFeedback(null);
    } catch (err) {
      setError(handleApiError(err));
    } finally {
      setBusy(false);
    }
  }

  function toggleReason(value: FeedbackReason) {
    setReasons((prev) =>
      prev.includes(value) ? prev.filter((r) => r !== value) : [...prev, value],
    );
  }

  return (
    <div style={{ marginTop: 10 }}>
      {feedback ? (
        <div style={{ display: "flex", gap: 8, alignItems: "center", flexWrap: "wrap" }}>
          <span className={feedback.kind === "interested" ? "badge badge-ok" : "badge"}>
            {feedback.kind === "interested" ? "已标记感兴趣" : "已标记不感兴趣"}
          </span>
          {feedback.kind === "not_interested" && feedback.reasons.length > 0 ? (
            <span className="muted">
              原因：{feedback.reasons.map(feedbackReasonLabel).join("、")}
            </span>
          ) : null}
          <button type="button" className="btn" onClick={withdraw} disabled={busy}>
            {busy ? "处理中…" : "撤回反馈"}
          </button>
        </div>
      ) : (
        <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
          <button
            type="button"
            className="btn btn-primary"
            onClick={markInterested}
            disabled={busy}
          >
            感兴趣
          </button>
          <button
            type="button"
            className="btn"
            onClick={() => {
              setDialogError(null);
              setDialogOpen(true);
            }}
            disabled={busy}
          >
            不感兴趣
          </button>
        </div>
      )}
      {error ? <p className="error-text">{error}</p> : null}

      <ConfirmDialog
        open={dialogOpen}
        title="不感兴趣的原因"
        confirmText="提交"
        loading={busy}
        onConfirm={submitNotInterested}
        onCancel={() => {
          if (!busy) setDialogOpen(false);
        }}
      >
        <p className="muted" style={{ marginBottom: 8 }}>
          选择原因（可多选），系统会据此调整后续推荐：
        </p>
        <div style={{ display: "grid", gap: 6 }}>
          {FEEDBACK_REASONS.map((reason) => (
            <label
              key={reason.value}
              style={{ display: "flex", gap: 8, alignItems: "center", cursor: "pointer" }}
            >
              <input
                type="checkbox"
                checked={reasons.includes(reason.value)}
                onChange={() => toggleReason(reason.value)}
              />
              <span>{reason.label}</span>
            </label>
          ))}
        </div>
        <label className="field" style={{ marginTop: 12 }}>
          <span>备注（可选）</span>
          <textarea
            className="input"
            rows={2}
            value={note}
            onChange={(e) => setNote(e.target.value)}
            placeholder="补充说明，帮助系统更准地调整推荐"
          />
        </label>
        {dialogError ? <p className="error-text">{dialogError}</p> : null}
      </ConfirmDialog>
    </div>
  );
}

/** 单个风险徽标（含不确定性标注，docs/05 第 6 节） */
function RiskBadge({ risk }: { risk: RiskSignal }) {
  const severity = RISK_SEVERITY_NAMES[(risk.severity ?? "").toLowerCase()];
  return (
    <span className="badge badge-busy">
      {riskName(risk)}
      {severity ? `（${severity}风险）` : ""}
      {riskUncertain(risk) ? " · 存在不确定性" : ""}
    </span>
  );
}

/**
 * 推荐卡片（docs/05 第 6 节，列表页与 Dashboard 复用）：
 * 岗位/公司/城市/办公方式、薪资（缺失显示未披露）、发布/发现时间与主来源、
 * 总分 + 等级徽标、前 3 个匹配点、最关键缺口、风险徽标、感兴趣/不感兴趣。
 */
export default function RecommendationCard({ item }: { item: Recommendation }) {
  const detailHref = `/recommendations/${encodeURIComponent(item.id)}`;
  const matchPoints = recMatchPoints(item).slice(0, 3);
  const keyGap = recKeyGap(item);
  const risks = Array.isArray(item.risks) ? item.risks : [];
  const source = recSourceText(item);
  const published = item.published_at ?? null;
  const firstSeen = item.first_seen_at ?? item.discovered_at ?? item.created_at ?? null;

  return (
    <div className="card">
      <div
        style={{
          display: "flex",
          justifyContent: "space-between",
          alignItems: "flex-start",
          gap: 8,
          flexWrap: "wrap",
        }}
      >
        <span style={{ display: "inline-flex", alignItems: "center", gap: 8, flexWrap: "wrap" }}>
          <Link href={detailHref} className="link">
            <strong>{recTitle(item)}</strong>
          </Link>
          {isSyntheticSeed(item) ? (
            <span className="badge" title="该岗位为合成种子数据，仅用于产品演示，非真实在招岗位">
              合成示例
            </span>
          ) : null}
        </span>
        {item.score != null || item.grade ? (
          <span className={gradeBadgeClass(item.grade)}>
            {item.score != null ? `${item.score} 分` : ""}
            {item.grade ? `${item.score != null ? " · " : ""}${gradeName(item.grade)}` : ""}
          </span>
        ) : null}
      </div>

      <p style={{ marginTop: 6, fontSize: 14 }}>
        {recCompany(item)}
        {" · "}
        {recCity(item)}
        {item.work_mode ? ` · ${workModeLabel(item.work_mode)}` : ""}
        {" · "}
        {recSalaryText(item)}
      </p>

      <p className="muted" style={{ marginTop: 4 }}>
        {published ? `发布于 ${fmtTime(published)}` : null}
        {published && firstSeen ? " · " : null}
        {firstSeen ? `发现于 ${fmtTime(firstSeen)}` : null}
        {(published || firstSeen) && source ? " · " : null}
        {source ? `来源：${source}` : null}
        {!published && !firstSeen && !source ? "发布/来源信息不足" : null}
      </p>

      {matchPoints.length > 0 ? (
        <p style={{ marginTop: 6, fontSize: 14 }}>匹配：{matchPoints.join(" / ")}</p>
      ) : null}
      {keyGap ? (
        <p style={{ marginTop: 4, fontSize: 14 }}>缺口：{keyGap}</p>
      ) : null}

      {risks.length > 0 ? (
        <div style={{ display: "flex", gap: 6, flexWrap: "wrap", marginTop: 8 }}>
          {risks.map((risk, i) => (
            <RiskBadge key={`${risk.code ?? risk.name ?? "risk"}-${i}`} risk={risk} />
          ))}
        </div>
      ) : (
        <p className="muted" style={{ marginTop: 6 }}>
          风险：无明显风险信号
        </p>
      )}

      <div
        style={{
          display: "flex",
          gap: 8,
          alignItems: "center",
          flexWrap: "wrap",
          marginTop: 4,
        }}
      >
        <Link href={detailHref} className="btn" style={{ marginTop: 10 }}>
          查看报告
        </Link>
        <div style={{ flex: 1 }}>
          <FeedbackActions recommendationId={item.id} initial={parseFeedback(item)} />
        </div>
      </div>
    </div>
  );
}
