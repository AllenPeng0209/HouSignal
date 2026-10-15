import assert from 'node:assert/strict';
import test from 'node:test';

const load = async () => import('../src/domain/operations-input');

test('account profile rejects credential-bearing homepage links', async () => {
  const { validateAccount } = await load();
  assert.throws(() => validateAccount({ name: '顾问', platform: '抖音', homepage: 'https://user:password@example.com' }), /主页/);
});
test('discovery requires a public verifiable source', async () => {
  const { validateOpportunity } = await load();
  assert.throws(() => validateOpportunity({ title: '楼市趋势', platform: '抖音', kind: 'trend', source_url: '', summary: '' }), /来源/);
});
test('scheduled content rejects impossible dates', async () => {
  const { validatePlan } = await load();
  assert.throws(() => validatePlan({ title: '看房', channel: '抖音', topic_id: 'tour', status: 'scheduled', scheduled_for: '2026-02-30', account_id: '', opportunity_id: '', angle: '', brief: '' }, []), /日期/);
});
test('plan update retains original source by omitting editable provenance', async () => {
  const { validatePlan } = await load();
  const result = validatePlan({ title: '  看房  ', channel: '抖音', topic_id: 'tour', status: 'draft', scheduled_for: '', account_id: '', opportunity_id: 'other', angle: '', brief: '' }, [], { opportunity_id: 'original' });
  assert.deepEqual(result, { title: '看房', channel: '抖音', topic_id: 'tour', status: 'draft', scheduled_for: null, account_id: null, angle: '', brief: '' });
});
test('plans reject paused newly selected accounts and channel mismatch', async () => {
  const { validatePlan } = await load();
  const form = { title: '看房', channel: '抖音', topic_id: 'tour', status: 'draft', scheduled_for: '', account_id: 'a', opportunity_id: '', angle: '', brief: '' };
  assert.throws(() => validatePlan(form, [{ id: 'a', platform: '抖音', status: 'paused' }]), /暂停/);
  assert.throws(() => validatePlan(form, [{ id: 'a', platform: '小红书', status: 'managed' }]), /平台/);
});
test('public links reject access tokens and executable schemes', async () => {
  const { publicUrl } = await load();
  assert.equal(publicUrl('https://example.com/?token=secret'), null);
  assert.equal(publicUrl('javascript:alert(1)'), null);
  assert.equal(publicUrl('https://example.com/post/23'), 'https://example.com/post/23');
});
