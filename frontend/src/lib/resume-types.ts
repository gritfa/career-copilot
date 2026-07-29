/**
 * 阶段 7：定制简历版本 / 导出的前端类型与文案（对应 backend app/tailoring/schemas.py）。
 */

export interface ResumeItem {
  text: string;
  fact_ids: string[];
}

export interface ResumeSection {
  kind: "summary" | "skills" | "work_experience" | "projects" | "education";
  title: string;
  items: ResumeItem[];
}

export interface ResumeContent {
  schema_version: string;
  target_job_title: string;
  target_company: string;
  sections: ResumeSection[];
}

export interface ResumeChange {
  change_type: "selected" | "reordered" | "rephrased" | "omitted";
  section: string;
  before: string;
  after: string;
  reason: string;
  fact_ids: string[];
  job_span: string;
}

export type ResumeVersionStatus =
  | "generating"
  | "draft"
  | "confirmed"
  | "failed"
  | "deleted";

export interface ResumeVersion {
  id: string;
  kind: "plan_base" | "job_tailored";
  recommendation_id: string | null;
  canonical_job_id: string | null;
  parent_version_id: string | null;
  template_id: string;
  status: ResumeVersionStatus;
  created_by: "user" | "agent_draft";
  provider: string | null;
  model_id: string | null;
  verified: boolean;
  error_code: string | null;
  content: ResumeContent | null;
  changes: ResumeChange[];
  edited_by_user_at: string | null;
  confirmed_at: string | null;
  created_at: string;
}

export type ResumeExportStatus = "queued" | "running" | "succeeded" | "failed";

export interface ResumeExport {
  id: string;
  resume_version_id: string;
  format: "docx" | "pdf";
  status: ResumeExportStatus;
  error_code: string | null;
  size_bytes: number | null;
  file_sha256: string | null;
  download_url: string | null;
  expires_at: string | null;
  created_at: string;
  completed_at: string | null;
}

export const RESUME_VERSION_STATUS_LABELS: Record<ResumeVersionStatus, string> = {
  generating: "生成中",
  draft: "草稿（待确认）",
  confirmed: "已确认",
  failed: "生成失败",
  deleted: "已删除",
};

export const TAILOR_ERROR_LABELS: Record<string, string> = {
  EVIDENCE_VALIDATION_FAILED:
    "生成内容未通过事实校验（拒绝未确认事实与编造数字），未保留任何内容",
  MODEL_UNAVAILABLE: "定制模型暂时不可用，可稍后重试",
  SCHEMA_INVALID: "模型输出未通过结构校验（已按规则拒绝）",
  CONSENT_REQUIRED: "缺少模型服务商授权，请先在隐私设置中完成授权",
  NO_CONFIRMED_FACTS: "你还没有已确认的简历事实，请先上传简历并确认事实",
  RECOMMENDATION_NOT_FOUND: "关联的推荐已不存在",
  JOB_POSTING_NOT_FOUND: "关联的岗位原文已不存在",
};

export const EXPORT_ERROR_LABELS: Record<string, string> = {
  VERSION_NOT_CONFIRMED: "请先确认简历内容，再导出文件",
  FACT_REFERENCE_INVALID:
    "内容引用的事实已变更或被撤销，导出被阻止；请重新生成或编辑后再试",
  RENDER_FAILED: "文件渲染失败，可稍后重试",
  VERSION_NOT_FOUND: "简历版本已不存在",
};

export function isGeneratingVersion(version: ResumeVersion | null): boolean {
  return version != null && version.status === "generating";
}

export function isActiveExport(exp: ResumeExport | null): boolean {
  return exp != null && (exp.status === "queued" || exp.status === "running");
}

export const SECTION_KIND_LABELS: Record<string, string> = {
  summary: "个人摘要",
  skills: "专业技能",
  work_experience: "工作经历",
  projects: "项目经历",
  education: "教育背景",
};

export const CHANGE_TYPE_LABELS: Record<string, string> = {
  selected: "筛选",
  reordered: "排序调整",
  rephrased: "措辞调整",
  omitted: "省略",
};

export function shortFactRef(id: string): string {
  return `#${id.slice(0, 8)}`;
}
