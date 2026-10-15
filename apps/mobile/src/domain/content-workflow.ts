import type { Asset, Content, Fact, Project, Shot } from '../api/types';
export function normalizeShots(value: unknown): Shot[] {
  const shots = value && typeof value === 'object' && !Array.isArray(value) ? (value as { shots?: unknown }).shots : value;
  return Array.isArray(shots) ? shots.map(shot => ({ ...shot })) : [];
}
type Material = Pick<Asset, 'id' | 'rights' | 'rights_evidence' | 'kind' | 'origin_type'>;
export function validateRenderDraft(shots: Shot[], assets: Material[], isTest: boolean): string | null {
  if (!shots.length || shots.length > 20) return '请设置 1 至 20 个实际素材分镜。';
  let total = 0;
  for (const shot of shots) {
    const asset = assets.find(item => item.id === shot.asset_id);
    if (!asset || asset.rights !== 'authorized' || !asset.rights_evidence.trim()) return '分镜需选择当前项目已授权素材。';
    if (!['image', 'video'].includes(asset.kind) || asset.origin_type === 'ai_generated' || (!isTest && asset.origin_type !== 'actual')) return '请使用当前项目授权实拍素材。';
    const duration = Number(shot.duration ?? 5), start = Number(shot.start ?? 0);
    if (!Number.isFinite(duration) || duration < 1 || duration > 60) return '每个分镜时长须为 1 至 60 秒。';
    if (!Number.isFinite(start) || start < 0 || start > 600) return '素材起始位置须为 0 至 600 秒。';
    total += duration;
  }
  return total >= 15 && total <= 60 ? null : '成片总时长须为 15 至 60 秒。';
}
export const metricLabels: Record<string, string> = { views: '播放量', likes: '点赞量', comments: '评论量', saves: '收藏量', inquiries: '内容带来咨询', qualified: '内容带来资格客户' };
export function parseMetricValues(values: Record<string, string>): Record<string, number | null> {
  return Object.fromEntries(Object.keys(metricLabels).map(key => {
    const value = (values[key] || '').trim();
    if (!value) return [key, null];
    if (!/^\d+$/.test(value) || !Number.isSafeInteger(Number(value))) throw new Error(`${metricLabels[key]}须为非负整数，未观察请留空。`);
    return [key, Number(value)];
  }));
}
export function isVerifiedLive(record: { receipt_status: string; platform_state: string }): boolean { return record.receipt_status === 'verified' && record.platform_state === 'live'; }
export function safePublicationUrl(value: string): string | null {
  try {
    const url = new URL(value.trim());
    return ['https:', 'http:'].includes(url.protocol) && url.hostname && !url.username && !url.password ? url.toString() : null;
  } catch { return null; }
}
type Artifact = Pick<Asset, 'id' | 'kind' | 'rights' | 'metadata_json'>;
export function findApprovedArtifacts<T extends Artifact>(assets: T[], contentId: string, hash: string): { subtitle?: T; cover?: T } {
  const matching = assets.filter(asset => hash && asset.rights === 'authorized' && asset.metadata_json?.content_version_id === contentId && asset.metadata_json?.content_snapshot_sha256 === hash);
  return { subtitle: matching.find(asset => asset.kind === 'subtitle'), cover: matching.find(asset => asset.kind === 'image' && asset.metadata_json?.artifact_type === 'cover') };
}
export function reviewFingerprint(content: Pick<Content, 'id' | 'render_asset_id' | 'fact_refs' | 'storyboard'>, facts: Pick<Fact, 'id' | 'revision' | 'valid' | 'status' | 'valid_until'>[], assets: (Pick<Asset, 'id' | 'rights' | 'rights_evidence'> & { rights_revision?: number })[], marketing?: Pick<Project, 'marketing_confirmed' | 'marketing_evidence'>): string {
  const factIds = new Set(content.fact_refs.map(ref => typeof ref === 'string' ? ref : ref.id || ref.fact_id));
  const assetIds = new Set(normalizeShots(content.storyboard).map(shot => shot.asset_id));
  return JSON.stringify({ content: content.id, render: content.render_asset_id,
    facts: facts.filter(fact => factIds.has(fact.id)).map(fact => ({ id: fact.id, revision: fact.revision, valid: fact.valid, status: fact.status, valid_until: fact.valid_until })).sort((a, b) => a.id.localeCompare(b.id)),
    assets: assets.filter(asset => assetIds.has(asset.id)).map(asset => ({ id: asset.id, rights: asset.rights, evidence: asset.rights_evidence, revision: asset.rights_revision })).sort((a, b) => a.id.localeCompare(b.id)),
    marketing: marketing ? { confirmed: marketing.marketing_confirmed, evidence: marketing.marketing_evidence } : null });
}
export function mergeMetricWindows(existing: Record<string, Record<string, number | null> | null>, drafts: Record<string, Record<string, string>>, changed: string[]): Record<string, Record<string, number | null> | null> {
  const merged = { ...existing };
  for (const window of new Set(changed)) {
    if (!['D7', 'D30'].includes(window)) throw new Error('指标仅支持 D7、D30 观察窗口。');
    const values = parseMetricValues(drafts[window] || {});
    merged[window] = Object.values(values).some(value => value !== null) ? values : null;
  }
  return merged;
}
