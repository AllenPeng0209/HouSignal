import {useCallback,useEffect,useRef,useState} from 'react';
import {useApp} from '../state/AppContext';

export function useProjectResource<T>(path:string|null){
 const {api,refreshKey}=useApp();const [attempt,setAttempt]=useState(0);const requestKey=`${path}:${attempt}:${refreshKey}`;
 const [state,setState]=useState<{key:string;data:T|null;error:string}>({key:'',data:null,error:''});
 const retry=useCallback(()=>setAttempt(value=>value+1),[]);
 useEffect(()=>{
  const controller=new AbortController();
  if(path)void api<T>(path,{signal:controller.signal}).then(data=>{if(!controller.signal.aborted)setState({key:requestKey,data,error:''});}).catch(error=>{if(!controller.signal.aborted)setState({key:requestKey,data:null,error:error instanceof Error?error.message:'读取失败，请重试'});});
  return()=>controller.abort();
 },[api,path,requestKey]);
 const setData=useCallback((data:T)=>setState({key:requestKey,data,error:''}),[requestKey]);
 return {data:state.key===requestKey?state.data:null,error:state.key===requestKey?state.error:'',loading:!!path&&state.key!==requestKey,retry,setData};
}
export function useAlive(){const alive=useRef(true);useEffect(()=>{alive.current=true;return()=>{alive.current=false;};},[]);return alive;}
