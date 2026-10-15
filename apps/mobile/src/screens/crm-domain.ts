import type { Api } from '../lib/contracts';
import type { Report } from '../api/types';

export type InquiryDraft = { name: string; need: string; channel: string; contact: string; source_confidence: string; source_text: string; contactable: boolean; consent: boolean };
export type Checks = { contactable: boolean; consent: boolean; demand_confirmed: boolean; fit_confirmed: boolean };
export type Member = { id: string; user_id: string; name: string; email: string; role: string; active: boolean; project_ids: string[] | null };
export class InquiryIntent {
  private signature = '';
  private key = '';
  constructor(private makeKey: () => string = () => `inq_${Date.now().toString(36)}_${Math.random().toString(36).slice(2)}_${Math.random().toString(36).slice(2)}`) {}
  prepare(draft: InquiryDraft): InquiryDraft & { request_key: string } {
    const body = { ...draft, name: draft.name.trim(), need: draft.need.trim(), contact: draft.contact.trim(), source_text: draft.source_text.trim() };
    if (!body.name) throw new Error('请填写客户称呼');
    if (body.contact && !body.consent) throw new Error('保存联系方式前需确认客户联系授权');
    if (body.contactable && !body.contact) throw new Error('可联系咨询需填写联系方式');
    if (body.source_confidence === 'confirmed' && !body.source_text) throw new Error('请填写已核实来源的依据');
    const signature = JSON.stringify(body);
    if (signature !== this.signature) { this.signature = signature; this.key = this.makeKey(); }
    return { ...body, request_key: this.key };
  }
  complete(): void { this.signature = ''; this.key = ''; }
}
export function qualificationBody(checks: Checks, need: string, next: string, existing: string, same: boolean): Record<string, unknown> {
  if (![checks.contactable, checks.consent, checks.demand_confirmed, checks.fit_confirmed].every(v => v === true)) throw new Error('请逐项确认四项客户资格');
  if (existing && !same) throw new Error('关联已有客户前需确认是同一位客户');
  return { ...checks, need: need.trim(), next_action: next.trim(), ...(existing ? { existing_lead_id: existing, same_contact_confirmed: true } : {}) };
}
export async function revealInquiryContact(api: Api, pid: string, iid: string, purpose: string, confirmed: boolean): Promise<string> {
  if (!confirmed) throw new Error('请确认本次查看在客户联系授权范围内');
  if (!purpose.trim() || purpose.trim().length > 200) throw new Error('请填写本次联系用途，最多 200 字');
  const result = await api<{ contact: string }>(`/api/projects/${encodeURIComponent(pid)}/inquiries/${encodeURIComponent(iid)}/contact-reveal`, { method: 'POST', body: { purpose: purpose.trim() } });
  return result.contact;
}
export function phoneLink(contact: string): string | null {
  if (!/^\+?[\d ()-]+$/.test(contact.trim())) return null;
  const digits = contact.replace(/[ ()-]/g, '');
  return /^\+?\d{5,20}$/.test(digits) ? `tel:${digits}` : null;
}
export function metricValue(value: number | null | undefined): string { return value == null ? '待观察' : value.toLocaleString('zh-CN'); }
export function reportComplete(metrics: Report['metrics']): boolean { return metrics.window_complete && ['views', 'likes', 'comments', 'saves', 'inquiries', 'qualified'].every(key => typeof metrics[key as keyof Report['metrics']] === 'number'); }
export class RevealGate {
  private version = 0;
  begin(): number { return ++this.version; }
  invalidate(): void { this.version++; }
  allows(ticket: number): boolean { return ticket === this.version; }
}
export function salesMembers(members: Member[], pid: string): Member[] { return members.filter(m => m.active && ['owner', 'admin', 'sales'].includes(m.role) && (m.project_ids === null || m.project_ids.includes(pid))); }

export type MemberDraft = { name: string; email: string; password: string; role: string; active: boolean };
export function memberRequest(initial: Member | null, draft: MemberDraft, all: boolean, scope: string[], selfId: string): Record<string, unknown> {
  if (!Object.hasOwn(roleLabels, draft.role)) throw new Error('请选择受支持的成员角色');
  const project_ids = all ? null : [...new Set(scope)];
  if (initial) {
    if (initial.user_id === selfId && !draft.active) throw new Error('不能停用自己的账号，请由其他管理员操作');
    return { role: draft.role, active: draft.active, project_ids };
  }
  const email = draft.email.trim().toLowerCase();
  if (!draft.name.trim() || !/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(email)) throw new Error('请填写成员姓名与有效邮箱');
  if (draft.password.length < 12) throw new Error('初始密码至少 12 位');
  return { name: draft.name.trim(), email, password: draft.password, role: draft.role, project_ids };
}

export const sourceLabels: Record<string, string> = { confirmed: '来源已核实', self_reported: '客户自报', inferred: '推测来源', unknown: '来源未知' };
export const stageLabels: Record<string, string> = { qualified: '资格确认', appointment: '已预约', visit_completed: '已到访', opportunity: '洽谈中', won: '已成交', lost: '已结案', withdrawn: '授权已撤回' };
export const stages = ['qualified', 'appointment', 'visit_completed', 'opportunity', 'won'];
export const roleLabels: Record<string, string> = { owner: '组织负责人', admin: '管理员', content: '内容负责人', editor: '内容编辑', reviewer: '审核负责人', sales: '销售顾问', viewer: '查看成员' };
