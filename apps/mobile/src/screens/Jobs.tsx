import { useEffect, useState } from 'react';
import { useIsFocused } from 'expo-router';
import { ActivityIndicator, AppState, StyleSheet, Text, View } from 'react-native';
import { useApp } from '../state/AppContext';
import type { Job } from '../api/types';
import { ApiError } from '../api/client';
import { Badge, Button, Card, Empty, Heading, Notice, Page, Row, Select, Sheet, colors, formatDate } from '../components/ui';
import { createForegroundPoller } from '../domain/jobs-poller';
import { useAlive } from '../domain/project-resource';
const kinds: Record<string, string> = { chat: '楼管家问答', generate_content: '生成脚本', render_content: '制作成片', extract_asset: '解析素材', content_skill: '内容技能' };
const statuses: Record<string, string> = { queued: '排队中', running: '执行中', succeeded: '已完成', failed: '失败', cancelled: '已取消' };
const active = (job: Job) => ['queued', 'running'].includes(job.status);

export function JobsScreen() {
  const { api, project, refreshKey, navigate, notify, reload } = useApp();
  const [jobs, setJobs] = useState<Job[]>([]), [loading, setLoading] = useState(true), [error, setError] = useState(''), [loadError, setLoadError] = useState('');
  const [refresh, setRefresh] = useState(0), [filter, setFilter] = useState('all'), [selected, setSelected] = useState<Job | null>(null), [busy, setBusy] = useState(false);
  const pid = project?.id;
  const alive = useAlive();
  const focused = useIsFocused();
  function refreshData() { setLoading(true); setError(''); setLoadError(''); setRefresh(value => value + 1); }
  useEffect(() => {
    if (!pid || !focused) return;
    const poller = createForegroundPoller({ read: signal => api<Job[]>(`/api/projects/${pid}/jobs`, { signal }), onData: rows => { setJobs(rows); setLoadError(''); setLoading(false); }, onError: value => { if (value instanceof ApiError && [401, 403, 404].includes(value.status)) setJobs([]); setLoadError(value instanceof Error ? value.message : '任务加载失败'); setLoading(false); } });
    if (!AppState.currentState || AppState.currentState === 'active') poller.resume();
    const listener = AppState.addEventListener('change', state => state === 'active' ? poller.resume() : poller.pause());
    return () => { listener.remove(); poller.dispose(); };
  }, [api, pid, focused, refresh, refreshKey]);
  async function cancel() {
    if (!selected || busy) return;
    setBusy(true); setError('');
    try {
      const result = await api<Job>(`/api/jobs/${selected.id}/cancel`, { method: 'POST' });
      if (!alive.current) return;
      setSelected(null); setRefresh(value => value + 1); await reload();
      if (alive.current) notify(result.status === 'cancelled' ? '任务已取消。' : `任务状态已更新：${statuses[result.status] || result.status}。`);
    } catch (value) { if (alive.current) setError(value instanceof Error ? value.message : '取消任务失败'); }
    finally { if (alive.current) setBusy(false); }
  }
  if (!project) return <Page><Heading title="任务中心" /><Empty title="先选择一个项目" action={<Button title="选择项目" onPress={() => navigate('projects')} />} /></Page>;
  const visible = jobs.filter(job => filter === 'all' || (filter === 'active' ? active(job) : job.status === filter));
  return <Page refreshing={loading} onRefresh={refreshData} testID="jobs-screen">
    <Heading title="任务中心" subtitle={`${project.name} · 后台持续执行，回来即可查看结果`} />
    <Select label="任务状态" value={filter} onChange={setFilter} options={[{ label: '全部任务', value: 'all' }, { label: '进行中', value: 'active' }, { label: '已完成', value: 'succeeded' }, { label: '失败', value: 'failed' }, { label: '已取消', value: 'cancelled' }]} />
    {error || loadError ? <Notice message={error || loadError} onRetry={refreshData} /> : null}
    {loading && !jobs.length ? <ActivityIndicator accessibilityLabel="正在加载任务" /> : !visible.length && !error && !loadError ? <Empty title="此状态下没有任务" description="脚本生成、素材解析和成片制作的实际进度会显示在这里。" action={<Button title="去内容工作台" onPress={() => navigate('studio')} />} /> : visible.map(job => {
      const percent = Math.max(0, Math.min(100, Math.round(job.progress * 100)));
      return <Card key={job.id}>
        <Row><Text style={s.title}>{kinds[job.kind] || job.kind}</Text><Badge label={statuses[job.status] || job.status} tone={job.status === 'failed' ? 'red' : job.status === 'succeeded' ? 'green' : active(job) ? 'blue' : 'muted'} /></Row>
        <Text style={s.hint}>{formatDate(job.created_at)}</Text>
        {active(job) ? <><View accessible accessibilityRole="progressbar" accessibilityLabel="任务进度" accessibilityValue={{ min: 0, max: 100, now: percent }} style={s.progress}><View style={[s.fill, { width: `${percent}%` }]} /></View><Text style={s.body}>{job.phase || (job.status === 'queued' ? '等待 Worker 执行' : '后台正在处理')} · {percent}%</Text></> : <Text style={s.body}>{job.phase || statuses[job.status] || job.status}</Text>}
        {job.error ? <Text selectable style={s.error}>{job.error}</Text> : null}
        {job.status === 'failed' ? <Text style={s.hint}>核对上面的失败原因，回到原内容或素材修正后重新提交。</Text> : null}
        <Row>{job.status === 'succeeded' && job.result?.content_version_id ? <Button title="打开内容结果" tone="secondary" onPress={() => navigate('content', { id: String(job.result.content_version_id) })} /> : job.status === 'succeeded' && job.kind === 'extract_asset' ? <Button title="查看素材" tone="secondary" onPress={() => navigate('assets')} /> : job.status === 'succeeded' && job.kind === 'chat' ? <Button title="查看楼管家" tone="secondary" onPress={() => navigate('assistant')} /> : null}
        {active(job) ? <Button title="取消任务" tone="ghost" onPress={() => setSelected(job)} /> : null}
        {job.status === 'failed' && ['generate_content', 'render_content', 'content_skill'].includes(job.kind) ? <Button title="返回内容工作台" tone="secondary" onPress={() => navigate('studio')} /> : null}</Row>
      </Card>;
    })}
    <Sheet visible={!!(selected)} title="取消后台任务" onClose={() => !busy && setSelected(null)}>
      <Text style={s.body}>取消后该任务不会继续生成结果。需要时可从原内容或素材重新提交。</Text>
      {error ? <Notice message={error} /> : null}
      <Button title="确认取消任务" tone="danger" loading={busy} onPress={() => void cancel()} />
      <Button title="保留任务" tone="secondary" disabled={busy} onPress={() => setSelected(null)} />
    </Sheet>
  </Page>;
}
const s = StyleSheet.create({ title: { fontSize: 17, fontWeight: '600', color: colors.navy }, body: { fontSize: 14, lineHeight: 23, color: colors.text, marginVertical: 10 }, hint: { fontSize: 12, lineHeight: 20, color: colors.muted, marginVertical: 5 }, error: { fontSize: 14, lineHeight: 22, color: colors.red, marginBottom: 10 }, progress: { height: 8, borderRadius: 4, backgroundColor: colors.border, overflow: 'hidden', marginTop: 8 }, fill: { height: 8, borderRadius: 4, backgroundColor: colors.primary } });
