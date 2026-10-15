import { Pressable, Text, View } from 'react-native';
import Ionicons from '@expo/vector-icons/Ionicons';
import { useApp } from '../state/AppContext';
import { Card, Heading, Page, colors, styles } from '../components/ui';
import type { ScreenName } from '../lib/contracts';
const entries: { screen: ScreenName; label: string; detail: string; icon: keyof typeof Ionicons.glyphMap }[] = [
  { screen: 'projects', label: '项目管理', detail: '切换、搜索与新建项目', icon: 'business-outline' },
  { screen: 'project', label: '楼盘资料', detail: '项目设置与事实核验', icon: 'document-text-outline' },
  { screen: 'assets', label: '素材库', detail: '拍摄、录音、上传与授权', icon: 'images-outline' },
  { screen: 'jobs', label: '任务中心', detail: '生成、转写、渲染与结果', icon: 'time-outline' },
  { screen: 'accounts', label: '账号与内容计划', detail: '账号策略、选题机会与排期', icon: 'calendar-outline' },
  { screen: 'skills', label: '技能库', detail: '用专业工作流推进内容', icon: 'sparkles-outline' },
  { screen: 'publications', label: '发布记录', detail: '回执、平台状态与指标', icon: 'paper-plane-outline' },
  { screen: 'reports', label: '效果复盘', detail: 'D7 / D30 内容和客户转化', icon: 'stats-chart-outline' },
  { screen: 'settings', label: '账号与组织设置', detail: '成员、项目权限、提醒与退出', icon: 'settings-outline' },
];
export function MoreScreen() { const app = useApp(); return <Page><Heading title="更多工作工具" subtitle={app.session.organization.name} /><Card>{entries.map(item => <Pressable key={item.screen} accessibilityRole="button" accessibilityLabel={item.label} onPress={() => app.navigate(item.screen)} style={{ minHeight: 76, flexDirection: 'row', gap: 14, alignItems: 'center', paddingVertical: 12, borderBottomWidth: 1, borderColor: colors.border }}><View style={{ width: 42, height: 42, borderRadius: 12, backgroundColor: '#EEF2FF', alignItems: 'center', justifyContent: 'center' }}><Ionicons name={item.icon} color={colors.primary} size={22} /></View><View style={{ flex: 1 }}><Text style={styles.label}>{item.label}</Text><Text style={styles.muted}>{item.detail}</Text></View><Ionicons name="chevron-forward" color={colors.muted} size={18} /></Pressable>)}</Card></Page>; }
