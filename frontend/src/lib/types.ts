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

/** GET /recommendations 列表项（字段按契约宽松兼容） */
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
  /** 岗位数据来源：connector / user_import / synthetic_seed（阶段 11 P1） */
  data_origin?: string;
}

/**
 * 是否为合成种子岗位（阶段 11 P1 硬要求）：
 * data_origin === 'synthetic_seed' 时列表卡片与详情页必须展示「合成示例」徽标。
 */
export function isSyntheticSeed(rec: RecommendationListItem): boolean {
  return rec.data_origin === "synthetic_seed";
}

/* ---------------- 推荐详情、反馈与能力状态（docs/04 第 6 节、docs/07） ---------------- */

/** 硬条件三态（docs/07 第 2 节：必须返回 passed/failed/unknown 和证据） */
export type HardConditionStatus = "passed" | "failed" | "unknown";

export function normalizeHardStatus(raw?: string): HardConditionStatus {
  const s = (raw ?? "").toLowerCase();
  if (s === "passed" || s === "pass" || s === "ok" || s === "true") return "passed";
  if (s === "failed" || s === "fail" || s === "false") return "failed";
  return "unknown";
}

/** 三态的图标 + 文案（颜色不是唯一表达，必须带文字） */
export const HARD_STATUS_META: Record<
  HardConditionStatus,
  { icon: string; label: string; badgeClass: string }
> = {
  passed: { icon: "✓", label: "通过", badgeClass: "badge badge-ok" },
  failed: { icon: "✕", label: "未通过", badgeClass: "badge badge-fail" },
  unknown: { icon: "？", label: "无法判断", badgeClass: "badge badge-busy" },
};

/** 单条硬条件结果 */
export interface HardConditionItem {
  code?: string;
  name?: string;
  label?: string;
  status?: string;
  evidence?: string;
  detail?: string;
  reason?: string;
}

/** GET /recommendations/{id} 的 hard_conditions */
export interface HardConditions {
  status?: string;
  items?: HardConditionItem[];
}

/** 硬条件项的展示名（未知 code 原样展示） */
const HARD_CONDITION_NAMES: Record<string, string> = {
  city: "城市",
  salary: "薪资",
  work_mode: "办公方式",
  employment_type: "全职",
  full_time: "全职",
  outsourcing: "外包/派遣",
  blocked_company: "屏蔽公司",
  education: "学历要求",
  experience: "经验要求",
};

export function hardConditionLabel(item: HardConditionItem): string {
  const key = (item.code ?? item.name ?? "").toLowerCase();
  return item.label ?? HARD_CONDITION_NAMES[key] ?? item.name ?? item.code ?? "硬条件";
}

/** 匹配证据：简历事实 ↔ 岗位原文（docs/07 第 5 节，宽松兼容） */
export interface MatchEvidence {
  claim?: string;
  fact_id?: string;
  profile_fact_ids?: string[];
  fact_text?: string;
  resume_fact?: string;
  job_span?: string;
  job_evidence?: { span?: string; snapshot_id?: string } | string | null;
  strength?: string;
  uncertainty?: string | null;
}

/** 证据中的简历侧文本（缺失返回空串，由页面显示「信息不足」） */
export function evidenceFactText(e: MatchEvidence): string {
  return e.fact_text ?? e.resume_fact ?? e.claim ?? "";
}

/** 证据中的岗位原文片段 */
export function evidenceJobText(e: MatchEvidence): string {
  if (typeof e.job_span === "string" && e.job_span) return e.job_span;
  if (typeof e.job_evidence === "string") return e.job_evidence;
  if (e.job_evidence && typeof e.job_evidence === "object") {
    return e.job_evidence.span ?? "";
  }
  return "";
}

/** 分项分数（docs/04 第 6 节 components） */
export interface MatchComponent {
  name?: string;
  code?: string;
  score?: number;
  weight?: number;
  evidence?: MatchEvidence[];
}

/** 分项维度 → 展示名 + 初始权重（docs/07 第 4 节，后端给了 weight 则以后端为准） */
const COMPONENT_META: Record<string, { label: string; weight: number }> = {
  skills: { label: "核心技能", weight: 35 },
  skill: { label: "核心技能", weight: 35 },
  experience: { label: "工作经验", weight: 20 },
  work_experience: { label: "工作经验", weight: 20 },
  projects: { label: "项目证据", weight: 20 },
  project: { label: "项目证据", weight: 20 },
  direction: { label: "岗位方向", weight: 10 },
  role_direction: { label: "岗位方向", weight: 10 },
  semantic: { label: "岗位方向", weight: 10 },
  industry: { label: "行业/业务背景", weight: 5 },
  preferences: { label: "用户偏好", weight: 10 },
  preference: { label: "用户偏好", weight: 10 },
  user_preferences: { label: "用户偏好", weight: 10 },
};

export function componentLabel(c: MatchComponent): string {
  const key = (c.name ?? c.code ?? "").toLowerCase();
  return COMPONENT_META[key]?.label ?? c.name ?? c.code ?? "其他";
}

/** 分项权重：后端字段优先，否则回退 docs/07 初始权重，未知维度返回 null */
export function componentWeight(c: MatchComponent): number | null {
  if (typeof c.weight === "number") return c.weight;
  const key = (c.name ?? c.code ?? "").toLowerCase();
  return COMPONENT_META[key]?.weight ?? null;
}

/** 风险信号（docs/04 第 6 节 risks；不确定性必须标注，不得当成确定结论） */
export interface RiskSignal {
  code?: string;
  name?: string;
  severity?: string;
  evidence?: string;
  uncertainty?: string | boolean | null;
  note?: string;
}

const RISK_CODE_NAMES: Record<string, string> = {
  POSSIBLE_OUTSOURCING: "疑似外包/派遣",
  OUTSOURCING: "外包/派遣",
  POSSIBLE_SCAM: "疑似虚假/收费岗位",
  FEE_REQUIRED: "疑似要求付费",
  POSSIBLE_STALE: "岗位可能已过期",
  STALE_JOB: "岗位可能已过期",
  DUPLICATE: "疑似重复岗位",
  VAGUE_JD: "职位描述含糊",
};

export function riskName(risk: RiskSignal): string {
  const code = (risk.code ?? "").toUpperCase();
  return risk.name ?? RISK_CODE_NAMES[code] ?? risk.code ?? "风险信号";
}

export const RISK_SEVERITY_NAMES: Record<string, string> = {
  low: "低",
  medium: "中",
  high: "高",
};

/** 风险是否带不确定性标注 */
export function riskUncertain(risk: RiskSignal): boolean {
  const code = (risk.code ?? "").toUpperCase();
  return Boolean(risk.uncertainty) || code.startsWith("POSSIBLE_");
}

/** 「不感兴趣」原因（docs/05 第 11 节，9 类） */
export type FeedbackReason =
  | "location"
  | "salary"
  | "company"
  | "tech_direction"
  | "job_content"
  | "requirements"
  | "outsourcing"
  | "risk_concern"
  | "duplicate";

export const FEEDBACK_REASONS: ReadonlyArray<{ value: FeedbackReason; label: string }> = [
  { value: "location", label: "地点不合适" },
  { value: "salary", label: "薪资不合适" },
  { value: "company", label: "不想去这家公司" },
  { value: "tech_direction", label: "技术方向不匹配" },
  { value: "job_content", label: "岗位内容不合适" },
  { value: "requirements", label: "经验/学历要求不符" },
  { value: "outsourcing", label: "外包/派遣" },
  { value: "risk_concern", label: "风险顾虑" },
  { value: "duplicate", label: "已看过/重复" },
];

export function feedbackReasonLabel(value: string): string {
  return FEEDBACK_REASONS.find((r) => r.value === value)?.label ?? value;
}

/** 已提交的反馈（响应字段宽松兼容：feedback 可能是字符串或对象） */
export interface RecommendationFeedback {
  type?: string;
  status?: string;
  feedback?: string;
  interest?: string;
  reasons?: string[];
  reason_codes?: string[];
  note?: string | null;
  created_at?: string;
}

/** 归一化后的反馈状态 */
export interface FeedbackState {
  kind: "interested" | "not_interested";
  reasons: string[];
  note?: string;
}

/** 从推荐对象里宽松解析当前反馈状态（无反馈返回 null） */
export function parseFeedback(rec: Recommendation): FeedbackState | null {
  const raw = rec.feedback ?? rec.my_feedback ?? null;
  let kind = "";
  let reasons: string[] = [];
  let note: string | undefined;
  if (typeof raw === "string") {
    kind = raw;
  } else if (raw && typeof raw === "object") {
    const f = raw as RecommendationFeedback;
    kind = f.type ?? f.status ?? f.feedback ?? f.interest ?? "";
    reasons = f.reasons ?? f.reason_codes ?? [];
    note = f.note ?? undefined;
  }
  const key = kind.toLowerCase();
  if (key === "interested" || key === "like" || key === "positive") {
    return { kind: "interested", reasons, note };
  }
  if (
    key === "not_interested" ||
    key === "uninterested" ||
    key === "dislike" ||
    key === "negative"
  ) {
    return { kind: "not_interested", reasons, note };
  }
  return null;
}

/**
 * GET /recommendations 列表项 / GET /recommendations/{id} 详情。
 * 后端并行开发中，字段全部宽松声明。
 */
export interface Recommendation extends RecommendationListItem {
  salary_min?: number;
  salary_max?: number;
  salary_range?: string;
  salary_months?: number;
  published_at?: string;
  first_seen_at?: string;
  discovered_at?: string;
  created_at?: string;
  primary_source?: string;
  /** 前几个匹配点（字符串或含 label/text/claim 的对象） */
  match_points?: unknown[];
  highlights?: unknown[];
  top_matches?: unknown[];
  /** 最关键缺口 */
  key_gap?: string;
  top_gap?: string;
  gaps?: unknown[];
  risks?: RiskSignal[];
  hard_conditions?: HardConditions;
  components?: MatchComponent[];
  analysis_level?: string;
  source_links?: unknown[];
  sources?: unknown[];
  job_description?: string;
  description?: string;
  requirements_text?: string;
  feedback?: RecommendationFeedback | string | null;
  my_feedback?: RecommendationFeedback | string | null;
}

/** 等级 → 展示名（docs/07：80-100 high / 65-79 potential / <65 low） */
export const GRADE_NAMES: Record<string, string> = {
  high: "高匹配",
  potential: "可尝试",
  low: "低匹配",
};

export function gradeName(grade?: string): string {
  if (!grade) return "";
  return GRADE_NAMES[grade.toLowerCase()] ?? grade;
}

export function gradeBadgeClass(grade?: string): string {
  const g = (grade ?? "").toLowerCase();
  if (g === "high") return "badge badge-ok";
  if (g === "potential") return "badge badge-busy";
  return "badge";
}

/** 推荐卡片的岗位名/公司/城市 */
export function recTitle(rec: Recommendation): string {
  return rec.job_title ?? rec.title ?? "（无标题岗位）";
}

export function recCompany(rec: Recommendation): string {
  return rec.company ?? rec.company_name ?? "公司未知";
}

export function recCity(rec: Recommendation): string {
  if (rec.city) return rec.city;
  if (rec.cities && rec.cities.length > 0) return rec.cities.join(" / ");
  return "城市未知";
}

/** 薪资展示：缺失时按契约显示「未披露」，有薪数时附带 */
export function recSalaryText(rec: Recommendation): string {
  let base = "";
  if (rec.salary_text) base = rec.salary_text;
  else if (rec.salary_range) base = rec.salary_range;
  else {
    const { salary_min: min, salary_max: max } = rec;
    const fmt = (n: number) => `${n.toLocaleString("zh-CN")} 元/月`;
    if (min != null && max != null) base = `${fmt(min)} ~ ${fmt(max)}`;
    else if (min != null || max != null) base = fmt((min ?? max) as number);
  }
  if (!base) return "薪资未披露";
  if (rec.salary_months != null) return `${base} · ${rec.salary_months} 薪`;
  return base;
}

/** 主来源展示 */
export function recSourceText(rec: Recommendation): string {
  return rec.primary_source ?? rec.source_name ?? rec.source ?? "";
}

/** 把字符串/对象混合数组归一化为字符串列表 */
function toTextList(raw: unknown[] | undefined): string[] {
  if (!Array.isArray(raw)) return [];
  const out: string[] = [];
  for (const entry of raw) {
    if (typeof entry === "string" && entry.trim()) {
      out.push(entry.trim());
    } else if (entry && typeof entry === "object") {
      const e = entry as Record<string, unknown>;
      const text =
        (typeof e.label === "string" && e.label) ||
        (typeof e.text === "string" && e.text) ||
        (typeof e.claim === "string" && e.claim) ||
        (typeof e.name === "string" && e.name) ||
        (typeof e.description === "string" && e.description) ||
        "";
      if (text.trim()) out.push(text.trim());
    }
  }
  return out;
}

/** 匹配点（用于卡片「前 3 个匹配点」） */
export function recMatchPoints(rec: Recommendation): string[] {
  for (const raw of [rec.match_points, rec.highlights, rec.top_matches]) {
    const list = toTextList(raw);
    if (list.length > 0) return list;
  }
  // 回退：从分项证据的 claim 提取
  const fromComponents: string[] = [];
  for (const c of rec.components ?? []) {
    for (const e of c.evidence ?? []) {
      const text = e.claim ?? "";
      if (text && !fromComponents.includes(text)) fromComponents.push(text);
    }
  }
  return fromComponents;
}

/** 关键缺口（缺失返回空串） */
export function recKeyGap(rec: Recommendation): string {
  if (rec.key_gap) return rec.key_gap;
  if (rec.top_gap) return rec.top_gap;
  const gaps = toTextList(rec.gaps);
  return gaps[0] ?? "";
}

/** 全部缺口列表 */
export function recGaps(rec: Recommendation): string[] {
  const list = toTextList(rec.gaps);
  if (list.length > 0) return list;
  const single = recKeyGap(rec);
  return single ? [single] : [];
}

/** 全部来源链接（详情页第 1 区块），兼容 source_links / sources */
export function recSourceLinks(rec: Recommendation): LinkOutEntry[] {
  const out: LinkOutEntry[] = [];
  for (const raw of [rec.source_links, rec.sources]) {
    if (!Array.isArray(raw)) continue;
    for (const entry of raw) {
      if (typeof entry === "string" && entry) {
        out.push({ url: entry, label: hostnameOf(entry) });
      } else if (entry && typeof entry === "object") {
        const e = entry as Record<string, unknown>;
        const url =
          (typeof e.url === "string" && e.url) ||
          (typeof e.source_url === "string" && e.source_url) ||
          "";
        if (!url) continue;
        const label =
          (typeof e.label === "string" && e.label) ||
          (typeof e.source === "string" && e.source) ||
          (typeof e.source_name === "string" && e.source_name) ||
          (typeof e.name === "string" && e.name) ||
          hostnameOf(url);
        out.push({ url, label });
      }
    }
    if (out.length > 0) break;
  }
  return out;
}

function hostnameOf(url: string): string {
  try {
    return new URL(url).hostname;
  } catch {
    return url;
  }
}

/* ---------------- 能力状态（GET /health/capabilities，docs/02 第 161-163 行） ---------------- */

export interface CapabilityEntry {
  name: string;
  label: string;
  status: string;
}

const CAPABILITY_NAMES: Record<string, string> = {
  job_sources: "岗位来源",
  sources: "岗位来源",
  deepseek: "DeepSeek",
  qwen: "通义千问",
  embedding: "Embedding",
  embeddings: "Embedding",
  export: "导出",
  exports: "导出",
};

/** 能力状态 → 展示文案 + 徽标 class */
export function capabilityStatusMeta(status: string): { label: string; badgeClass: string } {
  const s = status.toLowerCase();
  if (["ok", "normal", "healthy", "up", "available", "active"].includes(s)) {
    return { label: "正常", badgeClass: "badge badge-ok" };
  }
  if (["degraded", "limited", "partial", "slow"].includes(s)) {
    return { label: "降级", badgeClass: "badge badge-busy" };
  }
  if (["down", "failed", "error", "unavailable", "disabled"].includes(s)) {
    return { label: "不可用", badgeClass: "badge badge-fail" };
  }
  return { label: status, badgeClass: "badge" };
}

const CAPABILITY_TIME_KEYS = [
  "updated_at",
  "data_updated_at",
  "jobs_updated_at",
  "last_updated_at",
  "last_sync_at",
  "last_job_sync_at",
];

/**
 * 宽松解析 /health/capabilities：
 * 兼容 { capabilities: {...} }、{ items: [{name,status}] }、
 * 以及顶层直接是 name → status（字符串或 { status }）的映射。
 */
export function parseCapabilities(data: unknown): CapabilityEntry[] {
  if (!data || typeof data !== "object" || Array.isArray(data)) return [];
  let obj = data as Record<string, unknown>;
  if (obj.capabilities && typeof obj.capabilities === "object" && !Array.isArray(obj.capabilities)) {
    obj = obj.capabilities as Record<string, unknown>;
  }
  const entries: CapabilityEntry[] = [];
  const push = (name: string, status: string) => {
    if (!name || !status) return;
    entries.push({
      name,
      label: CAPABILITY_NAMES[name.toLowerCase()] ?? name,
      status,
    });
  };
  if (Array.isArray(obj.items)) {
    for (const item of obj.items) {
      if (!item || typeof item !== "object") continue;
      const e = item as Record<string, unknown>;
      const name = (typeof e.name === "string" && e.name) || (typeof e.capability === "string" && e.capability) || "";
      const status = typeof e.status === "string" ? e.status : "";
      push(name, status);
    }
    return entries;
  }
  for (const [key, value] of Object.entries(obj)) {
    if (CAPABILITY_TIME_KEYS.includes(key) || key === "status" || key === "meta") continue;
    if (typeof value === "string") {
      push(key, value);
    } else if (value && typeof value === "object" && !Array.isArray(value)) {
      const status = (value as Record<string, unknown>).status;
      if (typeof status === "string") push(key, status);
    }
  }
  return entries;
}

/** 宽松提取数据更新时间（顶层或 meta 内） */
export function extractDataUpdatedAt(data: unknown): string | null {
  if (!data || typeof data !== "object" || Array.isArray(data)) return null;
  const obj = data as Record<string, unknown>;
  const scopes: Record<string, unknown>[] = [obj];
  if (obj.meta && typeof obj.meta === "object" && !Array.isArray(obj.meta)) {
    scopes.push(obj.meta as Record<string, unknown>);
  }
  for (const scope of scopes) {
    for (const key of CAPABILITY_TIME_KEYS) {
      const v = scope[key];
      if (typeof v === "string" && v) return v;
    }
  }
  return null;
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
