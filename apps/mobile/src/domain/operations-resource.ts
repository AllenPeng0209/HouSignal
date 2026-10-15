import { useCallback, useEffect, useRef, useState } from 'react';
import { useApp } from '../state/AppContext';
export function useOperationsResource<T>(path: string | null) {
  const { api, refreshKey } = useApp();
  const [revision, setRevision] = useState(0);
  const [state, setState] = useState<{ path: string | null; revision: number; refreshKey: number; data: T | null; error: string }>({ path: null, revision: -1, refreshKey: -1, data: null, error: '' });
  useEffect(() => {
    if (!path) return;
    const controller = new AbortController();
    api<T>(path, { signal: controller.signal }).then(data => {
      if (!controller.signal.aborted) setState({ path, revision, refreshKey, data, error: '' });
    }).catch(error => {
      if (!controller.signal.aborted) setState({ path, revision, refreshKey, data: null, error: error instanceof Error ? error.message : '读取失败，请重试' });
    });
    return () => controller.abort();
  }, [api, path, refreshKey, revision]);
  const retry = useCallback(() => setRevision(value => value + 1), []);
  const loading = !!path && (state.path !== path || state.revision !== revision || state.refreshKey !== refreshKey);
  return { data: state.path === path ? state.data : null, error: !loading && state.path === path ? state.error : '', loading, retry };
}
export function useOperationsMutation() {
  const { project, notify } = useApp();
  const currentProject = useRef(project?.id);
  useEffect(() => { currentProject.current = project?.id; }, [project?.id]);
  const running = useRef(false);
  const mounted = useRef(true);
  useEffect(() => { mounted.current = true; return () => { mounted.current = false; }; }, []);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  async function run<T>(operation: () => Promise<T>, message?: string): Promise<T | null> {
    if (running.current) return null;
    running.current = true; setBusy(true); setError('');
    const scope = currentProject.current;
    try {
      const result = await operation();
      if (!mounted.current || scope !== currentProject.current) return null;
      if (message) notify(message);
      return result;
    } catch (value) {
      if (mounted.current && scope === currentProject.current) setError(value instanceof Error ? value.message : '操作失败，请重试');
      return null;
    } finally { running.current = false; if (mounted.current) setBusy(false); }
  }
  return { busy, error, setError, run };
}
