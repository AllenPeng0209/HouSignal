import {createContext,useCallback,useContext,useEffect,useRef,useState} from 'react';
import {api,ApiError} from './api.js';
import {watchJob} from './job-watch.js';
import {jobProgressPercent,jobStatusText} from './job-status.js';
import type {Job,Page,Project,Session,Citation} from './types';
interface AppContextType {session:Session;project:Project;projects:Project[];refresh:number;reload:()=>Promise<void>;navigate:(page:Page)=>void;notify:(text:string)=>void;citation:(ref:Citation)=>void;canEdit:boolean;canReview:boolean;canSales:boolean;canManage:boolean;openProject:()=>void;openReadiness:()=>void;logout:()=>Promise<void>}
export const AppContext=createContext<AppContextType>(null!);
export const useApp=()=>useContext(AppContext);
export function useResource<T>(path:string|null) {
 const {refresh}=useApp();const [data,setData]=useState<T|null>(null),[error,setError]=useState(''),[loading,setLoading]=useState(true),[retry,setRetry]=useState(0);
 useEffect(()=>{let current=true;const controller=new AbortController();setData(null);setError('');setLoading(true);if(!path){setLoading(false);return;}api(path,{signal:controller.signal}).then((value:T)=>{if(current)setData(value);}).catch((e:Error)=>{if(current&&e.name!=='AbortError')setError(e.message);}).finally(()=>{if(current)setLoading(false);});return()=>{current=false;controller.abort();};},[path,refresh,retry]);
 return {data,error,loading,retry:()=>setRetry(v=>v+1),setData};
}
export function useMutation(){const [busy,setBusy]=useState(false),[error,setError]=useState('');const {notify,reload}=useApp();async function run<T>(fn:()=>Promise<T>,message?:string,refresh=true):Promise<T|undefined>{setBusy(true);setError('');try{const result=await fn();if(refresh)await reload();if(message)notify(message);return result;}catch(e){setError((e as Error).message);return undefined;}finally{setBusy(false);}}return{busy,error,run,setError};}
export function useJob(){
 const [job,setJob]=useState<Job|null>(null),[error,setError]=useState(''),[busy,setBusy]=useState(false),[now,setNow]=useState(Date.now);
 const {notify}=useApp();const active=useRef<AbortController|null>(null),mounted=useRef(true);
 useEffect(()=>{mounted.current=true;return()=>{mounted.current=false;active.current?.abort();};},[]);
 useEffect(()=>{if(!busy)return;const timer=setInterval(()=>setNow(Date.now()),1000);return()=>clearInterval(timer);},[busy]);
 const wait=useCallback(async(id:string,onProgress?:(job:Job)=>void)=>{
  if(!mounted.current)return undefined;
  active.current?.abort();const controller=new AbortController();active.current=controller;setBusy(true);setError('');setJob(null);
  try{
   const result=await watchJob(id,{signal:controller.signal,onProgress:(value:Job)=>{if(mounted.current&&!controller.signal.aborted){setJob(value);setNow(Date.now());onProgress?.(value);}}}) as Job;
   if(result.status!=='succeeded')throw new ApiError(result.error||'任务未完成，可以重新提交',409);
   return result;
  }catch(e){if(mounted.current&&!controller.signal.aborted){setError((e as Error).message);notify((e as Error).message);}return undefined;}
  finally{if(active.current===controller){active.current=null;if(mounted.current)setBusy(false);}}
 },[notify]);
 const cancel=useCallback(()=>{active.current?.abort();active.current=null;if(mounted.current){setBusy(false);setError('');}},[]);
 return{job,error,busy,wait,cancel,setJob,setError,statusText:jobStatusText(job,now),percent:jobProgressPercent(job?.progress)};
}
