import {useEffect,useRef,useState,type FormEvent} from 'react';
import {api,safeUrl} from './api.js';
import {supportedPlanningDuration} from './planning-model';
import {useApp,useJob,useResource} from './hooks';
import {Badge,Button,Empty,ErrorNotice,Field,Icon,Loading,Modal} from './components';
import {discoverySearchState,type DiscoveryItem,type DiscoveryResult} from './DiscoverySearch';
import {VideoBreakdownDialog,type BreakdownSource,type BreakdownInput} from './VideoBreakdownDialog';
import {contentDirections,createContentPlanDraft,type ContentTypeId,type ContentPlanDraft} from './ContentDirectionGuide';
import {ContentCatalog,type CatalogItem,type ResearchCatalog} from './ContentCatalog';
export {ContentCatalog} from './ContentCatalog';
export {createContentPlanDraft} from './ContentDirectionGuide';
import type {Asset,Job} from './types';
import './research-workspace.css';

type TopicId=BreakdownInput['topic_id'];
type AnalysisAttempt={revision:number;key:string};
export function nextAnalysisAttempt(current:AnalysisAttempt|null,revision:number):AnalysisAttempt{return current?.revision===revision?current:{revision,key:crypto.randomUUID()};}
export function settleAnalysisAttempt(current:AnalysisAttempt|null,status:string):AnalysisAttempt|null{return ['succeeded','failed','cancelled'].includes(status)?null:current;}
type ResearchNotes=Partial<Pick<BreakdownInput,'transcript'|'observations'|'target_seconds'|'topic_id'>>;
export interface ReferenceAnalysis {
 job_id?:string;asset_id?:string;asset_sha256?:string;duration_seconds?:number;
 transcript_segments?:{id?:string;start_seconds:number;end_seconds:number;text:string}[];
 frames?:{id?:string;time_seconds:number;observation:string;frame_url?:string}[];
 techniques?:{id:string;label:string;reason:string;evidence_refs:string[]}[];
 summary?:string;coverage?:{note?:string;sampled_frame_count?:number};
}
export interface ResearchReference {
 id:string;title:string;platform:string;source_url:string;summary?:string;observed_at:string;status:string;
 source_snapshot?:Partial<DiscoveryItem>&{content_type?:ContentTypeId;subject?:string;subjects?:string[]};curation_state:'inbox'|'shortlisted'|'archived';tags:string[];note:string;
 media_asset_id?:string|null;research_notes:ResearchNotes;analysis_snapshot:ReferenceAnalysis;revision:number;
 plan_id?:string|null;content_id?:string|null;next_action?:string;
}
interface ReferenceOutcomeData {
 reference_id:string;plans:{id:string;title:string;content_id:string|null;stage:string;template_id:string|null}[];
 publications:{id:string;title:string;url:string;channel:string;platform_state:string;receipt_status:string;plan_id?:string;content_version_id?:string;content_version?:number;template_id?:string|null;metrics:{D7:Record<string,number|null>|null;D30:Record<string,number|null>|null}}[];
 verified_live_count:number;coverage_note:string;
}
export function ReferenceOutcomes({data,loading=false,error='',retry,onAnalyze}:{data:ReferenceOutcomeData|null;loading?:boolean;error?:string;retry?:()=>void;onAnalyze:()=>void}) {
 const [window,setWindow]=useState<'D7'|'D30'>('D7');
 const stageNames:Record<string,string>={planned:'计划已保存',script_ready:'脚本已生成',publication_recorded:'发布记录待核实',published:'有核实上线回执'};
 const templateNames:Record<string,string>={question:'问题引入',contrast:'反差表达',numeric_anchor:'数字锚点',walkthrough:'实拍动线',evidence_sequence:'证据递进'};
 const observed=(value:unknown)=>typeof value==='number'&&Number.isFinite(value)?new Intl.NumberFormat('zh-CN').format(value):'待观察';
 return <details className="research-outcomes"><summary>改编效果与复盘{data&&<span>{data.plans.length} 个计划 · {data.publications.length} 条发布记录</span>}</summary>
  {loading?<Loading text="正在读取关联发布与反馈…"/>:error?<ErrorNotice message={error} retry={retry}/>:data?<>
   {!!data.plans.length&&<div className="research-outcome-plans">{data.plans.map(p=><div key={p.id}><strong>{p.title}</strong><Badge tone={p.stage==='published'?'green':'neutral'}>{stageNames[p.stage]||'计划状态待核对'}</Badge>{p.template_id&&templateNames[p.template_id]&&<small>借鉴结构：{templateNames[p.template_id]}</small>}</div>)}</div>}
   {!data.publications.length?<Empty icon="chart" title="暂无关联发布记录">改编内容发布并回填回执与观察数据后，在这里对照研究结论。</Empty>:<>
    <div className="research-outcome-toolbar"><span>已核实且在线：{data.verified_live_count} 条</span><Field label="复盘观察窗口"><select value={window} onChange={e=>setWindow(e.target.value as 'D7'|'D30')}><option value="D7">D7 · 发布后 7 天</option><option value="D30">D30 · 发布后 30 天</option></select></Field></div>
    <div className="research-outcome-publications">{data.publications.map(p=>{const metrics=p.metrics?.[window],url=safeUrl(p.url),verified=p.receipt_status==='verified',live=p.platform_state==='live';return <article key={p.id}><div className="research-outcome-publication-heading"><h4>{p.title}</h4><span>{p.channel}{p.content_version?` · 脚本 v${p.content_version}`:''}</span><Badge tone={verified?'green':'amber'}>{verified?'回执已核实':'回执待核实'}</Badge><Badge tone={live?'green':'neutral'}>{live?'在线':['removed','offline','deleted'].includes(p.platform_state)?'已下线':'上线状态待核对'}</Badge></div><div className="research-outcome-metrics">{Object.entries({views:'播放',likes:'点赞',inquiries:'咨询',qualified:'资格客户'}).map(([key,label])=><div key={key}><span>{label}</span><strong>{observed(metrics?.[key])}</strong></div>)}</div>{!metrics&&<p className="research-outcome-pending">{window} 观察数据尚未回填</p>}{url?<a href={url} target="_blank" rel="noreferrer">查看发布作品 <Icon name="external" size={12}/></a>:<small>发布链接不可用</small>}</article>;})}</div>
   </>}
   <p className="research-outcome-note">{data.coverage_note}</p>
  </>:null}
  <div className="research-outcome-footer"><span>结合实际发布与观察结果，人工完善研究结论。</span><Button icon="chart" onClick={onAnalyze}>前往效果分析</Button></div>
 </details>;
}
const platformNames:Record<string,string>={douyin:'抖音',xiaohongshu:'小红书',bilibili:'B站'};
const topics:Record<string,string>={tour:'实拍看房',layout:'户型动线',light:'采光景观',space:'空间尺度',kitchen:'厨房细节',pitfalls:'买房避坑',comparison:'住宅对比'};
const states:Record<string,string>={inbox:'待筛选',shortlisted:'重点参考',archived:'已归档'};
const root=(pid:string)=>`/api/projects/${encodeURIComponent(pid)}/operations/research`;
const prettyNumber=(v:unknown)=>typeof v==='number'&&Number.isFinite(v)?new Intl.NumberFormat('zh-CN').format(v):'未获取';
const dateTime=(v:string)=>v?new Intl.DateTimeFormat('zh-CN',{month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit',timeZone:'Asia/Shanghai'}).format(new Date(v)):'时间未获取';
export function referenceStage(r:ResearchReference):string {if(r.plan_id||r.content_id)return '已改编';if(r.analysis_snapshot?.job_id)return '已分析';if(r.research_notes?.transcript?.trim()||r.research_notes?.observations?.trim())return '笔记已保存';return r.media_asset_id?'待分析':'待补原片';}
export function ResearchSearchHistory({jobs,onOpen}:{jobs:Job[];onOpen:(jobId:string,data:DiscoveryResult)=>void}) {
 return <div className="research-history">{jobs.map(j=>{const data=j.result?.discovery_search as DiscoveryResult|undefined,state=discoverySearchState(data),succeeded=j.status==='succeeded';return <article className="card" key={j.id}><div><Badge tone={succeeded&&!state.incomplete?'green':'amber'}>{succeeded?state.label:({failed:'失败',cancelled:'已取消',queued:'排队中',running:'搜索中'} as Record<string,string>)[j.status]||j.status}</Badge><strong>{data?.query||'房地产内容搜索'}</strong><small>{dateTime(j.created_at)}</small><p>{data?`${data.items?.length||0} 条候选 · ${data.window_days===null?'不限时间':`近 ${data.window_days} 天`}`:j.error||'查询作品来源中'}</p>{succeeded&&state.incomplete&&<div className="research-history-issues">{state.failedQueries.map((run,i)=><p key={`query-${i}`}>「{run.query}」：{run.error||'查询失败'}</p>)}{state.sourceIssues.map((source,i)=><p key={`source-${i}`}>{platformNames[source.platform]||source.platform}：{source.reason||'来源尚未完成核对'}</p>)}{!state.failedQueries.length&&!state.sourceIssues.length&&<p>未返回可核对的来源状态，请查看这次结果后重新搜索。</p>}</div>}</div>{data?.items&&<Button onClick={()=>onOpen(j.id,data)}>查看这次结果</Button>}</article>;})}</div>;
}
export function requestReferenceCapture(pid:string,source:{job_id:string;platform:string;content_id:string}|{source_url:string;title:string},signal?:AbortSignal):Promise<ResearchReference>{return api(`${root(pid)}/references`,{method:'POST',body:source,signal});}
export function requestCatalogReferenceCapture(pid:string,item:CatalogItem,signal?:AbortSignal):Promise<ResearchReference>{
 if(item.job_id)return requestReferenceCapture(pid,{job_id:item.job_id,platform:item.platform,content_id:item.content_id},signal);
 if(item.catalog_only&&safeUrl(item.url))return requestReferenceCapture(pid,{source_url:item.url,title:item.title},signal);
 return Promise.reject(new Error('作品来源尚未完整，暂时无法收藏'));
}
type PlanningReference=Pick<ResearchReference,'id'|'title'>&Partial<Pick<ResearchReference,'source_snapshot'|'summary'|'note'|'research_notes'|'analysis_snapshot'>>;
export function createReferencePlanDraft(reference:PlanningReference,type?:ContentTypeId,subject?:string):ContentPlanDraft&{target_seconds:number}{
 const selectedSubject=subject?.trim()||reference.source_snapshot?.subject?.trim();
 const direction=contentDirections.find(d=>d.id===(type||reference.source_snapshot?.content_type))||(!type&&selectedSubject?contentDirections.find(d=>d.subjects.some(s=>s.query===selectedSubject||s.label===selectedSubject)):undefined);
 const subjectQuery=direction?.subjects.find(s=>s.query===selectedSubject||s.label===selectedSubject)?.query||selectedSubject;
 const categoryDraft=createContentPlanDraft(direction?.id,subjectQuery||'');
 const observation=reference.note||reference.summary||reference.source_snapshot?.snippet;
 const savedDuration=reference.research_notes?.target_seconds,target_seconds=typeof savedDuration==='number'&&supportedPlanningDuration(savedDuration)?savedDuration:30;
 const evidenceLabel=reference.analysis_snapshot?.job_id?'参考笔记':'结构假设（待验证）';
 return {title:reference.title,angle:categoryDraft?.angle||'结合当前项目与目标客群，确定这条参考值得借鉴的表达角度。',brief:[categoryDraft?.brief,observation&&`${evidenceLabel}：${observation}`].filter(Boolean).join('\n'),topic_id:reference.research_notes?.topic_id||categoryDraft?.topic_id||'tour',reference_id:reference.id,content_type:direction?.id,subject:subjectQuery,target_seconds,start_mode:'reference'};
}
export async function requestCatalogPlanningDraft(pid:string,item:CatalogItem,type:ContentTypeId,subject:string,signal?:AbortSignal):Promise<ContentPlanDraft>{
 const reference=item.reference_id&&!item.catalog_only?{id:item.reference_id,title:item.title,source_snapshot:item}:await requestCatalogReferenceCapture(pid,item,signal);
 return createReferencePlanDraft({...reference,title:reference.title||item.title,source_snapshot:{...item,...reference.source_snapshot}},type,subject||item.subject);
}
export function requestReferenceUpdate(pid:string,rid:string,revision:number,changes:Record<string,unknown>,signal?:AbortSignal):Promise<ResearchReference>{return api(`${root(pid)}/references/${encodeURIComponent(rid)}`,{method:'PATCH',body:{expected_revision:revision,...changes},signal});}
function timestamp(seconds:number){const value=Math.max(0,seconds),mins=Math.floor(value/60),secs=Number((value%60).toFixed(3));return `${String(mins).padStart(2,'0')}:${secs<10?'0':''}${secs}`;}
export function analysisToNotes(a:ReferenceAnalysis):{transcript:string;observations:string}{
 const transcript=(a.transcript_segments||[]).map(v=>`${timestamp(v.start_seconds)}–${timestamp(v.end_seconds)} ${v.text}`).join('\n');
 const observations=(a.frames||[]).map(v=>`${timestamp(v.time_seconds)} ${v.observation}`).join('\n');
 if(transcript.length>10000||observations.length>10000)throw new Error('分析内容超过笔记上限，请按片段手动复制，已有笔记未改变。');
 return {transcript,observations};
}
function sourceLink(url:string){const href=safeUrl(url);return href?<a href={href} target="_blank" rel="noreferrer">查看原作品 <Icon name="external" size={12}/></a>:<span>来源链接不可用</span>;}

export function ReferenceLibrary({references,editable,busy=false,onOpen,onPlan,onOpenPlan,onOpenContent}:{references:ResearchReference[];editable:boolean;busy?:boolean;onOpen:(r:ResearchReference)=>void;onPlan?:(r:ResearchReference)=>void;onOpenPlan?:(id:string)=>void;onOpenContent?:(id:string)=>void}) {
 const [stage,setStage]=useState('all');
 const rows=references.filter(r=>stage==='all'||(stage==='pending'?!['已分析','已改编'].includes(referenceStage(r)):referenceStage(r)===stage));
 return <section aria-label="房地产参考库" className="research-library">
  <div className="research-library-toolbar"><div className="research-library-stages" aria-label="参考研究进度">{([['all','全部'],['pending','待研究'],['已分析','已分析'],['已改编','已改编']] as const).map(([id,label])=><button key={id} type="button" aria-pressed={stage===id} className={stage===id?'active':''} onClick={()=>setStage(id)}>{label}</button>)}</div><span>{rows.length} 条参考</span></div>
  {!rows.length?<Empty icon="studio" title={references.length?'这个进度暂无参考':'还没有收藏的参考'}>在内容灵感中收藏作品，就能继续研究与拆解。</Empty>:<div className="research-reference-grid">{rows.map(r=><article className="card research-reference-card" key={r.id}><div className="research-card-top"><span>{r.platform}</span><Badge tone={r.curation_state==='shortlisted'?'blue':'neutral'}>{states[r.curation_state]||'待筛选'}</Badge><Badge tone={r.plan_id?'green':'neutral'}>{referenceStage(r)}</Badge></div><h3>{r.title}</h3><p className="research-card-note">{r.note||r.summary||'记录值得借鉴的开场、动线或表达方式。'}</p><div className="research-reference-metrics"><span>点赞 {prettyNumber(r.source_snapshot?.metrics?.likes)}</span><span>播放 {prettyNumber(r.source_snapshot?.metrics?.views)}</span></div>{!!r.tags?.length&&<div className="research-tags">{r.tags.map(t=><span key={t}>{t}</span>)}</div>}<div className="research-reference-source">{sourceLink(r.source_url)}<small>收录于 {dateTime(r.observed_at)}</small></div><div className="research-card-actions"><Button icon="studio" onClick={()=>onOpen(r)} disabled={busy}>{editable?'研究与拆解':'查看研究'}</Button>{onPlan&&<Button primary icon="arrow" disabled={!editable||busy} onClick={()=>onPlan(r)}>用这条策划</Button>}{r.plan_id&&onOpenPlan&&<Button onClick={()=>onOpenPlan(r.plan_id!)}>查看内容计划</Button>}{r.content_id&&onOpenContent&&<Button primary onClick={()=>onOpenContent(r.content_id!)}>继续编辑脚本</Button>}</div></article>)}</div>}
 </section>;
}

export function ResearchWorkspace({onPlan,onOpenPlan,onOpenContent}:{onPlan:(draft?:ContentPlanDraft)=>void;onOpenPlan?:(id:string)=>void;onOpenContent?:(id:string)=>void}) {
 const {project,session,notify}=useApp(),editable=['owner','admin','content'].includes(session.role),base=root(project.id);
 const catalog=useResource<ResearchCatalog>(`${base}/catalog`),references=useResource<ResearchReference[]>(`${base}/references`);
 const [tab,setTab]=useState<'catalog'|'library'>('catalog'),[type,setType]=useState<ContentTypeId>('professional'),[subject,setSubject]=useState('');
 const [error,setError]=useState(''),[busyItemKey,setBusyItemKey]=useState(''),[opened,setOpened]=useState<ResearchReference|null>(null),[breakdown,setBreakdown]=useState<BreakdownSource|null>(null);
 const alive=useRef(true),captureGuard=useRef(false),capturedReferences=useRef(new Map<string,string>());
 useEffect(()=>{alive.current=true;return()=>{alive.current=false;};},[]);
 useEffect(()=>{capturedReferences.current.clear();},[project.id]);
 useEffect(()=>{
  if(!catalog.data?.categories?.some(category=>category.collection_state==='collecting'))return;
  const timer=setTimeout(catalog.retry,8000);return()=>clearTimeout(timer);
 },[catalog.data]);
 const displayedCatalog=catalog.data?.categories?{...catalog.data,categories:catalog.data.categories.map(category=>({...category,items:category.items.map(item=>{
  const saved=references.data?.find(reference=>(reference.source_snapshot?.platform===item.platform&&reference.source_snapshot?.content_id===item.content_id)||(Boolean(item.url)&&reference.source_url===item.url));
  const savedId=saved?.id||capturedReferences.current.get(`${item.platform}:${item.content_id}`);
  return savedId?{...item,reference_id:savedId,catalog_only:false}:item;
 })}))}:null;
 async function capture(item:CatalogItem){
  if(!editable||captureGuard.current)return;captureGuard.current=true;setBusyItemKey(`${item.platform}:${item.content_id}`);setError('');
  try{const reference=await requestCatalogReferenceCapture(project.id,item);if(alive.current){capturedReferences.current.set(`${item.platform}:${item.content_id}`,reference.id);references.retry();catalog.retry();notify('已收藏到我的参考');setOpened(reference);}}
  catch(cause){if(alive.current)setError((cause as Error).message);}
  finally{captureGuard.current=false;if(alive.current)setBusyItemKey('');}
 }
 async function planItem(item:CatalogItem){
  if(!editable||captureGuard.current)return;captureGuard.current=true;const key=`${item.platform}:${item.content_id}`;setBusyItemKey(key);setError('');
  try{
   const existing=references.data?.find(reference=>reference.id===item.reference_id),savedId=existing?.id||capturedReferences.current.get(key);
   const draft=existing?createReferencePlanDraft({...existing,source_snapshot:{...item,...existing.source_snapshot}},type,subject||item.subject):await requestCatalogPlanningDraft(project.id,savedId?{...item,reference_id:savedId,catalog_only:false}:item,type,subject);
   if(alive.current){if(draft.reference_id)capturedReferences.current.set(key,draft.reference_id);references.retry();catalog.retry();onPlan(draft);}
  }catch(cause){if(alive.current)setError((cause as Error).message);}
  finally{captureGuard.current=false;if(alive.current)setBusyItemKey('');}
 }
 async function openReference(id:string){
  setError('');const existing=references.data?.find(reference=>reference.id===id);if(existing){setOpened(existing);return;}
  try{const reference=await api(`${base}/references/${encodeURIComponent(id)}`) as ResearchReference;if(alive.current)setOpened(reference);}
  catch(cause){if(alive.current)setError((cause as Error).message);}
 }
 function adapt(reference:ResearchReference){setOpened(null);setBreakdown({source_url:reference.source_url,title:reference.title,reference_id:reference.id,analysis_job_id:reference.analysis_snapshot?.job_id,initial_notes:reference.research_notes});}
 function planReference(reference:ResearchReference){if(!editable||captureGuard.current)return;setOpened(null);onPlan(createReferencePlanDraft(reference));}
 return <section className="research-workspace">
  <div className="research-workspace-heading"><div><h2>内容灵感</h2><p>按内容目的浏览已整理的作品，找到适合你的选题。</p></div></div>
  <div className="research-tabs" role="tablist" aria-label="内容浏览">{([['catalog','内容灵感'],['library','我的参考']] as const).map(([id,label])=><button key={id} id={`research-tab-${id}`} type="button" role="tab" aria-controls={`research-panel-${id}`} aria-selected={tab===id} className={tab===id?'active':''} onClick={()=>setTab(id)}>{label}{id==='library'&&<span>{references.data?.length??0}</span>}</button>)}</div>
  {error&&<ErrorNotice message={error}/>}
  {tab==='catalog'&&<div role="tabpanel" id="research-panel-catalog" aria-labelledby="research-tab-catalog"><ContentCatalog catalog={displayedCatalog} type={type} subject={subject} loading={catalog.loading} error={catalog.error} retry={catalog.retry} editable={editable} busyItemKey={busyItemKey} onSelectType={selected=>{setType(selected);setSubject('');}} onSelectSubject={setSubject} onCapture={item=>void capture(item)} onOpenReference={id=>void openReference(id)} onPlan={onPlan} onPlanItem={item=>void planItem(item)}/></div>}
  {tab==='library'&&<div role="tabpanel" id="research-panel-library" aria-labelledby="research-tab-library">{references.loading?<Loading text="正在读取我的参考…"/>:references.error?<ErrorNotice message={references.error} retry={references.retry}/>:<ReferenceLibrary references={references.data||[]} editable={editable} busy={Boolean(busyItemKey)} onOpen={setOpened} onPlan={planReference} onOpenPlan={onOpenPlan} onOpenContent={onOpenContent}/>}</div>}
  {opened&&<ReferenceResearchDialog key={`${project.id}:${opened.id}`} reference={opened} editable={editable} onClose={()=>setOpened(null)} onChanged={references.retry} onAdapt={adapt} onPlan={planReference} onOpenPlan={onOpenPlan} onOpenContent={onOpenContent}/>}
  {breakdown&&<VideoBreakdownDialog key={`${project.id}:${breakdown.reference_id}`} source={breakdown} onClose={()=>setBreakdown(null)} onSaved={()=>{references.retry();onPlan();}}/>}
 </section>;
}

export function ReferenceResearchDialog({reference,editable,onClose,onChanged,onAdapt,onPlan,onOpenPlan,onOpenContent}:{reference:ResearchReference;editable:boolean;onClose:()=>void;onChanged:()=>void;onAdapt:(r:ResearchReference)=>void;onPlan?:(r:ResearchReference)=>void;onOpenPlan?:(id:string)=>void;onOpenContent?:(id:string)=>void}) {
 const {project,notify,navigate}=useApp(),base=root(project.id),[record,setRecord]=useState(reference),[notes,setNotes]=useState<ResearchNotes>({transcript:'',observations:'',target_seconds:30,topic_id:'tour',...reference.research_notes});
 const [note,setNote]=useState(reference.note||''),[tags,setTags]=useState(reference.tags?.join('、')||''),[state,setState]=useState(reference.curation_state||'inbox'),[selectedAsset,setSelectedAsset]=useState(reference.media_asset_id||'');
 const [error,setError]=useState(''),[busy,setBusy]=useState(false),[file,setFile]=useState<File|null>(null),[evidence,setEvidence]=useState(''),[authorized,setAuthorized]=useState(false),[replace,setReplace]=useState(false);
 const outcomes=useResource<ReferenceOutcomeData>(`${base}/references/${reference.id}/outcomes`);
 const assets=useResource<Asset[]>(`/api/projects/${project.id}/assets`),jobs=useResource<Job[]>(`/api/projects/${project.id}/jobs`),capabilities=useResource<{video_analysis:{ready:boolean;local_asr_ready:boolean;vision_ready:boolean;ffmpeg_ready:boolean};evidence_note:string}>(`${base}/capabilities`),task=useJob();
 const alive=useRef(true),inFlight=useRef(false),watching=useRef(''),analysisAttempt=useRef<AnalysisAttempt|null>(null);
 useEffect(()=>{alive.current=true;return()=>{alive.current=false;};},[]);
 const analysis=record.analysis_snapshot||{},videoAssets=(assets.data||[]).filter(a=>a.mime_type.startsWith('video/')),asset=videoAssets.find(a=>a.id===record.media_asset_id);
 const active=jobs.data?.find(j=>j.kind==='reference_analyze'&&['queued','running'].includes(j.status)&&((j as Job&{reference_id?:string}).reference_id===record.id||j.result?.reference_id===record.id||Boolean(record.analysis_snapshot?.job_id&&(j.result?.reference_analysis as ReferenceAnalysis)?.job_id===record.analysis_snapshot.job_id)));
 async function refreshRecord(){const latest=await api(`${base}/references/${record.id}`) as ResearchReference;if(alive.current){setRecord(latest);setSelectedAsset(latest.media_asset_id||'');onChanged();}return latest;}
 async function watch(id:string){watching.current=id;const finished=await task.wait(id,j=>{analysisAttempt.current=settleAnalysisAttempt(analysisAttempt.current,j.status);});if(alive.current){if(finished)await refreshRecord();jobs.retry();}}
 useEffect(()=>{if(active&&watching.current!==active.id&&!task.busy)void watch(active.id);},[active?.id]);
 async function mutate(changes:Record<string,unknown>){if(inFlight.current||!editable)return;inFlight.current=true;setBusy(true);setError('');try{const r=await requestReferenceUpdate(project.id,record.id,record.revision,changes);if(alive.current){setRecord(r);analysisAttempt.current=null;onChanged();notify('参考研究已保存');}return r;}catch(e){if(alive.current)setError(`${(e as Error).message}。如其他人已修改，请重新打开此参考后重试；当前笔记仍保留在窗口中。`);}finally{inFlight.current=false;if(alive.current)setBusy(false);}}
 async function saveNotes(andAdapt=false,andPlan=false){if(notes.target_seconds&&!supportedPlanningDuration(notes.target_seconds)){setError('请选择 15–60 秒短视频或 300–600 秒长视频。');return;}const saved=await mutate({research_notes:notes,note:note.trim(),tags:Array.from(new Set(tags.split(/[、,，\n]/).map(t=>t.trim()).filter(Boolean))),curation_state:state});if(saved&&alive.current){if(andAdapt)onAdapt(saved);else if(andPlan)onPlan?.(saved);}}
 async function upload(e:FormEvent){e.preventDefault();if(!editable||!file||!authorized||inFlight.current)return;inFlight.current=true;setBusy(true);setError('');try{if(file.size>100*1024*1024)throw new Error('原片最大 100 MB，请先裁剪或压缩');const form=new FormData();form.set('file',file);form.set('expected_revision',String(record.revision));form.set('evidence',evidence.trim());form.set('model_processing_authorized',String(authorized));const uploaded=await api(`${base}/references/${record.id}/media`,{method:'POST',body:form}) as {reference:ResearchReference;asset:Asset};if(alive.current){setRecord(uploaded.reference);setSelectedAsset(uploaded.asset.id);setFile(null);assets.retry();onChanged();notify('参考原片已保存，可以开始分析');}}catch(e){if(alive.current)setError((e as Error).message);}finally{inFlight.current=false;if(alive.current)setBusy(false);}}
 async function analyze(){if(!editable||inFlight.current||task.busy)return;inFlight.current=true;setBusy(true);setError('');try{analysisAttempt.current=nextAnalysisAttempt(analysisAttempt.current,record.revision);const started=await api(`${base}/references/${record.id}/analyze`,{method:'POST',body:{expected_revision:record.revision,idempotency_key:analysisAttempt.current.key,force:Boolean(analysis.job_id)}}) as {job_id:string};setBusy(false);await watch(started.job_id);}catch(e){if(alive.current)setError((e as Error).message);}finally{inFlight.current=false;if(alive.current)setBusy(false);}}
 async function cancelAnalysis(){if(!task.job)return;try{await api(`/api/jobs/${task.job.id}/cancel`,{method:'POST'});jobs.retry();}catch(e){setError((e as Error).message);}}
 function importAnalysis(){try{const incoming=analysisToNotes(analysis);setNotes(current=>({...current,...incoming}));setReplace(false);notify('已导入可编辑笔记，点击保存后持久化');}catch(e){setError((e as Error).message);}}
 const noteDirty=Boolean(notes.transcript?.trim()||notes.observations?.trim()),assetAI=asset?.model_processing_authorized===true||asset?.metadata_json?.model_processing_authorized===true;
 return <Modal title="参考研究与原片拆解" onClose={onClose} wide><div className="research-detail">
  <div className="research-detail-source"><div><Badge>{referenceStage(record)}</Badge><h3>{record.title}</h3>{sourceLink(record.source_url)}</div><small>修订 {record.revision} · {dateTime(record.observed_at)}</small>{onPlan&&<Button primary icon="arrow" disabled={!editable||busy||task.busy} onClick={()=>void saveNotes(false,true)}>用这条策划</Button>}</div>
  {(record.plan_id||record.content_id)&&<div className="research-detail-next"><span>这条参考已有改编成果</span>{record.plan_id&&onOpenPlan&&<Button onClick={()=>onOpenPlan(record.plan_id!)}>查看内容计划</Button>}{record.content_id&&onOpenContent&&<Button primary onClick={()=>onOpenContent(record.content_id!)}>继续编辑脚本</Button>}</div>}
  <div className="form-grid"><Field label="收录状态"><select value={state} disabled={!editable||busy} onChange={e=>setState(e.target.value as ResearchReference['curation_state'])}>{Object.entries(states).map(([id,label])=><option key={id} value={id}>{label}</option>)}</select></Field><Field label="标签"><input value={tags} disabled={!editable||busy} onChange={e=>setTags(e.target.value)} placeholder="用顿号分隔，例如：大平层、动线"/></Field></div><Field label="研究重点与复盘结论"><textarea value={note} maxLength={6000} rows={2} disabled={!editable||busy} onChange={e=>setNote(e.target.value)} placeholder="哪些结构值得借鉴？哪些不适合本项目？"/></Field>
  <ReferenceOutcomes data={outcomes.data} loading={outcomes.loading} error={outcomes.error} retry={outcomes.retry} onAnalyze={()=>{navigate('reports');onClose();}}/>
  <section className="research-evidence-section"><div className="research-section-heading"><span className="research-step">1</span><div><h3>提供有权使用的原片</h3><p>链接保留来源。选择项目内已授权视频，或上传仅供研究的参考原片。</p></div></div>
   {asset&&<video className="research-video" controls preload="metadata" src={`/api/projects/${project.id}/assets/${asset.id}/file`}/>}
   <div className="research-asset-choice"><Field label="项目内视频"><select value={selectedAsset} disabled={!editable||busy||task.busy} onChange={e=>setSelectedAsset(e.target.value)}><option value="">选择已授权视频</option>{videoAssets.map(a=><option key={a.id} value={a.id} disabled={a.rights!=='authorized'||!(a.model_processing_authorized||a.metadata_json?.model_processing_authorized)}>{a.name}{a.rights!=='authorized'?' · 用途待确认':!(a.model_processing_authorized||a.metadata_json?.model_processing_authorized)?' · 未授权 AI 分析':''}</option>)}</select></Field><Button disabled={!editable||busy||task.busy||!selectedAsset||selectedAsset===record.media_asset_id} onClick={()=>void mutate({media_asset_id:selectedAsset})}>关联此视频</Button></div>
   {assets.error&&<ErrorNotice message={assets.error} retry={assets.retry}/>}
   <details className="research-upload"><summary>上传参考原片</summary><form onSubmit={upload}><Field label="原片文件（最大 100 MB、10 分钟）"><input type="file" accept="video/*" disabled={!editable||busy||task.busy} onChange={e=>setFile(e.target.files?.[0]||null)}/></Field><Field label="来源与研究用途依据"><input value={evidence} maxLength={2000} disabled={!editable||busy||task.busy} onChange={e=>setEvidence(e.target.value)} placeholder="例如：自有拍摄，仅用于本项目内容研究"/></Field><label className="checkbox-line"><input type="checkbox" checked={authorized} disabled={!editable||busy||task.busy} onChange={e=>setAuthorized(e.target.checked)}/>我有权使用此原片，并允许本项目进行本地音频转写与百炼视觉分析</label><p className="form-note">参考原片用于研究，不会自动加入项目营销素材。</p><Button type="submit" icon="upload" busy={busy} disabled={!editable||!file||!authorized||!evidence.trim()||task.busy}>上传研究原片</Button></form></details>
  </section>
  <section className="research-evidence-section"><div className="research-section-heading"><span className="research-step">2</span><div><h3>本地转写与百炼画面分析</h3><p>提取实际语音时间码，分析少量采样画面，再根据证据识别表达结构。</p></div></div>
   {capabilities.error&&<ErrorNotice message={capabilities.error} retry={capabilities.retry}/>}
   {capabilities.data&&!capabilities.data.video_analysis.ready&&<p className="research-capability-note">分析暂不可用：{!capabilities.data.video_analysis.ffmpeg_ready?'缺少视频处理运行时；':''}{!capabilities.data.video_analysis.vision_ready?'视觉模型尚未就绪；':''}{!capabilities.data.video_analysis.local_asr_ready?'本地语音转写尚未就绪；':''}可先手动记录下方笔记。</p>}
   {asset&&!assetAI&&<p className="form-note">此视频尚未确认 AI 处理用途，请在素材库完成授权后再分析。</p>}
   <Button primary icon="sparkle" onClick={()=>void analyze()} busy={busy} disabled={!editable||busy||task.busy||!record.media_asset_id||!assetAI||!capabilities.data?.video_analysis.ready}>{analysis.job_id?'重新分析原片':'开始分析原片'}</Button>
   {task.busy&&<div className="research-job" role="status"><span className="spinner"/><div><strong>{task.statusText||'正在处理原片…'}</strong><progress max={100} value={task.percent}/></div><Button onClick={()=>void cancelAnalysis()}>取消分析</Button></div>}{task.error&&<ErrorNotice message={task.error}/>}
   {analysis.job_id&&<div className="research-analysis"><div className="research-analysis-heading"><Badge tone="green">分析已保存</Badge><span>{prettyNumber(analysis.duration_seconds)} 秒原片 · {analysis.frames?.length??0} 张采样帧</span></div><p className="research-coverage">{analysis.coverage?.note||'结果依据语音转写与采样画面，不能代表逐帧观看完整视频。'}</p>{analysis.summary&&<p>{analysis.summary}</p>}{!!analysis.techniques?.length&&<div className="research-techniques">{analysis.techniques.map(t=><div key={t.id}><strong>{t.label}</strong><p>{t.reason}</p><small>依据 {t.evidence_refs?.join('、')||'未提供'}</small></div>)}</div>}<details><summary>查看实际语音与采样画面</summary><div className="research-transcript">{analysis.transcript_segments?.length?analysis.transcript_segments.map((t,i)=><p key={t.id||i}><span>{timestamp(t.start_seconds)}–{timestamp(t.end_seconds)}</span>{t.text}</p>):<p>未获取语音转写。画面分析独立保留；静音原片不补写口播。</p>}</div><div className="research-analysis-frames">{analysis.frames?.map((f,i)=><figure key={f.id||i}>{f.frame_url?.startsWith(`${base}/references/${record.id}/frames/`)&&<img src={f.frame_url} alt={`${timestamp(f.time_seconds)} 采样画面`} loading="lazy"/>}<figcaption><strong>{timestamp(f.time_seconds)} · {f.id}</strong><p>{f.observation}</p></figcaption></figure>)}</div></details>
    {noteDirty&&<label className="checkbox-line"><input type="checkbox" checked={replace} onChange={e=>setReplace(e.target.checked)} disabled={!editable||busy}/>已确认用本次分析替换当前编辑区笔记</label>}
    <Button icon="arrow" disabled={!editable||busy||noteDirty&&!replace} onClick={importAnalysis}>导入分析到可编辑笔记</Button>
   </div>}
  </section>
  <section className="research-evidence-section"><div className="research-section-heading"><span className="research-step">3</span><div><h3>核对笔记，改编到当前项目</h3><p>可先独立保存研究草稿。改编时再核对本项目资料，参考内容不会变为项目事实。</p></div></div><Field label="原片口播或字幕" hint="保留实际时间码；例如 00:00-00:03。没有时间码的片段会标为待核对。"><textarea rows={5} maxLength={10000} value={notes.transcript||''} disabled={!editable||busy} onChange={e=>setNotes(n=>({...n,transcript:e.target.value}))}/></Field><Field label="画面与节奏笔记"><textarea rows={5} maxLength={10000} value={notes.observations||''} disabled={!editable||busy} onChange={e=>setNotes(n=>({...n,observations:e.target.value}))}/></Field><div className="form-grid"><Field label="改编时长（秒）" hint="默认 30 秒，支持 15–60 秒短视频或 300–600 秒长视频；已保存目标会继续沿用。"><input type="number" min={15} max={600} step={1} value={notes.target_seconds??30} disabled={!editable||busy} onChange={e=>setNotes(n=>({...n,target_seconds:Number(e.target.value)}))}/></Field><Field label="项目选题方向"><select value={notes.topic_id||'tour'} disabled={!editable||busy} onChange={e=>setNotes(n=>({...n,topic_id:e.target.value as TopicId}))}>{Object.entries(topics).filter(([id])=>!['pitfalls','comparison'].includes(id)).map(([id,label])=><option key={id} value={id}>{label}</option>)}</select></Field></div></section>
  {error&&<ErrorNotice message={error}/>}
  <div className="research-detail-footer"><span>研究草稿与内容计划分别保存</span><Button onClick={onClose}>关闭</Button><Button onClick={()=>void saveNotes()} busy={busy} disabled={!editable||busy||task.busy}>保存研究草稿</Button><Button primary onClick={()=>void saveNotes(true)} disabled={!editable||busy||task.busy||!noteDirty}>{record.plan_id?'保存并再写一条改编':'保存并改编为项目计划'}</Button></div>
 </div></Modal>;
}
