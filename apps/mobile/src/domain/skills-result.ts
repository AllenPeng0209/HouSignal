import type { ScreenName } from '../lib/contracts';
export type Capability = { id: string; title: string; layer: string; description: string; easel_skill: string; mode: 'native' | 'easel' | 'manual'; target: ScreenName; status: 'ready' | 'setup' | 'unavailable' | 'manual'; reason: string };
export const skillLayers: Record<string, string> = { discover: '发现', plan: '策划', text_visual: '文字与视觉', audio_video: '音频与视频', publish_attribute: '发布与归因' };
export const skillStatuses: Record<string, string> = { ready: '可使用', setup: '待配置', unavailable: '暂不可用', manual: '人工确认' };
export type SkillArtifact = { name: string; url: string; mimeType: string };
export function canRunSkill(skill: Pick<Capability, 'mode' | 'status'>, canEdit: boolean, input: string): boolean {
  return canEdit && skill.mode === 'easel' && skill.status === 'ready' && input.trim().length > 0 && input.trim().length <= 6000;
}
export function skillResult(result: Record<string, unknown>, projectId: string, jobId: string): { text: string; artifacts: SkillArtifact[] } {
  const prefix = `/api/projects/${encodeURIComponent(projectId)}/operations/skills/artifacts/${encodeURIComponent(jobId)}/`;
  const artifacts: SkillArtifact[] = [];
  if (Array.isArray(result.artifacts)) for (const item of result.artifacts) {
    if (!item || typeof item !== 'object') continue;
    const row = item as Record<string, unknown>;
    if (typeof row.name !== 'string' || typeof row.url !== 'string' || /[\/\\\u0000-\u001f]/.test(row.name)) continue;
    if (row.url !== `${prefix}${encodeURIComponent(row.name)}`) continue;
    artifacts.push({ name: row.name, url: row.url, mimeType: typeof row.mime_type === 'string' ? row.mime_type : 'application/octet-stream' });
  }
  return { text: typeof result.response === 'string' ? result.response : '', artifacts };
}
export function skillDraftPlan(form: { title: string; angle: string; channel: string; topic_id: string; account_id: string; brief: string }, task: { id: string; skillTitle: string; input: string }) {
  const title = form.title.trim();
  if (!title || title.length > 300) throw new Error('请填写不超过 300 字的计划标题');
  const brief = [`来源：楼响技能「${task.skillTitle}」 · 任务 ${task.id}`, '结果状态：待人工核验，尚未发布。', ...(task.input.trim() ? [`原始要求：${task.input.trim()}`] : []), `任务回复：\n${form.brief.trim()}`].join('\n\n');
  if (brief.length > 12000) throw new Error('任务回复较长，请精简创作简报后保存（总计不超过 12000 字）');
  return { title, angle: form.angle.trim(), channel: form.channel, topic_id: form.topic_id, account_id: form.account_id || null, opportunity_id: null, status: 'draft', scheduled_for: null, brief };
}
