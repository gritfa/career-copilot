/**
 * docs/04-api.md 契约相关的前端类型。
 * 后端并行开发中，字段按契约（snake_case、ISO 8601 UTC）宽松声明，
 * 待 OpenAPI 稳定后再收紧/生成。
 */

/** GET /auth/session */
export interface SessionInfo {
  user?: {
    id?: string;
    email?: string;
    role?: string;
    onboarding_completed?: boolean;
  };
  onboarding_completed?: boolean;
  age_confirmed?: boolean;
  terms_accepted?: boolean;
  account_deletion?: AccountDeletionStatus | null;
}

/** POST /privacy/account-deletion 及会话内的注销状态 */
export interface AccountDeletionStatus {
  status?: string;
  requested_at?: string;
  recoverable_until?: string;
}

/** POST /auth/magic-links/verify */
export interface VerifyResponse {
  onboarding_completed?: boolean;
  user?: { onboarding_completed?: boolean };
}

/** GET /consents 中的单条授权 */
export interface Consent {
  id: string;
  provider: string;
  scope: string;
  status?: string;
  notice_version?: string;
  granted_at?: string;
  revoked_at?: string | null;
}

/** GET /auth/sessions 中的单个会话 */
export interface AuthSession {
  id: string;
  created_at?: string;
  last_seen_at?: string;
  ip?: string;
  user_agent?: string;
  current?: boolean;
  is_current?: boolean;
}

/**
 * 列表统一使用游标分页（{ items, next_cursor }），
 * 兼容后端直接返回数组的过渡形态。
 */
export function toItems<T>(data: unknown): T[] {
  if (Array.isArray(data)) return data as T[];
  if (
    data &&
    typeof data === "object" &&
    Array.isArray((data as { items?: unknown }).items)
  ) {
    return (data as { items: T[] }).items;
  }
  return [];
}

/** 供应商标识 → 展示名 */
export const PROVIDER_NAMES: Record<string, string> = {
  deepseek: "DeepSeek（深度求索）",
  qwen: "通义千问（阿里云百炼）",
};

/** 授权 scope → 展示名（完整简历与脱敏范围是不同 scope） */
export const SCOPE_NAMES: Record<string, string> = {
  full_resume: "完整简历内容",
  deidentified: "脱敏内容",
  structured_facts: "结构化事实",
};

/** ISO 时间 → 本地化展示，缺失时显示占位 */
export function fmtTime(iso?: string | null): string {
  if (!iso) return "—";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  return d.toLocaleString("zh-CN", { hour12: false });
}

/* ---------------- 简历与事实库（docs/04-api.md 第 4 节） ---------------- */

/** GET /resumes 列表项 / GET /resumes/{id}（不含正文） */
export interface Resume {
  id: string;
  file_name?: string;
  /** uploaded | parsing | parsed | failed 等，后端可能扩展 */
  status?: string;
  size_bytes?: number;
  mime_type?: string;
  created_at?: string;
  uploaded_at?: string;
  parse_error_code?: string | null;
}

/** POST /resumes/uploads 返回的上传会话/预签名参数（字段宽松兼容） */
export interface ResumeUploadSession {
  id?: string;
  upload_id?: string;
  upload_url?: string;
  url?: string;
  method?: string;
  headers?: Record<string, string>;
}

/** 候选事实的原文证据片段 */
export interface SourceSpan {
  text?: string;
  start?: number;
  end?: number;
  page?: number;
}

/** GET /resumes/{id}/parse 返回的单条候选事实 */
export interface FactCandidate {
  id: string;
  /** profile | skill | work_experience | project | education */
  group?: string;
  category?: string;
  label?: string;
  value?: string;
  source_span?: SourceSpan | null;
  evidence_text?: string;
  confidence?: number;
}

/** GET /resumes/{id}/parse：解析状态和候选事实 */
export interface ParseStatus {
  status?: string;
  error_code?: string | null;
  error_message?: string | null;
  candidates?: FactCandidate[];
  items?: FactCandidate[];
}

/** GET /profile/facts 中的已确认事实 */
export interface ProfileFact {
  id: string;
  group?: string;
  category?: string;
  label?: string;
  value?: string;
  version?: number;
  status?: string;
  source_resume_id?: string | null;
  created_at?: string;
  updated_at?: string;
}

/** 事实分组 key → 展示名（按 docs/05 第 8 节顺序） */
export const FACT_GROUP_NAMES: Record<string, string> = {
  profile: "个人概况",
  skill: "技能",
  skills: "技能",
  work_experience: "工作经历",
  work: "工作经历",
  project: "项目",
  projects: "项目",
  education: "教育",
};

/** 分组展示顺序（未知分组排在最后的「其他」） */
export const FACT_GROUP_ORDER = [
  "profile",
  "skill",
  "work_experience",
  "project",
  "education",
] as const;

/** 归一化分组 key（skills → skill 等），未知返回 "other" */
export function normalizeFactGroup(raw?: string): string {
  if (!raw) return "other";
  const key = raw.toLowerCase();
  if (key === "skills") return "skill";
  if (key === "work" || key === "work_experiences") return "work_experience";
  if (key === "projects") return "project";
  return FACT_GROUP_NAMES[key] ? key : "other";
}

/** 分组 key → 展示名（含未知分组兜底） */
export function factGroupName(key: string): string {
  return FACT_GROUP_NAMES[key] ?? "其他";
}

/** 简历状态 → 中文展示 */
export const RESUME_STATUS_NAMES: Record<string, string> = {
  uploaded: "已上传",
  pending: "等待解析",
  queued: "等待解析",
  parsing: "解析中",
  processing: "解析中",
  parsed: "解析完成",
  succeeded: "解析完成",
  completed: "解析完成",
  ready: "解析完成",
  confirmed: "事实已确认",
  failed: "解析失败",
};
