import { test } from 'node:test';
import assert from 'node:assert/strict';
import { randomUUID } from 'node:crypto';
import { createStorage } from '../src/state/storage-core';

function memory() {
  const rows = new Map<string, string>();
  return { rows, get: async (key: string) => rows.get(key) ?? null,
    set: async (key: string, value: string) => { rows.set(key, value); },
    remove: async (key: string) => { rows.delete(key); } };
}

const valid = { token: 'hs_mobile_' + 'a'.repeat(64), expiresAt: '2099-01-01T00:00:00Z', baseUrl: 'https://api.example.test/' };

test('credentials persist only in the private store and restore a normalized origin', async () => {
  const privateStore = memory(), publicStore = memory();
  const store = createStorage(privateStore, publicStore, false);
  await store.saveCredentials(valid);
  assert.equal(publicStore.rows.size, 0);
  assert.deepEqual(await store.readCredentials(), { ...valid, baseUrl: 'https://api.example.test' });
  await store.savePreference('project-id', 'p1');
  assert.equal(publicStore.rows.size, 1);
  await store.clearCredentials();
  assert.equal(await store.readCredentials(), null);
  assert.equal(await store.readPreference('project-id'), 'p1');
});

test('malformed and expired stored credentials are erased without returning a token', async () => {
  const browserToken = randomUUID();
  for (const payload of ['{broken', '{}', JSON.stringify({ ...valid, expiresAt: '2020-01-01T00:00:00Z' }),
    JSON.stringify({ ...valid, token: browserToken }), JSON.stringify({ ...valid, baseUrl: 'https://name:password@api.example.test' })]) {
    const privateStore = memory(), publicStore = memory();
    const store = createStorage(privateStore, publicStore, false);
    await store.saveCredentials(valid);
    const key = [...privateStore.rows.keys()][0];
    privateStore.rows.set(key, payload);
    assert.equal(await store.readCredentials(), null);
    assert.equal(privateStore.rows.size, 0);
  }
});

test('invalid saves cannot replace a working private session', async () => {
  const privateStore = memory(), store = createStorage(privateStore, memory(), false);
  await store.saveCredentials(valid);
  for (const change of [{ token: 'wrong' }, { expiresAt: 'invalid' }, { baseUrl: 'http://api.example.test' }]) {
    await assert.rejects(store.saveCredentials({ ...valid, ...change }));
    assert.equal((await store.readCredentials())?.token, valid.token);
  }
});

test('local development origins require explicit development mode', async () => {
  const store = createStorage(memory(), memory(), true);
  await store.saveCredentials({ ...valid, baseUrl: 'http://127.0.0.1:8017/' });
  assert.equal((await store.readCredentials())?.baseUrl, 'http://127.0.0.1:8017');
});

test('public preference removal does not erase other preferences or accept credential keys', async () => {
  const publicStore = memory(), store = createStorage(memory(), publicStore, false);
  await store.savePreference('project-id', 'p1');
  await store.savePreference('notifications', 'true');
  await store.savePreference('project-id', null);
  assert.equal(await store.readPreference('project-id'), null);
  assert.equal(await store.readPreference('notifications'), 'true');
  await assert.rejects(store.savePreference('access-token', 'private'));
  await assert.rejects(store.savePreference('../escape', 'value'));
});

test('a locked or failing private store never falls back to public preferences', async () => {
  const publicStore = memory();
  const store = createStorage({ get: async () => { throw new Error('device locked'); }, set: async () => { throw new Error('device locked'); }, remove: async () => {} }, publicStore, false);
  await assert.rejects(store.readCredentials());
  await assert.rejects(store.saveCredentials(valid));
  assert.equal(publicStore.rows.size, 0);
});

test('native storage failures show an actionable message without SDK internals or private paths', async () => {
  const fail = async (): Promise<never> => { throw new Error('FunctionCallException: secret-value at /private/customer/ExpoSecureStore/SecureStoreModule.swift:160'); };
  const store = createStorage({get:fail,set:fail,remove:fail},memory(),false);
  for (const work of [()=>store.readCredentials(),()=>store.saveCredentials(valid),()=>store.clearCredentials()]) {
    await assert.rejects(work(), (error: unknown) => {
      assert.ok(error instanceof Error);
      assert.equal(/FunctionCallException|secret-value|\/private\/|\.swift/.test(error.message),false);
      assert.match(error.message,/重试/);
      return true;
    });
  }
});
