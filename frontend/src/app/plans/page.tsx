"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { api } from "@/lib/api";
import { handleApiError } from "@/lib/api-error";
import {
  type SearchPlan,
  cityLabel,
  directionLabel,
  isPlanActive,
  planSalaryText,
  planStatusName,
  toItems,
  workModeLabel,
} from "@/lib/types";

/**
 * 求职方案列表（docs/04-api.md 第 5 节、docs/01-prd.md 7.3）。
 * 每用户最多 3 个方案，最多 3 个处于进行中（active）。
 */

const MAX_PLANS = 3;

function statusBadgeClass(status?: string): string {
  const s = (status ?? "").toLowerCase();
  if (s === "active") return "badge badge-ok";
  if (s === "paused" || s === "draft") return "badge badge-busy";
  return "badge";
}

export default function PlansPage() {
  const [plans, setPlans] = useState<SearchPlan[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const data = await api.get<unknown>("/search-plans");
        if (cancelled) return;
        setPlans(toItems<SearchPlan>(data));
        setError(null);
      } catch (err) {
        if (cancelled) return;
        setPlans([]);
        setError(handleApiError(err));
      }
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  const loading = plans === null;
  const items = plans ?? [];
  const activeCount = items.filter(isPlanActive).length;
  const full = items.length >= MAX_PLANS;

  return (
    <main className="page">
      <h1>求职方案</h1>
      <p className="muted">
        每个账号最多创建 {MAX_PLANS} 个求职方案，最多 {MAX_PLANS} 个同时处于进行中；
        每个方案独立设置方向、城市、薪资、办公方式和公司偏好。
      </p>

      <div style={{ margin: "16px 0" }}>
        {full ? (
          <>
            <button type="button" className="btn btn-primary" disabled>
              新建方案
            </button>
            <p className="muted" style={{ marginTop: 6 }}>
              已达到 {MAX_PLANS} 个方案上限，请先删除一个不再使用的方案再新建。
            </p>
          </>
        ) : (
          <Link href="/plans/new" className="btn btn-primary">
            新建方案
          </Link>
        )}
      </div>

      {activeCount >= MAX_PLANS ? (
        <div className="notice">
          当前已有 {activeCount} 个进行中的方案（上限 {MAX_PLANS} 个）。
          若要激活其他方案，请先在方案详情里停用或删除一个进行中的方案。
        </div>
      ) : null}

      {error ? <p className="error-text">{error}</p> : null}

      {loading ? (
        <p className="muted">加载中…</p>
      ) : items.length === 0 && !error ? (
        <div className="card">
          <p>还没有求职方案。</p>
          <p className="muted" style={{ marginTop: 4 }}>
            创建第一个方案后，系统会按方向、城市和薪资每天为你筛选岗位。
          </p>
        </div>
      ) : (
        items.map((plan) => (
          <Link
            key={plan.id}
            href={`/plans/${encodeURIComponent(plan.id)}`}
            style={{ display: "block" }}
          >
            <div className="card">
              <div
                style={{
                  display: "flex",
                  justifyContent: "space-between",
                  alignItems: "center",
                  gap: 8,
                  flexWrap: "wrap",
                }}
              >
                <strong>
                  {plan.name || directionLabel(plan.role_family ?? plan.direction)}
                </strong>
                <span style={{ display: "flex", gap: 8, alignItems: "center" }}>
                  {plan.priority != null ? (
                    <span className="badge">优先级 {plan.priority}</span>
                  ) : null}
                  <span className={statusBadgeClass(plan.status)}>
                    {planStatusName(plan.status)}
                  </span>
                </span>
              </div>
              <p style={{ marginTop: 8, fontSize: 14 }}>
                方向：{directionLabel(plan.role_family ?? plan.direction)}
              </p>
              <p className="muted" style={{ marginTop: 4 }}>
                城市：
                {(plan.city_codes ?? plan.cities ?? []).length > 0
                  ? (plan.city_codes ?? plan.cities ?? []).map(cityLabel).join(" / ")
                  : "未设置"}
                {" · "}
                {planSalaryText(plan)}
                {(plan.work_modes ?? []).length > 0
                  ? ` · ${(plan.work_modes ?? []).map(workModeLabel).join(" / ")}`
                  : ""}
              </p>
            </div>
          </Link>
        ))
      )}
    </main>
  );
}
