"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { api } from "@/lib/api";
import { handleApiError } from "@/lib/api-error";
import RecommendationCard from "@/components/RecommendationCard";
import {
  type EmptyReason,
  type LinkOutEntry,
  type Recommendation,
  type SearchPlan,
  GRADE_NAMES,
  directionLabel,
  extractEmptyReason,
  extractLinkOuts,
  toItems,
} from "@/lib/types";

/**
 * 每日推荐列表（docs/05 第 6 节 + 第 14 节空态）：
 * - 筛选条：方案 / 日期 / 等级 / 状态（GET /recommendations 查询参数）
 * - 推荐卡片复用 RecommendationCard（Dashboard 同款）
 * - 空态区分 来源暂无数据 / 硬条件过严 / 能力降级，并提供原平台跳转与手动导入
 */

const REASON_TEXTS: Record<Exclude<EmptyReason, "unknown">, { title: string; body: string }> = {
  no_source_data: {
    title: "来源暂无数据",
    body: "接入的岗位来源今天还没有可用的新岗位，系统每天更新一次，稍后会自动补上。",
  },
  hard_conditions_too_strict: {
    title: "硬条件过严",
    body: "城市、薪资或办公方式等硬条件过滤掉了全部岗位，可以到方案里适当放宽再看。",
  },
  capability_degraded: {
    title: "来源能力降级",
    body: "部分岗位来源暂时受限或降级，只支持跳转到原平台搜索或手动导入，恢复后会自动继续采集。",
  },
};

/** 反馈状态筛选（后端并行开发中，按契约传 status 参数） */
const STATUS_OPTIONS: ReadonlyArray<{ value: string; label: string }> = [
  { value: "", label: "全部状态" },
  { value: "pending", label: "未反馈" },
  { value: "interested", label: "已感兴趣" },
  { value: "not_interested", label: "已不感兴趣" },
];

/** 请求结果带上查询串，用于派生「加载中」状态（结果与当前筛选不一致即视为加载中） */
type FetchResult =
  | { qs: string; kind: "error"; reason: string }
  | {
      qs: string;
      kind: "ready";
      items: Recommendation[];
      linkOuts: LinkOutEntry[];
      emptyReason: EmptyReason;
    };

export default function RecommendationsPage() {
  const [result, setResult] = useState<FetchResult | null>(null);
  const [plans, setPlans] = useState<SearchPlan[]>([]);

  // 筛选条件
  const [planId, setPlanId] = useState("");
  const [date, setDate] = useState("");
  const [grade, setGrade] = useState("");
  const [status, setStatus] = useState("");

  // 方案列表只加载一次（筛选用，失败不阻断页面）
  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const data = await api.get<unknown>("/search-plans");
        if (!cancelled) setPlans(toItems<SearchPlan>(data));
      } catch {
        // 方案筛选降级为不可用即可，不影响推荐列表
      }
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  const params = new URLSearchParams();
  if (planId) params.set("plan_id", planId);
  if (date) params.set("date", date);
  if (grade) params.set("grade", grade);
  if (status) params.set("status", status);
  const qs = params.toString();

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const data = await api.get<unknown>(`/recommendations${qs ? `?${qs}` : ""}`);
        if (cancelled) return;
        setResult({
          qs,
          kind: "ready",
          items: toItems<Recommendation>(data),
          linkOuts: extractLinkOuts(data),
          emptyReason: extractEmptyReason(data),
        });
      } catch (err) {
        if (cancelled) return;
        setResult({ qs, kind: "error", reason: handleApiError(err) });
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [qs]);

  // 结果对应的查询串与当前筛选不一致 → 加载中
  const state = result && result.qs === qs ? result : null;
  const filtered = Boolean(planId || date || grade || status);

  return (
    <main className="page">
      <h1>每日推荐</h1>
      <p className="muted">分数代表岗位适配度，不代表面试或录用概率。</p>

      <div className="filters">
        <label className="field">
          <span>方案</span>
          <select className="input" value={planId} onChange={(e) => setPlanId(e.target.value)}>
            <option value="">全部方案</option>
            {plans.map((plan) => (
              <option key={plan.id} value={plan.id}>
                {plan.name || directionLabel(plan.role_family ?? plan.direction)}
              </option>
            ))}
          </select>
        </label>
        <label className="field">
          <span>日期</span>
          <input
            type="date"
            className="input"
            value={date}
            onChange={(e) => setDate(e.target.value)}
          />
        </label>
        <label className="field">
          <span>等级</span>
          <select className="input" value={grade} onChange={(e) => setGrade(e.target.value)}>
            <option value="">全部等级</option>
            {Object.entries(GRADE_NAMES).map(([value, label]) => (
              <option key={value} value={value}>
                {label}
              </option>
            ))}
          </select>
        </label>
        <label className="field">
          <span>状态</span>
          <select className="input" value={status} onChange={(e) => setStatus(e.target.value)}>
            {STATUS_OPTIONS.map((opt) => (
              <option key={opt.value} value={opt.value}>
                {opt.label}
              </option>
            ))}
          </select>
        </label>
      </div>

      {state === null ? <p className="muted">加载中…</p> : null}

      {state?.kind === "error" ? (
        <>
          <p className="error-text">{state.reason}</p>
          <ImportEntry />
        </>
      ) : null}

      {state?.kind === "ready" && state.items.length > 0 ? (
        <>
          <p className="muted">共 {state.items.length} 条推荐。</p>
          {state.items.map((item) => (
            <RecommendationCard key={item.id} item={item} />
          ))}
        </>
      ) : null}

      {state?.kind === "ready" && state.items.length === 0 ? (
        filtered ? (
          <div className="card">
            <strong>当前筛选条件下没有推荐</strong>
            <p className="muted" style={{ marginTop: 6 }}>
              可以调整方案、日期、等级或状态筛选，或清空筛选后查看全部推荐。
            </p>
            <p style={{ marginTop: 10 }}>
              <button
                type="button"
                className="btn"
                onClick={() => {
                  setPlanId("");
                  setDate("");
                  setGrade("");
                  setStatus("");
                }}
              >
                清空筛选
              </button>
            </p>
          </div>
        ) : (
          <EmptyState reason={state.emptyReason} linkOuts={state.linkOuts} />
        )
      ) : null}
    </main>
  );
}

function EmptyState({
  reason,
  linkOuts,
}: {
  reason: EmptyReason;
  linkOuts: LinkOutEntry[];
}) {
  return (
    <>
      <div className="card">
        <strong>今天还没有可推荐的岗位</strong>
        {reason !== "unknown" ? (
          <div className="notice">
            <strong>{REASON_TEXTS[reason].title}：</strong>
            {REASON_TEXTS[reason].body}
          </div>
        ) : (
          <div style={{ marginTop: 8, fontSize: 14 }}>
            <p>可能的原因：</p>
            <ul style={{ margin: "6px 0 0 18px", lineHeight: 1.8 }}>
              <li>
                <strong>来源暂无数据</strong>：{REASON_TEXTS.no_source_data.body}
              </li>
              <li>
                <strong>硬条件过严</strong>：{REASON_TEXTS.hard_conditions_too_strict.body}
              </li>
              <li>
                <strong>来源能力降级</strong>：{REASON_TEXTS.capability_degraded.body}
              </li>
            </ul>
          </div>
        )}
        <p style={{ marginTop: 10 }}>
          <Link href="/plans" className="link">
            去调整求职方案 →
          </Link>
        </p>
      </div>

      <h2>按你的方案去原平台搜索</h2>
      {linkOuts.length > 0 ? (
        <>
          <p className="muted">
            以下链接按你的方向、城市和薪资拼好了搜索条件，点开即到原平台查看：
          </p>
          <div style={{ display: "flex", gap: 8, flexWrap: "wrap", margin: "10px 0" }}>
            {linkOuts.map((entry) => (
              <a
                key={entry.url}
                className="btn"
                href={entry.url}
                target="_blank"
                rel="noopener noreferrer"
              >
                {entry.label} ↗
              </a>
            ))}
          </div>
        </>
      ) : (
        <p className="muted">
          暂无可用的跳转链接。激活一个求职方案后，这里会显示按方案条件拼好的原平台搜索入口。
        </p>
      )}

      <ImportEntry />
    </>
  );
}

/** 手动导入入口（空态与错误态共用） */
function ImportEntry() {
  return (
    <>
      <h2>看到合适的岗位？手动导入</h2>
      <p className="muted">
        受平台政策限制，部分来源仅支持跳转到原平台或手动导入。
        把岗位链接或职位描述粘贴进来，系统会标准化后参与匹配打分。
      </p>
      <p style={{ margin: "10px 0" }}>
        <Link href="/jobs/import" className="btn btn-primary">
          导入岗位
        </Link>
      </p>
    </>
  );
}
