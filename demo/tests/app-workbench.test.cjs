'use strict';
const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const Projects = require('../dist/project-store.js');

// Run the shipped scripts and click their real event handler. Only the browser
// shell is replaced; navigation, rendering and project mutations stay real.
function appHarness(workspace) {
  const listeners = new Map(), elements = new Map(), storage = new Map();
  if (workspace) storage.set('housignal-demo-projects-v1', JSON.stringify(workspace));
  const element = id => {
    if (!elements.has(id)) elements.set(id, { id, innerHTML: '', value: '', checked: false, style: {}, setAttribute() {}, removeAttribute() {}, focus() {} });
    return elements.get(id);
  };
  const context = vm.createContext({
    document: { body: { style: {} }, activeElement: null, getElementById: element, querySelector: () => null, contains: () => false, addEventListener: (name, callback) => listeners.set(name, callback) },
    localStorage: { getItem: key => storage.get(key) || null, setItem: (key, value) => storage.set(key, value) },
    setTimeout() {}, clearTimeout() {}, setInterval() {}, clearInterval() {}, scrollTo() {}
  });
  context.window = context;
  for (const name of ['model.js', 'project-store.js', 'ui.js', 'views.js', 'assistant-model.js', 'assistant-session.js', 'assistant-view.js', 'app.js']) {
    vm.runInContext(fs.readFileSync(path.join(__dirname, '../dist', name), 'utf8'), context, { filename: name });
  }
  return {
    demo: context.HouSignalDemo,
    click(action, data = {}) { const node = { dataset: { action, ...data } }; listeners.get('click')({ target: { closest: () => node }, preventDefault() {} }); },
    element,
    page: () => element('app').innerHTML
  };
}
const region = (html, label) => {
  const match = html.match(new RegExp('<section[^>]*aria-label="' + label + '"[^>]*>([\\s\\S]*?)</section>'));
  assert.ok(match, label + ' must be visible');
  return match[1];
};

// Catches making a new project visit start in conversation instead of the project board.
test('a fresh visit opens project overview and the butler remains explicitly accessible', () => {
  const app = appHarness();
  assert.equal(app.demo.getUI().page, 'dashboard');
  assert.equal(app.demo.getUI().workspaceTab, 'overview');
  assert.match(region(app.page(), '效果概览'), /14,100/);
  app.click('navigate', { page: 'assistant' });
  assert.equal(app.demo.getUI().page, 'assistant');
  assert.match(app.page(), /本地数据演示/);
});

// Catches leaving reports as another top-level page or opening the analysis by default.
test('workbench opens project overview with a nested analysis entry and independent butler', () => {
  const app = appHarness();
  app.demo.navigate('dashboard');
  assert.equal(app.demo.getUI().workspaceTab, 'overview');
  assert.match(app.page(), /data-action="workspace-tab" data-value="overview"[^>]*aria-pressed="true"/);
  assert.match(app.page(), /data-action="workspace-tab" data-value="effects"/);
  const navigation = app.page().match(/<nav class="nav-list"[\s\S]*?<\/nav>/)[0];
  assert.doesNotMatch(navigation, /data-page="reports"/);
  assert.match(app.page(), /data-page="assistant"/);
  assert.match(app.page(), /今天，推进这几件事/);
  assert.match(region(app.page(), '效果概览'), /14,100/);
  assert.match(region(app.page(), '复盘与下一步'), /下一条，讲清楚长辈的日常动线/);
  app.click('workspace-tab', { value: 'effects' });
  assert.equal(app.demo.getUI().page, 'dashboard');
  assert.match(app.page(), /发布与指标回执/);
  app.click('workspace-tab', { value: 'overview' });
  assert.match(region(app.page(), '效果概览'), /14,100/);
});

// Catches old studio/assistant links losing their destination after menu removal.
test('legacy reports navigation opens analysis inside workbench with correct active navigation', () => {
  const app = appHarness();
  app.demo.navigate('reports');
  assert.equal(app.demo.getUI().page, 'dashboard');
  assert.equal(app.demo.getUI().workspaceTab, 'effects');
  assert.match(app.page(), /class="nav-item active"[^>]*data-page="dashboard"[^>]*aria-current="page"/);
  assert.match(app.page(), /class="crumb-current">工作台<\/span>[\s\S]*class="crumb-current">效果分析<\/span>/);
  assert.match(app.page(), /data-action="workspace-tab" data-value="effects"[^>]*aria-pressed="true"/);
  assert.equal((app.page().match(/<h1>/g) || []).length, 1);
  app.demo.navigate('dashboard');
  assert.equal(app.demo.getUI().workspaceTab, 'overview');
});

// Catches moved report controls dropping their handlers or mutating a different project.
test('analysis retains D7/D30, receipt confirmation and proposal adoption with overview refresh', () => {
  const app = appHarness();
  app.demo.navigate('reports');
  app.click('report-window', { value: 'D30' });
  assert.match(app.page(), /D30 尚未取得发布或指标回执/);
  assert.doesNotMatch(app.page(), /14,100/);
  app.click('approve-proposal', { id: 'P01' });
  assert.equal(app.demo.getState().proposals[0].status, 'approved');
  assert.match(app.page(), /已采纳，待创作/);
  app.click('report-window', { value: 'D7' });
  app.click('show-publication', { id: 'PUB003' });
  assert.match(app.element('overlay-root').innerHTML, /作品与发布记录/);
  app.element('receipt-confirm').checked = true;
  app.element('receipt-state').value = 'live';
  app.click('verify-publication', { id: 'PUB003' });
  assert.equal(app.demo.getState().publications[2].status, 'verified');
  app.demo.navigate('dashboard');
  assert.match(region(app.page(), '效果概览'), /3 条公开作品/);
  assert.match(region(app.page(), '效果概览'), /1 条待回填/);
  assert.match(region(app.page(), '复盘与下一步'), /已采纳，待创作/);
});

// Catches stale summary metrics, proposals or window state leaking across projects.
test('switching project returns to its own overview and resets the observation window', () => {
  const app = appHarness();
  app.demo.navigate('reports');
  app.click('report-window', { value: 'D30' });
  app.click('switch-project', { projectId: 'project-qian' });
  assert.equal(app.demo.getUI().workspaceTab, 'overview');
  assert.equal(app.demo.getUI().reportWindow, 'D7');
  assert.match(region(app.page(), '效果概览'), /待观察/);
  assert.doesNotMatch(app.page(), /14,100|长辈的日常动线|陈女士/);
  app.click('switch-project', { projectId: 'project-yunjing' });
  assert.match(region(app.page(), '效果概览'), /14,100/);
});

// Catches summarizing pending receipts, other windows or absent data as measured zero.
test('overview summarizes verified live D7 metrics and keeps missing data distinct from zero', () => {
  const workspace = Projects.createWorkspace();
  const state = workspace.projects[0].state;
  state.publications[0].views = 0;
  state.publications[1].views = null;
  state.publications[2].views = 99000;
  state.publications.push({ ...state.publications[0], id: 'PUB030', title: 'D30 独立作品', window: 'D30', views: 88000 });
  const app = appHarness(workspace);
  app.demo.navigate('dashboard');
  const summary = region(app.page(), '效果概览');
  assert.match(summary, /D7/);
  assert.match(summary, /<strong>0<\/strong>/);
  assert.match(summary, /1 条待回填/);
  assert.doesNotMatch(summary, /99,000|88,000/);
  state.publications[0].views = null;
  const pending = appHarness(workspace);
  pending.demo.navigate('dashboard');
  assert.match(region(pending.page(), '效果概览'), /待回填/);
  assert.doesNotMatch(region(pending.page(), '效果概览'), /<strong>0<\/strong>/);
});

// Catches a second platform being included in overview totals but omitted from analysis.
test('overview and analysis include each platform with the same D7 total and separate platform figures', () => {
  const workspace = Projects.createWorkspace();
  workspace.projects[0].state.publications[1].channel = '小红书';
  const app = appHarness(workspace);
  const summary = region(app.page(), '效果概览');
  assert.match(summary, /14,100/);
  app.demo.navigate('reports');
  const content = app.page().match(/<h2[^>]*>内容表现<\/h2>([\s\S]*?)<\/section>/)[1];
  assert.match(content, /14,100/);
  assert.match(content, /抖音/);
  assert.match(content, /9,800/);
  assert.match(content, /小红书/);
  assert.match(content, /4,300/);
  assert.match(summary, /跨平台.*不去重/);
});

// Catches D7 summary links opening a previously selected D30 analysis window.
test('D7 summary opens D7 analysis while ordinary tabs preserve the selected observation window', () => {
  const app = appHarness();
  app.demo.navigate('reports');
  app.click('report-window', { value: 'D30' });
  app.click('workspace-tab', { value: 'overview' });
  app.click('workspace-tab', { value: 'effects' });
  assert.equal(app.demo.getUI().reportWindow, 'D30');
  app.click('workspace-tab', { value: 'overview' });
  const summary = region(app.page(), '效果概览');
  const button = summary.match(/<button[^>]*data-action="navigate"[^>]*>/)[0];
  const data = Object.fromEntries([...button.matchAll(/data-([\w-]+)="([^"]*)"/g)].map(match => [match[1], match[2]]));
  app.click(data.action, data);
  assert.equal(app.demo.getUI().workspaceTab, 'effects');
  assert.equal(app.demo.getUI().reportWindow, 'D7');
  assert.match(app.page(), /14,100/);
});
