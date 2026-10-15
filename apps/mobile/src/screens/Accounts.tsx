import { useState } from 'react';
import { ActivityIndicator, Linking, StyleSheet, Text, View } from 'react-native';
import { useApp } from '../state/AppContext';
import { Badge, Button, Card, Empty, Field, Heading, Notice, Page, Row, Select, Sheet, formatDate } from '../components/ui';
import type { Topic } from '../api/types';
import { type Account, type AccountForm, type Opportunity, type OpportunityForm, type Plan, type PlanForm, defaultPlatform, kindLabels, planLabels, platforms, publicUrl, topicLabels, validateAccount, validateOpportunity, validatePlan } from '../domain/operations-input';
import { useOperationsMutation, useOperationsResource } from '../domain/operations-resource';
type PlatformReference = { account_authorization: boolean; scope: string; note: string; platforms: { platform: string; name: string; supported: boolean; loggedIn: boolean; note: string }[] };
type Trends = { trends: { platform: string; label: string; items: { title: string; url: string; heat: string | number }[] }[]; fetched_at: string; note: string };
const options = (labels: Record<string, string>) => Object.entries(labels).map(([value, label]) => ({ value, label }));
const platformOptions = platforms.map(value => ({ value, label: value }));
function SourceLink({ url, label = '打开原始来源' }: { url: string; label?: string }) {
  const { notify } = useApp();
  const href = publicUrl(url);
  return href ? <Button title={label} tone="ghost" onPress={() => { void Linking.openURL(href).catch(() => notify('当前设备无法打开链接，请核对地址')); }} /> : <Text style={s.muted}>原始来源未提供可打开链接</Text>;
}
function ResourceState({ loading, error, onRetry }: { loading: boolean; error: string; onRetry: () => void }) {
  return loading ? <ActivityIndicator accessibilityLabel="正在读取" style={s.loading} /> : error ? <Notice message={error} onRetry={onRetry} /> : null;
}
export function AccountsScreen() {
  const { project } = useApp();
  return <AccountsWorkspace key={project?.id || 'none'} />;
}
function AccountsWorkspace() {
  const { project, canEdit, navigate, api, trackJob } = useApp();
  const base = project ? `/api/projects/${project.id}/operations` : null;
  const accounts = useOperationsResource<Account[]>(base ? `${base}/accounts` : null);
  const opportunities = useOperationsResource<Opportunity[]>(base ? `${base}/opportunities` : null);
  const plans = useOperationsResource<Plan[]>(base ? `${base}/plans` : null);
  const topics = useOperationsResource<Topic[]>(project ? `/api/projects/${project.id}/topics` : null);
  const reference = useOperationsResource<PlatformReference>(base ? `${base}/platform-status` : null);
  const trends = useOperationsResource<Trends>(base ? `${base}/trends` : null);
  const [tab, setTab] = useState('accounts');
  const [query, setQuery] = useState('');
  const [filter, setFilter] = useState('all');
  const [accountForm, setAccountForm] = useState<{ value: Account | null } | null>(null);
  const [opportunityForm, setOpportunityForm] = useState<Partial<OpportunityForm> | null>(null);
  const [planForm, setPlanForm] = useState<{ value: Plan | null; source?: Opportunity } | null>(null);
  const mutation = useOperationsMutation();
  function refresh() { accounts.retry(); opportunities.retry(); plans.retry(); topics.retry(); reference.retry(); trends.retry(); }
  function chooseTab(value: string) { setTab(value); setQuery(''); setFilter('all'); }
  async function generate(plan: Plan) {
    if (!project || !canEdit) return;
    const topic = topics.data?.find(value => value.id === plan.topic_id);
    if (!topic?.eligible) { mutation.setError(topic?.missing?.join('、') || '先补齐并核验项目资料，再生成脚本'); return; }
    const result = await mutation.run(() => api<{ job_id: string }>(`/api/projects/${project.id}/contents/generate`, { method: 'POST', body: { topic_id: plan.topic_id, channel: plan.channel, plan_id: plan.id } }), '脚本任务已提交');
    if (result?.job_id) { trackJob(result.job_id); navigate('jobs', { id: result.job_id }); }
  }
  const needle = query.trim().toLocaleLowerCase();
  const accountRows = (accounts.data || []).filter(value => (filter === 'all' || value.platform === filter) && `${value.name} ${value.positioning} ${value.audience}`.toLocaleLowerCase().includes(needle));
  const opportunityRows = (opportunities.data || []).filter(value => (filter === 'all' || value.kind === filter) && `${value.title} ${value.summary}`.toLocaleLowerCase().includes(needle));
  const planRows = (plans.data || []).filter(value => (filter === 'all' || value.status === filter) && `${value.title} ${value.angle} ${value.brief}`.toLocaleLowerCase().includes(needle)).sort((a, b) => (a.scheduled_for || '9999').localeCompare(b.scheduled_for || '9999'));
  if (!project) return <Page><Empty title="先选择项目" description="账号、发现与计划分别保存在对应项目中。" action={<Button title="选择项目" onPress={() => navigate('projects')} />} /></Page>;
  return <Page onRefresh={refresh} refreshing={accounts.loading || plans.loading} testID="accounts-screen">
    <Heading title="内容运营" subtitle={project.name} action={canEdit ? <Button title={tab === 'accounts' ? '新增账号' : tab === 'discover' ? '登记发现' : '新建计划'} onPress={() => { if (tab === 'accounts') setAccountForm({ value: null }); else if (tab === 'discover') setOpportunityForm({}); else setPlanForm({ value: null }); }} /> : undefined} />
    <Select label="运营工作台" value={tab} onChange={chooseTab} options={[{ value: 'accounts', label: '账号画像' }, { value: 'discover', label: '内容发现' }, { value: 'plans', label: '内容计划' }]} />
    {!!(!canEdit) && <Notice message="当前角色可查看；账号、发现和计划修改需要内容编辑或管理员权限。" />}
    <Field label="搜索" value={query} onChangeText={setQuery} placeholder="搜索账号、标题或简报" />
    <Select label="筛选" value={filter} onChange={setFilter} options={[{ value: 'all', label: '全部' }, ...(tab === 'accounts' ? platformOptions : options(tab === 'discover' ? kindLabels : planLabels))]} />
    {!!(mutation.error) && <Notice message={mutation.error} />}
    {!!(tab === 'accounts') && <>
      <Card><Text style={s.title}>运营账号资料</Text><Text style={s.body}>登记团队的账号定位、受众和表达边界。平台登录和账号授权在对应平台确认。</Text></Card>
      <ResourceState loading={accounts.loading} error={accounts.error} onRetry={accounts.retry} />
      {!!(!accounts.loading && !accounts.error && !accountRows.length) && <Empty title="暂无匹配账号" description="登记公开主页和账号画像，为内容任务提供上下文。" />}
      {accountRows.map(account => <Card key={account.id}>
        <Row><Badge label={account.platform} tone="blue" /><Badge label={account.status === 'managed' ? '运营中' : '已暂停'} tone={account.status === 'managed' ? 'green' : 'muted'} /></Row>
        <Text style={s.title}>{account.name}</Text><SourceLink url={account.homepage} label="打开公开主页" />
        {([['定位', account.positioning], ['目标受众', account.audience], ['声音与风格', account.voice], ['表达边界', account.red_lines]]).map(([label, value]) => <View key={label}><Text style={s.label}>{label}</Text><Text selectable style={s.body}>{value || '待完善'}</Text></View>)}
        {!!(canEdit) && <Button title="编辑账号画像" tone="secondary" onPress={() => setAccountForm({ value: account })} />}
      </Card>)}
      <Card><Text style={s.title}>服务端平台会话参考</Text><Text style={s.body}>这是服务端 Easel 的会话查询结果，供团队核对平台状态。项目账号的授权需单独确认。</Text><ResourceState loading={reference.loading} error={reference.error} onRetry={reference.retry} />
        {reference.data?.platforms.map(value => <View key={value.platform} style={s.reference}><Row><Text style={s.label}>{value.name}</Text><Badge label={!value.supported ? '查询暂不支持' : value.loggedIn ? '服务端会话可用' : '无可用会话'} tone={value.supported && value.loggedIn ? 'blue' : 'muted'} /></Row>{!!(!!value.note) && <Text style={s.muted}>{value.note}</Text>}</View>)}
        <Button title="刷新会话参考" tone="ghost" onPress={reference.retry} />
      </Card>
    </>}
    {!!(tab === 'discover') && <>
      <Card><Text style={s.title}>公开热榜</Text><Text style={s.body}>查看真实来源，判断是否与当前项目相关，再保存为发现。</Text><ResourceState loading={trends.loading} error={trends.error} onRetry={trends.retry} />
        {!!(!!trends.data?.fetched_at) && <Text style={s.muted}>本次获取：{formatDate(trends.data.fetched_at)} · 原始榜单观测时间未知</Text>}
        {trends.data?.trends.map(group => <View key={group.platform}><Text style={s.label}>{group.label}</Text>{group.items.slice(0, 5).map((item, index) => <View key={`${item.url}-${index}`} style={s.trend}><Text style={s.body}>{index + 1}. {item.title}</Text><SourceLink url={item.url} />{!!(canEdit) && <Button title="保存为内容发现" tone="secondary" disabled={!publicUrl(item.url)} onPress={() => setOpportunityForm({ title: item.title, source_url: item.url, platform: group.platform === 'douyin' ? '抖音' : group.platform === 'zhihu' ? '知乎' : defaultPlatform(project.channel), summary: `来自${group.label}。请补充与当前项目的关联，核对来源内容与时效。` })} />}</View>)}</View>)}
        {!!(!trends.loading && !trends.error && !trends.data?.trends.some(group => group.items.length)) && <Text style={s.muted}>本次没有返回热榜，可登记团队发现。</Text>}
      </Card>
      <ResourceState loading={opportunities.loading} error={opportunities.error} onRetry={opportunities.retry} />
      {!!(!opportunities.loading && !opportunities.error && !opportunityRows.length) && <Empty title="暂无匹配发现" description="从真实文章、平台动态或公开讨论保存内容机会。" />}
      {opportunityRows.map(value => <Card key={value.id}><Row><Badge label={kindLabels[value.kind] || value.kind} tone="blue" /><Badge label={value.status === 'planned' ? '已入计划' : '待策划'} tone={value.status === 'planned' ? 'green' : 'muted'} /></Row><Text style={s.title}>{value.title}</Text><Text style={s.body}>{value.summary || '摘要待完善'}</Text><Text style={s.muted}>{value.platform} · 登记于 {formatDate(value.observed_at)}</Text><SourceLink url={value.source_url} />{!!(canEdit && value.status === 'saved') && <Button title="转为内容计划" onPress={() => setPlanForm({ value: null, source: value })} />}</Card>)}
    </>}
    {!!(tab === 'plans') && <>
      <Text style={s.muted}>按计划日期排列。团队维护完成状态，实际发布需平台链接与核验回执。</Text>
      <ResourceState loading={plans.loading} error={plans.error} onRetry={plans.retry} /><ResourceState loading={topics.loading} error={topics.error} onRetry={topics.retry} />
      {!!(!plans.loading && !plans.error && !planRows.length) && <Empty title="暂无匹配计划" description="写清选题、策划角度、资料来源和排期，再生成脚本。" />}
      {planRows.map(plan => {
        const source = plan.source || opportunities.data?.find(item => item.id === plan.opportunity_id);
        const topic = topics.data?.find(item => item.id === plan.topic_id);
        const account = accounts.data?.find(item => item.id === plan.account_id);
        return <Card key={plan.id}><Row><Badge label={planLabels[plan.status]} tone={plan.status === 'completed' ? 'green' : plan.status === 'scheduled' ? 'blue' : 'muted'} /><Text style={s.muted}>{plan.channel}</Text></Row><Text style={s.title}>{plan.title}</Text><Text selectable style={s.body}>{plan.angle || '策划角度待完善'}</Text>{!!(!!plan.brief) && <Text selectable style={s.body}>{plan.brief}</Text>}<Text style={s.muted}>{topic?.title || topicLabels[plan.topic_id] || plan.topic_id} · {plan.scheduled_for || '未排期'}</Text><Text style={s.muted}>账号：{account?.name || (plan.account_id ? '关联账号暂不可用' : '未指定')}</Text>{!!(source) && <><Text style={s.label}>发现来源：{source.title}</Text><SourceLink url={source.source_url} /></>}
          {!!(plan.content_version_id) && <Button title="打开已生成脚本" tone="secondary" onPress={() => navigate('content', { id: plan.content_version_id! })} />}
          {!!(canEdit) && <Row><Button title="编辑计划" tone="secondary" disabled={mutation.busy} onPress={() => setPlanForm({ value: plan })} /><Button title="生成脚本" loading={mutation.busy} disabled={!topic?.eligible || topics.loading} onPress={() => { void generate(plan); }} /></Row>}
          {!!(!topic?.eligible) && <Text style={s.muted}>{topic?.missing?.join('、') || '补齐并核验选题资料后可生成'}</Text>}
        </Card>;
      })}
    </>}
    {!!(accountForm) && <AccountEditor key={`account-${accountForm.value?.id || 'new'}-${project.id}`} value={accountForm.value} base={base!} channel={project.channel} onClose={() => setAccountForm(null)} onSaved={accounts.retry} />}
    {!!(opportunityForm) && <OpportunityEditor key={`discovery-${project.id}`} seed={opportunityForm} base={base!} channel={project.channel} onClose={() => setOpportunityForm(null)} onSaved={opportunities.retry} />}
    {!!(planForm) && <PlanEditor key={`plan-${planForm.value?.id || planForm.source?.id || 'new'}-${project.id}`} value={planForm.value} source={planForm.source} accounts={accounts.data || []} sources={opportunities.data || []} base={base!} channel={project.channel} onClose={() => setPlanForm(null)} onSaved={() => { plans.retry(); opportunities.retry(); chooseTab('plans'); }} />}
  </Page>;
}
function AccountEditor({ value, base, channel, onClose, onSaved }: { value: Account | null; base: string; channel: string; onClose: () => void; onSaved: () => void }) {
  const { api, canEdit } = useApp(); const mutation = useOperationsMutation();
  const [form, setForm] = useState<AccountForm>({ name: value?.name || '', platform: value?.platform || defaultPlatform(channel), homepage: value?.homepage || '', positioning: value?.positioning || '', audience: value?.audience || '', voice: value?.voice || '', red_lines: value?.red_lines || '', status: value?.status || 'managed' });
  function change(key: keyof AccountForm, text: string) { setForm(current => ({ ...current, [key]: text })); }
  async function save() { if (!canEdit) return; const result = await mutation.run(async () => api(`${base}/accounts${value ? `/${value.id}` : ''}`, { method: value ? 'PATCH' : 'POST', body: validateAccount(form) }), '账号画像已保存'); if (result) { onSaved(); onClose(); } }
  return <Sheet visible title={value ? '编辑账号画像' : '登记运营账号'} onClose={onClose}><Text style={s.body}>填写公开账号资料与表达方向。</Text><Field label="账号名称" value={form.name} onChangeText={text => change('name', text)} editable={!mutation.busy} /><Select label="平台" value={form.platform} options={platformOptions} onChange={text => change('platform', text)} /><Field label="公开主页" value={form.homepage} onChangeText={text => change('homepage', text)} placeholder="https://…" keyboardType="url" editable={!mutation.busy} />{([['positioning', '账号定位'], ['audience', '目标受众'], ['voice', '声音与风格'], ['red_lines', '表达边界']] as const).map(([key, label]) => <Field key={key} label={label} multiline value={form[key]} onChangeText={text => change(key, text)} editable={!mutation.busy} />)}<Select label="运营状态" value={form.status} options={[{ value: 'managed', label: '运营中' }, { value: 'paused', label: '已暂停' }]} onChange={text => change('status', text)} />{!!(mutation.error) && <Notice message={mutation.error} />}<Button title="保存账号" onPress={() => { void save(); }} loading={mutation.busy} disabled={!canEdit} /></Sheet>;
}
function OpportunityEditor({ seed, base, channel, onClose, onSaved }: { seed: Partial<OpportunityForm>; base: string; channel: string; onClose: () => void; onSaved: () => void }) {
  const { api, canEdit } = useApp(); const mutation = useOperationsMutation();
  const [form, setForm] = useState<OpportunityForm>({ title: seed.title || '', kind: 'trend', platform: seed.platform || defaultPlatform(channel), source_url: seed.source_url || '', summary: seed.summary || '' });
  function change(key: keyof OpportunityForm, text: string) { setForm(current => ({ ...current, [key]: text })); }
  async function save() { if (!canEdit) return; const result = await mutation.run(async () => api(`${base}/opportunities`, { method: 'POST', body: validateOpportunity(form) }), '内容发现已保存'); if (result) { onSaved(); onClose(); } }
  return <Sheet visible title="登记内容发现" onClose={onClose}><Field label="发现标题" value={form.title} onChangeText={text => change('title', text)} editable={!mutation.busy} /><Select label="类型" value={form.kind} options={options(kindLabels)} onChange={text => change('kind', text)} /><Select label="目标平台" value={form.platform} options={platformOptions} onChange={text => change('platform', text)} /><Field label="原始来源链接" value={form.source_url} onChangeText={text => change('source_url', text)} keyboardType="url" placeholder="https://…" editable={!mutation.busy} /><Field label="摘要与项目关联" multiline value={form.summary} onChangeText={text => change('summary', text)} editable={!mutation.busy} /><Text style={s.muted}>保存时记录登记时间；来源时效与内容由团队核对。</Text>{!!(mutation.error) && <Notice message={mutation.error} />}<Button title="保存发现" loading={mutation.busy} disabled={!canEdit} onPress={() => { void save(); }} /></Sheet>;
}
function PlanEditor({ value, source, accounts, sources, base, channel, onClose, onSaved }: { value: Plan | null; source?: Opportunity; accounts: Account[]; sources: Opportunity[]; base: string; channel: string; onClose: () => void; onSaved: () => void }) {
  const { api, canEdit } = useApp(); const mutation = useOperationsMutation();
  const [form, setForm] = useState<PlanForm>({ title: value?.title || source?.title || '', angle: value?.angle || source?.summary || '', channel: value?.channel || source?.platform || defaultPlatform(channel), scheduled_for: value?.scheduled_for || '', account_id: value?.account_id || '', opportunity_id: value?.opportunity_id || source?.id || '', topic_id: value?.topic_id || 'tour', status: value?.status || 'draft', brief: value?.brief || source?.summary || '' });
  function change(key: keyof PlanForm, text: string) { setForm(current => ({ ...current, [key]: text })); }
  async function save() { if (!canEdit) return; const result = await mutation.run(async () => api(`${base}/plans${value ? `/${value.id}` : ''}`, { method: value ? 'PATCH' : 'POST', body: validatePlan(form, accounts, value || undefined) }), '内容计划已保存'); if (result) { onSaved(); onClose(); } }
  return <Sheet visible title={value ? '编辑内容计划' : '新建内容计划'} onClose={onClose}><Field label="选题标题" value={form.title} onChangeText={text => change('title', text)} editable={!mutation.busy} /><Field label="策划角度与目标" multiline value={form.angle} onChangeText={text => change('angle', text)} editable={!mutation.busy} /><Select label="项目选题方向" value={form.topic_id} options={options(topicLabels)} onChange={text => change('topic_id', text)} /><Select label="关联账号" value={form.account_id} options={[{ value: '', label: '暂不指定账号' }, ...accounts.filter(item => item.status === 'managed' || item.id === value?.account_id).map(item => ({ value: item.id, label: `${item.name} · ${item.platform}${item.status === 'paused' ? ' · 已暂停' : ''}` }))]} onChange={text => { const account = accounts.find(item => item.id === text); setForm(current => ({ ...current, account_id: text, channel: account?.platform || current.channel })); }} />{form.account_id ? <Text style={s.label}>发布渠道：{form.channel}（与账号一致）</Text> : <Select label="发布渠道" value={form.channel} options={platformOptions} onChange={text => change('channel', text)} />}
    {value || source ? <Text style={s.muted}>发现来源：{value?.source?.title || source?.title || sources.find(item => item.id === form.opportunity_id)?.title || '独立策划'}。已保存计划保留原始来源。</Text> : <Select label="发现来源" value={form.opportunity_id} options={[{ value: '', label: '独立策划' }, ...sources.filter(item => item.status === 'saved').map(item => ({ value: item.id, label: item.title }))]} onChange={text => change('opportunity_id', text)} />}
    <Field label="创作简报" multiline value={form.brief} onChangeText={text => change('brief', text)} editable={!mutation.busy} /><Field label="计划日期" value={form.scheduled_for} onChangeText={text => change('scheduled_for', text)} placeholder="YYYY-MM-DD" editable={!mutation.busy} /><Select label="计划状态" value={form.status} options={options(planLabels)} onChange={text => change('status', text)} /><Text style={s.muted}>保存计划后，用已核验项目资料生成脚本。完成状态由团队维护。</Text>{!!(mutation.error) && <Notice message={mutation.error} />}<Button title="保存内容计划" loading={mutation.busy} disabled={!canEdit} onPress={() => { void save(); }} /></Sheet>;
}
const s = StyleSheet.create({ title: { fontSize: 18, fontWeight: '700', color: '#172D3E', marginBottom: 4 }, body: { fontSize: 15, lineHeight: 23, color: '#3A5364', marginVertical: 3 }, label: { fontSize: 14, fontWeight: '600', color: '#233D51', marginTop: 9, marginBottom: 3 }, muted: { fontSize: 13, lineHeight: 20, color: '#63798A', marginVertical: 4 }, loading: { padding: 18 }, reference: { paddingVertical: 8, borderBottomColor: '#E4EBEF', borderBottomWidth: 1 }, trend: { marginVertical: 8, paddingBottom: 10, borderBottomColor: '#E4EBEF', borderBottomWidth: 1 } });
