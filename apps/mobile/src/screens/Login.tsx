import { useState } from 'react';
import { KeyboardAvoidingView, Platform, ScrollView, Text, View } from 'react-native';
import { SafeAreaView } from 'react-native-safe-area-context';
import Ionicons from '@expo/vector-icons/Ionicons';
import { Button, Card, Field, Notice, colors, styles } from '../components/ui';
import { useRuntime } from '../state/RuntimeProvider';

export function LoginScreen() {
  const runtime = useRuntime();
  const [email, setEmail] = useState(''), [password, setPassword] = useState(''), [server, setServer] = useState(runtime.baseUrl);
  const [advanced, setAdvanced] = useState(!runtime.baseUrl), [busy, setBusy] = useState(false), [error, setError] = useState('');
  const submit = async () => {
    setBusy(true); setError('');
    try { await runtime.login(server, email, password); setPassword(''); }
    catch (e) { setError((e as Error).message); }
    finally { setBusy(false); }
  };
  return <SafeAreaView style={{ flex: 1, backgroundColor: colors.background }}><KeyboardAvoidingView style={{ flex: 1 }} behavior={Platform.OS === 'ios' ? 'padding' : undefined}><ScrollView contentContainerStyle={{ flexGrow: 1, padding: 26, gap: 26, justifyContent: 'center', maxWidth: 520, width: '100%', alignSelf: 'center' }} keyboardShouldPersistTaps="handled">
    <View style={{ flexDirection: 'row', gap: 13, alignItems: 'center' }}><View style={{ width: 50, height: 50, borderRadius: 16, backgroundColor: colors.primary, justifyContent: 'center', alignItems: 'center' }}><Ionicons name="business-outline" color="#FFF" size={28} /></View><View><Text style={{ color: colors.navy, fontSize: 27, fontWeight: '700' }}>楼响</Text><Text style={{ color: colors.muted, fontSize: 11, letterSpacing: 2 }}>HOUSIGNAL</Text></View></View>
    <View style={{ gap: 10 }}><Text accessibilityRole="header" style={{ color: colors.navy, fontSize: 32, lineHeight: 44, fontWeight: '700' }}>好房值得{ '\n' }被认真讲述。</Text><Text style={styles.muted}>随时整理项目、推进内容、连接客户。</Text></View>
    <Card><Text style={styles.title}>进入你的工作区</Text><Field label="工作邮箱" value={email} onChangeText={setEmail} keyboardType="email-address" placeholder="输入工作邮箱" testID="login-email" /><Field label="密码" value={password} onChangeText={setPassword} secureTextEntry placeholder="输入密码" testID="login-password" />
      {(error || runtime.error) ? <Notice message={error || runtime.error} onRetry={runtime.error ? () => void runtime.retry() : undefined} /> : null}
      <Button title="登录工作区" onPress={() => void submit()} loading={busy} disabled={!email.trim() || !password || !server.trim()} testID="login-submit" />
      <Button title={advanced ? '收起服务地址' : '服务地址设置'} tone="ghost" small onPress={() => setAdvanced(value => !value)} />
      {advanced ? <Field label="楼响服务地址" value={server} onChangeText={setServer} keyboardType="url" placeholder="https://你的楼响服务域名" testID="login-server" /> : null}
    </Card>
    <View style={{ flexDirection: 'row', gap: 8, justifyContent: 'center', alignItems: 'center' }}><Ionicons name="shield-checkmark-outline" color={colors.muted} size={16} /><Text style={{ color: colors.muted, fontSize: 12 }}>项目和客户数据按组织与权限隔离</Text></View>
  </ScrollView></KeyboardAvoidingView></SafeAreaView>;
}
