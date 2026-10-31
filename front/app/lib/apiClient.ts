/**
 * 全站统一的 API 客户端。
 *
 * 背景：前端同时存在 `fetch`、`apiFetch`、`apiJson` 和一个全局 fetch 拦截器，
 * 每个调用点各自拼 `${API_BASE}`、各自处理认证与错误，于是同一类故障在不同面板里
 * 表现不一：有的把 "Failed to fetch" 直接显示给用户，有的没有超时，有的拿不到错误码。
 *
 * 现在业务代码只描述"要请求哪个接口"：
 *   apiRequest<T>('/api/v1/planning/execution', { query: { trip_id } })
 * 认证、超时、错误码、重试、请求追踪全部在这里完成；组件不再接触服务地址。
 */

import { resolveApiBase } from './gatewayBase.ts';

const configuredApiBase = process.env.NEXT_PUBLIC_API_BASE;
export const API_BASE = resolveApiBase(
  configuredApiBase,
  typeof window !== 'undefined' ? window.location : undefined,
  process.env.NEXT_PUBLIC_GATEWAY_PORT,
);

export const AUTH_STORAGE_KEY = 'omni_user';

/**
 * Access tokens live in memory only. They are short-lived (15 min) and are
 * renewed through the HttpOnly refresh cookie; persisting them in localStorage
 * would hand an XSS one long-lived credential that survives page reloads.
 *
 * `getAuthToken` still migrates a token left behind by older builds: it moves
 * the value into memory and strips it from localStorage on first read.
 */
let memoryAccessToken = '';

export function setAccessToken(token: string): void {
  memoryAccessToken = typeof token === 'string' ? token.trim() : '';
}

export function clearAccessToken(): void {
  memoryAccessToken = '';
}

/** 每个请求都带上的追踪头；网关/日志侧用它把一次用户操作串起来。 */
export const REQUEST_ID_HEADER = 'X-Request-Id';

export type ApiErrorCode =
  | 'REQUEST_FAILED'
  | 'REQUEST_TIMEOUT'
  | 'NETWORK_UNAVAILABLE'
  | 'INVALID_RESPONSE'
  | 'ABORTED'
  | 'ABSOLUTE_URL_FORBIDDEN'
  | 'HTTP_ERROR';

export class ApiRequestError extends Error {
  status: number;
  code: string;
  requestId: string;

  constructor(message: string, status = 0, code: string = 'REQUEST_FAILED', requestId = '') {
    super(message);
    this.name = 'ApiRequestError';
    this.status = status;
    this.code = code;
    this.requestId = requestId;
  }
}

export function getAuthToken(): string {
  if (memoryAccessToken) return memoryAccessToken;
  if (typeof window === 'undefined') return '';
  try {
    const raw = window.localStorage.getItem(AUTH_STORAGE_KEY);
    const user = raw ? JSON.parse(raw) : null;
    if (typeof user?.token === 'string' && user.token) {
      // Legacy build persisted the access token. Move it into memory and scrub
      // the copy on disk so it cannot be read by a later XSS.
      memoryAccessToken = user.token;
      delete user.token;
      window.localStorage.setItem(AUTH_STORAGE_KEY, JSON.stringify(user));
      return memoryAccessToken;
    }
  } catch {
    // ignore corrupt storage
  }
  return '';
}

/**
 * Exchanges the HttpOnly refresh cookie for a new access token. Returns false
 * when there is no valid refresh session.
 */
export async function refreshAccessToken(): Promise<boolean> {
  if (typeof window === 'undefined') return false;
  try {
    const response = await fetch(`${API_BASE}/api/auth/refresh`, {
      method: 'POST',
      credentials: 'include',
      cache: 'no-store',
      headers: { [REQUEST_ID_HEADER]: newRequestId() },
    });
    if (!response.ok) return false;
    const payload = (await response.json()) as { token?: string };
    if (typeof payload?.token === 'string' && payload.token) {
      setAccessToken(payload.token);
      return true;
    }
  } catch {
    // network failure: caller decides whether to surface it
  }
  return false;
}

/**
 * Requests a single-use, 60-second WebSocket ticket. The long-lived access
 * token never appears in the socket URL: only this ticket does, and it is burned
 * by the first handshake.
 */
export async function fetchWsTicket(): Promise<string> {
  const payload = await apiRequest<{ ticket?: string }>('/api/auth/ws-ticket', {
    method: 'POST',
    timeoutMs: 5000,
  });
  return typeof payload?.ticket === 'string' ? payload.ticket : '';
}

/** 把后端返回的相对资源路径（如头像）转成可访问地址。 */
export function assetUrl(path: string): string {
  if (!path) return '';
  if (/^(https?:|data:|blob:)/.test(path)) return path;
  return `${API_BASE}${path.startsWith('/') ? path : `/${path}`}`;
}

function newRequestId(): string {
  try {
    if (typeof crypto !== 'undefined' && typeof crypto.randomUUID === 'function') return crypto.randomUUID();
  } catch {
    // 老浏览器没有 randomUUID，退化成时间戳 + 随机数
  }
  return `req-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 10)}`;
}

function friendlyHttpError(status: number, serverMessage?: string, code?: string): string {
  if (status === 401) return '登录状态已失效，请返回大厅重新登录';
  if (status === 403 && code === 'ORIGIN_FORBIDDEN') return '当前访问地址未获网关授权，请检查前端来源配置';
  if (status === 403) return '当前账号没有执行此操作的权限';
  if (status === 404) return '请求的数据或服务不存在';
  if (status === 429) return '操作过于频繁，请稍后再试';
  if (status >= 500) return '数据服务暂时不可用，请稍后重试';
  return serverMessage || `请求失败 (HTTP ${status})`;
}

export type QueryValue = string | number | boolean | null | undefined;

export interface ApiRequestOptions {
  method?: string;
  /** 普通对象会被 JSON 序列化；FormData / Blob / string 原样发送。 */
  body?: unknown;
  query?: Record<string, QueryValue>;
  headers?: Record<string, string>;
  /** 0 表示不设超时（流式响应、上传进度等）。默认 12000ms。 */
  timeoutMs?: number;
  /** 额外重试次数；默认 GET/HEAD 为 2，其余为 0（写操作不自动重放）。 */
  retries?: number;
  signal?: AbortSignal;
  /** false 时不附带 Authorization（仅登录/注册这类匿名接口需要）。 */
  auth?: boolean;
  /** gateway = 拼网关基址（默认）；self = 同源 Next 路由。 */
  origin?: 'gateway' | 'self';
  requestId?: string;
}

function buildUrl(path: string, options: ApiRequestOptions, requestId: string): string {
  if (/^[a-z][a-z0-9+.-]*:\/\//i.test(path)) {
    throw new ApiRequestError(
      '业务代码不得直接使用服务地址，请传以 / 开头的接口路径',
      0,
      'ABSOLUTE_URL_FORBIDDEN',
      requestId,
    );
  }
  const search = new URLSearchParams();
  Object.entries(options.query || {}).forEach(([key, value]) => {
    if (value === null || value === undefined) return;
    search.append(key, String(value));
  });
  const queryString = search.toString();
  const withQuery = queryString ? `${path}${path.includes('?') ? '&' : '?'}${queryString}` : path;
  return options.origin === 'self' ? withQuery : `${API_BASE}${withQuery}`;
}

function buildBody(options: ApiRequestOptions, headers: Headers): BodyInit | undefined {
  const { body } = options;
  if (body === undefined || body === null) return undefined;
  if (typeof body === 'string' || body instanceof FormData || body instanceof Blob || body instanceof URLSearchParams) {
    return body as BodyInit;
  }
  if (body instanceof ArrayBuffer || ArrayBuffer.isView(body)) return body as BodyInit;
  if (!headers.has('Content-Type')) headers.set('Content-Type', 'application/json');
  return JSON.stringify(body);
}

function retryAfterMs(response: Response): number | null {
  const raw = response.headers?.get?.('Retry-After');
  if (!raw) return null;
  const seconds = Number(raw);
  return Number.isFinite(seconds) && seconds >= 0 ? Math.min(seconds * 1000, 5000) : null;
}

function isRetryableStatus(status: number): boolean {
  return status === 408 || status === 425 || status === 429 || status >= 500;
}

function sleep(ms: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

function defaultRetries(method: string): number {
  const upper = method.toUpperCase();
  return upper === 'GET' || upper === 'HEAD' ? 2 : 0;
}

/**
 * 带认证与追踪的原始请求：返回 Response，供流式读取 / FormData 上传使用。
 * 默认不设超时（流式响应可能持续很久），需要超时请显式传 timeoutMs。
 */
export async function apiFetch(path: string, options: ApiRequestOptions = {}): Promise<Response> {
  const method = (options.method || 'GET').toUpperCase();
  const requestId = options.requestId || newRequestId();
  const url = buildUrl(path, options, requestId);
  const retries = options.retries ?? defaultRetries(method);
  const timeoutMs = options.timeoutMs ?? 0;

  const isRefreshPath = /\/api\/auth\/refresh(?:\?|$)/.test(path);
  let authRetried = false;
  let lastError: unknown = null;
  for (let attempt = 0; attempt <= retries; attempt += 1) {
    const headers = new Headers(options.headers || {});
    headers.set(REQUEST_ID_HEADER, requestId);
    if (options.auth !== false) {
      let token = getAuthToken();
      // After a reload the in-memory access token is gone; the HttpOnly refresh
      // cookie is the only credential left, so silently renew before asking the
      // server for data instead of showing a spurious "session expired".
      if (!token && !isRefreshPath && typeof window !== 'undefined') {
        await refreshAccessToken();
        token = getAuthToken();
      }
      if (token && !headers.has('Authorization')) headers.set('Authorization', `Bearer ${token}`);
    }
    const controller = new AbortController();
    const upstream = options.signal;
    const abortFromUpstream = () => controller.abort();
    upstream?.addEventListener('abort', abortFromUpstream, { once: true });
    const timeoutId = timeoutMs > 0 ? setTimeout(() => controller.abort(), timeoutMs) : null;

    try {
      const response = await fetch(url, {
        method,
        headers,
        body: buildBody(options, headers),
        signal: controller.signal,
        cache: 'no-store',
        // The refresh cookie is HttpOnly and lives on the gateway origin; it is
        // only sent when the request explicitly asks for credentials.
        credentials: 'include',
      });
      // A 401 with a valid refresh session is not a real failure: renew once and
      // replay the same request. Re-auth must not consume the network retry
      // budget reserved for GET/HEAD.
      if (response.status === 401 && options.auth !== false && !isRefreshPath && !authRetried) {
        authRetried = true;
        if (await refreshAccessToken()) {
          attempt -= 1;
          continue;
        }
      }
      if (attempt < retries && isRetryableStatus(response.status) && method !== 'POST') {
        const wait = retryAfterMs(response) ?? Math.min(300 * 2 ** attempt, 2000);
        await sleep(wait);
        continue;
      }
      return response;
    } catch (error) {
      lastError = error;
      // 用户主动取消不算失败，也不再重试
      if (upstream?.aborted) {
        throw new ApiRequestError('请求已取消', 0, 'ABORTED', requestId);
      }
      if (attempt < retries) {
        await sleep(Math.min(300 * 2 ** attempt, 2000));
        continue;
      }
      if (timeoutId) clearTimeout(timeoutId);
      upstream?.removeEventListener('abort', abortFromUpstream);
      if (controller.signal.aborted) {
        throw new ApiRequestError('连接数据服务超时，请确认服务已启动后重试', 0, 'REQUEST_TIMEOUT', requestId);
      }
      throw new ApiRequestError('暂时无法连接数据服务，请确认网关已启动后重试', 0, 'NETWORK_UNAVAILABLE', requestId);
    } finally {
      if (timeoutId) clearTimeout(timeoutId);
      upstream?.removeEventListener('abort', abortFromUpstream);
    }
  }
  throw new ApiRequestError(
    lastError instanceof Error ? lastError.message : '请求失败',
    0,
    'REQUEST_FAILED',
    requestId,
  );
}

async function parseResponse<T>(response: Response, requestId: string): Promise<T> {
  const raw = await response.text();
  let payload: unknown = {};
  if (raw.trim()) {
    try {
      payload = JSON.parse(raw);
    } catch {
      if (!response.ok) {
        const plain = raw.trim();
        const serverMessage = plain.length <= 200 && !/<[a-z][\s\S]*>/i.test(plain) ? plain : undefined;
        throw new ApiRequestError(
          friendlyHttpError(response.status, serverMessage),
          response.status,
          'INVALID_RESPONSE',
          requestId,
        );
      }
      throw new ApiRequestError(
        `数据服务返回了非 JSON 内容 (HTTP ${response.status})，请检查网关或代理配置`,
        response.status,
        'INVALID_RESPONSE',
        requestId,
      );
    }
  }
  if (!response.ok) {
    const envelope = (payload as { error?: unknown } | null)?.error;
    const serverMessage = typeof envelope === 'string'
      ? envelope
      : (envelope as { message?: string } | undefined)?.message
        || (payload as { message?: string } | null)?.message;
    const serverCode = (envelope as { code?: string } | undefined)?.code
      || (payload as { code?: string } | null)?.code;
    throw new ApiRequestError(
      friendlyHttpError(response.status, serverMessage, serverCode),
      response.status,
      serverCode || 'HTTP_ERROR',
      requestId,
    );
  }
  return payload as T;
}

/** 统一的 JSON 请求：认证、超时、错误码、重试、追踪都在这里。 */
export async function apiRequest<T = unknown>(path: string, options: ApiRequestOptions = {}): Promise<T> {
  const requestId = options.requestId || newRequestId();
  const response = await apiFetch(path, {
    ...options,
    requestId,
    timeoutMs: options.timeoutMs ?? 12000,
    // 非幂等请求已在 apiFetch 内禁止自动重放，这里不再额外放开
    retries: options.retries ?? defaultRetries(options.method || 'GET'),
  });
  return parseResponse<T>(response, requestId);
}

/**
 * 兼容既有调用点的 JSON 请求签名（path, RequestInit, timeoutMs）。
 * 新代码请直接用 apiRequest；此函数只为迁移期减少噪声。
 */
export async function apiJson<T = unknown>(
  path: string,
  init: RequestInit = {},
  timeoutMs = 12000,
): Promise<T> {
  const headers: Record<string, string> = {};
  new Headers(init.headers || {}).forEach((value, key) => {
    headers[key] = value;
  });
  return apiRequest<T>(path, {
    method: init.method,
    body: init.body,
    headers,
    signal: init.signal ?? undefined,
    timeoutMs,
  });
}
