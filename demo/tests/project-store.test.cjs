'use strict';
const { test } = require('node:test');
const assert = require('node:assert/strict');
const Model = require('../dist/model.js');
let Projects;
try { Projects = require('../dist/project-store.js'); } catch (error) {
  if (error.code !== 'MODULE_NOT_FOUND') throw error;
  Projects = {};
}

function workspace(legacy) {
  assert.equal(typeof Projects.createWorkspace, 'function', 'project workspace API must exist');
  return Projects.createWorkspace(legacy);
}
function act(workspace, projectId, type, payload = {}) {
  const result = Projects.dispatch(workspace, projectId, { type, ...payload });
  assert.equal(result.error, undefined, result.error);
  return result.workspace;
}
function approve(workspace, id) {
  let next = act(workspace, id, 'SELECT_TOPIC', { id: 'T01' });
  next = act(next, id, 'GENERATE_SCRIPT');
  next = act(next, id, 'RENDER_PREVIEW');
  return act(next, id, 'APPROVE_RELEASE');
}
function freezeDeep(value) {
  Object.freeze(value);
  Object.values(value).forEach(child => {
    if (child && typeof child === 'object' && !Object.isFrozen(child)) freezeDeep(child);
  });
  return value;
}

// Catches copying the original project's leads, releases or claimed footage to a different listing.
test('project switching displays independent data and restores prior edits and approval', () => {
  let work = approve(workspace(), 'project-yunjing');
  work = act(work, 'project-yunjing', 'SAVE_FOLLOWUP', { id: 'LEAD001', note: '只属于云境的现场反馈' });
  work = Projects.switchProject(work, 'project-qian').workspace;
  const second = Projects.getActiveProject(work);
  assert.equal(second.state.property.name, '栖岸·观澜');
  assert.equal(second.state.property.city, '杭州');
  assert.equal(second.state.leads.length, 0);
  assert.equal(second.state.publications.length, 0);
  assert.equal(second.state.campaign.release, null);
  assert.equal(second.state.assets.length, 0, 'new example must allow upload without permanent empty source slots');
  work = Projects.switchProject(work, 'project-yunjing').workspace;
  assert.equal(Projects.getActiveProject(work).state.leads[0].notes[0].text, '只属于云境的现场反馈');
  assert.equal(Projects.getActiveProject(work).state.campaign.release.valid, true);
});

// Catches creating a new project from the seed project instead of empty, unverified materials.
test('new projects start with blank evidence and no inherited business results', () => {
  const result = Projects.createProject(workspace(), { name: '澄湾府', city: '苏州' });
  assert.equal(result.error, undefined);
  assert.equal(result.workspace.activeProjectId, result.projectId);
  const state = Projects.getActiveProject(result.workspace).state;
  assert.equal(state.property.name, '澄湾府');
  assert.equal(state.property.heroSrc, null);
  assert.equal(state.property.marketingPermission.confirmed, false);
  assert.equal(state.assets.length, 0);
  assert.ok(state.facts.every(fact => fact.value === '' && fact.source === '' && fact.status === 'unverified'));
  assert.equal(state.topics.length, 5);
  assert.ok(state.topics.every(topic => !/328|8\.2|4 房|云境/.test(JSON.stringify(topic))));
  assert.equal(state.campaign.script, '');
  for (const key of ['inquiries', 'leads', 'publications', 'proposals', 'activities']) assert.deepEqual(state[key], []);
  assert.ok(Projects.createProject(workspace(), { name: ' ', city: '上海' }).error);
  assert.ok(Projects.createProject(workspace(), { name: '澄湾府', city: '' }).error);
});

// Catches a task started on A writing into B after the UI changes its active selection.
test('late project dispatch stays with its explicit project and mismatched property IDs are rejected', () => {
  const start = workspace();
  const switched = Projects.switchProject(start, 'project-qian').workspace;
  const result = Projects.dispatch(switched, 'project-yunjing', { type: 'SELECT_TOPIC', id: 'T02', propertyId: 'project-yunjing' });
  assert.equal(result.error, undefined);
  assert.equal(result.workspace.activeProjectId, 'project-qian');
  assert.equal(Projects.getProject(result.workspace, 'project-yunjing').state.campaign.topicId, 'T02');
  assert.equal(Projects.getProject(result.workspace, 'project-qian').state.campaign.topicId, null);
  assert.ok(Projects.dispatch(switched, 'project-qian', { type: 'SELECT_TOPIC', id: 'T02', propertyId: 'project-yunjing' }).error);
  assert.ok(Projects.switchProject(switched, 'missing').error);
});

// Catches the generated script using cloud seed dimensions after a project switch.
test('verified new project facts drive its generated script without original listing claims', () => {
  const created = Projects.createProject(workspace(), { name: '澄湾府', city: '苏州' });
  let work = created.workspace;
  const id = created.projectId;
  for (const fact of [
    { id: 'F01', value: '216', unit: '㎡', source: '澄湾府销售资料第 1 页' },
    { id: 'F02', value: '6.8', unit: '米', source: '澄湾府实测记录第 2 页' }
  ]) {
    work = act(work, id, 'UPDATE_FACT', fact);
    work = act(work, id, 'VERIFY_FACT', { id: fact.id });
  }
  work = act(work, id, 'SELECT_TOPIC', { id: 'T01' });
  work = act(work, id, 'GENERATE_SCRIPT');
  const script = Projects.getProject(work, id).state.campaign.script;
  assert.match(script, /澄湾府/);
  assert.match(script, /216 ㎡/);
  assert.match(script, /6\.8 米/);
  assert.doesNotMatch(script, /328|8\.2|云境/);
});

// Catches a false-ready state when a fact is verified by label but has no actual evidence.
test('missing facts block script generation and human verification rejects placeholders', () => {
  const created = Projects.createProject(workspace(), { name: '澄湾府', city: '苏州' });
  let work = act(created.workspace, created.projectId, 'SELECT_TOPIC', { id: 'T01' });
  assert.ok(Projects.dispatch(work, created.projectId, { type: 'GENERATE_SCRIPT' }).error);
  work = act(work, created.projectId, 'UPDATE_FACT', { id: 'F01', value: '待确认', source: '未提供' });
  assert.ok(Projects.dispatch(work, created.projectId, { type: 'VERIFY_FACT', id: 'F01' }).error);
});

// Catches marketing metadata changes leaving an old release valid or invalidating another project.
test('marketing scope and channel edits invalidate only that project and unconfirmed scope blocks release', () => {
  const original = approve(workspace(), 'project-yunjing');
  const changed = Projects.updateProject(original, 'project-yunjing', { marketingConfirmed: false, marketingEvidence: '' }).workspace;
  assert.equal(Projects.getProject(changed, 'project-yunjing').state.campaign.release.valid, false);
  assert.ok(Projects.dispatch(changed, 'project-yunjing', { type: 'APPROVE_RELEASE' }).error);
  assert.ok(Projects.dispatch(changed, 'project-yunjing', { type: 'EXPORT_RELEASE' }).error);
  const updatedB = Projects.updateProject(original, 'project-qian', { channel: '小红书' }).workspace;
  assert.equal(Projects.getProject(updatedB, 'project-yunjing').state.campaign.release.valid, true);
  assert.equal(Projects.getProject(updatedB, 'project-qian').state.campaign.channel, '小红书');
});

// Catches a renamed project approving the previous listing name after merely rerendering its old script.
test('renaming preserves the old script for review and blocks release until the current name is regenerated', () => {
  const original = approve(workspace(), 'project-yunjing');
  const oldScript = Projects.getProject(original, 'project-yunjing').state.campaign.script;
  let changed = Projects.updateProject(original, 'project-yunjing', { name: '澄湾府' }).workspace;
  let campaign = Projects.getProject(changed, 'project-yunjing').state.campaign;
  assert.equal(campaign.script, oldScript);
  assert.equal(campaign.projectNameNeedsReview, true);
  assert.equal(campaign.previousProjectName, '云境·壹号');
  changed = act(changed, 'project-yunjing', 'RENDER_PREVIEW');
  assert.ok(Projects.dispatch(changed, 'project-yunjing', { type: 'APPROVE_RELEASE' }).error);
  assert.ok(Projects.dispatch(changed, 'project-yunjing', { type: 'EXPORT_RELEASE' }).error);
  changed = act(changed, 'project-yunjing', 'GENERATE_SCRIPT');
  campaign = Projects.getProject(changed, 'project-yunjing').state.campaign;
  assert.equal(campaign.projectNameNeedsReview, false);
  assert.match(campaign.script, /澄湾府/);
  assert.doesNotMatch(campaign.script, /云境/);
  changed = act(changed, 'project-yunjing', 'RENDER_PREVIEW');
  assert.equal(act(changed, 'project-yunjing', 'APPROVE_RELEASE').projects[0].state.campaign.release.valid, true);
});

// Catches manual editing dismissing a name review while the old listing name remains in the text.
test('manual name review clears only after the script contains the current project and removes the old name', () => {
  let work = Projects.updateProject(approve(workspace(), 'project-yunjing'), 'project-yunjing', { name: '澄湾府' }).workspace;
  const oldScript = Projects.getProject(work, 'project-yunjing').state.campaign.script;
  work = act(work, 'project-yunjing', 'UPDATE_SCRIPT', { text: oldScript + '\n澄湾府内容待复核。' });
  assert.equal(Projects.getProject(work, 'project-yunjing').state.campaign.projectNameNeedsReview, true);
  work = act(work, 'project-yunjing', 'UPDATE_SCRIPT', { text: oldScript.replaceAll('云境·壹号', '澄湾府') });
  assert.equal(Projects.getProject(work, 'project-yunjing').state.campaign.projectNameNeedsReview, false);
  work = act(work, 'project-yunjing', 'RENDER_PREVIEW');
  assert.equal(act(work, 'project-yunjing', 'APPROVE_RELEASE').projects[0].state.campaign.release.valid, true);
  const untouched = Projects.updateProject(workspace(), 'project-qian', { name: '澄湾府' }).workspace;
  assert.notEqual(Projects.getProject(untouched, 'project-qian').state.campaign.projectNameNeedsReview, true);
});

// Catches legacy migration resetting the user's script or CRM edits in pre-workspace storage.
test('legacy state migration preserves user edits and supplies project scope metadata', () => {
  const legacy = Model.createInitialState();
  legacy.campaign.script = '用户已编辑的旧版脚本';
  legacy.leads[0].notes.push({ text: '旧版销售反馈' });
  const migrated = workspace(legacy);
  const state = Projects.getActiveProject(migrated).state;
  assert.equal(migrated.schemaVersion, 1);
  assert.equal(state.campaign.script, '用户已编辑的旧版脚本');
  assert.equal(state.leads[0].notes[0].text, '旧版销售反馈');
  assert.equal(state.property.id, 'project-yunjing');
  assert.equal(state.property.city, '上海');
  assert.equal(state.property.marketingPermission.confirmed, true);
});

// Catches counting templates, empty verified claims or checkbox-only scope as complete input.
test('completeness counts seven evidence based criteria and ignores placeholder pictures', () => {
  const work = workspace();
  assert.equal(Projects.getCompleteness(Projects.getActiveProject(work)).percent, 86);
  const empty = Projects.createProject(work, { name: '澄湾府', city: '苏州', propertyType: '庭院', owner: '周负责人' });
  const readiness = Projects.getCompleteness(Projects.getActiveProject(empty.workspace));
  assert.equal(readiness.total, 7);
  assert.equal(readiness.completed, 1);
  assert.equal(readiness.percent, 14);
  assert.equal(readiness.items.find(item => item.id === 'facts').complete, false);
  assert.equal(readiness.items.find(item => item.id === 'assets').complete, false);
  assert.deepEqual(readiness.items.map(item => item.action), ['edit-project-details', 'edit-project-details', 'project-facts', 'project-assets', 'project-assets', 'edit-project-details', 'edit-project-details']);
  const state = structuredClone(Projects.getActiveProject(empty.workspace).state);
  state.property = { name: '', city: '', propertyType: '', owner: '', marketingPermission: { confirmed: false, evidence: '' } };
  state.campaign.channel = '';
  state.facts.forEach(fact => { fact.status = 'verified'; });
  assert.equal(Projects.getCompleteness(state).percent, 0);
});

// Catches in-place writes that corrupt the state held by an in-flight project task.
test('project operations preserve frozen input and stored JSON round trips keep project isolation', () => {
  const frozen = freezeDeep(workspace());
  const switched = Projects.switchProject(frozen, 'project-qian').workspace;
  const changed = Projects.updateProject(switched, 'project-qian', { owner: '何负责人', audience: '庭院家庭', salesOwner: '何顾问' }).workspace;
  const created = Projects.createProject(frozen, { name: '澄湾府', city: '苏州' }).workspace;
  assert.equal(frozen.activeProjectId, 'project-yunjing');
  assert.equal(frozen.projects.length, 2);
  assert.equal(created.projects.length, 3);
  const restored = workspace(JSON.parse(JSON.stringify(Projects.sanitizeForStorage(changed))));
  assert.equal(restored.activeProjectId, 'project-qian');
  assert.equal(Projects.getActiveProject(restored).state.property.owner, '何负责人');
  assert.equal(Projects.getProject(restored, 'project-yunjing').state.leads.length, 3);
  assert.equal(Projects.getProject(restored, 'project-qian').state.leads.length, 0);
});

// Catches persisting dead blob URLs and retaining an approval whose footage disappears after reload.
test('storage removes local uploads and invalidates affected project output only', () => {
  let work = approve(workspace(), 'project-yunjing');
  work = act(work, 'project-yunjing', 'ADD_ASSET', { asset: { id: 'LOCAL001', name: '真实会客照片', type: 'image', src: 'blob:real-photo', localUpload: true } });
  work = act(work, 'project-yunjing', 'AUTHORIZE_ASSET', { id: 'LOCAL001' });
  work = act(work, 'project-yunjing', 'RENDER_PREVIEW');
  work = act(work, 'project-yunjing', 'APPROVE_RELEASE');
  work = act(work, 'project-yunjing', 'EXPORT_RELEASE');
  assert.equal(Projects.getCompleteness(Projects.getProject(work, 'project-yunjing')).percent, 100);
  const stored = Projects.sanitizeForStorage(freezeDeep(work));
  const state = Projects.getProject(stored, 'project-yunjing').state;
  assert.equal(state.assets.length, 4);
  assert.ok(state.assets.every(asset => !asset.localUpload));
  assert.equal(state.campaign.release.valid, false);
  assert.equal(state.campaign.exported, false);
  assert.equal(state.campaign.renderedVersion, null);
  assert.equal(Projects.getProject(work, 'project-yunjing').state.assets.length, 5);
});
