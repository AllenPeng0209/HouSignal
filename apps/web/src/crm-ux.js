/** Convert an optional datetime-local value into an explicit Shanghai deadline. */
export function toShanghaiTimestamp(value) {
 if (!value?.trim()) return null;
 const match = /^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2})$/.exec(value);
 if (!match) throw new Error('请填写有效的北京时间');
 const [, year, month, day, hour, minute] = match.map(Number);
 const date = new Date(Date.UTC(year, month - 1, day));
 if (date.getUTCFullYear() !== year || date.getUTCMonth() !== month - 1 || date.getUTCDate() !== day || hour > 23 || minute > 59) throw new Error('请填写有效的北京时间');
 return `${value}:00+08:00`;
}
/** A missing deadline remains absent, rather than becoming midnight or today. */
export function followupPayload(note, nextAction, localDateTime) {
 return {note, next_action: nextAction, next_action_at: toShanghaiTimestamp(localDateTime)};
}
/** Choosing a work does not imply the source has been verified. */
export function inquiryPayload(draft, requestKey) {
 return {...draft, publication_id: draft.publication_id || null, request_key: requestKey};
}
export function formatShanghaiTime(value) {
 if (!value) return '未安排具体时间';
 if (/^\d{4}-\d{2}-\d{2}$/.test(value)) return value.replaceAll('-', '/');
 const date = new Date(value);
 if (!Number.isFinite(date.getTime())) return '时间待核实';
 const parts = Object.fromEntries(new Intl.DateTimeFormat('en-GB', {timeZone:'Asia/Shanghai',year:'numeric',month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit',hourCycle:'h23'}).formatToParts(date).map(({type,value})=>[type,value]));
 return `${parts.year}/${parts.month}/${parts.day} ${parts.hour}:${parts.minute}`;
}
export function followupDueLabel(value, asOf = new Date().toISOString()) {
 if (!value) return '未安排具体时间';
 if (/^\d{4}-\d{2}-\d{2}$/.test(value)) return `${formatShanghaiTime(value)} · 具体时间待补充`;
 const timestamp = new Date(value).getTime();
 if (!Number.isFinite(timestamp)) return '时间待核实';
 return `${timestamp <= new Date(asOf).getTime() ? '已到期 · ' : ''}${formatShanghaiTime(value)}`;
}
/** @param {({page?:string}&Partial<import('./workspace-navigation').NavigationTarget>)|null|undefined} destination */
export function taskDestination(destination) {
 if (!destination || !['assistant','dashboard','studio','accounts','skills','property','assets','leads','reports','settings'].includes(destination.page || '')) return null;
 const {page,...target} = destination;
 return {page: /** @type {import('./types').Page} */ (page), target: /** @type {import('./workspace-navigation').NavigationTarget} */ (target)};
}
