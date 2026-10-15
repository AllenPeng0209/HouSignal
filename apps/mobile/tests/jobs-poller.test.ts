import assert from 'node:assert/strict';
import { test } from 'node:test';
import { createForegroundPoller } from '../src/domain/jobs-poller';
const flush = async () => { for (let i = 0; i < 6; i++) await Promise.resolve(); };

test('polling never overlaps and starts another read only after the previous read settles', async () => {
  const timers: (() => void)[] = []; const results: number[] = []; let reads = 0; let resolve!: (value: number) => void;
  const poller = createForegroundPoller<number>({ read: () => { reads++; return new Promise(done => { resolve = done; }); }, onData: value => results.push(value), onError: () => {}, schedule: fn => { timers.push(fn); return timers.length; }, unschedule: () => {} });
  poller.resume(); poller.resume();
  assert.equal(reads, 1); assert.equal(timers.length, 0);
  resolve(7); await flush();
  assert.deepEqual(results, [7]); assert.equal(timers.length, 1);
  timers.shift()!(); assert.equal(reads, 2); poller.dispose();
});

test('background pause aborts requests and suppresses stale results; resume performs a fresh read', async () => {
  const reads: { signal: AbortSignal; resolve: (value: number) => void }[] = []; const results: number[] = [];
  const poller = createForegroundPoller<number>({ read: signal => new Promise(resolve => reads.push({ signal, resolve })), onData: value => results.push(value), onError: () => {}, schedule: () => 1, unschedule: () => {} });
  poller.resume(); poller.pause();
  assert.equal(reads.length, 1);
  assert.equal(reads[0].signal.aborted, true);
  reads[0].resolve(1); await flush(); assert.deepEqual(results, []);
  poller.resume(); reads[1].resolve(2); await flush(); assert.deepEqual(results, [2]);
  poller.dispose();
});

test('unmount disposal clears the timer and cannot restart or emit errors', async () => {
  let timer: (() => void) | undefined; const cleared: unknown[] = []; const errors: string[] = [];
  const poller = createForegroundPoller<number>({ read: async () => 1, onData: () => {}, onError: error => errors.push(String(error)), schedule: fn => { timer = fn; return 4; }, unschedule: value => cleared.push(value) });
  poller.resume(); await flush(); poller.dispose(); poller.resume(); timer?.(); await flush();
  assert.deepEqual(cleared, [4]); assert.deepEqual(errors, []);
});
