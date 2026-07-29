"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { useParams } from "next/navigation";
import { api } from "@/lib/api";
import { handleApiError } from "@/lib/api-error";
import { AnalysisSection } from "@/components/AnalysisSection";
import { FeedbackActions } from "@/components/RecommendationCard";
import {
  HARD_STATUS_META,
  type HardConditionItem,
  type MatchComponent,
  type MatchEvidence,
  type Recommendation,
  type RiskSignal,
  RISK_SEVERITY_NAMES,
  componentLabel,
  componentWeight,
  evidenceFactText,
  evidenceJobText,
  fmtTime,
  gradeBadgeClass,
  gradeName,
  hardConditionLabel,
  normalizeHardStatus,
  parseFeedback,
  recCity,
  recCompany,
  recGaps,
  recSalaryText,
  recSourceLinks,
  recSourceText,
  recTitle,
  riskName,
  riskUncertain,
  workModeLabel,
} from "@/lib/types";

/**
 * 岗位详情与最终报告（docs/05 第 7 节区块顺序）：
 * 1 原始岗位信息与全部来源链接 → 2 硬条件结果 → 3 总分与分项分数 →
 * 4 匹配证据 → 5 关键缺口 → 6 风险信号 → 标准分析（阶段 6，单模型）→ 反馈 →
 * 定制简历（阶段 7）。
 * 证据缺失一律显示「信息不足」，不显示编造内容（docs/05 第 14 节）。
 */

/** 详情响应可能包一层 { recommendation | item: {...} }，宽松展开 */
function unwrapRecommendation(data: unknown): Recommendation | null {
  if (!data || typeof data !== "object" || Array.isArray(data)) return null;
  const obj = data as Record<string, unknown>;
  for (const key of ["recommendation", "item", "data"]) {
    const nested = obj[key];
    if (
      nested &&
      typeof nested === "object" &&
      !Array.isArray(nested) &&
      typeof (nested as { id?: unknown }).id === "string"
    ) {
      return nested as Recommendation;
    }
  }
  if (typeof obj.id === "string") return obj as unknown as Recommendation;
  return null;
}

const INSUFFICIENT = "信息不足";

type PageState =
  | { kind: "loading" }
  | { kind: "error"; reason: string }
  | { kind: "ready"; rec: Recommendation };

export default function RecommendationDetailPage() {
  const params = useParams<{ id: string }>();
  const id = typeof params?.id === "string" ? params.id : "";
  const [state, setState] = useState<PageState>({ kind: "loading" });

  useEffect(() => {
    if (!id) return;
    let cancelled = false;
    (async () => {
      try {
        const data = await api.get<unknown>(
          `/recommendations/${encodeURIComponent(id)}`,
        );
        if (cancelled) return;
        const rec = unwrapRecommendation(data);
        if (rec) {
          setState({ kind: "ready", rec });
        } else {
          setState({ kind: "error", reason: "推荐数据格式异常，请稍后重试" });
        }
      } catch (err) {
        if (cancelled) return;
        setState({ kind: "error", reason: handleApiError(err) });
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [id]);

  return (
    <main className="page">
      <p style={{ marginBottom: 12 }}>
        <Link href="/recommendations" className="link">
          ← 返回每日推荐
        </Link>
      </p>

      {state.kind === "loading" ? <p className="muted">加载中…</p> : null}
      {state.kind === "error" ? <p className="error-text">{state.reason}</p> : null}
      {state.kind === "ready" ? <DetailBody rec={state.rec} /> : null}
    </main>
  );
}

function DetailBody({ rec }: { rec: Recommendation }) {
  return (
    <>
      <JobInfoSection rec={rec} />
      <HardConditionsSection rec={rec} />
      <ScoreSection rec={rec} />
      <EvidenceSection rec={rec} />
      <GapsSection rec={rec} />
      <RisksSection rec={rec} />

      <AnalysisSection recommendationId={rec.id} />

      <h2>反馈</h2>
      <div className="card">
        <p className="muted">你的反馈会用于调整后续推荐，可随时撤回。</p>
        <FeedbackActions recommendationId={rec.id} initial={parseFeedback(rec)} />
      </div>

      <h2>定制简历</h2>
      <div className="card">
        <button type="button" className="btn btn-primary" disabled>
          生成定制简历
        </button>
        <p className="muted" style={{ marginTop: 6 }}>
          岗位定制简历将在阶段 7 开放。
        </p>
      </div>
    </>
  );
}

/** 区块 1：原始岗位信息与全部来源链接 */
function JobInfoSection({ rec }: { rec: Recommendation }) {
  const sourceLinks = recSourceLinks(rec);
  const description = rec.job_description ?? rec.description ?? "";
  const published = rec.published_at ?? null;
  const firstSeen = rec.first_seen_at ?? rec.discovered_at ?? rec.created_at ?? null;
  const source = recSourceText(rec);

  return (
    <>
      <div
        style={{
          display: "flex",
          justifyContent: "space-between",
          alignItems: "flex-start",
          gap: 8,
          flexWrap: "wrap",
        }}
      >
        <h1>{recTitle(rec)}</h1>
        {rec.score != null || rec.grade ? (
          <span className={gradeBadgeClass(rec.grade)}>
            {rec.score != null ? `${rec.score} 分` : ""}
            {rec.grade ? `${rec.score != null ? " · " : ""}${gradeName(rec.grade)}` : ""}
          </span>
        ) : null}
      </div>

      <p style={{ marginTop: 4 }}>
        {recCompany(rec)}
        {" · "}
        {recCity(rec)}
        {rec.work_mode ? ` · ${workModeLabel(rec.work_mode)}` : ""}
        {" · "}
        {recSalaryText(rec)}
      </p>
      <p className="muted" style={{ marginTop: 4 }}>
        {published ? `发布于 ${fmtTime(published)}` : null}
        {published && firstSeen ? " · " : null}
        {firstSeen ? `发现于 ${fmtTime(firstSeen)}` : null}
        {(published || firstSeen) && source ? " · " : null}
        {source ? `主来源：${source}` : null}
        {!published && !firstSeen && !source ? `发布/来源${INSUFFICIENT}` : null}
      </p>

      <h2>原始岗位信息</h2>
      <div className="card">
        {description ? (
          <div className="evidence">{description}</div>
        ) : (
          <p className="muted">职位描述原文{INSUFFICIENT}。</p>
        )}
        <p style={{ marginTop: 8, fontSize: 14 }}>
          <strong>全部来源链接</strong>
          （去重后保留的所有来源）：
        </p>
        {sourceLinks.length > 0 ? (
          <div style={{ display: "flex", gap: 8, flexWrap: "wrap", marginTop: 8 }}>
            {sourceLinks.map((entry, i) => (
              <a
                key={`${entry.url}-${i}`}
                className="btn"
                href={entry.url}
                target="_blank"
                rel="noopener noreferrer"
              >
                {entry.label} ↗
              </a>
            ))}
          </div>
        ) : (
          <p className="muted" style={{ marginTop: 6 }}>
            暂无可展示的来源链接。
          </p>
        )}
      </div>
    </>
  );
}

/** 区块 2：硬条件结果（三态图标 + 证据） */
function HardConditionsSection({ rec }: { rec: Recommendation }) {
  const hard = rec.hard_conditions;
  const items: HardConditionItem[] = Array.isArray(hard?.items) ? hard.items : [];
  const overall = normalizeHardStatus(hard?.status);
  const overallMeta = HARD_STATUS_META[overall];

  return (
    <>
      <h2>硬条件结果</h2>
      <div className="card">
        {hard ? (
          <p style={{ fontSize: 14 }}>
            总体：
            <span className={overallMeta.badgeClass}>
              {overallMeta.icon} {overallMeta.label}
            </span>
          </p>
        ) : (
          <p className="muted">硬条件结果{INSUFFICIENT}。</p>
        )}
        {items.map((item, i) => {
          const meta = HARD_STATUS_META[normalizeHardStatus(item.status)];
          const evidence = item.evidence ?? item.detail ?? item.reason ?? "";
          return (
            <div
              key={`${item.code ?? item.name ?? "cond"}-${i}`}
              style={{
                borderTop: "1px solid rgba(127,127,127,0.25)",
                marginTop: 10,
                paddingTop: 10,
              }}
            >
              <p style={{ fontSize: 14 }}>
                <span className={meta.badgeClass}>
                  {meta.icon} {meta.label}
                </span>{" "}
                {hardConditionLabel(item)}
              </p>
              {evidence ? (
                <div className="evidence">{evidence}</div>
              ) : (
                <p className="muted" style={{ marginTop: 4 }}>
                  证据{INSUFFICIENT}。
                </p>
              )}
            </div>
          );
        })}
        {hard && items.length === 0 ? (
          <p className="muted" style={{ marginTop: 6 }}>
            未返回逐项硬条件明细。
          </p>
        ) : null}
      </div>
    </>
  );
}

/** 区块 3：总分与分项分数条（技能/经验/项目/方向/行业/偏好，各带权重） */
function ScoreSection({ rec }: { rec: Recommendation }) {
  const components: MatchComponent[] = Array.isArray(rec.components) ? rec.components : [];

  return (
    <>
      <h2>总分与分项分数</h2>
      <div className="card">
        <p>
          <span style={{ fontSize: 26, fontWeight: "bold" }}>
            {rec.score != null ? rec.score : "—"}
          </span>
          <span className="muted"> / 100</span>{" "}
          {rec.grade ? (
            <span className={gradeBadgeClass(rec.grade)}>{gradeName(rec.grade)}</span>
          ) : null}
        </p>
        <p className="muted" style={{ marginTop: 4 }}>
          分数代表岗位适配度，不代表面试或录用概率。
        </p>

        {components.length > 0 ? (
          <div style={{ marginTop: 12, display: "grid", gap: 10 }}>
            {components.map((c, i) => {
              const weight = componentWeight(c);
              const score = typeof c.score === "number" ? Math.max(0, Math.min(100, c.score)) : null;
              return (
                <div key={`${c.name ?? c.code ?? "component"}-${i}`}>
                  <div
                    style={{
                      display: "flex",
                      justifyContent: "space-between",
                      fontSize: 14,
                      marginBottom: 4,
                    }}
                  >
                    <span>
                      {componentLabel(c)}
                      {weight != null ? (
                        <span className="muted">（权重 {weight}%）</span>
                      ) : null}
                    </span>
                    <span>{score != null ? `${score} 分` : INSUFFICIENT}</span>
                  </div>
                  <div className="bar" aria-hidden="true">
                    <div className="bar-fill" style={{ width: `${score ?? 0}%` }} />
                  </div>
                </div>
              );
            })}
          </div>
        ) : (
          <p className="muted" style={{ marginTop: 10 }}>
            分项分数{INSUFFICIENT}。
          </p>
        )}
      </div>
    </>
  );
}

/** 区块 4：匹配证据（简历事实 ↔ 岗位原文对照） */
function EvidenceSection({ rec }: { rec: Recommendation }) {
  const components: MatchComponent[] = Array.isArray(rec.components) ? rec.components : [];
  const groups = components
    .map((c) => ({
      label: componentLabel(c),
      evidence: (Array.isArray(c.evidence) ? c.evidence : []) as MatchEvidence[],
    }))
    .filter((g) => g.evidence.length > 0);

  return (
    <>
      <h2>匹配证据</h2>
      {groups.length === 0 ? (
        <div className="card">
          <p className="muted">匹配证据{INSUFFICIENT}，不展示无证据的结论。</p>
        </div>
      ) : (
        groups.map((group) => (
          <div className="card" key={group.label}>
            <strong style={{ fontSize: 14 }}>{group.label}</strong>
            {group.evidence.map((e, i) => {
              const fact = evidenceFactText(e);
              const jobText = evidenceJobText(e);
              return (
                <div
                  key={i}
                  style={{
                    borderTop: i > 0 ? "1px solid rgba(127,127,127,0.25)" : undefined,
                    marginTop: 10,
                    paddingTop: i > 0 ? 10 : 0,
                  }}
                >
                  {e.claim ? <p style={{ fontSize: 14 }}>{e.claim}</p> : null}
                  <p className="muted" style={{ marginTop: 6, fontSize: 13 }}>
                    简历事实：
                  </p>
                  {fact ? (
                    <div className="evidence">{fact}</div>
                  ) : (
                    <p className="muted">{INSUFFICIENT}</p>
                  )}
                  <p className="muted" style={{ marginTop: 6, fontSize: 13 }}>
                    岗位原文：
                  </p>
                  {jobText ? (
                    <div className="evidence">{jobText}</div>
                  ) : (
                    <p className="muted">{INSUFFICIENT}</p>
                  )}
                  {e.uncertainty ? (
                    <p className="muted" style={{ marginTop: 4 }}>
                      不确定性：{e.uncertainty}
                    </p>
                  ) : null}
                </div>
              );
            })}
          </div>
        ))
      )}
    </>
  );
}

/** 区块 5：关键缺口 */
function GapsSection({ rec }: { rec: Recommendation }) {
  const gaps = recGaps(rec);
  return (
    <>
      <h2>关键缺口</h2>
      <div className="card">
        {gaps.length > 0 ? (
          <ul style={{ margin: "0 0 0 18px", lineHeight: 1.8, fontSize: 14 }}>
            {gaps.map((gap, i) => (
              <li key={i}>{gap}</li>
            ))}
          </ul>
        ) : (
          <p className="muted">关键缺口{INSUFFICIENT}。</p>
        )}
      </div>
    </>
  );
}

/** 区块 6：风险信号与原文证据（不确定性必须标注） */
function RisksSection({ rec }: { rec: Recommendation }) {
  const risks: RiskSignal[] = Array.isArray(rec.risks) ? rec.risks : [];
  return (
    <>
      <h2>风险信号</h2>
      {risks.length === 0 ? (
        <div className="card">
          <p className="muted">未识别到明显风险信号。</p>
        </div>
      ) : (
        risks.map((risk, i) => {
          const severity = RISK_SEVERITY_NAMES[(risk.severity ?? "").toLowerCase()];
          return (
            <div className="card" key={`${risk.code ?? risk.name ?? "risk"}-${i}`}>
              <p style={{ fontSize: 14 }}>
                <span className="badge badge-busy">{riskName(risk)}</span>
                {severity ? <span className="muted">（{severity}风险）</span> : null}
                {riskUncertain(risk) ? (
                  <span className="muted"> · 该判断存在不确定性，仅供参考</span>
                ) : null}
              </p>
              {risk.evidence ? (
                <div className="evidence">{risk.evidence}</div>
              ) : (
                <p className="muted" style={{ marginTop: 6 }}>
                  原文证据{INSUFFICIENT}。
                </p>
              )}
              {risk.note ? (
                <p className="muted" style={{ marginTop: 4 }}>
                  {risk.note}
                </p>
              ) : null}
            </div>
          );
        })
      )}
    </>
  );
}
