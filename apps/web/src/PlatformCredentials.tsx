import {useEffect,useRef,useState} from 'react';
import type {FormEvent,RefObject} from 'react';
import {api} from './api.js';
import {Badge,Button,ErrorNotice,Field} from './components';
import './platform-credentials.css';

export interface PlatformCredentialMetadata {
 configured:boolean;verified:boolean;mode:'official_api';appIdMasked:string;name:string;author:string;
 verified_at:string|null;verification_expires_at:string|null;code:number|null;message:string;capabilities:'not_checked';
}
interface CredentialInput {appId:string;appSecret:string;name?:string;author?:string}
type CredentialAction='read'|'save'|'clear';
export async function requestPlatformCredentials(base:string,action:CredentialAction,body?:CredentialInput,signal?:AbortSignal):Promise<PlatformCredentialMetadata>{
 if(!/^\/api\/projects\/[^/?#]+\/operations\/platform-accounts$/.test(base)||!['read','save','clear'].includes(action))throw new Error('公众号凭证操作地址无效。');
 signal?.throwIfAborted();
 const result=await api(`${base}/wechat-oa/credentials`,{method:action==='read'?'GET':action==='save'?'POST':'DELETE',...(action==='save'?{body}:{}),signal});
 signal?.throwIfAborted();
 if(!result||result.mode!=='official_api'||typeof result.configured!=='boolean'||typeof result.verified!=='boolean')throw new Error('公众号 API 凭证状态暂不可用。');
 return result;
}
interface CredentialViewProps {
 metadata:PlatformCredentialMetadata|null;error:string;busy:boolean;editable:boolean;formRef:RefObject<HTMLFormElement|null>;
 onSubmit:(event:FormEvent<HTMLFormElement>)=>void;onClear:()=>void;onRefresh:()=>void;
}
export function PlatformCredentialsView({metadata,error,busy,editable,formRef,onSubmit,onClear,onRefresh}:CredentialViewProps){
 return <section className="platform-credentials" aria-label="公众号 API 凭证">
  <div className="platform-credentials-heading"><div><h4>公众号 API 凭证</h4><p>填写公众号后台的 AppID 与 AppSecret，验证官方 API 接入。浏览器登录与此配置独立，具体接口权限还需在运营任务中核对。</p></div><Badge tone={metadata?.verified?'success':'neutral'}>{metadata?.verified?'官方 API 已核对':metadata?.configured?'已配置 · 未通过核对':'尚未配置'}</Badge></div>
  {metadata?.configured&&<div className="platform-credentials-metadata"><span>AppID：{metadata.appIdMasked}</span>{metadata.name&&<span>名称：{metadata.name}</span>}{metadata.author&&<span>作者：{metadata.author}</span>}{metadata.verified_at&&<span>上次核对：{new Date(metadata.verified_at).toLocaleString('zh-CN')}</span>}</div>}
  {metadata?.message&&<p className="platform-credentials-status" role="status">{metadata.message}{metadata.code!==null&&metadata.code!==0?`（错误码 ${metadata.code}）`:''}</p>}
  {error&&<ErrorNotice message={error}/>}
  <form ref={formRef} key={`${metadata?.appIdMasked||''}:${metadata?.verified_at||''}`} autoComplete="off" onSubmit={onSubmit}>
   <div className="platform-credentials-fields"><Field label="公众号 AppID" hint="在公众号后台的开发者信息中查看。"><input name="appId" aria-label="公众号 AppID" placeholder="wx…" required maxLength={18} pattern="wx[A-Za-z0-9]{16}" autoComplete="off" disabled={!editable||busy}/></Field>
    <Field label="公众号 AppSecret" hint="仅提交到本项目凭证接口；保存后不回显，不进入楼管家对话。"><input name="appSecret" aria-label="公众号 AppSecret" type="password" required maxLength={32} minLength={32} autoComplete="new-password" data-1p-ignore="true" data-lpignore="true" disabled={!editable||busy}/></Field>
    <Field label="公众号名称"><input name="name" aria-label="公众号名称" defaultValue={metadata?.name||''} maxLength={80} disabled={!editable||busy}/></Field><Field label="默认作者"><input name="author" aria-label="默认作者" defaultValue={metadata?.author||''} maxLength={80} disabled={!editable||busy}/></Field></div>
   <div className="platform-credentials-actions"><Button type="submit" primary disabled={!editable||busy}>保存并验证</Button><Button type="button" disabled={busy} onClick={onRefresh}>刷新凭证状态</Button><Button type="button" disabled={!editable||busy||!metadata?.configured} onClick={onClear}>清除 API 凭证</Button></div>
  </form>
 </section>;
}
export function PlatformCredentials({base,editable,onSaved}:{base:string;editable:boolean;onSaved?:()=>void}){
 const [metadata,setMetadata]=useState<PlatformCredentialMetadata|null>(null),[error,setError]=useState(''),[busy,setBusy]=useState(false);
 const formRef=useRef<HTMLFormElement|null>(null),scope=useRef(base),generation=useRef(0),controller=useRef<AbortController|null>(null),mounted=useRef(false);
 const savedCallback=useRef(onSaved);savedCallback.current=onSaved;
 function clearSecret(){const input=formRef.current?.elements.namedItem('appSecret');if(input instanceof HTMLInputElement)input.value='';}
 async function request(action:CredentialAction,body?:CredentialInput){
  if(action!=='read'&&!editable)return;
  controller.current?.abort();const current=new AbortController();controller.current=current;const mark=++generation.current,target=base;
  setBusy(true);setError('');
  try{const value=await requestPlatformCredentials(target,action,body,current.signal);if(!mounted.current||current.signal.aborted||scope.current!==target||generation.current!==mark)return;setMetadata(value);if(action!=='read')savedCallback.current?.();}
  catch(value){if(mounted.current&&!current.signal.aborted&&scope.current===target&&generation.current===mark)setError((value as Error).message||'公众号 API 凭证操作未完成。');}
  finally{if(body)body.appSecret='';if(mounted.current&&scope.current===target&&generation.current===mark)setBusy(false);}
 }
 useEffect(()=>{
  mounted.current=true;scope.current=base;setMetadata(null);setError('');clearSecret();void request('read');
  return()=>{mounted.current=false;generation.current++;controller.current?.abort();clearSecret();};
 },[base]);
 function submit(event:FormEvent<HTMLFormElement>){
  event.preventDefault();if(!editable||busy)return;const values=new FormData(event.currentTarget);
  const body={appId:String(values.get('appId')||'').trim(),appSecret:String(values.get('appSecret')||'').trim(),name:String(values.get('name')||'').trim(),author:String(values.get('author')||'').trim()};
  clearSecret();void request('save',body);
 }
 return <PlatformCredentialsView metadata={scope.current===base?metadata:null} error={error} busy={busy} editable={editable} formRef={formRef} onSubmit={submit} onRefresh={()=>void request('read')} onClear={()=>{clearSecret();void request('clear');}}/>;
}
