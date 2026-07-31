"use client";

import { useEffect, useState, type ReactNode } from "react";
import Link from "next/link";
import { useParams } from "next/navigation";
import { api } from "@/lib/api";
import { handleApiError } from "@/lib/api-error";
import { AnalysisSection } from "@/components/AnalysisSection";
import { TailorSection } from "@/components/TailorSection";
import { FeedbackActions, ScoreBadge, gradeTone } from "@/components/RecommendationCard";
import Card from "@/components/ui/Card";
import Badge, { SyntheticBadge, type BadgeTone } from "@/components/ui/Badge";
import { buttonClasses } from "@/components/ui/Button";
import Skeleton from "@/components/ui/Skeleton";
import { IconArrowLeft, IconExternalLink } from "@/components/ui/icons";
import {
  HARD_STATUS_META,
  type HardConditionItem,
  type HardConditionStatus,
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
  gradeName,
  hardConditionLabel,
  isSyntheticSeed,
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

/** 硬条件三态 → 徽标语义色 */
const HARD_STATUS_TONES: Record<HardConditionStatus, BadgeTone> = {
  passed: "success",
  failed: "danger",
  unknown: "warning",
};

/** 区块标题 + 内容的统一包装 */
function Section({ title, children }: { title: string; children: ReactNode }) {
  return (
    <section className="mt-7">
      <h2 className="mb-2.5 text-lg font-semibold text-slate-900">{title}</h2>
      {children}
    </section>
  );
}

/** 原文证据片段（左侧主色竖线 + 浅灰底） */
function Evidence({ children }: { children: ReactNode }) {
  return (
    <div className="my-2 whitespace-pre-wrap break-words rounded-r-lg border-l-3 border-indigo-200 bg-slate-50 px-3 py-2 text-[13px] leading-relaxed text-slate-600">
      {children}
    </div>
  );
}

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
      <p className="mb-4">
        <Link
          href="/recommendations"
          className="inline-flex items-center gap-1.5 text-sm font-medium text-slate-600 transition-colors hover:text-primary"
        >
          <IconArrowLeft className="h-4 w-4" />
          返回每日推荐
        </Link>
      </p>

      {state.kind === "loading" ? (
        <div aria-label="加载中">
          <Skeleton className="h-8 w-2/3" />
          <Skeleton className="mt-3 h-4 w-1/2" />
          <Skeleton className="mt-6 h-40 w-full rounded-xl" />
          <Skeleton className="mt-4 h-40 w-full rounded-xl" />
        </div>
      ) : null}
      {state.kind === "error" ? (
        <p className="text-sm text-danger">{state.reason}</p>
      ) : null}
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

      <Section title="反馈">
        <Card>
          <p className="mb-3 text-sm text-slate-600">
            你的反馈会用于调整后续推荐，可随时撤回。
          </p>
          <FeedbackActions recommendationId={rec.id} initial={parseFeedback(rec)} />
        </Card>
      </Section>

      <TailorSection recommendationId={rec.id} />
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
      <div className="flex flex-wrap items-start justify-between gap-x-4 gap-y-2">
        <span className="inline-flex flex-wrap items-center gap-2.5">
          <h1 className="text-2xl font-bold tracking-tight text-slate-900">
            {recTitle(rec)}
          </h1>
          {isSyntheticSeed(rec) ? <SyntheticBadge /> : null}
        </span>
        <ScoreBadge item={rec} />
      </div>

      <p className="mt-1.5 text-sm text-slate-700">
        {recCompany(rec)}
        {" · "}
        {recCity(rec)}
        {rec.work_mode ? ` · ${workModeLabel(rec.work_mode)}` : ""}
        {" · "}
        <span className="font-medium">{recSalaryText(rec)}</span>
      </p>
      <p className="mt-1 text-xs text-slate-400">
        {published ? `发布于 ${fmtTime(published)}` : null}
        {published && firstSeen ? " · " : null}
        {firstSeen ? `发现于 ${fmtTime(firstSeen)}` : null}
        {(published || firstSeen) && source ? " · " : null}
        {source ? `主来源：${source}` : null}
        {!published && !firstSeen && !source ? `发布/来源${INSUFFICIENT}` : null}
      </p>

      <Section title="原始岗位信息">
        <Card>
          {description ? (
            <Evidence>{description}</Evidence>
          ) : (
            <p className="text-sm text-slate-600">职位描述原文{INSUFFICIENT}。</p>
          )}
          <p className="mt-3 text-sm text-slate-900">
            <strong>全部来源链接</strong>
            <span className="text-slate-600">（去重后保留的所有来源）：</span>
          </p>
          {sourceLinks.length > 0 ? (
            <div className="mt-2 flex flex-wrap gap-2">
              {sourceLinks.map((entry, i) => (
                <a
                  key={`${entry.url}-${i}`}
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
          ) : (
            <p className="mt-1.5 text-sm text-slate-400">暂无可展示的来源链接。</p>
          )}
        </Card>
      </Section>
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
    <Section title="硬条件结果">
      <Card>
        {hard ? (
          <p className="flex items-center gap-2 text-sm text-slate-700">
            总体：
            <Badge tone={HARD_STATUS_TONES[overall]}>
              {overallMeta.icon} {overallMeta.label}
            </Badge>
          </p>
        ) : (
          <p className="text-sm text-slate-600">硬条件结果{INSUFFICIENT}。</p>
        )}
        {items.map((item, i) => {
          const status = normalizeHardStatus(item.status);
          const meta = HARD_STATUS_META[status];
          const evidence = item.evidence ?? item.detail ?? item.reason ?? "";
          return (
            <div
              key={`${item.code ?? item.name ?? "cond"}-${i}`}
              className="mt-3 border-t border-slate-100 pt-3"
            >
              <p className="flex items-center gap-2 text-sm text-slate-900">
                <Badge tone={HARD_STATUS_TONES[status]}>
                  {meta.icon} {meta.label}
                </Badge>
                <span className="font-medium">{hardConditionLabel(item)}</span>
              </p>
              {evidence ? (
                <Evidence>{evidence}</Evidence>
              ) : (
                <p className="mt-1 text-sm text-slate-400">证据{INSUFFICIENT}。</p>
              )}
            </div>
          );
        })}
        {hard && items.length === 0 ? (
          <p className="mt-1.5 text-sm text-slate-400">未返回逐项硬条件明细。</p>
        ) : null}
      </Card>
    </Section>
  );
}

/** 区块 3：总分与分项分数条（技能/经验/项目/方向/行业/偏好，各带权重） */
function ScoreSection({ rec }: { rec: Recommendation }) {
  const components: MatchComponent[] = Array.isArray(rec.components) ? rec.components : [];

  return (
    <Section title="总分与分项分数">
      <Card>
        <p className="flex flex-wrap items-baseline gap-2">
          <span className="text-3xl font-bold tracking-tight text-slate-900">
            {rec.score != null ? rec.score : "—"}
          </span>
          <span className="text-sm text-slate-400">/ 100</span>
          {rec.grade ? (
            <Badge tone={gradeTone(rec.grade)}>{gradeName(rec.grade)}</Badge>
          ) : null}
        </p>
        <p className="mt-1 text-sm text-slate-600">
          分数代表岗位适配度，不代表面试或录用概率。
        </p>

        {components.length > 0 ? (
          <div className="mt-4 grid gap-3">
            {components.map((c, i) => {
              const weight = componentWeight(c);
              const score =
                typeof c.score === "number" ? Math.max(0, Math.min(100, c.score)) : null;
              return (
                <div key={`${c.name ?? c.code ?? "component"}-${i}`}>
                  <div className="mb-1 flex justify-between text-sm">
                    <span className="text-slate-700">
                      {componentLabel(c)}
                      {weight != null ? (
                        <span className="text-slate-400">（权重 {weight}%）</span>
                      ) : null}
                    </span>
                    <span className="font-medium text-slate-900">
                      {score != null ? `${score} 分` : INSUFFICIENT}
                    </span>
                  </div>
                  <div
                    className="h-2 flex-1 overflow-hidden rounded-full bg-slate-200"
                    aria-hidden="true"
                  >
                    <div
                      className="h-full rounded-full bg-primary"
                      style={{ width: `${score ?? 0}%` }}
                    />
                  </div>
                </div>
              );
            })}
          </div>
        ) : (
          <p className="mt-3 text-sm text-slate-400">分项分数{INSUFFICIENT}。</p>
        )}
      </Card>
    </Section>
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
    <Section title="匹配证据">
      {groups.length === 0 ? (
        <Card>
          <p className="text-sm text-slate-600">
            匹配证据{INSUFFICIENT}，不展示无证据的结论。
          </p>
        </Card>
      ) : (
        groups.map((group) => (
          <Card key={group.label} className="mb-3">
            <Badge tone="info">{group.label}</Badge>
            {group.evidence.map((e, i) => {
              const fact = evidenceFactText(e);
              const jobText = evidenceJobText(e);
              return (
                <div
                  key={i}
                  className={i > 0 ? "mt-3 border-t border-slate-100 pt-3" : "mt-3"}
                >
                  {e.claim ? (
                    <p className="text-sm font-medium text-slate-900">{e.claim}</p>
                  ) : null}
                  <div className="mt-2 grid gap-3 sm:grid-cols-2">
                    <div>
                      <p className="text-[13px] font-medium text-slate-500">简历事实：</p>
                      {fact ? (
                        <Evidence>{fact}</Evidence>
                      ) : (
                        <p className="text-sm text-slate-400">{INSUFFICIENT}</p>
                      )}
                    </div>
                    <div>
                      <p className="text-[13px] font-medium text-slate-500">岗位原文：</p>
                      {jobText ? (
                        <Evidence>{jobText}</Evidence>
                      ) : (
                        <p className="text-sm text-slate-400">{INSUFFICIENT}</p>
                      )}
                    </div>
                  </div>
                  {e.uncertainty ? (
                    <p className="mt-1 text-sm text-amber-700">
                      不确定性：{e.uncertainty}
                    </p>
                  ) : null}
                </div>
              );
            })}
          </Card>
        ))
      )}
    </Section>
  );
}

/** 区块 5：关键缺口 */
function GapsSection({ rec }: { rec: Recommendation }) {
  const gaps = recGaps(rec);
  return (
    <Section title="关键缺口">
      <Card>
        {gaps.length > 0 ? (
          <ul className="list-disc space-y-1.5 pl-5 text-sm leading-relaxed text-slate-700">
            {gaps.map((gap, i) => (
              <li key={i}>{gap}</li>
            ))}
          </ul>
        ) : (
          <p className="text-sm text-slate-600">关键缺口{INSUFFICIENT}。</p>
        )}
      </Card>
    </Section>
  );
}

/** 区块 6：风险信号与原文证据（不确定性必须标注） */
function RisksSection({ rec }: { rec: Recommendation }) {
  const risks: RiskSignal[] = Array.isArray(rec.risks) ? rec.risks : [];
  return (
    <Section title="风险信号">
      {risks.length === 0 ? (
        <Card>
          <p className="text-sm text-slate-600">未识别到明显风险信号。</p>
        </Card>
      ) : (
        risks.map((risk, i) => {
          const severity = RISK_SEVERITY_NAMES[(risk.severity ?? "").toLowerCase()];
          return (
            <Card className="mb-3" key={`${risk.code ?? risk.name ?? "risk"}-${i}`}>
              <p className="flex flex-wrap items-center gap-1.5 text-sm">
                <Badge tone="warning">{riskName(risk)}</Badge>
                {severity ? (
                  <span className="text-slate-600">（{severity}风险）</span>
                ) : null}
                {riskUncertain(risk) ? (
                  <span className="text-slate-400">
                    · 该判断存在不确定性，仅供参考
                  </span>
                ) : null}
              </p>
              {risk.evidence ? (
                <Evidence>{risk.evidence}</Evidence>
              ) : (
                <p className="mt-1.5 text-sm text-slate-400">原文证据{INSUFFICIENT}。</p>
              )}
              {risk.note ? (
                <p className="mt-1 text-sm text-slate-600">{risk.note}</p>
              ) : null}
            </Card>
          );
        })
      )}
    </Section>
  );
}
