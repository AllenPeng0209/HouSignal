import {useEffect,useRef,useState,type SetStateAction} from 'react';
import {draftFingerprint,workspaceDrafts,type DraftAlternative,type DraftScope} from './workspace-draft-store.js';
interface DraftOptions<T>{scope:DraftScope;kind:string;resourceId:string;serverValue:T|null}
type DraftStatus='clean'|'saved'|'memory';
interface DraftState<T>{key:string;value:T|null;localValue:T|null;conflict:boolean;conflictKind:'server'|'concurrent'|null;alternatives:DraftAlternative<T>[];lineage:string[];status:DraftStatus;error:string}
const storageError='浏览器未能保存草稿，修改暂存于当前窗口。请勿关闭或刷新，恢复存储后重试。';
/** Each edit writes a document-owned branch synchronously; other windows never replace its text. */
export function useWorkspaceDraft<T>({scope,kind,resourceId,serverValue}:DraftOptions<T>){
 const key=JSON.stringify([scope.userId,scope.organizationId,scope.projectId,kind,resourceId,draftFingerprint(serverValue)]);
 function clean():DraftState<T>{return {key,value:serverValue,localValue:null,conflict:false,conflictKind:null,alternatives:[],lineage:[],status:'clean',error:''};}
 function read(previous?:DraftState<T>):DraftState<T>{
  const local=serverValue!==null&&resourceId?workspaceDrafts.load<T>(scope,kind,resourceId,serverValue):null;
  if(!local)return clean();
  // A clean editor may learn that another window has a draft, but does not adopt it silently.
  if(previous?.status==='clean'&&!local.isOwn&&draftFingerprint(local.value)!==draftFingerprint(previous.value)){
   return {...previous,conflict:true,conflictKind:'concurrent',localValue:previous.value,alternatives:[{id:local.id,writerId:local.writerId,value:local.value,savedAt:local.savedAt},...local.alternatives]};
  }
  return {key,value:local.conflictKind==='server'?serverValue:local.value,localValue:local.value,conflict:local.conflict,conflictKind:local.conflictKind,alternatives:local.alternatives,lineage:[...local.acknowledged,local.id],status:local.persisted?'saved':'memory',error:local.persisted?'':storageError};
 }
 const [state,setState]=useState<DraftState<T>>(()=>read());let current=state;
 if(state.key!==key){current=read();setState(current);}
 const latest=useRef(current);latest.current=current;
 function publish(value:DraftState<T>){latest.current=value;setState(value);}
 useEffect(()=>{if(!resourceId||serverValue===null)return;return workspaceDrafts.subscribe(scope,kind,resourceId,()=>{const previous=latest.current;if(previous.key===key)publish(read(previous));});},[key]);
 function write(value:T,resolveConflict=false){
  if(serverValue===null||!resourceId)return;const previous=latest.current;
  if(draftFingerprint(value)===draftFingerprint(serverValue)){workspaceDrafts.remove(scope,kind,resourceId);publish(clean());return;}
  const acknowledged=[...previous.lineage,...(resolveConflict?previous.alternatives.map(value=>value.id):[])];
  const outcome=workspaceDrafts.save(scope,kind,resourceId,serverValue,value,{acknowledged});const next=read();publish({...next,value,localValue:value,status:outcome.ok?'saved':'memory',error:outcome.error||''});
 }
 function setValue(update:SetStateAction<T>){const previous=latest.current;if(previous.conflict||previous.value===null)return;write(typeof update==='function'?(update as (previous:T)=>T)(previous.value):update);}
 function useServer(){workspaceDrafts.remove(scope,kind,resourceId);publish(clean());}
 function restoreLocal(){const previous=latest.current,value=previous.localValue??previous.value;if(value!==null)write(value,true);}
 function restoreAlternative(id:string){const choice=latest.current.alternatives.find(value=>value.id===id);if(choice)write(choice.value,true);}
 return {value:current.value,setValue,status:current.status,error:current.error,conflict:current.conflict,conflictKind:current.conflictKind,alternatives:current.alternatives,restoreLocal,restoreAlternative,useServer,clear:useServer,retry:()=>{if(latest.current.value!==null)write(latest.current.value);}};
}
