"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { api } from "@/lib/api";
import { handleApiError } from "@/lib/api-error";
import {
  type EmptyReason,
  type LinkOutEntry,
  type RecommendationListItem,
  extractEmptyReason,
  extractLinkOuts,
  toItems,
} from "@/lib/types";

/**
 * 每日推荐（本阶段重点是空态，docs/05-ui-ux.md 第 14 节）：
 * 空态需区分 来源暂无数据 / 硬条件过严 / 能力降级 三种原因，
 * 并提供原平台搜索跳转（GET /recommendations 返回的 link_out）与手动导入入口。
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

type PageState =
  | { kind: "loading" }
  | { kind: "error"; reason: string }
  | {
      kind: "ready";
      items: RecommendationListItem[];
      linkOuts: LinkOutEntry[];
      emptyReason: EmptyReason;
    };

export default function RecommendationsPage() {
  const [state, setState] = useState<PageState>({ kind: "loading" });

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const data = await api.get<unknown>("/recommendations");
        if (cancelled) return;
        setState({
          kind: "ready",
          items: toItems<RecommendationListItem>(data),
          linkOuts: extractLinkOuts(data),
          emptyReason: extractEmptyReason(data),
        });
      } catch (err) {
        if (cancelled) return;
        setState({ kind: "error", reason: handleApiError(err) });
      }
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  return (
    <main className="page">
      <h1>每日推荐</h1>

      {state.kind === "loading" ? <p className="muted">加载中…</p> : null}

      {state.kind === "error" ? (
        <>
          <p className="error-text">{state.reason}</p>
          <ImportEntry />
        </>
      ) : null}

      {state.kind === "ready" && state.items.length > 0 ? (
        <>
          <p className="muted">共 {state.items.length} 条推荐。</p>
          {state.items.map((item) => (
            <div key={item.id} className="card">
              <div
                style={{
                  display: "flex",
                  justifyContent: "space-between",
                  gap: 8,
                  flexWrap: "wrap",
                }}
              >
                <strong>{item.job_title ?? item.title ?? "（无标题岗位）"}</strong>
                {item.score != null ? (
                  <span className="badge">
                    {item.score} 分{item.grade ? ` · ${item.grade}` : ""}
                  </span>
                ) : null}
              </div>
              <p className="muted" style={{ marginTop: 6 }}>
                {item.company ?? item.company_name ?? "公司未知"}
                {" · "}
                {item.city ??
                  (item.cities && item.cities.length > 0
                    ? item.cities.join(" / ")
                    : "城市未知")}
                {item.salary_text ? ` · ${item.salary_text}` : ""}
              </p>
            </div>
          ))}
        </>
      ) : null}

      {state.kind === "ready" && state.items.length === 0 ? (
        <EmptyState reason={state.emptyReason} linkOuts={state.linkOuts} />
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
