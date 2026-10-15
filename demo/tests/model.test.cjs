'use strict';
const { test } = require('node:test');
const assert = require('node:assert/strict');
const Model = require('../dist/model.js');

function act(state, type, payload = {}) {
  const result = Model.transition(state, { type, ...payload });
  assert.equal(result.error, undefined, result.error);
  return result.state;
}

function scripted() {
  let state = Model.createInitialState();
  state = act(state, 'SELECT_TOPIC', { id: state.topics[0].id });
  return act(state, 'GENERATE_SCRIPT');
}

function approved() {
  let state = act(scripted(), 'RENDER_PREVIEW');
  return act(state, 'APPROVE_RELEASE');
}

function freezeDeep(value) {
  Object.freeze(value);
  Object.values(value).forEach(child => {
    if (child && typeof child === 'object' && !Object.isFrozen(child)) freezeDeep(child);
  });
  return value;
}

// Catches treating downloads, pending receipts or views as qualification results.
test('initial metrics count verified live publications and human-confirmed distinct leads', () => {
  const state = Model.createInitialState();
  assert.equal(state.demo, true);
  assert.deepEqual(Model.getMetrics(state), {
    published: 2, countsPublished: 2, qualified: 3, appointments: 2,
    visits: 1, inquiries: 4, views: 14100, unknownRate: 25
  });
  state.leads.push({ ...state.leads[0] });
  assert.equal(Model.getMetrics(state).qualified, 3);
});

// Catches accidental mutation of the prior state during editing or topic selection.
test('topic selection leaves the input state unchanged and requires a real topic', () => {
  const state = freezeDeep(Model.createInitialState());
  const next = act(state, 'SELECT_TOPIC', { id: state.topics[1].id });
  assert.equal(next.campaign.topicId, state.topics[1].id);
  assert.equal(state.campaign.topicId, null);
  assert.notEqual(next, state);
  assert.ok(Model.transition(state, { type: 'SELECT_TOPIC', id: 'missing' }).error);
});

// Catches approval being granted without a current visual output.
test('a script cannot be approved or exported before a current preview exists', () => {
  const state = scripted();
  assert.ok(state.campaign.script.trim().length > 20);
  assert.ok(Model.transition(state, { type: 'APPROVE_RELEASE' }).error);
  assert.ok(Model.transition(state, { type: 'EXPORT_RELEASE' }).error);
  const next = act(act(state, 'RENDER_PREVIEW'), 'APPROVE_RELEASE');
  assert.equal(next.campaign.release.scriptVersion, next.campaign.scriptVersion);
  const exported = act(next, 'EXPORT_RELEASE');
  assert.equal(exported.campaign.exported, true);
  assert.equal(Model.getMetrics(exported).published, 2);
});

// Catches approval ignoring an expired required fact.
test('expired required facts block approval even if the preview is current', () => {
  let state = scripted();
  state = act(state, 'EXPIRE_FACT', { id: state.facts.find(f => f.required).id });
  state = act(state, 'RENDER_PREVIEW');
  assert.ok(Model.transition(state, { type: 'APPROVE_RELEASE' }).error);
  state = act(state, 'VERIFY_FACT', { id: state.facts.find(f => f.required).id });
  state = act(state, 'RENDER_PREVIEW');
  assert.equal(act(state, 'APPROVE_RELEASE').campaign.release.valid, true);
});

// Catches a placeholder price being verified without a supplied value or evidence.
test('a placeholder quotation cannot be verified until real value and evidence are supplied', () => {
  const state = Model.createInitialState();
  const quotation = state.facts.find(fact => !fact.required && fact.status === 'unverified');
  const result = Model.transition(state, { type: 'VERIFY_FACT', id: quotation.id });
  assert.ok(result.error);
  assert.deepEqual(result.state, state);
  assert.equal(result.state.facts.find(fact => fact.id === quotation.id).status, 'unverified');
});

// Catches unauthorized visuals entering the publishable package.
test('unlicensed source material blocks preview and release approval', () => {
  const state = scripted();
  state.assets[0].rights = 'pending';
  assert.ok(Model.transition(state, { type: 'RENDER_PREVIEW' }).error);
  state.campaign.renderedVersion = state.campaign.scriptVersion;
  assert.ok(Model.transition(state, { type: 'APPROVE_RELEASE' }).error);
});

// Catches an edit carrying an older approval into a new download.
test('editing an approved script invalidates its approval and current preview', () => {
  const state = approved();
  const next = act(state, 'UPDATE_SCRIPT', { text: state.campaign.script + '\n补充经人工核对的生活取舍。' });
  assert.equal(next.campaign.scriptVersion, state.campaign.scriptVersion + 1);
  assert.equal(next.campaign.renderedVersion, null);
  assert.equal(next.campaign.release.valid, false);
  assert.ok(Model.transition(next, { type: 'EXPORT_RELEASE' }).error);
  assert.equal(state.campaign.release.valid, true);
});

// Catches packaging edits bypassing approval while preserving the unchanged script preview.
test('changing cover or channel invalidates approval and the new approval snapshots packaging', () => {
  const state = approved();
  let next = act(state, 'UPDATE_PACKAGING', { coverTitle: '家的尺度，回应生活', channel: '小红书' });
  assert.equal(next.campaign.release.valid, false);
  assert.equal(next.campaign.exported, false);
  assert.equal(next.campaign.scriptVersion, state.campaign.scriptVersion);
  assert.equal(next.campaign.renderedVersion, state.campaign.renderedVersion);
  assert.ok(Model.transition(next, { type: 'EXPORT_RELEASE' }).error);
  next = act(next, 'APPROVE_RELEASE');
  assert.equal(next.campaign.release.coverTitle, '家的尺度，回应生活');
  assert.equal(next.campaign.release.channel, '小红书');
  assert.equal(act(next, 'EXPORT_RELEASE').campaign.exported, true);
});

// Catches download trusting release.valid despite a channel or title snapshot mismatch.
test('export rejects packaging that differs from the approved release snapshot', () => {
  const state = approved();
  const channelChanged = structuredClone(state);
  channelChanged.campaign.channel = '小红书';
  assert.ok(Model.transition(channelChanged, { type: 'EXPORT_RELEASE' }).error);
  const titleChanged = structuredClone(state);
  titleChanged.campaign.coverTitle = '未经批准的新封面';
  assert.ok(Model.transition(titleChanged, { type: 'EXPORT_RELEASE' }).error);
});

// Catches accepting unsupported publishing channels and altering state on validation failure.
test('packaging only accepts the supported channels and preserves the original on rejection', () => {
  const state = approved();
  const result = Model.transition(state, { type: 'UPDATE_PACKAGING', channel: '未知平台' });
  assert.ok(result.error);
  assert.deepEqual(result.state, state);
  const next = act(state, 'UPDATE_PACKAGING', { channel: '抖音' });
  assert.equal(next.campaign.release.valid, true);
});

// Catches added or newly authorized local footage being exported using an older asset snapshot.
test('adding and authorizing a local demo asset requires a new preview and release approval', () => {
  const state = act(approved(), 'EXPORT_RELEASE');
  let next = act(state, 'ADD_ASSET', { asset: { id: 'LOCAL001', name: '本地示例照片', type: 'image', space: '会客', rights: 'pending', src: 'blob:demo-local-photo', localUpload: true } });
  assert.equal(next.assets.length, 5);
  assert.equal(next.campaign.release.valid, false);
  assert.equal(next.campaign.exported, false);
  assert.equal(next.campaign.renderedVersion, null);
  next = act(next, 'AUTHORIZE_ASSET', { id: 'LOCAL001' });
  assert.equal(next.assets.find(asset => asset.id === 'LOCAL001').rights, 'authorized');
  assert.ok(Model.transition(next, { type: 'EXPORT_RELEASE' }).error);
  assert.ok(Model.transition(next, { type: 'APPROVE_RELEASE' }).error);
  next = act(act(next, 'RENDER_PREVIEW'), 'APPROVE_RELEASE');
  assert.ok(next.campaign.release.assetIds.includes('LOCAL001'));
  assert.equal(act(next, 'EXPORT_RELEASE').campaign.exported, true);
  assert.equal(state.assets.length, 4);
});

// Catches media setting edits leaving a prior preview eligible for approval or download.
test('aspect ratio and subtitle changes require a fresh preview and matching approval snapshot', () => {
  const state = approved();
  let next = act(state, 'UPDATE_PACKAGING', { aspectRatio: '16:9', subtitleStyle: 'emphasis' });
  assert.equal(next.campaign.scriptVersion, state.campaign.scriptVersion);
  assert.equal(next.campaign.renderedVersion, null);
  assert.equal(next.campaign.release.valid, false);
  assert.ok(Model.transition(next, { type: 'APPROVE_RELEASE' }).error);
  assert.ok(Model.transition(next, { type: 'EXPORT_RELEASE' }).error);
  next = act(act(next, 'RENDER_PREVIEW'), 'APPROVE_RELEASE');
  assert.equal(next.campaign.release.aspectRatio, '16:9');
  assert.equal(next.campaign.release.subtitleStyle, 'emphasis');
  assert.equal(act(next, 'EXPORT_RELEASE').campaign.exported, true);
  const mismatch = structuredClone(next);
  mismatch.campaign.subtitleStyle = 'standard';
  assert.ok(Model.transition(mismatch, { type: 'EXPORT_RELEASE' }).error);
});

// Catches an approved file staying downloadable after its evidence expires.
test('fact expiration invalidates an existing approval and prevents export', () => {
  const state = approved();
  const next = act(state, 'EXPIRE_FACT', { id: state.facts.find(f => f.required).id });
  assert.equal(next.campaign.release.valid, false);
  assert.ok(Model.transition(next, { type: 'EXPORT_RELEASE' }).error);
});

// Catches manual edits introducing an unverified or nonexistent fact reference.
test('new fact references in edited scripts must also pass evidence verification', () => {
  const state = scripted();
  const quotation = state.facts.find(fact => !fact.required && fact.status === 'unverified');
  let next = act(state, 'UPDATE_SCRIPT', { text: state.campaign.script + '\n报价说明 [' + quotation.id + ']' });
  next = act(next, 'RENDER_PREVIEW');
  assert.ok(Model.transition(next, { type: 'APPROVE_RELEASE' }).error);
  next = act(state, 'UPDATE_SCRIPT', { text: state.campaign.script + '\n新增主张 [F99]' });
  next = act(next, 'RENDER_PREVIEW');
  assert.ok(Model.transition(next, { type: 'APPROVE_RELEASE' }).error);
});

// Catches a local export or unverified receipt being counted as a live public post.
test('publication requires a receipt and remains recorded until separately verified', () => {
  let state = act(approved(), 'EXPORT_RELEASE');
  assert.ok(Model.transition(state, { type: 'RECORD_PUBLICATION', url: '' }).error);
  state = act(state, 'RECORD_PUBLICATION', { url: 'https://example.com/demo/post-1', platformState: 'live' });
  const publication = state.publications.at(-1);
  assert.equal(publication.status, 'recorded');
  assert.equal(publication.platformState, 'live');
  assert.equal(publication.demo, true);
  assert.equal(publication.views, null);
  assert.equal(Model.getMetrics(state).published, 2);
});

// Catches a missing human receipt confirmation upgrading recorded data into verified data.
test('publication verification requires an explicit human confirmation', () => {
  const state = Model.createInitialState();
  const id = state.publications.find(publication => publication.status === 'recorded').id;
  const missing = Model.transition(state, { type: 'VERIFY_PUBLICATION', id, platformState: 'live' });
  assert.ok(missing.error);
  assert.deepEqual(missing.state, state);
  const declined = Model.transition(state, { type: 'VERIFY_PUBLICATION', id, receiptConfirmed: false, platformState: 'live' });
  assert.ok(declined.error);
  const confirmed = act(state, 'VERIFY_PUBLICATION', { id, receiptConfirmed: true, platformState: 'review_pending' });
  assert.equal(confirmed.publications.find(publication => publication.id === id).status, 'verified');
});

// Catches platform review being counted as publication or removal remaining publicly live.
test('verified receipts only count as published when the human records the platform as live', () => {
  let state = Model.createInitialState();
  const id = state.publications.find(publication => publication.status === 'recorded').id;
  state = act(state, 'VERIFY_PUBLICATION', { id, receiptConfirmed: true, platformState: 'review_pending' });
  assert.equal(state.publications.find(publication => publication.id === id).status, 'verified');
  assert.equal(Model.getMetrics(state).published, 2);
  state = act(state, 'VERIFY_PUBLICATION', { id, receiptConfirmed: true, platformState: 'live' });
  const publication = state.publications.find(publication => publication.id === id);
  assert.equal(publication.status, 'verified');
  assert.equal(publication.platformState, 'live');
  assert.equal(publication.demo, true);
  assert.equal(publication.views, null);
  assert.equal(Model.getMetrics(state).published, 3);
  state = act(state, 'VERIFY_PUBLICATION', { id, receiptConfirmed: true, platformState: 'removed' });
  assert.equal(state.publications.find(publication => publication.id === id).status, 'removed');
  assert.equal(Model.getMetrics(state).published, 2);
});

// Catches model scoring, phone availability or a partial check producing a qualified lead.
test('qualification needs all four human checks and rejects an unknown demand', () => {
  const state = Model.createInitialState();
  const id = state.inquiries.find(i => !i.qualified).id;
  assert.ok(Model.transition(state, { type: 'QUALIFY_INQUIRY', id }).error);
  assert.ok(Model.transition(state, { type: 'QUALIFY_INQUIRY', id, contactable: true, consent: true, demandConfirmed: false, fitConfirmed: true }).error);
  assert.ok(Model.transition(state, { type: 'QUALIFY_INQUIRY', id, contactable: true, consent: false, demandConfirmed: true, fitConfirmed: true }).error);
  assert.equal(state.leads.length, 3);
});

// Catches repeated qualification adding duplicate leads or inventing confirmed attribution.
test('human qualification adds one lead and preserves unknown source confidence', () => {
  let state = Model.createInitialState();
  const id = state.inquiries.find(i => !i.qualified).id;
  const checks = { id, contactable: true, consent: true, demandConfirmed: true, fitConfirmed: true };
  state = act(state, 'QUALIFY_INQUIRY', checks);
  assert.equal(state.leads.length, 4);
  const lead = state.leads.find(l => l.inquiryId === id);
  assert.equal(lead.stage, 'qualified');
  assert.equal(lead.sourceConfidence, 'unknown');
  state = act(state, 'QUALIFY_INQUIRY', checks);
  assert.equal(state.leads.length, 4);
  assert.equal(Model.getMetrics(state).qualified, 4);
});

// Catches a new project's unassigned inquiry inheriting the seed project's sales owner.
test('an unassigned inquiry uses the current project sales owner during qualification', () => {
  const state = Model.createInitialState();
  state.property = { ...state.property, id: 'project-new', name: '澄湾府', salesOwner: '唐顾问' };
  state.inquiries = [{ id: 'NEW001', name: '何女士', channel: '抖音', sourceConfidence: 'unknown', need: '关注庭院生活', owner: '待分配', qualified: false }];
  state.leads = [];
  const next = act(state, 'QUALIFY_INQUIRY', { id: 'NEW001', contactable: true, consent: true, demandConfirmed: true, fitConfirmed: true });
  assert.equal(next.inquiries[0].owner, '唐顾问');
  assert.equal(next.leads[0].owner, '唐顾问');
  assert.equal(state.inquiries[0].owner, '待分配');
});

// Catches appointment instantly counting as a visit or accepting a skipped stage.
test('booking an appointment does not complete a visit and stage order is enforced', () => {
  let state = Model.createInitialState();
  const id = state.leads.find(l => l.stage === 'qualified').id;
  assert.ok(Model.transition(state, { type: 'MOVE_LEAD', id, stage: 'won' }).error);
  state = act(state, 'MOVE_LEAD', { id, stage: 'appointment' });
  assert.equal(Model.getMetrics(state).appointments, 3);
  assert.equal(Model.getMetrics(state).visits, 1);
  state = act(state, 'MOVE_LEAD', { id, stage: 'visit_completed' });
  assert.equal(Model.getMetrics(state).visits, 2);
});

// Catches follow-up continuing after a person withdraws permission.
test('withdrawal stops follow-up and cannot be reversed by moving the stage', () => {
  let state = Model.createInitialState();
  const id = state.leads[0].id;
  state = act(state, 'MOVE_LEAD', { id, stage: 'withdrawn' });
  assert.ok(Model.transition(state, { type: 'SAVE_FOLLOWUP', id, note: '再次联系', nextAction: '电话' }).error);
  assert.ok(Model.transition(state, { type: 'MOVE_LEAD', id, stage: 'qualified' }).error);
  assert.equal(Model.getMetrics(state).qualified, 3);
  assert.equal(state.inquiries.find(i => i.id === state.leads[0].inquiryId).consent, false);
});

// Catches feedback overwriting earlier notes or auto-executing an AI proposal.
test('follow-up preserves history and proposal approval does not publish or change facts', () => {
  let state = Model.createInitialState();
  const id = state.leads[0].id;
  state = act(state, 'SAVE_FOLLOWUP', { id, note: '客户主动询问厨房动线', nextAction: '周二通过原会话回应' });
  state = act(state, 'SAVE_FOLLOWUP', { id, note: '已说明取舍', nextAction: '待客户确认时间' });
  assert.equal(state.leads[0].notes.length, 2);
  const priorFacts = JSON.stringify(state.facts);
  const priorPublicationCount = state.publications.length;
  state = act(state, 'APPROVE_PROPOSAL', { id: state.proposals[0].id });
  assert.equal(state.proposals[0].status, 'approved');
  assert.equal(JSON.stringify(state.facts), priorFacts);
  assert.equal(state.publications.length, priorPublicationCount);
});

// Catches recorded values being auto-verified and a release surviving evidence edits.
test('recording changed fact evidence needs human verification and invalidates current approval', () => {
  const state = approved();
  let next = act(state, 'UPDATE_FACT', { id: 'F01', value: '216', unit: '㎡', source: '新项目资料第 1 页', status: 'verified' });
  assert.equal(next.facts[0].value, '216');
  assert.equal(next.facts[0].status, 'unverified');
  assert.equal(next.campaign.release.valid, false);
  assert.equal(next.campaign.renderedVersion, null);
  assert.doesNotMatch(next.topics.find(topic => topic.id === 'T04').title, /328/);
  assert.ok(Model.transition(next, { type: 'GENERATE_SCRIPT' }).error);
  next = act(next, 'VERIFY_FACT', { id: 'F01' });
  next = act(next, 'GENERATE_SCRIPT');
  assert.match(next.campaign.script, /216 ㎡/);
  assert.doesNotMatch(next.campaign.script, /328 ㎡/);
});

// Catches status-only verification or the old fixed script using invalid evidence.
test('generation rejects a verified fact that has an empty value or placeholder source', () => {
  const state = act(Model.createInitialState(), 'SELECT_TOPIC', { id: 'T01' });
  state.facts[0].value = '';
  assert.ok(Model.transition(state, { type: 'GENERATE_SCRIPT' }).error);
  state.facts[0].value = '216';
  state.facts[0].source = '待补资料';
  assert.ok(Model.transition(state, { type: 'GENERATE_SCRIPT' }).error);
});

// Catches a new project's empty or source-less media positions earning a completed preview.
test('preview requires a nonempty visual collection with actual preview sources', () => {
  const state = scripted();
  state.assets = [];
  assert.ok(Model.transition(state, { type: 'RENDER_PREVIEW' }).error);
  state.assets = [{ id: 'EMPTY01', name: '待上传照片', type: 'image', rights: 'authorized', src: null }];
  assert.ok(Model.transition(state, { type: 'RENDER_PREVIEW' }).error);
  state.campaign.renderedVersion = state.campaign.scriptVersion;
  assert.ok(Model.transition(state, { type: 'APPROVE_RELEASE' }).error);
});
