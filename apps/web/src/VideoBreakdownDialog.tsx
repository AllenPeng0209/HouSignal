import {useEffect,useRef,useState,type FormEvent} from 'react';
import {api,safeUrl} from './api.js';
import {planningDurationLabel,supportedPlanningDuration,type PlanningConfig} from './planning-model';
import {useApp} from './hooks';
import {Badge,Button,ErrorNotice,Field,Modal} from './components';
import './video-breakdown.css';

type TopicId='tour'|'space'|'light'|'layout'|'kitchen';
export interface BreakdownSource {source_url:string;title:string;opportunity_id?:string;reference_id?:string;analysis_job_id?:string;initial_notes?:Partial<Pick<BreakdownInput,'transcript'|'observations'|'target_seconds'|'topic_id'>>}
export interface BreakdownInput {source_url:string;title:string;transcript:string;observations:string;target_seconds:number;topic_id:TopicId;reference_id?:string;analysis_job_id?:string}
export interface PlanSeed {title:string;angle:string;brief:string;topic_id:TopicId;channel:string;planning?:PlanningConfig}
export interface VideoBreakdownResult {
 source:{url:string;title:string;watched:false;evidence_basis:'user_supplied_notes'|'asset_asr_and_sampled_frames'};
 timeline:{start_seconds:number|null;end_seconds:number|null;transcript:string;visual:string;evidence_status:string}[];
 hook:{evidence:string;status:'user_supplied'|'pending';questions:string[]};
 adaptation:{topic_id:TopicId;eligible:boolean;missing_facts:string[];fact_refs:{id:string;revision:number;label:string;value:string|number;unit?:string;source?:string}[];shots:{start_seconds:number;end_seconds:number;purpose:string;voiceover:string;visual:string;evidence_status:string}[];chapters?:{id:string;title:string;target_seconds:number;purpose:string;evidence_status:'proposed'}[];needs_chapter_expansion?:boolean};
 report:string;plan_seed:PlanSeed;
}
const topics:Record<TopicId,string>={tour:'看房导览',space:'空间尺度',light:'采光与景观',layout:'户型与动线',kitchen:'厨房细节'};
const channels=['抖音','小红书','快手','知乎','B站','微信视频号','微信公众号'];
const analyzedEvidenceNote='依据原片语音转写、采样画面与已核对笔记改编。采样不能代表逐帧观看完整视频；内容与时间码仍需对照原片复核。';
const evidenceNote='仅根据你提供的口播和画面笔记拆解，未自动观看原片。时间码与内容需对照原片复核。';

function normalizeReference(input:BreakdownInput):BreakdownInput {
 const body={...input,source_url:input.source_url.trim(),title:input.title.trim(),transcript:input.transcript.trim(),observations:input.observations.trim()};
 if(!safeUrl(body.source_url)||!body.title)throw new Error('请填写原片标题和有效的原片链接。');
 if(body.title.length>300)throw new Error('原片标题最多 300 字。');
 if(body.transcript.length>10000||body.observations.length>10000)throw new Error('口播和画面笔记每项最多 10000 字，请保留需要拆解的关键片段。');
 if(!body.transcript&&!body.observations)throw new Error('请至少提供一段原片口播、字幕或画面笔记。');
 if(!Number.isInteger(body.target_seconds)||body.target_seconds<15||body.target_seconds>600)throw new Error('目标时长须为 15 至 600 秒的整数。');
 if(!supportedPlanningDuration(body.target_seconds))throw new Error('请选择 15–60 秒短视频或 300–600 秒长视频。');
 return body;
}
function normalizePlan(seed:PlanSeed):PlanSeed {return {...seed,title:seed.title.trim(),angle:seed.angle.trim(),brief:seed.brief.trim()};}
export async function requestVideoBreakdown(projectId:string,input:BreakdownInput,signal?:AbortSignal):Promise<VideoBreakdownResult> {
 return api(`/api/projects/${encodeURIComponent(projectId)}/operations/discovery/breakdown`,{method:'POST',body:normalizeReference(input),signal});
}
export async function requestBreakdownPlan(projectId:string,reference:BreakdownInput,plan:PlanSeed,idempotencyKey:string,opportunityId?:string,signal?:AbortSignal):Promise<{id:string}> {
 return api(`/api/projects/${encodeURIComponent(projectId)}/operations/discovery/breakdown/plan`,{method:'POST',body:{reference:normalizeReference(reference),plan:normalizePlan(plan),opportunity_id:opportunityId||null,idempotency_key:idempotencyKey},signal});
}
function timeRange(start:number|null,end:number|null){return start===null||end===null?'时间待核对':start===end?`${start} 秒（采样时刻）`:`${start}–${end} 秒`;}

export function VideoBreakdownPreview({result}:{result:VideoBreakdownResult}) {
 const analyzed=result.source.evidence_basis==='asset_asr_and_sampled_frames',chapterOutline=Boolean(result.adaptation.needs_chapter_expansion||result.adaptation.chapters?.length);
 return <section className="video-breakdown-preview" aria-label="拆解与改编预览">
  <p className="video-breakdown-evidence">{analyzed?analyzedEvidenceNote:evidenceNote}</p>
  <div className="video-breakdown-heading"><h3>原片拆解</h3><Badge>{analyzed?'语音转写与采样画面':'用户提供的片段'}</Badge></div>
  <div className="video-breakdown-hook"><strong>开场钩子</strong><p>{result.hook.evidence||'尚未提供可核对的开场片段'}</p>{result.hook.questions.length>0&&<ul>{result.hook.questions.map((question,index)=><li key={index}>{question}</li>)}</ul>}</div>
  <ol className="video-breakdown-timeline">{result.timeline.map((row,index)=><li key={index}><span>{timeRange(row.start_seconds,row.end_seconds)}</span><div>{row.transcript&&<p><strong>口播：</strong>{row.transcript}</p>}{row.visual&&<p><strong>画面：</strong>{row.visual}</p>}</div></li>)}</ol>
  <div className="video-breakdown-heading"><h3>{chapterOutline?'项目长片章纲':'项目改编分镜'}</h3><Badge tone={result.adaptation.eligible?'green':'amber'}>{result.adaptation.eligible?'项目资料可用于改编':'项目资料待补齐'}</Badge></div>
  {result.adaptation.missing_facts.length>0&&<p className="video-breakdown-missing">待补齐：{result.adaptation.missing_facts.join('、')}。可先保存草稿，补齐资料后再生成正式选题脚本。</p>}
  {chapterOutline?<><p className="video-breakdown-evidence">这是章纲建议，完整口播与分镜待展开。采用并保存计划后，逐章生成 30 个以上镜头，再核对素材与项目依据。</p><ol className="video-breakdown-shots">{result.adaptation.chapters?.map((chapter,index)=><li key={chapter.id}><span>第 {index+1} 章 · {chapter.title} · {planningDurationLabel(chapter.target_seconds)}</span><p>{chapter.purpose}</p><Badge tone="amber">章纲建议 · 待展开</Badge></li>)}</ol></>:<ol className="video-breakdown-shots">{result.adaptation.shots.map((shot,index)=><li key={index}><span>{timeRange(shot.start_seconds,shot.end_seconds)} · {shot.purpose}</span><p><strong>口播：</strong>{shot.voiceover}</p><p><strong>画面：</strong>{shot.visual}</p></li>)}</ol>}
  <details className="video-breakdown-report"><summary>完整拆解报告</summary><pre>{result.report}</pre></details>
 </section>;
}

export function VideoBreakdownDialog({source,onClose,onSaved}:{source:BreakdownSource;onClose:()=>void;onSaved:()=>void}) {
 const {project,session,notify}=useApp(),editable=['owner','admin','content'].includes(session.role);
 const [form,setForm]=useState<BreakdownInput>({source_url:source.source_url,title:source.title,transcript:source.initial_notes?.transcript||'',observations:source.initial_notes?.observations||'',target_seconds:source.initial_notes?.target_seconds??300,topic_id:source.initial_notes?.topic_id||'tour',...(source.reference_id?{reference_id:source.reference_id}:{}),...(source.analysis_job_id?{analysis_job_id:source.analysis_job_id}:{})});
 const [result,setResult]=useState<VideoBreakdownResult|null>(null),[plan,setPlan]=useState<PlanSeed|null>(null),[busy,setBusy]=useState<'breakdown'|'save'|null>(null),[error,setError]=useState('');
 const request=useRef<AbortController|null>(null),mounted=useRef(true),currentProject=useRef(project.id),inFlight=useRef(false);
 const previewReference=useRef<BreakdownInput|null>(null),saveAttempt=useRef<{body:string;key:string}|null>(null);
 currentProject.current=project.id;
 useEffect(()=>{mounted.current=true;return()=>{mounted.current=false;request.current?.abort();};},[]);
 useEffect(()=>{request.current?.abort();request.current=null;inFlight.current=false;previewReference.current=null;saveAttempt.current=null;setResult(null);setPlan(null);setBusy(null);setError('');return()=>request.current?.abort();},[project.id]);
 function change<K extends keyof BreakdownInput>(key:K,value:BreakdownInput[K]){setForm(current=>({...current,[key]:value}));previewReference.current=null;saveAttempt.current=null;setResult(null);setPlan(null);setError('');}
 function cancel(){request.current?.abort();request.current=null;inFlight.current=false;setBusy(null);setError('');}
 const current=(pid:string,controller:AbortController)=>mounted.current&&currentProject.current===pid&&!controller.signal.aborted;
 async function submit(event:FormEvent){
  event.preventDefault();if(!editable||inFlight.current)return;
  const controller=new AbortController(),pid=project.id;request.current=controller;inFlight.current=true;setBusy('breakdown');setError('');setResult(null);setPlan(null);previewReference.current=null;saveAttempt.current=null;
  try{const value=await requestVideoBreakdown(pid,form,controller.signal);if(current(pid,controller)){previewReference.current={...form};setResult(value);setPlan(value.plan_seed);}}
  catch(cause){if(current(pid,controller))setError((cause as Error).message);}
  finally{if(request.current===controller){request.current=null;inFlight.current=false;if(current(pid,controller))setBusy(null);}}
 }
 async function save(event:FormEvent){
  event.preventDefault();if(!editable||!plan||!result||!previewReference.current||inFlight.current)return;
  const controller=new AbortController(),pid=project.id;request.current=controller;inFlight.current=true;setBusy('save');setError('');
  try{
   const reference=normalizeReference(previewReference.current),reviewedPlan=normalizePlan(plan);
   const body=JSON.stringify({reference,plan:reviewedPlan,opportunity_id:source.opportunity_id||null});
   if(saveAttempt.current?.body!==body)saveAttempt.current={body,key:crypto.randomUUID()};
   const saved=await requestBreakdownPlan(pid,reference,reviewedPlan,saveAttempt.current.key,source.opportunity_id,controller.signal);
   if(saved?.id&&current(pid,controller)){notify(form.target_seconds>60?'长片章纲与来源记录已保存为内容计划草稿':'拆解脚本与来源记录已保存为内容计划草稿');onSaved();onClose();}}
  catch(cause){if(current(pid,controller))setError((cause as Error).message);}
  finally{if(request.current===controller){request.current=null;inFlight.current=false;if(current(pid,controller))setBusy(null);}}
 }
 return <Modal title="视频拆解与脚本改编" onClose={onClose} wide><div className="video-breakdown-dialog">
  <p className="video-breakdown-intro">打开原作品，记录开场、口播、镜头与节奏，再结合本项目资料改编成可拍的房地产视频脚本。</p>
  <p className="video-breakdown-evidence">{source.analysis_job_id?analyzedEvidenceNote:evidenceNote}</p>
  {!editable&&<p className="form-note">当前角色可查看；拆解和保存需要项目管理员或内容负责人。</p>}
  <form onSubmit={submit}>
   <Field label="原片标题"><input required maxLength={300} value={form.title} disabled={!editable||!!busy} onChange={event=>change('title',event.target.value)}/></Field>
   <Field label="原片链接" hint={source.opportunity_id?'保留已保存发现的原始来源；如需拆解其他作品，请从搜索结果打开。':'支持抖音、小红书、B站作品链接；仅保留来源，不自动下载原片。'}><input required type="url" readOnly={Boolean(source.opportunity_id||source.reference_id)} value={form.source_url} disabled={!editable||!!busy} onChange={event=>change('source_url',event.target.value)}/></Field>
   <Field label="原片口播或字幕" hint="按片段填写，例如：00:00-00:03 买房先看这条动线。没有时间码的片段会标为时间待核对。"><textarea rows={5} maxLength={10000} value={form.transcript} disabled={!editable||!!busy} onChange={event=>change('transcript',event.target.value)} placeholder="00:00-00:03 开场口播\n00:03-00:10 核心观点与转折"/></Field>
   <Field label="画面与节奏笔记" hint="填写实际看到的镜头、字幕和切换；与口播至少填写一项。"><textarea rows={4} maxLength={10000} value={form.observations} disabled={!editable||!!busy} onChange={event=>change('observations',event.target.value)} placeholder="00:00-00:03 玄关广角镜头，字幕提出问题\n00:03-00:10 沿真实户型拍摄动线"/></Field>
   <div className="form-grid"><Field label="目标时长（秒）" hint="默认 5 分钟，支持 15–600 秒；按章节展开有依据的解释。"><input type="number" required min={15} max={600} step={1} value={form.target_seconds} disabled={!editable||!!busy} onChange={event=>change('target_seconds',Number(event.target.value))}/></Field><Field label="项目选题方向"><select value={form.topic_id} disabled={!editable||!!busy} onChange={event=>change('topic_id',event.target.value as TopicId)}>{Object.entries(topics).map(([id,label])=><option key={id} value={id}>{label}</option>)}</select></Field></div>
   <div className="form-actions">{busy==='breakdown'&&<Button onClick={cancel}>停止接收结果</Button>}<Button primary type="submit" busy={busy==='breakdown'} disabled={!editable||!!busy||(!form.transcript.trim()&&!form.observations.trim())}>{form.target_seconds>60?'生成拆解与长片章纲':'生成拆解与脚本'}</Button></div>
  </form>
  {result&&plan&&<><VideoBreakdownPreview result={result}/><form className="video-breakdown-plan" onSubmit={save}><h3>{result.adaptation.needs_chapter_expansion?'采用章纲，保存到内容计划':'核对后保存到内容计划'}</h3><Field label="改编标题"><input required maxLength={300} disabled={!editable||!!busy} value={plan.title} onChange={event=>setPlan({...plan,title:event.target.value})}/></Field><Field label="策划角度"><textarea rows={2} maxLength={6000} disabled={!editable||!!busy} value={plan.angle} onChange={event=>setPlan({...plan,angle:event.target.value})}/></Field><Field label={result.adaptation.needs_chapter_expansion?'创作简报与章节安排':'创作简报与分镜'} hint={result.adaptation.needs_chapter_expansion?'核对每章要回答的问题与参考结构；采用后再逐章生成完整口播与分镜。':'可调整口播与镜头；保留未核验事项，避免把原片描述当作本项目事实。'}><textarea rows={10} maxLength={12000} disabled={!editable||!!busy} value={plan.brief} onChange={event=>setPlan({...plan,brief:event.target.value})}/></Field><Field label="发布渠道"><select disabled={!editable||!!busy} value={plan.channel} onChange={event=>setPlan({...plan,channel:event.target.value})}>{channels.map(channel=><option key={channel}>{channel}</option>)}</select></Field><p className="form-note">原片笔记、完整拆解与来源记录会随草稿保存；正式生成前仍会校验项目资料。</p><div className="form-actions"><Button onClick={onClose}>关闭</Button><Button primary type="submit" busy={busy==='save'} disabled={!editable||!!busy||!plan.title.trim()}>保存到内容计划</Button></div></form></>}
  {error&&<ErrorNotice message={error}/>}
 </div></Modal>;
}
