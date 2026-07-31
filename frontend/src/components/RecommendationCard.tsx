"use client";

import { useState } from "react";
import Link from "next/link";
import { api } from "@/lib/api";
import { handleApiError } from "@/lib/api-error";
import ConfirmDialog from "@/components/ConfirmDialog";
import Button, { buttonClasses } from "@/components/ui/Button";
import Badge, { SyntheticBadge, type BadgeTone } from "@/components/ui/Badge";
import { Field, Textarea } from "@/components/ui/form";
import {
  FEEDBACK_REASONS,
  type FeedbackReason,
  type FeedbackState,
  type Recommendation,
  type RiskSignal,
  RISK_SEVERITY_NAMES,
  feedbackReasonLabel,
  fmtTime,
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

/** 等级 → 徽标语义色（high=success / potential=warning / 其他=neutral） */
export function gradeTone(grade?: string): BadgeTone {
  const g = (grade ?? "").toLowerCase();
  if (g === "high") return "success";
  if (g === "potential") return "warning";
  return "neutral";
}

/** 总分 + 等级徽标（列表卡片与详情页共用） */
export function ScoreBadge({ item }: { item: Recommendation }) {
  if (item.score == null && !item.grade) return null;
  return (
    <Badge tone={gradeTone(item.grade)} className="px-3 py-1 text-sm font-semibold">
      {item.score != null ? `${item.score} 分` : ""}
      {item.grade ? `${item.score != null ? " · " : ""}${gradeName(item.grade)}` : ""}
    </Badge>
  );
}

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
    <div>
      {feedback ? (
        <div className="flex flex-wrap items-center gap-2">
          <Badge tone={feedback.kind === "interested" ? "success" : "neutral"}>
            {feedback.kind === "interested" ? "已标记感兴趣" : "已标记不感兴趣"}
          </Badge>
          {feedback.kind === "not_interested" && feedback.reasons.length > 0 ? (
            <span className="text-sm text-slate-600">
              原因：{feedback.reasons.map(feedbackReasonLabel).join("、")}
            </span>
          ) : null}
          <Button size="sm" onClick={withdraw} disabled={busy}>
            {busy ? "处理中…" : "撤回反馈"}
          </Button>
        </div>
      ) : (
        <div className="flex flex-wrap gap-2">
          <Button variant="primary" size="sm" onClick={markInterested} disabled={busy}>
            感兴趣
          </Button>
          <Button
            size="sm"
            onClick={() => {
              setDialogError(null);
              setDialogOpen(true);
            }}
            disabled={busy}
          >
            不感兴趣
          </Button>
        </div>
      )}
      {error ? <p className="mt-2 text-sm text-danger">{error}</p> : null}

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
        <p className="mb-2 text-sm text-slate-600">
          选择原因（可多选），系统会据此调整后续推荐：
        </p>
        <div className="grid gap-1.5">
          {FEEDBACK_REASONS.map((reason) => (
            <label
              key={reason.value}
              className="flex cursor-pointer items-center gap-2.5 rounded-lg px-1 py-0.5 hover:bg-slate-50"
            >
              <input
                type="checkbox"
                className="h-4 w-4 accent-indigo-600"
                checked={reasons.includes(reason.value)}
                onChange={() => toggleReason(reason.value)}
              />
              <span>{reason.label}</span>
            </label>
          ))}
        </div>
        <Field label="备注（可选）" className="mt-3">
          <Textarea
            rows={2}
            value={note}
            onChange={(e) => setNote(e.target.value)}
            placeholder="补充说明，帮助系统更准地调整推荐"
          />
        </Field>
        {dialogError ? <p className="mt-2 text-sm text-danger">{dialogError}</p> : null}
      </ConfirmDialog>
    </div>
  );
}

/** 单个风险徽标（含不确定性标注，docs/05 第 6 节） */
function RiskBadge({ risk }: { risk: RiskSignal }) {
  const severity = RISK_SEVERITY_NAMES[(risk.severity ?? "").toLowerCase()];
  return (
    <Badge tone="warning">
      {riskName(risk)}
      {severity ? `（${severity}风险）` : ""}
      {riskUncertain(risk) ? " · 存在不确定性" : ""}
    </Badge>
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
    <div className="group my-3 rounded-xl border border-slate-200 bg-white p-5 shadow-card transition-all hover:-translate-y-0.5 hover:border-indigo-200 hover:shadow-md">
      <div className="flex flex-wrap items-start justify-between gap-x-4 gap-y-2">
        <span className="inline-flex flex-wrap items-center gap-2">
          <Link
            href={detailHref}
            className="text-base font-semibold text-slate-900 transition-colors group-hover:text-primary"
          >
            {recTitle(item)}
          </Link>
          {isSyntheticSeed(item) ? <SyntheticBadge /> : null}
        </span>
        <ScoreBadge item={item} />
      </div>

      <p className="mt-1.5 text-sm text-slate-600">
        {recCompany(item)}
        {" · "}
        {recCity(item)}
        {item.work_mode ? ` · ${workModeLabel(item.work_mode)}` : ""}
        {" · "}
        <span className="font-medium text-slate-700">{recSalaryText(item)}</span>
      </p>

      <p className="mt-1 text-xs text-slate-400">
        {published ? `发布于 ${fmtTime(published)}` : null}
        {published && firstSeen ? " · " : null}
        {firstSeen ? `发现于 ${fmtTime(firstSeen)}` : null}
        {(published || firstSeen) && source ? " · " : null}
        {source ? `来源：${source}` : null}
        {!published && !firstSeen && !source ? "发布/来源信息不足" : null}
      </p>

      {matchPoints.length > 0 || keyGap ? (
        <div className="mt-3 space-y-1 rounded-lg bg-slate-50 px-3 py-2.5 text-sm">
          {matchPoints.length > 0 ? (
            <p className="text-slate-700">
              <span className="font-medium text-emerald-700">匹配</span>
              ：{matchPoints.join(" / ")}
            </p>
          ) : null}
          {keyGap ? (
            <p className="text-slate-700">
              <span className="font-medium text-amber-700">缺口</span>
              ：{keyGap}
            </p>
          ) : null}
        </div>
      ) : null}

      {risks.length > 0 ? (
        <div className="mt-3 flex flex-wrap gap-1.5">
          {risks.map((risk, i) => (
            <RiskBadge key={`${risk.code ?? risk.name ?? "risk"}-${i}`} risk={risk} />
          ))}
        </div>
      ) : (
        <p className="mt-2 text-xs text-slate-400">风险：无明显风险信号</p>
      )}

      <div className="mt-4 flex flex-wrap items-center gap-3 border-t border-slate-100 pt-3">
        <Link href={detailHref} className={buttonClasses("secondary", "sm")}>
          查看报告
        </Link>
        <div className="min-w-0 flex-1">
          <FeedbackActions recommendationId={item.id} initial={parseFeedback(item)} />
        </div>
      </div>
    </div>
  );
}
