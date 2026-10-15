import { useEffect, useRef, useState } from 'react';
import { AppState, Linking, Text } from 'react-native';
import { useApp } from '../state/AppContext';
import { Badge, Button, Field, Notice, Row, Toggle, colors } from '../components/ui';
import { phoneLink, RevealGate, revealInquiryContact } from './crm-domain';
import { useCrmMutation } from './crm-hooks';

export function ContactPanel({ inquiryId, eligible }: { inquiryId: string; eligible: boolean }) {
  const { project, canSales } = useApp();
  return <ContactBody key={`${project?.id}:${inquiryId}:${eligible}:${canSales}`} inquiryId={inquiryId} eligible={eligible} />;
}
function ContactBody({ inquiryId, eligible }: { inquiryId: string; eligible: boolean }) {
  const { api, project, canSales } = useApp();
  const [purpose, setPurpose] = useState('');
  const [confirmed, setConfirmed] = useState(false);
  const [contact, setContact] = useState('');
  const gate = useRef(new RevealGate());
  const mutation = useCrmMutation();
  useEffect(() => { const current = gate.current; const listener = AppState.addEventListener('change', state => { if (state !== 'active') { current.invalidate(); setContact(''); setConfirmed(false); } }); return () => { current.invalidate(); listener.remove(); }; }, []);
  if (!canSales || !eligible) return <Text style={{ color: colors.muted }}>联系方式仅在有效联系授权与销售权限范围内查看。</Text>;
  return <>
    <Badge label={contact ? '本次查看已授权' : '联系方式由服务端加密保管'} tone={contact ? 'green' : 'muted'} />
    {contact ? <>
      <Text selectable style={{ fontSize: 22, color: colors.navy, fontWeight: '700' }}>{contact}</Text>
      <Row><Button title="隐藏联系方式" tone="secondary" onPress={() => { gate.current.invalidate(); setContact(''); setConfirmed(false); }} />{!!(phoneLink(contact)) && <Button title="拨打电话" onPress={() => { void mutation.run(async () => { await Linking.openURL(phoneLink(contact)!); }); }} loading={mutation.busy} />}</Row>
    </> : <>
      <Field label="本次查看用途" value={purpose} onChangeText={setPurpose} placeholder="例如：回复客户本项目购房咨询" />
      <Toggle label="确认在客户联系授权范围内查看" value={confirmed} onChange={setConfirmed} description="本次查看会按服务端权限校验并记录审计。" />
      <Button title="查看授权联系方式" loading={mutation.busy} disabled={!confirmed || !purpose.trim()} onPress={() => { if (project) void mutation.run(async () => { const ticket = gate.current.begin(); const result = await revealInquiryContact(api, project.id, inquiryId, purpose, confirmed); if (gate.current.allows(ticket)) setContact(result); }); }} />
    </>}
    {mutation.error ? <Notice message={mutation.error} /> : null}
  </>;
}
