"use client";

import { useEffect, useState, type ReactNode } from "react";
import Link from "next/link";
import { API_BASE_URL, ApiError, api } from "@/lib/api";
import { handleApiError } from "@/lib/api-error";
import RecommendationCard from "@/components/RecommendationCard";
import PageHeader from "@/components/ui/PageHeader";
import Card from "@/components/ui/Card";
import Badge, { type BadgeTone } from "@/components/ui/Badge";
import { buttonClasses } from "@/components/ui/Button";
import EmptyStateBlock from "@/components/ui/EmptyState";
import Skeleton, { CardSkeleton } from "@/components/ui/Skeleton";
import { Field, Select } from "@/components/ui/form";
import { IconArrowRight, IconSparkles } from "@/components/ui/icons";
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

/** 统计卡片（数值 + 语义色顶部小条） */
function StatCard({
  label,
  value,
  accent,
}: {
  label: string;
  value: ReactNode;
  accent: string;
}) {
  return (
    <div className="min-w-[120px] flex-1 overflow-hidden rounded-xl border border-slate-200 bg-white shadow-card">
      <div className={`h-1 ${accent}`} />
      <div className="px-4 py-3.5">
        <p className="text-sm text-slate-600">{label}</p>
        <p className="mt-0.5 text-2xl font-bold tracking-tight text-slate-900">
          {value}
        </p>
      </div>
    </div>
  );
}

/** 能力状态 badgeClass → 语义 tone（复用 types.ts 的状态归类，不改逻辑） */
function capabilityTone(badgeClass: string): BadgeTone {
  if (badgeClass.includes("badge-ok")) return "success";
  if (badgeClass.includes("badge-busy")) return "warning";
  if (badgeClass.includes("badge-fail")) return "danger";
  return "neutral";
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
      <PageHeader
        title="首页概览"
        actions={
          <Field label="求职方案" className="w-64">
            <Select value={planId} onChange={(e) => setPlanId(e.target.value)}>
              <option value="">全部方案</option>
              {plans.map((plan) => (
                <option key={plan.id} value={plan.id}>
                  {plan.name || directionLabel(plan.role_family ?? plan.direction)}
                </option>
              ))}
            </Select>
          </Field>
        }
      />

      <div className="flex flex-wrap gap-3">
        <StatCard
          label="今日新岗位"
          value={recState?.kind === "ready" ? todayCount : "—"}
          accent="bg-primary"
        />
        <StatCard
          label="高匹配"
          value={recState?.kind === "ready" ? highCount : "—"}
          accent="bg-emerald-500"
        />
        <StatCard
          label="可尝试"
          value={recState?.kind === "ready" ? potentialCount : "—"}
          accent="bg-amber-500"
        />
      </div>

      <div className="mt-7 flex flex-wrap items-baseline justify-between gap-2">
        <h2 className="text-lg font-semibold text-slate-900">最新推荐</h2>
        <Link
          href="/recommendations"
          className="inline-flex items-center gap-1 text-sm font-medium text-primary transition-colors hover:text-primary-hover"
        >
          查看全部推荐
          <IconArrowRight className="h-3.5 w-3.5" />
        </Link>
      </div>
      <p className="text-sm text-slate-600">
        分数代表岗位适配度，不代表面试或录用概率。
      </p>

      {recState === null ? (
        <div aria-label="加载中">
          <CardSkeleton />
          <CardSkeleton />
        </div>
      ) : null}
      {recState?.kind === "error" ? (
        <p className="mt-2 text-sm text-danger">{recState.reason}</p>
      ) : null}
      {recState?.kind === "ready" && latest.length === 0 ? (
        <EmptyStateBlock
          className="mt-3"
          icon={<IconSparkles className="h-6 w-6" />}
          title="今天还没有可推荐的岗位"
          action={
            <Link href="/recommendations" className={buttonClasses("secondary", "sm")}>
              查看原因与原平台搜索入口 →
            </Link>
          }
        />
      ) : null}
      {latest.map((item) => (
        <RecommendationCard key={item.id} item={item} />
      ))}

      <h2 className="mt-7 mb-2.5 text-lg font-semibold text-slate-900">系统状态</h2>
      <Card>
        {caps === null ? (
          <div className="space-y-2" aria-label="系统状态加载中">
            <Skeleton className="h-4 w-1/2" />
            <Skeleton className="h-6 w-2/3" />
          </div>
        ) : caps.failed ? (
          <p className="text-sm text-slate-600">能力状态暂不可用，不影响推荐浏览。</p>
        ) : (
          <>
            <p className="text-sm text-slate-700">
              岗位数据更新于 {caps.updatedAt ? fmtTime(caps.updatedAt) : "—"}
            </p>
            {caps.entries.length > 0 ? (
              <div className="mt-2 flex flex-wrap gap-1.5">
                {caps.entries.map((entry) => {
                  const meta = capabilityStatusMeta(entry.status);
                  return (
                    <Badge key={entry.name} tone={capabilityTone(meta.badgeClass)}>
                      {entry.label}：{meta.label}
                    </Badge>
                  );
                })}
              </div>
            ) : (
              <p className="mt-1.5 text-sm text-slate-400">暂无逐项能力状态。</p>
            )}
          </>
        )}
      </Card>
    </main>
  );
}
