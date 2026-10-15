import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState, type ReactNode } from 'react';
import { Alert, AppState as NativeAppState, Platform, Text, View } from 'react-native';
import { router, type Href } from 'expo-router';
import * as Notifications from 'expo-notifications';
import { AppContext } from './AppContext';
import { SessionEpoch, JobTransitions } from './lifecycle';
import { clearCredentials, readCredentials, readPreference, saveCredentials, savePreference } from './storage';
import { clearPrivateFiles, shareFile as transferFile, shareText as transferText, uploadAsset as transferUpload } from '../lib/transfers';
import { createClient, normalizeBaseUrl } from '../api/client';
import type { AuthSession, Citation, Job, Project, Session } from '../api/types';
import type { AppState, Params, ScreenName } from '../lib/contracts';
import { colors } from '../components/ui';
import { permissionsFor } from '../domain/permissions';

type Runtime = { session: Session | null; ready: boolean; error: string; baseUrl: string; login: (server: string, email: string, password: string) => Promise<void>; retry: () => Promise<void>; logout: () => Promise<void> };
const RuntimeContext = createContext<Runtime | null>(null);
const DEFAULT_SERVER = process.env.EXPO_PUBLIC_API_URL || (__DEV__ ? (Platform.OS === 'android' ? 'http://10.0.2.2:8018' : 'http://127.0.0.1:8018') : '');
const routes: Record<ScreenName, string> = { home: '/', projects: '/projects', project: '/project', assets: '/materials', assistant: '/assistant', studio: '/studio', content: '/content', publications: '/publications', jobs: '/jobs', leads: '/leads', lead: '/lead', reports: '/reports', settings: '/settings', accounts: '/accounts', skills: '/skills' };

if (Platform.OS !== 'web') Notifications.setNotificationHandler({ handleNotification: async () => ({ shouldPlaySound: false, shouldSetBadge: false, shouldShowBanner: true, shouldShowList: true }) });
export function useRuntime(): Runtime { const context = useContext(RuntimeContext); if (!context) throw new Error('Runtime context is unavailable'); return context; }
export function RuntimeProvider({ children }: { children: ReactNode }) {
  const [session, setSession] = useState<Session | null>(null);
  const [accessToken, setAccessToken] = useState<string | null>(null);
  const [ready, setReady] = useState(false), [error, setError] = useState(''), [baseUrl, setBaseUrl] = useState(DEFAULT_SERVER);
  const [projects, setProjects] = useState<Project[]>([]), [projectId, setProjectId] = useState(''), [refreshKey, setRefreshKey] = useState(0);
  const [toast, setToast] = useState(''), [notificationsEnabled, setNotificationsEnabled] = useState(false), [pendingJobs, setPendingJobs] = useState<Job[]>([]);
  const epoch = useRef(new SessionEpoch()), token = useRef<string | null>(null), projectLoad = useRef(0);
  const tracked = useRef(new Set<string>()), transitions = useRef(new JobTransitions());
  const notify = useCallback((message: string) => setToast(message), []);
  const invalidate = useCallback((usedToken?: string | null) => {
    if (!usedToken || usedToken !== token.current) return;
    epoch.current.invalidate(epoch.current.current); token.current = null; setAccessToken(null); projectLoad.current++;
    setSession(null); setProjects([]); setProjectId(''); setPendingJobs([]); tracked.current.clear(); transitions.current.reset();
    setError('登录已过期或权限已变更，请重新登录');
    void clearCredentials().catch(() => {}); void clearPrivateFiles().catch(() => {});
    void import('../screens/Assistant').then(module => module.clearAssistantDrafts());
    void import('../screens/Content').then(module => module.clearContentDrafts());
    void import('../screens/Assets').then(module => module.clearCaptureFiles()).catch(() => {});
  }, []);
  // Construction registers the invalidation callback; it does not invoke it during render.
  // eslint-disable-next-line react-hooks/refs
  const client = useMemo(() => createClient(baseUrl || 'https://unconfigured.invalid', () => accessToken, invalidate, __DEV__), [baseUrl, accessToken, invalidate]);

  const loadProjects = useCallback(async (identity: Session, currentClient = client) => {
    const version = ++projectLoad.current;
    const credential = token.current;
    const rows = await currentClient.request<Project[]>('/api/projects');
    const saved = await readPreference(`p.${identity.organization.id}.${identity.user.id}`);
    if (version !== projectLoad.current || credential !== token.current) return;
    setProjects(rows);
    setProjectId(current => rows.some(p => p.id === current) ? current : rows.some(p => p.id === saved) ? saved! : rows[0]?.id || '');
  }, [client]);

  const restore = useCallback(async () => {
    try {
      const saved = await readCredentials();
      const preferred = await readPreference('server');
      const server = saved?.baseUrl || preferred || DEFAULT_SERVER;
      if (server) setBaseUrl(normalizeBaseUrl(server, __DEV__));
      setNotificationsEnabled(await readPreference('notifications') === 'true');
      if (!saved) return;
      token.current = saved.token; setAccessToken(saved.token);
      const version = epoch.current.begin(saved.token);
      const restoredClient = createClient(saved.baseUrl, () => saved.token, invalidate, __DEV__);
      const identity = await restoredClient.request<Session>('/api/auth/me');
      if (!epoch.current.isCurrent(version)) return;
      setSession(identity); await loadProjects(identity, restoredClient);
    } catch (e) { setError((e as Error).message); }
    finally { setReady(true); }
  }, [invalidate, loadProjects]);
  // Restore once; later retries are explicit to avoid an authentication effect loop.
  useEffect(() => { void restore(); }, []); // eslint-disable-line react-hooks/exhaustive-deps, react-hooks/set-state-in-effect -- State updates follow awaited secure storage, and initialization runs once.
  useEffect(() => { if (!toast) return; const timer = setTimeout(() => setToast(''), 4500); return () => clearTimeout(timer); }, [toast]);

  const login = useCallback(async (server: string, email: string, password: string) => {
    const origin = normalizeBaseUrl(server, __DEV__);
    const loginClient = createClient(origin, () => null, () => {}, __DEV__);
    const auth = await loginClient.request<AuthSession>('/api/mobile/auth/login', { method: 'POST', body: { email: email.trim(), password } });
    await clearPrivateFiles();
    await saveCredentials({ token: auth.access_token, expiresAt: auth.expires_at, baseUrl: origin });
    await savePreference('server', origin);
    token.current = auth.access_token; setAccessToken(auth.access_token); epoch.current.begin(auth.access_token);
    setBaseUrl(origin); setProjectId(''); setError(''); setSession({ user: auth.user, organization: auth.organization, role: auth.role, project_ids: auth.project_ids });
    const authenticated = createClient(origin, () => auth.access_token, invalidate, __DEV__);
    try { await loadProjects(auth, authenticated); } catch (e) { notify((e as Error).message); }
  }, [invalidate, loadProjects, notify]);

  const reload = useCallback(async () => {
    if (!session) return;
    const version = epoch.current.current;
    const identity = await client.request<Session>('/api/auth/me');
    if (!epoch.current.isCurrent(version) || !token.current) return;
    setSession(identity); await loadProjects(identity); setRefreshKey(value => value + 1);
  }, [client, loadProjects, session]);
  const navigate = useCallback((screen: ScreenName, params?: Params) => {
    const query: Record<string, string> = {};
    if (params?.id) query.id = params.id;
    if (params?.conversationId) query.conversationId = params.conversationId;
    router.push({ pathname: routes[screen], params: query } as Href);
  }, []);
  const back = useCallback(() => { if (router.canGoBack()) router.back(); else router.replace('/' as Href); }, []);
  const selectProject = useCallback(async (id: string) => {
    if (!session) throw new Error('请先登录');
    const version = epoch.current.current; projectLoad.current++;
    const selected = await client.request<Project>(`/api/projects/${encodeURIComponent(id)}`);
    if (!epoch.current.isCurrent(version)) throw new Error('会话已变更，请重新选择项目');
    await savePreference(`p.${session.organization.id}.${session.user.id}`, id);
    if (!epoch.current.isCurrent(version)) return;
    setProjects(current => [selected, ...current.filter(project => project.id !== id)]);
    transitions.current.reset(); tracked.current.clear(); setPendingJobs([]); setProjectId(id); router.replace('/' as Href);
  }, [session, client]);
  const logout = useCallback(async () => {
    const credential = token.current;
    const { flushAssistantDrafts, clearAssistantDrafts } = await import('../screens/Assistant');
    try { await flushAssistantDrafts(); } catch {
      const discard = Platform.OS === 'web' ? window.confirm('有草稿尚未保存。是否放弃这些本机编辑并退出？') : await new Promise<boolean>(resolve => Alert.alert('有草稿尚未保存', '网络或权限变更导致保存未完成。你可以返回继续处理，或放弃本机编辑并退出。', [{ text: '返回处理', style: 'cancel', onPress: () => resolve(false) }, { text: '放弃并退出', style: 'destructive', onPress: () => resolve(true) }], { cancelable: true, onDismiss: () => resolve(false) }));
      if (!discard) return;
    }
    try { await client.request('/api/mobile/auth/logout', { method: 'POST' }); }
    catch (e) { if ((e as any).status !== 401) throw new Error('退出未完成，请联网后重试，以撤销此设备的会话'); }
    if (token.current && token.current !== credential) return;
    clearAssistantDrafts(); const { clearContentDrafts } = await import('../screens/Content'); clearContentDrafts();
    token.current = null; setAccessToken(null); epoch.current.invalidate(epoch.current.current); projectLoad.current++;
    await clearCredentials(); await clearPrivateFiles();
    const { clearCaptureFiles } = await import('../screens/Assets'); await clearCaptureFiles();
    if (Platform.OS !== 'web') await Notifications.dismissAllNotificationsAsync().catch(() => {});
    setSession(null); setProjects([]); setProjectId(''); setPendingJobs([]); tracked.current.clear(); transitions.current.reset(); setError('');
  }, [client]);
  const project = projects.find(value => value.id === projectId) || null;

  const pollUserId = session?.user.id, pollOrganizationId = session?.organization.id, pollProjectId = project?.id;
  useEffect(() => {
    if (!pollUserId || !pollOrganizationId || !pollProjectId) return;
    let running = false, stopped = false, active = NativeAppState.currentState === 'active' || Platform.OS === 'web';
    let controller: AbortController | null = null;
    const credential = token.current;
    const poll = async () => {
      if (running || stopped || !active) return;
      running = true; controller = new AbortController();
      try {
        const jobs = await client.request<Job[]>(`/api/projects/${pollProjectId}/jobs`, { signal: controller.signal });
        if (stopped || credential !== token.current) return;
        setPendingJobs(jobs.filter(job => ['queued', 'running'].includes(job.status)));
        for (const id of transitions.current.observe(jobs)) {
          const job = jobs.find(value => value.id === id)!;
          if (!tracked.current.has(id)) continue;
          tracked.current.delete(id);
          const message = job.status === 'succeeded' ? '任务已完成，可在任务中心查看结果' : job.status === 'failed' ? '任务未完成，可在任务中心查看原因' : '任务已取消';
          notify(message); setRefreshKey(value => value + 1);
          if (notificationsEnabled && Platform.OS !== 'web') await Notifications.scheduleNotificationAsync({ content: { title: '楼响任务更新', body: message, data: { screen: 'jobs' } }, trigger: null }).catch(() => {});
        }
      } catch { /* Each screen retains an actionable error; transient polling is quiet. */ }
      finally { running = false; }
    };
    void poll(); const timer = setInterval(() => void poll(), 5000);
    const listener = NativeAppState.addEventListener('change', state => { active = state === 'active'; if (active) void poll(); else controller?.abort(); });
    return () => { stopped = true; clearInterval(timer); controller?.abort(); listener.remove(); };
  }, [client, pollUserId, pollOrganizationId, pollProjectId, notificationsEnabled, notify]);
  useEffect(() => {
    if (Platform.OS === 'web') return;
    const listener = Notifications.addNotificationResponseReceivedListener(() => { if (token.current) navigate('jobs'); });
    return () => listener.remove();
  }, [navigate]);
  const enableNotifications = useCallback(async (enabled: boolean) => {
    if (enabled && Platform.OS === 'web') throw new Error('手机 App 才支持系统提醒');
    if (enabled) {
      if (Platform.OS === 'android') await Notifications.setNotificationChannelAsync('tasks', { name: '任务更新', importance: Notifications.AndroidImportance.DEFAULT });
      const permission = await Notifications.requestPermissionsAsync();
      if (!permission.granted) throw new Error('提醒权限未开启，可在手机设置中允许楼响发送通知');
    }
    await savePreference('notifications', enabled ? 'true' : 'false'); setNotificationsEnabled(enabled);
  }, []);
  const openCitation = useCallback((citation: Citation) => {
    const screens: Record<string, ScreenName> = { fact: 'project', asset: 'assets', lead: 'lead', inquiry: 'leads', publication: 'publications', content: 'content' };
    navigate(screens[citation.kind] || 'project', citation.id ? { id: citation.id } : undefined);
  }, [navigate]);
  const context: AppState | null = session ? {
    api: client.request, baseUrl, authHeaders: client.headers, session, project, projects, refreshKey,
    ...permissionsFor(session),
    navigate, back, selectProject, reload, notify, logout,
    trackJob: id => { tracked.current.add(id); transitions.current.track(id); },
    uploadAsset: (id, file, progress) => transferUpload(client, id, file, progress),
    shareFile: (path, filename, mimeType) => transferFile(client, path, filename, mimeType), shareText: transferText,
    openCitation, notificationsEnabled, enableNotifications, pendingJobs,
  } : null;
  return <RuntimeContext.Provider value={{ session, ready, error, baseUrl, login, retry: async () => { setReady(false); setError(''); await restore(); }, logout }}><AppContext.Provider value={context}>{children}{toast ? <View pointerEvents="none" style={{ position: 'absolute', bottom: 105, left: 22, right: 22, backgroundColor: colors.navy, padding: 15, borderRadius: 14 }}><Text accessibilityRole="alert" style={{ color: '#FFF', lineHeight: 21, fontSize: 14 }}>{toast}</Text></View> : null}</AppContext.Provider></RuntimeContext.Provider>;
}
