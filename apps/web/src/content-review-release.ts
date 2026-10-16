import type {Content,Job} from './types';
import type {PlatformAccount} from './PlatformAccounts';
export interface ReviewFact {id:string;expected_revision?:number;revision?:number;label?:string;value?:string;unit?:string;source?:string;status?:string;valid_until?:string|null;valid:boolean;missing?:boolean}
export interface ReviewAsset {id:string;name?:string;kind?:string;rights?:string;rights_evidence?:string;rights_revision?:number;origin_type?:string;valid:boolean;missing?:boolean}
export interface ReviewShot {id:string;voiceover?:string;narration?:string;caption?:string;visual_direction?:string;asset_id?:string|null;fact_refs?:unknown[];duration?:number;[key:string]:unknown}
export interface ReviewFeedback {id:string;content_version_id:string;version:number;render_asset_id?:string|null;reviewer_name:string;note:string;shot_id?:string|null;sentence?:string|null;created_at:string}
export interface ReviewEvidence {content:Content;shots:ReviewShot[];facts:ReviewFact[];assets:ReviewAsset[];feedback:ReviewFeedback[];release_frozen:boolean}
export interface ReviewedContent extends Content {approval_valid?:boolean;approved_release_id?:string|null}
export interface ReleaseJob extends Job {preparation_request?:{platform:string;content_version_id:string};publication_request?:{platform:string;content_version_id:string;release_id:string}}
export function reviewEvidenceMatches(review:ReviewEvidence|null|undefined,content:Content):boolean {
 return Boolean(review&&content.render_asset_id&&review.content.id===content.id&&review.content.render_asset_id===content.render_asset_id&&review.facts.every(row=>row.valid===true)&&review.assets.every(row=>row.valid===true));
}
export function reviewFeedbackBody(review:ReviewEvidence,note:string,shotId:string,sentence:string) {
 const cleaned=note.trim();if(!cleaned||cleaned.length>4000)throw new Error('请填写 1 至 4000 字的修改意见。');
 const shot=shotId?review.shots.find(row=>row.id===shotId):null;
 if(shotId&&!shot)throw new Error('镜头已改变，请重新选择修改位置。');
 const originals=shot?[shot.voiceover,shot.narration,shot.caption].filter((value):value is string=>typeof value==='string'):[review.content.script];
 if(sentence&&!originals.some(text=>text.includes(sentence)))throw new Error('所选原文已改变，请重新选择。');
 return {note:cleaned,...(shotId?{shot_id:shotId}:{}),...(sentence?{sentence}:{}),expected_render_asset_id:review.content.render_asset_id||null};
}
export function reviewSentences(text:string):string[]{return text.match(/[^。！？!?\n]+[。！？!?]?/g)?.map(value=>value.trim()).filter(Boolean)||[];}
export function matchingReleaseJobs(jobs:ReleaseJob[],content:Content,releaseId:string,platform:string):ReleaseJob[]{
 return jobs.filter(job=>{const binding=job.publication_request||job.result;return Boolean(releaseId&&platform&&job.kind==='platform_publish'&&binding?.platform===platform&&binding?.content_version_id===content.id&&binding?.release_id===releaseId);});
}
export function releaseResultState(job:Job):{label:string;tone:string} {
 if(['queued','running'].includes(job.status))return {label:job.status==='queued'?'平台任务排队中':'平台正在处理',tone:'blue'};
 if(job.status==='failed'||job.status==='cancelled')return {label:job.status==='failed'?'平台任务失败':'平台任务已取消',tone:'amber'};
 const label:Record<string,string>={submitted:'已提交平台，待核对回执',draft_saved:'已保存到平台草稿箱',review_pending:'等待平台审核',reconciliation_required:'需要人工核对结果'};
 return {label:label[String(job.result?.status)]||'平台任务已结束，待核对回执',tone:job.result?.status==='reconciliation_required'?'amber':'blue'};
}
export function releasePlatformAccounts(accounts:PlatformAccount[],content:Content):PlatformAccount[]{
 const aliases:Record<string,string[]>={bilibili:['B站','B 站','哔哩哔哩'],'weixin-channels':['微信视频号','视频号'],'wechat-oa':['微信公众号','公众号']};
 return accounts.filter(account=>account.supported&&(aliases[account.platform]||[account.name]).includes(content.channel));
}
