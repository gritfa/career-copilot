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

/* ---------------- 求职方案与公司偏好（docs/04-api.md 第 5 节） ---------------- */

/** GET /search-plans 列表项 / GET /search-plans/{id}（字段按契约宽松兼容） */
export interface SearchPlan {
  id: string;
  name?: string;
  /** 求职方向（docs/03 数据模型的 role_family），兼容 direction 字段 */
  role_family?: string;
  direction?: string;
  /** active | paused | draft | archived 等，后端可能扩展 */
  status?: string;
  priority?: number;
  city_codes?: string[];
  cities?: string[];
  work_modes?: string[];
  minimum_monthly_salary?: number;
  target_monthly_salary?: number;
  /** 第一版只接受 CNY */
  currency?: string;
  /** 薪数偏好（如 13 表示 13 薪） */
  salary_months_preference?: number | null;
  /** 最低匹配分，默认 65 */
  minimum_match_score?: number;
  /** 外包/派遣/驻场，默认 false（默认排除） */
  allow_outsourcing?: boolean;
  base_resume_version_id?: string | null;
  created_at?: string;
  updated_at?: string;
}

/** 求职方向（6 选 1）：稳定 code → 展示名 */
export const PLAN_DIRECTIONS: ReadonlyArray<{ value: string; label: string }> = [
  { value: "ai_application", label: "AI 应用开发" },
  { value: "python_backend", label: "Python 后端" },
  { value: "java_backend", label: "Java 后端" },
  { value: "frontend", label: "前端" },
  { value: "data_engineering", label: "数据分析与数据工程" },
  { value: "qa_testing", label: "软件测试与测试开发" },
];

/** 方向 code → 展示名（未知 code 原样展示，避免后端扩展时崩溃） */
export function directionLabel(value?: string): string {
  if (!value) return "未设置";
  return PLAN_DIRECTIONS.find((d) => d.value === value)?.label ?? value;
}

/** 第一版支持的城市：稳定 code → 展示名 */
export const PLAN_CITIES: ReadonlyArray<{ value: string; label: string }> = [
  { value: "beijing", label: "北京" },
  { value: "shanghai", label: "上海" },
  { value: "shenzhen", label: "深圳" },
  { value: "hangzhou", label: "杭州" },
  { value: "guangzhou", label: "广州" },
  { value: "chengdu", label: "成都" },
];

export function cityLabel(value: string): string {
  return PLAN_CITIES.find((c) => c.value === value)?.label ?? value;
}

/** 办公方式 */
export const WORK_MODES: ReadonlyArray<{ value: string; label: string }> = [
  { value: "onsite", label: "现场办公" },
  { value: "hybrid", label: "混合办公" },
  { value: "remote", label: "远程办公" },
];

export function workModeLabel(value: string): string {
  return WORK_MODES.find((m) => m.value === value)?.label ?? value;
}

/** 方案状态 → 中文展示 */
export const PLAN_STATUS_NAMES: Record<string, string> = {
  active: "进行中",
  inactive: "已停用",
  paused: "已暂停",
  draft: "草稿",
  archived: "已归档",
};

export function planStatusName(status?: string): string {
  if (!status) return "草稿";
  return PLAN_STATUS_NAMES[status.toLowerCase()] ?? status;
}

export function isPlanActive(plan: SearchPlan): boolean {
  return (plan.status ?? "").toLowerCase() === "active";
}

/** 方案薪资区间的展示文案（缺失时显示未设置） */
export function planSalaryText(plan: SearchPlan): string {
  const min = plan.minimum_monthly_salary;
  const target = plan.target_monthly_salary;
  if (min == null && target == null) return "薪资未设置";
  const fmt = (n: number) => `${n.toLocaleString("zh-CN")} 元/月`;
  if (min != null && target != null) return `${fmt(min)} ~ ${fmt(target)}`;
  if (min != null) return `不低于 ${fmt(min)}`;
  return `目标 ${fmt(target as number)}`;
}

/** 公司偏好类别（docs/03：follow/priority/block，屏蔽优先级最高） */
export type CompanyPreferenceKind = "follow" | "priority" | "block";

export const COMPANY_PREFERENCE_KINDS: ReadonlyArray<{
  value: CompanyPreferenceKind;
  label: string;
  hint: string;
}> = [
  { value: "follow", label: "关注", hint: "关注公司的岗位会被标记提醒" },
  { value: "priority", label: "优先", hint: "优先公司的岗位排序靠前" },
  { value: "block", label: "屏蔽", hint: "屏蔽优先级最高，直接排除" },
];

/** GET /search-plans/{id}/company-preferences 单条（字段宽松兼容） */
export interface CompanyPreference {
  id?: string;
  company_id?: string;
  company_name?: string;
  name?: string;
  preference?: string;
}

/** 三类公司名单的本地编辑形态 */
export type CompanyPreferenceLists = Record<CompanyPreferenceKind, string[]>;

export function emptyCompanyPreferenceLists(): CompanyPreferenceLists {
  return { follow: [], priority: [], block: [] };
}

/**
 * 宽松解析公司偏好响应：
 * 兼容 [{company_name, preference}]、{ items: [...] }、
 * 以及 { follow: [...], priority: [...], block: [...] }（元素为字符串或对象）。
 */
export function parseCompanyPreferences(data: unknown): CompanyPreferenceLists {
  const lists = emptyCompanyPreferenceLists();
  const kinds: CompanyPreferenceKind[] = ["follow", "priority", "block"];

  const nameOf = (entry: unknown): string => {
    if (typeof entry === "string") return entry.trim();
    if (entry && typeof entry === "object") {
      const e = entry as CompanyPreference;
      return (e.company_name ?? e.name ?? e.company_id ?? "").trim();
    }
    return "";
  };

  const push = (kind: CompanyPreferenceKind, entry: unknown) => {
    const name = nameOf(entry);
    if (name && !lists[kind].includes(name)) lists[kind].push(name);
  };

  const fromItems = (items: unknown[]) => {
    for (const item of items) {
      if (!item || typeof item !== "object") continue;
      const pref = ((item as CompanyPreference).preference ?? "").toLowerCase();
      const kind = kinds.find((k) => k === pref);
      if (kind) push(kind, item);
    }
  };

  if (Array.isArray(data)) {
    fromItems(data);
    return lists;
  }
  if (data && typeof data === "object") {
    const obj = data as Record<string, unknown>;
    if (Array.isArray(obj.items)) {
      fromItems(obj.items);
      return lists;
    }
    for (const kind of kinds) {
      const arr = obj[kind];
      if (Array.isArray(arr)) {
        for (const entry of arr) push(kind, entry);
      }
    }
  }
  return lists;
}

/* ---------------- 岗位导入与推荐（docs/04-api.md 第 6 节） ---------------- */

/** POST /jobs/import 返回的标准化结果（字段宽松兼容，可能嵌套在 job 字段里） */
export interface JobImportResult {
  id?: string;
  job_id?: string;
  title?: string;
  job_title?: string;
  company?: string;
  company_name?: string;
  city?: string;
  cities?: string[];
  salary_min?: number;
  salary_max?: number;
  salary_text?: string;
  salary_range?: string;
  source?: string;
  source_name?: string;
  source_url?: string;
  url?: string;
  status?: string;
  job?: JobImportResult;
}

/** 展开可能的 { job: {...} } 嵌套 */
export function unwrapJobImportResult(data: JobImportResult): JobImportResult {
  return data.job && typeof data.job === "object" ? { ...data.job, ...pickTopLevel(data) } : data;
}

function pickTopLevel(data: JobImportResult): Partial<JobImportResult> {
  const out: Partial<JobImportResult> = {};
  if (data.status) out.status = data.status;
  return out;
}

/** 导入结果的薪资展示 */
export function importSalaryText(job: JobImportResult): string {
  if (job.salary_text) return job.salary_text;
  if (job.salary_range) return job.salary_range;
  const { salary_min: min, salary_max: max } = job;
  if (min == null && max == null) return "未披露";
  const fmt = (n: number) => `${n.toLocaleString("zh-CN")} 元/月`;
  if (min != null && max != null) return `${fmt(min)} ~ ${fmt(max)}`;
  return fmt((min ?? max) as number);
}

/** 推荐空态下的原平台搜索跳转链接（GET /recommendations 返回，字段宽松兼容） */
export interface LinkOutEntry {
  url: string;
  label: string;
}

/**
 * 宽松提取 link_out 链接：兼容 link_out / link_outs / link_out_urls /
 * link_out_links / search_links 字段，元素为字符串或 { url, source|name|label }。
 */
export function extractLinkOuts(data: unknown): LinkOutEntry[] {
  if (!data || typeof data !== "object" || Array.isArray(data)) return [];
  const obj = data as Record<string, unknown>;
  const candidates = [
    obj.link_out,
    obj.link_outs,
    obj.link_out_urls,
    obj.link_out_links,
    obj.search_links,
  ];
  const out: LinkOutEntry[] = [];
  for (const candidate of candidates) {
    if (!Array.isArray(candidate)) continue;
    for (const entry of candidate) {
      if (typeof entry === "string" && entry) {
        out.push({ url: entry, label: linkOutLabelFromUrl(entry) });
      } else if (entry && typeof entry === "object") {
        const e = entry as Record<string, unknown>;
        const url = typeof e.url === "string" ? e.url : "";
        if (!url) continue;
        const label =
          (typeof e.label === "string" && e.label) ||
          (typeof e.source === "string" && e.source) ||
          (typeof e.source_name === "string" && e.source_name) ||
          (typeof e.name === "string" && e.name) ||
          linkOutLabelFromUrl(url);
        out.push({ url, label });
      }
    }
    if (out.length > 0) break;
  }
  return out;
}

function linkOutLabelFromUrl(url: string): string {
  try {
    return new URL(url).hostname;
  } catch {
    return url;
  }
}

/** 推荐空态原因（docs/05 第 14 节：来源暂无数据 / 硬条件过严 / 能力降级） */
export type EmptyReason =
  | "no_source_data"
  | "hard_conditions_too_strict"
  | "capability_degraded"
  | "unknown";

/** 宽松提取空态原因字段并归一化 */
export function extractEmptyReason(data: unknown): EmptyReason {
  if (!data || typeof data !== "object" || Array.isArray(data)) return "unknown";
  const obj = data as Record<string, unknown>;
  const meta =
    obj.meta && typeof obj.meta === "object" && !Array.isArray(obj.meta)
      ? (obj.meta as Record<string, unknown>)
      : undefined;
  const raw: string =
    (typeof obj.empty_reason === "string" && obj.empty_reason) ||
    (typeof obj.reason === "string" && obj.reason) ||
    (typeof meta?.empty_reason === "string" && meta.empty_reason) ||
    "";
  const key = raw.toLowerCase();
  if (!key) return "unknown";
  if (key.includes("source") && (key.includes("empty") || key.includes("no_data"))) {
    return "no_source_data";
  }
  if (key === "no_source_data" || key === "no_data") return "no_source_data";
  if (key.includes("hard") || key.includes("strict")) return "hard_conditions_too_strict";
  if (key.includes("degrad") || key.includes("capability")) return "capability_degraded";
  return "unknown";
}

/** GET /recommendations 列表项（当前阶段只做空态与最小卡片，字段宽松兼容） */
export interface RecommendationListItem {
  id: string;
  score?: number;
  grade?: string;
  job_title?: string;
  title?: string;
  company?: string;
  company_name?: string;
  city?: string;
  cities?: string[];
  work_mode?: string;
  salary_text?: string;
  source?: string;
  source_name?: string;
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
