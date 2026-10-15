import {api} from './api.js';
import {contentDirections,type ContentPlanDraft,type ContentTypeId} from './ContentDirectionGuide';
import type {Job} from './types';

export const planningMethods=[
 {id:'topic-matrix',label:'选题矩阵',description:'展开不同的受众问题与角度'},
 {id:'titles-and-hooks',label:'标题与开场',description:'让标题和前三秒回应受众'},
 {id:'topic-evaluation',label:'选题评估',description:'比较适配、成本和资料缺口'},
 {id:'series-planning',label:'系列规划',description:'让这一条成为可持续的系列'},
 {id:'cross-platform-reuse',label:'平台适配',description:'按目标平台调整表达'},
];
export const defaultPlanningMethods=['topic-matrix','titles-and-hooks','topic-evaluation'];
export interface PlanningRequest {
 content_type:ContentTypeId;subject:string;topic_id:string;channel:string;account_id:string|null;
 reference_id:string|null;skill_ids:string[];intent:string;target_seconds:number;
}
export interface PlanningConfig {
 content_type:ContentTypeId;subject:string;target_seconds:number;skill_ids:string[];
 hook:string;outline:string[];cta:string;suggestion_job_id?:string;candidate_id?:string;
}
export interface PlanningCandidate {
 id:string;title:string;angle:string;hook:string;outline:string[];cta:string;topic_id:string;
 why:string;missing:string[];reference_note:string;
}
export interface PlanningResult {candidates:PlanningCandidate[];context:PlanningRequest;jobId:string}
export type PlanningDraft=ContentPlanDraft&{target_seconds?:number};
export function supportedPlanningDuration(seconds:number):boolean{return Number.isInteger(seconds)&&(seconds>=15&&seconds<=60||seconds>=300&&seconds<=600);}
export function planningDurationLabel(seconds:number):string {
 if(seconds<60)return `${seconds} 秒`;
 const minutes=Math.floor(seconds/60),remainder=seconds%60;
 return remainder?`${minutes} 分 ${remainder} 秒`:`${minutes} 分钟`;
}
export function planningDurationBudget(seconds:number):{chapters:number;shotsMin:number;shotsMax:number} {
 const duration=Number.isFinite(seconds)?Math.max(15,Math.min(600,seconds)):300;
 return {chapters:duration>=600?8:duration>=300?6:duration>=120?4:3,
  shotsMin:Math.ceil(duration/10),shotsMax:Math.ceil(duration/(duration<=60?5:duration<=300?20/3:7.5))};
}
export function planningDraftState(draft?:PlanningDraft|null,channel='抖音'):PlanningRequest {
 const category=draft?.content_type||contentDirections.find(d=>draft?.brief?.includes(`内容类型：${d.label}`))?.id||'professional';
 const direction=contentDirections.find(d=>d.id===category)!;
 const duration=draft?.target_seconds;
 return {content_type:category,subject:draft?.subject||direction.subjects[0].query,topic_id:draft?.topic_id||'tour',channel,
  account_id:null,reference_id:draft?.reference_id||null,skill_ids:[...defaultPlanningMethods],intent:draft?draft.angle||draft.title:'',target_seconds:typeof duration==='number'&&supportedPlanningDuration(duration)?duration:300};
}
export function candidatePlan(context:PlanningRequest,candidate:PlanningCandidate,jobId:string) {
 return {title:candidate.title,angle:candidate.angle,channel:context.channel,account_id:context.account_id,
  reference_id:context.reference_id,topic_id:candidate.topic_id,scheduled_for:null,status:'draft' as const,
  brief:[`内容目标：${contentDirections.find(d=>d.id===context.content_type)?.goal||''}`,context.intent,
   candidate.reference_note?`参考借鉴：${candidate.reference_note}`:'',candidate.why?`选题理由：${candidate.why}`:'',
   candidate.missing.length?`待准备：${candidate.missing.join('；')}`:''].filter(Boolean).join('\n'),
  planning:{content_type:context.content_type,subject:context.subject,target_seconds:context.target_seconds,
   skill_ids:[...context.skill_ids],hook:candidate.hook,outline:[...candidate.outline],cta:candidate.cta,
   suggestion_job_id:jobId,candidate_id:candidate.id} satisfies PlanningConfig};
}
export function readPlanningSuggestions(job:Job):PlanningResult|null {
 if(job.status!=='succeeded')return null;
 const raw=job.result?.planning_suggestions as Partial<PlanningResult>|undefined,context=raw?.context;
 if(!context||!contentDirections.some(d=>d.id===context.content_type)||!Array.isArray(context.skill_ids)||
  typeof context.channel!=='string'||typeof context.subject!=='string'||typeof context.intent!=='string'||
  !supportedPlanningDuration(context.target_seconds)||
  !Array.isArray(raw?.candidates)||raw.candidates.length!==3)return null;
 const candidates=raw.candidates;
 if(candidates.some(c=>!c||['id','title','angle','hook','cta','why','reference_note','topic_id'].some(k=>typeof c[k as keyof PlanningCandidate]!=='string')||
  !c.id||!c.title||!['tour','space','light','layout','kitchen'].includes(c.topic_id)||
  !Array.isArray(c.outline)||!c.outline.length||c.outline.some(v=>typeof v!=='string')||!Array.isArray(c.missing)||c.missing.some(v=>typeof v!=='string'))||new Set(candidates.map(c=>c.id)).size!==3)return null;
 return {context,candidates,jobId:job.id};
}
export function requestPlanningSuggestions(projectId:string,body:PlanningRequest):Promise<{job_id:string}> {
 return api(`/api/projects/${encodeURIComponent(projectId)}/operations/planning/suggest`,{method:'POST',body:{...body,intent:body.intent.trim()}});
}
