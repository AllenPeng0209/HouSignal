import { ApiError, type createClient } from '../api/client';
import type { Asset } from '../api/types';
import type { PickedFile } from './contracts';

export type PrivateArtifact = { uri: string; name: string; size: number; remove(): Promise<void> };
export type TransferAdapter = {
  transport: typeof fetch;
  readUploadFile(file: PickedFile): Promise<Blob>;
  writeResponse(response: Response, name: string, signal: AbortSignal): Promise<PrivateArtifact>;
  writeText(text: string, name: string, signal: AbortSignal): Promise<PrivateArtifact>;
  share(file: PrivateArtifact, mimeType?: string): Promise<void>;
  clear(): Promise<void>;
};
const MAX_UPLOAD_BYTES = 100 * 1024 * 1024;

// React Native's AbortSignal omits throwIfAborted even when the DOM types expose it.
export function assertTransferActive(signal: AbortSignal): void {
  if (signal.aborted) throw new Error('文件传输已取消');
}

export function sanitizeFilename(value: string): string {
  return value.replace(/[<>:"/\\|?*\u0000-\u001f]/g, '_').replace(/\.{2,}/g, '_').replace(/^[.\s]+/, '').trim().slice(0, 120) || '楼响文件';
}

function checkSize(size: number): void {
  if (!Number.isFinite(size) || size <= 0) throw new Error('不能上传空文件或无法读取的素材');
  if (size > MAX_UPLOAD_BYTES) throw new Error('单个素材不能超过 100MB');
}

function assetPayload(payload: unknown, client: ReturnType<typeof createClient>, projectId: string): Asset {
  if (!payload || typeof payload !== 'object') throw new Error('服务返回的素材格式无效');
  const value = payload as Partial<Asset>;
  if (typeof value.id !== 'string' || !value.id || /[\\/\u0000-\u001f]/.test(value.id)
    || typeof value.name !== 'string' || typeof value.mime_type !== 'string'
    || typeof value.kind !== 'string' || typeof value.byte_size !== 'number' || value.byte_size <= 0 || value.byte_size > MAX_UPLOAD_BYTES
    || typeof value.file_url !== 'string' || !Number.isFinite(Date.parse(value.created_at ?? ''))
    || !value.metadata_json || typeof value.metadata_json !== 'object') throw new Error('服务返回的素材格式无效');
  const expected = `/api/projects/${encodeURIComponent(projectId)}/assets/${encodeURIComponent(value.id)}/file`;
  if (client.url(value.file_url) !== client.url(expected)) throw new Error('服务返回的素材不属于当前项目');
  return value as Asset;
}

async function successfulResponse(response: Response): Promise<void> {
  if (response.status >= 200 && response.status < 300 && !response.redirected) return;
  const body = await response.json().catch(() => null);
  throw new ApiError(typeof body?.detail === 'string' ? body.detail : '传输未完成，请检查权限和网络后重试', response.status);
}

export function createTransfers(adapter: TransferAdapter) {
  const active = new Set<AbortController>();
  let clearing = false;
  async function operation<T>(action: (signal: AbortSignal) => Promise<T>): Promise<T> {
    if (clearing) throw new Error('正在清理会话文件，请稍后重试');
    const controller = new AbortController();
    active.add(controller);
    const timeout = setTimeout(() => controller.abort(new Error('文件传输超时，请稍后重试')), 10 * 60 * 1000);
    try { return await action(controller.signal); }
    catch (error) {
      if (controller.signal.aborted) throw new Error('文件传输已取消');
      if (error instanceof TypeError) throw new ApiError('无法传输文件，请检查网络与服务地址', 0);
      throw error;
    } finally { clearTimeout(timeout); active.delete(controller); }
  }
  async function shareArtifact(file: PrivateArtifact, signal: AbortSignal, mimeType?: string): Promise<void> {
    try {
      assertTransferActive(signal);
      if (!Number.isFinite(file.size) || file.size <= 0) throw new Error('下载的文件为空，请重试');
      await adapter.share(file, mimeType);
    } finally { await file.remove(); }
  }
  function authorizedHeaders(client: ReturnType<typeof createClient>): Record<string, string> {
    const headers = client.headers();
    if (!headers.Authorization) throw new ApiError('请先登录', 401);
    return { Authorization: headers.Authorization };
  }
  return {
    uploadAsset: async (client: ReturnType<typeof createClient>, projectId: string, file: PickedFile, onProgress?: (fraction: number) => void): Promise<Asset> => operation(async signal => {
      if (!projectId || /[\\/\u0000-\u001f?#]/.test(projectId)) throw new Error('项目地址无效');
      if (!file.uri || !file.name || !/^(?:file|content|blob):/.test(file.uri)) throw new Error('请重新选择本地素材');
      if (file.size !== undefined) checkSize(file.size);
      const url = client.url(`/api/projects/${encodeURIComponent(projectId)}/assets`);
      const headers = authorizedHeaders(client);
      const blob = await adapter.readUploadFile(file);
      checkSize(blob.size);
      assertTransferActive(signal);
      const body = new FormData();
      body.append('file', blob, sanitizeFilename(file.name));
      onProgress?.(0);
      const response = await adapter.transport(url, { method: 'POST', headers, body, credentials: 'omit', redirect: 'error', signal });
      assertTransferActive(signal);
      await successfulResponse(response);
      const payload = assetPayload(await response.json().catch(() => null), client, projectId);
      assertTransferActive(signal);
      onProgress?.(1);
      return payload;
    }),
    shareFile: async (client: ReturnType<typeof createClient>, path: string, filename: string, mimeType?: string): Promise<void> => operation(async signal => {
      const url = client.url(path), headers = authorizedHeaders(client);
      const response = await adapter.transport(url, { headers, credentials: 'omit', redirect: 'error', signal });
      assertTransferActive(signal);
      await successfulResponse(response);
      const returnedType = response.headers.get('Content-Type')?.split(';')[0].trim().toLowerCase();
      const expectedType = mimeType?.split(';')[0].trim().toLowerCase();
      const decodedPath = decodeURIComponent(path);
      const jsonArtifact = /^\/api\/projects\/[^/?#]+\/operations\/skills\/artifacts\/[^/?#]+\/[^/?#]+$/.test(decodedPath)
        && (expectedType === 'application/json' || filename.toLowerCase().endsWith('.json'));
      if (response.status !== 200 || !response.body || response.headers.get('Content-Length') === '0'
        || returnedType === 'text/html' || (returnedType === 'application/json' && !jsonArtifact)
        || (expectedType && returnedType && returnedType !== 'application/octet-stream' && returnedType !== expectedType)) {
        await response.body?.cancel();
        throw new Error('返回内容不是可分享的文件，请重新获取成片或素材');
      }
      const file = await adapter.writeResponse(response, sanitizeFilename(filename), signal);
      await shareArtifact(file, signal, mimeType ?? returnedType);
    }),
    shareText: async (text: string, filename: string): Promise<void> => operation(async signal => {
      if (!text.trim()) throw new Error('没有可导出的内容');
      const file = await adapter.writeText(text, sanitizeFilename(filename), signal);
      await shareArtifact(file, signal, 'text/plain');
    }),
    clearPrivateFiles: async (): Promise<void> => {
      clearing = true;
      try {
        for (const controller of active) controller.abort();
        await adapter.clear();
      } finally { clearing = false; }
    },
  };
}
