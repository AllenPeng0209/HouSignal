import { Platform } from 'react-native';
import { fetch as expoFetch } from 'expo/fetch';
import { Directory, File as NativeFile, FileMode, Paths } from 'expo-file-system';
import * as Sharing from 'expo-sharing';
import { assertTransferActive, createTransfers, type PrivateArtifact, type TransferAdapter } from './transfers-core';

const objectUrls = new Set<string>();
const privateDirectory = () => new Directory(Paths.cache, 'housignal-private');

function nativeArtifact(name: string): { file: NativeFile; artifact: () => PrivateArtifact } {
  const directory = new Directory(privateDirectory(), Date.now().toString(36) + '-' + Math.random().toString(36).slice(2));
  directory.create({ intermediates: true, idempotent: true });
  const file = new NativeFile(directory, name);
  file.create();
  return { file, artifact: () => ({ uri: file.uri, name, size: file.size,
    remove: async () => { if (directory.exists) directory.delete(); } }) };
}

function webArtifact(blob: Blob, name: string): PrivateArtifact {
  const uri = URL.createObjectURL(blob);
  objectUrls.add(uri);
  return { uri, name, size: blob.size, remove: async () => { URL.revokeObjectURL(uri); objectUrls.delete(uri); } };
}

const adapter: TransferAdapter = Platform.OS === 'web' ? {
  transport: globalThis.fetch.bind(globalThis),
  readUploadFile: async file => {
    if (!file.file) throw new Error('未取得所选文件，请重新选择素材');
    return file.file;
  },
  writeResponse: async (response, name, signal) => { const blob = await response.blob(); assertTransferActive(signal); return webArtifact(blob, name); },
  writeText: async (text, name, signal) => { assertTransferActive(signal); return webArtifact(new Blob([text], { type: 'text/plain;charset=utf-8' }), name); },
  share: async file => {
    const anchor = document.createElement('a');
    anchor.href = file.uri; anchor.download = file.name; anchor.rel = 'noopener';
    document.body.appendChild(anchor); anchor.click(); anchor.remove();
    await new Promise<void>(resolve => setTimeout(resolve, 200));
  },
  clear: async () => { for (const uri of objectUrls) URL.revokeObjectURL(uri); objectUrls.clear(); },
} : {
  transport: expoFetch as typeof fetch,
  readUploadFile: async file => {
    const selected = new NativeFile(file.uri);
    if (!selected.exists) throw new Error('素材已失效，请重新选择文件');
    return selected;
  },
  writeResponse: async (response, name, signal) => {
    const local = nativeArtifact(name);
    const handle = local.file.open(FileMode.WriteOnly);
    const reader = response.body!.getReader();
    try {
      while (true) {
        assertTransferActive(signal);
        const { done, value } = await reader.read();
        if (done) break;
        assertTransferActive(signal);
        handle.writeBytes(value);
      }
      return local.artifact();
    } catch (error) {
      handle.close();
      await reader.cancel().catch(() => {});
      await local.artifact().remove();
      throw error;
    } finally { if (handle.offset !== null) handle.close(); reader.releaseLock(); }
  },
  writeText: async (text, name, signal) => {
    assertTransferActive(signal);
    const local = nativeArtifact(name);
    try { local.file.write(text); return local.artifact(); }
    catch (error) { await local.artifact().remove(); throw error; }
  },
  share: async (file, mimeType) => {
    if (!await Sharing.isAvailableAsync()) throw new Error('当前设备无法分享文件');
    await Sharing.shareAsync(file.uri, { mimeType, dialogTitle: '分享楼响文件' });
  },
  clear: async () => { const directory = privateDirectory(); if (directory.exists) directory.delete(); },
};

export const { uploadAsset, shareFile, shareText, clearPrivateFiles } = createTransfers(adapter);
