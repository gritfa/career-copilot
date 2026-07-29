/**
 * ApiError 的统一前端处理：
 * - 401 → 跳转 /login（登录/验证页自身可关闭该行为）
 * - 429 → 「操作过于频繁」
 * - 稳定错误码 → 中文文案（docs/04-api.md 1.1 节）
 */

import { ApiError } from "./api";

/** 稳定错误码 → 中文文案。未命中时回退到后端 message。 */
const CODE_MESSAGES: Record<string, string> = {
  CONSENT_REQUIRED:
    "需要先完成对应模型服务商的授权，可在「设置 → 隐私」中授权后重试",
  RATE_LIMITED: "操作过于频繁，请稍后再试",
  TOO_MANY_REQUESTS: "操作过于频繁，请稍后再试",
  INVITE_INVALID: "邀请码无效或已过期，请确认后重新输入",
  INVALID_INVITE: "邀请码无效或已过期，请确认后重新输入",
  TOKEN_INVALID: "链接已失效，请重新获取",
  TOKEN_EXPIRED: "链接已失效，请重新获取",
  MAGIC_LINK_INVALID: "链接已失效，请重新获取",
  VALIDATION_ERROR: "提交内容不符合要求，请检查后重试",
  UNAUTHORIZED: "登录状态已失效，请重新登录",
  AUTH_REQUIRED: "登录状态已失效，请重新登录",
  FORBIDDEN: "没有权限执行此操作",
  NOT_FOUND: "请求的资源不存在或已被删除",
  // ---- 简历上传/解析（docs/04-api.md 第 4 节） ----
  NO_TEXT_LAYER: "这是扫描件/图片型 PDF，未检测到文本层，请上传文本型文件",
  FILE_TOO_LARGE: "文件超过大小限制（10MB），请压缩后重试",
  UNSUPPORTED_FILE_TYPE: "不支持的文件类型，仅支持 PDF 或 DOCX",
  INVALID_FILE_TYPE: "不支持的文件类型，仅支持 PDF 或 DOCX",
  FILE_SIGNATURE_MISMATCH: "文件内容与扩展名不符，请重新导出后上传",
  MALWARE_DETECTED: "文件未通过安全扫描，请检查文件来源后重试",
  FILE_CORRUPTED: "文件已损坏或无法读取，请重新导出后上传",
  PASSWORD_PROTECTED: "文件已加密，请移除密码保护后重新上传",
  ENCRYPTED_FILE: "文件已加密，请移除密码保护后重新上传",
  EMPTY_FILE: "文件内容为空，请检查后重新上传",
  EMPTY_CONTENT: "未从文件中提取到有效内容，请检查后重新上传",
  PARSE_FAILED: "解析失败，请稍后重试或更换文件",
  PARSE_TIMEOUT: "解析超时，请稍后重试",
  FACT_REFERENCED: "该事实正被其他数据引用，无法直接废止",
};

/** 把任意错误转成可展示的中文文案（不触发跳转） */
export function apiErrorMessage(err: unknown): string {
  if (err instanceof ApiError) {
    if (err.status === 429) return CODE_MESSAGES.RATE_LIMITED;
    if (err.status === 401) return CODE_MESSAGES.UNAUTHORIZED;
    const mapped = CODE_MESSAGES[err.code];
    if (mapped) return mapped;
    if (err.message) return err.message;
    return `请求失败（${err.code}）`;
  }
  if (err instanceof Error) {
    // fetch 网络层失败（后端未启动、断网等）
    return "网络异常或服务暂不可用，请稍后重试";
  }
  return "发生未知错误，请稍后重试";
}

/**
 * 解析失败等场景下，错误码来自响应体字段（而非 ApiError）时的文案映射。
 * 未命中稳定错误码时回退到后端 message，再兜底为通用文案。
 */
export function codeMessage(code?: string | null, fallback?: string | null): string {
  if (code && CODE_MESSAGES[code]) return CODE_MESSAGES[code];
  if (fallback) return fallback;
  if (code) return `处理失败（${code}）`;
  return "处理失败，请稍后重试";
}

export interface HandleApiErrorOptions {
  /** 401 时是否跳转登录页，默认 true；登录/验证页自身应传 false */
  redirectOn401?: boolean;
}

/**
 * 统一错误处理入口：401 跳 /login，其余返回中文文案供页面展示。
 */
export function handleApiError(
  err: unknown,
  options: HandleApiErrorOptions = {},
): string {
  const { redirectOn401 = true } = options;
  if (
    redirectOn401 &&
    err instanceof ApiError &&
    err.status === 401 &&
    typeof window !== "undefined"
  ) {
    window.location.assign("/login");
  }
  return apiErrorMessage(err);
}
