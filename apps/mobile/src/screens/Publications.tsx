import { useEffect, useState } from 'react';
import { useIsFocused } from 'expo-router';
import { ActivityIndicator, AppState, Linking, StyleSheet, Text } from 'react-native';
import { useApp } from '../state/AppContext';
import type { Content, Publication } from '../api/types';
import { ApiError } from '../api/client';
import { Badge, Button, Card, Empty, Field, Heading, Notice, Page, Row, Select, Sheet, Toggle, colors, formatDate } from '../components/ui';
import { isVerifiedLive, mergeMetricWindows, metricLabels, safePublicationUrl } from '../domain/content-workflow';
import { createForegroundPoller } from '../domain/jobs-poller';
import { useAlive } from '../domain/project-resource';
const platformOptions = [{ label: '审核中', value: 'pending' }, { label: '已发布可见', value: 'live' }, { label: '已下线', value: 'removed' }, { label: '审核未通过', value: 'rejected' }];
const platformLabels: Record<string, string> = Object.fromEntries(platformOptions.map(item => [item.value, item.label]));

export function PublicationsScreen() {
  const { api, project, refreshKey, canEdit, navigate, notify } = useApp();
  const [records, setRecords] = useState<Publication[]>([]), [contents, setContents] = useState<Content[]>([]);
  const [loading, setLoading] = useState(true), [error, setError] = useState(''), [refresh, setRefresh] = useState(0);
  const [entry, setEntry] = useState(false), [selected, setSelected] = useState<Publication | null>(null), [filter, setFilter] = useState('all');
  const base = project ? `/api/projects/${project.id}` : '';
  const focused = useIsFocused();
  useEffect(() => {
    if (!base || !focused) return;
    const poller = createForegroundPoller({ interval: 15000, read: async signal => {
      const [publications, versions] = await Promise.all([api<Publication[]>(`${base}/publications`, { signal }), api<Content[]>(`${base}/contents`, { signal })]);
      return { publications, versions };
    }, onData: value => { setRecords(value.publications); setContents(value.versions); setLoading(false); setError(''); }, onError: value => { if (value instanceof ApiError && [401, 403, 404].includes(value.status)) { setRecords([]); setContents([]); } setError(value instanceof Error ? value.message : '发布回执加载失败'); setLoading(false); } });
    if (!AppState.currentState || AppState.currentState === 'active') poller.resume();
    const listener = AppState.addEventListener('change', state => state === 'active' ? poller.resume() : poller.pause());
    return () => { listener.remove(); poller.dispose(); };
  }, [api, base, focused, refresh, refreshKey]);
  if (!project) return <Page><Heading title="发布与回执" /><Empty title="先选择一个项目" action={<Button title="选择项目" onPress={() => navigate('projects')} />} /></Page>;
  const approved = contents.filter(content => content.release_id);
  const visible = records.filter(record => filter === 'all' || (filter === 'public' ? isVerifiedLive(record) : record.receipt_status !== 'verified'));
  return <Page refreshing={loading} onRefresh={() => setRefresh(value => value + 1)} testID="publications-screen">
    <Heading title="发布与回执" subtitle={`${project.name} · 核对平台链接，记录真实表现`} action={canEdit ? <Button title="登记回执" small disabled={!approved.length || loading} onPress={() => setEntry(true)} /> : undefined} />
    <Card><Text style={s.body}>导出成片后，在目标平台完成发布，再登记真实链接。只有人工核实且平台当前可见的回执，才计入公开发布。</Text><Row><Badge label={`${records.filter(isVerifiedLive).length} 条已核实可见`} tone="green" /><Badge label={`${records.filter(record => record.receipt_status !== 'verified').length} 条待核实`} tone="amber" /></Row></Card>
    <Select label="查看范围" value={filter} onChange={setFilter} options={[{ label: '全部回执', value: 'all' }, { label: '已核实且可见', value: 'public' }, { label: '待核实', value: 'unverified' }]} />
    {error ? <Notice message={error} onRetry={() => setRefresh(value => value + 1)} /> : null}
    {loading && !records.length ? <ActivityIndicator accessibilityLabel="正在加载发布回执" /> : !visible.length && !error ? <Empty title="此范围内没有发布回执" description={approved.length ? '完成平台发布后可登记链接，再由审核负责人核实。' : '先完成内容成片与人工审核。'} action={<Button title="查看内容" tone="secondary" onPress={() => navigate('studio')} />} /> : visible.map(record => <Card key={record.id}>
      <Text style={s.title}>{record.title}</Text>
      <Row><Badge label={isVerifiedLive(record) ? '已核实公开发布' : record.receipt_status === 'verified' ? `已核实 · ${platformLabels[record.platform_state] || record.platform_state}` : '待核实回执'} tone={isVerifiedLive(record) ? 'green' : 'amber'} /><Text style={s.hint}>{record.channel}</Text></Row>
      <Text style={s.hint}>{formatDate(record.created_at)}</Text>
      <Text selectable style={s.url}>{record.url}</Text>
      <Text style={s.body}>D7 播放量：{record.metrics?.D7?.views ?? '未观察'}　咨询：{record.metrics?.D7?.inquiries ?? '未观察'}</Text>
      <Row><Button title="打开平台链接" tone="secondary" disabled={!safePublicationUrl(record.url)} onPress={() => void openPublication(record.url).catch(() => notify('无法打开平台链接，请确认已安装浏览器。'))} /><Button title="核对 / 回填" onPress={() => setSelected(record)} /></Row>
    </Card>)}
    {entry ? <PublicationEntrySheet available={approved} onClose={() => setEntry(false)} onSaved={() => { setEntry(false); setRefresh(value => value + 1); }} /> : null}
    {selected ? <PublicationVerificationSheet record={selected} onClose={() => setSelected(null)} onSaved={() => { setSelected(null); setRefresh(value => value + 1); }} /> : null}
  </Page>;
}

async function openPublication(value: string) {
  const url = safePublicationUrl(value);
  if (url) await Linking.openURL(url);
}

export function PublicationEntrySheet({ releaseId, available = [], onClose, onSaved }: { releaseId?: string; available?: Content[]; onClose: () => void; onSaved: () => void }) {
  const { api, project, canEdit, notify } = useApp();
  const [selected, setSelected] = useState(releaseId || available[0]?.release_id || ''), [url, setUrl] = useState(''), [state, setState] = useState('pending');
  const [busy, setBusy] = useState(false), [error, setError] = useState('');
  const alive = useAlive();
  async function save() {
    if (!project || !canEdit || busy) return;
    const safeUrl = safePublicationUrl(url);
    if (!safeUrl || !selected) { setError('选择有效审核版本，并填写不含账号密码的真实 http / https 平台链接。'); return; }
    setBusy(true); setError('');
    try {
      await api(`/api/projects/${project.id}/publications`, { method: 'POST', body: { release_id: selected, url: safeUrl, platform_state: state } });
      if (!alive.current) return;
      notify('发布链接已登记，等待人工核实回执。'); onSaved();
    } catch (value) { if (alive.current) setError(value instanceof Error ? value.message : '发布回执登记失败'); }
    finally { if (alive.current) setBusy(false); }
  }
  return <Sheet visible title="登记平台发布回执" onClose={() => !busy && onClose()}>
    {!releaseId ? <Select label="已审核内容版本" value={selected} onChange={setSelected} options={available.map(content => ({ label: `${content.cover_title || content.topic} · v${content.version}`, value: content.release_id! }))} /> : <Text style={s.hint}>回执关联当前内容的有效审核版本。</Text>}
    <Field label="真实平台链接" value={url} onChangeText={setUrl} keyboardType="url" placeholder="https://…" editable={canEdit && !busy} />
    <Select label="平台当前状态" value={state} onChange={setState} options={platformOptions} />
    <Text style={s.body}>登记链接会保存一条待核实回执。请核实平台实际状态后，再完成回执确认。</Text>
    {error ? <Notice message={error} /> : null}
    <Button title="保存待核实回执" onPress={() => void save()} loading={busy} disabled={!canEdit || !selected || !url.trim()} />
    <Button title="取消" tone="secondary" disabled={busy} onPress={onClose} />
  </Sheet>;
}

function PublicationVerificationSheet({ record, onClose, onSaved }: { record: Publication; onClose: () => void; onSaved: () => void }) {
  const { api, project, canReview, notify } = useApp();
  const [platform, setPlatform] = useState(record.platform_state), [window, setWindow] = useState('D7');
  const [windowValues, setWindowValues] = useState<Record<string, Record<string, string>>>(() => Object.fromEntries(['D7', 'D30'].map(window => [window, Object.fromEntries(Object.keys(metricLabels).map(key => [key, record.metrics?.[window]?.[key] == null ? '' : String(record.metrics[window]![key])]))])));
  const [touchedWindows, setTouchedWindows] = useState<string[]>([]), values = windowValues[window] || {};
  const [confirmed, setConfirmed] = useState(false), [busy, setBusy] = useState(false), [error, setError] = useState('');
  const alive = useAlive();
  async function save() {
    if (!project || !canReview || !confirmed || busy) return;
    let metrics: Publication['metrics'];
    try { metrics = mergeMetricWindows(record.metrics, windowValues, touchedWindows); } catch (value) { setError(value instanceof Error ? value.message : '指标格式不正确'); return; }
    setBusy(true); setError('');
    try {
      await api(`/api/projects/${project.id}/publications/${record.id}/verify`, { method: 'POST', body: { confirmed: true, platform_state: platform, metrics } });
      if (!alive.current) return;
      notify(platform === 'live' ? '回执已核实，平台当前可见。' : '回执已核实，当前平台状态已更新。'); onSaved();
    } catch (value) { if (alive.current) setError(value instanceof Error ? value.message : '回执核实失败'); }
    finally { if (alive.current) setBusy(false); }
  }
  return <Sheet visible title="核实发布回执" onClose={() => !busy && onClose()}>
    <Text style={s.title}>{record.title}</Text>
    <Button title="打开原平台链接" tone="secondary" disabled={!safePublicationUrl(record.url)} onPress={() => void openPublication(record.url).catch(() => notify('无法打开平台链接，请确认已安装浏览器。'))} />
    <Select label="实际平台状态" value={platform} onChange={value => { if (canReview && !busy) { setPlatform(value); setConfirmed(false); } }} options={platformOptions} />
    <Select label="指标观察窗口" value={window} onChange={setWindow} options={[{ label: '发布后 7 天 · D7', value: 'D7' }, { label: '发布后 30 天 · D30', value: 'D30' }]} />
    <Text style={s.hint}>未观察的指标留空；只有确认实际为零时填写 0。</Text>
    {Object.entries(metricLabels).map(([key, label]) => <Field key={key} label={label} value={values[key] || ''} onChangeText={value => { setWindowValues(current => ({ ...current, [window]: { ...current[window], [key]: value } })); setTouchedWindows(current => current.includes(window) ? current : [...current, window]); setConfirmed(false); }} keyboardType="number-pad" placeholder="未观察" editable={canReview && !busy} />)}
    <Toggle label="我已核对原链接、实际可见状态及本次填写的指标" value={confirmed} onChange={value => canReview && !busy && setConfirmed(value)} />
    {!canReview ? <Text style={s.hint}>当前角色可查看回执，由审核负责人或管理员核实。</Text> : null}
    {error ? <Notice message={error} /> : null}
    <Button title="确认回执并保存" onPress={() => void save()} loading={busy} disabled={!canReview || !confirmed} />
    <Button title="关闭" tone="secondary" disabled={busy} onPress={onClose} />
  </Sheet>;
}
const s = StyleSheet.create({ title: { fontSize: 17, fontWeight: '600', color: colors.navy, marginBottom: 10 }, body: { fontSize: 14, lineHeight: 23, color: colors.text, marginVertical: 10 }, hint: { fontSize: 12, lineHeight: 20, color: colors.muted, marginVertical: 6 }, url: { fontSize: 12, lineHeight: 20, color: colors.primary, marginVertical: 6 } });
