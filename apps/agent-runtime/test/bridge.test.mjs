import test from 'node:test';
import assert from 'node:assert/strict';
import { createServer } from 'node:http';
import { spawn } from 'node:child_process';
import { createInterface } from 'node:readline';
import { fileURLToPath } from 'node:url';

const runtime = fileURLToPath(new URL('../bridge.mjs', import.meta.url));
const fakeKey = 'test-only-environment-credential';
const tool = { type: 'function', function: { name: 'crm_update', description: 'Update the client',
  parameters: { type: 'object', properties: { note: { type: 'string' } }, required: ['note'] } } };
async function serve(t, handler) {
  const server = createServer(async (request, response) => {
    let body = '';
    for await (const chunk of request) body += chunk;
    await handler(request, JSON.parse(body), response);
  });
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  t.after(() => { server.closeAllConnections(); server.close(); });
  return `http://127.0.0.1:${server.address().port}/v1`;
}
function reply(response, delta, finish) {
  response.writeHead(200, { 'content-type': 'text/event-stream' });
  response.end(`data: ${JSON.stringify({ id: 'test-completion', model: 'qwen-plus',
    choices: [{ index: 0, delta, finish_reason: finish }],
    usage: { prompt_tokens: 12, completion_tokens: 3, total_tokens: 15 } })}\n\ndata: [DONE]\n\n`);
}
function processRun(t, onEvent = () => {}, key = fakeKey) {
  const child = spawn(process.execPath, [runtime], { env: { ...process.env, HOUSIGNAL_PI_API_KEY: key }, stdio: ['pipe', 'pipe', 'pipe'] });
  const events = [];
  let stderr = '';
  const timeout = setTimeout(() => child.kill('SIGKILL'), 8000);
  t.after(() => { clearTimeout(timeout); child.kill('SIGKILL'); });
  child.stderr.on('data', chunk => { stderr += chunk; });
  const send = command => child.stdin.write(JSON.stringify(command) + '\n');
  createInterface({ input: child.stdout }).on('line', line => {
    const event = JSON.parse(line); events.push(event); onEvent(event, send);
  });
  const completion = new Promise((resolve, reject) => {
    child.once('error', reject);
    child.once('exit', (code, signal) => { clearTimeout(timeout); resolve({ events, stderr, code, signal }); });
  });
  return { send, completion };
}

// Fails if the executable bridge cannot drive actual Pi streaming and delegated tools.
test('NDJSON bridge completes a real Pi/OpenAI-compatible stream and Python-style tool round trip', async t => {
  const requests = [];
  const baseUrl = await serve(t, (request, body, response) => {
    assert.equal(request.headers.authorization, `Bearer ${fakeKey}`);
    requests.push(body);
    if (!body.messages.some(m => m.role === 'tool')) reply(response, {
      role: 'assistant', tool_calls: [{ index: 0, id: 'c1', type: 'function', function: { name: 'crm_update', arguments: '{"note":"safe redacted preference"}' } }],
    }, 'tool_calls');
    else reply(response, { role: 'assistant', content: `Saved. ${fakeKey}` }, 'stop');
  });
  const run = processRun(t, (event, send) => {
    if (event.type === 'tool_call') {
      assert.deepEqual(event, { type: 'tool_call', id: 'c1', name: 'crm_update', arguments: { note: 'safe redacted preference' } });
      send({ type: 'tool_result', id: event.id, result: { updated: true } });
    }
  });
  run.send({ type: 'start', systemPrompt: 'Authorized project only.', model: { id: 'qwen-plus', baseUrl },
    messages: [{ role: 'user', content: 'Save the client preference.' }], tools: [tool], maxOutputTokens: 800 });
  const result = await run.completion;
  assert.equal(result.code, 0);
  assert.equal(result.stderr, '');
  assert.equal(requests.length, 2);
  assert.equal(requests[0].max_tokens, 800);
  assert.equal(requests[0].store, undefined);
  assert.equal(requests[0].reasoning_effort, undefined);
  assert.equal(requests[0].messages[0].role, 'system');
  assert.equal(requests[0].tools[0].function.name, 'crm_update');
  assert.equal(requests[0].tool_choice, 'required');
  assert.equal(requests[1].tool_choice, 'auto');
  const done = result.events.find(event => event.type === 'done');
  assert.equal(done.text, 'Saved. [REDACTED]');
  assert.equal(done.usage.totalTokens, 30);
  assert.equal(done.toolCalls, 1);
  assert.ok(!JSON.stringify(result.events).includes(fakeKey));
});

// Fails if unlimited scheduling echoes the complete transcript as an oversized done frame.
test('bridge terminal summary excludes restorable engine history and stays below the parent frame boundary', async t => {
  const history = 'historical-transcript-only|'.repeat(120_000);
  assert.ok(Buffer.byteLength(history) > 2 * 1024 * 1024);
  let receivedHistory = false;
  const baseUrl = await serve(t, (_request, body, response) => {
    receivedHistory = body.messages.some(message => message.role === 'user' && message.content === history);
    reply(response, { role: 'assistant', content: 'Long-running task completed.' }, 'stop');
  });
  const run = processRun(t);
  run.send({ type: 'start', config: { systemPrompt: 'Scope.', model: { id: 'qwen-plus', baseUrl },
    messages: [
      { role: 'user', content: history }, { role: 'assistant', content: 'Earlier work.' },
      { role: 'user', content: 'Complete the remaining task.' },
    ], tools: [], maxTurns: null, maxToolCalls: null, maxTotalTokens: null } });
  const result = await run.completion;
  assert.equal(result.code, 0);
  assert.equal(receivedHistory, true);
  const done = result.events.find(event => event.type === 'done');
  assert.equal(Object.hasOwn(done, 'state'), false);
  assert.equal(done.text, 'Long-running task completed.');
  assert.equal(done.usage.totalTokens, 15);
  assert.equal(done.limitReached, null);
  assert.equal(done.aborted, false);
  assert.ok(result.events.every(event => Buffer.byteLength(JSON.stringify(event) + '\n') < 2 * 1024 * 1024));
  assert.ok(!JSON.stringify(result.events).includes('historical-transcript-only'));
});

// Fails if abort does not cancel a tool waiter or queues reveal sensitive instructions.
test('bridge controls expose queue counts, clear queues and abort a pending parent tool', async t => {
  const baseUrl = await serve(t, (_request, _body, response) => reply(response, {
    role: 'assistant', tool_calls: [{ index: 0, id: 'pending', type: 'function', function: { name: 'crm_update', arguments: '{"note":"save"}' } }],
  }, 'tool_calls'));
  const run = processRun(t, (event, send) => {
    if (event.type === 'tool_call') {
      send({ type: 'steer', text: 'private meeting details' });
      send({ type: 'follow_up', text: 'private contact details' });
      send({ type: 'queue_status' });
      send({ type: 'clear_all_queues' });
      send({ type: 'queue_status' });
      send({ type: 'abort' });
    }
  });
  run.send({ type: 'start', config: { systemPrompt: 'Scope.', model: { id: 'qwen-plus', baseUrl },
    messages: [{ role: 'user', content: 'Save.' }], tools: [tool] } });
  const result = await run.completion;
  assert.equal(result.code, 0);
  assert.equal(result.events.find(event => event.type === 'done')?.aborted, true);
  const statuses = result.events.filter(event => event.type === 'queue_status');
  assert.deepEqual(statuses.map(({ steering, followUp }) => [steering, followUp]), [[1, 1], [0, 0]]);
  assert.ok(!JSON.stringify(result.events).includes('private meeting details'));
  assert.ok(!JSON.stringify(result.events).includes('private contact details'));
});

// Fails if missing credentials cause a raw error dump or an indefinite stdin wait.
test('bridge reports a safe credential error and exits when no configured key exists', async t => {
  const run = processRun(t, () => {}, '');
  run.send({ type: 'start', config: { model: { id: 'qwen-plus', baseUrl: 'https://example.invalid/v1' },
    messages: [{ role: 'user', content: 'Save.' }], tools: [] } });
  const result = await run.completion;
  assert.equal(result.code, 1);
  assert.equal(result.stderr, '');
  assert.deepEqual(result.events.at(-1), { type: 'error', code: 'CREDENTIALS_MISSING', message: 'The configured model credential is unavailable.' });
});

// Fails if a disconnected parent causes unhandled-rejection recursion instead of process exit.
test('bridge exits quietly when the parent closes its output pipe', async t => {
  const child = spawn(process.execPath, [runtime], { env: { ...process.env, HOUSIGNAL_PI_API_KEY: fakeKey }, stdio: ['pipe', 'pipe', 'pipe'] });
  t.after(() => child.kill('SIGKILL'));
  let stderr = '';
  child.stderr.on('data', chunk => { stderr += chunk; });
  child.stdout.destroy();
  const exited = new Promise(resolve => child.once('exit', (code, signal) => resolve({ code, signal })));
  const timer = setTimeout(() => child.kill('SIGKILL'), 2500);
  const result = await exited;
  clearTimeout(timer);
  assert.equal(result.signal, null);
  assert.equal(result.code, 1);
  assert.equal(stderr, '');
});

// Fails if the process protocol drops control ids or emits authority changes on enqueue.
test('bridge preserves human control ids and reports consumption after the active answer', async t => {
  const baseUrl = await serve(t, (_request, body, response) => {
    const lastUser = body.messages.filter(m => m.role === 'user').at(-1).content;
    if (lastUser === 'Next request.') reply(response, { role: 'assistant', content: 'Follow-up saved.' }, 'stop');
    else if (body.messages.some(m => m.role === 'tool')) reply(response, { role: 'assistant', content: 'Current note saved.' }, 'stop');
    else reply(response, { role: 'assistant', tool_calls: [{ index: 0, id: 'current', type: 'function',
      function: { name: 'crm_update', arguments: '{"note":"current"}' } }] }, 'tool_calls');
  });
  const run = processRun(t, (event, send) => {
    if (event.type === 'tool_call') {
      send({ type: 'follow_up', id: 'trusted-human-control-id', text: 'Next request.' });
      send({ type: 'tool_result', id: event.id, result: { updated: true } });
    }
  });
  run.send({ type: 'start', config: { systemPrompt: 'Scope.', model: { id: 'qwen-plus', baseUrl },
    messages: [{ role: 'user', content: 'Save current note.' }], tools: [tool] } });
  const result = await run.completion;
  assert.equal(result.code, 0);
  const consumption = result.events.findIndex(event => event.type === 'input_consumed');
  assert.deepEqual(result.events[consumption], { type: 'input_consumed', id: 'trusted-human-control-id', kind: 'follow_up' });
  assert.ok(result.events.findIndex(event => event.type === 'text_delta' && event.delta === 'Current note saved.') < consumption);
  assert.ok(result.events.findIndex(event => event.type === 'text_delta' && event.delta === 'Follow-up saved.') > consumption);
});
