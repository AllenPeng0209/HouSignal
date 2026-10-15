import { useState } from 'react';
import { useProjectResource } from '../domain/project-resource';
import { Pressable, Text, View } from 'react-native';
import Ionicons from '@expo/vector-icons/Ionicons';
import { useApp } from '../state/AppContext';
import type { Overview } from '../api/types';
import type { ScreenName } from '../lib/contracts';
import { Badge, Button, Card, Empty, Heading, Loading, Notice, Page, Row, colors, styles } from '../components/ui';

const shortcuts: { screen: ScreenName; label: string; icon: keyof typeof Ionicons.glyphMap; text: string }[] = [
  { screen: 'assets', label: '现场素材', icon: 'camera-outline', text: '拍摄、录音与上传' },
  { screen: 'project', label: '楼盘资料', icon: 'business-outline', text: '核验事实和来源' },
  { screen: 'jobs', label: '任务中心', icon: 'time-outline', text: '查看进度与结果' },
  { screen: 'reports', label: '效果复盘', icon: 'stats-chart-outline', text: '内容与客户转化' },
];
export function HomeScreen() {
  const app = useApp();
  const resource = useProjectResource<Overview>(app.project ? `/api/projects/${app.project.id}/overview` : null);
  const {data,error,loading} = resource;
  const [refreshing,setRefreshing] = useState(false), [refreshError,setRefreshError] = useState('');
  const refresh = async () => { setRefreshing(true); setRefreshError(''); try { await app.reload(); resource.retry(); } catch(e) {setRefreshError((e as Error).message);} finally {setRefreshing(false);} };
  if (!app.project) return <Page><Heading title={`你好，${app.session.user.name}`} subtitle="让每一份项目经验，连接到真正的客户。" /><Empty title="建立你的第一个项目" description="把资料、素材、内容与客户放在同一个工作区。" action={<Button title="选择或新建项目" onPress={() => app.navigate('projects')} />} /></Page>;
  return <Page testID="home-screen" refreshing={refreshing} onRefresh={() => void refresh()}><Heading title={`你好，${app.session.user.name}`} subtitle="今天，让项目再向前一步。" />
    <Card style={{ backgroundColor: colors.navy, borderColor: colors.navy }}><Row>{app.project.is_test ? <Badge label="示例项目" tone="amber" /> : <Badge label={app.project.city} />}</Row><Text style={{ fontSize: 22, fontWeight: '700', color: '#FFF' }}>{app.project.name}</Text><Text style={{ color: '#C5D1E6', fontSize: 14 }}>资料准备 {app.project.completeness.completed}/{app.project.completeness.total} · {app.project.completeness.percent}%</Text><View style={{ height: 6, backgroundColor: '#35465E', borderRadius: 4 }}><View style={{ height: 6, width: `${app.project.completeness.percent}%`, backgroundColor: '#85A1FF', borderRadius: 4 }} /></View><Button title="和楼管家一起推进项目" onPress={() => app.navigate('assistant')} /></Card>
    <View style={{ flexDirection: 'row', flexWrap: 'wrap', gap: 12 }}>{shortcuts.map(item => <Pressable accessibilityRole="button" accessibilityLabel={item.label} key={item.screen} onPress={() => app.navigate(item.screen)} style={{ flexGrow: 1, width: '45%', backgroundColor: '#FFF', borderRadius: 18, borderWidth: 1, borderColor: colors.border, padding: 17, gap: 8 }}><Ionicons name={item.icon} size={23} color={colors.primary} /><Text style={styles.label}>{item.label}</Text><Text style={styles.muted}>{item.text}</Text></Pressable>)}</View>
    {(error || refreshError) ? <Notice message={error || refreshError} onRetry={resource.retry} /> : null}{loading ? <Loading /> : data ? <>
      <Heading title="项目进展" /><Row>{[{ label: '已发布', value: data.metrics.published }, { label: '有效客户', value: data.metrics.qualified }, { label: '预约', value: data.metrics.appointments }].map(item => <View key={item.label} style={{ flex: 1, minWidth: 85, backgroundColor: '#FFF', borderRadius: 16, padding: 15 }}><Text style={{ fontSize: 25, color: colors.navy, fontWeight: '700' }}>{item.value}</Text><Text style={styles.muted}>{item.label}</Text></View>)}</Row>
      <Card><Text style={styles.title}>待处理事项</Text><Text style={styles.text}>{data.pending_inquiries.length} 条咨询等待资格确认</Text><Text style={styles.text}>{data.content_versions.filter(content => !content.release_id).length} 份内容等待完善或审核</Text><Text style={styles.text}>{app.pendingJobs.length} 个任务正在处理</Text><Row><Button title="处理咨询" small tone="secondary" onPress={() => app.navigate('leads')} /><Button title="查看内容" small tone="secondary" onPress={() => app.navigate('studio')} /></Row></Card>
      {app.project.completeness.items.filter(item => !item.complete).length ? <Card><Text style={styles.title}>下一步完善资料</Text>{app.project.completeness.items.filter(item => !item.complete).slice(0, 3).map(item => <Pressable key={item.key} accessibilityRole="button" accessibilityLabel={item.label} onPress={() => app.navigate(['assets', 'rights'].includes(item.key) ? 'assets' : 'project')} style={{ minHeight: 56, justifyContent: 'center', borderBottomColor: colors.border, borderBottomWidth: 1, gap: 4 }}><Text style={styles.text}>{item.label}</Text><Text style={styles.muted}>{item.detail}</Text></Pressable>)}</Card> : null}
    </> : null}</Page>;
}
