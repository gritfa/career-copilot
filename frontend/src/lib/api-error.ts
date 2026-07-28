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
