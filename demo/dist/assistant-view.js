(function (root, factory) {
  'use strict';
  const api = factory(root);
  if (typeof module === 'object' && module.exports) module.exports = api;
  root.LouxiangAssistantView = api;
})(typeof globalThis !== 'undefined' ? globalThis : this, function (root) {
  'use strict';
  const esc = value => root.HSUI && root.HSUI.escape ? root.HSUI.escape(value) : String(value == null ? '' : value).replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  const icon = (name, size = 18) => root.HSUI && root.HSUI.icon ? root.HSUI.icon(name, size) : '';
  const list = value => Array.isArray(value) ? value : [];
  const fallbackPages = { fact: 'property', asset: 'assets', lead: 'leads', inquiry: 'leads', publication: 'reports', page: 'dashboard', completeness: 'property' };
  const kindIcons = { fact: 'file', asset: 'image', lead: 'user', inquiry: 'message', publication: 'chart', page: 'grid', completeness: 'check-circle' };
  const prompts = [
    { icon: 'grid', title: '项目全貌', note: '把进展与重点，一次讲清', question: '请完整汇总当前项目情况，包括资料、内容、客户与下一步。' },
    { icon: 'shield', title: '资料缺口', note: '哪些没补齐，先补什么', question: '当前项目还缺什么资料？哪些问题会影响内容制作与发布？' },
    { icon: 'users', title: '关键客户', note: '谁值得跟进，依据是什么', question: '当前项目的关键客户是谁？请说明判断依据和下一步跟进动作。' },
    { icon: 'target', title: '下一步', note: '从项目记录里找行动重点', question: '根据当前项目记录，接下来最应该推进哪三件事？' }
  ];

  function promptButton(label, question, className) {
    return `<button type="button" class="${esc(className || 'agent-followup')}" data-action="agent-prompt" data-question="${esc(question || label)}">${esc(label)}${icon('arrow-right', 14)}</button>`;
  }

  function sourceButton(ref, projectId, label, className) {
    if (!ref || typeof ref !== 'object') return '';
    const kind = Object.prototype.hasOwnProperty.call(fallbackPages, ref.kind) ? ref.kind : 'page';
    const title = label || ref.label || '查看项目记录';
    return `<button type="button" class="${esc(className || 'agent-record-link')}" data-action="agent-source" data-ref-kind="${esc(kind)}" data-id="${esc(ref.id || '')}" data-page="${esc(ref.page || fallbackPages[kind])}" data-project-id="${esc(projectId || '')}">${icon(kindIcons[kind], 14)}<span>${esc(title)}</span>${icon('arrow-right', 13)}</button>`;
  }

  function answerView(answer, projectId) {
    const data = answer || {};
    const sections = list(data.sections).filter(section => section && list(section.items).length);
    const refs = list(data.sources);
    const suggestions = list(data.suggestions);
    const linkedRecords = new Set();
    const itemSourceLink = ref => {
      if (!ref || typeof ref !== 'object') return '';
      const key = JSON.stringify([ref.kind || 'page', ref.id || '', ref.page || fallbackPages[ref.kind] || 'dashboard']);
      if (linkedRecords.has(key)) return '';
      linkedRecords.add(key);
      return sourceButton(ref, projectId, ref.kind === 'lead' ? '客户详情' : '查看记录', 'agent-item-link');
    };
    return `<div class="agent-answer-content">${data.title ? `<h2 class="agent-answer-title">${esc(data.title)}</h2>` : ''}${data.summary ? `<p class="agent-answer-summary">${esc(data.summary)}</p>` : ''}${sections.map(section => `<section class="agent-answer-section">${section.title ? `<h3>${esc(section.title)}</h3>` : ''}<div class="agent-answer-items">${list(section.items).map(item => { const tone = item.tone === 'warning' ? 'amber' : ['blue', 'amber', 'green', 'red'].includes(item.tone) ? item.tone : 'neutral'; return `<article class="agent-answer-item ${tone}"><div class="agent-answer-item-copy">${item.label ? `<h4>${esc(item.label)}</h4>` : ''}${item.text ? `<p>${esc(item.text)}</p>` : ''}</div>${itemSourceLink(item.ref)}</article>`; }).join('')}</div></section>`).join('')}${data.notice ? `<p class="agent-answer-notice">${icon('info', 14)}<span>${esc(data.notice)}</span></p>` : ''}${refs.length ? `<details class="agent-sources"><summary>${icon('link', 14)}<span>依据与记录 · ${refs.length} 项</span>${icon('chevron', 15)}</summary><div class="agent-source-list">${refs.map(ref => sourceButton(ref, projectId, null, 'agent-source-button')).join('')}</div></details>` : ''}${suggestions.length ? `<div class="agent-followups" aria-label="继续追问">${suggestions.map(suggestion => promptButton(suggestion.label || suggestion.question, suggestion.question, 'agent-followup')).join('')}</div>` : ''}</div>`;
  }

  function messageView(message, projectId) {
    if (message.role === 'user') {
      return `<article class="agent-message agent-user-message" data-message-id="${esc(message.id || '')}"><span class="agent-message-role agent-sr-only">你</span><div class="agent-user-bubble"><p>${esc(message.text || '')}</p></div></article>`;
    }
    return `<article class="agent-message agent-assistant-message" data-message-id="${esc(message.id || '')}"><span class="agent-message-avatar" aria-hidden="true">${icon('sparkle', 17)}</span><div class="agent-assistant-body"><div class="agent-message-role">楼管家</div>${message.answer ? answerView(message.answer, message.answer.projectId || projectId) : `<p class="agent-answer-summary">${esc(message.text || '')}</p>`}</div></article>`;
  }

  function welcomeView(state, context) {
    const facts = list(state.facts);
    const leads = list(state.leads);
    const complete = context.completeness || {};
    return `<div class="agent-welcome"><div class="agent-welcome-mark" aria-hidden="true">${icon('sparkle', 31)}<span class="agent-mark-corner"></span></div><div class="agent-welcome-eyebrow">YOUR PROJECT, IN FOCUS</div><h2>聊聊这个项目，<br class="agent-mobile-break">下一步更清楚。</h2><p class="agent-welcome-copy">从楼盘事实到客户跟进，直接问。<br>回答附上项目记录，方便你核对与继续推进。</p><div class="agent-context-line"><span>${facts.length} 条事实记录</span><i></i><span>${leads.length} 条资格客户记录</span><i></i><span>资料完整度 ${esc(complete.percent == null ? '—' : complete.percent)}${complete.percent == null ? '' : '%'}</span></div><div class="agent-prompt-grid">${prompts.map(prompt => `<button type="button" class="agent-prompt-card" data-action="agent-prompt" data-question="${esc(prompt.question)}"><span class="agent-prompt-icon">${icon(prompt.icon, 19)}</span><span class="agent-prompt-copy"><strong>${esc(prompt.title)}</strong><small>${esc(prompt.note)}</small></span>${icon('arrow-right', 16)}</button>`).join('')}</div></div>`;
  }

  function render(state, context) {
    state = state || {};
    context = context || {};
    const projectName = context.projectName || (state.property || {}).name || '当前项目';
    const projectId = context.projectId || (state.property || {}).id || '';
    const messages = list(context.messages);
    const pending = Boolean(context.pending);
    const draft = String(context.draft || '');
    const complete = context.completeness || {};
    return `<section class="agent-page ${messages.length ? 'has-conversation' : 'is-welcome'}"><header class="agent-page-header"><div><div class="eyebrow">PROJECT COPILOT</div><div class="agent-title-row"><h1>楼管家</h1><span class="agent-demo-label"><span></span>本地数据演示</span></div><p>读懂项目记录，理清下一步。</p></div><div class="agent-page-actions"><button type="button" class="agent-header-button" data-action="agent-refresh" ${pending ? 'disabled' : ''} aria-label="刷新当前项目汇总">${icon('refresh', 16)}<span>刷新汇总</span></button><button type="button" class="agent-header-button" data-action="agent-new-chat" aria-label="开始新对话" ${pending ? 'disabled' : ''}>${icon('plus', 16)}<span>新对话</span></button></div></header><div class="agent-chat"><div class="agent-project-bar"><div class="agent-project-binding">${icon('building', 15)}<span>当前项目</span><strong title="${esc(projectName)}">${esc(projectName)}</strong></div><button type="button" class="agent-project-status" data-action="project-completeness" aria-label="查看当前项目资料完整度">${icon('check-circle', 14)}<span>${esc(complete.completed == null ? '—' : complete.completed)} / ${esc(complete.total == null ? 7 : complete.total)} 项准备完成</span>${icon('arrow-right', 13)}</button></div><div class="agent-thread" id="agent-messages" role="log" aria-label="楼管家对话" aria-live="polite" aria-relevant="additions text">${messages.length ? `<div class="agent-message-list">${messages.map(message => messageView(message, projectId)).join('')}${pending ? `<article class="agent-message agent-assistant-message agent-pending"><span class="agent-message-avatar" aria-hidden="true">${icon('sparkle', 17)}</span><div class="agent-assistant-body"><div class="agent-message-role">楼管家</div><div class="agent-pending-body" role="status"><span class="agent-loading-dots" aria-hidden="true"><i></i><i></i><i></i></span>正在查阅本项目记录…</div></div></article>` : ''}</div>` : welcomeView(state, context)}</div><div class="agent-composer-wrap"><div class="agent-composer"><label class="agent-sr-only" for="agent-input">向楼管家提问</label><textarea id="agent-input" aria-label="向楼管家提问" maxlength="2000" rows="2" placeholder="问问项目进展、资料缺口，或某位客户的情况…" spellcheck="false">${esc(draft)}</textarea><div class="agent-composer-actions"><span class="agent-composer-scope">${icon('lock', 12)}<span>仅查阅本项目记录</span></span><button type="button" class="agent-send" data-action="agent-send" ${pending || !draft.trim() ? 'disabled' : ''} aria-label="发送消息">${icon('arrow-right', 17)}<span>发送</span></button></div></div><div class="agent-composer-footnote"><span>本地记录检索演示，模型与项目数据库待接入。</span><span class="agent-keyboard-hint"><kbd>Enter</kbd> 发送 · <kbd>Shift + Enter</kbd> 换行</span></div></div></div></section>`;
  }
  return { render };
});
