import { useCallback, useEffect, useState } from 'react';
import { useIsFocused } from 'expo-router';
import { ActivityIndicator, AppState, StyleSheet, Text, View } from 'react-native';
import { useApp } from '../state/AppContext';
import type { Content, Topic } from '../api/types';
import { ApiError } from '../api/client';
import { Badge, Button, Card, Empty, Heading, Notice, Page, Row, Select, colors, formatDate } from '../components/ui';
import { createForegroundPoller } from '../domain/jobs-poller';
import { useAlive } from '../domain/project-resource';

export function StudioScreen() {
  const { api, project, refreshKey, canEdit, navigate, notify, trackJob, pendingJobs } = useApp();
  const [topics, setTopics] = useState<Topic[]>([]), [contents, setContents] = useState<Content[]>([]);
  const [loading, setLoading] = useState(true), [error, setError] = useState(''), [loadError, setLoadError] = useState(''), [busy, setBusy] = useState('');
  const [refresh, setRefresh] = useState(0), [chosenChannel, setChosenChannel] = useState<{ projectId: string; value: string } | null>(null);
  const channel = chosenChannel?.projectId === project?.id ? chosenChannel!.value : project?.channel || '抖音';
  const setChannel = (value: string) => setChosenChannel({ projectId: project!.id, value });
  const alive = useAlive();
  const focused = useIsFocused();
  const productionBusy = pendingJobs.some(job => job.property_id === project?.id && ['generate_content', 'render_content'].includes(job.kind));
  const base = project ? `/api/projects/${project.id}` : '';
  function refreshData() { setLoading(true); setLoadError(''); setError(''); setRefresh(value => value + 1); }
  const read = useCallback(async (signal: AbortSignal) => {
    const [topicRows, contentRows] = await Promise.all([api<Topic[]>(`${base}/topics`, { signal }), api<Content[]>(`${base}/contents`, { signal })]);
    return { topics: topicRows, contents: contentRows };
  }, [api, base]);
  useEffect(() => {
    if (!base || !focused) return;
    const poller = createForegroundPoller({ read, interval: 10000, onData: rows => { setTopics(rows.topics); setContents(rows.contents); setLoadError(''); setLoading(false); }, onError: value => { if (value instanceof ApiError && [401, 403, 404].includes(value.status)) { setTopics([]); setContents([]); } setLoadError(value instanceof Error ? value.message : '内容资料加载失败'); setLoading(false); } });
    if (!AppState.currentState || AppState.currentState === 'active') poller.resume();
    const listener = AppState.addEventListener('change', state => state === 'active' ? poller.resume() : poller.pause());
    return () => { listener.remove(); poller.dispose(); };
  }, [base, read, focused, refresh, refreshKey]);
  async function generate(topic: Topic) {
    if (!project || busy || productionBusy || !canEdit || !topic.eligible) return;
    setBusy(topic.id); setError('');
    try {
      const result = await api<{ job_id: string }>(`${base}/contents/generate`, { method: 'POST', body: { topic_id: topic.id, channel } });
      if (!alive.current) return;
      trackJob(result.job_id); notify('脚本任务已提交，可在任务中心查看进度。'); navigate('jobs');
    } catch (value) { if (alive.current) setError(value instanceof Error ? value.message : '提交脚本任务失败'); }
    finally { if (alive.current) setBusy(''); }
  }
  if (!project) return <Page><Heading title="内容工作台" /><Empty title="先选择一个项目" description="选题、脚本和成片均保存在对应项目中。" action={<Button title="选择项目" onPress={() => navigate('projects')} />} /></Page>;
  return <Page refreshing={loading} onRefresh={refreshData} testID="studio-screen">
    <Heading title="内容工作台" subtitle={`${project.name} · 从有依据的选题开始`} action={<Button title="任务中心" tone="secondary" small onPress={() => navigate('jobs')} />} />
    {error || loadError ? <Notice message={error || loadError} onRetry={refreshData} /> : null}
    {productionBusy ? <Card><Text style={s.body}>项目有脚本或成片任务正在处理，完成后可以继续提交。</Text><Button title="查看后台任务" tone="secondary" onPress={() => navigate('jobs')} /></Card> : null}
    <Card><Text style={s.title}>这一条，为谁而讲</Text><Text style={s.body}>{project.audience || '先在项目资料中补充目标客群。'}</Text><Select label="生成渠道" value={channel} options={[{ label: '抖音', value: '抖音' }, { label: '小红书', value: '小红书' }, ...(channel && !['抖音', '小红书'].includes(channel) ? [{ label: channel, value: channel }] : [])]} onChange={setChannel} /><Text style={s.hint}>生成结果先保存为草稿，经过真实成片与人工审核后才能导出。</Text></Card>
    <Row><Text style={s.section}>事实支持的选题</Text><Button title="补充资料" tone="ghost" small onPress={() => navigate('project')} /></Row>
    {loading && !topics.length ? <ActivityIndicator accessibilityLabel="正在加载选题" /> : !topics.length && !error && !loadError ? <Empty title="暂无可用选题" description="补充并核验项目事实后再刷新。" /> : topics.map(topic => <Card key={topic.id}>
      <Row><Text style={s.title}>{topic.title}</Text><Badge label={topic.eligible ? '资料可用' : '待补依据'} tone={topic.eligible ? 'green' : 'amber'} /></Row>
      <Text style={s.body}>{topic.angle}</Text>
      {!topic.eligible ? <Text style={s.warning}>待补充：{topic.missing?.join('、') || '对应项目事实'}</Text> : <Text style={s.hint}>{topic.fact_refs?.length || 0} 条已核验事实引用</Text>}
      <Button title="生成脚本草稿" onPress={() => void generate(topic)} loading={busy === topic.id} disabled={!canEdit || !topic.eligible || Boolean(busy) || productionBusy} />
    </Card>)}
    {!canEdit ? <Text style={s.hint}>当前角色可查看内容，由内容负责人提交生成与修改。</Text> : null}
    <Text style={s.section}>内容与历史版本</Text>
    {loading && !contents.length ? <ActivityIndicator accessibilityLabel="正在加载内容" /> : !contents.length && !error && !loadError ? <Empty title="还没有脚本" description="选择资料完整的选题生成第一条草稿。" /> : contents.map(content => <Card key={content.id}>
      <Row><Text style={s.title}>{content.cover_title || content.topic}</Text><Badge label={`v${content.version}`} tone="blue" /></Row>
      <Text style={s.body} numberOfLines={3}>{content.script}</Text>
      <Row><Badge label={content.release_id ? '审核有效' : content.render_asset_id ? '待审核成片' : '脚本草稿'} tone={content.release_id ? 'green' : 'amber'} /><Text style={s.hint}>{content.channel} · {formatDate(content.created_at)}</Text></Row>
      <Button title="打开内容版本" tone="secondary" onPress={() => navigate('content', { id: content.id })} />
    </Card>)}
    <View style={{ height: 8 }} />
  </Page>;
}
const s = StyleSheet.create({ title: { fontSize: 17, fontWeight: '600', color: colors.navy, flexShrink: 1 }, body: { fontSize: 14, lineHeight: 23, color: colors.text, marginVertical: 8 }, hint: { fontSize: 12, lineHeight: 20, color: colors.muted, marginVertical: 5 }, warning: { fontSize: 13, lineHeight: 21, color: colors.amber, marginBottom: 12 }, section: { fontSize: 19, fontWeight: '600', color: colors.navy, marginVertical: 12 } });
