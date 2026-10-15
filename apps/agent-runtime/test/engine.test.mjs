import test from 'node:test';
import assert from 'node:assert/strict';
import { randomUUID } from 'node:crypto';
import { AssistantMessageEventStream } from '@earendil-works/pi-ai';

const load = () => import('../engine.mjs');
const tool = { type: 'function', function: {
  name: 'crm_update', description: 'Write verified CRM information',
  parameters: { type: 'object', properties: { note: { type: 'string' } }, required: ['note'] },
} };
const baseConfig = (extra = {}) => ({
  systemPrompt: 'Work only in the authorized project.',
  model: { id: 'qwen-plus', baseUrl: 'https://dashscope.aliyuncs.com/compatible-mode/v1' },
  messages: [{ role: 'user', content: 'Update the client then summarize.' }],
  tools: [tool], ...extra,
});
const usage = { input: 10, output: 4, cacheRead: 0, cacheWrite: 0, totalTokens: 14,
  cost: { input: 0, output: 0, cacheRead: 0, cacheWrite: 0, total: 0 } };
const assistant = (model, content, stopReason = 'stop') => ({
  role: 'assistant', api: model.api, provider: model.provider, model: model.id,
  content, stopReason, usage: structuredClone(usage), timestamp: Date.now(),
});
function scriptedStream(responder) {
  return (model, context, options) => {
    const stream = new AssistantMessageEventStream();
    queueMicrotask(async () => {
      try {
        const message = await responder(model, context, options);
        stream.push({ type: 'start', partial: { ...message, content: [] } });
        for (const block of message.content) if (block.type === 'text') {
          stream.push({ type: 'text_delta', contentIndex: 0, delta: block.text, partial: message });
        }
        stream.push(message.stopReason === 'aborted'
          ? { type: 'error', reason: 'aborted', error: message }
          : { type: 'done', reason: message.stopReason, message });
      } catch (error) {
        const message = assistant(model, [], 'error');
        message.errorMessage = error.message;
        stream.push({ type: 'error', reason: 'error', error: message });
      }
    });
    return stream;
  };
}

// Fails if tool schemas/messages are not normalized or tool writes run in parallel.
test('real Pi loop executes delegated business tools sequentially and reports usage/state', async () => {
  const { createEngine } = await load();
  const testApiKey = randomUUID();
  const events = [];
  const writes = [];
  let active = 0;
  const engine = createEngine(baseConfig(), {
    apiKey: testApiKey, send: event => events.push(event),
    streamFn: scriptedStream((model, context) => {
      const results = context.messages.filter(m => m.role === 'toolResult');
      return assistant(model, results.length ? [{ type: 'text', text: 'CRM updated.' }] : [
        { type: 'toolCall', id: 'crm-1', name: 'crm_update', arguments: { note: 'appointment next Wednesday' } },
        { type: 'toolCall', id: 'crm-2', name: 'crm_update', arguments: { note: 'prefers quiet homes' } },
      ], results.length ? 'stop' : 'toolUse');
    }),
    executeTool: async call => {
      assert.equal(active++, 0, 'tool writes must be sequential');
      await new Promise(resolve => setTimeout(resolve, 5));
      writes.push(call.arguments.note);
      active--;
      return { updated: true };
    },
  });
  const result = await engine.run();
  assert.deepEqual(writes, ['appointment next Wednesday', 'prefers quiet homes']);
  assert.equal(result.text, 'CRM updated.');
  assert.equal(result.turns, 2);
  assert.equal(result.toolCalls, 2);
  assert.equal(result.usage.totalTokens, 28);
  assert.deepEqual(events.filter(event=>event.type==='model_usage').map(event=>event.usage.input), [10,20]);
  assert.ok(result.state.messages.some(m => m.role === 'toolResult'));
  assert.ok(events.some(e => e.type === 'text_delta' && e.delta === 'CRM updated.'));
  assert.ok(!JSON.stringify(result).includes(testApiKey));
});

// Fails if steering/follow-up are not delivered through real Pi queues.
test('steering interrupts a multi-tool batch and follow-up is processed after the natural stop', async () => {
  const { createEngine } = await load();
  const receivedUsers = [];
  const writes = [];
  let engine;
  engine = createEngine(baseConfig(), {
    send: () => {}, apiKey: 'test',
    executeTool: async call => {
      writes.push(call.id);
      engine.steer('Change meeting place to the clubhouse.');
      engine.followUp('Also add the parking reminder.');
      return { updated: true };
    },
    streamFn: scriptedStream((model, context) => {
      const users = context.messages.filter(m => m.role === 'user').map(m => m.content);
      receivedUsers.push(users.at(-1));
      if (receivedUsers.length === 1) return assistant(model, [
        { type: 'toolCall', id: 'first', name: 'crm_update', arguments: { note: 'initial plan' } },
        { type: 'toolCall', id: 'stale', name: 'crm_update', arguments: { note: 'stale plan' } },
      ], 'toolUse');
      return assistant(model, [{ type: 'text', text: users.at(-1) }]);
    }),
  });
  const result = await engine.run();
  assert.deepEqual(writes, ['first']);
  assert.deepEqual(receivedUsers, [
    'Update the client then summarize.',
    'Change meeting place to the clubhouse.',
    'Also add the parking reminder.',
  ]);
  assert.equal(result.text, 'Also add the parking reminder.');
});

// Fails if user cancellation leaves a running provider/tool promise behind.
test('abort settles the real Pi run and yields an aborted snapshot', async () => {
  const { createEngine } = await load();
  let requestStarted;
  const started = new Promise(resolve => { requestStarted = resolve; });
  const engine = createEngine(baseConfig({ tools: [] }), {
    send: () => {}, apiKey: 'test', executeTool: async () => ({}),
    streamFn: scriptedStream(async (model, _context, options) => {
      requestStarted();
      await new Promise(resolve => options.signal.addEventListener('abort', resolve, { once: true }));
      return assistant(model, [], 'aborted');
    }),
  });
  const running = engine.run();
  await started;
  engine.abort();
  const result = await running;
  await engine.waitForIdle();
  assert.equal(result.aborted, true);
});

// Fails if restored transcript loses tool results or duplicates the current input.
test('Pi state restores conversation context with fresh authorized system and tools', async () => {
  const { createEngine } = await load();
  const first = createEngine(baseConfig({ tools: [] }), {
    send: () => {}, apiKey: 'test', executeTool: async () => ({}),
    streamFn: scriptedStream(model => assistant(model, [{ type: 'text', text: 'Saved.' }])),
  });
  const saved = await first.run();
  let contextSeen;
  const resumed = createEngine(baseConfig({
    state: saved.state, systemPrompt: 'Fresh project scope.', tools: [],
    messages: [{ role: 'user', content: 'What did we save?' }],
  }), {
    send: () => {}, apiKey: 'test', executeTool: async () => ({}),
    streamFn: scriptedStream((model, context) => {
      contextSeen = context.messages;
      return assistant(model, [{ type: 'text', text: 'Your CRM note.' }]);
    }),
  });
  const result = await resumed.run();
  assert.equal(contextSeen[0].content, 'Fresh project scope.');
  assert.equal(contextSeen.filter(m => m.role === 'user').length, 2);
  assert.ok(contextSeen.some(m => m.role === 'assistant' && m.content[0]?.text === 'Saved.'));
  assert.equal(result.text, 'Your CRM note.');
});

// Fails if omitted, zero, or null run limits silently fall back to the former caps.
test('unlimited runs complete beyond the former turn, tool, and accumulated-token caps', async t => {
  const { createEngine } = await load();
  for (const [name, limits] of [
    ['omitted', {}],
    ['zero', { maxTurns: 0, maxToolCalls: 0, maxTotalTokens: 0 }],
    ['null', { maxTurns: null, maxToolCalls: null, maxTotalTokens: null }],
    ['explicit finite values above the former caps', { maxTurns: 40, maxToolCalls: 100, maxTotalTokens: 2_000_000 }],
  ]) await t.test(name, async () => {
    let requests = 0;
    let writes = 0;
    const engine = createEngine(baseConfig(limits), {
      send: () => {}, apiKey: 'test', executeTool: async () => { writes++; return { updated: true }; },
      streamFn: scriptedStream(model => {
        requests++;
        const finished = requests === 34;
        const message = assistant(model, finished ? [{ type: 'text', text: 'All sixty-six operations completed.' }] : [
          { type: 'toolCall', id: `long-${requests}-a`, name: 'crm_update', arguments: { note: 'first authorized operation' } },
          { type: 'toolCall', id: `long-${requests}-b`, name: 'crm_update', arguments: { note: 'second authorized operation' } },
        ], finished ? 'stop' : 'toolUse');
        message.usage = { ...structuredClone(usage), input: 30_000, output: 10_000, totalTokens: 40_000 };
        return message;
      }),
    });
    const result = await engine.run();
    assert.equal(result.limitReached, null);
    assert.equal(result.turns, 34);
    assert.equal(result.toolCalls, 66);
    assert.equal(result.usage.totalTokens, 1_360_000);
    assert.equal(writes, 66);
    assert.equal(result.text, 'All sixty-six operations completed.');
  });
});

// Fails if malformed optional limits silently turn into unbounded execution.
test('run limits accept only omitted, null, zero, or positive safe integers', async () => {
  const { createEngine } = await load();
  for (const key of ['maxTurns', 'maxToolCalls', 'maxTotalTokens']) {
    for (const invalid of [-1, 1.5, '0', true, Infinity, Number.MAX_SAFE_INTEGER + 1]) {
      assert.throws(() => createEngine(baseConfig({ [key]: invalid }), {
        send: () => {}, apiKey: 'test', executeTool: async () => ({}),
        streamFn: () => { throw new Error('Invalid configuration must not request the model'); },
      }), error => error.code === 'INVALID_CONFIG');
    }
  }
});

// Fails if an omitted output limit keeps the former artificial 1,200-token cap.
test('per-call output defaults to the actual model maximum and respects explicit lower values', async t => {
  const { createEngine } = await load();
  for (const [name, outputLimit, expected] of [
    ['omitted', undefined, 8192], ['null', null, 8192], ['zero', 0, 8192],
    ['explicit lower', 2048, 2048], ['explicit above model capability', 12000, 8192],
  ]) await t.test(name, async () => {
    let requestLimit;
    const engine = createEngine(baseConfig({ tools: [], model: {
      id: 'qwen-plus', baseUrl: 'https://dashscope.aliyuncs.com/compatible-mode/v1', maxTokens: 8192,
    }, maxOutputTokens: outputLimit }), {
      send: () => {}, apiKey: 'test', executeTool: async () => ({}),
      streamFn: scriptedStream((model, _context, options) => {
        requestLimit = options.maxTokens;
        return assistant(model, [{ type: 'text', text: 'Complete response.' }]);
      }),
    });
    await engine.run();
    assert.equal(requestLimit, expected);
  });
});

// Fails if a model can keep writing beyond the bounded tool/turn budget.
test('tool and turn budgets bound an endlessly tool-calling model', async () => {
  const { createEngine } = await load();
  let writes = 0;
  const engine = createEngine(baseConfig({ maxToolCalls: 2, maxTurns: 3 }), {
    send: () => {}, apiKey: 'test', executeTool: async () => { writes++; return { updated: true }; },
    streamFn: scriptedStream(model => assistant(model, [
      { type: 'toolCall', id: `loop-${writes}`, name: 'crm_update', arguments: { note: 'loop' } },
    ], 'toolUse')),
  });
  const result = await engine.run();
  assert.equal(writes, 2);
  assert.equal(result.limitReached, 'max_tool_calls');
  assert.ok(result.turns <= 3);
});

// Fails if SDK errors or a malicious model output expose the environment credential.
test('model failure emits a safe error without credentials or provider payloads', async () => {
  const { createEngine } = await load();
  const testApiKey = randomUUID();
  const events = [];
  const engine = createEngine(baseConfig(), {
    send: e => events.push(e), apiKey: testApiKey, executeTool: async () => ({}),
    streamFn: scriptedStream(() => { throw new Error(`Authorization: Bearer ${testApiKey} raw payload`); }),
  });
  await assert.rejects(engine.run(), error => error.code === 'MODEL_REQUEST_FAILED' && !error.message.includes(testApiKey)
    && !error.message.includes(testApiKey.slice(0, 12)));
  assert.ok(!JSON.stringify(events).includes(testApiKey));
  assert.ok(!JSON.stringify(events).includes('raw payload'));
});

// Fails if clearing Pi queues leaves stale follow-up work or pending steering barriers.
test('queue controls clear pending work without exposing queued text', async () => {
  const { createEngine } = await load();
  let engine;
  const users = [];
  engine = createEngine(baseConfig(), {
    send: () => {}, apiKey: 'test',
    executeTool: async () => {
      engine.steer('private new meeting details');
      engine.followUp('private follow up details');
      assert.deepEqual(engine.queueStatus(), { hasQueuedMessages: true, steering: 1, followUp: 1 });
      engine.clearSteeringQueue();
      assert.deepEqual(engine.queueStatus(), { hasQueuedMessages: true, steering: 0, followUp: 1 });
      engine.clearFollowUpQueue();
      assert.deepEqual(engine.queueStatus(), { hasQueuedMessages: false, steering: 0, followUp: 0 });
      engine.steer('discarded');
      engine.followUp('discarded');
      engine.clearAllQueues();
      assert.deepEqual(engine.queueStatus(), { hasQueuedMessages: false, steering: 0, followUp: 0 });
      return { updated: true };
    },
    streamFn: scriptedStream((model, context) => {
      users.push(context.messages.filter(m => m.role === 'user').at(-1).content);
      return assistant(model, users.length === 1 ? [{ type: 'toolCall', id: 'q', name: 'crm_update', arguments: { note: 'save' } }]
        : [{ type: 'text', text: 'Saved.' }], users.length === 1 ? 'toolUse' : 'stop');
    }),
  });
  const result = await engine.run();
  assert.equal(result.text, 'Saved.');
  assert.deepEqual(users, ['Update the client then summarize.', 'Update the client then summarize.']);
});

// Fails if continuing a persisted non-assistant tail appends the current input twice.
test('continue uses the persisted transcript tail through Pi continue()', async () => {
  const { createEngine } = await load();
  let users;
  const engine = createEngine(baseConfig({ continue: true, tools: [], messages: [
    { role: 'user', content: 'Earlier input' }, { role: 'assistant', content: 'Earlier answer' },
  ] }), {
    send: () => {}, apiKey: 'test', executeTool: async () => ({}),
    streamFn: scriptedStream((model, context) => {
      users = context.messages.filter(m => m.role === 'user');
      return assistant(model, [{ type: 'text', text: 'Continued.' }]);
    }),
  });
  engine.followUp('Continue from the saved answer.');
  const result = await engine.run();
  assert.equal(users.length, 2);
  assert.equal(users.at(-1).content, 'Continue from the saved answer.');
  assert.equal(result.text, 'Continued.');
});

// Fails if a turn budget ends only after another model request has already begun.
test('turn limit ends a tool run before an extra provider request', async () => {
  const { createEngine } = await load();
  let requests = 0;
  let writes = 0;
  const engine = createEngine(baseConfig({ maxTurns: 1 }), {
    send: () => {}, apiKey: 'test', executeTool: async () => { writes++; return {}; },
    streamFn: scriptedStream(model => { requests++; return assistant(model, [
      { type: 'toolCall', id: 'bounded-turn', name: 'crm_update', arguments: { note: 'save' } },
    ], 'toolUse'); }),
  });
  const result = await engine.run();
  assert.equal(result.limitReached, 'max_turns');
  assert.equal(requests, 1);
  assert.equal(writes, 1);
});

// Fails if the model token budget permits further business writes after it is exhausted.
test('total token limit blocks tools after the budget is spent', async () => {
  const { createEngine } = await load();
  let writes = 0;
  const engine = createEngine(baseConfig({ maxTotalTokens: 10 }), {
    send: () => {}, apiKey: 'test', executeTool: async () => { writes++; return {}; },
    streamFn: scriptedStream(model => assistant(model, [
      { type: 'toolCall', id: 'bounded-token', name: 'crm_update', arguments: { note: 'save' } },
    ], 'toolUse')),
  });
  const result = await engine.run();
  assert.equal(result.limitReached, 'max_total_tokens');
  assert.equal(writes, 0);
});

// Fails if a provider can leak the environment credential by splitting it across text chunks.
test('streaming credential redaction covers secrets split across provider deltas', async () => {
  const { createEngine } = await load();
  const testApiKey = randomUUID();
  const splitAt = Math.floor(testApiKey.length / 2);
  const events = [];
  const engine = createEngine(baseConfig({ tools: [] }), {
    send: event => events.push(event), apiKey: testApiKey, executeTool: async () => ({}),
    streamFn: (model) => {
      const stream = new AssistantMessageEventStream();
      queueMicrotask(() => {
        const message = assistant(model, [{ type: 'text', text: `Saved ${testApiKey} safely` }]);
        stream.push({ type: 'start', partial: { ...message, content: [] } });
        for (const delta of [`Saved ${testApiKey.slice(0, splitAt)}`, `${testApiKey.slice(splitAt)} safely`]) stream.push({ type: 'text_delta', contentIndex: 0, delta, partial: message });
        stream.push({ type: 'done', reason: 'stop', message });
      });
      return stream;
    },
  });
  const result = await engine.run();
  assert.equal(events.filter(e => e.type === 'text_delta').map(e => e.delta).join(''), 'Saved [REDACTED] safely');
  assert.equal(result.text, 'Saved [REDACTED] safely');
});

// Fails if a queued follow-up grants business-write authority before Pi consumes its input.
test('input-consumed events identify controls only when Pi dequeues them', async () => {
  const { createEngine } = await load();
  const events = [];
  let engine;
  let requests = 0;
  engine = createEngine(baseConfig(), {
    send: event => events.push(event), apiKey: 'test',
    executeTool: async () => {
      engine.steer('Change the meeting.', 'control-steer');
      engine.followUp('Also update the contact.', 'control-follow');
      assert.equal(events.filter(e => e.type === 'input_consumed').length, 0);
      return {};
    },
    streamFn: scriptedStream(model => {
      requests++;
      return assistant(model, requests === 1
        ? [{ type: 'toolCall', id: 'initial', name: 'crm_update', arguments: { note: 'original request' } }]
        : [{ type: 'text', text: 'Done.' }], requests === 1 ? 'toolUse' : 'stop');
    }),
  });
  await engine.run();
  assert.deepEqual(events.filter(e => e.type === 'input_consumed'), [
    { type: 'input_consumed', id: 'control-steer', kind: 'steer' },
    { type: 'input_consumed', id: 'control-follow', kind: 'follow_up' },
  ]);
  assert.equal(requests, 3);
});

// Fails if a natural answer on the last allowed turn is falsely rejected as budget exhaustion.
test('a final answer on the exact turn budget completes successfully', async () => {
  const { createEngine } = await load();
  const engine = createEngine(baseConfig({ maxTurns: 1, tools: [] }), {
    send: () => {}, apiKey: 'test', executeTool: async () => ({}),
    streamFn: scriptedStream(model => assistant(model, [{ type: 'text', text: 'Finished within the budget.' }])),
  });
  const result = await engine.run();
  assert.equal(result.turns, 1);
  assert.equal(result.limitReached, null);
  assert.equal(result.text, 'Finished within the budget.');
});


// Fails if a provider's no-tool claim of completion bypasses a real business action.
test('one bounded Pi grounding retry rejects an initial no-tool completion claim', async () => {
  const { createEngine } = await load();
  let requests = 0;
  let writes = 0;
  const engine = createEngine(baseConfig(), {
    send: () => {}, apiKey: 'test', executeTool: async () => { writes++; return { updated: true }; },
    streamFn: scriptedStream((model) => {
      requests++;
      if (requests === 1) return assistant(model, [{ type: 'text', text: 'I saved it without a tool.' }]);
      if (requests === 2) return assistant(model, [{ type: 'toolCall', id: 'grounded', name: 'crm_update', arguments: { note: 'save the real note' } }], 'toolUse');
      return assistant(model, [{ type: 'text', text: 'Saved using the real business tool.' }]);
    }),
  });
  const result = await engine.run();
  assert.equal(writes, 1);
  assert.equal(requests, 3);
  assert.equal(result.text, 'Saved using the real business tool.');
});

const namedTool = name => ({ ...tool, function: { ...tool.function, name } });
// Fails if a read or one completed domain can satisfy a multi-domain write request.
test('required tool groups continue past read-only and partially completed final answers', async () => {
  const { createEngine } = await load();
  const executed = [];
  let requests = 0;
  const engine = createEngine(baseConfig({
    tools: [namedTool('get_property_details'), tool, namedTool('update_asset')],
    requiredToolGroups: [['crm_update'], ['update_asset']],
  }), {
    send: () => {}, apiKey: 'test',
    executeTool: async call => {
      executed.push(call.name);
      return { _execution: { name: call.name, applied: call.name !== 'get_property_details' } };
    },
    streamFn: scriptedStream((model, _context, options) => {
      requests++;
      const name = { 1: 'get_property_details', 3: 'crm_update', 5: 'update_asset' }[requests];
      if (name) {
        assert.equal(options.toolChoice, 'required');
        return assistant(model, [{ type: 'toolCall', id: `goal-${requests}`, name, arguments: { note: 'authorized' } }], 'toolUse');
      }
      return assistant(model, [{ type: 'text', text: requests === 6 ? 'Both actual writes completed.' : 'I claim everything is saved.' }]);
    }),
  });
  const result = await engine.run();
  assert.deepEqual(executed, ['get_property_details', 'crm_update', 'update_asset']);
  assert.equal(result.text, 'Both actual writes completed.');
  assert.deepEqual(result.incompleteRequiredGroups, []);
});

// Fails if a rejected native business write is counted as a successful operation.
test('only Python applied execution receipts satisfy a required operation', async () => {
  const { createEngine } = await load();
  let writes = 0;
  let requests = 0;
  const engine = createEngine(baseConfig({ requiredToolGroups: [['crm_update']] }), {
    send: () => {}, apiKey: 'test',
    executeTool: async () => { writes++; return { _execution: { name: 'crm_update', applied: writes > 1 }, ...(writes === 1 ? { error: 'rejected' } : {}) }; },
    streamFn: scriptedStream(model => {
      requests++;
      return assistant(model, [1, 3].includes(requests) ? [{ type: 'toolCall', id: `receipt-${requests}`, name: 'crm_update', arguments: { note: 'save' } }]
        : [{ type: 'text', text: 'Completed.' }], [1, 3].includes(requests) ? 'toolUse' : 'stop');
    }),
  });
  const result = await engine.run();
  assert.equal(writes, 2);
  assert.deepEqual(result.incompleteRequiredGroups, []);
});

// Fails if a successful native read must falsely report a database mutation to finish.
test('Python completed read receipts satisfy a required matching operation without a write', async () => {
  const { createEngine } = await load();
  let requests = 0;
  const toolChoices = [];
  const engine = createEngine(baseConfig({
    tools: [namedTool('match_customer_resources')],
    requiredToolGroups: [['match_customer_resources']], maxTurns: 3,
  }), {
    send: () => {}, apiKey: 'test',
    executeTool: async () => ({ properties: [{ id: 'property-1' }], assets: [{ id: 'asset-1' }],
      _execution: { name: 'match_customer_resources', applied: false, completed: true } }),
    streamFn: scriptedStream((model, _context, options) => {
      requests++;
      toolChoices.push(options.toolChoice);
      return assistant(model, requests === 1
        ? [{ type: 'toolCall', id: 'matching-read', name: 'match_customer_resources', arguments: { note: 'match this customer' } }]
        : [{ type: 'text', text: 'Found the matching property and material.' }], requests === 1 ? 'toolUse' : 'stop');
    }),
  });
  const result = await engine.run();
  assert.equal(requests, 2);
  assert.deepEqual(toolChoices, ['required', 'auto']);
  assert.equal(result.toolCalls, 1);
  assert.equal(result.limitReached, null);
  assert.deepEqual(result.incompleteRequiredGroups, []);
  assert.equal(result.text, 'Found the matching property and material.');
  const receipt = JSON.parse(result.state.messages.find(message => message.role === 'toolResult').content[0].text);
  assert.equal(receipt._execution.applied, false);
});

// Fails if a missing, false, mismatched, or failed read receipt bypasses the native proof requirement.
test('unsuccessful read receipts leave the required matching operation unfinished', async t => {
  const { createEngine } = await load();
  const cases = [
    ['absent', { properties: [] }],
    ['false', { _execution: { name: 'match_customer_resources', applied: false, completed: false } }],
    ['different tool', { _execution: { name: 'get_property_details', applied: false, completed: true } }],
    ['error', { error: 'matching failed', _execution: { name: 'match_customer_resources', applied: false, completed: true } }],
    ['thrown error', null],
  ];
  for (const [name, receipt] of cases) await t.test(name, async () => {
    let requests = 0;
    const toolChoices = [];
    const engine = createEngine(baseConfig({
      tools: [namedTool('match_customer_resources')],
      requiredToolGroups: [['match_customer_resources']], maxTurns: 2,
    }), {
      send: () => {}, apiKey: 'test',
      executeTool: async () => { if (receipt === null) throw new Error('matching failed'); return receipt; },
      streamFn: scriptedStream((model, _context, options) => {
        requests++;
        toolChoices.push(options.toolChoice);
        return assistant(model, requests === 1
          ? [{ type: 'toolCall', id: 'unsuccessful-read', name: 'match_customer_resources', arguments: { note: 'match this customer' } }]
          : [{ type: 'text', text: 'No confirmed match.' }], requests === 1 ? 'toolUse' : 'stop');
      }),
    });
    const result = await engine.run();
    assert.deepEqual(toolChoices, ['required', 'required']);
    assert.deepEqual(result.incompleteRequiredGroups, [['match_customer_resources']]);
    assert.equal(result.limitReached, 'max_turns');
  });
});

// Fails if a new human read correction retains old write goals or old executable tools.
test('human control changes requirements and tools only at actual consumption', async () => {
  const { createEngine } = await load();
  const reads = namedTool('get_property_details');
  const events = [];
  const executed = [];
  let requests = 0;
  let engine;
  engine = createEngine(baseConfig({ tools: [reads, tool], requiredToolGroups: [['crm_update']] }), {
    send: event => events.push(event), apiKey: 'test',
    executeTool: async call => {
      executed.push(call.name);
      if (executed.length === 1) engine.steer('Only inspect existing records.', 'read-correction', { requiredToolGroups: [], tools: [reads] });
      return { _execution: { name: call.name, applied: false } };
    },
    streamFn: scriptedStream((model, context) => {
      requests++;
      if (requests === 1) return assistant(model, [
        { type: 'toolCall', id: 'old-read', name: 'get_property_details', arguments: { note: 'inspect' } },
        { type: 'toolCall', id: 'old-write', name: 'crm_update', arguments: { note: 'stale write' } },
      ], 'toolUse');
      if (requests === 2) return assistant(model, [{ type: 'toolCall', id: 'new-read', name: 'get_property_details', arguments: { note: 'fresh inspect' } }], 'toolUse');
      return assistant(model, [{ type: 'text', text: 'Inspected without writing.' }]);
    }),
  });
  const result = await engine.run();
  assert.deepEqual(executed, ['get_property_details', 'get_property_details']);
  assert.deepEqual(result.incompleteRequiredGroups, []);
  assert.equal(result.text, 'Inspected without writing.');
  const declared = result.state.messages.filter(m => m.role === 'system').flatMap(m => m.toolsRemoved ?? []);
  assert.ok(declared.some(t => (typeof t === 'string' ? t : t.name) === 'crm_update'));
  assert.deepEqual(events.filter(e => e.type === 'input_consumed'), [{ type: 'input_consumed', id: 'read-correction', kind: 'steer' }]);
});

// Fails if budget exhaustion reports a false complete state for unmet business goals.
test('unfinished required operations are explicit in the terminal snapshot', async () => {
  const { createEngine } = await load();
  const engine = createEngine(baseConfig({ maxTurns: 2, requiredToolGroups: [['crm_update']] }), {
    send: () => {}, apiKey: 'test', executeTool: async () => ({ _execution: { name: 'crm_update', applied: false } }),
    streamFn: scriptedStream(model => assistant(model, [{ type: 'text', text: 'No actual write.' }])),
  });
  const result = await engine.run();
  assert.deepEqual(result.incompleteRequiredGroups, [['crm_update']]);
  assert.equal(result.limitReached, 'max_turns');
});

// Fails if an impossible required/loadout combination spends provider tokens before rejection.
test('impossible required tool groups are rejected before any provider call', async () => {
  const { createEngine } = await load();
  assert.throws(() => createEngine(baseConfig({ requiredToolGroups: [['update_asset']] }), {
    send: () => {}, apiKey: 'test', executeTool: async () => ({}),
    streamFn: () => { throw new Error('The model must not be called for an impossible operation'); },
  }), error => error.code === 'UNAVAILABLE_REQUIRED_TOOL');
});
