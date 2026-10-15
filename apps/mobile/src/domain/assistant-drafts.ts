type Options={patch:(id:string,text:string)=>Promise<unknown>;send:(id:string,text:string)=>Promise<any>;delay?:number;onError?:(error:Error)=>void};
type Entry={text:string;revision:number;dirty:boolean;hydrated:boolean;sending:boolean;timer:ReturnType<typeof setTimeout>|null;tail:Promise<unknown>;listeners:Set<(text:string)=>void>};
export class AssistantDrafts{
 private entries=new Map<string,Entry>();
 constructor(private options:Options){}
 setErrorHandler(onError:(error:Error)=>void):void{this.options.onError=onError;}
 private entry(id:string):Entry{let entry=this.entries.get(id);if(!entry){entry={text:'',revision:0,dirty:false,hydrated:false,sending:false,timer:null,tail:Promise.resolve(),listeners:new Set()};this.entries.set(id,entry);}return entry;}
 private emit(entry:Entry){entry.listeners.forEach(listener=>listener(entry.text));}
 private stop(entry:Entry){if(entry.timer){clearTimeout(entry.timer);entry.timer=null;}}
 private schedule(id:string){const entry=this.entry(id);this.stop(entry);if(entry.sending||!entry.dirty)return;entry.timer=setTimeout(()=>{entry.timer=null;void this.flush(id).catch(error=>this.options.onError?.(error));},this.options.delay??650);}
 private queue<T>(entry:Entry,operation:()=>Promise<T>):Promise<T>{const pending=entry.tail.then(operation);entry.tail=pending.catch(()=>{});return pending;}
 hydrate(id:string,text=''):void{const entry=this.entry(id);if(!entry.hydrated){entry.text=text;entry.hydrated=true;this.emit(entry);}}
 get(id:string):string{return this.entry(id).text;}
 set(id:string,text:string):void{const entry=this.entry(id);entry.text=text.slice(0,2000);entry.hydrated=true;entry.revision++;entry.dirty=true;this.emit(entry);this.schedule(id);}
 subscribe(id:string,listener:(text:string)=>void):()=>void{const entry=this.entry(id);entry.listeners.add(listener);listener(entry.text);return()=>{entry.listeners.delete(listener);};}
 async flush(id:string):Promise<void>{
  const entry=this.entry(id);this.stop(entry);if(entry.sending){await entry.tail;return this.flush(id);}if(!entry.dirty){await entry.tail;return;}
  const revision=entry.revision,text=entry.text;
  await this.queue(entry,async()=>{if(!entry.dirty||revision!==entry.revision)return;await this.options.patch(id,text);if(revision===entry.revision)entry.dirty=false;});
  if(entry.dirty)return this.flush(id);
 }
 async flushAll():Promise<void>{await Promise.all([...this.entries.keys()].map(id=>this.flush(id)));}
 async send(id:string,text:string):Promise<any>{
  const entry=this.entry(id);if(entry.sending)throw new Error('消息正在发送，请稍候');
  const original=entry.text,clearsComposer=original.trim()===text.trim();this.stop(entry);entry.sending=true;
  if(clearsComposer){entry.text='';entry.revision++;entry.dirty=false;this.emit(entry);}const revision=entry.revision;
  return this.queue(entry,async()=>{
   try{const result=await this.options.send(id,text);entry.dirty=Boolean(entry.text);return result;}
   catch(error){if(clearsComposer&&entry.revision===revision){entry.text=original;entry.revision++;entry.dirty=true;this.emit(entry);}throw error;}
   finally{entry.sending=false;this.schedule(id);}
  });
 }
 close():void{this.entries.forEach(entry=>this.stop(entry));}
}
