import assert from 'node:assert/strict';
import { test } from 'node:test';
import { normalizeShots, validateRenderDraft, parseMetricValues, isVerifiedLive, safePublicationUrl, findApprovedArtifacts, reviewFingerprint, mergeMetricWindows } from '../src/domain/content-workflow';

test('restores both array and legacy object storyboards without discarding shot properties', () => {
  assert.deepEqual(normalizeShots({ shots: [{ asset_id: 'a', start: 2, duration: 15, caption: '采光' }] }), [{ asset_id: 'a', start: 2, duration: 15, caption: '采光' }]);
  assert.deepEqual(normalizeShots([{ duration: 5 }]), [{ duration: 5 }]);
});

test('render validation rejects unauthorized assets and total duration outside 15–60 seconds', () => {
  const assets = [{ id: 'a', rights: 'authorized', rights_evidence: '产权方授权', kind: 'image', origin_type: 'actual' }];
  assert.equal(validateRenderDraft([{ asset_id: 'a', duration: 15, start: 0 }], assets, false), null);
  assert.match(validateRenderDraft([{ asset_id: 'a', duration: 10 }], assets, false) || '', /15.*60/);
  assert.match(validateRenderDraft([{ asset_id: 'a', duration: 15 }], [{ ...assets[0], rights: 'pending' }], false) || '', /授权/);
  assert.match(validateRenderDraft([{ asset_id: 'a', duration: 15, start: NaN }], assets, false) || '', /起始/);
});

test('real-project render rejects AI and test images while test projects admit test materials', () => {
  const assets = [{ id: 'a', rights: 'authorized', rights_evidence: '授权', kind: 'image', origin_type: 'test' }];
  assert.match(validateRenderDraft([{ asset_id: 'a', duration: 15 }], assets, false) || '', /实拍/);
  assert.equal(validateRenderDraft([{ asset_id: 'a', duration: 15 }], assets, true), null);
  assert.match(validateRenderDraft([{ asset_id: 'a', duration: 15 }], [{ ...assets[0], origin_type: 'ai_generated' }], true) || '', /实拍/);
});

test('unknown metrics stay null and malformed, fractional and negative counts are rejected', () => {
  assert.deepEqual(parseMetricValues({ views: '', likes: '0', comments: '12' }), { views: null, likes: 0, comments: 12, saves: null, inquiries: null, qualified: null });
  for (const value of ['-1', '1.5', '1e3', 'Infinity', 'wat']) assert.throws(() => parseMetricValues({ views: value }), /非负整数/);
});

test('only a verified receipt in live platform state counts as public', () => {
  assert.equal(isVerifiedLive({ receipt_status: 'verified', platform_state: 'live' }), true);
  assert.equal(isVerifiedLive({ receipt_status: 'unverified', platform_state: 'live' }), false);
  assert.equal(isVerifiedLive({ receipt_status: 'verified', platform_state: 'removed' }), false);
});

test('publication URLs reject embedded credentials and non-web schemes', () => {
  assert.equal(safePublicationUrl(' https://www.douyin.com/video/123 '), 'https://www.douyin.com/video/123');
  for (const value of ['javascript:alert(1)', 'https://user:pass@example.com', 'https://', 'file:///tmp/x']) assert.equal(safePublicationUrl(value), null);
});

test('subtitle and cover exports use the exact approved render snapshot, not another version or render', () => {
  const assets = [
    { id: 'old', kind: 'subtitle', rights: 'authorized', metadata_json: { content_version_id: 'c', content_snapshot_sha256: 'old' } },
    { id: 'srt', kind: 'subtitle', rights: 'authorized', metadata_json: { content_version_id: 'c', content_snapshot_sha256: 'hash' } },
    { id: 'cover', kind: 'image', rights: 'authorized', metadata_json: { content_version_id: 'c', content_snapshot_sha256: 'hash', artifact_type: 'cover' } },
    { id: 'other', kind: 'subtitle', rights: 'authorized', metadata_json: { content_version_id: 'other', content_snapshot_sha256: 'hash' } },
  ];
  assert.deepEqual(findApprovedArtifacts(assets, 'c', 'hash'), { subtitle: assets[1], cover: assets[2] });
});
test('human confirmations invalidate when the actual render, referenced facts or rights change', () => {
  const content = { id: 'c', render_asset_id: 'render1', fact_refs: [{ id: 'f', revision: 1 }], storyboard: [{ asset_id: 'a' }] };
  const facts = [{ id: 'f', revision: 1, status: 'verified', valid: true }];
  const assets = [{ id: 'a', rights: 'authorized', rights_evidence: '授权范围一', rights_revision: 1 }];
  const original = reviewFingerprint(content, facts, assets);
  assert.notEqual(reviewFingerprint({ ...content, render_asset_id: 'render2' }, facts, assets), original);
  assert.notEqual(reviewFingerprint(content, [{ ...facts[0], revision: 2 }], assets), original);
  assert.notEqual(reviewFingerprint(content, [{ ...facts[0], valid: false }], assets), original);
  assert.notEqual(reviewFingerprint(content, facts, [{ ...assets[0], rights_revision: 2 }]), original);
  assert.notEqual(reviewFingerprint(content, facts, [{ ...assets[0], rights_evidence: '新授权范围' }]), original);
  assert.equal(reviewFingerprint(content, [...facts, { id: 'unrelated', revision: 10, status: 'verified', valid: true }], assets), original);
});
test('receipt verification leaves untouched windows unobserved and preserves a real zero count', () => {
  const existing = { D7: null, D30: { views: 12 } };
  assert.deepEqual(mergeMetricWindows(existing, { D7: {} }, []), { D7: null, D30: { views: 12 } });
  assert.deepEqual(mergeMetricWindows(existing, { D7: { views: '0' } }, ['D7']), { D7: { views: 0, likes: null, comments: null, saves: null, inquiries: null, qualified: null }, D30: { views: 12 } });
  assert.deepEqual(mergeMetricWindows({ D7: { views: 10 }, D30: null }, { D7: { views: '' } }, ['D7']), { D7: null, D30: null });
});
