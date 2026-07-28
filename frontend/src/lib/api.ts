/**
 * 极简 typed fetch 封装。
 *
 * - 基础地址读取 NEXT_PUBLIC_API_BASE_URL，默认 http://localhost:8000/api/v1
 * - 后端错误统一为 docs/04-api.md 1.1 节格式：
 *   { "error": { "code", "message", "request_id", "details"? } }
 */

export const API_BASE_URL =
  process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8000/api/v1";

/** 后端标准错误体 */
export interface ApiErrorBody {
  error: {
    code: string;
    message: string;
    request_id: string;
    details?: Record<string, unknown>;
  };
}

/** 请求失败时抛出的错误，携带稳定错误码与 request_id 便于排查 */
export class ApiError extends Error {
  readonly status: number;
  readonly code: string;
  readonly requestId: string;
  readonly details?: Record<string, unknown>;

  constructor(status: number, body: ApiErrorBody["error"]) {
    super(body.message);
    this.name = "ApiError";
    this.status = status;
    this.code = body.code;
    this.requestId = body.request_id;
    this.details = body.details;
  }
}

export interface ApiRequestOptions extends Omit<RequestInit, "body"> {
  /** JSON 请求体，自动序列化并设置 Content-Type */
  json?: unknown;
}

function isApiErrorBody(value: unknown): value is ApiErrorBody {
  if (typeof value !== "object" || value === null) return false;
  const err = (value as { error?: unknown }).error;
  if (typeof err !== "object" || err === null) return false;
  const e = err as Record<string, unknown>;
  return typeof e.code === "string" && typeof e.message === "string";
}

/**
 * 发起请求并解析 JSON。
 *
 * @param path 以 / 开头的接口路径，例如 "/auth/session"
 * @throws ApiError 后端返回标准错误体时
 * @throws Error 网络失败或响应格式不符合预期时
 */
export async function apiFetch<T>(
  path: string,
  options: ApiRequestOptions = {},
): Promise<T> {
  const { json, headers, ...rest } = options;

  const init: RequestInit = {
    ...rest,
    headers: {
      Accept: "application/json",
      ...(json !== undefined ? { "Content-Type": "application/json" } : {}),
      ...headers,
    },
    ...(json !== undefined ? { body: JSON.stringify(json) } : {}),
  };

  const response = await fetch(`${API_BASE_URL}${path}`, init);

  if (!response.ok) {
    let body: unknown = null;
    try {
      body = await response.json();
    } catch {
      // 非 JSON 错误响应，走统一兜底
    }
    if (isApiErrorBody(body)) {
      throw new ApiError(response.status, body.error);
    }
    throw new ApiError(response.status, {
      code: "UNKNOWN_ERROR",
      message: `请求失败（HTTP ${response.status}）`,
      request_id: response.headers.get("x-request-id") ?? "",
    });
  }

  if (response.status === 204) {
    return undefined as T;
  }
  return (await response.json()) as T;
}

/** 常用方法的便捷封装 */
export const api = {
  get: <T>(path: string, options?: ApiRequestOptions) =>
    apiFetch<T>(path, { ...options, method: "GET" }),
  post: <T>(path: string, json?: unknown, options?: ApiRequestOptions) =>
    apiFetch<T>(path, { ...options, method: "POST", json }),
  put: <T>(path: string, json?: unknown, options?: ApiRequestOptions) =>
    apiFetch<T>(path, { ...options, method: "PUT", json }),
  patch: <T>(path: string, json?: unknown, options?: ApiRequestOptions) =>
    apiFetch<T>(path, { ...options, method: "PATCH", json }),
  delete: <T>(path: string, options?: ApiRequestOptions) =>
    apiFetch<T>(path, { ...options, method: "DELETE" }),
};
