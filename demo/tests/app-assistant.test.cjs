'use strict';
const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

// Execute the shipped browser scripts; only the browser DOM, storage and clock
// are replaced so we can deterministically deliver an already queued answer.
function appHarness() {
  const listeners = new Map(), elements = new Map(), storage = new Map(), timers = new Map();
  let nextTimer = 0;
  const element = id => {
    if (!elements.has(id)) elements.set(id, {
      id, innerHTML: '', value: '', style: {}, scrollTop: 0, scrollHeight: 0,
      setAttribute() {}, removeAttribute() {}, focus() {}
    });
    return elements.get(id);
  };
  const document = {
    body: { style: {} }, activeElement: null,
    getElementById: element, querySelector: () => null,
    addEventListener: (name, callback) => listeners.set(name, callback)
  };
  const context = vm.createContext({
    document,
    localStorage: { getItem: key => storage.get(key) || null, setItem: (key, value) => storage.set(key, value) },
    setTimeout(callback) { const id = ++nextTimer; timers.set(id, { callback, cancelled: false }); return id; },
    clearTimeout(id) { if (timers.has(id)) timers.get(id).cancelled = true; },
    setInterval() {}, clearInterval() {}, scrollTo() {}
  });
  context.window = context;
  for (const name of ['model.js', 'project-store.js', 'ui.js', 'views.js', 'assistant-model.js', 'assistant-session.js', 'assistant-view.js', 'app.js']) {
    vm.runInContext(fs.readFileSync(path.join(__dirname, '../dist', name), 'utf8'), context, { filename: name });
  }
  const click = (action, data = {}) => {
    const node = { dataset: { action, ...data } };
    listeners.get('click')({ target: { closest: () => node }, preventDefault() {} });
  };
  return {
    click,
    demo: context.HouSignalDemo,
    ask(question) { click('agent-prompt', { question }); return nextTimer; },
    runTimer(id) { const timer = timers.get(id); assert.ok(timer, 'query must schedule a callback'); if (!timer.cancelled) timer.callback(); },
    thread(projectId = 'project-yunjing') { return JSON.parse(storage.get('housignal-demo-agent-v1')).threads[projectId]; },
    page() { return element('app').innerHTML; }
  };
}

// Removing the reset boundary must make these fail: request IDs may be reused
// after reset, but a response from the previous session must never finish it.
for (const resetMethod of ['confirmed reset action', 'demo reset API']) {
  test('pending answer from before ' + resetMethod + ' cannot replace a new question response', () => {
    const app = appHarness();
    const oldQuery = app.ask('陈女士情况怎样？');
    if (resetMethod === 'confirmed reset action') app.click('confirm-reset');
    else app.demo.reset();
    const newQuery = app.ask('当前资料缺什么？');

    app.runTimer(oldQuery);
    const waiting = app.thread();
    assert.ok(waiting.pending, 'old callback must leave the new question pending');
    assert.equal(waiting.messages.length, 1, 'old customer answer must not be attached to the new gap question');
    assert.equal(waiting.messages[0].text, '当前资料缺什么？');

    app.runTimer(newQuery);
    const completed = app.thread();
    assert.equal(completed.pending, null);
    assert.equal(completed.messages.length, 2);
    assert.equal(completed.messages[1].answer.intent, 'gaps');
    assert.match(completed.messages[1].answer.summary, /资料完整度/);
  });
}

// Removing captured project IDs or the active-project render check would send
// the first answer into the second project or overwrite its visible page.
test('switching projects before a reply preserves its original conversation and the visible project', () => {
  const app = appHarness();
  const query = app.ask('陈女士情况怎样？');
  app.click('switch-project', { projectId: 'project-qian' });
  const switchedPage = app.page();
  assert.equal(app.demo.getWorkspace().activeProjectId, 'project-qian');

  app.runTimer(query);
  assert.equal(app.page(), switchedPage);
  assert.equal(app.thread('project-yunjing').messages[1].answer.contextLeadId, 'LEAD001');
  assert.equal(app.thread('project-qian'), undefined);

  const otherQuery = app.ask('当前资料缺什么？');
  app.runTimer(otherQuery);
  assert.equal(app.thread('project-qian').messages[1].answer.intent, 'gaps');
  assert.equal(app.thread('project-qian').messages[1].answer.projectId, 'project-qian');
  assert.doesNotMatch(app.page(), /陈女士/);
});
