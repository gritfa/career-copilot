"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { api } from "@/lib/api";
import { handleApiError } from "@/lib/api-error";
import RecommendationCard from "@/components/RecommendationCard";
import PageHeader from "@/components/ui/PageHeader";
import Card from "@/components/ui/Card";
import Button, { buttonClasses } from "@/components/ui/Button";
import EmptyStateBlock from "@/components/ui/EmptyState";
import { CardSkeleton } from "@/components/ui/Skeleton";
import { Field, Input, Select } from "@/components/ui/form";
import { IconExternalLink, IconImport, IconSparkles } from "@/components/ui/icons";
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
      <PageHeader
        title="每日推荐"
        description="分数代表岗位适配度，不代表面试或录用概率。"
      />

      <Card className="mb-5 py-4">
        <div className="flex flex-wrap items-end gap-3">
          <Field label="方案" className="min-w-[140px] flex-1">
            <Select value={planId} onChange={(e) => setPlanId(e.target.value)}>
              <option value="">全部方案</option>
              {plans.map((plan) => (
                <option key={plan.id} value={plan.id}>
                  {plan.name || directionLabel(plan.role_family ?? plan.direction)}
                </option>
              ))}
            </Select>
          </Field>
          <Field label="日期" className="min-w-[140px] flex-1">
            <Input type="date" value={date} onChange={(e) => setDate(e.target.value)} />
          </Field>
          <Field label="等级" className="min-w-[130px] flex-1">
            <Select value={grade} onChange={(e) => setGrade(e.target.value)}>
              <option value="">全部等级</option>
              {Object.entries(GRADE_NAMES).map(([value, label]) => (
                <option key={value} value={value}>
                  {label}
                </option>
              ))}
            </Select>
          </Field>
          <Field label="状态" className="min-w-[130px] flex-1">
            <Select value={status} onChange={(e) => setStatus(e.target.value)}>
              {STATUS_OPTIONS.map((opt) => (
                <option key={opt.value} value={opt.value}>
                  {opt.label}
                </option>
              ))}
            </Select>
          </Field>
        </div>
      </Card>

      {state === null ? (
        <div aria-label="加载中">
          <CardSkeleton />
          <CardSkeleton />
          <CardSkeleton />
        </div>
      ) : null}

      {state?.kind === "error" ? (
        <>
          <p className="text-sm text-danger">{state.reason}</p>
          <ImportEntry />
        </>
      ) : null}

      {state?.kind === "ready" && state.items.length > 0 ? (
        <>
          <p className="text-sm text-slate-600">共 {state.items.length} 条推荐。</p>
          {state.items.map((item) => (
            <RecommendationCard key={item.id} item={item} />
          ))}
        </>
      ) : null}

      {state?.kind === "ready" && state.items.length === 0 ? (
        filtered ? (
          <EmptyStateBlock
            icon={<IconSparkles className="h-6 w-6" />}
            title="当前筛选条件下没有推荐"
            description="可以调整方案、日期、等级或状态筛选，或清空筛选后查看全部推荐。"
            action={
              <Button
                onClick={() => {
                  setPlanId("");
                  setDate("");
                  setGrade("");
                  setStatus("");
                }}
              >
                清空筛选
              </Button>
            }
          />
        ) : (
          <RecommendationEmpty reason={state.emptyReason} linkOuts={state.linkOuts} />
        )
      ) : null}
    </main>
  );
}

function RecommendationEmpty({
  reason,
  linkOuts,
}: {
  reason: EmptyReason;
  linkOuts: LinkOutEntry[];
}) {
  return (
    <>
      <EmptyStateBlock
        icon={<IconSparkles className="h-6 w-6" />}
        title="今天还没有可推荐的岗位"
        description={
          reason !== "unknown" ? (
            <span className="notice mt-2 block text-left">
              <strong>{REASON_TEXTS[reason].title}：</strong>
              {REASON_TEXTS[reason].body}
            </span>
          ) : (
            <span className="mt-2 block text-left">
              <span className="block">可能的原因：</span>
              <ul className="mt-1.5 list-disc space-y-1 pl-5 text-left leading-relaxed">
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
            </span>
          )
        }
        action={
          <Link href="/plans" className={buttonClasses("secondary", "sm")}>
            去调整求职方案 →
          </Link>
        }
      />

      <h2 className="mt-7 mb-2.5 text-lg font-semibold text-slate-900">
        按你的方案去原平台搜索
      </h2>
      {linkOuts.length > 0 ? (
        <>
          <p className="text-sm text-slate-600">
            以下链接按你的方向、城市和薪资拼好了搜索条件，点开即到原平台查看：
          </p>
          <div className="my-2.5 flex flex-wrap gap-2">
            {linkOuts.map((entry) => (
              <a
                key={entry.url}
                className={buttonClasses("secondary", "sm")}
                href={entry.url}
                target="_blank"
                rel="noopener noreferrer"
              >
                {entry.label}
                <IconExternalLink className="h-3.5 w-3.5" />
              </a>
            ))}
          </div>
        </>
      ) : (
        <p className="text-sm text-slate-600">
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
      <h2 className="mt-7 mb-2.5 text-lg font-semibold text-slate-900">
        看到合适的岗位？手动导入
      </h2>
      <p className="text-sm text-slate-600">
        受平台政策限制，部分来源仅支持跳转到原平台或手动导入。
        把岗位链接或职位描述粘贴进来，系统会标准化后参与匹配打分。
      </p>
      <p className="my-2.5">
        <Link href="/jobs/import" className={buttonClasses("primary", "md")}>
          <IconImport className="h-4 w-4" />
          导入岗位
        </Link>
      </p>
    </>
  );
}
