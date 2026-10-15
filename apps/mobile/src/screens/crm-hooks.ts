import { useCallback, useEffect, useRef, useState } from 'react';
import { useApp } from '../state/AppContext';

export function useCrmResource<T>(path: string | null) {
  const { api, refreshKey } = useApp();
  const [state, setState] = useState<{ key: string | null; data: T | null; error: string }>({ key: null, data: null, error: '' });
  const [version, setVersion] = useState(0);
  const retry = useCallback(() => setVersion(v => v + 1), []);
  const key = path ? `${path}:${refreshKey}:${version}` : null;
  useEffect(() => {
    if (!path) return;
    const controller = new AbortController();
    api<T>(path, { signal: controller.signal }).then(data => { if (!controller.signal.aborted) setState({ key, data, error: '' }); }).catch(error => { if (!controller.signal.aborted) setState({ key, data: null, error: error instanceof Error ? error.message : '暂时无法加载，请重试' }); });
    return () => controller.abort();
  }, [api, path, key]);
  const ready = key !== null && key === state.key;
  return { data: ready ? state.data : null, loading: !!path && !ready, error: ready ? state.error : '', retry };
}

export function useCrmMutation() {
  const { notify } = useApp();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const locked = useRef(false);
  async function run<T>(operation: () => Promise<T>, message?: string): Promise<{ data: T } | null> {
    if (locked.current) return null;
    locked.current = true; setBusy(true); setError('');
    try { const data = await operation(); if (message) notify(message); return { data }; }
    catch (cause) { setError(cause instanceof Error ? cause.message : '操作未完成，请重试'); return null; }
    finally { locked.current = false; setBusy(false); }
  }
  return { busy, error, run };
}
