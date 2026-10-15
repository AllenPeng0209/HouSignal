import { test } from 'node:test';
import assert from 'node:assert/strict';
import { createServer } from 'node:http';
import { createClient, normalizeBaseUrl, ApiError } from '../src/api/client';

// These boundaries catch origin credential leaks, accidental cookie use, and silent auth expiry.
test('server URLs reject embedded credentials and cleartext release endpoints', () => {
  assert.throws(() => normalizeBaseUrl('https://name:secret@example.com', false));
  assert.throws(() => normalizeBaseUrl('http://example.com', false));
  assert.equal(normalizeBaseUrl('https://example.com/', false), 'https://example.com');
  assert.equal(normalizeBaseUrl('http://127.0.0.1:8017/', true), 'http://127.0.0.1:8017');
  assert.throws(() => normalizeBaseUrl('https://example.com/api', false));
});

test('API makes authenticated JSON calls without browser cookies', async () => {
  const observed: any[] = [];
  const server = createServer((req, res) => {
    let raw = ''; req.on('data', data => raw += data); req.on('end', () => {
      observed.push({ path: req.url, authorization: req.headers.authorization, cookie: req.headers.cookie, body: JSON.parse(raw) });
      res.setHeader('Content-Type', 'application/json'); res.end(JSON.stringify({ job_id: 'job-1' }));
    });
  });
  await new Promise<void>(resolve => server.listen(0, '127.0.0.1', resolve));
  const address = server.address() as { port: number };
  try {
    const client = createClient(`http://127.0.0.1:${address.port}`, () => 'hs_mobile_test', () => {}, true);
    assert.deepEqual(await client.request('/api/projects/p1/contents/generate', { method: 'POST', body: { topic_id: 't1' } }), { job_id: 'job-1' });
    assert.deepEqual(observed, [{ path: '/api/projects/p1/contents/generate', authorization: 'Bearer hs_mobile_test', cookie: undefined, body: { topic_id: 't1' } }]);
  } finally { server.close(); }
});

test('API refuses external asset destinations before attaching a bearer', async () => {
  const client = createClient('https://api.example.com', () => 'private', () => {}, false);
  await assert.rejects(client.request('https://other.example/api/projects'), /地址|接口/);
  assert.throws(() => client.url('//other.example/api/file'));
  assert.throws(() => client.url('/api/../other'));
  assert.throws(() => client.url('/api/file?access_token=secret'));
});

test('an expired session is surfaced once and server detail is shown', async () => {
  let expired = 0;
  const server = createServer((_req,res) => { res.writeHead(401, { 'Content-Type': 'application/json' }); res.end('{"detail":"登录已过期"}'); });
  await new Promise<void>(resolve => server.listen(0,'127.0.0.1',resolve));
  try {
    const client = createClient(`http://127.0.0.1:${(server.address() as any).port}`, () => 'expired', () => expired++, true);
    await assert.rejects(client.request('/api/auth/me'), (error: unknown) => error instanceof ApiError && error.status === 401 && error.message === '登录已过期');
    assert.equal(expired,1);
  } finally { server.close(); }
});

test('permission revocation does not silently use cached identity', async () => {
  let invalidated = 0;
  const client = createClient('https://api.example.com', () => 'revoked', () => invalidated++, false,
    async () => new Response('{"detail":"账号或组织权限已失效"}', {status:403}));
  await assert.rejects(client.request('/api/auth/me'), (e: unknown) => e instanceof ApiError && e.status === 403);
  assert.equal(invalidated,1);
});

test('empty and non-JSON errors remain actionable', async () => {
  const client = createClient('https://api.example.com', () => null, () => {}, false,
    async () => new Response('<h1>Bad Gateway</h1>', {status:502}));
  await assert.rejects(client.request('/api/health'), (e: unknown) => e instanceof ApiError && e.status === 502 && !e.message.includes('<h1>'));
});

test('auth expiry reports the credential used by the request, not a replacement session', async () => {
  let token = 'first'; let used: string | null | undefined;
  let release!: (response: Response) => void;
  const client = createClient('https://api.example.com', () => token, value => { used = value; }, false,
    () => new Promise<Response>(resolve => { release = resolve; }));
  const request = client.request('/api/auth/me');
  token = 'second';
  release(new Response('{"detail":"登录已过期"}', { status:401 }));
  await assert.rejects(request, ApiError);
  assert.equal(used, 'first');
});
