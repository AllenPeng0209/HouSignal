import { useState } from 'react';
import { ActivityIndicator, Text } from 'react-native';
import { useApp } from '../state/AppContext';
import { Badge, Button, Card, Empty, Field, Heading, Notice, Page, Row, Select, Sheet, Toggle, styles } from '../components/ui';
import { memberRequest, roleLabels, type Member, type MemberDraft } from './crm-domain';
import { useCrmMutation, useCrmResource } from './crm-hooks';

const titleStyle = styles.title;
const bodyStyle = styles.muted;
export function SettingsScreen() {
  const { api, session, project, projects, canManage, reload, logout, notificationsEnabled, enableNotifications, navigate, baseUrl } = useApp();
  const organization = useCrmResource<{ id: string; name: string }>('/api/organization');
  const members = useCrmResource<Member[]>(canManage ? '/api/organization/members' : null);
  const [name, setName] = useState('');
  const [member, setMember] = useState<Member | null | undefined>(undefined);
  const [logoutConfirm, setLogoutConfirm] = useState(false);
  const mutation = useCrmMutation();
  const refresh = () => { organization.retry(); members.retry(); };
  return <Page refreshing={organization.loading || members.loading} onRefresh={refresh}>
    <Heading title="我的与团队" subtitle="账号、项目权限与手机提醒" />
    <Card>
      <Row><Text style={titleStyle}>{session.user.name}</Text><Badge label={roleLabels[session.role] || session.role} tone="blue" /></Row>
      <Text style={bodyStyle}>{session.user.email}</Text>
      <Text style={bodyStyle}>组织：{organization.data?.name || session.organization.name}</Text>
      <Text style={bodyStyle}>当前项目：{project?.name || '尚未选择项目'}</Text>
      <Button title="切换项目" tone="secondary" onPress={() => navigate('projects')} />
      {!!(organization.loading) && <ActivityIndicator />}
      {!!(organization.error) && <Notice message={organization.error} onRetry={organization.retry} />}
      {!!(canManage) && <><Field label="新的团队名称" value={name} onChangeText={setName} placeholder="输入团队名称" /><Button title="保存名称" disabled={!name.trim()} loading={mutation.busy} onPress={() => { void mutation.run(async () => { await api('/api/organization', { method: 'PATCH', body: { name: name.trim() } }); setName(''); organization.retry(); await reload(); }, '团队名称已保存'); }} /></>}
    </Card>
    <Card>
      <Text style={titleStyle}>手机提醒</Text>
      <Toggle label="任务完成与失败提醒" value={notificationsEnabled} onChange={value => { void mutation.run(() => enableNotifications(value)); }} description="打开后按系统授权发送本机任务提醒。可随时关闭。" />
      <Text style={bodyStyle}>提醒不包含客户联系方式与项目私有正文。</Text>
      <Button title="查看任务中心" tone="secondary" onPress={() => navigate('jobs')} />
    </Card>
    {!!(canManage) && <>
      <Heading title="团队成员" subtitle="按职责与项目范围分配权限" action={<Button title="添加成员" small onPress={() => setMember(null)} />} />
      {members.loading ? <ActivityIndicator accessibilityLabel="加载成员" /> : members.error ? <Notice message={members.error} onRetry={members.retry} /> : members.data?.length ? members.data.map(item => <Card key={item.id}>
        <Row><Text style={titleStyle}>{item.name}{item.user_id === session.user.id ? ' · 我' : ''}</Text><Badge label={item.active ? roleLabels[item.role] || item.role : '已停用'} tone={item.active ? 'muted' : 'red'} /></Row>
        <Text style={bodyStyle}>{item.email}</Text>
        <Text style={bodyStyle}>项目：{item.project_ids === null ? '全部项目' : item.project_ids.length ? item.project_ids.map(id => projects.find(p => p.id === id)?.name || '受限项目').join('、') : '暂无项目权限'}</Text>
        <Button title="管理成员权限" tone="secondary" onPress={() => setMember(item)} />
      </Card>) : <Empty title="暂无成员" action={<Button title="添加成员" onPress={() => setMember(null)} />} />}
    </>}
    <Card><Text style={titleStyle}>工作区连接</Text><Text style={bodyStyle}>{baseUrl}</Text><Text style={bodyStyle}>项目资料、发布版本和跟进记录由当前组织的服务端统一保存。</Text></Card>
    {!!(mutation.error) && <Notice message={mutation.error} />}
    <Button title="退出登录" tone="danger" onPress={() => setLogoutConfirm(true)} />
    {!!(canManage && member !== undefined) && <MemberSheet initial={member} onClose={() => setMember(undefined)} onSaved={() => { members.retry(); void reload(); }} />}
    <Sheet visible={logoutConfirm} title="退出当前工作区" onClose={() => { if (!mutation.busy) setLogoutConfirm(false); }}>
      <Text style={bodyStyle}>已保存的项目与对话记录保留在服务器。尚未提交的手机表单需要重新填写。</Text>
      {!!(mutation.error) && <Notice message={mutation.error} />}
      <Button title="确认退出登录" tone="danger" loading={mutation.busy} onPress={() => { void mutation.run(logout); }} />
    </Sheet>
  </Page>;
}

function MemberSheet({ initial, onClose, onSaved }: { initial: Member | null; onClose: () => void; onSaved: () => void }) {
  const { api, projects, session } = useApp();
  const [draft, setDraft] = useState<MemberDraft>({ name: initial?.name || '', email: initial?.email || '', password: '', role: initial?.role || 'content', active: initial?.active ?? true });
  const [all, setAll] = useState(initial ? initial.project_ids === null : true);
  const [scope, setScope] = useState<string[]>(initial?.project_ids || []);
  const [reviewed, setReviewed] = useState(false);
  const mutation = useCrmMutation();
  const self = initial?.user_id === session.user.id;
  const roleOptions = Object.entries(roleLabels).filter(([value]) => value !== 'owner' || initial?.role === 'owner').map(([value, label]) => ({ value, label }));
  const set = (key: keyof MemberDraft, value: string | boolean) => { setReviewed(false); setDraft(s => ({ ...s, [key]: value })); };
  async function save() {
    if (!reviewed) return;
    const result = await mutation.run(() => api(initial ? `/api/organization/members/${initial.id}` : '/api/organization/members', { method: initial ? 'PATCH' : 'POST', body: memberRequest(initial, draft, all, scope, session.user.id) }), initial ? '成员权限已更新' : '新成员已创建');
    if (result) { setDraft(s => ({ ...s, password: '' })); onSaved(); onClose(); }
  }
  return <Sheet visible title={initial ? '管理成员权限' : '添加团队成员'} onClose={() => { if (!mutation.busy) onClose(); }}>
    <Field label="成员姓名" value={draft.name} editable={!initial} onChangeText={value => set('name', value)} />
    <Field label="登录邮箱" value={draft.email} editable={!initial} keyboardType="email-address" onChangeText={value => set('email', value)} />
    {!!(!initial) && <Field label="初始密码（至少 12 位）" value={draft.password} secureTextEntry onChangeText={value => set('password', value)} />}
    <Select label="业务角色" value={draft.role} options={roleOptions} onChange={value => set('role', value)} />
    <Toggle label="可访问全部项目" value={all} onChange={value => { setAll(value); setReviewed(false); }} />
    {!!(!all) && <Card><Text style={titleStyle}>选择允许访问的项目</Text>{projects.length ? projects.map(item => <Toggle key={item.id} label={item.name} value={scope.includes(item.id)} onChange={value => { setReviewed(false); setScope(s => value ? [...s, item.id] : s.filter(id => id !== item.id)); }} />) : <Text style={bodyStyle}>暂无可分配项目。保存空范围后，成员不能访问项目。</Text>}</Card>}
    {initial && (self ? <Notice message="当前账号不能在手机端停用自己。角色或项目范围变化可能影响下一次访问，服务端会保护组织最后一位管理员。" /> : <Toggle label="成员账号启用" value={draft.active} onChange={value => set('active', value)} description="停用后，成员已有登录也会被服务端拒绝。" />)}
    <Toggle label="已确认角色、项目范围与账号状态" value={reviewed} onChange={setReviewed} />
    {!!(mutation.error) && <Notice message={mutation.error} />}
    <Button title={initial ? '保存权限' : '创建成员'} disabled={!reviewed} loading={mutation.busy} onPress={() => void save()} />
  </Sheet>;
}
