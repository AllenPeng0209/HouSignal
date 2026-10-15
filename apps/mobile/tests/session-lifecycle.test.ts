import { test } from 'node:test';
import assert from 'node:assert/strict';
import { SessionEpoch, JobTransitions } from '../src/state/lifecycle';
test('previous device requests cannot invalidate a newly logged-in session', () => {
  const session = new SessionEpoch();
  const first = session.begin('device-first');
  session.begin('device-second');
  assert.equal(session.isCurrent(first), false);
  assert.equal(session.invalidate(first), false);
  assert.equal(session.token, 'device-second');
  assert.equal(session.invalidate(session.current), true);
  assert.equal(session.token, null);
});
test('completed tasks alert only after a visible active task and only once', () => {
  const jobs = new JobTransitions();
  assert.deepEqual(jobs.observe([{ id:'historic', status:'succeeded' }]), []);
  assert.deepEqual(jobs.observe([{ id:'new', status:'running' }]), []);
  assert.deepEqual(jobs.observe([{ id:'new', status:'succeeded' }]), ['new']);
  assert.deepEqual(jobs.observe([{ id:'new', status:'succeeded' }]), []);
});
test('task observations from a prior project are discarded on scope change', () => {
  const jobs = new JobTransitions();
  jobs.observe([{ id:'other-project', status:'running' }]);
  jobs.reset();
  assert.deepEqual(jobs.observe([{ id:'other-project', status:'succeeded' }]), []);
});

test('a newly submitted task completing before the first poll still alerts once', () => {
  const jobs = new JobTransitions();
  jobs.track('fast-task');
  assert.deepEqual(jobs.observe([{id:'fast-task',status:'succeeded'}]), ['fast-task']);
  assert.deepEqual(jobs.observe([{id:'fast-task',status:'succeeded'}]), []);
});
