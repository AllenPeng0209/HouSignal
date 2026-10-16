const PREFIX='housignal-workspace:v1:';
const clone=value=>JSON.parse(JSON.stringify(value));
const identity=scope=>`${PREFIX}${encodeURIComponent(scope.userId)}:${encodeURIComponent(scope.organizationId)}:`;
const keyFor=(scope,kind,id)=>`${identity(scope)}${encodeURIComponent(scope.projectId)}:${encodeURIComponent(kind)}:${encodeURIComponent(id)}`;
const uniqueId=()=>globalThis.crypto?.randomUUID?.()||`${Date.now().toString(36)}-${Math.random().toString(36).slice(2)}-${Math.random().toString(36).slice(2)}`;
const browserStorage=()=>{if(typeof window==='undefined')throw new Error('Browser storage unavailable');return window.localStorage;};
const tabStorage=()=>typeof window==='undefined'?null:window.sessionStorage;
const strings=value=>Array.isArray(value)?value.filter(item=>typeof item==='string'):[];
const failure='浏览器未能保存草稿，修改暂存于当前窗口。请勿关闭或刷新，恢复存储后重试。';
export const draftFingerprint=value=>JSON.stringify(value);
function legacyRevision(record){let hash=2166136261;for(const char of draftFingerprint(record.value)){hash^=char.charCodeAt(0);hash=Math.imul(hash,16777619);}return `legacy:${record.savedAt}:${hash>>>0}`;}
/** Every document writes its own branch; session storage remembers which branch this tab restores. */
export class WorkspaceDraftStore {
 constructor(storage=browserStorage,options={}){this.storage=storage;this.tabStorage=options.sessionStorage||tabStorage;this.writerId=options.writerId||uniqueId();this.memory=new Map();this.pointers=new Map();this.listeners=new Map();}
 pointer(key){
  if(this.pointers.has(key))return this.pointers.get(key);
  try{const raw=this.tabStorage()?.getItem(`${key}:tab-state`),value=raw?JSON.parse(raw):null;if(value&&typeof value==='object'){const result={selected:typeof value.selected==='string'?value.selected:null,acknowledged:strings(value.acknowledged)};this.pointers.set(key,result);return result;}}catch{}
  return {selected:null,acknowledged:[]};
 }
 setPointer(key,pointer){this.pointers.set(key,pointer);try{this.tabStorage()?.setItem(`${key}:tab-state`,JSON.stringify(pointer));return true;}catch{return false;}}
 valid(record,scope,kind,id){return record&&[1,2].includes(record.schema)&&record.kind===kind&&record.id===id&&record.scope?.userId===scope.userId&&record.scope?.organizationId===scope.organizationId&&record.scope?.projectId===scope.projectId&&typeof record.base==='string'&&Number.isFinite(record.savedAt)&&Math.abs(record.savedAt)<=8640000000000000&&record.value!==undefined&&(record.schema===1||(typeof record.writerId==='string'&&typeof record.revision==='string'));}
 records(scope,kind,id){
  const key=keyFor(scope,kind,id),entries=new Map();
  const add=(storageKey,entry)=>{if(!this.valid(entry.record,scope,kind,id))return;const record=entry.record.schema===1?{...entry.record,writerId:'legacy',revision:legacyRevision(entry.record),acknowledged:[]}:entry.record;entries.set(storageKey,{...entry,record});};
  try{const disk=this.storage(),raw=disk.getItem(key);if(raw){try{add(key,{record:JSON.parse(raw),persisted:true});}catch{}}for(let i=0;i<disk.length;i++){const storageKey=disk.key(i);if(storageKey?.startsWith(`${key}:writer:`)){const value=disk.getItem(storageKey);if(value){try{add(storageKey,{record:JSON.parse(value),persisted:true});}catch{}}}}}catch{}
  for(const [storageKey,entry] of this.memory)if(storageKey===key||storageKey.startsWith(`${key}:writer:`))add(storageKey,entry);
  return [...entries.values()].sort((a,b)=>b.record.savedAt-a.record.savedAt);
 }
 save(scope,kind,id,base,value,options={}){
  const key=keyFor(scope,kind,id),navigation=kind==='navigation',storageKey=navigation?key:`${key}:writer:${encodeURIComponent(this.writerId)}`,previous=this.memory.get(storageKey)?.record,pointer=this.pointer(key);
  const record={schema:navigation?1:2,scope:{...scope},kind,id,base:draftFingerprint(base),value:clone(value),savedAt:Date.now(),...(!navigation?{writerId:this.writerId,revision:uniqueId(),acknowledged:[...new Set([...strings(previous?.acknowledged),...pointer.acknowledged,...strings(options.acknowledged)])]}:{})};
  this.memory.set(storageKey,{record,persisted:false});let persisted=false;
  try{this.storage().setItem(storageKey,JSON.stringify(record));persisted=navigation||this.setPointer(key,{selected:this.writerId,acknowledged:pointer.acknowledged});}catch{if(!navigation)this.setPointer(key,{selected:this.writerId,acknowledged:pointer.acknowledged});}
  this.memory.set(storageKey,{record,persisted});this.emit(key);
  return {ok:persisted,savedAt:record.savedAt,error:persisted?undefined:failure};
 }
 load(scope,kind,id,base){
  const key=keyFor(scope,kind,id),pointer=this.pointer(key),entries=this.records(scope,kind,id).filter(entry=>!pointer.acknowledged.includes(entry.record.revision));
  const entry=entries.find(value=>value.record.writerId===pointer.selected)||entries.find(value=>value.record.writerId===this.writerId)||entries[0];
  if(!entry)return null;
  const record=entry.record,acknowledged=[...new Set([...strings(record.acknowledged),...pointer.acknowledged,...entries.filter(value=>value.record.revision!==record.revision&&draftFingerprint(value.record.value)===draftFingerprint(record.value)).map(value=>value.record.revision)])],alternatives=entries.filter(value=>value.record.revision!==record.revision&&!acknowledged.includes(value.record.revision)&&draftFingerprint(value.record.value)!==draftFingerprint(record.value)).map(value=>({id:value.record.revision,writerId:value.record.writerId,value:clone(value.record.value),savedAt:value.record.savedAt}));
  const conflictKind=record.base!==draftFingerprint(base)?'server':alternatives.length?'concurrent':null;
  return {id:record.revision,writerId:record.writerId,isOwn:record.writerId===this.writerId||record.writerId===pointer.selected,value:clone(record.value),conflict:Boolean(conflictKind),conflictKind,alternatives,acknowledged,persisted:entry.persisted,savedAt:record.savedAt};
 }
 remove(scope,kind,id){
  const key=keyFor(scope,kind,id),navigation=kind==='navigation',storageKey=navigation?key:`${key}:writer:${encodeURIComponent(this.writerId)}`;
  if(!navigation){const acknowledged=this.records(scope,kind,id).map(entry=>entry.record.revision);this.setPointer(key,{selected:null,acknowledged:[...new Set([...this.pointer(key).acknowledged,...acknowledged])]});}
  this.memory.delete(storageKey);try{this.storage().removeItem(storageKey);}catch{}this.emit(key);
 }
 clearIdentity(scope){
  const prefix=identity(scope);for(const key of this.memory.keys())if(key.startsWith(prefix))this.memory.delete(key);for(const key of this.pointers.keys())if(key.startsWith(prefix))this.pointers.delete(key);
  for(const getStorage of [this.storage,this.tabStorage])try{const disk=getStorage();if(!disk)continue;const keys=[];for(let i=0;i<disk.length;i++){const key=disk.key(i);if(key?.startsWith(prefix))keys.push(key);}for(const key of keys)disk.removeItem(key);}catch{}
  for(const key of this.listeners.keys())if(key.startsWith(prefix))this.emit(key);
 }
 subscribe(scope,kind,id,listener){
  const key=keyFor(scope,kind,id),listeners=this.listeners.get(key)||new Set();this.listeners.set(key,listeners);listeners.add(listener);
  const changed=event=>{if(!event.key||event.key===key||event.key.startsWith(`${key}:writer:`))listener();};
  if(typeof window!=='undefined'){window.addEventListener('storage',changed);window.addEventListener('focus',listener);}
  return()=>{listeners.delete(listener);if(!listeners.size)this.listeners.delete(key);if(typeof window!=='undefined'){window.removeEventListener('storage',changed);window.removeEventListener('focus',listener);}};
 }
 emit(key){for(const listener of this.listeners.get(key)||[])listener();}
 hasUnpersisted(){return [...this.memory.values()].some(entry=>!entry.persisted&&entry.record.kind!=='navigation');}
}
export const workspaceDrafts=new WorkspaceDraftStore();
if(typeof window!=='undefined')window.addEventListener('beforeunload',event=>{if(workspaceDrafts.hasUnpersisted()){event.preventDefault();event.returnValue='';}});
