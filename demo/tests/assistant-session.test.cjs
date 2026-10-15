'use strict';
const { test } = require('node:test');
const assert = require('node:assert/strict');
let Sessions;
try { Sessions = require('../dist/assistant-session.js'); } catch (error) {
  if (error.code !== 'MODULE_NOT_FOUND') throw error;
  Sessions = {};
}

function session(restored) {
  assert.equal(typeof Sessions.createSession, 'function', 'project conversation session API must exist');
  return Sessions.createSession(restored);
}
function answer(projectId, summary) {
  return { projectId, summary, sections: [{ title: '项目情况', items: [summary] }], sources: [] };
}
function freezeDeep(value) {
  Object.freeze(value);
  for (const child of Object.values(value)) {
    if (child && typeof child === 'object' && !Object.isFrozen(child)) freezeDeep(child);
  }
  return value;
}

// Catches routing project A's late response into the currently displayed project B.
test('late answers remain in their initiating project while drafts stay separate', () => {
  let store = Sessions.setDraft(session(), 'project-a', '汇总云境');
  store = Sessions.setDraft(store, 'project-b', '谁是栖岸的关键客户？');
  const started = Sessions.startQuestion(store, 'project-a', '  汇总云境  ', '2026-10-12T12:00:00Z');
  assert.equal(started.error, undefined);
  assert.equal(Sessions.getThread(started.store, 'project-a').draft, '');
  assert.equal(Sessions.getThread(started.store, 'project-b').draft, '谁是栖岸的关键客户？');
  const completed = Sessions.finishQuestion(started.store, 'project-a', started.requestId, answer('project-a', '云境资料需要补充'), '2026-10-12T12:00:01Z');
  assert.equal(completed.accepted, true);
  const thread = Sessions.getThread(completed.store, 'project-a');
  assert.equal(thread.messages.length, 2);
  assert.equal(thread.messages[0].text, '汇总云境');
  assert.equal(thread.messages[1].answer.summary, '云境资料需要补充');
  assert.equal(thread.pending, null);
  assert.equal(Sessions.getThread(completed.store, 'project-b').messages.length, 0);
});

// Catches treating a request ID alone as authority to write into a different project.
test('mismatched project, request ID and answer project IDs cannot finish a question', () => {
  const a = Sessions.startQuestion(session(), 'project-a', '缺少什么？', 'start');
  const b = Sessions.startQuestion(a.store, 'project-b', '关键客户是谁？', 'start');
  for (const [projectId, requestId, result] of [
    ['project-b', a.requestId, answer('project-b', 'B')],
    ['project-a', 'unknown-request', answer('project-a', 'A')],
    ['project-a', a.requestId, answer('project-b', '错误的 B 回答')]
  ]) {
    const rejected = Sessions.finishQuestion(b.store, projectId, requestId, result, 'end');
    assert.equal(rejected.accepted, false);
    assert.deepEqual(rejected.store, b.store);
  }
  assert.equal(Sessions.finishQuestion(b.store, 'project-a', a.requestId, answer('project-a', 'A'), 'end').accepted, true);
});

// Catches resetting the sequence on clear, which would allow an old result to match the new request.
test('new chat clears only that project and makes old answers permanently stale', () => {
  const first = Sessions.startQuestion(session(), 'project-a', '第一次', 'one');
  const b = Sessions.setDraft(first.store, 'project-b', '保留的草稿');
  const cleared = Sessions.newChat(b, 'project-a');
  assert.deepEqual(Sessions.getThread(cleared, 'project-a'), { messages: [], draft: '', pending: null });
  assert.equal(Sessions.getThread(cleared, 'project-b').draft, '保留的草稿');
  const second = Sessions.startQuestion(cleared, 'project-a', '第二次', 'two');
  assert.notEqual(second.requestId, first.requestId);
  const late = Sessions.finishQuestion(second.store, 'project-a', first.requestId, answer('project-a', '旧答案'), 'late');
  assert.equal(late.accepted, false);
  assert.equal(Sessions.getThread(late.store, 'project-a').pending.id, second.requestId);
  assert.equal(Sessions.finishQuestion(late.store, 'project-a', second.requestId, answer('project-a', '新答案'), 'done').accepted, true);
});

// Catches refresh losing drafts or leaving the spinner active indefinitely.
test('refresh restores drafts and converts pending queries into a transparent interruption', () => {
  const started = Sessions.startQuestion(session(), 'project-a', '汇总项目', 'started');
  const saved = Sessions.setDraft(started.store, 'project-b', '  尚未发送的输入\n');
  const restored = session(JSON.parse(JSON.stringify(saved)));
  assert.equal(Sessions.getThread(restored, 'project-b').draft, '  尚未发送的输入\n');
  const a = Sessions.getThread(restored, 'project-a');
  assert.equal(a.pending, null);
  assert.equal(a.messages.length, 2);
  assert.equal(a.messages[1].role, 'assistant');
  assert.match(a.messages[1].text, /查询已中断.*重新提问/);
  assert.equal(Sessions.finishQuestion(restored, 'project-a', started.requestId, answer('project-a', '迟到答案'), 'late').accepted, false);
  const next = Sessions.startQuestion(restored, 'project-a', '再汇总一次', 'next');
  assert.notEqual(next.requestId, started.requestId);
});

// Catches mutating the shared store or answer payload through a returned thread.
test('session changes and returned threads do not mutate prior stores or answer objects', () => {
  const empty = freezeDeep(session());
  const drafted = Sessions.setDraft(empty, 'project-a', '草稿');
  const draftSnapshot = JSON.stringify(drafted);
  freezeDeep(drafted);
  const start = Sessions.startQuestion(drafted, 'project-a', '问题', 'start');
  assert.equal(JSON.stringify(drafted), draftSnapshot);
  freezeDeep(start.store);
  const payload = freezeDeep(answer('project-a', '回答'));
  const finished = Sessions.finishQuestion(start.store, 'project-a', start.requestId, payload, 'end');
  const thread = Sessions.getThread(finished.store, 'project-a');
  thread.messages[1].answer.sections[0].items[0] = '被修改';
  thread.messages.push({ role: 'user', text: '额外输入' });
  assert.equal(Sessions.getThread(finished.store, 'project-a').messages[1].answer.sections[0].items[0], '回答');
  assert.equal(Sessions.getThread(finished.store, 'project-a').messages.length, 2);
  assert.equal(payload.sections[0].items[0], '回答');
  assert.equal(Object.keys(empty.threads).length, 0);
});

// Catches retaining orphaned answers or exceeding the bounded chat history after many questions.
test('conversation limit retains the latest 20 complete question answer pairs', () => {
  let store = session();
  for (let index = 1; index <= 25; index += 1) {
    const start = Sessions.startQuestion(store, 'project-a', '问题 ' + index, String(index));
    const finish = Sessions.finishQuestion(start.store, 'project-a', start.requestId, answer('project-a', '回答 ' + index), String(index));
    assert.equal(finish.accepted, true);
    store = finish.store;
  }
  const messages = Sessions.getThread(store, 'project-a').messages;
  assert.equal(messages.length, 40);
  assert.equal(messages[0].role, 'user');
  assert.equal(messages[0].text, '问题 6');
  assert.equal(messages.at(-1).answer.summary, '回答 25');
  assert.ok(messages.every((message, index) => message.role === (index % 2 ? 'assistant' : 'user')));
});

// Catches invalid input creating empty messages or overwriting an in-flight query.
test('blank long and overlapping questions are rejected without changing the conversation', () => {
  const store = session();
  for (const invalid of ['', '   \n', '字'.repeat(2001)]) {
    const result = Sessions.startQuestion(store, 'project-a', invalid, 'start');
    assert.ok(result.error);
    assert.deepEqual(result.store, store);
  }
  const started = Sessions.startQuestion(store, 'project-a', '字'.repeat(2000), 'start');
  assert.equal(started.error, undefined);
  const overlap = Sessions.startQuestion(started.store, 'project-a', '另一个问题', 'second');
  assert.ok(overlap.error);
  assert.deepEqual(overlap.store, started.store);
  assert.equal(Sessions.getThread(Sessions.setDraft(store, 'project-a', '字'.repeat(2100)), 'project-a').draft.length, 2000);
});

// Catches malformed local storage causing crashes or prototype pollution.
test('bad restored schemas and dangerous project keys are discarded safely', () => {
  for (const input of [null, [], { schemaVersion: 99, threads: {} }, { schemaVersion: 1, threads: null }]) {
    assert.deepEqual(Sessions.getThread(session(input), 'project-a'), { messages: [], draft: '', pending: null });
  }
  const polluted = JSON.parse('{"schemaVersion":1,"sequence":7,"threads":{"__proto__":{"draft":"污染","messages":[]},"constructor":{"draft":"污染","messages":[]},"project-a":{"draft":"正常草稿","messages":"坏消息","pending":false}}}');
  const restored = session(polluted);
  assert.equal(Object.prototype.hasOwnProperty.call(restored.threads, '__proto__'), false);
  assert.equal(Object.prototype.hasOwnProperty.call(restored.threads, 'constructor'), false);
  assert.equal(Sessions.getThread(restored, 'project-a').draft, '正常草稿');
  for (const projectId of ['__proto__', 'constructor', '', null]) {
    assert.ok(Sessions.startQuestion(restored, projectId, '问题', 'start').error);
    assert.deepEqual(Sessions.getThread(restored, projectId), { messages: [], draft: '', pending: null });
  }
  assert.equal({}.draft, undefined);
});

// Catches restoring more threads than the bounded local demo can safely retain.
test('thread limit rejects additional projects without evicting existing conversations', () => {
  let store = session();
  for (let index = 1; index <= 50; index += 1) store = Sessions.setDraft(store, 'project-' + index, '保留 ' + index);
  const tooMany = Sessions.startQuestion(store, 'project-51', '新项目', 'start');
  assert.ok(tooMany.error);
  assert.deepEqual(tooMany.store, store);
  assert.equal(Sessions.getThread(tooMany.store, 'project-1').draft, '保留 1');
  assert.equal(Sessions.startQuestion(store, 'project-50', '已有项目', 'start').error, undefined);
});
