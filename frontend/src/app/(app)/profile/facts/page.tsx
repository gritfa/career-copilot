"use client";

import { useEffect, useMemo, useState } from "react";
import Link from "next/link";
import { api } from "@/lib/api";
import { handleApiError } from "@/lib/api-error";
import ConfirmDialog from "@/components/ConfirmDialog";
import {
  type ProfileFact,
  FACT_GROUP_ORDER,
  factGroupName,
  fmtTime,
  normalizeFactGroup,
  toItems,
} from "@/lib/types";

/**
 * 事实库（docs/04-api.md 第 4 节）。
 * GET /profile/facts 分组展示；行内编辑 PATCH 生成新版本；
 * DELETE 废止（二次确认 + 引用影响警告）。
 */

export default function ProfileFactsPage() {
  const [facts, setFacts] = useState<ProfileFact[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [reloadKey, setReloadKey] = useState(0);

  // 行内编辑：factId → 草稿值
  const [editingId, setEditingId] = useState<string | null>(null);
  const [draft, setDraft] = useState("");
  const [saving, setSaving] = useState(false);
  const [editError, setEditError] = useState<string | null>(null);

  const [revokeTarget, setRevokeTarget] = useState<ProfileFact | null>(null);
  const [revoking, setRevoking] = useState(false);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const data = await api.get<unknown>("/profile/facts");
        if (cancelled) return;
        setFacts(toItems<ProfileFact>(data));
        setError(null);
      } catch (err) {
        if (cancelled) return;
        setFacts([]);
        setError(handleApiError(err));
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [reloadKey]);

  const grouped = useMemo(() => {
    if (!facts) return [];
    const map = new Map<string, ProfileFact[]>();
    for (const f of facts) {
      const key = normalizeFactGroup(f.group ?? f.category);
      const list = map.get(key) ?? [];
      list.push(f);
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
  }, [facts]);

  function startEdit(f: ProfileFact) {
    setEditingId(f.id);
    setDraft(f.value ?? "");
    setEditError(null);
  }

  function cancelEdit() {
    setEditingId(null);
    setDraft("");
    setEditError(null);
  }

  async function saveEdit(f: ProfileFact) {
    const value = draft.trim();
    if (!value) return;
    setSaving(true);
    setEditError(null);
    try {
      // PATCH 修改并生成新版本
      await api.patch(`/profile/facts/${f.id}`, { value });
      setEditingId(null);
      setDraft("");
      setReloadKey((k) => k + 1);
    } catch (err) {
      setEditError(handleApiError(err));
    } finally {
      setSaving(false);
    }
  }

  async function confirmRevoke() {
    if (!revokeTarget) return;
    setRevoking(true);
    try {
      await api.delete(`/profile/facts/${revokeTarget.id}`);
      setRevokeTarget(null);
      setReloadKey((k) => k + 1);
    } catch (err) {
      setError(handleApiError(err));
      setRevokeTarget(null);
    } finally {
      setRevoking(false);
    }
  }

  return (
    <main className="page">
      <h1>事实库</h1>
      <p className="muted">
        已确认的事实是推荐匹配和简历生成的唯一依据。修改会生成新版本，废止会影响引用它的数据。
      </p>

      <nav className="tabs" aria-label="简历工作台导航">
        <Link href="/resumes">简历列表</Link>
        <Link href="/profile/facts" className="active">
          事实库
        </Link>
      </nav>

      {error && <p className="error-text">{error}</p>}

      {facts === null ? (
        <p className="muted">加载中…</p>
      ) : facts.length === 0 ? (
        <div className="card">
          <p className="muted">
            事实库还是空的。先在
            <Link href="/resumes" className="link">
              简历工作台
            </Link>
            上传简历并确认候选事实。
          </p>
        </div>
      ) : (
        grouped.map((group) => (
          <section key={group.key}>
            <h2>{group.name}</h2>
            {group.items.map((f) => (
              <div className="card" key={f.id}>
                {f.label && (
                  <p style={{ fontWeight: "bold", marginBottom: 4 }}>{f.label}</p>
                )}

                {editingId === f.id ? (
                  <div>
                    <label className="field">
                      <span>修改内容（保存后生成新版本）</span>
                      <input
                        className="input"
                        value={draft}
                        onChange={(e) => setDraft(e.target.value)}
                        disabled={saving}
                      />
                    </label>
                    {editError && <p className="error-text">{editError}</p>}
                    <div style={{ display: "flex", gap: 8 }}>
                      <button
                        type="button"
                        className="btn btn-primary"
                        disabled={saving || !draft.trim()}
                        onClick={() => saveEdit(f)}
                      >
                        {saving ? "保存中…" : "保存"}
                      </button>
                      <button
                        type="button"
                        className="btn"
                        onClick={cancelEdit}
                        disabled={saving}
                      >
                        取消
                      </button>
                    </div>
                  </div>
                ) : (
                  <>
                    <p style={{ lineHeight: 1.7, wordBreak: "break-word" }}>
                      {f.value ?? "—"}
                    </p>
                    <p className="muted" style={{ marginTop: 4 }}>
                      {f.version !== undefined && `版本 v${f.version} · `}
                      更新时间：{fmtTime(f.updated_at ?? f.created_at)}
                    </p>
                    <div style={{ display: "flex", gap: 8, marginTop: 8 }}>
                      <button type="button" className="btn" onClick={() => startEdit(f)}>
                        编辑
                      </button>
                      <button
                        type="button"
                        className="btn btn-danger"
                        onClick={() => setRevokeTarget(f)}
                      >
                        废止
                      </button>
                    </div>
                  </>
                )}
              </div>
            ))}
          </section>
        ))
      )}

      <ConfirmDialog
        open={revokeTarget !== null}
        title="废止这条事实？"
        danger
        loading={revoking}
        confirmText="确认废止"
        onConfirm={confirmRevoke}
        onCancel={() => setRevokeTarget(null)}
      >
        <p>
          将废止「
          {revokeTarget?.label ?? revokeTarget?.value ?? "该事实"}
          」。如果它正被求职方案、推荐结果或简历版本引用，相关内容将不再以它为依据，可能需要重新生成。此操作会保留历史版本记录，但该事实不再生效。
        </p>
      </ConfirmDialog>
    </main>
  );
}
