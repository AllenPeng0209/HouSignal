import { Agent } from '@earendil-works/pi-agent-core';
import { getCurrentTools, getToolStateChanges, toToolDeclaration } from '@earendil-works/pi-ai';
import { streamSimple } from '@earendil-works/pi-ai/api/openai-completions';

const ZERO_COST = { input: 0, output: 0, cacheRead: 0, cacheWrite: 0, total: 0 };
const emptyUsage = () => ({ input: 0, output: 0, cacheRead: 0, cacheWrite: 0, totalTokens: 0, cost: { ...ZERO_COST } });
const bounded = (value, fallback, maximum) => Number.isInteger(value) && value > 0 ? Math.min(value, maximum) : fallback;
// Scheduling limits are optional; a missing, null or zero value means no application cap.
const optionalLimit = value => {
  if (value === undefined || value === null || value === 0) return Infinity;
  if (!Number.isSafeInteger(value) || value < 0) throw new RuntimeError('INVALID_CONFIG', 'Run limits require a non-negative safe integer or null.');
  return value;
};
const requiredGroups = value => {
  if (value === undefined) return [];
  if (!Array.isArray(value) || value.length > 32 || value.some(group => !Array.isArray(group) || !group.length || group.length > 32 || group.some(name => typeof name !== 'string' || !name || name.length > 100))) {
    throw new RuntimeError('INVALID_CONFIG', 'Invalid required operation groups.');
  }
  return value.map(group => [...new Set(group)]);
};
const textContent = content => typeof content === 'string' ? content : (content ?? []).filter(b => b.type === 'text').map(b => b.text ?? '').join('\n');

export class RuntimeError extends Error {
  constructor(code, message) { super(message); this.name = 'RuntimeError'; this.code = code; }
}

/** Redaction is defense in depth; Python still owns field-level business-data policy. */
export function redactSecrets(value, secret) {
  if (typeof value === 'string') return secret ? value.split(secret).join('[REDACTED]') : value;
  if (Array.isArray(value)) return value.map(item => redactSecrets(item, secret));
  if (value && typeof value === 'object') {
    return Object.fromEntries(Object.entries(value)
      .filter(([key]) => !/^(api[_-]?key|authorization|access[_-]?token|refresh[_-]?token|password|secret)$/i.test(key))
      .map(([key, item]) => [key, redactSecrets(item, secret)]));
  }
  return value;
}

function makeModel(config = {}) {
  if (typeof config.id !== 'string' || !config.id.trim()) throw new RuntimeError('INVALID_CONFIG', 'A model id is required.');
  let url;
  try { url = new URL(config.baseUrl); } catch { throw new RuntimeError('INVALID_CONFIG', 'A valid model endpoint is required.'); }
  if (!['https:', 'http:'].includes(url.protocol) || url.username || url.password || url.search || url.hash) {
    throw new RuntimeError('INVALID_CONFIG', 'The model endpoint is invalid.');
  }
  return {
    id: config.id, name: config.id, api: 'openai-completions', provider: 'housignal-bailian',
    baseUrl: url.toString().replace(/\/$/, ''), reasoning: false, input: ['text'],
    cost: { input: 0, output: 0, cacheRead: 0, cacheWrite: 0 },
    contextWindow: bounded(config.contextWindow, 32768, 1_000_000),
    maxTokens: bounded(config.maxTokens, 4096, 32768),
    compat: { supportsStore: false, supportsDeveloperRole: false, supportsReasoningEffort: false,
      supportsStrictMode: false, maxTokensField: 'max_tokens', thinkingFormat: 'qwen' },
  };
}

function normalizeMessage(message, model) {
  if (!message || typeof message !== 'object') throw new RuntimeError('INVALID_CONFIG', 'Invalid conversation message.');
  const timestamp = Number.isFinite(message.timestamp) ? message.timestamp : Date.now();
  if (message.role === 'user') return { role: 'user', content: textContent(message.content), timestamp };
  if (message.role === 'assistant') {
    const content = [];
    const text = textContent(message.content);
    if (text) content.push({ type: 'text', text });
    for (const call of message.tool_calls ?? []) {
      let args;
      try { args = typeof call.function.arguments === 'string' ? JSON.parse(call.function.arguments) : call.function.arguments; }
      catch { throw new RuntimeError('INVALID_CONFIG', 'Invalid historical tool arguments.'); }
      content.push({ type: 'toolCall', id: call.id, name: call.function.name, arguments: args ?? {} });
    }
    if (Array.isArray(message.content)) for (const call of message.content.filter(b => b.type === 'toolCall')) {
      content.push({ type: 'toolCall', id: call.id, name: call.name, arguments: call.arguments ?? {} });
    }
    return { role: 'assistant', content, api: model.api, provider: model.provider, model: model.id,
      usage: message.usage ?? emptyUsage(), stopReason: content.some(b => b.type === 'toolCall') ? 'toolUse' : 'stop', timestamp };
  }
  if (message.role === 'tool' || message.role === 'toolResult') return {
    role: 'toolResult', toolCallId: message.tool_call_id ?? message.toolCallId,
    toolName: message.name ?? message.toolName ?? '', content: [{ type: 'text', text: textContent(message.content) }],
    isError: Boolean(message.isError), timestamp,
  };
  throw new RuntimeError('INVALID_CONFIG', 'Unsupported conversation message role.');
}

/**
 * Uses the actual Pi Agent for request/tool scheduling, queues, cancellation and state.
 * Only the provider stream is injectable, so tests retain the real Pi event loop.
 */
export function createEngine(config, { send, executeTool, apiKey = process.env.HOUSIGNAL_PI_API_KEY, streamFn = streamSimple } = {}) {
  if (!config || typeof send !== 'function' || typeof executeTool !== 'function') throw new RuntimeError('INVALID_CONFIG', 'Invalid runtime configuration.');
  if (!apiKey) throw new RuntimeError('CREDENTIALS_MISSING', 'The configured model credential is unavailable.');
  const model = makeModel(config.model);
  const maxTurns = optionalLimit(config.maxTurns);
  const maxToolCalls = optionalLimit(config.maxToolCalls);
  const maxTotalTokens = optionalLimit(config.maxTotalTokens);
  const maxOutputTokens = Math.min(optionalLimit(config.maxOutputTokens), model.maxTokens);
  const safe = value => redactSecrets(value, apiKey);
  const emit = event => send(safe(event));
  const history = [...(config.state?.messages ?? []), ...(config.messages ?? [])]
    .filter(message => message.role !== 'system')
    .map(message => normalizeMessage(safe(message), model));
  if (config.prompt) history.push({ role: 'user', content: String(config.prompt), timestamp: Date.now() });
  if (!history.length || (!config.continue && !['user', 'toolResult'].includes(history.at(-1).role))) {
    throw new RuntimeError('INVALID_CONFIG', 'The conversation must end with new user input or a tool result.');
  }
  const prompt = config.continue ? undefined : history.pop();
  let turns = 0;
  let toolCalls = 0;
  let limitReached = null;
  let aborted = false;
  let started = false;
  let runPromise;
  let streamingTextTail = '';
  let groundingRetries = 0;
  const steeringInputs = [];
  const followUpInputs = [];
  const usage = emptyUsage();
  let requiredToolGroups = requiredGroups(config.requiredToolGroups);
  const completedNames = new Set();
  const missingGroups = () => requiredToolGroups.filter(group => !group.some(name => completedNames.has(name)));
  const makeTools = schemas => (schemas ?? []).map(schema => {
    const definition = schema.function ?? schema;
    if (!definition?.name || !definition.parameters || definition.parameters.type !== 'object') {
      throw new RuntimeError('INVALID_CONFIG', 'Invalid tool declaration.');
    }
    return {
      name: definition.name, label: definition.name, description: definition.description ?? '',
      parameters: definition.parameters, executionMode: 'sequential',
      execute: async (id, args, signal) => {
        if (signal?.aborted) throw new RuntimeError('ABORTED', 'Agent run aborted.');
        toolCalls++;
        try {
          const result = safe(await executeTool({ id, name: definition.name, arguments: safe(args) }, signal));
          const execution = result?._execution;
          if (!result?.error && result?.isError !== true && execution?.name === definition.name
            && (execution.applied === true || execution.completed === true)) completedNames.add(definition.name);
          return { content: [{ type: 'text', text: JSON.stringify(result ?? null) }], details: {} };
        } catch {
          throw new Error(signal?.aborted ? 'Agent run aborted.' : 'Business tool execution failed.');
        }
      },
    };
  });
  let tools = makeTools(config.tools);
  const validateLoadout = (groups, loadout) => {
    const names = new Set(loadout.map(tool => tool.name));
    if (groups.some(group => !group.some(name => names.has(name)))) throw new RuntimeError('UNAVAILABLE_REQUIRED_TOOL', 'A required business operation is unavailable in the active tool loadout.');
  };
  validateLoadout(requiredToolGroups, tools);
  const agent = new Agent({
    initialState: { systemPrompt: String(config.systemPrompt ?? ''), model, tools, messages: history, thinkingLevel: 'off' },
    getApiKey: () => apiKey,
    streamFn: (selectedModel, context, options) => streamFn(selectedModel, context, { ...options, maxTokens: maxOutputTokens,
      toolChoice: tools.length && (toolCalls === 0 || missingGroups().length > 0) ? 'required' : 'auto' }),
    prepareRequest: async ({ context }) => {
      const changes = getToolStateChanges(getCurrentTools(context.messages), tools.map(toToolDeclaration));
      if (changes.toolsAdded.length || changes.toolsRemoved.length) {
        const patch = { role: 'system', content: '', ...changes, timestamp: Date.now() };
        agent.state.messages.push(patch);
        return { context: { ...context, tools: tools.slice(), messages: [...context.messages, patch] } };
      }
      return { context: { ...context, tools: tools.slice() } };
    },
    toolExecution: 'sequential', steeringMode: 'one-at-a-time', followUpMode: 'one-at-a-time',
    beforeToolCall: async () => {
      if (steeringInputs.length) return { block: true, reason: 'A newer user instruction superseded this pending tool call.' };
      if (toolCalls >= maxToolCalls) {
        limitReached = 'max_tool_calls';
        return { block: true, reason: 'The run reached its tool-call limit.', terminate: true };
      }
      if (usage.totalTokens >= maxTotalTokens) {
        limitReached = 'max_total_tokens';
        return { block: true, reason: 'The run reached its token limit.', terminate: true };
      }
    },
    finishTurn: async ({ message }) => {
      if (message.stopReason === 'error' || message.stopReason === 'aborted') return;
      const missing = missingGroups();
      const needsRequiredWrite = missing.length > 0 && steeringInputs.length === 0;
      const needsGrounding = tools.length > 0 && toolCalls === 0 && groundingRetries < 1;
      const needsAnotherTurn = needsRequiredWrite || needsGrounding || message.stopReason === 'toolUse' || agent.hasQueuedMessages();
      if (!limitReached && needsAnotherTurn && turns >= maxTurns) limitReached = 'max_turns';
      if (!limitReached && needsAnotherTurn && usage.totalTokens >= maxTotalTokens) limitReached = 'max_total_tokens';
      if (limitReached) return { action: 'end' };
      if (needsRequiredWrite && message.stopReason !== 'toolUse') {
        agent.steer({ role: 'system', content: '原始用户要求的以下操作尚无服务端成功回执，不能声称已保存：' + JSON.stringify(missing) + '。请继续调用相应业务工具，按真实结果完成这些原始操作；不可扩大用户授权。', timestamp: Date.now() });
        return { action: 'continue' };
      }
      if (needsGrounding) {
        groundingRetries++;
        agent.steer({ role: 'system', content: '上一轮没有实际调用业务工具，不能声称已读取、保存或安排。现在必须先调用最相关的业务工具，使用真实结果继续原始用户任务；不要只输出完成说明。', timestamp: Date.now() });
        return { action: 'continue' };
      }
    },
  });
  agent.subscribe(async event => {
    if (event.type === 'message_end' && event.message.role === 'user') {
      let consumed;
      if (steeringInputs[0]?.message === event.message) consumed = steeringInputs.shift();
      else if (followUpInputs[0]?.message === event.message) consumed = followUpInputs.shift();
      if (consumed) {
        requiredToolGroups = consumed.options.requiredToolGroups;
        completedNames.clear();
        if (consumed.options.tools) { tools = consumed.options.tools; agent.state.tools = tools; }
        if (consumed.id) await emit({ type: 'input_consumed', id: consumed.id, kind: consumed.kind });
      }
    }
    if (event.type === 'turn_start') turns++;
    if (event.type === 'message_end' && event.message.role === 'assistant') {
      if (streamingTextTail) { await emit({ type: 'text_delta', delta: safe(streamingTextTail) }); streamingTextTail = ''; }
      const current = event.message.usage;
      for (const field of ['input', 'output', 'cacheRead', 'cacheWrite', 'totalTokens']) usage[field] += Number(current?.[field]) || 0;
      for (const field of Object.keys(ZERO_COST)) usage.cost[field] += Number(current?.cost?.[field]) || 0;
      await emit({type:'model_usage',model:model.id,usage:structuredClone(usage)});
    }
    if (event.type === 'message_update' && event.assistantMessageEvent.type === 'text_delta') {
      const text = safe(streamingTextTail + event.assistantMessageEvent.delta);
      let retained = 0;
      for (let size = Math.min(apiKey.length - 1, text.length); size > 0; size--) {
        if (text.endsWith(apiKey.slice(0, size))) { retained = size; break; }
      }
      streamingTextTail = retained ? text.slice(-retained) : '';
      const delta = retained ? text.slice(0, -retained) : text;
      if (delta) await emit({ type: 'text_delta', delta });
    }
    if (['agent_start', 'agent_end', 'turn_start', 'turn_end'].includes(event.type)) {
      await emit({ type: 'event', event: event.type, turns });
    }
    if (event.type === 'tool_execution_start' || event.type === 'tool_execution_end') {
      await emit({ type: 'event', event: event.type, id: event.toolCallId, name: event.toolName,
        ...(event.type === 'tool_execution_end' ? { isError: Boolean(event.isError) } : {}) });
    }
  });
  const userMessage = text => {
    if (typeof text !== 'string' || !text.trim()) throw new RuntimeError('INVALID_COMMAND', 'A control message requires text.');
    return { role: 'user', content: safe(text), timestamp: Date.now() };
  };
  const controlId = id => {
    if (id !== undefined && (typeof id !== 'string' || !id || id.length > 256)) throw new RuntimeError('INVALID_COMMAND', 'Invalid control identifier.');
    return id;
  };
  return {
    run() {
      if (started) throw new RuntimeError('ALREADY_STARTED', 'This runtime already processed a request.');
      started = true;
      runPromise = (async () => {
        try {
          if (config.continue) await agent.continue();
          else await agent.prompt(prompt);
          await agent.waitForIdle();
        } catch {
          if (!aborted) throw new RuntimeError('MODEL_REQUEST_FAILED', 'The model request could not be completed.');
        }
        const lastAssistant = agent.state.messages.filter(message => message.role === 'assistant').at(-1);
        if (!aborted && lastAssistant?.stopReason === 'error') {
          throw new RuntimeError('MODEL_REQUEST_FAILED', 'The model request could not be completed.');
        }
        return safe({ type: 'done', text: textContent(lastAssistant?.content), usage, model: model.id,
          turns, toolCalls, incompleteRequiredGroups: missingGroups(), aborted: aborted || lastAssistant?.stopReason === 'aborted', limitReached,
          state: { messages: agent.state.messages.map(message => {
            const { errorMessage, diagnostics, ...rest } = message;
            return { ...rest, ...(Array.isArray(rest.content) ? { content: rest.content.filter(block => block.type !== 'thinking') } : {}) };
          }) } });
      })();
      return runPromise;
    },
    steer(text, id, options = {}) { const message = userMessage(text); const intent = { requiredToolGroups: requiredGroups(options.requiredToolGroups), ...(options.tools ? { tools: makeTools(options.tools) } : {}) }; validateLoadout(intent.requiredToolGroups, intent.tools ?? tools); steeringInputs.push({ message, id: controlId(id), kind: 'steer', options: intent }); agent.steer(message); },
    followUp(text, id, options = {}) { const message = userMessage(text); const intent = { requiredToolGroups: requiredGroups(options.requiredToolGroups), ...(options.tools ? { tools: makeTools(options.tools) } : {}) }; validateLoadout(intent.requiredToolGroups, intent.tools ?? tools); followUpInputs.push({ message, id: controlId(id), kind: 'follow_up', options: intent }); agent.followUp(message); },
    clearSteeringQueue() { steeringInputs.length = 0; agent.clearSteeringQueue(); },
    clearFollowUpQueue() { followUpInputs.length = 0; agent.clearFollowUpQueue(); },
    clearAllQueues() { steeringInputs.length = 0; followUpInputs.length = 0; agent.clearAllQueues(); },
    queueStatus() { return { hasQueuedMessages: agent.hasQueuedMessages(), steering: steeringInputs.length, followUp: followUpInputs.length }; },
    abort() { aborted = true; agent.abort(); },
    async waitForIdle() { await agent.waitForIdle(); },
  };
}
