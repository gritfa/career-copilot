"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { useParams, useRouter } from "next/navigation";
import { api } from "@/lib/api";
import { handleApiError } from "@/lib/api-error";
import ConfirmDialog from "@/components/ConfirmDialog";
import PlanForm, { type PlanFormPayload } from "@/components/PlanForm";
import {
  type CompanyPreferenceKind,
  type CompanyPreferenceLists,
  type SearchPlan,
  COMPANY_PREFERENCE_KINDS,
  emptyCompanyPreferenceLists,
  isPlanActive,
  parseCompanyPreferences,
  planStatusName,
} from "@/lib/types";

/**
 * 求职方案详情（docs/04-api.md 第 5 节）：
 * - PATCH /search-plans/{id} 保存设置
 * - GET/PUT /search-plans/{id}/company-preferences 关注/优先/屏蔽
 * - POST /search-plans/{id}/activate 激活并触发基础推荐刷新
 * - DELETE /search-plans/{id} 删除（二次确认；不自动删除简历版本）
 */

type LoadState =
  | { kind: "loading" }
  | { kind: "error"; reason: string }
  | { kind: "ready"; plan: SearchPlan };

export default function PlanDetailPage() {
  const params = useParams<{ id: string }>();
  const router = useRouter();
  const planId = params?.id ?? "";
  const planPath = `/search-plans/${encodeURIComponent(planId)}`;

  const [state, setState] = useState<LoadState>({ kind: "loading" });
  const [reloadKey, setReloadKey] = useState(0);

  // 保存基本设置
  const [saving, setSaving] = useState(false);
  const [saveError, setSaveError] = useState<string | null>(null);
  const [saveDone, setSaveDone] = useState(false);

  // 公司偏好
  const [prefs, setPrefs] = useState<CompanyPreferenceLists>(
    emptyCompanyPreferenceLists(),
  );
  const [prefsLoaded, setPrefsLoaded] = useState(false);
  const [prefsError, setPrefsError] = useState<string | null>(null);
  const [prefsSaving, setPrefsSaving] = useState(false);
  const [prefsSaveDone, setPrefsSaveDone] = useState(false);
  const [drafts, setDrafts] = useState<Record<CompanyPreferenceKind, string>>({
    follow: "",
    priority: "",
    block: "",
  });

  // 激活 / 删除
  const [activating, setActivating] = useState(false);
  const [actionError, setActionError] = useState<string | null>(null);
  const [confirmDelete, setConfirmDelete] = useState(false);
  const [deleting, setDeleting] = useState(false);

  useEffect(() => {
    if (!planId) return;
    let cancelled = false;
    (async () => {
      try {
        const plan = await api.get<SearchPlan>(planPath);
        if (!cancelled) setState({ kind: "ready", plan });
      } catch (err) {
        if (!cancelled) setState({ kind: "error", reason: handleApiError(err) });
      }
    })();
    (async () => {
      try {
        const data = await api.get<unknown>(`${planPath}/company-preferences`);
        if (cancelled) return;
        setPrefs(parseCompanyPreferences(data));
        setPrefsError(null);
      } catch (err) {
        if (cancelled) return;
        // 公司偏好加载失败不阻断方案编辑
        setPrefsError(handleApiError(err));
      } finally {
        if (!cancelled) setPrefsLoaded(true);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [planId, planPath, reloadKey]);

  async function handleSave(payload: PlanFormPayload) {
    setSaving(true);
    setSaveError(null);
    setSaveDone(false);
    try {
      await api.patch(planPath, payload);
      setSaveDone(true);
      setReloadKey((k) => k + 1);
    } catch (err) {
      setSaveError(handleApiError(err));
    } finally {
      setSaving(false);
    }
  }

  function addCompany(kind: CompanyPreferenceKind) {
    const name = drafts[kind].trim();
    if (!name) return;
    setPrefs((prev) => {
      if (prev[kind].includes(name)) return prev;
      // 同一公司只保留一种偏好：加入某列表时从其他列表移除
      const next = emptyCompanyPreferenceLists();
      for (const k of ["follow", "priority", "block"] as const) {
        next[k] = prev[k].filter((n) => n !== name);
      }
      next[kind] = [...next[kind], name];
      return next;
    });
    setDrafts((prev) => ({ ...prev, [kind]: "" }));
    setPrefsSaveDone(false);
  }

  function removeCompany(kind: CompanyPreferenceKind, name: string) {
    setPrefs((prev) => ({ ...prev, [kind]: prev[kind].filter((n) => n !== name) }));
    setPrefsSaveDone(false);
  }

  async function savePreferences() {
    setPrefsSaving(true);
    setPrefsError(null);
    setPrefsSaveDone(false);
    try {
      const items = (["follow", "priority", "block"] as const).flatMap((kind) =>
        prefs[kind].map((name) => ({ company_name: name, preference: kind })),
      );
      await api.put(`${planPath}/company-preferences`, { items });
      setPrefsSaveDone(true);
    } catch (err) {
      setPrefsError(handleApiError(err));
    } finally {
      setPrefsSaving(false);
    }
  }

  async function activatePlan() {
    setActivating(true);
    setActionError(null);
    try {
      await api.post(`${planPath}/activate`);
      setReloadKey((k) => k + 1);
    } catch (err) {
      setActionError(handleApiError(err));
    } finally {
      setActivating(false);
    }
  }

  async function deletePlan() {
    setDeleting(true);
    setActionError(null);
    try {
      await api.delete(planPath);
      router.push("/plans");
    } catch (err) {
      setActionError(handleApiError(err));
      setConfirmDelete(false);
      setDeleting(false);
    }
  }

  if (state.kind === "loading") {
    return (
      <main className="page">
        <h1>求职方案</h1>
        <p className="muted">加载中…</p>
      </main>
    );
  }

  if (state.kind === "error") {
    return (
      <main className="page">
        <h1>求职方案</h1>
        <p className="error-text">{state.reason}</p>
        <Link href="/plans" className="btn">
          返回方案列表
        </Link>
      </main>
    );
  }

  const plan = state.plan;
  const active = isPlanActive(plan);

  return (
    <main className="page">
      <p>
        <Link href="/plans" className="link muted">
          ← 返回方案列表
        </Link>
      </p>
      <div
        style={{
          display: "flex",
          justifyContent: "space-between",
          alignItems: "center",
          gap: 8,
          flexWrap: "wrap",
        }}
      >
        <h1>{plan.name || "求职方案"}</h1>
        <span className={active ? "badge badge-ok" : "badge badge-busy"}>
          {planStatusName(plan.status)}
        </span>
      </div>

      <h2>方案设置</h2>
      <div className="card">
        <PlanForm
          key={`${plan.id}-${plan.updated_at ?? ""}`}
          initial={plan}
          submitLabel="保存设置"
          submitting={saving}
          submitError={saveError}
          onSubmit={handleSave}
        />
        {saveDone ? (
          <p className="muted" style={{ marginTop: 8 }}>
            已保存。
          </p>
        ) : null}
      </div>

      <h2>公司偏好</h2>
      <p className="muted">
        屏蔽优先级最高：被屏蔽公司的岗位会被直接排除，不受关注 / 优先影响。
      </p>
      {!prefsLoaded ? (
        <p className="muted">加载中…</p>
      ) : (
        <>
          {prefsError ? <p className="error-text">{prefsError}</p> : null}
          <div style={{ display: "flex", gap: 12, flexWrap: "wrap" }}>
            {COMPANY_PREFERENCE_KINDS.map((kind) => (
              <div
                key={kind.value}
                className="card"
                style={{ flex: "1 1 200px", margin: 0 }}
              >
                <strong>{kind.label}</strong>
                <p className="muted" style={{ marginTop: 2 }}>
                  {kind.hint}
                </p>
                <ul style={{ listStyle: "none", margin: "8px 0" }}>
                  {prefs[kind.value].length === 0 ? (
                    <li className="muted">暂无</li>
                  ) : (
                    prefs[kind.value].map((name) => (
                      <li
                        key={name}
                        style={{
                          display: "flex",
                          justifyContent: "space-between",
                          alignItems: "center",
                          gap: 8,
                          padding: "4px 0",
                          fontSize: 14,
                        }}
                      >
                        <span style={{ wordBreak: "break-all" }}>{name}</span>
                        <button
                          type="button"
                          className="btn"
                          style={{ padding: "2px 8px", fontSize: 12 }}
                          onClick={() => removeCompany(kind.value, name)}
                        >
                          移除
                        </button>
                      </li>
                    ))
                  )}
                </ul>
                <div style={{ display: "flex", gap: 6 }}>
                  <input
                    className="input"
                    placeholder="公司名称"
                    maxLength={60}
                    value={drafts[kind.value]}
                    onChange={(e) =>
                      setDrafts((prev) => ({ ...prev, [kind.value]: e.target.value }))
                    }
                    onKeyDown={(e) => {
                      if (e.key === "Enter") {
                        e.preventDefault();
                        addCompany(kind.value);
                      }
                    }}
                  />
                  <button
                    type="button"
                    className="btn"
                    onClick={() => addCompany(kind.value)}
                  >
                    添加
                  </button>
                </div>
              </div>
            ))}
          </div>
          <div style={{ marginTop: 12 }}>
            <button
              type="button"
              className="btn btn-primary"
              onClick={savePreferences}
              disabled={prefsSaving}
            >
              {prefsSaving ? "保存中…" : "保存公司偏好"}
            </button>
            {prefsSaveDone ? (
              <span className="muted" style={{ marginLeft: 8 }}>
                已保存。
              </span>
            ) : null}
          </div>
        </>
      )}

      <h2>方案操作</h2>
      {actionError ? <p className="error-text">{actionError}</p> : null}
      <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
        <button
          type="button"
          className="btn btn-primary"
          onClick={activatePlan}
          disabled={activating || active}
        >
          {active ? "已激活" : activating ? "激活中…" : "激活方案"}
        </button>
        <button
          type="button"
          className="btn btn-danger"
          onClick={() => setConfirmDelete(true)}
          disabled={deleting}
        >
          删除方案
        </button>
      </div>
      <p className="muted" style={{ marginTop: 6 }}>
        激活后会触发一次基础推荐刷新；最多 3 个方案同时处于进行中。
      </p>

      <ConfirmDialog
        open={confirmDelete}
        title="删除这个求职方案？"
        danger
        loading={deleting}
        confirmText="确认删除"
        onConfirm={deletePlan}
        onCancel={() => setConfirmDelete(false)}
      >
        <p>
          删除后该方案的推荐将停止生成，此操作无法撤销；
          已生成的简历版本不会被自动删除。
        </p>
      </ConfirmDialog>
    </main>
  );
}
