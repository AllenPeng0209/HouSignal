import { Stack } from 'expo-router';
import { StatusBar } from 'expo-status-bar';
import { SafeAreaProvider } from 'react-native-safe-area-context';
import { View } from 'react-native';
import { RuntimeProvider, useRuntime } from '../state/RuntimeProvider';
import { colors, Loading } from '../components/ui';

function Navigation() { const { session, ready } = useRuntime(); if (!ready) return <View style={{ flex: 1, justifyContent: 'center', backgroundColor: colors.background }}><Loading text="正在确认工作区身份…" /></View>; return <Stack screenOptions={{ headerTintColor: colors.navy, headerBackTitle: '返回', headerShadowVisible: false, headerStyle: { backgroundColor: colors.background }, contentStyle: { backgroundColor: colors.background } }}><Stack.Protected guard={!session}><Stack.Screen name="login" options={{ headerShown: false }} /></Stack.Protected><Stack.Protected guard={!!session}><Stack.Screen name="(tabs)" options={{ headerShown: false }} />{[{ name: 'projects', title: '项目管理' }, { name: 'project', title: '楼盘资料' }, { name: 'materials', title: '素材库' }, { name: 'content', title: '内容详情' }, { name: 'jobs', title: '任务中心' }, { name: 'lead', title: '客户详情' }, { name: 'reports', title: '效果复盘' }, { name: 'settings', title: '账号与组织设置' }, { name: 'accounts', title: '账号与内容计划' }, { name: 'skills', title: '技能库' }, { name: 'publications', title: '发布记录' }].map(route => <Stack.Screen key={route.name} name={route.name} options={{ title: route.title }} />)}</Stack.Protected></Stack>; }
export default function Layout() { return <SafeAreaProvider><RuntimeProvider><StatusBar style="dark" /><Navigation /></RuntimeProvider></SafeAreaProvider>; }
