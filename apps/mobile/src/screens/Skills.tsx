import { useEffect, useMemo, useState } from 'react';
import { ActivityIndicator, StyleSheet, Text, View } from 'react-native';
import { useApp } from '../state/AppContext';
import { Badge, Button, Card, Empty, Field, Heading, Notice, Page, Row, Select, Sheet, formatDate } from '../components/ui';
import type { Job } from '../api/types';
import { type Account, defaultPlatform, platforms, topicLabels } from '../domain/operations-input';
import { useOperationsMutation, useOperationsResource } from '../domain/operations-resource';
import { type Capability, canRunSkill, skillDraftPlan, skillLayers, skillResult, skillStatuses } from '../domain/skills-result';
const jobLabels: Record<string, string> = { queued: '排队中', running: '执行中', succeeded: '已完成', failed: '执行失败', cancelled: '已取消' };
const terminal = (job: Job) => ['succeeded', 'failed', 'cancelled'].includes(job.status);
function statusTone(value: string): 'green' | 'blue' | 'amber' | 'red' | 'muted' { return value === 'ready' || value === 'succeeded' ? 'green' : value === 'setup' || value === 'running' || value === 'queued' ? 'amber' : value === 'failed' ? 'red' : value === 'manual' ? 'blue' : 'muted'; }
type Submission = { skillTitle: string; input: string; accountId: string };
type DraftContext = { job: Job; text: string; submission: Submission };
export function SkillsScreen() {
  const { project } = useApp();
  return <SkillsWorkspace key={project?.id || 'none'} />;
}
function SkillsWorkspace() {
  const { project, canEdit, navigate, api, trackJob, shareText, shareFile } = useApp();
  const base = project ? `/api/projects/${project.id}/operations` : null;
  const skills = useOperationsResource<Capability[]>(base ? `${base}/skills` : null);
  const accounts = useOperationsResource<Account[]>(base ? `${base}/accounts` : null);
  const jobs = useOperationsResource<Job[]>(project ? `/api/projects/${project.id}/jobs` : null);
  const mutation = useOperationsMutation();
  const [tab, setTab] = useState('catalog');
  const [layer, setLayer] = useState('all');
  const [query, setQuery] = useState('');
  const [selected, setSelected] = useState<Capability | null>(null);
  const [input, setInput] = useState('');
  const [accountId, setAccountId] = useState('');
  const [selectedJobId, setSelectedJobId] = useState<string | null>(null);
  const [queuedJobs, setQueuedJobs] = useState<Job[]>([]);
  const [draft, setDraft] = useState<DraftContext | null>(null);
  const [submissions, setSubmissions] = useState<Record<string, Submission>>({});
  const history = useMemo(() => [...queuedJobs.filter(job => !jobs.data?.some(value => value.id === job.id)), ...(jobs.data || []).filter(job => job.kind === 'content_skill')].sort((a, b) => b.created_at.localeCompare(a.created_at)), [queuedJobs, jobs.data]);
  const active = history.some(job => !terminal(job));
  useEffect(() => { if (!active || jobs.loading) return; const timer = setTimeout(jobs.retry, 5000); return () => clearTimeout(timer); }, [active, jobs.loading, jobs.retry]);
  const selectedJob = history.find(job => job.id === selectedJobId);
  const selectedResult = selectedJob && project ? skillResult(selectedJob.result || {}, project.id, selectedJob.id) : null;
  const selectedTitle = selectedJob ? submissions[selectedJob.id]?.skillTitle || skills.data?.find(skill => skill.id === selectedJob.result?.skill_id)?.title || '内容技能任务' : '内容技能任务';
  function refresh() { skills.retry(); accounts.retry(); jobs.retry(); }
  function openSkill(skill: Capability) { setSelected(skill); setInput(''); setAccountId(''); mutation.setError(''); }
  async function runSkill() {
    if (!base || !selected || !canRunSkill(selected, canEdit, input)) return;
    const skill = selected; const request = input.trim(); const account = accountId;
    const result = await mutation.run(() => api<{ job_id: string; job: Job }>(`${base}/skills/${encodeURIComponent(skill.id)}/run`, { method: 'POST', body: { input: request, ...(account ? { account_id: account } : {}) } }), '内容技能任务已提交');
    if (!result) return;
    const jobId = result.job_id || result.job?.id;
    if (!jobId) { mutation.setError('服务未返回任务标识，请刷新技能状态后重试'); return; }
    setSubmissions(previous => ({ ...previous, [jobId]: { skillTitle: skill.title, input: request, accountId: account } }));
    if (result.job) setQueuedJobs(previous => [result.job, ...previous.filter(job => job.id !== jobId)]);
    trackJob(jobId); jobs.retry(); setSelected(null); setSelectedJobId(jobId); setTab('history');
  }
  async function cancelJob(job: Job) {
    const result = await mutation.run(() => api<Job>(`/api/jobs/${job.id}/cancel`, { method: 'POST' }), '任务已取消');
    if (result) { setQueuedJobs(previous => previous.map(value => value.id === result.id ? result : value)); jobs.retry(); }
  }
  const needle = query.trim().toLocaleLowerCase();
  const rows = (skills.data || []).filter(skill => (layer === 'all' || skill.layer === layer) && `${skill.title} ${skill.description} ${skillLayers[skill.layer] || ''}`.toLocaleLowerCase().includes(needle));
  if (!project) return <Page><Empty title="先选择项目" description="技能任务会使用当前项目资料与账号画像。" action={<Button title="选择项目" onPress={() => navigate('projects')} />} /></Page>;
  return <Page onRefresh={refresh} refreshing={skills.loading || jobs.loading} testID="skills-screen">
    <Heading title="技能库" subtitle={`${project.name} · 根据实际服务状态选择能力`} action={<Button title="刷新状态" tone="secondary" onPress={refresh} />} />
    <Select label="工作区" value={tab} options={[{ label: '内容能力', value: 'catalog' }, { label: '执行记录与结果', value: 'history' }]} onChange={setTab} />
    {!!(mutation.error) && <Notice message={mutation.error} />}
    {tab === 'catalog' ? <>
      <Card><Text style={s.title}>{skills.data?.length ?? '—'} 项内容能力</Text><Text style={s.body}>{skills.data?.filter(skill => skill.status === 'ready').length ?? '—'} 项可使用 · {skills.data?.filter(skill => skill.status === 'manual').length ?? '—'} 项需人工确认</Text><Text style={s.muted}>能力状态来自服务端配置与实际工具检查。生成结果进入待核对工作流。</Text></Card>
      <Field label="搜索技能" value={query} onChangeText={setQuery} placeholder="输入能力名称或用途" />
      <Select label="能力层" value={layer} onChange={setLayer} options={[{ value: 'all', label: '全部能力' }, ...Object.entries(skillLayers).map(([value, label]) => ({ value, label }))]} />
      {!!(skills.loading && !skills.data) && <ActivityIndicator accessibilityLabel="正在读取技能" style={s.loading} />}{!!(skills.error) && <Notice message={skills.error} onRetry={skills.retry} />}
      {!!(!skills.loading && !skills.error && !rows.length) && <Empty title="没有匹配的技能" description="尝试更换关键词或能力层。" action={<Button title="查看全部" onPress={() => { setQuery(''); setLayer('all'); }} />} />}
      {rows.map(skill => <Card key={skill.id}><Row><Badge label={skillLayers[skill.layer] || skill.layer} tone="blue" /><Badge label={skillStatuses[skill.status] || '暂不可用'} tone={statusTone(skill.status)} /></Row><Text style={s.title}>{skill.title}</Text><Text style={s.body}>{skill.description}</Text><Text style={s.muted}>{skill.reason || '状态以服务端实际配置为准'}</Text><Button title={skill.mode === 'native' ? '打开操作' : skill.mode === 'manual' ? '查看流程' : skill.status === 'ready' ? '使用技能' : '查看准备要求'} tone={skill.status === 'ready' ? 'primary' : 'secondary'} onPress={() => openSkill(skill)} /></Card>)}
    </> : <>
      <Text style={s.muted}>显示当前项目最近 100 个任务中的内容技能记录。执行中的任务会自动刷新。</Text>
      {!!(jobs.loading && !jobs.data) && <ActivityIndicator accessibilityLabel="正在读取执行记录" style={s.loading} />}{!!(jobs.error) && <Notice message={jobs.error} onRetry={jobs.retry} />}
      {!!(!jobs.loading && !jobs.error && !history.length) && <Empty title="暂无技能执行记录" description="选择一项可使用技能，写清目标和要求后提交。" action={<Button title="查看内容能力" onPress={() => setTab('catalog')} />} />}
      {history.map(job => <Card key={job.id}><Row><Badge label={jobLabels[job.status] || job.status} tone={statusTone(job.status)} /><Text style={s.muted}>{formatDate(job.created_at)}</Text></Row><Text style={s.title}>{submissions[job.id]?.skillTitle || skills.data?.find(skill => skill.id === job.result?.skill_id)?.title || '内容技能任务'}</Text>{!!(!terminal(job)) && <Text style={s.body}>{job.phase || jobLabels[job.status]} · {Math.round(Math.max(0, Math.min(1, job.progress || 0)) * 100)}%</Text>}{!!(job.error) && <Text style={s.error}>{job.error}</Text>}<Button title={job.status === 'succeeded' ? '查看回复与产物' : '查看任务'} tone="secondary" onPress={() => { setSelectedJobId(job.id); mutation.setError(''); }} /></Card>)}
    </>}
    {!!(selected) && <Sheet visible title={selected.title} onClose={() => setSelected(null)}><Row><Badge label={skillStatuses[selected.status] || '暂不可用'} tone={statusTone(selected.status)} /><Badge label={skillLayers[selected.layer] || selected.layer} tone="blue" /></Row><Text style={s.body}>{selected.description}</Text><Text style={s.muted}>{selected.reason}</Text>
      {selected.mode === 'native' ? <><Text style={s.body}>在项目工作流中使用此能力，结果保存在相应项目记录中。</Text><Button title="打开项目操作" onPress={() => { setSelected(null); navigate(selected.target); }} /></> : selected.mode === 'manual' ? <><Text style={s.body}>1. 核对账号、平台和表达方向。{ '\n' }2. 审核最终内容并导出发布包。{ '\n' }3. 人工登录目标平台发布，登记真实链接并核验回执。</Text><Row><Button title="查看账号" onPress={() => { setSelected(null); navigate('accounts'); }} /><Button title="打开内容工作台" tone="secondary" onPress={() => { setSelected(null); navigate('studio'); }} /></Row></> : selected.status !== 'ready' ? <><Text style={s.body}>完成所列服务与工具准备后，刷新状态即可使用。</Text><Row><Button title="查看设置" onPress={() => { setSelected(null); navigate('settings'); }} /><Button title="刷新技能状态" tone="secondary" onPress={() => { skills.retry(); setSelected(null); }} /></Row></> : <>
        <Field label="你的需求" multiline value={input} onChangeText={setInput} editable={canEdit && !mutation.busy} placeholder="写清目标、平台、受众、材料；生成媒体时补充画幅和时长。" /><Text style={input.trim().length > 6000 ? s.error : s.muted}>{input.trim().length}/6000 字</Text>
        <Select label="账号画像（可选）" value={accountId} options={[{ label: '使用当前项目资料', value: '' }, ...(accounts.data || []).filter(account => account.status === 'managed').map(account => ({ value: account.id, label: `${account.name} · ${account.platform}` }))]} onChange={setAccountId} />
        {!!(accounts.error) && <Notice message={`账号画像读取失败：${accounts.error}`} onRetry={accounts.retry} />}{!!(!canEdit) && <Notice message="需要内容编辑或管理员权限才能运行技能。" />}
        <Text style={s.muted}>结果需要人工核验。平台发布需完成最终审核、人工提交和真实回执登记。</Text>
        {!!(mutation.error) && <Notice message={mutation.error} />}<Button title="提交技能任务" loading={mutation.busy} disabled={!canRunSkill(selected, canEdit, input) || accounts.loading || (!!accountId && !accounts.data?.some(account => account.id === accountId))} onPress={() => { void runSkill(); }} />
      </>}
    </Sheet>}
    {!!(selectedJobId) && <Sheet visible title={selectedTitle} onClose={() => setSelectedJobId(null)}>
      {!selectedJob ? <><Text style={s.body}>任务已提交，正在读取服务端记录。</Text><Button title="打开任务中心" onPress={() => { const id = selectedJobId; setSelectedJobId(null); navigate('jobs', { id }); }} /></> : <>
        <Badge label={jobLabels[selectedJob.status] || selectedJob.status} tone={statusTone(selectedJob.status)} /><Text style={s.muted}>任务 {selectedJob.id} · {formatDate(selectedJob.created_at)}</Text>
        {!!(!terminal(selectedJob)) && <><ActivityIndicator accessibilityLabel="技能正在执行" style={s.loading} /><Text style={s.body}>{selectedJob.phase || jobLabels[selectedJob.status]} · {Math.round(Math.max(0, Math.min(1, selectedJob.progress || 0)) * 100)}%</Text><Button title="取消任务" tone="danger" loading={mutation.busy} onPress={() => { void cancelJob(selectedJob); }} /></>}
        {!!(selectedJob.error) && <Notice message={selectedJob.error} />}
        {!!(selectedJob.status === 'succeeded' && selectedResult) && <>
          <Badge label="待人工核验 · 尚未发布" tone="amber" /><Text selectable style={s.result}>{selectedResult.text || '服务未返回文字回复，请核对实际产物。'}</Text>
          {selectedResult.artifacts.map(artifact => <View key={artifact.url} style={s.artifact}><Text style={s.label}>{artifact.name}</Text><Button title="下载并分享产物" tone="secondary" loading={mutation.busy} onPress={() => { void mutation.run(() => shareFile(artifact.url, artifact.name, artifact.mimeType)); }} /></View>)}
          <Text style={s.muted}>请在项目资料中核对回复里的事实，并打开回复所列原始来源确认内容。核对完成前保持草稿状态。</Text><Button title="查看项目依据" tone="ghost" onPress={() => { setSelectedJobId(null); navigate('project', { id: project.id }); }} />
          {!!(!!selectedResult.text) && <Row><Button title="分享执行回复" tone="secondary" loading={mutation.busy} onPress={() => { void mutation.run(() => shareText(`楼响 · ${selectedTitle}\n任务 ${selectedJob.id}\n状态：待人工核验，尚未发布\n\n${selectedResult.text}`, `楼响_技能回复_${selectedJob.id}.txt`)); }} />{!!(canEdit) && <Button title="保存为内容计划" onPress={() => { setDraft({ job: selectedJob, text: selectedResult.text, submission: submissions[selectedJob.id] || { skillTitle: selectedTitle, input: '', accountId: '' } }); setSelectedJobId(null); }} />}</Row>}
        </>}
        {!!(mutation.error) && <Notice message={mutation.error} />}
        <Button title="打开任务中心" tone="ghost" onPress={() => { const id = selectedJob.id; setSelectedJobId(null); navigate('jobs', { id }); }} />
      </>}
    </Sheet>}
    {!!(draft) && <SkillPlanEditor value={draft} accounts={accounts.data || []} base={base!} channel={project.channel} onClose={() => setDraft(null)} />}
  </Page>;
}
function SkillPlanEditor({ value, accounts, base, channel, onClose }: { value: DraftContext; accounts: Account[]; base: string; channel: string; onClose: () => void }) {
  const { api, canEdit, navigate } = useApp(); const mutation = useOperationsMutation();
  const account = accounts.find(item => item.id === value.submission.accountId && item.status === 'managed');
  const [form, setForm] = useState({ title: '', angle: value.submission.input, brief: value.text, channel: account?.platform || defaultPlatform(channel), account_id: account?.id || '', topic_id: 'tour' });
  function change(key: keyof typeof form, text: string) { setForm(previous => ({ ...previous, [key]: text })); }
  async function save() {
    if (!canEdit) return;
    const result = await mutation.run(() => api(`${base}/plans`, { method: 'POST', body: skillDraftPlan(form, { id: value.job.id, skillTitle: value.submission.skillTitle, input: value.submission.input }) }), '已保存为待核验内容计划');
    if (result) { onClose(); navigate('accounts'); }
  }
  return <Sheet visible title="保存为内容计划" onClose={onClose}><Text style={s.body}>先确认选题与角度，再把执行回复保存为草稿。来源会保留技能名称和真实任务标识。</Text><Field label="计划标题" value={form.title} onChangeText={text => change('title', text)} editable={!mutation.busy} /><Field label="策划角度与目标" multiline value={form.angle} onChangeText={text => change('angle', text)} editable={!mutation.busy} /><Select label="项目选题方向" value={form.topic_id} onChange={text => change('topic_id', text)} options={Object.entries(topicLabels).map(([value, label]) => ({ value, label }))} /><Select label="账号画像" value={form.account_id} onChange={text => { const account = accounts.find(item => item.id === text); setForm(previous => ({ ...previous, account_id: text, channel: account?.platform || previous.channel })); }} options={[{ value: '', label: '暂不指定账号' }, ...accounts.filter(item => item.status === 'managed').map(item => ({ value: item.id, label: `${item.name} · ${item.platform}` }))]} />{form.account_id ? <Text style={s.label}>发布渠道：{form.channel}</Text> : <Select label="发布渠道" value={form.channel} onChange={text => change('channel', text)} options={platforms.map(value => ({ value, label: value }))} />}<Field label="创作简报（可精简）" multiline value={form.brief} onChangeText={text => change('brief', text)} editable={!mutation.busy} /><Text style={s.muted}>计划保存为选题草稿；任务回复仍需核对事实、来源与实际产物。</Text>{!!(mutation.error) && <Notice message={mutation.error} />}<Button title="保存草稿计划" loading={mutation.busy} disabled={!canEdit || !form.title.trim()} onPress={() => { void save(); }} /></Sheet>;
}
const s = StyleSheet.create({ title: { fontSize: 18, fontWeight: '700', color: '#172D3E', marginTop: 5, marginBottom: 4 }, body: { fontSize: 15, lineHeight: 23, color: '#3A5364', marginVertical: 5 }, muted: { fontSize: 13, lineHeight: 20, color: '#63798A', marginVertical: 5 }, label: { fontSize: 14, lineHeight: 22, fontWeight: '600', color: '#233D51', marginVertical: 5 }, error: { fontSize: 14, lineHeight: 21, color: '#A13832', marginVertical: 5 }, loading: { padding: 18 }, result: { fontSize: 15, lineHeight: 24, color: '#233D51', marginVertical: 14 }, artifact: { paddingVertical: 9, borderBottomColor: '#E4EBEF', borderBottomWidth: 1 } });
