import assert from 'node:assert/strict';
import { randomUUID } from 'node:crypto';
import { test } from 'node:test';
import { InquiryIntent, memberRequest, metricValue, phoneLink, qualificationBody, reportComplete, RevealGate, revealInquiryContact, salesMembers, type InquiryDraft, type Member } from '../src/screens/crm-domain';

const draft: InquiryDraft = { name: '王女士', need: '改善居住', channel: '抖音', contact: '13800000000', source_confidence: 'unknown', source_text: '', contactable: true, consent: true };
test('uncertain inquiry submission retries use the same key, while a changed intent gets a new key', () => {
  let sequence = 0;
  const intent = new InquiryIntent(() => `attempt-${++sequence}`);
  const first = intent.prepare(draft);
  const retry = intent.prepare({ ...draft });
  assert.equal(first.request_key, 'attempt-1');
  assert.equal(retry.request_key, 'attempt-1');
  assert.equal(intent.prepare({ ...draft, need: '学区需求' }).request_key, 'attempt-2');
  intent.complete();
  assert.equal(intent.prepare(draft).request_key, 'attempt-3');
});
test('contact cannot be submitted without explicit consent or a usable contact', () => {
  const intent = new InquiryIntent(() => 'request');
  assert.throws(() => intent.prepare({ ...draft, consent: false }), /授权/);
  assert.throws(() => intent.prepare({ ...draft, contact: '' }), /联系方式/);
  assert.throws(() => intent.prepare({ ...draft, source_confidence: 'confirmed' }), /依据/);
});
test('qualifying needs all four checks and an explicit same-person check before linking', () => {
  const checks = { contactable: true, consent: true, demand_confirmed: true, fit_confirmed: true };
  assert.throws(() => qualificationBody({ ...checks, fit_confirmed: false }, '需求', '联系', '', false), /四项/);
  assert.throws(() => qualificationBody(checks, '需求', '联系', 'lead-1', false), /同一/);
  assert.deepEqual(qualificationBody(checks, ' 需求 ', ' 联系 ', 'lead-1', true), { ...checks, need: '需求', next_action: '联系', existing_lead_id: 'lead-1', same_contact_confirmed: true });
});
test('contact reveal never calls the server before purpose and explicit confirmation', async () => {
  const calls: unknown[] = [];
  const api = async <T>(path: string, options?: unknown): Promise<T> => { calls.push([path, options]); return { contact: '13800000000' } as T; };
  await assert.rejects(revealInquiryContact(api, 'project-1', 'inquiry-1', '购房咨询', false), /确认/);
  await assert.rejects(revealInquiryContact(api, 'project-1', 'inquiry-1', ' ', true), /用途/);
  assert.equal(calls.length, 0);
  assert.equal(await revealInquiryContact(api, 'project-1', 'inquiry-1', ' 购房咨询 ', true), '13800000000');
  assert.deepEqual(calls, [['/api/projects/project-1/inquiries/inquiry-1/contact-reveal', { method: 'POST', body: { purpose: '购房咨询' } }]]);
});
test('dial action only permits phone numbers, never links or dialer commands', () => {
  assert.equal(phoneLink('+86 (138) 0000-0000'), 'tel:+8613800000000');
  assert.equal(phoneLink('https://evil.test'), null);
  assert.equal(phoneLink('*100#'), null);
  assert.equal(phoneLink('13800000000;123'), null);
});
test('unobserved metrics stay unknown while observed zero remains zero', () => {
  assert.equal(metricValue(null), '待观察');
  assert.equal(metricValue(undefined), '待观察');
  assert.equal(metricValue(0), '0');
});
test('sales assignment excludes disabled members and other project scopes', () => {
  const base: Member = { id: 'm', user_id: 'u', name: '顾问', email: 'sales@example.test', role: 'sales', active: true, project_ids: null };
  assert.deepEqual(salesMembers([base, { ...base, id: 'disabled', active: false }, { ...base, id: 'wrong-project', project_ids: ['other'] }, { ...base, id: 'viewer', role: 'viewer' }], 'project-1').map(m => m.id), ['m']);
});
test('member creation validates credentials while edits send only permissions and prevent self disabling', () => {
  const initial: Member = { id: 'm', user_id: 'u', name: '成员', email: 'member@example.test', role: 'sales', active: true, project_ids: null };
  const password = randomUUID();
  const draft = { name: '新成员', email: ' MEMBER@EXAMPLE.TEST ', password, role: 'sales', active: true };
  assert.deepEqual(memberRequest(null, draft, false, ['p1', 'p1'], 'admin'), { name: '新成员', email: 'member@example.test', password, role: 'sales', project_ids: ['p1'] });
  assert.deepEqual(memberRequest(initial, draft, true, [], 'admin'), { role: 'sales', active: true, project_ids: null });
  assert.throws(() => memberRequest(null, { ...draft, password: 'short' }, true, [], 'admin'), /12/);
  assert.throws(() => memberRequest(initial, { ...draft, active: false }, true, [], 'u'), /自己/);
});
test('a partially filled window is unfinished even when every publication has a window record', () => {
  const metrics = { views: 0, likes: 0, comments: 0, saves: 0, inquiries: 0, qualified: 0, window_complete: true, observed_publications: 1, total_publications: 1 };
  assert.equal(reportComplete({ ...metrics, qualified: null }), false);
  assert.equal(reportComplete({ ...metrics, window_complete: false }), false);
  assert.equal(reportComplete(metrics), true);
});
test('a contact response arriving after backgrounding or changing customer cannot be displayed', () => {
  const gate = new RevealGate();
  const ticket = gate.begin();
  assert.equal(gate.allows(ticket), true);
  gate.invalidate();
  assert.equal(gate.allows(ticket), false);
  const next = gate.begin();
  assert.equal(gate.allows(next), true);
  assert.equal(gate.allows(ticket), false);
});
