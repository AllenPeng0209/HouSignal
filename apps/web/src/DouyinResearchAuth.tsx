import {useEffect,useRef,useState} from 'react';
import {api,ApiError} from './api.js';
import {useApp} from './hooks';
import {Badge,Button,ErrorNotice,Icon} from './components';
import './douyin-research-auth.css';

export interface DouyinResearchStatus {
 state:'unknown'|'starting'|'qr_ready'|'window_login'|'success'|'challenge'|'expired'|'error'|'busy'|'not_configured';
 message?:string;qrReady?:boolean;qrTs?:string|number;loggedIn?:boolean;verified?:boolean;ready?:boolean;scope:'research';
}
type ResearchAction='status'|'login'|'verify'|'disconnect';
const connectingStates=new Set(['starting','qr_ready','window_login','challenge']);
const stateLabels:Record<DouyinResearchStatus['state'],string>={unknown:'尚未连接',starting:'正在打开登录',qr_ready:'等待手机扫码',window_login:'在官方窗口登录',success:'登录待核对',challenge:'需要人工验证',expired:'登录已过期',error:'连接未完成',busy:'研究账号正在使用',not_configured:'运行环境待配置'};
function accountPath(projectId:string){return `/api/projects/${encodeURIComponent(projectId)}/operations/discovery/douyin-account`;}
function connected(status:DouyinResearchStatus|null){return status?.scope==='research'&&status.state==='success'&&status.loggedIn===true&&status.verified===true&&status.ready!==false;}

export async function requestDouyinResearchAccount(projectId:string,action:ResearchAction,signal?:AbortSignal):Promise<DouyinResearchStatus>{
 if(!['status','login','verify','disconnect'].includes(action))throw new Error('此入口只接受抖音研究账号操作。');
 const status=await api(`${accountPath(projectId)}${action==='status'?'':`/${action}`}`,{method:action==='status'?'GET':'POST',signal}) as DouyinResearchStatus;
 if(!status||status.scope!=='research'||!Object.hasOwn(stateLabels,status.state))throw new Error('未取得本项目抖音研究账号状态，请重试。');
 return status;
}
export async function updateDouyinResearchAccount(projectId:string,action:ResearchAction,{signal,onStatus}:{signal:AbortSignal;onStatus:(status:DouyinResearchStatus)=>void}):Promise<DouyinResearchStatus>{
 signal.throwIfAborted();
 if(action==='login')onStatus({state:'starting',message:'正在打开抖音官方登录页面…',scope:'research'});
 const status=await requestDouyinResearchAccount(projectId,action,signal);signal.throwIfAborted();onStatus(status);return status;
}

export async function requestDouyinResearchQr(projectId:string,version:string|number,signal?:AbortSignal):Promise<Blob>{
 const response=await fetch(`${accountPath(projectId)}/qr?v=${encodeURIComponent(String(version))}`,{credentials:'same-origin',cache:'no-store',signal});
 if(!response.ok){
  if(response.status===401&&typeof window!=='undefined')window.dispatchEvent(new Event('housignal-session-expired'));
  const details=await response.json().catch(()=>null);
  throw new ApiError(typeof details?.detail==='string'?details.detail:'二维码暂时不可用，请重试。',response.status);
 }
 const blob=await response.blob();
 if(blob.type.split(';')[0]!=='image/png'||blob.size>2*1024*1024)throw new Error('未取得有效的官方登录二维码，请重试。');
 const header=new Uint8Array(await blob.slice(0,8).arrayBuffer());
 if(header.length!==8||![137,80,78,71,13,10,26,10].every((value,index)=>header[index]===value))throw new Error('未取得有效的官方登录二维码，请重试。');
 signal?.throwIfAborted();return blob;
}
function delay(ms:number,signal:AbortSignal){return new Promise<void>((resolve,reject)=>{
 signal.throwIfAborted();const timer=setTimeout(()=>{signal.removeEventListener('abort',abort);resolve();},ms);
 function abort(){clearTimeout(timer);signal.removeEventListener('abort',abort);reject(signal.reason||new DOMException('研究登录查询已结束','AbortError'));}signal.addEventListener('abort',abort,{once:true});
});}
export async function watchDouyinResearchLogin(projectId:string,{signal,onStatus,pollMs=2000,maxMs=300000}:{signal:AbortSignal;onStatus:(status:DouyinResearchStatus)=>void;pollMs?:number;maxMs?:number}):Promise<DouyinResearchStatus>{
 const started=Date.now();
 while(Date.now()-started<maxMs){
  signal.throwIfAborted();const status=await requestDouyinResearchAccount(projectId,'status',signal);signal.throwIfAborted();onStatus(status);signal.throwIfAborted();
  if(!connectingStates.has(status.state))return status;
  await delay(pollMs,signal);
 }
 const status:DouyinResearchStatus={state:'expired',message:'登录等待已超时，请重新连接或核对官方窗口状态。',scope:'research',qrReady:false,loggedIn:false,verified:false};onStatus(status);return status;
}

interface ViewProps {status:DouyinResearchStatus|null;qrSource:string;error:string;qrError:string;busy:boolean;editable:boolean;onLogin:()=>void;onVerify:()=>void;onDisconnect:()=>void;onRefresh:()=>void}
export function DouyinResearchAuthView({status,qrSource,error,qrError,busy,editable,onLogin,onVerify,onDisconnect,onRefresh}:ViewProps){
 const verified=connected(status),showQr=status?.state==='qr_ready'&&status.qrReady===true,officialWindow=['window_login','challenge'].includes(status?.state||''),retry=['expired','error'].includes(status?.state||'');
 return <div className="douyin-research-body">
  <p className="douyin-research-purpose">登录会话用于本项目住宅内容研究。请用抖音 App 扫码，平台验证在官方页面完成。</p>
  <div className="douyin-research-state" role="status"><Badge tone={verified?'green':'amber'}>{verified?'已连接并核对':status?stateLabels[status.state]:'正在读取账号状态'}</Badge><p>{status?.message||''}</p></div>
  {showQr&&(qrSource?<img className="douyin-research-qr" src={qrSource} alt="抖音研究账号登录二维码"/>:!qrError&&<p className="douyin-research-qr-wait" role="status"><span className="spinner"/>正在读取官方二维码…</p>)}
  {officialWindow&&<p className="douyin-research-window">请在已打开的抖音官方浏览器窗口完成登录或验证，然后点击「核对登录状态」。</p>}
  {status?.state==='success'&&!verified&&<p className="douyin-research-window">平台登录信息待核对，点击「核对登录状态」确认。</p>}
  {(error||qrError)&&<ErrorNotice message={error||qrError} retry={onRefresh}/>}
  <div className="douyin-research-actions">
   {!verified&&<Button primary icon="key" busy={busy} disabled={!editable||status?.ready===false} onClick={onLogin}>{retry?'重新连接':'连接抖音'}</Button>}
   <Button icon="check" disabled={!editable||busy} onClick={onVerify}>核对登录状态</Button>
   <Button icon="refresh" disabled={busy} onClick={onRefresh}>刷新状态</Button>
   {(status?.loggedIn||connectingStates.has(status?.state||''))&&<Button disabled={!editable||busy} onClick={onDisconnect}>{status?.loggedIn?'断开研究账号':'结束本次连接'}</Button>}
  </div>
  {!editable&&<p className="douyin-research-permission">连接或断开研究账号需要项目管理员或内容负责人权限。</p>}
 </div>;
}

function ResearchProjectAuth({projectId,editable}:{projectId:string;editable:boolean}){
 const [expanded,setExpanded]=useState(false),[status,setStatus]=useState<DouyinResearchStatus|null>(null),[error,setError]=useState(''),[busy,setBusy]=useState(false),[qrSource,setQrSource]=useState(''),[qrError,setQrError]=useState(''),[qrRetry,setQrRetry]=useState(0);
 const mounted=useRef(true),generation=useRef(0),controllers=useRef(new Set<AbortController>());
 const current=(controller:AbortController,run:number)=>mounted.current&&run===generation.current&&!controller.signal.aborted;
 useEffect(()=>{mounted.current=true;return()=>{mounted.current=false;generation.current++;controllers.current.forEach(controller=>controller.abort());};},[]);
 useEffect(()=>{
  const controller=new AbortController(),run=generation.current;controllers.current.add(controller);
  void requestDouyinResearchAccount(projectId,'status',controller.signal).then(value=>{if(current(controller,run))setStatus(value);}).catch(error=>{if(current(controller,run))setError((error as Error).message);}).finally(()=>controllers.current.delete(controller));
  return()=>{controller.abort();controllers.current.delete(controller);};
 },[projectId]);
 useEffect(()=>{
  if(!expanded||busy||!status||!connectingStates.has(status.state))return;
  const controller=new AbortController(),run=generation.current;controllers.current.add(controller);
  void watchDouyinResearchLogin(projectId,{signal:controller.signal,onStatus:value=>{if(current(controller,run))setStatus(value);}}).catch(error=>{if(current(controller,run))setError((error as Error).message);}).finally(()=>controllers.current.delete(controller));
  return()=>{controller.abort();controllers.current.delete(controller);};
 },[projectId,expanded,busy,status?.state]);
 useEffect(()=>{
  setQrSource('');setQrError('');if(!expanded||busy||status?.state!=='qr_ready'||!status.qrReady)return;
  const controller=new AbortController(),run=generation.current;let active=true,objectUrl='';controllers.current.add(controller);
  void requestDouyinResearchQr(projectId,status.qrTs??run,controller.signal).then(blob=>{if(active&&current(controller,run)){objectUrl=URL.createObjectURL(blob);setQrSource(objectUrl);}}).catch(error=>{if(active&&current(controller,run))setQrError((error as Error).message);}).finally(()=>controllers.current.delete(controller));
  return()=>{active=false;controller.abort();controllers.current.delete(controller);if(objectUrl)URL.revokeObjectURL(objectUrl);};
 },[projectId,expanded,busy,status?.state,status?.qrReady,status?.qrTs,qrRetry]);
 async function action(action:ResearchAction){
  if(busy||(action!=='status'&&!editable))return;
  const run=++generation.current;controllers.current.forEach(controller=>controller.abort());controllers.current.clear();const controller=new AbortController();controllers.current.add(controller);
  setExpanded(true);setBusy(true);setError('');setQrError('');
  try{await updateDouyinResearchAccount(projectId,action,{signal:controller.signal,onStatus:value=>{if(current(controller,run))setStatus(value);}});if(current(controller,run))setQrRetry(value=>value+1);}
  catch(error){if(current(controller,run))setError((error as Error).message);}
  finally{controllers.current.delete(controller);if(current(controller,run))setBusy(false);}
 }
 const label=connected(status)?'已连接并核对':status?stateLabels[status.state]:'读取状态中';
 return <details className="douyin-research-auth" open={expanded} onToggle={event=>setExpanded(event.currentTarget.open)}>
  <summary><span className="douyin-research-heading"><Icon name="key" size={16}/>抖音研究账号</span><Badge tone={connected(status)?'green':'neutral'}>{label}</Badge><Icon name="chevron" size={15}/></summary>
  <DouyinResearchAuthView status={status} qrSource={qrSource} error={error} qrError={qrError} busy={busy} editable={editable} onLogin={()=>void action('login')} onVerify={()=>void action('verify')} onDisconnect={()=>void action('disconnect')} onRefresh={()=>void action('status')}/>
 </details>;
}
export function DouyinResearchAuth({editable}:{editable:boolean}){
 const {project}=useApp();return <ResearchProjectAuth key={project.id} projectId={project.id} editable={editable}/>;
}
