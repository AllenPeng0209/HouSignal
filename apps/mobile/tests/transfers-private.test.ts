import { test } from 'node:test';
import assert from 'node:assert/strict';
import { createServer, type Server } from 'node:http';
import { AbortController as ReactNativeAbortController } from 'abort-controller';
import { createClient } from '../src/api/client';
import { createTransfers, sanitizeFilename, type TransferAdapter } from '../src/lib/transfers-core';

const asset = { id: 'a1', name: '证据.txt', kind: 'document', mime_type: 'text/plain', byte_size: 3, rights: 'pending', rights_evidence: '', origin_type: 'actual', metadata_json: {}, extracted_text: '', file_url: '/api/projects/p1/assets/a1/file', created_at: '2026-10-13T00:00:00Z' };
async function listen(server: Server) {
  await new Promise<void>(resolve => server.listen(0, '127.0.0.1', resolve));
  return 'http://127.0.0.1:' + (server.address() as { port: number }).port;
}
function localArtifacts(transport: typeof fetch = fetch) {
  const remaining = new Map<string, Uint8Array>();
  const shared: { uri: string; name: string; text: string }[] = [];
  let cleaned = 0;
  const adapter: TransferAdapter = {
    transport,
    readUploadFile: async file => file.file ?? new File(['abc'], file.name, { type: file.mimeType }),
    writeResponse: async (response, name, signal) => {
      if (signal.aborted) throw new Error('cancelled');
      const data = new Uint8Array(await response.arrayBuffer());
      const uri = 'file:///private/' + name;
      remaining.set(uri, data);
      return { uri, name, size: data.length, remove: async () => { remaining.delete(uri); } };
    },
    writeText: async (text, name, signal) => adapter.writeResponse(new Response(text), name, signal),
    share: async file => { shared.push({ uri: file.uri, name: file.name, text: new TextDecoder().decode(remaining.get(file.uri)) }); },
    clear: async () => { remaining.clear(); cleaned++; },
  };
  return { adapter, remaining, shared, cleaned: () => cleaned };
}

async function withNativeAbortController(action: () => Promise<void>) {
  const original = globalThis.AbortController;
  globalThis.AbortController = ReactNativeAbortController as unknown as typeof AbortController;
  try { await action(); }
  finally { globalThis.AbortController = original; }
}

test('native transfers work with React Native AbortSignal and clear private artifacts', async () => {
  await withNativeAbortController(async () => {
    const artifacts = localArtifacts(async (_url, options) => options?.method === 'POST'
      ? new Response(JSON.stringify(asset), { headers: { 'Content-Type': 'application/json' } })
      : new Response('native private file', { headers: { 'Content-Type': 'text/plain' } }));
    const client = createClient('https://api.example.test', () => 'secret', () => {}, false);
    const transfers = createTransfers(artifacts.adapter);
    await transfers.shareText('楼响：原生核验包', '核验.txt');
    await transfers.shareFile(client, '/api/file', 'private.txt', 'text/plain');
    assert.deepEqual(await transfers.uploadAsset(client, 'p1', { uri: 'file:///asset', name: '证据.txt', mimeType: 'text/plain', size: 3 }), asset);
    assert.deepEqual(artifacts.shared.map(item => item.text), ['楼响：原生核验包', 'native private file']);
    assert.equal(artifacts.remaining.size, 0);
  });
});

test('upload sends multipart file to the owning API without a token URL or browser cookie', async () => {
  let observed: { path?: string; authorization?: string; cookie?: string; contentType?: string; body?: string } = {};
  const server = createServer((request, response) => {
    let body = ''; request.on('data', bytes => body += bytes); request.on('end', () => {
      observed = { path: request.url, authorization: request.headers.authorization, cookie: request.headers.cookie, contentType: request.headers['content-type'], body };
      response.setHeader('Content-Type', 'application/json'); response.end(JSON.stringify(asset));
    });
  });
  const origin = await listen(server), adapter = localArtifacts(), progress: number[] = [];
  try {
    const client = createClient(origin, () => 'private-session', () => {}, true);
    const file = new File(['abc'], '证据.txt', { type: 'text/plain' });
    assert.deepEqual(await createTransfers(adapter.adapter).uploadAsset(client, 'p1', { uri: 'blob:local', name: file.name, mimeType: file.type, size: file.size, file }, value => progress.push(value)), asset);
    assert.equal(observed.path, '/api/projects/p1/assets');
    assert.equal(observed.authorization, 'Bearer private-session');
    assert.equal(observed.cookie, undefined);
    assert.match(observed.contentType ?? '', /multipart\/form-data; boundary=/);
    assert.match(observed.body ?? '', /name="file"/);
    assert.match(observed.body ?? '', /abc/);
    assert.deepEqual(progress, [0, 1]);
  } finally { server.close(); }
});

test('upload and download refuse HTTP redirects before contacting an external host', async () => {
  let leaked = 0;
  const external = createServer((_req, res) => { leaked++; res.end('wrong destination'); });
  const externalOrigin = await listen(external);
  const source = createServer((_req, res) => { res.writeHead(307, { Location: externalOrigin + '/api/file' }); res.end(); });
  const origin = await listen(source), artifacts = localArtifacts();
  try {
    const client = createClient(origin, () => 'secret-session', () => {}, true), transfers = createTransfers(artifacts.adapter);
    await assert.rejects(transfers.uploadAsset(client, 'p1', { uri: 'file:///asset', name: 'file.txt', mimeType: 'text/plain', size: 3 }));
    await assert.rejects(transfers.shareFile(client, '/api/file', 'file.txt'));
    assert.equal(leaked, 0);
    assert.equal(artifacts.shared.length, 0);
  } finally { source.close(); external.close(); }
});

test('uploads verify actual file sizes and reject malformed successful asset responses', async () => {
  const oversized = localArtifacts(async () => new Response(JSON.stringify(asset)));
  oversized.adapter.readUploadFile = async () => ({ size: 104857601 } as Blob);
  const client = createClient('https://api.example.test', () => 'secret', () => {}, false);
  await assert.rejects(createTransfers(oversized.adapter).uploadAsset(client, 'p1', { uri: 'file:///asset', name: 'file.txt', mimeType: 'text/plain', size: 3 }), /100MB/);
  for (const payload of [{}, { ...asset, file_url: 'https://external.test/private' }, { ...asset, file_url: '/api/projects/other/assets/a1/file' }, { ...asset, byte_size: -1 }]) {
    const artifacts = localArtifacts(async () => new Response(JSON.stringify(payload), { headers: { 'Content-Type': 'application/json' } }));
    await assert.rejects(createTransfers(artifacts.adapter).uploadAsset(client, 'p1', { uri: 'file:///asset', name: 'file.txt', mimeType: 'text/plain', size: 3 }), /返回|素材|接口/);
  }
});

test('private downloads share a local file and erase it after the share sheet returns', async () => {
  const artifacts = localArtifacts(async () => new Response('private document', { headers: { 'Content-Type': 'text/plain' } }));
  const client = createClient('https://api.example.test', () => 'secret', () => {}, false);
  await createTransfers(artifacts.adapter).shareFile(client, '/api/projects/p1/assets/a1/file', '../../客户资料.txt', 'text/plain');
  assert.equal(artifacts.shared.length, 1);
  assert.match(artifacts.shared[0].uri, /^file:\/\/\/private\//);
  assert.ok(!artifacts.shared[0].uri.includes('secret'));
  assert.equal(artifacts.shared[0].text, 'private document');
  assert.ok(!artifacts.shared[0].name.includes('..') && !artifacts.shared[0].name.includes('/'));
  assert.equal(artifacts.remaining.size, 0);
});

test('errors, HTML login pages, JSON errors and empty files are never passed to sharing', async () => {
  for (const response of [new Response('not found', { status: 404 }),
    new Response('<html>login</html>', { headers: { 'Content-Type': 'text/html' } }),
    new Response('{"detail":"error"}', { headers: { 'Content-Type': 'application/json' } }),
    new Response('', { headers: { 'Content-Type': 'video/mp4', 'Content-Length': '0' } }), new Response(null, { status: 204 })]) {
    const artifacts = localArtifacts(async () => response), client = createClient('https://api.example.test', () => 'secret', () => {}, false);
    await assert.rejects(createTransfers(artifacts.adapter).shareFile(client, '/api/file', 'video.mp4', 'video/mp4'));
    assert.equal(artifacts.shared.length, 0);
    assert.equal(artifacts.remaining.size, 0);
  }
});

test('share failure still erases temporary private files', async () => {
  const artifacts = localArtifacts(async () => new Response('private', { headers: { 'Content-Type': 'text/plain' } }));
  artifacts.adapter.share = async () => { throw new Error('sharing unavailable'); };
  const transfers = createTransfers(artifacts.adapter), client = createClient('https://api.example.test', () => 'secret', () => {}, false);
  await assert.rejects(transfers.shareFile(client, '/api/file', 'private.txt'), /sharing unavailable/);
  assert.equal(artifacts.remaining.size, 0);
});

test('logout cleanup aborts pending transfers and prevents their completed responses from sharing', async () => {
  await withNativeAbortController(async () => {
    let started!: () => void, release!: () => void;
    const startedPromise = new Promise<void>(resolve => started = resolve);
    const releasePromise = new Promise<void>(resolve => release = resolve);
    let signal: AbortSignal | undefined;
    const artifacts = localArtifacts(async (_url, options) => { signal = options?.signal ?? undefined; started(); await releasePromise; return new Response('private', { headers: { 'Content-Type': 'text/plain' } }); });
    const transfers = createTransfers(artifacts.adapter), client = createClient('https://api.example.test', () => 'secret', () => {}, false);
    const pending = transfers.shareFile(client, '/api/file', 'private.txt');
    const reached = await Promise.race([startedPromise.then(() => true), new Promise<false>(resolve => setTimeout(() => resolve(false), 500))]);
    assert.equal(reached, true, 'private download never started');
    await transfers.clearPrivateFiles();
    assert.equal(signal?.aborted, true);
    release();
    await assert.rejects(pending, /文件传输已取消/);
    assert.equal(artifacts.shared.length, 0);
    assert.equal(artifacts.remaining.size, 0);
    assert.equal(artifacts.cleaned(), 1);
  });
});

test('text export preserves Chinese UTF-8 and clears the artifact after sharing', async () => {
  const artifacts = localArtifacts();
  await createTransfers(artifacts.adapter).shareText('楼响：实拍事实与来源', '脚本.txt');
  assert.equal(artifacts.shared[0].text, '楼响：实拍事实与来源');
  assert.equal(artifacts.remaining.size, 0);
});

test('file names cannot escape the private cache or inject control characters', () => {
  for (const name of ['../../客户.txt', '\\..\\private.txt', '\u0000\r\nfile.txt', '', '.']) {
    const clean = sanitizeFilename(name);
    assert.ok(clean.length > 0 && clean.length <= 120);
    assert.ok(!/[\\/\u0000-\u001f]/.test(clean));
    assert.ok(!clean.includes('..'));
  }
});

test('JSON skill artifacts can be shared with an explicit JSON type or JSON filename', async () => {
  for (const [filename, mimeType] of [['结果.data', 'application/json'], ['结果.json', undefined]]) {
    const artifacts = localArtifacts(async () => new Response('{"response":"楼响分析"}', { headers: { 'Content-Type': 'application/json' } }));
    const client = createClient('https://api.example.test', () => 'secret', () => {}, false);
    await createTransfers(artifacts.adapter).shareFile(client, '/api/projects/p1/operations/skills/artifacts/job1/result.json', filename!, mimeType);
    assert.equal(artifacts.shared.length, 1);
    assert.equal(artifacts.shared[0].text, '{"response":"楼响分析"}');
    assert.equal(artifacts.remaining.size, 0);
  }
});

test('JSON API bodies cannot bypass file sharing checks with a JSON extension or caller type', async () => {
  for (const path of ['/api/projects/p1/overview', '/api/projects/p1/skills/artifacts/job1/result.json', '/api/projects/p1/operations/skills/artifacts/job1/result.json/extra']) {
    const artifacts = localArtifacts(async () => new Response('{"detail":"error"}', { headers: { 'Content-Type': 'application/json' } }));
    const client = createClient('https://api.example.test', () => 'secret', () => {}, false);
    await assert.rejects(createTransfers(artifacts.adapter).shareFile(client, path, 'result.json', 'application/json'));
    assert.equal(artifacts.shared.length, 0);
  }
  const artifacts = localArtifacts(async () => new Response('{"response":"text"}', { headers: { 'Content-Type': 'application/json' } }));
  const client = createClient('https://api.example.test', () => 'secret', () => {}, false);
  await assert.rejects(createTransfers(artifacts.adapter).shareFile(client, '/api/projects/p1/operations/skills/artifacts/job1/result.json', 'result.data'));
});
