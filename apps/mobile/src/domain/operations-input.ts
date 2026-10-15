export const platforms = ['抖音', '小红书', '快手', '知乎', 'B站', '微信视频号', '微信公众号'];
export const kindLabels: Record<string, string> = { trend: '热搜与趋势', industry: '行业资讯', gap: '内容缺口', event: '节日与事件', algorithm: '平台动态', competitor: '竞品分析', cross_platform: '跨平台差异', rss: 'RSS 线索', ugc: 'UGC 发现' };
export const topicLabels: Record<string, string> = { space: '空间尺度', light: '采光与景观', layout: '户型与动线', kitchen: '厨房细节', tour: '看房导览' };
export const planLabels: Record<string, string> = { draft: '选题草稿', scheduled: '已排期', completed: '已完成' };
export type Account = { id: string; platform: string; name: string; homepage: string; positioning: string; audience: string; voice: string; red_lines: string; status: 'managed' | 'paused' };
export type Opportunity = { id: string; title: string; kind: string; platform: string; source_url: string; summary: string; observed_at: string; status: 'saved' | 'planned' };
export type Plan = { id: string; title: string; angle: string; channel: string; scheduled_for: string | null; account_id: string | null; opportunity_id: string | null; topic_id: string; status: 'draft' | 'scheduled' | 'completed'; brief: string; source?: Opportunity | null; content_version_id?: string | null };
export type AccountForm = { name: string; platform: string; homepage: string; positioning: string; audience: string; voice: string; red_lines: string; status: string };
export type OpportunityForm = { title: string; kind: string; platform: string; source_url: string; summary: string };
export type PlanForm = { title: string; angle: string; channel: string; scheduled_for: string; account_id: string; opportunity_id: string; topic_id: string; status: string; brief: string };
export function defaultPlatform(value?: string) { return value && platforms.includes(value) ? value : '抖音'; }
export function publicUrl(value: unknown): string | null {
  if (typeof value !== 'string' || /[\s\u0000-\u001f]/.test(value)) return null;
  try {
    const url = new URL(value);
    if (!['http:', 'https:'].includes(url.protocol) || !url.hostname || url.username || url.password) return null;
    if ([...url.searchParams.keys()].some(key => /token|secret|api[_-]?key|password|auth/i.test(key))) return null;
    return url.toString();
  } catch { return null; }
}
function required(value: string, label: string, limit: number) {
  const clean = value.trim();
  if (!clean) throw new Error(`请填写${label}`);
  if (clean.length > limit) throw new Error(`${label}不能超过 ${limit} 字`);
  return clean;
}
export function validateAccount(form: Partial<AccountForm> & Pick<AccountForm, 'name' | 'platform' | 'homepage'>) {
  const name = required(form.name, '账号名称', 200);
  if (!platforms.includes(form.platform)) throw new Error('请选择运营平台');
  const homepage = form.homepage.trim();
  if (homepage && !publicUrl(homepage)) throw new Error('请填写有效且不含凭证的 HTTP(S) 账号主页');
  return { name, platform: form.platform, homepage, positioning: (form.positioning || '').trim(), audience: (form.audience || '').trim(), voice: (form.voice || '').trim(), red_lines: (form.red_lines || '').trim(), status: form.status || 'managed' };
}
export function validateOpportunity(form: OpportunityForm) {
  const title = required(form.title, '发现标题', 300);
  const source = publicUrl(form.source_url.trim());
  if (!source) throw new Error('请填写可核对的 HTTP(S) 原始来源链接');
  return { ...form, title, source_url: source, summary: form.summary.trim() };
}
export function validatePlan(form: PlanForm, accounts: Pick<Account, 'id' | 'platform' | 'status'>[], previous?: Pick<Plan, 'account_id' | 'opportunity_id'> | { opportunity_id: string }) {
  const title = required(form.title, '选题标题', 300);
  const scheduled = form.scheduled_for.trim();
  if (scheduled) {
    const date = new Date(`${scheduled}T12:00:00Z`);
    if (!/^\d{4}-\d{2}-\d{2}$/.test(scheduled) || Number.isNaN(date.getTime()) || date.toISOString().slice(0, 10) !== scheduled) throw new Error('请填写存在的 YYYY-MM-DD 日期');
  }
  if (form.status === 'scheduled' && !scheduled) throw new Error('已排期计划需要填写计划日期');
  const account = accounts.find(item => item.id === form.account_id);
  if (form.account_id && !account) throw new Error('关联账号暂不可用，请刷新账号');
  if (account && account.platform !== form.channel) throw new Error('计划渠道需与所选账号的平台一致');
  if (account?.status === 'paused' && (!previous || !('account_id' in previous) || previous.account_id !== account.id)) throw new Error('账号已暂停，请选择正在运营的账号');
  return { title, angle: form.angle.trim(), channel: form.channel, scheduled_for: scheduled || null, account_id: form.account_id || null, ...(!previous ? { opportunity_id: form.opportunity_id || null } : {}), topic_id: form.topic_id, status: form.status, brief: form.brief.trim() };
}
