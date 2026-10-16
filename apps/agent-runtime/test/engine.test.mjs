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
          { type: 'toolCall', id: `long-${requests}-a`, name: 'crm_update', arguments: { note: `first authorized operation ${requests}` } },
          { type: 'toolCall', id: `long-${requests}-b`, name: 'crm_update', arguments: { note: `second authorized operation ${requests}` } },
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

// Fails if a missing write can force an unbounded loop over an unchanged read result.
test('repeated identical read outcomes stop an unfinished required operation without a global cap', async () => {
  const { createEngine } = await load();
  let requests = 0;
  const executed = [];
  const engine = createEngine(baseConfig({
    tools: [namedTool('get_property_details'), tool], requiredToolGroups: [['crm_update']],
  }), {
    send: () => {}, apiKey: 'test',
    executeTool: async call => {
      executed.push(call.name);
      if (call.name === 'crm_update') return { _execution: { name: call.name, applied: true } };
      // JSON object insertion order is transport noise, not progress.
      return executed.length % 2
        ? { property: { id: 'property-1', state: 'unchanged' }, _execution: { name: call.name, applied: false } }
        : { _execution: { applied: false, name: call.name }, property: { state: 'unchanged', id: 'property-1' } };
    },
    streamFn: scriptedStream(model => {
      requests++;
      // The escape path makes the regression fail promptly even before the guard exists.
      if (executed.includes('crm_update')) return assistant(model, [{ type: 'text', text: 'The write completed.' }]);
      const name = requests > 6 ? 'crm_update' : 'get_property_details';
      const args = requests % 2 ? { note: 'inspect', page: 1 } : { page: 1, note: 'inspect' };
      return assistant(model, [{ type: 'toolCall', id: `unchanged-${requests}`, name, arguments: args }], 'toolUse');
    }),
  });
  const result = await engine.run();
  assert.equal(result.limitReached, 'no_progress');
  assert.deepEqual(result.incompleteRequiredGroups, [['crm_update']]);
  assert.deepEqual(executed, ['get_property_details', 'get_property_details', 'get_property_details', 'get_property_details']);
  assert.equal(requests, 4); // One new server result, followed by three turns without new evidence.
  assert.equal(result.aborted, false);
});

// Fails if the guard treats changing arguments/results as a general turn or tool cap.
test('changing tool arguments or results can advance a long run while a required write remains pending', async t => {
  const { createEngine } = await load();
  for (const changing of ['arguments', 'result']) await t.test(changing, async () => {
    let requests = 0;
    let calls = 0;
    const engine = createEngine(baseConfig({
      tools: [namedTool('get_property_details'), tool], requiredToolGroups: [['crm_update']],
    }), {
      send: () => {}, apiKey: 'test',
      executeTool: async call => {
        calls++;
        return call.name === 'crm_update' ? { _execution: { name: call.name, applied: true } }
          : { progress: changing === 'result' ? calls : 1, _execution: { name: call.name, applied: false } };
      },
      streamFn: scriptedStream(model => {
        requests++;
        if (requests > 36) throw new Error('The progressing run did not finish after its confirmed write.');
        if (requests === 36) return assistant(model, [{ type: 'text', text: 'Completed after the progressing reads.' }]);
        return assistant(model, [{ type: 'toolCall', id: `progress-${requests}`,
          name: requests === 35 ? 'crm_update' : 'get_property_details',
          arguments: { note: changing === 'arguments' ? `inspect item ${requests}` : 'poll current status' },
        }], 'toolUse');
      }),
    });
    const result = await engine.run();
    assert.equal(result.limitReached, null);
    assert.equal(result.toolCalls, 35);
    assert.equal(result.turns, 36);
    assert.deepEqual(result.incompleteRequiredGroups, []);
  });
});

// Fails if repeated rejected or thrown tool outcomes continue spending provider requests.
test('identical failed writes and thrown business-tool failures stop with explicit unfinished goals', async t => {
  const { createEngine } = await load();
  for (const failure of ['receipt', 'exception']) await t.test(failure, async () => {
    let calls = 0;
    let requests = 0;
    const engine = createEngine(baseConfig({ requiredToolGroups: [['crm_update']] }), {
      send: () => {}, apiKey: 'test',
      executeTool: async () => {
        calls++;
        // A finite escape keeps a missing guard from hanging the regression suite.
        if (calls > 6) return { _execution: { name: 'crm_update', applied: true } };
        if (failure === 'exception') throw new Error('private backend failure');
        return { error: 'The update was rejected.', _execution: { name: 'crm_update', applied: false } };
      },
      streamFn: scriptedStream(model => {
        requests++;
        return calls > 6 ? assistant(model, [{ type: 'text', text: 'Escaped.' }])
          : assistant(model, [{ type: 'toolCall', id: `failed-${requests}`, name: 'crm_update', arguments: { note: 'save' } }], 'toolUse');
      }),
    });
    const result = await engine.run();
    assert.equal(result.limitReached, 'no_progress');
    assert.deepEqual(result.incompleteRequiredGroups, [['crm_update']]);
    assert.equal(result.toolCalls, 3);
    assert.equal(requests, 3);
    assert.ok(!JSON.stringify(result).includes('private backend failure'));
  });
});

// Fails if an unchanged tool streak ends a batch that actually completes a required write.
test('required-operation progress in a tool batch clears the unchanged-outcome streak', async () => {
  const { createEngine } = await load();
  let requests = 0;
  const engine = createEngine(baseConfig({
    tools: [namedTool('get_property_details'), tool, namedTool('update_asset')],
    requiredToolGroups: [['crm_update'], ['update_asset']],
  }), {
    send: () => {}, apiKey: 'test',
    executeTool: async call => ({ _execution: { name: call.name, applied: call.name !== 'get_property_details' } }),
    streamFn: scriptedStream(model => {
      requests++;
      if (requests > 3) return assistant(model, [{ type: 'text', text: 'Both required writes completed.' }]);
      const names = requests === 1
        ? ['get_property_details', 'get_property_details', 'get_property_details', 'crm_update']
        : requests === 2 ? ['get_property_details', 'get_property_details'] : ['update_asset'];
      return assistant(model, names.map((name, index) => ({
        type: 'toolCall', id: `batch-${requests}-${index}`, name, arguments: { note: 'authorized' },
      })), 'toolUse');
    }),
  });
  const result = await engine.run();
  assert.equal(result.limitReached, null);
  assert.deepEqual(result.incompleteRequiredGroups, []);
  assert.equal(result.toolCalls, 7);
});

// Fails if a new consumed human instruction inherits the previous instruction's no-progress streak.
test('human steering consumption resets an unfinished operation no-progress streak', async () => {
  const { createEngine } = await load();
  let engine;
  let calls = 0;
  let requests = 0;
  const events = [];
  const reads = namedTool('get_property_details');
  engine = createEngine(baseConfig({ tools: [reads, tool], requiredToolGroups: [['crm_update']] }), {
    send: event => events.push(event), apiKey: 'test',
    executeTool: async call => {
      calls++;
      if (calls === 3) engine.steer('Complete the write after one fresh check.', 'fresh-goal', {
        requiredToolGroups: [['crm_update']], tools: [reads, tool],
      });
      return { _execution: { name: call.name, applied: call.name === 'crm_update' } };
    },
    streamFn: scriptedStream(model => {
      requests++;
      if (requests > 8) throw new Error('The human correction did not finish after its confirmed write.');
      if (requests > 5) return assistant(model, [{ type: 'text', text: 'Completed the new instruction.' }]);
      return assistant(model, [{ type: 'toolCall', id: `human-${requests}`,
        name: requests === 5 ? 'crm_update' : 'get_property_details', arguments: { note: 'inspect' },
      }], 'toolUse');
    }),
  });
  const result = await engine.run();
  assert.equal(result.limitReached, null);
  assert.deepEqual(result.incompleteRequiredGroups, []);
  assert.equal(result.toolCalls, 5);
  assert.deepEqual(events.filter(event => event.type === 'input_consumed'), [{ type: 'input_consumed', id: 'fresh-goal', kind: 'steer' }]);
});

// Fails if a future follow-up disables the guard while Pi is still enforcing the current required operation.
test('an unconsumed follow-up does not keep an unchanged unfinished operation running', async () => {
  const { createEngine } = await load();
  const reads = namedTool('get_property_details');
  let engine;
  let requests = 0;
  let calls = 0;
  engine = createEngine(baseConfig({ tools: [reads, tool], requiredToolGroups: [['crm_update']] }), {
    send: () => {}, apiKey: 'test',
    executeTool: async call => {
      calls++;
      if (calls === 3) engine.followUp('Summarize after the current operation.', 'later-summary');
      return { _execution: { name: call.name, applied: call.name === 'crm_update' } };
    },
    streamFn: scriptedStream(model => {
      requests++;
      if (requests > 7) return assistant(model, [{ type: 'text', text: 'Escaped.' }]);
      return assistant(model, [{ type: 'toolCall', id: `queued-${requests}`,
        name: requests === 7 ? 'crm_update' : 'get_property_details', arguments: { note: 'inspect' },
      }], 'toolUse');
    }),
  });
  const result = await engine.run();
  assert.equal(result.limitReached, 'no_progress');
  assert.deepEqual(result.incompleteRequiredGroups, [['crm_update']]);
  assert.equal(result.toolCalls, 4);
  assert.deepEqual(engine.queueStatus(), { hasQueuedMessages: true, steering: 0, followUp: 1 });
});

// Fails if gateway observation timestamps let an unchanged read bypass no-progress detection.
test('gateway observation timestamps do not turn unchanged business results into progress', async t => {
  const { createEngine } = await load();
  for (const timestampLocation of ['envelope', 'source', 'both']) await t.test(timestampLocation, async () => {
    let calls = 0;
    let requests = 0;
    const engine = createEngine(baseConfig({
      tools: [namedTool('get_property_details'), tool], requiredToolGroups: [['crm_update']],
    }), {
      send: () => {}, apiKey: 'test',
      executeTool: async call => {
        calls++;
        if (call.name === 'crm_update') return { _execution: { name: call.name, applied: true } };
        const observedAt = `2026-10-15T06:00:${String(calls).padStart(2, '0')}+00:00`;
        return {
          data: { property: { id: 'property-1', status: 'verified', updated_at: '2026-10-14T00:00:00+00:00' } },
          sources: [{ kind: 'property', id: 'property-1', ref: 'S1', label: 'Property details', revision: 2,
            status: 'verified', valid: true, observed_at: timestampLocation === 'envelope' ? 'unchanged' : observedAt }],
          observed_at: timestampLocation === 'source' ? 'unchanged' : observedAt,
          _execution: { name: call.name, applied: false, completed: true },
        };
      },
      streamFn: scriptedStream(model => {
        requests++;
        if (requests > 7) return assistant(model, [{ type: 'text', text: 'Escaped.' }]);
        return assistant(model, [{ type: 'toolCall', id: `observation-${requests}`,
          name: requests === 7 ? 'crm_update' : 'get_property_details', arguments: { note: 'inspect property-1' },
        }], 'toolUse');
      }),
    });
    const result = await engine.run();
    assert.equal(result.limitReached, 'no_progress');
    assert.deepEqual(result.incompleteRequiredGroups, [['crm_update']]);
    assert.equal(result.toolCalls, 4);
    const lastRead = JSON.parse(result.state.messages.filter(message => message.role === 'toolResult').at(-1).content[0].text);
    // Fingerprinting must not remove provenance metadata from the model-visible transcript.
    assert.equal(lastRead.observed_at, timestampLocation === 'source' ? 'unchanged' : '2026-10-15T06:00:04+00:00');
    assert.equal(lastRead.sources[0].observed_at, timestampLocation === 'envelope' ? 'unchanged' : '2026-10-15T06:00:04+00:00');
  });
});

// Fails if provenance normalization erases business updates or source identity/revision changes.
test('gateway business data and source changes remain progress during long required-operation runs', async t => {
  const { createEngine } = await load();
  for (const field of ['data.updated_at', 'data.status', 'data.progress', 'data.observed_at', 'source.id', 'source.revision', 'source.status']) {
    await t.test(field, async () => {
      let calls = 0;
      let requests = 0;
      const engine = createEngine(baseConfig({
        tools: [namedTool('get_property_details'), tool], requiredToolGroups: [['crm_update']],
      }), {
        send: () => {}, apiKey: 'test',
        executeTool: async call => {
          calls++;
          if (call.name === 'crm_update') return { _execution: { name: call.name, applied: true } };
          const result = {
            data: { updated_at: 'unchanged', status: 'pending', progress: 0, observed_at: 'business observation' },
            sources: [{ kind: 'property', id: 'property-1', ref: 'S1', revision: 1, status: 'verified', observed_at: `observation-${calls}` }],
            observed_at: `observation-${calls}`,
            _execution: { name: call.name, applied: false, completed: true },
          };
          const [location, key] = field.split('.');
          (location === 'data' ? result.data : result.sources[0])[key] = calls;
          return result;
        },
        streamFn: scriptedStream(model => {
          requests++;
          if (requests > 36) throw new Error('Business progress must permit the confirmed write to finish.');
          if (requests === 36) return assistant(model, [{ type: 'text', text: 'Completed after the changing gateway results.' }]);
          return assistant(model, [{ type: 'toolCall', id: `business-progress-${requests}`,
            name: requests === 35 ? 'crm_update' : 'get_property_details', arguments: { note: 'inspect current business state' },
          }], 'toolUse');
        }),
      });
      const result = await engine.run();
      assert.equal(result.limitReached, null);
      assert.equal(result.toolCalls, 35);
      assert.equal(result.turns, 36);
      assert.deepEqual(result.incompleteRequiredGroups, []);
    });
  }
});

// Fails if alternating old evidence, Pi validation failures, or tool-free claims can force endless required retries.
test('three turns without new server evidence stop repeated cycles and calls rejected by Pi', async t => {
  const { createEngine } = await load();
  for (const mode of ['alternating', 'invalid arguments', 'unknown tool', 'text only']) await t.test(mode, async () => {
    let requests = 0;
    let calls = 0;
    const reads = [namedTool('get_property_details'), namedTool('get_customer')];
    const engine = createEngine(baseConfig({ tools: [...reads, tool], requiredToolGroups: [['crm_update']] }), {
      send: () => {}, apiKey: 'test',
      executeTool: async call => {
        calls++;
        return call.name === 'crm_update' ? { _execution: { name: call.name, applied: true } }
          : { data: { state: 'unchanged' }, sources: [], _execution: { name: call.name, applied: false } };
      },
      streamFn: scriptedStream(model => {
        requests++;
        if (requests > 9) return assistant(model, [{ type: 'text', text: 'Escaped.' }]);
        if (requests === 9) return assistant(model, [{ type: 'toolCall', id: 'escape-write', name: 'crm_update', arguments: { note: 'finish' } }], 'toolUse');
        if (mode === 'text only') return assistant(model, [{ type: 'text', text: 'I claim the write finished.' }]);
        return assistant(model, [{ type: 'toolCall', id: `stalled-${requests}`,
          name: mode === 'unknown tool' ? 'not_a_real_tool' : mode === 'alternating' ? reads[(requests - 1) % 2].function.name : 'crm_update',
          arguments: mode === 'invalid arguments' ? {} : { note: 'inspect' },
        }], 'toolUse');
      }),
    });
    const result = await engine.run();
    assert.equal(result.limitReached, 'no_progress');
    assert.deepEqual(result.incompleteRequiredGroups, [['crm_update']]);
    assert.equal(requests, mode === 'alternating' ? 5 : 3);
    assert.equal(calls, mode === 'alternating' ? 5 : 0);
  });
});

// Fails if a confirmed background wait keeps buying model turns or is presented as a successful final answer.
test('a server deferred execution receipt yields immediately without polling or claiming success', async t => {
  const { createEngine } = await load();
  for (const status of ['queued', 'pending', 'running']) await t.test(status, async () => {
    let requests = 0;
    const engine = createEngine(baseConfig({ tools: [namedTool('get_production_job'), tool], requiredToolGroups: [['crm_update']] }), {
      send: () => {}, apiKey: 'test',
      executeTool: async () => ({ data: { job_id: 'production-1', status }, sources: [],
        _execution: { name: 'get_production_job', applied: false,
          deferred: { jobId: 'production-1', status, continuationJobId: 'continuation-1' } } }),
      streamFn: scriptedStream(model => {
        requests++;
        return requests > 5 ? assistant(model, [{ type: 'text', text: 'Escaped.' }], 'error')
          : assistant(model, [{ type: 'text', text: 'The production is complete.' },
            { type: 'toolCall', id: `pending-${requests}`, name: 'get_production_job', arguments: { note: 'check job' } }], 'toolUse');
      }),
    });
    const result = await engine.run();
    assert.equal(requests, 1);
    assert.equal(result.limitReached, 'waiting_for_external');
    assert.deepEqual(result.deferred, { jobId: 'production-1', status, continuationJobId: 'continuation-1' });
    assert.deepEqual(result.incompleteRequiredGroups, [['crm_update']]);
    assert.equal(result.text, '');
    assert.equal(result.aborted, false);
  });
});

// Fails if yielding loses the original obligation or requires paid polling to observe a finished dependency.
test('a fresh engine resumes an externally completed dependency and finishes the original required write', async () => {
  const { createEngine } = await load();
  const tools = [namedTool('get_production_job'), tool];
  let waitingRequests = 0;
  const waitingEngine = createEngine(baseConfig({ tools, requiredToolGroups: [['crm_update']] }), {
    send: () => {}, apiKey: 'test',
    executeTool: async () => ({ data: { job_id: 'production-1', status: 'running' }, sources: [],
      _execution: { name: 'get_production_job', applied: false,
        deferred: { jobId: 'production-1', status: 'running', continuationJobId: 'continuation-1' } } }),
    streamFn: scriptedStream(model => {
      waitingRequests++;
      return assistant(model, [{ type: 'toolCall', id: `wait-${waitingRequests}`, name: 'get_production_job', arguments: { note: 'check real job' } }], 'toolUse');
    }),
  });
  const waiting = await waitingEngine.run();
  assert.equal(waitingRequests, 1);
  assert.equal(waiting.limitReached, 'waiting_for_external');
  assert.deepEqual(waiting.incompleteRequiredGroups, [['crm_update']]);

  // The persistent Python dependency scheduler supplies this new run after the actual job finishes.
  let resumedRequests = 0;
  const executed = [];
  const resumedEngine = createEngine(baseConfig({ tools, requiredToolGroups: waiting.incompleteRequiredGroups,
    state: waiting.state, messages: [{ role: 'user', content: 'The real dependency finished; continue the original authorized write.' }],
  }), {
    send: () => {}, apiKey: 'test',
    executeTool: async call => {
      executed.push(call.name);
      return call.name === 'get_production_job'
        ? { data: { job_id: 'production-1', status: 'succeeded', artifacts: [{ id: 'artifact-1' }] }, sources: [],
          _execution: { name: call.name, applied: false } }
        : { data: { saved: true }, sources: [], _execution: { name: call.name, applied: true } };
    },
    streamFn: scriptedStream(model => {
      resumedRequests++;
      if (resumedRequests === 3) return assistant(model, [{ type: 'text', text: 'The actual artifact was checked and the original write saved.' }]);
      return assistant(model, [{ type: 'toolCall', id: `resume-${resumedRequests}`,
        name: resumedRequests === 1 ? 'get_production_job' : 'crm_update', arguments: { note: 'original authorized action' } }], 'toolUse');
    }),
  });
  const result = await resumedEngine.run();
  assert.deepEqual(executed, ['get_production_job', 'crm_update']);
  assert.equal(result.limitReached, null);
  assert.equal(result.deferred, null);
  assert.deepEqual(result.incompleteRequiredGroups, []);
  assert.equal(result.text, 'The actual artifact was checked and the original write saved.');
});

// Fails if arbitrary business statuses, rejected results, or another tool's receipt can yield the runtime.
test('only a valid matching server execution receipt can defer a run', async t => {
  const { createEngine } = await load();
  const waiting = { jobId: 'production-1', status: 'running' };
  for (const [name, receipt] of [
    ['business status only', { data: { status: 'running', deferred: waiting }, sources: [] }],
    ['different tool', { _execution: { name: 'crm_update', deferred: waiting } }],
    ['failed result', { error: 'rejected', _execution: { name: 'get_production_job', deferred: waiting } }],
    ['missing job id', { _execution: { name: 'get_production_job', deferred: { status: 'running' } } }],
    ['terminal job', { _execution: { name: 'get_production_job', deferred: { jobId: 'production-1', status: 'succeeded' } } }],
  ]) await t.test(name, async () => {
    let requests = 0;
    const engine = createEngine(baseConfig({ tools: [namedTool('get_production_job')] }), {
      send: () => {}, apiKey: 'test', executeTool: async () => receipt,
      streamFn: scriptedStream(model => {
        requests++;
        return requests === 1 ? assistant(model, [{ type: 'toolCall', id: 'not-deferred', name: 'get_production_job', arguments: { note: 'inspect' } }], 'toolUse')
          : assistant(model, [{ type: 'text', text: 'Reported the actual record.' }]);
      }),
    });
    const result = await engine.run();
    assert.equal(requests, 2);
    assert.equal(result.limitReached, null);
    assert.equal(result.deferred, null);
  });
});

// Fails if tool calls later in the same model batch still execute after the gateway has handed off to a dependency.
test('a deferred receipt prevents later tool execution in the current batch', async () => {
  const { createEngine } = await load();
  const executed = [];
  const engine = createEngine(baseConfig({ tools: [namedTool('get_production_job'), tool], requiredToolGroups: [['crm_update']] }), {
    send: () => {}, apiKey: 'test',
    executeTool: async call => {
      executed.push(call.name);
      return call.name === 'get_production_job'
        ? { _execution: { name: call.name, applied: false, deferred: { jobId: 'production-1', status: 'running' } } }
        : { _execution: { name: call.name, applied: true } };
    },
    streamFn: scriptedStream(model => assistant(model, [
      { type: 'toolCall', id: 'dependency-wait', name: 'get_production_job', arguments: { note: 'check' } },
      { type: 'toolCall', id: 'after-dependency', name: 'crm_update', arguments: { note: 'must wait for the dependency' } },
    ], 'toolUse')),
  });
  const result = await engine.run();
  assert.deepEqual(executed, ['get_production_job']);
  assert.equal(result.limitReached, 'waiting_for_external');
  assert.deepEqual(result.incompleteRequiredGroups, [['crm_update']]);
});
