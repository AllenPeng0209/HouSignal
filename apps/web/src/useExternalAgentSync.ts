import {useEffect,useRef} from 'react';
import {api,ApiError} from './api.js';

type AuthorizedProject={id:string};
export function selectAuthorizedProject(projects:AuthorizedProject[],current:string,search:string):string {
 const requested=new URLSearchParams(search).get('project_id');
 if(requested&&projects.some(project=>project.id===requested))return requested;
 return projects.some(project=>project.id===current)?current:projects[0]?.id||'';
}

/** Owns one project's polling lifecycle; cleanup also discards late responses. */
export function startExternalAgentSync(projectId:string,onChange:()=>Promise<void>|void):()=>void {
 let stopped=false,halted=false,baseline:string|undefined,failures=0;
 let timer:ReturnType<typeof setTimeout>|undefined,request:AbortController|null=null;
 const visible=()=>document.visibilityState==='visible';
 const clearTimer=()=>{if(timer!==undefined){clearTimeout(timer);timer=undefined;}};
 const check=async()=>{
  if(stopped||halted||!visible()||request)return;
  clearTimer();const current=new AbortController();request=current;let wait=3000;
  try{
   const result=await api(`/api/projects/${encodeURIComponent(projectId)}/agent-changes`,{signal:current.signal}) as {revision?:unknown};
   if(stopped||current.signal.aborted)return;
   if(typeof result?.revision!=='string')throw new Error('项目更新状态暂不可用');
   if(baseline===undefined)baseline=result.revision;
   else if(result.revision!==baseline){await onChange();if(!stopped&&!current.signal.aborted)baseline=result.revision;}
   failures=0;
  }catch(error){
   if(stopped||current.signal.aborted)return;
   if(error instanceof ApiError&&[401,403,404].includes(error.status)){halted=true;return;}
   failures++;wait=Math.min(30000,3000*2**Math.min(failures,4));
  }finally{
   if(request===current){request=null;if(!stopped&&!halted&&visible())timer=setTimeout(()=>void check(),wait);}
  }
 };
 const visibilityChanged=()=>{
  clearTimer();
  if(!visible()){request?.abort();request=null;return;}
  void check();
 };
 const focused=()=>{if(visible())void check();};
 document.addEventListener('visibilitychange',visibilityChanged);
 window.addEventListener('focus',focused);
 void check();
 return()=>{stopped=true;clearTimer();request?.abort();request=null;document.removeEventListener('visibilitychange',visibilityChanged);window.removeEventListener('focus',focused);};
}

export function useExternalAgentSync({projectId,identityKey,onChange}:{projectId:string|null;identityKey:string;onChange:()=>Promise<void>}):void {
 const latestOnChange=useRef(onChange);latestOnChange.current=onChange;
 useEffect(()=>{
  if(!projectId||!identityKey)return;
  return startExternalAgentSync(projectId,()=>latestOnChange.current());
 },[projectId,identityKey]);
}
