"use client";

import { useEffect, useMemo, useState } from "react";
import Link from "next/link";
import { useParams } from "next/navigation";
import { api } from "@/lib/api";
import { codeMessage, handleApiError } from "@/lib/api-error";
import {
  type FactCandidate,
  type ParseStatus,
  FACT_GROUP_ORDER,
  factGroupName,
  normalizeFactGroup,
} from "@/lib/types";

/**
 * 候选事实确认页（docs/05-ui-ux.md 第 8 节）。
 * 按 个人概况/技能/工作经历/项目/教育 分组；每张卡：解析出的值 + 原文证据片段。
 * 动作只有三个：接受 / 编辑后接受 / 拒绝。不提供任何「帮我估算」类功能。
 * 底部「提交确认」批量 POST /resumes/{id}/facts/confirm。
 */

type Decision =
  | { action: "accept" }
  | { action: "edit"; value: string }
  | { action: "reject" };

type PageState =
  | { kind: "loading" }
  | { kind: "parsing" }
  | { kind: "parse_failed"; reason: string }
  | { kind: "error"; reason: string }
  | { kind: "ready"; candidates: FactCandidate[] };

function evidenceText(c: FactCandidate): string {
  return c.source_span?.text ?? c.evidence_text ?? "";
}

export default function ResumeFactsPage() {
  const params = useParams<{ id: string }>();
  const resumeId = params?.id;

  const [state, setState] = useState<PageState>({ kind: "loading" });
  const [decisions, setDecisions] = useState<Record<string, Decision>>({});
  // 行内编辑：candidateId → 正在编辑的草稿值；null 表示未开启编辑表单
  const [editingDrafts, setEditingDrafts] = useState<Record<string, string>>({});
  const [submitting, setSubmitting] = useState(false);
  const [submitError, setSubmitError] = useState<string | null>(null);
  const [submitted, setSubmitted] = useState(false);

  useEffect(() => {
    if (!resumeId) return;
    let cancelled = false;
    (async () => {
      try {
        const parse = await api.get<ParseStatus>(`/resumes/${resumeId}/parse`);
        if (cancelled) return;
        const status = parse?.status?.toLowerCase();
        if (status === "failed") {
          setState({
            kind: "parse_failed",
            reason: codeMessage(parse?.error_code, parse?.error_message),
          });
          return;
        }
        const candidates = parse?.candidates ?? parse?.items;
        if (!Array.isArray(candidates)) {
          setState({ kind: "parsing" });
          return;
        }
        setState({ kind: "ready", candidates });
      } catch (err) {
        if (cancelled) return;
        setState({ kind: "error", reason: handleApiError(err) });
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [resumeId]);

  const grouped = useMemo(() => {
    if (state.kind !== "ready") return [];
    const map = new Map<string, FactCandidate[]>();
    for (const c of state.candidates) {
      const key = normalizeFactGroup(c.group ?? c.category);
      const list = map.get(key) ?? [];
      list.push(c);
      map.set(key, list);
    }
    const orderedKeys = [
      ...FACT_GROUP_ORDER.filter((k) => map.has(k)),
      ...[...map.keys()].filter(
        (k) => !(FACT_GROUP_ORDER as readonly string[]).includes(k),
      ),
    ];
    return orderedKeys.map((key) => ({
      key,
      name: factGroupName(key),
      items: map.get(key)!,
    }));
  }, [state]);

  const decidedCount = Object.keys(decisions).length;
  const totalCount = state.kind === "ready" ? state.candidates.length : 0;

  function setDecision(id: string, decision: Decision | null) {
    setDecisions((prev) => {
      const next = { ...prev };
      if (decision === null) delete next[id];
      else next[id] = decision;
      return next;
    });
  }

  function openEdit(c: FactCandidate) {
    const current = decisions[c.id];
    setEditingDrafts((prev) => ({
      ...prev,
      [c.id]: current?.action === "edit" ? current.value : (c.value ?? ""),
    }));
  }

  function closeEdit(id: string) {
    setEditingDrafts((prev) => {
      const next = { ...prev };
      delete next[id];
      return next;
    });
  }

  function confirmEdit(id: string) {
    const value = editingDrafts[id]?.trim();
    if (!value) return;
    setDecision(id, { action: "edit", value });
    closeEdit(id);
  }

  async function submitConfirm() {
    if (!resumeId || decidedCount === 0) return;
    setSubmitting(true);
    setSubmitError(null);
    try {
      await api.post(`/resumes/${resumeId}/facts/confirm`, {
        decisions: Object.entries(decisions).map(([candidate_id, d]) => ({
          candidate_id,
          action: d.action,
          ...(d.action === "edit" ? { value: d.value } : {}),
        })),
      });
      setSubmitted(true);
    } catch (err) {
      setSubmitError(handleApiError(err));
    } finally {
      setSubmitting(false);
    }
  }

  function renderCard(c: FactCandidate) {
    const decision = decisions[c.id];
    const editing = editingDrafts[c.id] !== undefined;
    const evidence = evidenceText(c);

    return (
      <div className="card" key={c.id}>
        {c.label && <p style={{ fontWeight: "bold", marginBottom: 4 }}>{c.label}</p>}
        <p style={{ lineHeight: 1.7, wordBreak: "break-word" }}>
          {decision?.action === "edit" ? decision.value : (c.value ?? "（未解析出值）")}
        </p>
        {decision?.action === "edit" && (
          <p className="muted" style={{ marginTop: 2 }}>
            已编辑（原解析值：{c.value ?? "无"}）
          </p>
        )}

        {evidence ? (
          <blockquote className="evidence">原文：{evidence}</blockquote>
        ) : (
          <p className="muted" style={{ margin: "8px 0" }}>
            无原文证据片段
          </p>
        )}

        {editing ? (
          <div style={{ marginTop: 8 }}>
            <label className="field">
              <span>编辑后的内容（仅可修正为你确认真实的信息）</span>
              <input
                className="input"
                value={editingDrafts[c.id] ?? ""}
                onChange={(e) =>
                  setEditingDrafts((prev) => ({ ...prev, [c.id]: e.target.value }))
                }
              />
            </label>
            <div style={{ display: "flex", gap: 8 }}>
              <button
                type="button"
                className="btn btn-primary"
                disabled={!editingDrafts[c.id]?.trim()}
                onClick={() => confirmEdit(c.id)}
              >
                确认修改并接受
              </button>
              <button type="button" className="btn" onClick={() => closeEdit(c.id)}>
                取消
              </button>
            </div>
          </div>
        ) : (
          <div style={{ display: "flex", gap: 8, marginTop: 8, flexWrap: "wrap", alignItems: "center" }}>
            <button
              type="button"
              className={decision?.action === "accept" ? "btn btn-primary" : "btn"}
              onClick={() =>
                setDecision(c.id, decision?.action === "accept" ? null : { action: "accept" })
              }
            >
              {decision?.action === "accept" ? "✓ 已接受" : "接受"}
            </button>
            <button type="button" className="btn" onClick={() => openEdit(c)}>
              {decision?.action === "edit" ? "重新编辑" : "编辑后接受"}
            </button>
            <button
              type="button"
              className={decision?.action === "reject" ? "btn btn-danger" : "btn"}
              onClick={() =>
                setDecision(c.id, decision?.action === "reject" ? null : { action: "reject" })
              }
            >
              {decision?.action === "reject" ? "✗ 已拒绝" : "拒绝"}
            </button>
            {decision?.action === "edit" && (
              <span style={{ color: "var(--color-success)", fontSize: 14 }}>✓ 已编辑并接受</span>
            )}
          </div>
        )}
      </div>
    );
  }

  return (
    <main className="page">
      <h1>确认候选事实</h1>
      <p className="muted">
        以下内容由系统从你的简历中解析，仅在你逐条确认后才会进入事实库。
        对不准确的内容请编辑修正或拒绝；系统不会替你估算或补造任何数据。
      </p>
      <p style={{ margin: "8px 0" }}>
        <Link href="/resumes" className="link">
          ← 返回简历工作台
        </Link>
      </p>

      {state.kind === "loading" && <p className="muted">加载中…</p>}

      {state.kind === "parsing" && (
        <div className="notice">
          这份简历还在解析中，请稍后刷新本页，或返回简历工作台查看进度。
        </div>
      )}

      {state.kind === "parse_failed" && <p className="error-text">{state.reason}</p>}
      {state.kind === "error" && <p className="error-text">{state.reason}</p>}

      {state.kind === "ready" && submitted && (
        <div className="card">
          <p style={{ lineHeight: 1.7 }}>
            ✓ 已提交确认。接受的内容已进入事实库。
          </p>
          <div style={{ display: "flex", gap: 8, marginTop: 8, flexWrap: "wrap" }}>
            <Link href="/profile/facts" className="btn btn-primary">
              查看事实库
            </Link>
            <Link href="/resumes" className="btn">
              返回简历工作台
            </Link>
          </div>
        </div>
      )}

      {state.kind === "ready" && !submitted && (
        <>
          {state.candidates.length === 0 ? (
            <div className="card">
              <p className="muted">
                未从这份简历中解析出候选事实。可尝试上传内容更完整的文本型简历。
              </p>
            </div>
          ) : (
            <>
              {grouped.map((group) => (
                <section key={group.key}>
                  <h2>{group.name}</h2>
                  {group.items.map(renderCard)}
                </section>
              ))}

              {submitError && <p className="error-text">{submitError}</p>}

              <div
                style={{
                  display: "flex",
                  gap: 12,
                  alignItems: "center",
                  marginTop: 24,
                  flexWrap: "wrap",
                }}
              >
                <button
                  type="button"
                  className="btn btn-primary"
                  disabled={submitting || decidedCount === 0}
                  onClick={submitConfirm}
                >
                  {submitting ? "提交中…" : "提交确认"}
                </button>
                <span className="muted">
                  已处理 {decidedCount} / {totalCount} 条
                  {decidedCount < totalCount ? "，未处理的条目本次不会提交" : ""}
                </span>
              </div>
            </>
          )}
        </>
      )}
    </main>
  );
}
