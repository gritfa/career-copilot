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
