(function (root, factory) {
  'use strict';
  const api = factory();
  if (typeof module === 'object' && module.exports) module.exports = api;
  root.LouxiangAssistantSessions = api;
})(typeof globalThis !== 'undefined' ? globalThis : this, function () {
  'use strict';
  const MAX_INPUT = 2000;
  const MAX_MESSAGES = 40;
  const MAX_THREADS = 50;
  const own = (object, key) => Object.prototype.hasOwnProperty.call(object, key);
  const object = value => value !== null && typeof value === 'object' && !Array.isArray(value);
  const clone = value => JSON.parse(JSON.stringify(value));
  const validId = value => typeof value === 'string' && value.length > 0 && value.length <= 120 && value === value.trim() && !['__proto__', 'constructor', 'prototype'].includes(value);
  const blankThread = () => ({ messages: [], draft: '', pending: null });
  const blankStore = () => ({ schemaVersion: 1, sequence: 0, threads: {} });
  const timestamp = value => typeof value === 'string' ? value.slice(0, 80) : '';

  function serializableAnswer(value, projectId) {
    if (typeof value === 'string') return value.trim() ? value : null;
    if (!object(value) || (own(value, 'projectId') && value.projectId !== projectId)) return null;
    try {
      const encoded = JSON.stringify(value);
      if (!encoded || encoded.length > 100000) return null;
      const copied = JSON.parse(encoded);
      return object(copied) && Object.keys(copied).length ? copied : null;
    } catch (_) { return null; }
  }

  function requestSequence(value) {
    const match = typeof value === 'string' && /^assistant-query-(\d+)$/.exec(value);
    const parsed = match ? Number(match[1]) : 0;
    return Number.isSafeInteger(parsed) && parsed >= 0 ? parsed : 0;
  }

  function message(value, projectId) {
    if (!object(value) || !['user', 'assistant'].includes(value.role)) return null;
    const content = typeof value.text === 'string' ? value.text : '';
    if (value.role === 'user' && (!content.trim() || content.length > MAX_INPUT)) return null;
    const next = {
      id: typeof value.id === 'string' ? value.id.slice(0, 160) : '',
      requestId: typeof value.requestId === 'string' ? value.requestId.slice(0, 160) : '',
      role: value.role, text: content.slice(0, 100000), at: timestamp(value.at)
    };
    if (own(value, 'answer')) {
      const answer = serializableAnswer(value.answer, projectId);
      if (answer !== null) next.answer = answer;
      else if (value.role === 'assistant') return null;
    }
    if (value.role === 'assistant' && !next.text && !own(next, 'answer')) return null;
    return next;
  }

  // Restoring is the one boundary that cancels pending work: no query runs survive a page refresh.
  function createSession(restored) {
    const result = blankStore();
    if (!object(restored) || restored.schemaVersion !== 1 || !object(restored.threads)) return result;
    if (Number.isSafeInteger(restored.sequence) && restored.sequence >= 0) result.sequence = restored.sequence;
    for (const projectId of Object.keys(restored.threads).filter(validId).slice(0, MAX_THREADS)) {
      const saved = restored.threads[projectId];
      if (!object(saved)) continue;
      const thread = blankThread();
      thread.draft = typeof saved.draft === 'string' ? saved.draft.slice(0, MAX_INPUT) : '';
      const savedMessages = Array.isArray(saved.messages) ? saved.messages.slice(-MAX_MESSAGES - 2) : [];
      let user = null;
      for (const candidate of savedMessages) {
        const normalized = message(candidate, projectId);
        if (!normalized) continue;
        result.sequence = Math.max(result.sequence, requestSequence(normalized.requestId));
        if (normalized.role === 'user') user = normalized;
        else if (user && (!normalized.requestId || !user.requestId || normalized.requestId === user.requestId)) {
          thread.messages.push(user, normalized);
          user = null;
        }
      }
      if (object(saved.pending)) result.sequence = Math.max(result.sequence, requestSequence(saved.pending.id));
      if (user) {
        thread.messages.push(user, {
          id: user.id + '-interrupted', requestId: user.requestId,
          role: 'assistant', text: '上次查询已中断，请重新提问', at: user.at
        });
      }
      thread.messages = thread.messages.slice(-MAX_MESSAGES);
      result.threads[projectId] = thread;
    }
    return result;
  }

  function copyStore(store) {
    if (!object(store) || store.schemaVersion !== 1 || !object(store.threads)) return blankStore();
    try { return clone(store); } catch (_) { return blankStore(); }
  }

  function getThread(store, projectId) {
    if (!validId(projectId) || !object(store) || !object(store.threads) || !own(store.threads, projectId) || !object(store.threads[projectId])) return blankThread();
    try { return clone(store.threads[projectId]); } catch (_) { return blankThread(); }
  }

  function canAddThread(store, projectId) {
    return own(store.threads, projectId) || Object.keys(store.threads).length < MAX_THREADS;
  }

  function setDraft(store, projectId, text) {
    const next = copyStore(store);
    if (!validId(projectId) || !canAddThread(next, projectId)) return next;
    const draft = typeof text === 'string' ? text.slice(0, MAX_INPUT) : '';
    if (!own(next.threads, projectId) && !draft) return next;
    const thread = getThread(next, projectId);
    thread.draft = draft;
    next.threads[projectId] = thread;
    return next;
  }

  function startQuestion(store, projectId, text, at) {
    const next = copyStore(store);
    const reject = error => ({ store: next, requestId: null, error });
    if (!validId(projectId)) return reject('请选择有效项目后再提问');
    if (typeof text !== 'string' || !text.trim()) return reject('请输入你想了解的项目问题');
    if (text.length > MAX_INPUT) return reject('问题最多输入 2000 字');
    if (!canAddThread(next, projectId)) return reject('本地对话项目已达上限，请先清空一个项目的对话');
    const thread = getThread(next, projectId);
    if (thread.pending) return reject('当前项目的问题仍在查询中，请稍候');
    if (!Number.isSafeInteger(next.sequence) || next.sequence < 0 || next.sequence === Number.MAX_SAFE_INTEGER) return reject('对话计数异常，请重新初始化本地对话');
    next.sequence += 1;
    const requestId = 'assistant-query-' + next.sequence;
    const question = text.trim();
    thread.messages = thread.messages.slice(-(MAX_MESSAGES - 2));
    thread.messages.push({ id: requestId + '-user', requestId, role: 'user', text: question, at: timestamp(at) });
    thread.draft = '';
    thread.pending = { id: requestId, question, at: timestamp(at) };
    next.threads[projectId] = thread;
    return { store: next, requestId };
  }

  function finishQuestion(store, projectId, requestId, answer, at) {
    const next = copyStore(store);
    const thread = getThread(next, projectId);
    if (!validId(projectId) || !thread.pending || thread.pending.id !== requestId) return { store: next, accepted: false };
    const payload = serializableAnswer(answer, projectId);
    if (payload === null) return { store: next, accepted: false };
    const response = {
      id: requestId + '-assistant', requestId, role: 'assistant',
      text: typeof payload === 'string' ? payload : (typeof payload.text === 'string' ? payload.text : ''),
      at: timestamp(at)
    };
    if (object(payload)) response.answer = payload;
    thread.messages.push(response);
    thread.messages = thread.messages.slice(-MAX_MESSAGES);
    thread.pending = null;
    next.threads[projectId] = thread;
    return { store: next, accepted: true };
  }

  function newChat(store, projectId) {
    const next = copyStore(store);
    if (validId(projectId) && own(next.threads, projectId)) delete next.threads[projectId];
    return next;
  }

  return { createSession, getThread, setDraft, startQuestion, finishQuestion, newChat };
});
