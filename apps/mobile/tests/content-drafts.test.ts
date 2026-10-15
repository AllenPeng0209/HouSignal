import assert from 'node:assert/strict';
import { test } from 'node:test';
import { ContentDraftStore } from '../src/domain/content-drafts';
const scope = { organizationId: 'org', userId: 'user', projectId: 'project', contentId: 'v1' };
test('editing survives navigation and is isolated by organization, user, project and content version', () => {
  const store = new ContentDraftStore<{ script: string }>();
  store.set(scope, { script: '未保存的编辑' });
  assert.deepEqual(store.get(scope), { script: '未保存的编辑' });
  for (const field of ['organizationId', 'userId', 'projectId', 'contentId']) assert.equal(store.get({ ...scope, [field]: 'other' }), undefined);
});
test('restoring a draft returns an independent copy and discarding preserves other versions', () => {
  const store = new ContentDraftStore<{ script: string }>();
  const original = { script: 'a' }; store.set(scope, original); store.set({ ...scope, contentId: 'v2' }, { script: 'b' });
  original.script = 'mutated'; const restored = store.get(scope); assert.deepEqual(restored, { script: 'a' }); restored!.script = 'changed'; assert.deepEqual(store.get(scope), { script: 'a' });
  store.forget(scope); assert.equal(store.get(scope), undefined); assert.deepEqual(store.get({ ...scope, contentId: 'v2' }), { script: 'b' });
});
test('logout removes every scoped content draft', () => {
  const store = new ContentDraftStore<{ script: string }>(); store.set(scope, { script: 'a' }); store.set({ ...scope, userId: 'other' }, { script: 'b' });
  store.clear(); assert.equal(store.get(scope), undefined); assert.equal(store.get({ ...scope, userId: 'other' }), undefined);
});
