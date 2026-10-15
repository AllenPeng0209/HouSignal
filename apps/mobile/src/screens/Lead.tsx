import { useState } from 'react';
import { ActivityIndicator, Text } from 'react-native';
import { useApp } from '../state/AppContext';
import type { Params } from '../lib/contracts';
import type { LeadDetail } from '../api/types';
import { Badge, Button, Card, Empty, Field, Heading, Notice, Page, Row, Sheet, Toggle, formatDate, styles } from '../components/ui';
import { ContactPanel } from './crm-contact';
import { sourceLabels, stageLabels, stages } from './crm-domain';
import { useCrmMutation, useCrmResource } from './crm-hooks';

const titleStyle = styles.title;
const bodyStyle = styles.muted;
export function LeadScreen({ params }: { params: Params }) {
  const { project } = useApp();
  return <LeadContent key={`${project?.id}:${params.id}`} params={params} />;
}
function LeadContent({ params }: { params: Params }) {
  const { api, project, canSales, back } = useApp();
  const base = project && params.id ? `/api/projects/${project.id}/leads/${params.id}` : null;
  const detail = useCrmResource<LeadDetail>(base);
  const [note, setNote] = useState('');
  const [next, setNext] = useState('');
  const [target, setTarget] = useState('');
  const [confirmed, setConfirmed] = useState(false);
  const mutation = useCrmMutation();
  if (!base) return <Page><Empty title="客户记录不可用" action={<Button title="返回" onPress={back} />} /></Page>;
  const data = detail.data;
  const nextStage = data && !['lost', 'won', 'withdrawn'].includes(data.lead.stage) ? stages[stages.indexOf(data.lead.stage) + 1] : undefined;
  const ongoing = !!data && !['lost', 'won', 'withdrawn'].includes(data.lead.stage);
  async function followup() {
    const result = await mutation.run(() => api(`${base}/followups`, { method: 'POST', body: { note: note.trim(), next_action: next.trim() } }), '跟进记录已保存');
    if (result) { setNote(''); setNext(''); detail.retry(); }
  }
  async function changeStage() {
    if (!target || !confirmed) return;
    const result = await mutation.run(() => api(`${base}/stage`, { method: 'POST', body: { stage: target, confirmed: true } }), target === 'withdrawn' ? '联系授权已撤回' : '客户阶段已更新');
    if (result) { setTarget(''); setConfirmed(false); if (target === 'withdrawn') back(); else detail.retry(); }
  }
  return <Page refreshing={detail.loading} onRefresh={detail.retry}>
    <Heading title={data?.lead.name || '客户详情'} subtitle="以实际沟通和销售记录更新客户进展" action={<Button title="返回" tone="ghost" small onPress={back} />} />
    {detail.loading ? <ActivityIndicator accessibilityLabel="加载客户详情" /> : detail.error ? <Notice message={detail.error} onRetry={detail.retry} /> : data ? <>
      <Card>
        <Row><Text style={titleStyle}>{data.lead.name}</Text><Badge label={stageLabels[data.lead.stage] || data.lead.stage} tone="blue" /></Row>
        <Text style={bodyStyle}>{data.lead.need || '需求待确认'}</Text>
        <Text style={bodyStyle}>承接人：{data.lead.owner_name || '按项目销售权限分配'}</Text>
        <Text style={bodyStyle}>来源：{sourceLabels[data.lead.source_confidence] || data.lead.source_confidence}</Text>
        <Text style={bodyStyle}>下一步：{data.lead.next_action || '待安排'}</Text>
        <ContactPanel inquiryId={data.inquiry.id} eligible={data.inquiry.consent && data.inquiry.contactable && data.lead.stage !== 'withdrawn'} />
      </Card>
      {!!(canSales) && <Card>
        <Text style={titleStyle}>客户阶段</Text>
        {!!(nextStage) && <Button title={`记录${stageLabels[nextStage]}`} onPress={() => { setTarget(nextStage); setConfirmed(false); }} />}
        {!!(ongoing) && <Button title="记录结案" tone="secondary" onPress={() => { setTarget('lost'); setConfirmed(false); }} />}
        <Button title="记录撤回联系授权" tone="danger" onPress={() => { setTarget('withdrawn'); setConfirmed(false); }} />
      </Card>}
      {!!(canSales) && <Card>
        <Text style={titleStyle}>新增跟进</Text>
        <Field label="实际沟通记录 *" multiline value={note} onChangeText={setNote} placeholder="记录真实沟通内容与客户反馈" />
        <Field label="下一步安排" multiline value={next} onChangeText={setNext} placeholder="本次保存会更新客户的下一步" />
        <Button title="保存跟进" disabled={!note.trim()} loading={mutation.busy} onPress={() => void followup()} />
        {!!(mutation.error) && <Notice message={mutation.error} />}
      </Card>}
      <Heading title="跟进记录" subtitle={`${data.followups.length} 条实际记录`} />
      {data.followups.length ? data.followups.map(item => <Card key={item.id}><Text style={bodyStyle}>{formatDate(item.created_at)}</Text><Text style={styles.text}>{item.note}</Text><Text style={bodyStyle}>下一步：{item.next_action || '未安排'}</Text></Card>) : <Empty title="尚未记录跟进" description="首次沟通后填写真实反馈与下一步安排。" />}
      {!!(!!data.lead.stage_history?.length) && <Card><Text style={titleStyle}>阶段记录</Text>{data.lead.stage_history.map((entry, index) => <Text key={`${entry.stage}-${index}`} style={bodyStyle}>{stageLabels[entry.stage] || entry.stage} · {formatDate(entry.at)}</Text>)}</Card>}
    </> : <Empty title="客户不存在或无权访问" />}
    <Sheet visible={!!target} title={target === 'withdrawn' ? '撤回客户联系授权' : `记录${stageLabels[target] || '客户阶段'}`} onClose={() => { if (!mutation.busy) setTarget(''); }}>
      <Text style={bodyStyle}>{target === 'withdrawn' ? '确认客户已明确撤回联系授权后，服务端将移除相关联系方式并停止跟进。' : `将 ${data?.lead.name || '客户'} 的阶段更新为“${stageLabels[target] || target}”。请依据实际发生的销售进展确认。`}</Text>
      <Toggle label={target === 'withdrawn' ? '客户已明确撤回联系授权' : '已核实这一销售进展真实发生'} value={confirmed} onChange={setConfirmed} />
      {!!(mutation.error) && <Notice message={mutation.error} />}
      <Button title="确认记录" tone={target === 'withdrawn' ? 'danger' : 'primary'} disabled={!confirmed} loading={mutation.busy} onPress={() => void changeStage()} />
    </Sheet>
  </Page>;
}
