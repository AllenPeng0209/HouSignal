import { useRef, useState } from 'react';
import { ActivityIndicator, Text } from 'react-native';
import { useApp } from '../state/AppContext';
import type { Inquiry, Lead } from '../api/types';
import { Badge, Button, Card, Empty, Field, Heading, Notice, Page, Row, Select, Sheet, Toggle, formatDate, styles } from '../components/ui';
import { ContactPanel } from './crm-contact';
import { InquiryIntent, qualificationBody, salesMembers, sourceLabels, stageLabels, type Checks, type InquiryDraft, type Member } from './crm-domain';
import { useCrmMutation, useCrmResource } from './crm-hooks';

type InquiryRecord = Inquiry & { withdrawn?: boolean };
const titleStyle = styles.title;
const bodyStyle = styles.muted;

export function LeadsScreen() {
  const { project } = useApp();
  return <LeadsContent key={project?.id || 'no-project'} />;
}
function LeadsContent() {
  const { project, canSales, session, navigate } = useApp();
  const [tab, setTab] = useState('inquiries');
  const [search, setSearch] = useState('');
  const [create, setCreate] = useState(false);
  const [inquiry, setInquiry] = useState<InquiryRecord | null>(null);
  const base = project ? `/api/projects/${project.id}` : null;
  const inquiries = useCrmResource<InquiryRecord[]>(base ? `${base}/inquiries` : null);
  const leads = useCrmResource<Lead[]>(base ? `${base}/leads` : null);
  const refresh = () => { inquiries.retry(); leads.retry(); };
  if (!project) return <Page><Empty title="先选择项目" action={<Button title="选择项目" onPress={() => navigate('projects')} />} /></Page>;
  const resource = tab === 'inquiries' ? inquiries : leads;
  const rows = tab === 'inquiries' ? (inquiries.data || []).filter(i => !i.qualified && !i.withdrawn) : leads.data || [];
  const filtered = rows.filter(row => `${row.name} ${row.need} ${'next_action' in row ? row.next_action : ''}`.toLowerCase().includes(search.trim().toLowerCase()));
  return <Page refreshing={resource.loading} onRefresh={refresh}>
    <Heading title="咨询与客户" subtitle={`${project.name} · 记录真实需求与下一步`} action={canSales ? <Button title="录入咨询" small onPress={() => setCreate(true)} /> : undefined} />
    {!!(session.role === 'sales') && <Notice message="这里只显示分配给你的咨询与客户。" />}
    <Select value={tab} options={[{ label: '待资格确认', value: 'inquiries' }, { label: '资格客户', value: 'leads' }]} onChange={setTab} />
    <Field label="搜索客户" value={search} onChangeText={setSearch} placeholder="客户称呼、需求或下一步" />
    {resource.loading ? <ActivityIndicator accessibilityLabel="加载客户" /> : resource.error ? <Notice message={resource.error} onRetry={resource.retry} /> : !filtered.length ? <Empty title={search ? '没有匹配的客户' : tab === 'inquiries' ? '暂无待承接咨询' : '暂无资格客户'} description="以真实咨询、联系授权和人工资格确认建立客户记录。" action={canSales && !search ? <Button title="录入真实咨询" onPress={() => setCreate(true)} /> : undefined} /> : filtered.map(row => <Card key={row.id}>
      <Row><Text style={titleStyle}>{row.name}</Text><Badge label={'stage' in row ? stageLabels[row.stage] || row.stage : (row as Inquiry).channel} tone="blue" /></Row>
      <Text style={bodyStyle}>{row.need || '需求待确认'}</Text>
      <Badge label={sourceLabels[row.source_confidence] || row.source_confidence} tone={row.source_confidence === 'confirmed' ? 'green' : 'muted'} />
      <Text style={bodyStyle}>{'next_action' in row ? `下一步：${row.next_action || '待安排'}` : `咨询时间：${formatDate(row.created_at)}`}</Text>
      <Button title={'stage' in row ? '查看客户详情' : canSales ? '确认客户资格' : '查看咨询'} tone="secondary" onPress={() => 'stage' in row ? navigate('lead', { id: row.id }) : setInquiry(row as InquiryRecord)} />
    </Card>)}
    <NewInquirySheet key={project.id} visible={create} onClose={() => setCreate(false)} onSaved={refresh} />
    {!!(inquiry) && <InquirySheet inquiry={inquiry} leads={leads.data || []} onClose={() => setInquiry(null)} onSaved={refresh} />}
  </Page>;
}

function NewInquirySheet({ visible, onClose, onSaved }: { visible: boolean; onClose: () => void; onSaved: () => void }) {
  const { project, api } = useApp();
  const empty = (): InquiryDraft => ({ name: '', need: '', channel: project?.channel || '抖音', contact: '', source_confidence: 'unknown', source_text: '', contactable: false, consent: false });
  const [draft, setDraft] = useState<InquiryDraft>(empty);
  const intent = useRef(new InquiryIntent());
  const mutation = useCrmMutation();
  const set = (key: keyof InquiryDraft, value: string | boolean) => setDraft(s => ({ ...s, [key]: value }));
  async function save() {
    if (!project) return;
    const saved = await mutation.run(() => api(`/api/projects/${project.id}/inquiries`, { method: 'POST', body: intent.current.prepare(draft) }), '咨询已保存');
    if (saved) { intent.current.complete(); setDraft(empty()); onSaved(); onClose(); }
  }
  return <Sheet visible={visible} title="录入真实咨询" onClose={() => { if (!mutation.busy) onClose(); }}>
    <Field label="客户称呼 *" value={draft.name} onChangeText={value => set('name', value)} />
    <Select label="咨询渠道" value={draft.channel} options={['抖音', '视频号', '小红书', '线下', '转介绍', '其他'].map(value => ({ label: value, value }))} onChange={value => set('channel', value)} />
    <Field label="实际需求" multiline value={draft.need} onChangeText={value => set('need', value)} placeholder="记录客户实际表达的需求" />
    <Field label="联系方式" value={draft.contact} onChangeText={value => set('contact', value)} placeholder="客户授权后填写" />
    <Toggle label="客户已同意就本项目联系与跟进" value={draft.consent} onChange={value => set('consent', value)} />
    <Toggle label="已确认联系方式真实可用" value={draft.contactable} onChange={value => set('contactable', value)} />
    <Select label="来源可信度" value={draft.source_confidence} options={Object.entries(sourceLabels).map(([value, label]) => ({ value, label }))} onChange={value => set('source_confidence', value)} />
    <Field label="来源依据" multiline value={draft.source_text} onChangeText={value => set('source_text', value)} placeholder="已核实来源须附真实依据" />
    {!!(mutation.error) && <Notice message={`${mutation.error}。资料未修改时可直接重试，避免重复录入。`} />}
    <Button title="保存咨询" onPress={() => void save()} loading={mutation.busy} disabled={!draft.name.trim()} />
  </Sheet>;
}

function InquirySheet({ inquiry, leads, onClose, onSaved }: { inquiry: InquiryRecord; leads: Lead[]; onClose: () => void; onSaved: () => void }) {
  const { project, api, canSales, canManage, navigate } = useApp();
  const [checks, setChecks] = useState<Checks>({ contactable: false, consent: false, demand_confirmed: false, fit_confirmed: false });
  const [need, setNeed] = useState(inquiry.need);
  const [next, setNext] = useState('确认沟通时间');
  const [existing, setExisting] = useState('');
  const [same, setSame] = useState(false);
  const [owner, setOwner] = useState(inquiry.owner_id || '');
  const [withdraw, setWithdraw] = useState(false);
  const members = useCrmResource<Member[]>(canManage ? '/api/organization/members' : null);
  const mutation = useCrmMutation();
  if (!project) return null;
  const base = `/api/projects/${project.id}/inquiries/${inquiry.id}`;
  async function qualify() {
    const result = await mutation.run(() => api<Lead>(`${base}/qualify`, { method: 'POST', body: qualificationBody(checks, need, next, existing, same) }), '客户资格已确认');
    if (result) { onSaved(); onClose(); navigate('lead', { id: result.data.id }); }
  }
  return <Sheet visible title={`${inquiry.name} · 咨询资格`} onClose={() => { if (!mutation.busy) onClose(); }}>
    <Row><Badge label={inquiry.channel} /><Badge label={sourceLabels[inquiry.source_confidence] || inquiry.source_confidence} /></Row>
    <Text style={bodyStyle}>{formatDate(inquiry.created_at)}</Text>
    {!!(!!inquiry.source_text) && <Text style={bodyStyle}>来源依据：{inquiry.source_text}</Text>}
    <ContactPanel inquiryId={inquiry.id} eligible={inquiry.consent && inquiry.contactable && !inquiry.withdrawn} />
    {!!(canManage) && <Card>
      <Text style={titleStyle}>销售承接人</Text>
      {members.loading ? <ActivityIndicator /> : members.error ? <Notice message={members.error} onRetry={members.retry} /> : <><Select value={owner} options={[{ value: '', label: '选择承接人' }, ...salesMembers(members.data || [], project.id).map(m => ({ value: m.user_id, label: m.name }))]} onChange={setOwner} /><Button title="保存分配" disabled={!owner || owner === inquiry.owner_id} loading={mutation.busy} onPress={() => { void mutation.run(async () => { await api(`${base}/assign`, { method: 'POST', body: { owner_id: owner } }); onSaved(); onClose(); }, '承接人已更新'); }} /></>}
    </Card>}
    {!!(canSales) && <>
      <Field label="已确认的真实需求" multiline value={need} onChangeText={setNeed} />
      {([['contactable', '可联系：已确认联系方式可用'], ['consent', '已授权：客户同意本项目联系'], ['demand_confirmed', '真实需求：已与客户确认购房诉求'], ['fit_confirmed', '项目匹配：需求与当前项目匹配']] as [keyof Checks, string][]).map(([key, label]) => <Toggle key={key} label={label} value={checks[key]} onChange={value => setChecks(s => ({ ...s, [key]: value }))} />)}
      <Select label="关联已有客户" value={existing} options={[{ value: '', label: '建立新客户' }, ...leads.map(l => ({ value: l.id, label: `${l.name} · ${stageLabels[l.stage] || l.stage}` }))]} onChange={value => { setExisting(value); setSame(false); }} />
      {!!(!!existing) && <Toggle label="已人工确认是同一位客户" value={same} onChange={setSame} description="历史咨询会保留；同名或相同号码不能替代人工确认。" />}
      <Field label="下一步" value={next} onChangeText={setNext} />
      <Button title="确认成为资格客户" disabled={!Object.values(checks).every(Boolean) || (!!existing && !same) || inquiry.qualified} loading={mutation.busy} onPress={() => void qualify()} />
      <Card><Text style={titleStyle}>撤回联系授权</Text><Toggle label="客户已明确撤回联系授权" value={withdraw} onChange={setWithdraw} description="确认后服务端移除联系方式并停止相关客户跟进。" /><Button title="记录撤回授权" tone="danger" disabled={!withdraw} loading={mutation.busy} onPress={() => { void mutation.run(async () => { await api(`${base}/withdraw`, { method: 'POST', body: { confirmed: true } }); onSaved(); onClose(); }, '联系授权已撤回'); }} /></Card>
    </>}
    {!!(mutation.error) && <Notice message={mutation.error} />}
  </Sheet>;
}
