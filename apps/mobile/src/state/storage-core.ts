import { normalizeBaseUrl } from '../api/client';

export type StoredCredentials = { token: string; expiresAt: string; baseUrl: string };
export type StoreAdapter = { get(key: string): Promise<string | null>; set(key: string, value: string): Promise<void>; remove(key: string): Promise<void> };
const CREDENTIALS_KEY = 'housignal.credentials.v1';

function validatedCredentials(value: unknown, development: boolean): StoredCredentials {
  if (!value || typeof value !== 'object') throw new Error('登录凭证已失效');
  const { token, expiresAt, baseUrl } = value as Partial<StoredCredentials>;
  if (typeof token !== 'string' || !/^hs_mobile_[A-Za-z0-9_-]{64}$/.test(token)
    || typeof expiresAt !== 'string' || !Number.isFinite(Date.parse(expiresAt)) || Date.parse(expiresAt) <= Date.now()
    || typeof baseUrl !== 'string') throw new Error('登录凭证已失效');
  return { token, expiresAt, baseUrl: normalizeBaseUrl(baseUrl, development) };
}

function preferenceKey(key: string): string {
  if (!/^[a-zA-Z0-9._-]{1,80}$/.test(key) || /(?:token|password|credential|secret|auth)/i.test(key)) {
    throw new Error('偏好设置名称无效');
  }
  return 'housignal.preference.' + key;
}

export function createStorage(privateStore: StoreAdapter, publicStore: StoreAdapter, development: boolean) {
  async function secureOperation<T>(work: () => Promise<T>): Promise<T> {
    try { return await work(); }
    catch { throw new Error('设备安全会话暂时不可用，请解锁设备后重试'); }
  }
  return {
    readCredentials: async (): Promise<StoredCredentials | null> => {
      const raw = await secureOperation(() => privateStore.get(CREDENTIALS_KEY));
      if (raw === null) return null;
      try { return validatedCredentials(JSON.parse(raw), development); }
      catch { await secureOperation(() => privateStore.remove(CREDENTIALS_KEY)); return null; }
    },
    saveCredentials: async (value: StoredCredentials): Promise<void> => {
      const credentials = validatedCredentials(value, development);
      await secureOperation(() => privateStore.set(CREDENTIALS_KEY, JSON.stringify(credentials)));
    },
    clearCredentials: async (): Promise<void> => { await secureOperation(() => privateStore.remove(CREDENTIALS_KEY)); },
    readPreference: async (key: string): Promise<string | null> => publicStore.get(preferenceKey(key)),
    savePreference: async (key: string, value: string | null): Promise<void> => {
      const target = preferenceKey(key);
      if (value === null) await publicStore.remove(target);
      else await publicStore.set(target, value);
    },
  };
}
