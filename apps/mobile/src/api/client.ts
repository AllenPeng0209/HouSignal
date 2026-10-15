import type { ApiOptions } from '../lib/contracts';
export class ApiError extends Error { constructor(message: string, public status: number) { super(message); this.name = 'ApiError'; } }
export function normalizeBaseUrl(value: string, development: boolean): string {
  let url: URL;
  try { url = new URL(value.trim()); } catch { throw new Error('请输入有效的服务地址'); }
  if (!['https:', 'http:'].includes(url.protocol) || url.username || url.password || url.search || url.hash || !['', '/'].includes(url.pathname)) {
    throw new Error('服务地址应为不含账号、参数和路径的 HTTPS 地址');
  }
  if (url.protocol !== 'https:' && !development) throw new Error('正式版本需要 HTTPS 服务地址');
  return url.origin;
}
export function createClient(base: string, token: () => string | null, expired: (usedToken?: string | null) => void, development = false, transport: typeof fetch = fetch) {
  const origin = normalizeBaseUrl(base, development);
  function url(path: string): string {
    let decoded: string;
    try { decoded = decodeURIComponent(path); } catch { throw new Error('接口地址格式无效'); }
    if (!decoded.startsWith('/api/') || decoded.includes('\\') || /(?:^|\/)\.\.(?:\/|$|\?)/.test(decoded) || /[\x00-\x1f]/.test(decoded)) throw new Error('接口地址无效');
    const result = new URL(path, origin);
    if (result.origin !== origin || result.hash || /(?:token|password|secret|key)/i.test([...result.searchParams.keys()].join(' '))) throw new Error('接口地址不能包含登录凭证');
    return result.href;
  }
  function headers(): Record<string, string> { const current = token(); return current ? { Authorization: `Bearer ${current}` } : {}; }
  return {
    url, headers,
    request: async <T = any>(path: string, options: ApiOptions = {}): Promise<T> => {
      const target = url(path);
      const controller = new AbortController();
      const cancel = () => controller.abort(options.signal?.reason);
      if (options.signal?.aborted) cancel(); else options.signal?.addEventListener('abort', cancel, { once: true });
      const timeout = setTimeout(() => controller.abort(new Error('连接超时，请检查网络后重试')), 30000);
      const usedToken = token();
      const requestHeaders: Record<string, string> = { Accept: 'application/json', ...(usedToken ? { Authorization: `Bearer ${usedToken}` } : {}) };
      let body: BodyInit | undefined;
      if (options.body instanceof FormData) body = options.body;
      else if (options.body !== undefined) { requestHeaders['Content-Type'] = 'application/json'; body = JSON.stringify(options.body); }
      try {
        const response = await transport(target, { method: options.method || 'GET', headers: requestHeaders, body, credentials: 'omit', signal: controller.signal });
        const payload = response.status === 204 ? null : await response.json().catch(() => null);
        if (!response.ok) {
          if (response.status === 401 || (response.status === 403 && path === '/api/auth/me')) expired(usedToken);
          const detail = payload?.detail;
          const message = typeof detail === 'string' ? detail : Array.isArray(detail) ? detail.map((item: any) => item.msg).filter(Boolean).join('；') : '请求未完成，请稍后重试';
          throw new ApiError(message || '请求未完成，请稍后重试', response.status);
        }
        return payload as T;
      } catch (error) {
        if (error instanceof ApiError || controller.signal.aborted) throw error;
        throw new ApiError('无法连接楼响服务，请检查网络和服务地址', 0);
      } finally { clearTimeout(timeout); options.signal?.removeEventListener('abort', cancel); }
    },
  };
}
