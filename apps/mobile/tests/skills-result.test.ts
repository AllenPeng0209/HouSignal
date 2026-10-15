import assert from 'node:assert/strict';
import test from 'node:test';
const load = async () => import('../src/domain/skills-result');
test('unavailable and manual skills cannot submit background work', async () => {
  const { canRunSkill } = await load();
  assert.equal(canRunSkill({ mode: 'manual', status: 'manual' }, true, 'publish'), false);
  assert.equal(canRunSkill({ mode: 'easel', status: 'setup' }, true, 'write'), false);
  assert.equal(canRunSkill({ mode: 'easel', status: 'ready' }, false, 'write'), false);
  assert.equal(canRunSkill({ mode: 'easel', status: 'ready' }, true, '   '), false);
});
test('skill result downloads stay in the actual project job artifact scope', async () => {
  const { skillResult } = await load();
  const result = skillResult({ response: 'Generated draft', artifacts: [
    { name: 'valid.png', url: '/api/projects/p/operations/skills/artifacts/j/valid.png', mime_type: 'image/png' },
    { name: 'other.png', url: '/api/projects/other/operations/skills/artifacts/j/other.png' },
    { name: 'remote', url: 'https://example.com/?token=secret' },
    { name: 'script', url: 'javascript:alert(1)' },
  ] }, 'p', 'j');
  assert.equal(result.text, 'Generated draft');
  assert.deepEqual(result.artifacts, [{ name: 'valid.png', url: '/api/projects/p/operations/skills/artifacts/j/valid.png', mimeType: 'image/png' }]);
});
test('skill output enters a draft plan with real task provenance and review status', async () => {
  const { skillDraftPlan } = await load();
  const result = skillDraftPlan({ title: '看房方案', angle: '改善受众', channel: '抖音', topic_id: 'tour', account_id: '', brief: '原始回复' }, { id: 'j', skillTitle: '社媒文案', input: '写一条视频' });
  assert.equal(result?.status, 'draft');
  assert.equal(result?.opportunity_id, null);
  assert.equal(result?.scheduled_for, null);
  assert.match(result?.brief || '', /任务 j/);
  assert.match(result?.brief || '', /待人工核验/);
});
