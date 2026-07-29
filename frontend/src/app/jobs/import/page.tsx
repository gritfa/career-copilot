"use client";

import { useState } from "react";
import Link from "next/link";
import { api } from "@/lib/api";
import { handleApiError } from "@/lib/api-error";
import {
  type JobImportResult,
  importSalaryText,
  unwrapJobImportResult,
} from "@/lib/types";

/**
 * 岗位手动导入（docs/04-api.md 第 6 节 POST /jobs/import、docs/06 用户导入）。
 * 两种方式：粘贴岗位链接 / 粘贴职位描述正文。
 * 成功后展示标准化结果预览：标题 / 公司 / 城市 / 薪资 / 来源。
 */

type Tab = "url" | "text";

export default function JobImportPage() {
  const [tab, setTab] = useState<Tab>("url");
  const [url, setUrl] = useState("");
  const [rawText, setRawText] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [result, setResult] = useState<JobImportResult | null>(null);

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (submitting) return;
    setError(null);
    setResult(null);

    let payload: Record<string, unknown>;
    if (tab === "url") {
      const value = url.trim();
      if (!value) {
        setError("请粘贴岗位链接");
        return;
      }
      if (!/^https?:\/\//i.test(value)) {
        setError("链接需要以 http:// 或 https:// 开头");
        return;
      }
      payload = { type: "url", url: value };
    } else {
      const value = rawText.trim();
      if (value.length < 30) {
        setError("职位描述内容过短，请粘贴完整的岗位描述正文（至少 30 字）");
        return;
      }
      payload = { type: "raw_text", raw_text: value };
    }

    setSubmitting(true);
    try {
      const data = await api.post<JobImportResult>("/jobs/import", payload);
      setResult(unwrapJobImportResult(data ?? {}));
    } catch (err) {
      setError(handleApiError(err));
    } finally {
      setSubmitting(false);
    }
  }

  function switchTab(next: Tab) {
    setTab(next);
    setError(null);
  }

  return (
    <main className="page">
      <p>
        <Link href="/recommendations" className="link muted">
          ← 返回每日推荐
        </Link>
      </p>
      <h1>导入岗位</h1>
      <p className="muted">
        受平台政策限制，部分来源仅支持跳转到原平台或手动导入，无法自动采集。
        你可以把看到的岗位链接或职位描述粘贴进来，系统会标准化后纳入匹配。
      </p>

      <div className="tabs">
        <a
          href="#url"
          className={tab === "url" ? "active" : ""}
          onClick={(e) => {
            e.preventDefault();
            switchTab("url");
          }}
        >
          粘贴岗位链接
        </a>
        <a
          href="#text"
          className={tab === "text" ? "active" : ""}
          onClick={(e) => {
            e.preventDefault();
            switchTab("text");
          }}
        >
          粘贴职位描述正文
        </a>
      </div>

      <form onSubmit={handleSubmit}>
        {tab === "url" ? (
          <label className="field">
            <span>岗位链接（招聘平台或企业官网的岗位详情页）</span>
            <input
              className="input"
              type="url"
              value={url}
              onChange={(e) => setUrl(e.target.value)}
              placeholder="https://…"
            />
          </label>
        ) : (
          <label className="field">
            <span>职位描述正文（岗位名称、公司、城市、薪资、职责要求等）</span>
            <textarea
              className="input"
              rows={10}
              value={rawText}
              onChange={(e) => setRawText(e.target.value)}
              placeholder="把完整的职位描述粘贴到这里…"
            />
          </label>
        )}

        {error ? <p className="error-text">{error}</p> : null}

        <button type="submit" className="btn btn-primary" disabled={submitting}>
          {submitting ? "导入中…" : "提交导入"}
        </button>
      </form>

      {result ? (
        <>
          <h2>标准化结果预览</h2>
          <div className="card">
            <p>
              <strong>{result.title ?? result.job_title ?? "（未识别到标题）"}</strong>
            </p>
            <p style={{ marginTop: 6, fontSize: 14 }}>
              公司：{result.company ?? result.company_name ?? "未识别"}
            </p>
            <p style={{ marginTop: 4, fontSize: 14 }}>
              城市：
              {result.city ??
                (result.cities && result.cities.length > 0
                  ? result.cities.join(" / ")
                  : "未识别")}
            </p>
            <p style={{ marginTop: 4, fontSize: 14 }}>
              薪资：{importSalaryText(result)}
            </p>
            <p style={{ marginTop: 4, fontSize: 14 }}>
              来源：{result.source ?? result.source_name ?? "用户导入"}
              {result.source_url ?? result.url ? (
                <>
                  {" · "}
                  <a
                    className="link"
                    href={result.source_url ?? result.url}
                    target="_blank"
                    rel="noopener noreferrer"
                  >
                    原始链接
                  </a>
                </>
              ) : null}
            </p>
          </div>
          <p className="muted">
            导入的岗位会参与你各方案的匹配打分，符合条件时出现在每日推荐里。
          </p>
        </>
      ) : null}
    </main>
  );
}
