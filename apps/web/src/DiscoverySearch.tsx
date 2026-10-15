import {useEffect,useRef,useState,type FormEvent} from 'react';
import {api,safeUrl} from './api.js';
import {useApp,useJob,useResource} from './hooks';
import {Badge,Button,Empty,ErrorNotice,Field,Icon} from './components';
import type {Job} from './types';
import {SearchAccountConnection,type SearchAccountScope} from './SearchAccountConnection';
import type {BreakdownSource} from './VideoBreakdownDialog';
import './discovery-search.css';

export interface DiscoveryGrowth {
 status:'observed'|'no_baseline'|'incomparable';previous_observed_at:string|null;current_observed_at:string|null;elapsed_hours:number|null;
 metric_deltas:{views:number|null;likes:number|null;comments:number|null;saves:number|null;shares:number|null};likes_per_hour:number|null;reasons:string[];
}
export interface DiscoveryItem {
 platform:string;content_id:string;title:string;url:string;author?:string|null;published_at:string|null;
 metrics:{views:number|null;likes:number|null;comments:number|null;saves:number|null;shares:number|null};
 metrics_observed_at:string|null;metrics_source:string|null;match_keywords:string[];
 qualification:'verified_high_engagement'|'candidate_unverified';historical:boolean;qualification_reason:string;
 snippet?:string;date_unknown?:boolean;growth?:DiscoveryGrowth;
}
export interface DiscoveryResult {
 query:string;searched_at:string;window_days:number|null;total:number;queries?:string[];total_query_count?:number;queries_truncated?:boolean;coverage_note?:string;
 sources:{platform:string;adapter:string;status:'ok'|'partial'|'blocked'|'rate_limited'|'login_required'|'not_connected'|'busy'|'challenge'|'not_configured';reason:string;query?:string;http_status?:401|403|429|null;restriction_reason?:'visible_challenge'|'visible_login_prompt'|'response_verification_required'|'http_unauthorized'|'http_denied'|'rate_limited'|'unexpected_public_route'|null;challenge_visible?:boolean}[];
 query_runs?:{query:string;status:'succeeded'|'failed';total:number;error:string|null}[];
 items:DiscoveryItem[];
}
export interface DiscoveryFilters {query:string;platforms:string[];days:number|null;min_likes:number;min_views:number;sort:'engagement'|'latest'|'relevance'|'growth';account_scope?:SearchAccountScope}
type OpportunitySeed={title:string;source_url:string;summary:string;platform:string};
const platformLabels:Record<string,string>={douyin:'抖音',xiaohongshu:'小红书',bilibili:'B站'};
const sourceLabels:Record<string,string>={ok:'获取成功',partial:'部分结果',blocked:'暂时受阻',rate_limited:'请求受限',login_required:'需登录',not_connected:'尚未连接',busy:'账号正在使用',challenge:'平台校验未确认',not_configured:'未配置'};
const adapterLabels:Record<string,string>={douyin_public_browser:'抖音研究账号公开搜索',douyin_operating_public_browser:'本项目运营账号公开搜索',web_index:'公开网页索引','web_index+bilibili_public_api':'公开网页索引 + 作品数据核对',bilibili_public_search:'平台公开搜索','bilibili_public_search+bilibili_public_api':'平台公开搜索 + 作品数据核对',bailian_web_index:'公开网页索引',platform_search:'平台搜索',bilibili_public_api:'平台公开数据',bilibili_search:'平台搜索',easel:'历史检索来源'};
const metricLabels={views:'播放',likes:'点赞',comments:'评论',saves:'收藏',shares:'分享'};
const keywords=['住宅','豪宅','大平层','别墅','户型','看房','采光','装修'];
const presets=['房地产','买房避坑','户型动线','实拍看房','改善置业','新房测评'];
const numberFormat=new Intl.NumberFormat('zh-CN');
function metric(value:number|null|undefined){return typeof value==='number'&&Number.isFinite(value)?numberFormat.format(value):'未获取';}
function dateTime(value:string|null|undefined){if(!value)return '未获取';const date=new Date(value);return Number.isNaN(date.getTime())?'未获取':new Intl.DateTimeFormat('zh-CN',{year:'numeric',month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit',hour12:false,timeZone:'Asia/Shanghai'}).format(date);}
function growthSummary(growth:DiscoveryGrowth):string {
 const label=growth.status==='observed'?'已观测增量':growth.status==='incomparable'?'暂不可比':'待复测';
 if(growth.status!=='observed')return `${label}：${growth.reasons?.join('；')||'尚无可比较的历史采样'}`;
 const delta=growth.metric_deltas.likes;
 const likes=typeof delta==='number'&&Number.isFinite(delta)?`点赞增量 +${metric(delta)}`:'点赞增量未获取';
 const rate=typeof growth.likes_per_hour==='number'&&Number.isFinite(growth.likes_per_hour)?`${metric(growth.likes_per_hour)} 赞/小时`:'点赞增速未获取';
 const interval=typeof growth.elapsed_hours==='number'&&Number.isFinite(growth.elapsed_hours)?`间隔 ${metric(growth.elapsed_hours)} 小时`:'采样间隔未获取';
 return `${label}：${likes} · ${rate}；${dateTime(growth.previous_observed_at)} → ${dateTime(growth.current_observed_at)}（上海时间，${interval}）${growth.reasons?.length?`；${growth.reasons.join('；')}`:''}`;
}
function GrowthObservation({growth}:{growth?:DiscoveryGrowth}) {
 const observed=growth?.status==='observed',label=observed?'已观测增量':growth?.status==='incomparable'?'暂不可比':'待复测';
 return <div className={`discovery-growth ${observed?'observed':'pending'}`} aria-label="采样增量"><Badge tone={observed?'green':'amber'}>{label}</Badge>{growth?<p>{growthSummary(growth).replace(`${label}：`,'')}</p>:<p>尚无可比较的历史采样。再次获取同一作品的数据后核对增量。</p>}</div>;
}
function isResult(value:unknown):value is DiscoveryResult {return !!value&&typeof value==='object'&&Array.isArray((value as DiscoveryResult).items)&&Array.isArray((value as DiscoveryResult).sources)&&typeof (value as DiscoveryResult).query==='string';}

export function discoverySearchState(result?:DiscoveryResult) {
 const failedQueries=result?.query_runs?.filter(run=>run.status==='failed')||[];
 const allQueriesFailed=!!result?.query_runs?.length&&failedQueries.length===result.query_runs.length;
 const sourceIssues=result?.sources.filter(source=>source.status!=='ok')||[];
 const hasUsableQuery=!!result&&(result.items.length>0||(!allQueriesFailed&&result.sources.some(source=>source.status==='ok')));
 const incomplete=!hasUsableQuery||!!failedQueries.length||!!sourceIssues.length;
 return {failedQueries,sourceIssues,hasUsableQuery,incomplete,
  label:hasUsableQuery?(incomplete?'部分来源可用':'已完成'):(allQueriesFailed?'查询失败':'来源待处理')};
}
function sourceLabel(source:DiscoveryResult['sources'][number]) {
 if(source.status==='challenge')return source.challenge_visible===true?'需人工验证':'平台校验未确认';
 if(source.status==='blocked'&&source.http_status===403)return '公开访问受阻';
 return sourceLabels[source.status]||source.status;
}
function SourceEvidence({source}:{source:DiscoveryResult['sources'][number]}) {
 const httpStatus=[401,403,429].includes(source.http_status??0)?source.http_status:null;
 const hasVisibility=typeof source.challenge_visible==='boolean'||source.status==='challenge';
 if(!httpStatus&&!hasVisibility)return null;
 return <p className="discovery-source-evidence">{[httpStatus?`HTTP ${httpStatus}`:null,hasVisibility?`可见验证：${source.challenge_visible===true?'已确认':'未确认'}`:null].filter(Boolean).join(' · ')}</p>;
}
function sourceRecovery(source:DiscoveryResult['sources'][number]) {
 if(['login_required','not_connected'].includes(source.status))return '在「搜索账号与连接状态」核对当前所选账号的已有登录；项目运营账号与账号页使用同一项目会话。';
 if(source.status==='challenge')return source.challenge_visible===true?'官方页面已确认显示可见验证；请由本人在该窗口完成可见验证后重新搜索。':'尚未确认可见验证，暂时无法确定平台校验状态；请稍后手动重试，或选择其它可用平台。';
 if(source.status==='not_configured')return '请管理员检查来源配置后重新搜索，或选择当前可用的平台。';
 if(source.status==='rate_limited')return '本次请求受到平台限制；请稍后手动重试，或选择其它可用平台。';
 if(source.status==='blocked')return '本次公开访问受到平台限制；请稍后手动重试，或选择其它可用平台。';
 if(source.status==='busy')return '等待账号正在进行的任务结束后，再重新搜索。';
 return '当前来源查询不完整；查看具体原因，必要时重新查询或选择其它可用平台。';
}

export async function requestDiscoverySearch(projectId:string,filters:DiscoveryFilters,signal?:AbortSignal):Promise<{job_id:string}> {
 return api(`/api/projects/${encodeURIComponent(projectId)}/operations/discovery/search`,{method:'POST',body:{...filters,query:filters.query.trim()},signal});
}
export async function requestDiscoveryInspect(projectId:string,url:string,signal?:AbortSignal):Promise<{item:DiscoveryItem}> {
 return api(`/api/projects/${encodeURIComponent(projectId)}/operations/discovery/inspect`,{method:'POST',body:{url:url.trim()},signal});
}
export function discoveryOpportunitySeed(item:DiscoveryItem):OpportunitySeed {
 const measurements=Object.entries(metricLabels).map(([key,label])=>`${label} ${metric(item.metrics?.[key as keyof typeof metricLabels])}`).join(' · ');
 return {title:item.title,source_url:item.url,platform:platformLabels[item.platform]||item.platform,summary:[item.snippet,`作者：${item.author||'未获取'}`,`发布时间：${dateTime(item.published_at)}（上海时间）`,measurements,`数据获取：${dateTime(item.metrics_observed_at)}（上海时间）`,item.metrics_source?`指标来源：${item.metrics_source}`:null,item.growth?growthSummary(item.growth):null,`核验说明：${item.qualification_reason||'互动数据待核验'}`].filter(Boolean).join('\n')};
}

function ResultCard({item,onSave,onBreakdown,onCurate,editable}:{item:DiscoveryItem;onSave:(seed:OpportunitySeed)=>void;onBreakdown?:(source:BreakdownSource)=>void;onCurate?:(item:DiscoveryItem)=>void;editable:boolean}) {
 const href=safeUrl(item.url),verified=item.qualification==='verified_high_engagement';
 return <article className="discovery-result-card">
  <div className="discovery-result-top"><span>{platformLabels[item.platform]||item.platform}</span><Badge tone={verified?'green':'amber'}>{verified?'互动已核对':'待核验候选'}</Badge>{item.historical&&<Badge>历史参考</Badge>}</div>
  <h4>{item.title}</h4><p className="discovery-author">{item.author||'作者未获取'}</p>
  {item.snippet&&<p className="discovery-snippet">{item.snippet}</p>}
  <div className="discovery-metrics">{Object.entries(metricLabels).map(([key,label])=><div key={key}><span>{label}</span><strong>{metric(item.metrics?.[key as keyof typeof metricLabels])}</strong></div>)}</div>
  <GrowthObservation growth={item.growth}/>
  <div className="discovery-dates"><p><Icon name="clock" size={12}/>发布时间{item.published_at?`：${dateTime(item.published_at)}`:'未获取'}</p><p>数据获取：{dateTime(item.metrics_observed_at)}</p></div>
  <p className="discovery-qualification">{item.qualification_reason||'互动数据待核验'}</p>
  {!!item.match_keywords?.length&&<div className="discovery-matched">{item.match_keywords.map((word,index)=><span key={`${word}-${index}`}>{word}</span>)}</div>}
  <div className="discovery-result-actions">{href?<a href={href} target="_blank" rel="noreferrer">查看作品<Icon name="external" size={12}/></a>:<span>作品链接不可用</span>}<Button icon="plus" disabled={!editable||!href} onClick={()=>onCurate?onCurate(item):onSave(discoveryOpportunitySeed(item))}>{onCurate?'保存到参考库':'保存为发现'}</Button>{onBreakdown&&<Button icon="studio" disabled={!editable||!href} onClick={()=>onBreakdown({source_url:item.url,title:item.title})}>拆解并写脚本</Button>}</div>
 </article>;
}

export function DiscoverySearchResults({result,onSave,onBreakdown,onCurate,editable,verifiedOnly=false}:{result:DiscoveryResult;onSave:(seed:OpportunitySeed)=>void;onBreakdown?:(source:BreakdownSource)=>void;onCurate?:(item:DiscoveryItem)=>void;editable:boolean;verifiedOnly?:boolean}) {
 const rows=verifiedOnly?result.items.filter(item=>item.qualification==='verified_high_engagement'):result.items;
 const dated=rows.filter(item=>result.window_days===null||!!item.published_at),unknown=rows.filter(item=>result.window_days!==null&&!item.published_at);
 const verified=result.items.filter(item=>item.qualification==='verified_high_engagement').length;
 const state=discoverySearchState(result);
 return <div className="discovery-search-results">
  <div className="discovery-result-heading"><div><h3>房地产相关内容</h3><p>「{result.query}」 · {result.window_days===null?'不限发布时间':`近 ${result.window_days} 天`} · 本次搜索：{dateTime(result.searched_at)}（上海时间）</p></div><span>{result.items.length} 条候选 · {verified} 条互动已核对</span></div>
  {state.incomplete&&<div className="discovery-search-warning" role="alert"><strong>{state.hasUsableQuery?'本次搜索仅部分来源可用':'本次搜索未取得可用查询结果'}</strong><p>{state.hasUsableQuery?'仅对成功查询的来源展示结果；其余平台或关键词尚未完成有效查询。':'来源尚未成功返回可用数据，暂时无法判断所选范围是否有相关作品。请先处理来源或关键词查询问题，再重新搜索。'}</p>{!!state.failedQueries.length&&<ul>{state.failedQueries.map((run,i)=><li key={i}><strong>「{run.query}」查询失败</strong><span>{run.error||'未返回失败原因，请重新查询。'}</span></li>)}</ul>}</div>}
  {!!result.queries?.length&&<details className="discovery-query-coverage" open={result.queries_truncated||!!state.failedQueries.length}><summary>本次检索范围 · {result.queries.length}{result.total_query_count?` / ${result.total_query_count}`:''} 组查询</summary>{result.coverage_note&&<p>{result.coverage_note}</p>}<ul>{result.queries.map((q,i)=>{const run=result.query_runs?.find(run=>run.query===q);return <li key={i}>{q}{run&&<span> · {run.status==='failed'?'查询失败':`返回 ${run.total} 条候选`}</span>}</li>;})}</ul></details>}
  {!!result.sources.length&&<details className="discovery-source-details" open={state.incomplete}><summary>搜索来源与可用状态 · {result.sources.map(source=>`${platformLabels[source.platform]||source.platform} ${sourceLabel(source)}`).join(' / ')}</summary><div className="discovery-sources" aria-label="搜索来源状态">{result.sources.map((source,index)=><div key={`${source.platform}-${source.adapter}-${index}`}><strong>{platformLabels[source.platform]||source.platform}</strong><Badge tone={source.status==='ok'?'green':'amber'}>{sourceLabel(source)}</Badge><small>{adapterLabels[source.adapter]||'作品来源查询'}{source.query&&` · ${source.query}`}</small><p>{source.reason||'来源未提供说明'}</p><SourceEvidence source={source}/>{source.status!=='ok'&&<p className="discovery-source-recovery">{sourceRecovery(source)}</p>}</div>)}</div></details>}
  <p className="discovery-evidence-note">互动数来自本次获取的作品数据；累计互动不代表今日新增热度。未获取的指标与发布时间保留为空，候选内容需打开原作品核对。</p>
  {verifiedOnly&&result.items.length>verified&&<p className="discovery-hidden-candidates">另有 {result.items.length-verified} 条日期或指标待核验，关闭筛选查看。</p>}
  {dated.length?<div className="discovery-result-grid">{dated.map((item,index)=><ResultCard key={`${item.platform}-${item.content_id}-${index}`} item={item} onSave={onSave} onBreakdown={onBreakdown} onCurate={onCurate} editable={editable}/>)}</div>:state.hasUsableQuery&&!unknown.length&&<Empty icon="search" title={verifiedOnly?'暂无互动已核对的高互动作品':state.incomplete?'可用来源中暂无符合条件的作品':'所选时间范围内暂无可核对作品'}>{state.incomplete?'当前可用来源未返回符合条件的作品；请结合上方未完成查询的说明后再调整搜索。':'可以扩大发布时间范围，调整房地产关键词，或用已知作品链接核对数据。'}</Empty>}
  {!!unknown.length&&<section className="discovery-unknown-dates"><h3>发布时间待核验 · {unknown.length} 条</h3><p>以下作品的发布时间未获取，暂不计入所选时间范围。打开原作品核对后再使用。</p><div className="discovery-result-grid">{unknown.map((item,index)=><ResultCard key={`${item.platform}-${item.content_id}-${index}`} item={item} onSave={onSave} onBreakdown={onBreakdown} onCurate={onCurate} editable={editable}/>)}</div></section>}
 </div>;
}

export function DiscoverySearch({onSave,onBreakdown,editable}:{onSave:(seed:OpportunitySeed)=>void;onBreakdown?:(source:BreakdownSource)=>void;editable:boolean}) {
 const {project}=useApp(),task=useJob(),history=useResource<Job[]>(`/api/projects/${project.id}/jobs`);
 const [query,setQuery]=useState('房地产'),[platforms,setPlatforms]=useState(['douyin','bilibili']);
 const [accountScope,setAccountScope]=useState<SearchAccountScope>('operations');
 const [days,setDays]=useState<number|null>(30),[sort,setSort]=useState<DiscoveryFilters['sort']>('engagement');
 const [minLikes,setMinLikes]=useState(1000),[minViews,setMinViews]=useState(100000);
 const [verifiedOnly,setVerifiedOnly]=useState(false);
 const [submitting,setSubmitting]=useState(false),[error,setError]=useState(''),[message,setMessage]=useState('');
 const [result,setResult]=useState<DiscoveryResult|null>(null),[link,setLink]=useState(''),[inspecting,setInspecting]=useState(false),[inspectError,setInspectError]=useState(''),[inspected,setInspected]=useState<DiscoveryItem|null>(null);
 const mounted=useRef(true),generation=useRef(0),submitController=useRef<AbortController|null>(null),inspectController=useRef<AbortController|null>(null),hasSearch=useRef(false),projectId=useRef(project.id);
 const busy=submitting||task.busy;
 useEffect(()=>{mounted.current=true;return()=>{mounted.current=false;generation.current++;submitController.current?.abort();inspectController.current?.abort();};},[]);
 useEffect(()=>{
  if(projectId.current===project.id)return;
  projectId.current=project.id;generation.current++;submitController.current?.abort();inspectController.current?.abort();task.cancel();hasSearch.current=false;setResult(null);setInspected(null);setVerifiedOnly(false);setSubmitting(false);setInspecting(false);setError('');setInspectError('');setMessage('');
 },[project.id]);
 useEffect(()=>{
  if(hasSearch.current||!history.data?.length)return;
  const completed=history.data.filter(job=>job.kind==='discovery_search'&&job.status==='succeeded'&&isResult(job.result?.discovery_search)).sort((a,b)=>b.created_at.localeCompare(a.created_at))[0];
  if(completed){setResult(completed.result!.discovery_search as unknown as DiscoveryResult);setMessage('已恢复本项目上次搜索；点击搜索可重新获取。');}
 },[history.data]);
 function stop(){generation.current++;submitController.current?.abort();task.cancel();setSubmitting(false);setMessage('已停止接收后续结果。已提交的搜索可能继续运行，完成后可从项目记录恢复。');}
 async function search(event?:FormEvent){
  event?.preventDefault();if(!editable||busy)return;
  if(!query.trim()){setError('请填写房地产相关关键词');return;}
  if(!platforms.length){setError('请至少选择一个搜索平台');return;}
  const run=++generation.current;const controller=new AbortController();submitController.current=controller;hasSearch.current=true;setSubmitting(true);setError('');task.setError('');setMessage('');
  try{
   const submitted=await requestDiscoverySearch(project.id,{query,platforms,days,min_likes:minLikes,min_views:minViews,sort,account_scope:accountScope},controller.signal);
   if(!mounted.current||run!==generation.current)return;
   if(!submitted.job_id)throw new Error('搜索任务未返回编号，请重试');
   setSubmitting(false);
   const completed=await task.wait(submitted.job_id);
   if(!mounted.current||run!==generation.current||!completed)return;
   const value=completed.result?.discovery_search;
   if(!isResult(value))throw new Error('搜索任务未返回可核对结果，请重试');
   setResult(value);
  }catch(error){if(mounted.current&&run===generation.current&&!controller.signal.aborted)setError((error as Error).message);}
  finally{if(mounted.current&&run===generation.current)setSubmitting(false);if(submitController.current===controller)submitController.current=null;}
 }
 async function inspect(event:FormEvent){
  event.preventDefault();if(!editable||inspecting)return;
  if(!safeUrl(link)){setInspectError('请填写有效的作品链接');return;}
  const controller=new AbortController(),pid=project.id;inspectController.current=controller;setInspecting(true);setInspectError('');
  try{const response=await requestDiscoveryInspect(pid,link,controller.signal);if(mounted.current&&projectId.current===pid&&!controller.signal.aborted){if(!response.item?.title)throw new Error('未获取到可核对的作品数据');setInspected(response.item);}}
  catch(error){if(mounted.current&&projectId.current===pid&&!controller.signal.aborted)setInspectError((error as Error).message);}
  finally{if(mounted.current&&projectId.current===pid&&!controller.signal.aborted)setInspecting(false);if(inspectController.current===controller)inspectController.current=null;}
 }
 return <section className="discovery-search-panel" aria-label="房地产内容真实搜索">
  <div className="discovery-search-intro"><span className="discovery-search-icon"><Icon name="search" size={22}/></span><div><h3>搜索房地产相关的高互动内容</h3><p>按关键词重新查询作品来源，保留真实链接、互动数据与发布时间。可加入城市、住宅类型或受众需求，选择作品后拆解并写脚本。</p></div></div>
  <SearchAccountConnection scope={accountScope} editable={editable}/>
  <form onSubmit={search} className="discovery-search-form">
   <label className="discovery-query"><Icon name="search" size={17}/><input type="search" aria-label="房地产内容关键词" value={query} onChange={event=>setQuery(event.target.value)} placeholder="例如：上海 买房避坑 户型" disabled={!editable||busy}/><Button primary type="submit" busy={submitting} disabled={!editable||busy} icon="search">搜索相关内容</Button></label>
   <div className="discovery-keywords" aria-label="房地产内容方向">{presets.map(preset=><button key={preset} type="button" disabled={!editable||busy} className={query===preset?'selected':''} onClick={()=>setQuery(preset)}>{preset}</button>)}<span>选择方向后点击搜索</span></div>
   <div className="discovery-keywords" aria-label="住宅关键词">{keywords.map(word=><button key={word} type="button" disabled={!editable||busy} className={query.split(/\s+/).includes(word)?'selected':''} onClick={()=>setQuery(current=>current.split(/\s+/).includes(word)?current.split(/\s+/).filter(value=>value!==word).join(' '):`${current.trim()} ${word}`.trim())}>{word}</button>)}</div>
   <div className="discovery-filter-grid"><fieldset><legend>搜索平台</legend>{Object.entries(platformLabels).map(([value,label])=><label key={value}><input type="checkbox" checked={platforms.includes(value)} disabled={!editable||busy} onChange={event=>setPlatforms(current=>event.target.checked?[...current,value]:current.filter(platform=>platform!==value))}/>{label}</label>)}</fieldset><Field label="发布时间"><select value={days??'all'} disabled={!editable||busy} onChange={event=>setDays(event.target.value==='all'?null:Number(event.target.value))}>{[7,30,90,365].map(value=><option key={value} value={value}>近 {value} 天</option>)}<option value="all">不限时间</option></select></Field><Field label="排序"><select value={sort} disabled={!editable||busy} onChange={event=>setSort(event.target.value as DiscoveryFilters['sort'])}><option value="engagement">互动优先</option><option value="latest">最新发布</option><option value="relevance">相关优先</option><option value="growth">已观测增量</option></select></Field><Field label="高互动点赞门槛"><input type="number" min="0" value={minLikes} disabled={!editable||busy} onChange={event=>setMinLikes(Math.max(0,Number(event.target.value)))}/></Field><Field label="高互动播放门槛"><input type="number" min="0" value={minViews} disabled={!editable||busy} onChange={event=>setMinViews(Math.max(0,Number(event.target.value)))}/></Field></div>
  </form>
  <Field label="搜索账号"><select value={accountScope} disabled={!editable||busy} onChange={event=>setAccountScope(event.target.value as SearchAccountScope)}><option value="operations">项目运营账号</option><option value="research">独立研究账号</option></select></Field>
  <p className="discovery-filter-note">默认近30天，适合查找近期房地产内容。累计互动量不代表近期增速。「已观测增量」按实际采样区间的点赞增速排序，无基线的作品显示待复测。互动门槛只用于标记已获取数据的作品；缺少指标的作品会列为待核验候选。</p>
  <p className="discovery-filter-note">默认同时检索B站公开内容；{accountScope==='operations'?'抖音使用本项目运营账号，可核对账号页已有登录。':'抖音使用独立研究账号，需在当前研究入口核对会话。'}平台可用状态以本次实际返回为准。</p>
  <p className="discovery-filter-note">修改关键词、平台、发布时间、排序或互动门槛后，点击「搜索相关内容」重新查询。</p>
  <div className="discovery-local-filter"><label><input type="checkbox" checked={verifiedOnly} disabled={!result} onChange={event=>setVerifiedOnly(event.target.checked)}/>只看互动已核对</label><span>仅筛选已获取结果，不重新联网。</span></div>
  {!editable&&<p className="discovery-permission"><Icon name="lock" size={13}/>当前角色可查看已有结果；重新搜索和保存需要项目管理员或内容负责人。</p>}
  {busy&&<div className="discovery-progress" role="status"><div><span className="spinner"/><span>{submitting?'正在提交搜索…':task.statusText}</span>{!submitting&&task.job&&<progress max="100" value={task.percent} aria-label="搜索任务进度"/>}</div><Button onClick={stop}>停止接收结果</Button></div>}
  {(error||task.error)&&<ErrorNotice message={error||task.error} retry={()=>void search()}/>}<p className="discovery-status-message" role="status">{message}</p>
  {result&&<DiscoverySearchResults result={result} onSave={onSave} onBreakdown={onBreakdown} editable={editable} verifiedOnly={verifiedOnly}/>}
  <details className="discovery-inspect"><summary>用作品链接核对数据</summary><p>已有房地产作品参考时，可粘贴 B站作品链接读取公开互动数据；其它平台是否可读取以返回结果为准。</p><form onSubmit={inspect}><Field label="作品链接"><input type="url" value={link} disabled={!editable||inspecting} onChange={event=>setLink(event.target.value)} placeholder="https://www.bilibili.com/video/BV…"/></Field><Button type="submit" primary busy={inspecting} disabled={!editable||!link.trim()}>核对作品数据</Button></form>{inspectError&&<ErrorNotice message={inspectError}/>} {inspected&&<div className="discovery-inspected"><ResultCard item={inspected} onSave={onSave} onBreakdown={onBreakdown} editable={editable}/></div>}</details>
 </section>;
}
