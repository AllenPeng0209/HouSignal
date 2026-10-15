import { useState } from 'react';
import { ActivityIndicator, Text, View } from 'react-native';
import { useApp } from '../state/AppContext';
import type { Overview, Proposal, Report } from '../api/types';
import { Badge, Button, Card, Empty, Heading, Notice, Page, Row, Select, Sheet, Toggle, formatDate, styles, colors } from '../components/ui';
import { metricValue, reportComplete } from './crm-domain';
import { useCrmMutation, useCrmResource } from './crm-hooks';

const titleStyle = styles.title;
const bodyStyle = styles.muted;
export function ReportsScreen() {
  const { project } = useApp();
  return <ReportsContent key={project?.id || 'no-project'} />;
}
function ReportsContent() {
  const { project, api, session, navigate } = useApp();
  const [window, setWindow] = useState('D7');
  const [proposal, setProposal] = useState<Proposal | null>(null);
  const [confirmed, setConfirmed] = useState(false);
  const base = project ? `/api/projects/${project.id}` : null;
  const report = useCrmResource<Report>(base ? `${base}/reports?window=${window}` : null);
  const overview = useCrmResource<Overview>(base ? `${base}/overview` : null);
  const mutation = useCrmMutation();
  const canApprove = ['owner', 'admin', 'content', 'reviewer'].includes(session.role);
  if (!project) return <Page><Empty title="先选择项目" action={<Button title="选择项目" onPress={() => navigate('projects')} />} /></Page>;
  const data = report.data;
  return <Page refreshing={report.loading || overview.loading} onRefresh={() => { report.retry(); overview.retry(); }}>
    <Heading title="效果复盘" subtitle={`${project.name} · 真实发布与客户进展`} />
    <Select value={window} options={[{ value: 'D7', label: '7 天观察窗口' }, { value: 'D30', label: '30 天观察窗口' }]} onChange={setWindow} />
    {report.loading ? <ActivityIndicator accessibilityLabel="加载复盘数据" /> : report.error ? <Notice message={report.error} onRetry={report.retry} /> : data ? <>
      <View style={{ flexDirection: 'row', flexWrap: 'wrap', gap: 10 }}>{([['播放量', data.metrics.views], ['点赞量', data.metrics.likes], ['评论量', data.metrics.comments], ['收藏量', data.metrics.saves], ['内容咨询', data.metrics.inquiries], ['资格客户', data.metrics.qualified]] as [string, number | null][]).map(([label, value]) => <View key={label} style={{ width: '48%', minWidth: 130 }}><Card><Text style={bodyStyle}>{label}</Text><Text style={{ fontSize: value == null ? 20 : 28, color: colors.navy, fontWeight: '700' }}>{metricValue(value)}</Text><Text style={{ ...bodyStyle, fontSize: 12 }}>{value == null ? '尚未获取完整指标' : `${window} 已回填指标`}</Text></Card></View>)}</View>
      <Card><Badge label={reportComplete(data.metrics) ? '观察窗口已完整回填' : '观察窗口尚未完整回填'} tone={reportComplete(data.metrics) ? 'green' : 'amber'} /><Text style={bodyStyle}>{data.metrics.observed_publications} / {data.metrics.total_publications} 条发布已有 {window} 窗口记录。未获取的指标保留待观察，实际为零时显示 0。</Text></Card>
      <Card>
        <Text style={titleStyle}>项目客户进展</Text><Badge label="实际销售记录" />
        {overview.loading ? <ActivityIndicator /> : overview.error ? <Notice message={overview.error} onRetry={overview.retry} /> : overview.data ? <>{([['真实咨询', overview.data.metrics.inquiries], ['资格确认', overview.data.metrics.qualified], ['已预约', overview.data.metrics.appointments], ['已到访', overview.data.metrics.visits]] as [string, number][]).map(([label, value]) => <Row key={label}><Text style={bodyStyle}>{label}</Text><Text style={titleStyle}>{metricValue(value)}</Text></Row>)}<Text style={bodyStyle}>咨询来源未知占比：{overview.data.metrics.unknown_rate == null ? '暂无分母' : `${overview.data.metrics.unknown_rate}%`}</Text></> : null}
      </Card>
      <Heading title="改进建议" subtitle="采纳建议保留记录，事实与发布版本分别审核" />
      {data.proposals.length ? data.proposals.map(item => <Card key={item.id}><Text style={titleStyle}>{item.title}</Text><Text style={bodyStyle}>{item.reason}</Text>{item.status === 'approved' ? <Badge label="已采纳" tone="green" /> : canApprove ? <Button title="查看并采纳" tone="secondary" onPress={() => { setProposal(item); setConfirmed(false); }} /> : <Badge label="待负责人采纳" />}</Card>) : <Empty title="积累结果，再形成建议" description="真实内容指标与销售反馈将为优化提供依据。" />}
      <Heading title="已核实发布" subtitle="只统计已核验回执且平台状态为在线的发布" action={<Button title="发布与回填" small tone="secondary" onPress={() => navigate('publications')} />} />
      {data.publications.length ? data.publications.map(item => { const metrics = item.metrics[window]; return <Card key={item.id}><Text style={titleStyle}>{item.title}</Text><Row><Badge label={item.channel} /><Badge label="已核实在线" tone="green" /></Row><Text style={bodyStyle}>{formatDate(item.created_at)}</Text><Text style={bodyStyle}>{window} 播放量：{metricValue(metrics?.views)}</Text><Text style={bodyStyle}>{window} 内容咨询：{metricValue(metrics?.inquiries)}</Text></Card>; }) : <Empty title="尚无已核实在线发布" description="完成真实发布回执核验后，才能在这里观察效果。" />}
    </> : null}
    <Sheet visible={!!proposal} title="采纳复盘建议" onClose={() => { if (!mutation.busy) setProposal(null); }}>
      <Text style={titleStyle}>{proposal?.title}</Text><Text style={bodyStyle}>{proposal?.reason}</Text>
      <Toggle label="已审阅建议，确认采纳" value={confirmed} onChange={setConfirmed} description="采纳会保留记录；事实与发布版本仍需单独审核。" />
      {!!(mutation.error) && <Notice message={mutation.error} />}
      <Button title="确认采纳" disabled={!confirmed || !canApprove} loading={mutation.busy} onPress={() => { if (proposal) void mutation.run(async () => { await api(`${base}/proposals/${proposal.id}/approve`, { method: 'POST', body: { confirmed: true } }); setProposal(null); report.retry(); }, '建议已采纳'); }} />
    </Sheet>
  </Page>;
}
