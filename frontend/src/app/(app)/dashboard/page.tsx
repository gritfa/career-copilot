"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { API_BASE_URL, ApiError, api } from "@/lib/api";
import { handleApiError } from "@/lib/api-error";
import RecommendationCard from "@/components/RecommendationCard";
import {
  type CapabilityEntry,
  type Recommendation,
  type SearchPlan,
  capabilityStatusMeta,
  directionLabel,
  extractDataUpdatedAt,
  fmtTime,
  parseCapabilities,
  toItems,
} from "@/lib/types";

/**
 * 首页概览（docs/05 第 5 节线框）：
 * 方案切换下拉 → 今日统计（新岗位/高匹配/可尝试）→ 最新推荐卡片（复用列表卡片）
 * → 系统状态行（数据更新时间 + 能力状态，GET /health/capabilities 宽松兼容）。
 */

const LATEST_LIMIT = 5;

/** 请求结果带上方案 id，用于派生「加载中」状态（结果与当前方案不一致即视为加载中） */
type RecResult =
  | { planId: string; kind: "error"; reason: string }
  | { planId: string; kind: "ready"; items: Recommendation[] };

interface CapState {
  entries: CapabilityEntry[];
  updatedAt: string | null;
  failed: boolean;
}

/**
 * /health/capabilities 在部分部署下挂在根路径而非 /api/v1，
 * 404 时回退到根路径再试一次（宽松兼容）。
 */
async function fetchCapabilities(): Promise<unknown> {
  try {
    return await api.get<unknown>("/health/capabilities");
  } catch (err) {
    if (err instanceof ApiError && err.status === 404) {
      const root = API_BASE_URL.replace(/\/api\/v\d+\/?$/, "");
      if (root && root !== API_BASE_URL) {
        const res = await fetch(`${root}/health/capabilities`, {
          credentials: "include",
          headers: { Accept: "application/json" },
        });
        if (res.ok) return (await res.json()) as unknown;
      }
    }
    throw err;
  }
}

/** 判断 ISO 时间是否为今天（本地时区） */
function isToday(iso?: string): boolean {
  if (!iso) return false;
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return false;
  const now = new Date();
  return (
    d.getFullYear() === now.getFullYear() &&
    d.getMonth() === now.getMonth() &&
    d.getDate() === now.getDate()
  );
}

export default function DashboardPage() {
  const [plans, setPlans] = useState<SearchPlan[]>([]);
  const [planId, setPlanId] = useState("");
  const [recResult, setRecResult] = useState<RecResult | null>(null);
  const [caps, setCaps] = useState<CapState | null>(null);

  // 方案列表（失败不阻断首页）
  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const data = await api.get<unknown>("/search-plans");
        if (!cancelled) setPlans(toItems<SearchPlan>(data));
      } catch {
        // 方案切换降级为「全部方案」
      }
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  // 推荐列表（随方案切换刷新）
  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const qs = planId ? `?plan_id=${encodeURIComponent(planId)}` : "";
        const data = await api.get<unknown>(`/recommendations${qs}`);
        if (!cancelled) {
          setRecResult({ planId, kind: "ready", items: toItems<Recommendation>(data) });
        }
      } catch (err) {
        if (!cancelled) {
          setRecResult({ planId, kind: "error", reason: handleApiError(err) });
        }
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [planId]);

  // 能力状态（失败只降级展示，不影响其他区块）
  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const data = await fetchCapabilities();
        if (!cancelled) {
          setCaps({
            entries: parseCapabilities(data),
            updatedAt: extractDataUpdatedAt(data),
            failed: false,
          });
        }
      } catch {
        if (!cancelled) setCaps({ entries: [], updatedAt: null, failed: true });
      }
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  // 结果对应的方案与当前选择不一致 → 加载中
  const recState = recResult && recResult.planId === planId ? recResult : null;
  const items = recState?.kind === "ready" ? recState.items : [];
  const hasDates = items.some((r) =>
    Boolean(r.first_seen_at ?? r.discovered_at ?? r.published_at ?? r.created_at),
  );
  const todayCount = hasDates
    ? items.filter((r) =>
        isToday(r.first_seen_at ?? r.discovered_at ?? r.published_at ?? r.created_at),
      ).length
    : items.length;
  const highCount = items.filter((r) => (r.grade ?? "").toLowerCase() === "high").length;
  const potentialCount = items.filter(
    (r) => (r.grade ?? "").toLowerCase() === "potential",
  ).length;
  const latest = items.slice(0, LATEST_LIMIT);

  return (
    <main className="page">
      <h1>首页概览</h1>

      <div className="filters">
        <label className="field" style={{ maxWidth: 280 }}>
          <span>求职方案</span>
          <select
            className="input"
            value={planId}
            onChange={(e) => setPlanId(e.target.value)}
          >
            <option value="">全部方案</option>
            {plans.map((plan) => (
              <option key={plan.id} value={plan.id}>
                {plan.name || directionLabel(plan.role_family ?? plan.direction)}
              </option>
            ))}
          </select>
        </label>
      </div>

      <div className="stat-row">
        <div className="stat">
          <span className="muted">今日新岗位</span>
          <div className="stat-value">
            {recState?.kind === "ready" ? todayCount : "—"}
          </div>
        </div>
        <div className="stat">
          <span className="muted">高匹配</span>
          <div className="stat-value">{recState?.kind === "ready" ? highCount : "—"}</div>
        </div>
        <div className="stat">
          <span className="muted">可尝试</span>
          <div className="stat-value">
            {recState?.kind === "ready" ? potentialCount : "—"}
          </div>
        </div>
      </div>

      <div
        style={{
          display: "flex",
          justifyContent: "space-between",
          alignItems: "baseline",
          gap: 8,
          flexWrap: "wrap",
        }}
      >
        <h2>最新推荐</h2>
        <Link href="/recommendations" className="link" style={{ fontSize: 14 }}>
          查看全部推荐 →
        </Link>
      </div>
      <p className="muted">分数代表岗位适配度，不代表面试或录用概率。</p>

      {recState === null ? <p className="muted">加载中…</p> : null}
      {recState?.kind === "error" ? (
        <p className="error-text">{recState.reason}</p>
      ) : null}
      {recState?.kind === "ready" && latest.length === 0 ? (
        <div className="card">
          <p>今天还没有可推荐的岗位。</p>
          <p style={{ marginTop: 8 }}>
            <Link href="/recommendations" className="link">
              查看原因与原平台搜索入口 →
            </Link>
          </p>
        </div>
      ) : null}
      {latest.map((item) => (
        <RecommendationCard key={item.id} item={item} />
      ))}

      <h2>系统状态</h2>
      <div className="card">
        {caps === null ? (
          <p className="muted">系统状态加载中…</p>
        ) : caps.failed ? (
          <p className="muted">能力状态暂不可用，不影响推荐浏览。</p>
        ) : (
          <>
            <p style={{ fontSize: 14 }}>
              岗位数据更新于 {caps.updatedAt ? fmtTime(caps.updatedAt) : "—"}
            </p>
            {caps.entries.length > 0 ? (
              <div style={{ display: "flex", gap: 6, flexWrap: "wrap", marginTop: 8 }}>
                {caps.entries.map((entry) => {
                  const meta = capabilityStatusMeta(entry.status);
                  return (
                    <span key={entry.name} className={meta.badgeClass}>
                      {entry.label}：{meta.label}
                    </span>
                  );
                })}
              </div>
            ) : (
              <p className="muted" style={{ marginTop: 6 }}>
                暂无逐项能力状态。
              </p>
            )}
          </>
        )}
      </div>
    </main>
  );
}
