import {useEffect,useRef,useState} from 'react';
import {api} from './api.js';
import {useApp} from './hooks';
import {Badge,Button,ErrorNotice} from './components';
import {DouyinResearchAuth} from './DouyinResearchAuth';

export type SearchAccountScope='operations'|'research';
export interface OperationalSearchStatus {state:string;loggedIn:boolean;verified:boolean;name?:string;message?:string}
type AccountAction='status'|'verify';
export async function requestOperationalSearchAccount(projectId:string,action:AccountAction,signal?:AbortSignal):Promise<OperationalSearchStatus> {
 if(!['status','verify'].includes(action))throw new Error('此入口只读取或核对本项目已有的抖音运营登录。');
 signal?.throwIfAborted();
 const status=await api(`/api/projects/${encodeURIComponent(projectId)}/operations/platform-accounts/douyin/${action}`,{method:action==='status'?'GET':'POST',signal}) as OperationalSearchStatus;
 signal?.throwIfAborted();
 if(!status||typeof status.state!=='string'||typeof status.loggedIn!=='boolean'||typeof status.verified!=='boolean')throw new Error('未取得本项目抖音运营账号状态，请刷新后重试。');
 return status;
}
export async function updateOperationalSearchAccount(projectId:string,action:AccountAction,{signal,onStatus}:{signal:AbortSignal;onStatus:(status:OperationalSearchStatus)=>void}) {
 const status=await requestOperationalSearchAccount(projectId,action,signal);signal.throwIfAborted();onStatus(status);return status;
}
interface ViewProps {status:OperationalSearchStatus|null;projectName:string;loading:boolean;busy:boolean;error:string;editable:boolean;onVerify:()=>void;onRefresh:()=>void;onOpenAccounts:()=>void}
export function OperationalSearchAccountView({status,projectName,loading,busy,error,editable,onVerify,onRefresh,onOpenAccounts}:ViewProps) {
 const verified=status?.loggedIn===true&&status.verified===true;
 const label=loading?'正在读取运营账号状态':verified?'已登录并核对':status?.loggedIn?'已登录，待核对':status?.state==='challenge'?'需要官方验证':'登录状态待确认';
 return <section className="search-account-connection" aria-label="当前搜索抖音账号">
  <div className="search-account-heading"><h4>当前搜索使用：本项目运营账号</h4><Badge tone={verified?'green':'amber'}>{label}</Badge></div>
  <p>{projectName||'当前项目'} · 与账号页使用同一项目的抖音会话。已有登录可直接核对。</p>
  {status?.name&&<p>平台账号：<strong>{status.name}</strong></p>}
  {status?.message&&<p role="status">{status.message}</p>}
  {!loading&&!status?.loggedIn&&<p>请先核对已有登录；当前项目尚未登录时，前往账号页完成登录后返回搜索。</p>}
  {error&&<ErrorNotice message={error} retry={onRefresh}/>}
  <div className="search-account-actions"><Button icon="check" disabled={!editable||busy||loading} busy={busy} onClick={onVerify}>核对已有登录</Button><Button icon="refresh" disabled={busy||loading} onClick={onRefresh}>刷新账号状态</Button><Button onClick={onOpenAccounts}>前往账号页</Button></div>
  {!editable&&<p>当前角色可查看账号状态；核对登录需要项目管理员或内容负责人。</p>}
 </section>;
}
function OperationalSearchAccount({projectId,projectName,editable,onOpenAccounts}:{projectId:string;projectName:string;editable:boolean;onOpenAccounts:()=>void}) {
 const [status,setStatus]=useState<OperationalSearchStatus|null>(null),[loading,setLoading]=useState(true),[busy,setBusy]=useState(false),[error,setError]=useState('');
 const mounted=useRef(true),sequence=useRef(0),pending=useRef<AbortController|null>(null);
 useEffect(()=>{mounted.current=true;void action('status');return()=>{mounted.current=false;sequence.current++;pending.current?.abort();};},[projectId]);
 async function action(kind:AccountAction) {
  if(kind==='verify'&&!editable)return;
  const run=++sequence.current;pending.current?.abort();const controller=new AbortController();pending.current=controller;
  const current=()=>mounted.current&&run===sequence.current&&!controller.signal.aborted;
  setError('');setBusy(kind==='verify');setLoading(kind==='status');
  try{await updateOperationalSearchAccount(projectId,kind,{signal:controller.signal,onStatus:value=>{if(current())setStatus(value);}});}
  catch(cause){if(current())setError((cause as Error).message);}
  finally{if(current()){setBusy(false);setLoading(false);pending.current=null;}}
 }
 return <OperationalSearchAccountView status={status} projectName={projectName} loading={loading} busy={busy} error={error} editable={editable} onVerify={()=>void action('verify')} onRefresh={()=>void action('status')} onOpenAccounts={onOpenAccounts}/>;
}
export function SearchAccountConnection({scope,editable}:{scope:SearchAccountScope;editable:boolean}) {
 const {project,navigate}=useApp();
 return scope==='research'?<section key={`${project.id}:research`} aria-label="当前搜索抖音账号"><p className="form-note">当前搜索使用：独立研究账号。此方向按单独的研究会话查询；切换到「项目运营账号」可使用账号页已有登录。</p><DouyinResearchAuth editable={editable}/></section>:<OperationalSearchAccount key={`${project.id}:operations`} projectId={project.id} projectName={project.name} editable={editable} onOpenAccounts={()=>navigate('accounts')}/>;
}
