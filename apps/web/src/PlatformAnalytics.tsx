import {useEffect,useRef,useState} from 'react';
import {api,safeUrl} from './api.js';
import {useApp,useJob,useResource} from './hooks';
import {Badge,Button,ErrorNotice,Icon,Loading} from './components';
import {stageAgentPrompt} from './agent-prompt.js';
import type {PlatformAccount} from './PlatformAccounts';
import type {Job} from './types';

export interface PlatformAnalyticsItem {id?:string;title?:string;url?:string;published_at?:string|null;metrics_text?:string;metrics?:Record<string,number|null|undefined>}
export interface PlatformAnalyticsData {platform:string;session_id?:string|null;status:string;reason?:string;observed_at?:string|null;source?:string;adapter?:string;metric_scope?:string;limitations?:string[];period_metrics?:{label:string;value:string|number|null;comparison?:string;window?:string}[];metrics:Record<string,number|null|undefined>;items:PlatformAnalyticsItem[]}
const metricLabels:Record<string,string>={followers:'粉丝',following:'关注',works:'作品',reads:'阅读',views:'播放',likes:'点赞',comments:'评论',saves:'收藏',shares:'分享'};
const statusLabels:Record<string,string>={success:'已读取平台数据',succeeded:'已读取平台数据',partial:'部分数据可用',not_synced:'尚未同步',not_refreshed:'尚未同步',not_connected:'尚未登录',login_required:'需要登录',unavailable:'当前不可读取',blocked:'平台暂时限制读取',not_supported:'暂未支持读取',not_configured:'尚未准备好',busy:'平台正在处理其他操作',challenge:'需要官方安全验证',stale_session:'账号已改变，待重新同步',session_changed:'账号已改变，待重新同步'};
function usableAccount(account?:PlatformAccount):boolean{return Boolean(account?.loggedIn&&account.verified===true&&typeof account.session_id==='string'&&account.session_id.trim());}
function boundSnapshot(snapshot:PlatformAnalyticsData,account:PlatformAccount):boolean{return Boolean(usableAccount(account)&&snapshot.session_id&&snapshot.session_id===account.session_id);}
function metricText(value:unknown):string{return typeof value==='number'&&Number.isFinite(value)&&value>=0?value.toLocaleString('zh-CN'):'未获取';}
function observedTime(value?:string|null):string{if(!value)return '未获取';const parsed=new Date(value);return Number.isNaN(parsed.getTime())?'未获取':parsed.toLocaleString('zh-CN',{hour12:false});}
export function currentPlatformAnalytics(rows:PlatformAnalyticsData[],account?:PlatformAccount):PlatformAnalyticsData|null{
 if(!account)return null;const row=rows.find(value=>value.platform===account.platform);if(!row)return null;
 const hasCounts=Object.values(row.metrics||{}).some(value=>typeof value==='number'&&Number.isFinite(value)&&value>=0);
 if(boundSnapshot(row,account)||(!row.session_id&&!hasCounts&&!row.items?.length&&!row.period_metrics?.length))return row;
 return {...row,status:'stale_session',reason:'平台账号会话已改变，请重新同步当前账号的运营数据。',observed_at:null,source:undefined,adapter:undefined,metrics:{},items:[],period_metrics:[],limitations:[]};
}
export async function requestPlatformAnalyticsRefresh(base:string,account:PlatformAccount,signal?:AbortSignal):Promise<{job_id:string}>{
 if(!usableAccount(account))throw new Error('请先登录并校验本项目的平台账号会话。');
 return api(`${base}/${encodeURIComponent(account.platform)}/refresh`,{method:'POST',signal});
}
export function platformAnalyticsResult(job:Job|undefined,account:PlatformAccount):PlatformAnalyticsData|null{
 if(!job||job.kind!=='platform_analytics'||job.status!=='succeeded')return null;const raw=job.result?.platform_analytics;
 if(!raw||typeof raw!=='object')return null;const snapshot=raw as PlatformAnalyticsData;
 return snapshot.platform===account.platform&&boundSnapshot(snapshot,account)&&snapshot.metrics&&Array.isArray(snapshot.items)?snapshot:null;
}
export function platformAnalyticsPrompt(account:PlatformAccount,snapshot:PlatformAnalyticsData|null):string{
 const data=currentPlatformAnalytics(snapshot?[snapshot]:[],account),metrics=Object.entries(metricLabels).map(([key,label])=>`${label}：${metricText(data?.metrics?.[key])}`).join('；');
 return `请基于本项目的${account.name}账号「${account.identity_name||'昵称未获取'}」做住宅内容运营复盘与选题建议。先用平台账号工具核对当前会话，再读取项目保存的运营数据。${data?.observed_at?`当前数据读取时间：${data.observed_at}。`:'当前没有已核对的运营数据，请先同步。'}${metrics}。${data?.reason?`读取说明：${data.reason}。`:''}缺失指标保持未获取，不推断增长或爆款。给出内容表现、受众问题和下一轮选题，涉及发布时先准备确切内容交给我确认。`;
}
export function PlatformAnalyticsSnapshot({snapshot,account}:{snapshot:PlatformAnalyticsData|null;account:PlatformAccount}){
 const data=currentPlatformAnalytics(snapshot?[snapshot]:[],account),status=data?.status||'not_synced';
 return <div className="platform-analytics-snapshot"><div className="platform-analytics-status"><Badge tone={['success','succeeded'].includes(status)?'green':status==='partial'?'blue':'neutral'}>{statusLabels[status]||'读取状态待核对'}</Badge><span>{account.identity_name||'平台昵称未获取'}</span></div>
  {data?.reason&&<p className="platform-analytics-reason">{data.reason}</p>}
  <dl className="platform-analytics-metrics">{Object.entries(metricLabels).map(([key,label])=><div key={key}><dt>{label}</dt><dd>{metricText(data?.metrics?.[key])}</dd></div>)}</dl>
  <div className="platform-analytics-provenance"><span>数据读取时间：<time dateTime={data?.observed_at||undefined}>{observedTime(data?.observed_at)}</time></span><span>来源：{data?.source||data?.adapter||'未提供来源'}</span></div>
  {data?.period_metrics?.length?<div className="platform-analytics-period"><h4>平台期间指标</h4><dl>{data.period_metrics.map((metric,index)=><div key={`${index}:${metric.label}`}><dt>{metric.label}{metric.window&&<small>（{metric.window}）</small>}</dt><dd>{typeof metric.value==='string'?metric.value:metricText(metric.value)}{metric.comparison&&<small>{metric.comparison}</small>}</dd></div>)}</dl></div>:null}
  {data?.items?.length?<div className="platform-analytics-works"><h4>已读取作品（{data.items.length}）</h4>{data.items.map((item,index)=>{const href=safeUrl(item.url);return <article key={item.id||`${index}:${item.title}`}><div>{href?<a href={href} target="_blank" rel="noreferrer">{item.title||'标题未获取'}<Icon name="external" size={12}/></a>:<strong>{item.title||'标题未获取'}</strong>}<small>发布时间：{observedTime(item.published_at)}</small></div><p>播放 / 阅读 {metricText(item.metrics?.views)} · 点赞 {metricText(item.metrics?.likes)} · 评论 {metricText(item.metrics?.comments)}</p>{item.metrics_text&&<small className="platform-analytics-raw">平台指标原文：{item.metrics_text}</small>}</article>;})}</div>:null}
  {data?.limitations?.length?<ul className="platform-analytics-limitations">{data.limitations.map((value,index)=><li key={index}>{value}</li>)}</ul>:null}
  <p className="platform-agent-note">显示上次读取的数据。平台未提供的指标会标记为「未获取」；累计互动数不能直接证明近期增长。</p>
 </div>;
}
export function PlatformAnalytics({account,editable}:{account:PlatformAccount;editable:boolean}){
 const {project,navigate}=useApp(),base=`/api/projects/${project.id}/operations/platform-analytics`,cached=useResource<PlatformAnalyticsData[]>(base),task=useJob();
 const key=`${project.id}:${account.platform}:${account.session_id||''}`,[local,setLocal]=useState<{key:string;data:PlatformAnalyticsData}|null>(null),[error,setError]=useState(''),[submitting,setSubmitting]=useState(false);
 const mounted=useRef(true),currentKey=useRef(key),controller=useRef<AbortController|null>(null),pendingKey=useRef('');currentKey.current=key;
 const snapshot=currentPlatformAnalytics(local?.key===key?[local.data]:cached.data||[],account),busy=pendingKey.current===key&&(submitting||task.busy);
 useEffect(()=>{mounted.current=true;return()=>{mounted.current=false;controller.current?.abort();};},[]);
 useEffect(()=>{controller.current?.abort();task.cancel();pendingKey.current='';setSubmitting(false);setError('');setLocal(null);},[key]);
 async function synchronize(){
  if(!editable||busy||!usableAccount(account))return;const operation=new AbortController(),scope=key;controller.current=operation;pendingKey.current=scope;setSubmitting(true);setError('');task.setError('');
  const active=()=>mounted.current&&currentKey.current===scope&&!operation.signal.aborted;
  try{const response=await requestPlatformAnalyticsRefresh(base,account,operation.signal);if(!active())return;if(!response.job_id)throw new Error('运营数据任务未返回编号，请重试。');setSubmitting(false);const completed=await task.wait(response.job_id);if(!active())return;const result=platformAnalyticsResult(completed,account);if(result)setLocal({key:scope,data:result});else if(completed)setError('账号会话或平台已改变，请重新同步。');cached.retry();}
  catch(error){if(active())setError((error as Error).message);}
  finally{if(controller.current===operation)controller.current=null;if(active())setSubmitting(false);}
 }
 function stop(){controller.current?.abort();task.cancel();pendingKey.current='';setSubmitting(false);setError('已停止接收结果。已提交的数据读取任务可能继续执行，可稍后刷新查看。');}
 function handoff(){try{if(!stageAgentPrompt(sessionStorage,project.id,platformAnalyticsPrompt(account,snapshot)))throw new Error('未能保存运营复盘指令，请重试。');navigate('assistant');}catch(error){setError((error as Error).message);}}
 return <section className="platform-analytics" aria-label={`${account.name}运营数据`}><div className="platform-analytics-heading"><div><h3>{account.name}运营数据</h3><p>同步当前账号的数据，供楼管家复盘与安排下一轮内容。</p></div><Button icon="refresh" busy={busy} disabled={!editable||busy||!usableAccount(account)} onClick={()=>void synchronize()}>同步运营数据</Button></div>
  {cached.loading&&!local?<Loading text="正在读取项目保存的运营数据…"/>:cached.error&&!local?<ErrorNotice message={cached.error} retry={cached.retry}/>:<PlatformAnalyticsSnapshot snapshot={snapshot} account={account}/>}
  {!usableAccount(account)&&<p className="platform-agent-note">请先登录并校验 {account.name}，再同步本项目的运营数据。</p>}
  {busy&&<div className="platform-task-progress" role="status"><span className="spinner"/><span>{submitting?'正在提交数据读取任务…':task.statusText}</span>{!submitting&&task.busy&&task.job&&<progress max="100" value={task.percent} aria-label="运营数据任务进度"/>}<Button onClick={stop}>停止接收结果</Button></div>}
  {(error||task.error)&&<ErrorNotice message={error||task.error}/>}<Button icon="sparkle" onClick={handoff} disabled={!editable||busy}>交给楼管家复盘</Button>
 </section>;
}
