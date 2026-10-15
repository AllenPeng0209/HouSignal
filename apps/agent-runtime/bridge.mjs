#!/usr/bin/env node
import { createInterface } from 'node:readline';
import { createEngine, RuntimeError, redactSecrets } from './engine.mjs';

const secret = process.env.HOUSIGNAL_PI_API_KEY;
const input = createInterface({ input: process.stdin, crlfDelay: Infinity });
const pending = new Map();
let engine;
let started = false;
let finished = false;
let output = Promise.resolve();

// One sequenced writer keeps NDJSON intact even when provider and control events overlap.
function emit(event) {
  const line = JSON.stringify(redactSecrets(event, secret)) + '\n';
  output = output.then(() => new Promise((resolve, reject) => {
    process.stdout.write(line, error => error ? reject(error) : resolve());
  }));
  return output;
}
function safeError(error) {
  return error instanceof RuntimeError
    ? { type: 'error', code: error.code, message: error.message }
    : { type: 'error', code: 'RUNTIME_FAILED', message: 'The agent runtime could not complete the request.' };
}
async function finish(code) {
  if (finished) return;
  finished = true;
  for (const waiter of pending.values()) waiter.reject(new RuntimeError('ABORTED', 'Agent run ended.'));
  pending.clear();
  input.close();
  process.stdin.destroy();
  try { await output; } catch { /* A disconnected parent cannot receive diagnostics. */ }
  process.exitCode = code;
}
function executeTool(call, signal) {
  return new Promise((resolve, reject) => {
    const clean = () => { clearTimeout(timer); signal?.removeEventListener('abort', abort); pending.delete(call.id); };
    const abort = () => { clean(); reject(new RuntimeError('ABORTED', 'Agent run aborted.')); };
    const timer = setTimeout(() => {
      clean(); reject(new RuntimeError('TOOL_TIMEOUT', 'The business tool did not respond in time.'));
    }, 120000);
    timer.unref();
    if (signal?.aborted) { abort(); return; }
    signal?.addEventListener('abort', abort, { once: true });
    pending.set(call.id, {
      resolve(result) { clean(); resolve(result); },
      reject(error) { clean(); reject(error); },
    });
    emit({ type: 'tool_call', ...call }).catch(() => abort());
  });
}
async function start(command) {
  if (started) throw new RuntimeError('ALREADY_STARTED', 'This runtime already processed a request.');
  started = true;
  const config = { ...(command.config ?? command), ...(command.type === 'continue' ? { continue: true } : {}) };
  engine = createEngine(config, { send: emit, executeTool, apiKey: secret });
  try {
    // The gateway owns business history and never consumes Pi's restorable state.
    // Keep that in-process API available without echoing a growing transcript on NDJSON.
    const { state: _state, ...summary } = await engine.run();
    await emit(summary);
    await finish(0);
  } catch (error) {
    await emit(safeError(error));
    await finish(1);
  }
}
function control(command) {
  if (!engine || finished) throw new RuntimeError('NOT_RUNNING', 'There is no active agent run.');
  const actions = {
    steer: () => engine.steer(command.text, command.id, { requiredToolGroups: command.requiredToolGroups, tools: command.tools }),
    follow_up: () => engine.followUp(command.text, command.id, { requiredToolGroups: command.requiredToolGroups, tools: command.tools }),
    abort: () => engine.abort(),
    clear_steering_queue: () => engine.clearSteeringQueue(),
    clear_follow_up_queue: () => engine.clearFollowUpQueue(),
    clear_all_queues: () => engine.clearAllQueues(),
  };
  if (command.type === 'queue_status') {
    return emit({ type: 'queue_status', ...engine.queueStatus() });
  }
  if (command.type === 'continue') throw new RuntimeError('ALREADY_RUNNING', 'Use steer or follow_up during an active run.');
  if (!actions[command.type]) throw new RuntimeError('INVALID_COMMAND', 'Unsupported runtime command.');
  actions[command.type]();
  return emit({ type: 'control', action: command.type, accepted: true });
}

// Never print raw SDK errors, provider payloads, environment variables or stacks.
process.on('uncaughtException', async () => { engine?.abort(); try { await emit(safeError(null)); } finally { await finish(1); } });
process.on('unhandledRejection', async () => { engine?.abort(); try { await emit(safeError(null)); } finally { await finish(1); } });
process.stdout.on('error', () => { engine?.abort(); void finish(1); });
input.on('close', () => { if (!finished) { engine?.abort(); if (!started) void finish(1); } });

void emit({ type: 'ready', engine: 'pi-agent-core', version: '1.2.0' });
input.on('line', line => {
  if (finished) return;
  try {
    if (line.length > 8_000_000) throw new RuntimeError('INVALID_COMMAND', 'The runtime command is too large.');
    let command;
    try { command = JSON.parse(line); } catch { throw new RuntimeError('INVALID_COMMAND', 'The runtime command must be JSON.'); }
    if (!command || typeof command !== 'object') throw new RuntimeError('INVALID_COMMAND', 'Invalid runtime command.');
    if (command.type === 'tool_result') {
      const waiter = pending.get(command.id);
      if (!waiter) throw new RuntimeError('UNKNOWN_TOOL_CALL', 'The tool call is no longer pending.');
      if (command.error) waiter.reject(new RuntimeError('TOOL_FAILED', 'Business tool execution failed.'));
      else waiter.resolve(command.result);
    } else if (command.type === 'start' || (!started && command.type === 'continue')) {
      void start(command).catch(async error => { await emit(safeError(error)); await finish(1); });
    } else {
      void control(command);
    }
  } catch (error) {
    void emit(safeError(error));
    if (!started || !engine) void finish(1);
  }
});
