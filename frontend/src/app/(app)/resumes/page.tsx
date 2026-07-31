"use client";

import {
  useCallback,
  useEffect,
  useRef,
  useState,
  type ChangeEvent,
  type DragEvent,
} from "react";
import Link from "next/link";
import { api } from "@/lib/api";
import { codeMessage, handleApiError } from "@/lib/api-error";
import ConfirmDialog from "@/components/ConfirmDialog";
import {
  type ParseStatus,
  type Resume,
  type ResumeUploadSession,
  RESUME_STATUS_NAMES,
  fmtTime,
  toItems,
} from "@/lib/types";

/**
 * 简历工作台（docs/04-api.md 第 4 节）。
 * 上传流程：POST /resumes/uploads → 按会话上传文件 → POST /resumes 确认
 * → 轮询 GET /resumes/{id}/parse 直到解析完成/失败。
 */

const MAX_FILE_BYTES = 10 * 1024 * 1024; // 10MB
const ALLOWED_EXTENSIONS = [".pdf", ".docx"];
const POLL_INTERVAL_MS = 2000;
const POLL_MAX_ATTEMPTS = 90; // 约 3 分钟

type UploadPhase =
  | { step: "idle" }
  | { step: "uploading"; fileName: string }
  | { step: "parsing"; fileName: string; resumeId: string }
  | { step: "done"; fileName: string; resumeId: string }
  | { step: "failed"; fileName: string; reason: string };

const PARSING_STATUSES = ["pending", "queued", "parsing", "processing", "uploaded"];
const DONE_STATUSES = ["parsed", "succeeded", "completed", "ready", "confirmed"];

function isParsing(status?: string) {
  return !status || PARSING_STATUSES.includes(status.toLowerCase());
}

function isDone(status?: string) {
  return !!status && DONE_STATUSES.includes(status.toLowerCase());
}

function statusBadgeClass(status?: string): string {
  if (isDone(status)) return "badge badge-ok";
  if (status?.toLowerCase() === "failed") return "badge badge-fail";
  return "badge badge-busy";
}

function statusName(status?: string): string {
  if (!status) return "处理中";
  return RESUME_STATUS_NAMES[status.toLowerCase()] ?? status;
}

/** 前端预校验：扩展名 + 大小。通过返回 null，否则返回中文原因。 */
function precheckFile(file: File): string | null {
  const name = file.name.toLowerCase();
  if (!ALLOWED_EXTENSIONS.some((ext) => name.endsWith(ext))) {
    return "仅支持 PDF 或 DOCX 文件，请检查文件格式";
  }
  if (file.size > MAX_FILE_BYTES) {
    return "文件超过 10MB 大小限制，请压缩后重试";
  }
  if (file.size === 0) {
    return "文件内容为空，请检查后重新选择";
  }
  return null;
}

export default function ResumesPage() {
  const [resumes, setResumes] = useState<Resume[] | null>(null);
  const [listError, setListError] = useState<string | null>(null);
  const [reloadKey, setReloadKey] = useState(0);

  const [upload, setUpload] = useState<UploadPhase>({ step: "idle" });
  const [dragOver, setDragOver] = useState(false);
  const fileInputRef = useRef<HTMLInputElement>(null);

  const [deleteTarget, setDeleteTarget] = useState<Resume | null>(null);
  const [deleting, setDeleting] = useState(false);

  // 卸载时终止轮询
  const unmountedRef = useRef(false);
  useEffect(() => {
    unmountedRef.current = false;
    return () => {
      unmountedRef.current = true;
    };
  }, []);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const data = await api.get<unknown>("/resumes");
        if (cancelled) return;
        setResumes(toItems<Resume>(data));
        setListError(null);
      } catch (err) {
        if (cancelled) return;
        setResumes([]);
        setListError(handleApiError(err));
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [reloadKey]);

  /** 轮询解析状态直到完成/失败/超时 */
  const pollParse = useCallback(async (resumeId: string, fileName: string) => {
    for (let attempt = 0; attempt < POLL_MAX_ATTEMPTS; attempt++) {
      if (unmountedRef.current) return;
      try {
        const parse = await api.get<ParseStatus>(`/resumes/${resumeId}/parse`);
        if (unmountedRef.current) return;
        const status = parse?.status?.toLowerCase();
        if (status === "failed") {
          setUpload({
            step: "failed",
            fileName,
            reason: codeMessage(parse?.error_code, parse?.error_message),
          });
          setReloadKey((k) => k + 1);
          return;
        }
        if (isDone(status)) {
          setUpload({ step: "done", fileName, resumeId });
          setReloadKey((k) => k + 1);
          return;
        }
      } catch {
        // 单次轮询失败不终止（后端可能短暂不可用），继续重试
      }
      await new Promise((resolve) => setTimeout(resolve, POLL_INTERVAL_MS));
    }
    if (!unmountedRef.current) {
      setUpload({
        step: "failed",
        fileName,
        reason: "解析时间较长，请稍后在列表中刷新查看结果",
      });
      setReloadKey((k) => k + 1);
    }
  }, []);

  const startUpload = useCallback(
    async (file: File) => {
      const precheckError = precheckFile(file);
      if (precheckError) {
        setUpload({ step: "failed", fileName: file.name, reason: precheckError });
        return;
      }

      setUpload({ step: "uploading", fileName: file.name });
      try {
        // 1. 获取上传会话/预签名参数
        const session = await api.post<ResumeUploadSession>("/resumes/uploads", {
          file_name: file.name,
          size_bytes: file.size,
          mime_type: file.type || "application/octet-stream",
        });
        const uploadUrl = session?.upload_url ?? session?.url;
        const uploadId = session?.upload_id ?? session?.id;
        if (!uploadUrl || !uploadId) {
          setUpload({
            step: "failed",
            fileName: file.name,
            reason: "上传会话无效，请稍后重试",
          });
          return;
        }

        // 2. 按会话参数上传文件本体（预签名地址，不带 Cookie）
        const putResponse = await fetch(uploadUrl, {
          method: session.method ?? "PUT",
          headers: session.headers ?? {},
          body: file,
        });
        if (!putResponse.ok) {
          setUpload({
            step: "failed",
            fileName: file.name,
            reason: `文件上传失败（HTTP ${putResponse.status}），请重试`,
          });
          return;
        }

        // 3. 确认上传完成并创建解析任务
        const resume = await api.post<Resume>("/resumes", {
          upload_id: uploadId,
          file_name: file.name,
        });
        if (!resume?.id) {
          setUpload({
            step: "failed",
            fileName: file.name,
            reason: "创建解析任务失败，请重试",
          });
          return;
        }

        setUpload({ step: "parsing", fileName: file.name, resumeId: resume.id });
        setReloadKey((k) => k + 1);
        await pollParse(resume.id, file.name);
      } catch (err) {
        if (unmountedRef.current) return;
        setUpload({
          step: "failed",
          fileName: file.name,
          reason: handleApiError(err),
        });
      }
    },
    [pollParse],
  );

  const uploadBusy = upload.step === "uploading" || upload.step === "parsing";

  function handleFiles(files: FileList | null) {
    if (uploadBusy) return;
    const file = files?.[0];
    if (file) void startUpload(file);
  }

  function onDrop(e: DragEvent<HTMLDivElement>) {
    e.preventDefault();
    setDragOver(false);
    handleFiles(e.dataTransfer?.files ?? null);
  }

  function onInputChange(e: ChangeEvent<HTMLInputElement>) {
    handleFiles(e.target.files);
    // 允许重复选择同一文件
    e.target.value = "";
  }

  async function confirmDelete() {
    if (!deleteTarget) return;
    setDeleting(true);
    try {
      await api.delete(`/resumes/${deleteTarget.id}`);
      setDeleteTarget(null);
      setReloadKey((k) => k + 1);
    } catch (err) {
      setListError(handleApiError(err));
      setDeleteTarget(null);
    } finally {
      setDeleting(false);
    }
  }

  return (
    <main className="page">
      <h1>简历工作台</h1>
      <p className="muted">上传原始简历，解析后确认候选事实，形成你的事实库。</p>

      <nav className="tabs" aria-label="简历工作台导航">
        <Link href="/resumes" className="active">
          简历列表
        </Link>
        <Link href="/profile/facts">事实库</Link>
      </nav>

      <h2>上传简历</h2>
      <div
        className={dragOver ? "dropzone dragover" : "dropzone"}
        onDragOver={(e) => {
          e.preventDefault();
          if (!uploadBusy) setDragOver(true);
        }}
        onDragLeave={() => setDragOver(false)}
        onDrop={onDrop}
      >
        <p style={{ marginBottom: 12 }}>
          将 PDF 或 DOCX 文件拖到此处，或
        </p>
        <button
          type="button"
          className="btn"
          disabled={uploadBusy}
          onClick={() => fileInputRef.current?.click()}
        >
          选择文件
        </button>
        <input
          ref={fileInputRef}
          type="file"
          accept=".pdf,.docx,application/pdf,application/vnd.openxmlformats-officedocument.wordprocessingml.document"
          style={{ display: "none" }}
          onChange={onInputChange}
        />
        <p className="muted" style={{ marginTop: 12 }}>
          仅支持文本型 PDF / DOCX，大小不超过 10MB。上传不等同于授权第三方模型处理。
        </p>
      </div>

      {upload.step === "uploading" && (
        <div className="notice">「{upload.fileName}」上传中，请勿关闭页面…</div>
      )}
      {upload.step === "parsing" && (
        <div className="notice">
          「{upload.fileName}」解析中，通常需要几十秒，完成后可确认候选事实…
        </div>
      )}
      {upload.step === "done" && (
        <div className="card">
          <p style={{ lineHeight: 1.7 }}>
            ✓ 「{upload.fileName}」解析完成。
            <Link href={`/resumes/${upload.resumeId}/facts`} className="link">
              前往确认候选事实
            </Link>
          </p>
        </div>
      )}
      {upload.step === "failed" && <p className="error-text">{upload.reason}</p>}

      <h2>已上传的简历</h2>
      {listError && <p className="error-text">{listError}</p>}

      {resumes === null ? (
        <p className="muted">加载中…</p>
      ) : resumes.length === 0 ? (
        <div className="card">
          <p className="muted">还没有上传过简历。上传后系统会解析出候选事实，由你逐条确认。</p>
        </div>
      ) : (
        resumes.map((r) => (
          <div className="card" key={r.id}>
            <div
              style={{
                display: "flex",
                justifyContent: "space-between",
                gap: 12,
                flexWrap: "wrap",
                alignItems: "center",
              }}
            >
              <div style={{ minWidth: 0 }}>
                <p style={{ fontWeight: "bold", wordBreak: "break-all" }}>
                  {r.file_name ?? "未命名文件"}
                </p>
                <p className="muted" style={{ marginTop: 4 }}>
                  上传时间：{fmtTime(r.created_at ?? r.uploaded_at)}
                </p>
                {r.status?.toLowerCase() === "failed" && (
                  <p className="error-text" style={{ marginBottom: 0 }}>
                    {codeMessage(r.parse_error_code, "解析失败，请更换文件后重试")}
                  </p>
                )}
              </div>
              <div style={{ display: "flex", gap: 8, alignItems: "center", flexWrap: "wrap" }}>
                <span className={statusBadgeClass(r.status)}>{statusName(r.status)}</span>
                {isDone(r.status) && (
                  <Link href={`/resumes/${r.id}/facts`} className="btn">
                    确认事实
                  </Link>
                )}
                {!isDone(r.status) &&
                  r.status?.toLowerCase() !== "failed" &&
                  isParsing(r.status) && <span className="muted">解析中…</span>}
                <button
                  type="button"
                  className="btn btn-danger"
                  onClick={() => setDeleteTarget(r)}
                >
                  删除
                </button>
              </div>
            </div>
          </div>
        ))
      )}

      <ConfirmDialog
        open={deleteTarget !== null}
        title="删除这份简历？"
        danger
        loading={deleting}
        confirmText="确认删除"
        onConfirm={confirmDelete}
        onCancel={() => setDeleteTarget(null)}
      >
        <p>
          将删除「{deleteTarget?.file_name ?? "未命名文件"}
          」及其派生数据（解析结果、候选事实、关联的简历版本等）。此操作不可恢复。
        </p>
      </ConfirmDialog>
    </main>
  );
}
