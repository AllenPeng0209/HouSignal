import {useCallback,useEffect,useRef,useState} from 'react';
import {api,ApiError,safeUrl} from './api.js';
import {useApp,useJob,useResource} from './hooks';
import {Badge,Button,ErrorNotice,Field,Icon,Loading,Modal} from './components';
import {stageAgentPrompt} from './agent-prompt.js';
import {PlatformAnalytics} from './PlatformAnalytics';
import {PlatformCredentials} from './PlatformCredentials';
import type {Content,Job} from './types';
import './platform-accounts.css';

export interface PlatformAccount {platform:string;name:string;backend:string;supported:boolean;loggedIn:boolean;verified?:boolean;ready?:boolean;note?:string;identity_name?:string;avatar?:string;session_id?:string|null}
export interface LoginStatus {state:string;message?:string;qrReady?:boolean;qr?:boolean|string;qrTs?:number|string;loggedIn?:boolean;verified?:boolean}
interface ApprovedContent extends Content {approval_valid?:boolean;approved_release_id?:string|null}
interface PublicationPreview {title:string;body:string;draft_only:boolean;content_format:'video'|'text'|'html'}
function publicationPreview(value:unknown):PublicationPreview|null{
 if(!value||typeof value!=='object')return null;const preview=value as Partial<PublicationPreview>;
 return typeof preview.title==='string'&&Boolean(preview.title.trim())&&typeof preview.body==='string'&&typeof preview.draft_only==='boolean'&&['video','text','html'].includes(preview.content_format||'')?preview as PublicationPreview:null;
}
type PlatformAction='login'|'status'|'verify'|'logout'|'sms'|'prepare'|'publish'|'publish-status'|'publish-sms';
const terminalStates=['success','expired','error','cancelled','unknown','not_configured','idle'];
const channelAliases:Record<string,string[]>={bilibili:['B站','B 站','哔哩哔哩'],'weixin-channels':['微信视频号','视频号'],'wechat-oa':['微信公众号','公众号']};
const stateLabels:Record<string,string>={starting:'正在启动登录',qr_ready:'请用手机 App 扫码',scanned:'扫码成功，请在手机确认',verifying:'正在验证登录',sms_required:'需要短信验证',window_login:'请在官方浏览器窗口登录',challenge:'请在官方窗口完成安全验证',unknown:'尚未确认登录状态',not_configured:'平台登录尚未准备好',success:'登录成功',expired:'二维码已过期',error:'登录未完成',cancelled:'登录已结束',idle:'尚未登录'};
export function preparedSessionMatches(account:PlatformAccount|undefined,sessionId:unknown):boolean{
 return Boolean(account?.loggedIn&&account.verified===true&&typeof account.session_id==='string'&&account.session_id.trim()&&typeof sessionId==='string'&&sessionId.trim()&&account.session_id===sessionId);
}
export function requestPlatformAction<T=LoginStatus>(base:string,platform:string,action:PlatformAction,body?:Record<string,unknown>,signal?:AbortSignal):Promise<T>{
 return api(`${base}/${encodeURIComponent(platform)}/${action}`,{method:['status','publish-status'].includes(action)?'GET':'POST',...(body===undefined?{}:{body}),signal});
}
export async function requestConfirmedPlatformPublish(base:string,account:PlatformAccount,content:ApprovedContent,prepared:Job,requestKey:string,signal?:AbortSignal):Promise<{job_id:string}>{
 const result=prepared.result,release=content.approved_release_id||content.release_id;
 if(!release||prepared.kind!=='platform_prepare'||prepared.status!=='succeeded'||result?.platform!==account.platform||result.content_version_id!==content.id||result.release_id!==release||!publicationPreview(result.publication_preview)||!preparedSessionMatches(account,result.session_id)||!requestKey.trim())throw new Error('内容、审批或平台账号会话已改变，请重新准备发布包并确认。');
 return requestPlatformAction(base,account.platform,'publish',{content_version_id:content.id,expected_release_id:result.release_id,expected_session_id:result.session_id,confirmed:true,request_key:requestKey},signal);
}

function delay(ms:number,signal:AbortSignal){return new Promise<void>((resolve,reject)=>{
 signal.throwIfAborted();const timer=setTimeout(()=>{signal.removeEventListener('abort',abort);resolve();},ms);
 function abort(){clearTimeout(timer);signal.removeEventListener('abort',abort);reject(signal.reason||new DOMException('已停止登录状态查询','AbortError'));}signal.addEventListener('abort',abort,{once:true});
});}
export async function watchPlatformLogin(base:string,platform:string,{signal,onStatus,pollMs=2000,maxMs=300000}:{signal:AbortSignal;onStatus:(value:LoginStatus)=>void;pollMs?:number;maxMs?:number}):Promise<LoginStatus>{
 const started=Date.now();let failures=0;
 while(Date.now()-started<maxMs){
  signal.throwIfAborted();
  try{const status=await requestPlatformAction< LoginStatus>(base,platform,'status',undefined,signal);signal.throwIfAborted();failures=0;onStatus(status);signal.throwIfAborted();if(terminalStates.includes(status.state))return status;}
  catch(error){if(signal.aborted)throw signal.reason||error;if(error instanceof ApiError&&[401,403].includes(error.status))throw error;if(++failures>=3)throw new Error(`登录状态查询失败：${(error as Error).message}`);}
  await delay(pollMs,signal);
 }
 const expired={state:'expired',message:'登录等待已超时，请重新连接平台。'};onStatus(expired);return expired;
}
export async function watchPlatformPublication(base:string,platform:string,{signal,onStatus,pollMs=2000}:{signal:AbortSignal;onStatus:(value:LoginStatus)=>void;pollMs?:number}):Promise<never>{
 let failures=0;
 while(true){
  signal.throwIfAborted();
  try{const status=await requestPlatformAction(base,platform,'publish-status',undefined,signal);signal.throwIfAborted();failures=0;onStatus(status);signal.throwIfAborted();}
  catch(error){if(signal.aborted)throw signal.reason||error;if(error instanceof ApiError&&[401,403].includes(error.status))throw error;if(++failures>=3)throw new Error(`无法读取平台发布验证状态：${(error as Error).message}`);}
  await delay(pollMs,signal);
 }
}
export function PlatformPublicationSms({status,code,error,busy,onCode,onSubmit}:{status:LoginStatus|null;code:string;error:string;busy:boolean;onCode:(value:string)=>void;onSubmit:()=>void}){
 if(status?.state!=='sms_required')return status?.state==='verifying'?<p className="platform-agent-note" role="status">{status.message||'正在核对发布验证码…'}</p>:null;
 return <form className="platform-publication-sms" onSubmit={event=>{event.preventDefault();onSubmit();}}><h4>平台发布需要手机验证</h4><p>{status.message||'请填写平台发到你手机的验证码，以继续本次发布任务。'}</p><Field label="发布短信验证码" hint="验证码仅用于这次平台验证，不进入楼管家对话。"><input aria-label="发布短信验证码" inputMode="numeric" autoComplete="one-time-code" maxLength={8} value={code} onChange={event=>onCode(event.target.value.replace(/\D/g,'').slice(0,8))}/></Field>{error&&<ErrorNotice message={error}/>}<Button primary type="submit" busy={busy} disabled={!/^\d{4,8}$/.test(code)}>提交发布验证码</Button></form>;
}
export function eligiblePlatformContents(contents:ApprovedContent[],account?:PlatformAccount):ApprovedContent[]{return account?contents.filter(content=>content.status==='approved'&&content.approval_valid===true&&Boolean(content.render_asset_id)&&(channelAliases[account.platform]||[account.name]).includes(content.channel)):[];}
export function matchingActivePublication(jobs:Job[],platform:string,localJob:Job|null,role:string):Job|null{
 if(!['owner','admin','content'].includes(role))return null;
 return jobs.find(job=>job.kind==='platform_publish'&&['queued','running'].includes(job.status)&&(job.platform||job.result?.platform)===platform&&(job.can_verify===true||['owner','admin'].includes(role))&&!(localJob?.id===job.id&&['succeeded','failed','cancelled'].includes(localJob.status)))||null;
}
export function matchingPreparedJob(jobs:Job[],account?:PlatformAccount,content?:ApprovedContent):Job|null{
 if(!account||!content)return null;const release=content.approved_release_id||content.release_id;if(!release)return null;
 return jobs.find(job=>job.kind==='platform_prepare'&&job.status==='succeeded'&&job.result?.platform===account.platform&&job.result?.content_version_id===content.id&&job.result?.release_id===release&&preparedSessionMatches(account,job.result?.session_id))||null;
}
export function platformOperationsPrompt(account:PlatformAccount|undefined,content:ApprovedContent|null|undefined,instructions=''):string{
 const platform=(account?.name||'尚未选择的平台').slice(0,30),identity=account?.loggedIn&&account.verified&&account.identity_name?`平台显示账号「${account.identity_name.slice(0,80)}」`:'先核对真实平台账号身份';
 const selection=content?`使用内容版本 ${content.id}（${(content.cover_title||content.topic||'已审核内容').slice(0,120)}，v${content.version}）准备发布包与操作计划。`:'查看已审核内容，先列出可操作选项。';
 const context=`只在本项目的${platform}进行操作，${identity}。先校验当前平台登录状态。${selection}发布前把具体账号、标题、正文与审核版本交给我确认。`;
 return `${context}${instructions.trim()?`我的运营目标：${instructions.trim().slice(0,1500)}`:''}`.slice(0,2000);
}

export function PlatformAccountsGrid({accounts,editable,busy,onLogin,onVerify,onLogout}:{accounts:PlatformAccount[];editable:boolean;busy:string;onLogin:(account:PlatformAccount)=>void;onVerify:(account:PlatformAccount)=>void;onLogout:(account:PlatformAccount)=>void}){
 return <div className="platform-account-grid">{accounts.map(account=>{
  const active=account.loggedIn,verified=active&&account.verified===true,disabled=!editable||!account.supported||Boolean(busy),avatar=safeUrl(account.avatar);
  return <article className="platform-account-card" key={account.platform}><div className="platform-account-head"><h3>{account.name}</h3><Badge tone={verified?'green':'neutral'}>{!account.supported?'暂不可用':verified?'已登录':active?'待校验':'未登录'}</Badge></div>
   {verified?<div className="platform-account-identity"><span className="platform-identity-avatar">{avatar?<img src={avatar} alt="" loading="lazy" referrerPolicy="no-referrer"/>:account.identity_name?.slice(0,1)||'?'}</span><strong>{account.identity_name||'昵称未获取，请校验账号'}</strong></div>:<p className="platform-account-backend">{active?'登录态已保存，等待平台校验':'本项目独立平台会话'}</p>}
   {account.note&&<p className="platform-account-note">{account.note}</p>}
   <div className="platform-account-actions">{active?<><Button disabled={disabled} busy={busy===`${account.platform}:verify`} onClick={()=>onVerify(account)}>校验账号</Button><Button disabled={disabled} busy={busy===`${account.platform}:logout`} onClick={()=>onLogout(account)}>退出登录</Button></>:<Button primary disabled={disabled} busy={busy===`${account.platform}:login`} onClick={()=>onLogin(account)}>登录</Button>}</div>
  </article>;
 })}</div>;
}
export function PlatformLoginPanel({account,status,qrSource,qrError='',smsCode,smsError,smsBusy,onSmsCode,onSms,onRetry,onCancel,cancelBusy=false}:{account:PlatformAccount;status:LoginStatus;qrSource:string;qrError?:string;smsCode:string;smsError:string;smsBusy:boolean;onSmsCode:(value:string)=>void;onSms:()=>void;onRetry:()=>void;onCancel?:()=>void;cancelBusy?:boolean}){
 const ready=Boolean(status.qrReady||status.qr),terminal=terminalStates.includes(status.state),officialWindow=['window_login','challenge','unknown','not_configured'].includes(status.state);
 return <div className="platform-login-panel"><p className={`platform-login-state ${status.state==='success'?'success':terminal?'failed':''}`} role="status">{stateLabels[status.state]||status.state}</p>{status.message&&<p className="platform-login-message">{status.message}</p>}
  {status.state==='sms_required'?<form onSubmit={event=>{event.preventDefault();onSms();}}><Field label="短信验证码" hint="验证码仅提交给当前平台的登录流程。"><input aria-label="短信验证码" inputMode="numeric" autoComplete="one-time-code" value={smsCode} onChange={event=>onSmsCode(event.target.value.replace(/\D/g,'').slice(0,8))} maxLength={8}/></Field>{smsError&&<ErrorNotice message={smsError}/>}<Button primary type="submit" busy={smsBusy} disabled={smsCode.length<4}>提交验证码</Button></form>:
   status.state==='success'?<div className="platform-login-success"><Icon name="check" size={42}/><p>本项目会话已保存，可校验账号并交给楼管家准备内容。</p></div>:
   officialWindow?<div className="platform-login-window"><Icon name="external" size={32}/><p>{status.state==='challenge'?'请在已经打开的平台官方浏览器窗口中完成安全验证。确认后这里会继续核对登录状态。':status.state==='window_login'?'请切换到已经打开的平台官方浏览器窗口，完成登录与手机确认。这里会同步登录结果。':'平台未返回可确认的登录结果。请检查官方浏览器窗口，或重新连接。'}</p>{terminal&&<Button primary icon="refresh" onClick={onRetry}>重新连接</Button>}</div>:
   terminal?<div className="platform-login-retry"><Button primary icon="refresh" onClick={onRetry}>重新获取二维码</Button></div>:
   ready&&status.state==='qr_ready'?(qrSource?<img className="platform-login-qr" src={qrSource} alt={`${account.name}登录二维码`}/>:qrError?<ErrorNotice message={qrError} retry={onRetry}/>:<Loading text="正在读取登录二维码…"/>):<Loading text={status.state==='scanned'?'请在手机确认登录…':status.state==='verifying'?'正在验证登录…':'正在准备二维码…'}/>}
  {!terminal&&!officialWindow&&<p className="platform-login-hint">请用自己的 {account.name} 账号扫码。平台要求的手机确认或验证码由你完成。</p>}
  {onCancel&&status.state!=='success'&&status.state!=='cancelled'&&<Button busy={cancelBusy} disabled={cancelBusy} onClick={onCancel}>结束登录</Button>}
 </div>;
}
function packageHref(value:unknown){if(typeof value!=='string')return null;if(value.startsWith('/api/')&&!value.startsWith('//'))return value;return null;}
export function PlatformPreparePanel({job}:{job:Job}){
 const result=job.result||{},pack=result.package as {name?:string;download_url?:string}|undefined,plan=result.plan as {steps?:unknown[];draft_only?:boolean}|undefined,href=packageHref(pack?.download_url);
 return <div className="platform-prepared-result"><div className="platform-prepared-heading"><Badge tone="green">发布准备已完成</Badge><Badge tone="neutral">尚未发布</Badge></div>{pack?.name&&<p>{pack.name}</p>}{href&&<a className="btn" href={href} download><Icon name="download" size={15}/><span>下载 {pack?.name||'发布包'}</span></a>}{plan?.steps?.length?<ol>{plan.steps.map((step,index)=><li key={index}>{typeof step==='string'?step:typeof step==='object'&&step&&'description' in step?String(step.description):JSON.stringify(step)}</li>)}</ol>:null}<p className="platform-agent-note">发布包与操作计划已保存在项目任务中。平台提交后仍需核对真实回执。</p></div>;
}
export function PlatformPublishConfirmation({content,preparedContentId,preparedSessionId,preview:rawPreview,account,confirmed,onConfirmed,onPublish,editable,busy}:{content:ApprovedContent;preparedContentId:string;preparedSessionId?:unknown;preview?:unknown;account?:PlatformAccount;confirmed:boolean;onConfirmed:(value:boolean)=>void;onPublish:()=>void;editable:boolean;busy:boolean}){
 const preview=publicationPreview(rawPreview),sessionMatches=preparedSessionMatches(account,preparedSessionId),matches=content.id===preparedContentId&&Boolean(preview)&&sessionMatches;
 return <div className="platform-publication-confirmation"><h4>确认这条内容提交平台</h4><dl><div><dt>发布渠道</dt><dd>{content.channel}</dd></div>{account&&<div><dt>平台账号</dt><dd>{account.identity_name||'昵称未获取，请先校验账号'}</dd></div>}<div><dt>账号绑定</dt><dd>{sessionMatches?'已绑定当前已校验平台会话':'平台账号会话已改变，请重新准备发布包。'}</dd></div><div><dt>发布标题</dt><dd>{preview?.title||'请重新准备发布包，获取确切的平台提交内容。'}</dd></div><div><dt>内容版本</dt><dd>v{content.version} · 审核版本 {content.approved_release_id||content.release_id||'—'}</dd></div></dl>{preview&&<details><summary>{preview.content_format==='html'?'查看将提交的 HTML 正文':'查看将提交的已审核正文'}</summary><pre>{preview.body}</pre></details>}<label className="platform-confirm-checkbox"><input type="checkbox" checked={confirmed} disabled={!editable||busy||!matches} onChange={event=>onConfirmed(event.target.checked)}/>我已核对平台账号、标题、正文与审核版本，确认提交这条内容。</label><Button primary icon="upload" onClick={onPublish} disabled={!editable||busy||!matches||!confirmed} busy={busy}>确认并提交平台</Button><p className="platform-agent-note">{preview?.draft_only?'提交将保存到平台草稿箱。':'提交后需要等待平台处理。'}平台审核通过并取得真实回执后才算已发布。</p></div>;
}

function PublicationResult({job}:{job:Job}){
 const result=job.result||{},state=String(result.status||''),labels:Record<string,string>={submitted:'已提交平台',review_pending:'等待平台审核',draft_saved:'已保存平台草稿',reconciliation_required:'需要人工核对结果'},href=safeUrl(result.url);
 return <div className="platform-prepared-result" role="status"><Badge tone={state==='reconciliation_required'?'amber':'blue'}>{labels[state]||'平台任务已结束'}</Badge>{typeof result.message==='string'&&<p>{result.message}</p>}{href&&<a href={href} target="_blank" rel="noreferrer" className="operations-source"><Icon name="external" size={14}/>打开平台结果</a>}<p className="platform-agent-note">以平台实际状态与核验回执为准。</p></div>;
}

export function PlatformAccounts(){
 const {project,session,navigate,notify}=useApp(),editable=['owner','admin','content'].includes(session.role),base=`/api/projects/${project.id}/operations/platform-accounts`;
 const accounts=useResource<PlatformAccount[]>(base),contents=useResource<ApprovedContent[]>(`/api/projects/${project.id}/contents`),history=useResource<Job[]>(`${base}/tasks`),task=useJob();
 const [busy,setBusy]=useState(''),[error,setError]=useState(''),[notice,setNotice]=useState(''),[login,setLogin]=useState<{account:PlatformAccount;status:LoginStatus}|null>(null),[smsCode,setSmsCode]=useState(''),[smsError,setSmsError]=useState(''),[smsBusy,setSmsBusy]=useState(false);
 const [qrSource,setQrSource]=useState(''),[qrError,setQrError]=useState(''),[selectedPlatform,setSelectedPlatform]=useState(''),[contentId,setContentId]=useState(''),[instructions,setInstructions]=useState(''),[submitting,setSubmitting]=useState(false),[prepared,setPrepared]=useState<{job:Job;contentId:string;platform:string;releaseId:string}|null>(null),[confirmed,setConfirmed]=useState(false),[publication,setPublication]=useState<Job|null>(null);
 const [publishChallenge,setPublishChallenge]=useState<LoginStatus|null>(null),[publishSmsCode,setPublishSmsCode]=useState(''),[publishSmsError,setPublishSmsError]=useState(''),[publishSmsBusy,setPublishSmsBusy]=useState(false),[publishMonitorRetry,setPublishMonitorRetry]=useState(0);
 const publishMonitor=useRef<AbortController|null>(null),publishSmsController=useRef<AbortController|null>(null),[pausedPublicationId,setPausedPublicationId]=useState(''),[recoveryRetry,setRecoveryRetry]=useState(0);
 const recoveredPublication=matchingActivePublication(history.data||[],selectedPlatform,task.job,session.role);
 const activePublication=task.busy&&task.job?.kind==='platform_publish'&&task.job.status==='running'?task.job:recoveredPublication?.status==='running'&&pausedPublicationId!==recoveredPublication.id?recoveredPublication:null;
 const publishTaskActive=Boolean(activePublication&&selectedPlatform==='douyin'),publishTaskId=publishTaskActive?activePublication?.id:'';
 const mounted=useRef(true),scope=useRef(project.id),generation=useRef(0),loginController=useRef<AbortController|null>(null),actions=useRef(new Set<AbortController>()),requestKey=useRef('');
 const selected=accounts.data?.find(account=>account.platform===selectedPlatform),eligible=eligiblePlatformContents(contents.data||[],selected),content=eligible.find(row=>row.id===contentId),monitoringBusy=submitting||task.busy,workBusy=monitoringBusy||Boolean(recoveredPublication);
 const historicalPrepared=matchingPreparedJob(history.data||[],selected,content);const shownPrepared=prepared&&content&&prepared.platform===selectedPlatform&&prepared.contentId===content.id&&prepared.releaseId===(content.approved_release_id||content.release_id||'')?prepared:historicalPrepared&&content?{job:historicalPrepared,contentId:content.id,platform:selectedPlatform,releaseId:content.approved_release_id||content.release_id||''}:null;
 const closeLogin=useCallback(()=>{generation.current++;loginController.current?.abort();loginController.current=null;setLogin(null);setBusy('');setSmsCode('');setSmsError('');setSmsBusy(false);},[]);
 useEffect(()=>{mounted.current=true;return()=>{mounted.current=false;generation.current++;loginController.current?.abort();publishMonitor.current?.abort();publishSmsController.current?.abort();actions.current.forEach(controller=>controller.abort());};},[]);
 useEffect(()=>{if(scope.current===project.id)return;scope.current=project.id;closeLogin();actions.current.forEach(controller=>controller.abort());task.cancel();setError('');setNotice('');setSelectedPlatform('');setContentId('');setInstructions('');setPrepared(null);setConfirmed(false);setPublication(null);setSubmitting(false);setPausedPublicationId('');},[project.id,closeLogin]);
 useEffect(()=>{if(!selectedPlatform&&accounts.data?.length)setSelectedPlatform(accounts.data.find(account=>account.name===project.channel)?.platform||accounts.data[0].platform);},[accounts.data,project.channel,selectedPlatform]);
 useEffect(()=>{setConfirmed(false);requestKey.current='';setPublication(null);},[selectedPlatform,contentId,shownPrepared?.job.id,selected?.session_id,selected?.verified,selected?.loggedIn]);
 useEffect(()=>{
  if(!recoveredPublication||task.busy||submitting||pausedPublicationId===recoveredPublication.id)return;
  const pid=project.id,jid=recoveredPublication.id;
  setNotice('已恢复本项目未完成的平台任务，正在读取处理状态。');
  void task.wait(jid).then(completed=>{if(mounted.current&&scope.current===pid){if(completed){setPublication(completed);accounts.retry();}history.retry();}});
 },[project.id,selectedPlatform,recoveredPublication?.id,pausedPublicationId,recoveryRetry]);

 useEffect(()=>{
  setPublishSmsCode('');setPublishSmsError('');setPublishChallenge(null);setPublishSmsBusy(false);
  if(!publishTaskActive)return;const controller=new AbortController(),pid=project.id;publishMonitor.current=controller;
  void watchPlatformPublication(base,'douyin',{signal:controller.signal,onStatus:status=>{if(mounted.current&&scope.current===pid&&!controller.signal.aborted)setPublishChallenge(status);}}).catch(error=>{if(mounted.current&&scope.current===pid&&!controller.signal.aborted)setPublishSmsError((error as Error).message);});
  return()=>{controller.abort();publishSmsController.current?.abort();if(publishMonitor.current===controller)publishMonitor.current=null;};
 },[base,project.id,publishTaskActive,publishTaskId,publishMonitorRetry]);

 useEffect(()=>{
  setQrSource('');setQrError('');if(!login||login.status.state!=='qr_ready'||!(login.status.qrReady||login.status.qr))return;
  const controller=new AbortController();let active=true,objectUrl='';
  fetch(`${base}/${encodeURIComponent(login.account.platform)}/qr?v=${encodeURIComponent(String(login.status.qrTs||generation.current))}`,{credentials:'same-origin',signal:controller.signal,cache:'no-store'})
   .then(async response=>{if(!response.ok){if(response.status===401)window.dispatchEvent(new Event('housignal-session-expired'));const details=await response.json().catch(()=>null);throw new Error(typeof details?.detail==='string'?details.detail:'二维码暂时不可用，请重新获取。');}const blob=await response.blob();if(!blob.type.startsWith('image/'))throw new Error('平台未返回有效二维码，请重试。');if(active){objectUrl=URL.createObjectURL(blob);setQrSource(objectUrl);}})
   .catch(error=>{if(active&&!controller.signal.aborted)setQrError((error as Error).message);});
  return()=>{active=false;controller.abort();if(objectUrl)URL.revokeObjectURL(objectUrl);};
 },[base,login?.account.platform,login?.status.qrTs,login?.status.state,login?.status.qrReady,login?.status.qr]);
 async function startLogin(account:PlatformAccount){
  if(!editable||!account.supported||busy.endsWith(':logout'))return;closeLogin();const run=++generation.current,pid=project.id,controller=new AbortController();loginController.current=controller;setError('');setBusy(`${account.platform}:login`);setLogin({account,status:{state:'starting',message:'正在打开平台登录页面…'}});
  const current=()=>mounted.current&&scope.current===pid&&generation.current===run&&!controller.signal.aborted;
  try{
   const status=await requestPlatformAction(base,account.platform,'login',undefined,controller.signal);if(!current())return;setLogin({account,status});setBusy('');
   if(!terminalStates.includes(status.state))await watchPlatformLogin(base,account.platform,{signal:controller.signal,onStatus:value=>{if(current())setLogin({account,status:value});}});
   if(current())accounts.retry();
  }catch(error){if(current()){setLogin({account,status:{state:'error',message:(error as Error).message}});setError((error as Error).message);}}
  finally{if(current())setBusy('');}
 }
 async function accountAction(account:PlatformAccount,action:'verify'|'logout'){
  if(!editable||busy)return;const controller=new AbortController(),pid=project.id;actions.current.add(controller);setBusy(`${account.platform}:${action}`);setError('');setNotice('');setConfirmed(false);requestKey.current='';
  try{const status=await requestPlatformAction(base,account.platform,action,undefined,controller.signal);if(!mounted.current||scope.current!==pid||controller.signal.aborted)return;setNotice(action==='logout'?`${account.name}已退出本项目登录。`:status.loggedIn&&status.verified?`${account.name}账号已校验。`:status.message||`${account.name}会话需要重新登录。`);accounts.retry();}
  catch(error){if(mounted.current&&scope.current===pid&&!controller.signal.aborted)setError((error as Error).message);}
  finally{actions.current.delete(controller);if(mounted.current&&scope.current===pid&&!controller.signal.aborted)setBusy('');}
 }
 async function endLogin(){
  if(!editable||!login||busy.endsWith(':logout'))return;const account=login.account,pid=project.id,controller=new AbortController();
  const run=++generation.current;loginController.current?.abort();loginController.current=null;actions.current.add(controller);setBusy(`${account.platform}:logout`);setSmsCode('');setSmsBusy(false);setError('');
  const current=()=>mounted.current&&scope.current===pid&&generation.current===run&&!controller.signal.aborted;
  try{await requestPlatformAction(base,account.platform,'logout',undefined,controller.signal);if(current()){closeLogin();setNotice(`${account.name}登录流程已结束。`);accounts.retry();}}
  catch(error){if(current()){setLogin({account,status:{state:'error',message:(error as Error).message}});setError((error as Error).message);}}
  finally{actions.current.delete(controller);if(current())setBusy('');}
 }
 async function submitSms(){
  const code=smsCode.replace(/\D/g,'');if(!login||smsBusy)return;if(code.length<4){setSmsError('请输入手机收到的验证码。');return;}const run=generation.current,controller=loginController.current;if(!controller||controller.signal.aborted)return;setSmsBusy(true);setSmsError('');
  try{await requestPlatformAction(base,login.account.platform,'sms',{code},controller.signal);if(mounted.current&&run===generation.current&&!controller.signal.aborted){setSmsCode('');setLogin(value=>value?{...value,status:{...value.status,state:'verifying',message:'正在验证验证码…'}}:value);}}
  catch(error){if(mounted.current&&run===generation.current&&!controller.signal.aborted)setSmsError((error as Error).message);}
  finally{if(mounted.current&&run===generation.current&&!controller.signal.aborted)setSmsBusy(false);}
 }

 async function submitPublicationSms(){
  if(!editable||!publishTaskActive||publishSmsBusy||publishChallenge?.state!=='sms_required')return;const code=publishSmsCode.replace(/\D/g,'');if(!/^\d{4,8}$/.test(code)){setPublishSmsError('请输入手机收到的 4 至 8 位验证码。');return;}
  const controller=new AbortController(),pid=project.id;publishSmsController.current=controller;actions.current.add(controller);setPublishSmsBusy(true);setPublishSmsError('');
  try{await requestPlatformAction(base,'douyin','publish-sms',{code},controller.signal);if(mounted.current&&scope.current===pid&&!controller.signal.aborted){setPublishSmsCode('');setPublishChallenge({state:'verifying',message:'正在核对发布验证码…'});}}
  catch(error){if(mounted.current&&scope.current===pid&&!controller.signal.aborted)setPublishSmsError((error as Error).message);}
  finally{actions.current.delete(controller);if(publishSmsController.current===controller)publishSmsController.current=null;if(mounted.current&&scope.current===pid&&!controller.signal.aborted)setPublishSmsBusy(false);}
 }
 async function runWork(action:'prepare'|'publish'){
  if(!editable||!selected||!content||workBusy||!preparedSessionMatches(selected,selected.session_id))return;if(action==='publish'&&(!confirmed||shownPrepared?.contentId!==content.id||shownPrepared.platform!==selected.platform||shownPrepared.releaseId!==(content.approved_release_id||content.release_id||'')||!publicationPreview(shownPrepared.job.result?.publication_preview)||!preparedSessionMatches(selected,shownPrepared.job.result?.session_id)))return;
  const controller=new AbortController(),pid=project.id,cid=content.id,platform=selected.platform,releaseId=content.approved_release_id||content.release_id||'';actions.current.add(controller);setSubmitting(true);setError('');setNotice('');task.setError('');
  if(action==='prepare'){setPrepared(null);setConfirmed(false);setPublication(null);}else if(!requestKey.current)requestKey.current=crypto.randomUUID();
  const current=()=>mounted.current&&scope.current===pid&&!controller.signal.aborted;
  try{
   const submitted=action==='publish'?await requestConfirmedPlatformPublish(base,selected,content,shownPrepared!.job,requestKey.current,controller.signal):await requestPlatformAction<{job_id:string}>(base,platform,'prepare',{content_version_id:cid},controller.signal);if(!current())return;if(!submitted.job_id)throw new Error('平台任务未返回编号，请重试');setSubmitting(false);history.retry();
   const completed=await task.wait(submitted.job_id);if(!current()||!completed)return;
   if(action==='prepare')setPrepared({job:completed,contentId:cid,platform,releaseId});else{setPublication(completed);setConfirmed(false);accounts.retry();}history.retry();
  }catch(error){if(current())setError((error as Error).message);}
  finally{actions.current.delete(controller);if(current())setSubmitting(false);}
 }
 function stopWork(){setPausedPublicationId(task.job?.kind==='platform_publish'?task.job.id:recoveredPublication?.id||'');publishMonitor.current?.abort();publishSmsController.current?.abort();actions.current.forEach(controller=>controller.abort());task.cancel();setSubmitting(false);setNotice('已停止接收任务结果。已提交的任务可能继续执行，请先核对下方项目任务记录。');}
 function agentPrompt(){return platformOperationsPrompt(selected,content,instructions);}
 async function copyPrompt(){try{await navigator.clipboard.writeText(agentPrompt());notify('楼管家操作指令已复制。');}catch{setError('无法访问剪贴板，请选中指令手动复制。');}}
 function handoff(){try{if(!stageAgentPrompt(sessionStorage,project.id,agentPrompt()))throw new Error('未能保存楼管家指令，请复制后到对话页粘贴。');navigate('assistant');}catch(error){setError((error as Error).message);}}
 return <section className="platform-accounts" aria-label="本项目平台账号登录"><div className="platform-login-intro"><div><p>从平台官方页面扫码或完成登录。登录状态在本项目独立保存，校验有效后楼管家可接入运营。</p><p className="platform-network-note">平台可能要求短信验证、手机确认或浏览器安全验证，请在对应平台界面完成。</p></div><Button icon="refresh" disabled={accounts.loading||Boolean(busy)} onClick={()=>{accounts.retry();history.retry();contents.retry();}}>刷新</Button></div>
  {accounts.loading?<Loading text="正在读取本项目平台账号…"/>:accounts.error?<ErrorNotice message={accounts.error} retry={accounts.retry}/>:<PlatformAccountsGrid accounts={accounts.data||[]} editable={editable} busy={busy} onLogin={account=>void startLogin(account)} onVerify={account=>void accountAction(account,'verify')} onLogout={account=>void accountAction(account,'logout')}/>}
  {!editable&&<p className="platform-agent-note"><Icon name="lock" size={13}/>当前角色可查看。平台登录和操作需要项目管理员或内容负责人。</p>}{error&&<ErrorNotice message={error}/>}<p className="platform-account-notice" role="status">{notice}</p>
  <div className="platform-agent-panel"><div className="platform-agent-heading"><span className="platform-agent-icon"><Icon name="sparkle" size={22}/></span><div><h3>交给楼管家操盘</h3><p>校验登录、选择已审核内容，再准备发布包与平台操作。</p></div></div><div className="platform-agent-fields"><Field label="操作平台"><select value={selectedPlatform} disabled={submitting||accounts.loading} onChange={event=>{task.cancel();publishMonitor.current?.abort();publishSmsController.current?.abort();setSelectedPlatform(event.target.value);setContentId('');setPrepared(null);}}><option value="">选择平台</option>{accounts.data?.map(account=><option key={account.platform} value={account.platform}>{account.name}{account.loggedIn?'':' · 未登录'}</option>)}</select></Field><Field label="已审核内容版本"><select value={contentId} disabled={workBusy||contents.loading||!selected} onChange={event=>setContentId(event.target.value)}><option value="">{contents.loading?'正在读取内容…':eligible.length?'选择内容版本':'暂无有效审核成片'}</option>{eligible.map(row=><option key={row.id} value={row.id}>{row.cover_title||row.topic} · v{row.version}</option>)}</select></Field></div>{contents.error&&<ErrorNotice message={contents.error} retry={contents.retry}/>}
   {selected&&<PlatformAnalytics key={`${project.id}:${selected.platform}`} account={selected} editable={editable}/>}
   {selectedPlatform==='wechat-oa'&&<PlatformCredentials key={`${project.id}:${selected?.session_id||'none'}`} base={base} editable={editable} onSaved={accounts.retry}/>}
   <Field label="楼管家操作指令" hint="可以补充你的目标；账号验证码和平台确认由你完成。"><textarea rows={3} maxLength={1500} value={instructions} disabled={workBusy} onChange={event=>setInstructions(event.target.value)} placeholder={agentPrompt()}/></Field><div className="platform-agent-controls"><Button primary icon="sparkle" onClick={handoff} disabled={!editable||workBusy||!selected}>交给楼管家</Button><Button icon="copy" onClick={()=>void copyPrompt()} disabled={!selected}>复制指令</Button><Button icon="folder" onClick={()=>void runWork('prepare')} disabled={!editable||workBusy||!content||!preparedSessionMatches(selected,selected?.session_id)}>准备发布包</Button></div>
   {selected&&!selected.loggedIn&&<p className="platform-agent-note">请先登录 {selected.name}，再准备平台发布任务。</p>}{selected?.loggedIn&&!selected.verified&&<p className="platform-agent-note">请先点击「校验账号」，确认当前会话有效。</p>}
   {monitoringBusy&&<div className="platform-task-progress" role="status"><span className="spinner"/><span>{submitting?'正在提交平台任务…':task.statusText}</span>{task.job&&<progress max="100" value={task.percent} aria-label="平台任务进度"/>}<Button onClick={stopWork}>停止接收结果</Button></div>}{task.error&&<ErrorNotice message={task.error}/>}
   {recoveredPublication&&!monitoringBusy&&<div className="platform-task-progress" role="status"><span>本项目有未完成的平台提交任务：{recoveredPublication.id}。可恢复状态与手机验证监控。</span><Button onClick={()=>{setPausedPublicationId('');setRecoveryRetry(value=>value+1);}}>恢复任务监控</Button></div>}
   {publishTaskActive&&<><PlatformPublicationSms status={publishChallenge} code={publishSmsCode} error={publishSmsError} busy={publishSmsBusy} onCode={setPublishSmsCode} onSubmit={()=>void submitPublicationSms()}/>{publishSmsError&&publishChallenge?.state!=='sms_required'&&<ErrorNotice message={publishSmsError} retry={()=>setPublishMonitorRetry(value=>value+1)}/>}</>}
   {shownPrepared&&<><PlatformPreparePanel job={shownPrepared.job}/><PlatformPublishConfirmation content={content!} preparedContentId={shownPrepared.contentId} preparedSessionId={shownPrepared.job.result?.session_id} preview={shownPrepared.job.result?.publication_preview} account={selected} editable={editable&&Boolean(selected?.loggedIn&&selected.verified)} confirmed={confirmed} onConfirmed={setConfirmed} busy={workBusy} onPublish={()=>void runWork('publish')}/></>}{publication&&<PublicationResult job={publication}/>}
   {history.data?.length?<details className="platform-task-history"><summary>本项目平台任务记录（{history.data.length}）</summary><div>{history.data.map(job=><article key={job.id}><div><strong>{job.kind==='platform_publish'?'平台提交':'发布准备'}</strong><Badge tone={job.status==='succeeded'?'green':job.status==='failed'?'amber':'neutral'}>{({succeeded:'已完成',failed:'失败',queued:'排队中',running:'处理中',cancelled:'已取消'} as Record<string,string>)[job.status]||job.status}</Badge></div><small>{job.id}</small>{job.error&&<p>{job.error}</p>}{job.status==='succeeded'&&(job.kind==='platform_publish'?<PublicationResult job={job}/>:<PlatformPreparePanel job={job}/>)}</article>)}</div></details>:null}
  </div>
  {login&&<Modal title={`登录 ${login.account.name}`} onClose={closeLogin} footer={<Button onClick={closeLogin}>{login.status.state==='success'?'完成':'关闭面板'}</Button>}><PlatformLoginPanel account={login.account} status={login.status} qrSource={qrSource} qrError={qrError} smsCode={smsCode} smsError={smsError} smsBusy={smsBusy} onSmsCode={setSmsCode} onSms={()=>void submitSms()} onRetry={()=>void startLogin(login.account)} onCancel={()=>void endLogin()} cancelBusy={busy.endsWith(':logout')}/>{!terminalStates.includes(login.status.state)&&<p className="platform-agent-note">关闭面板后平台登录窗口可能继续等待。要停止本次流程，请点击「结束登录」。</p>}</Modal>}
 </section>;
}
