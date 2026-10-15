/** Serial server-backed draft writes per conversation. Never uses browser storage. */
export class ConversationDraftStore {
  /** @param {{patch:(id:string,text:string)=>Promise<unknown>,send:(id:string,text:string)=>Promise<unknown>,delay?:number,onError?:(error:Error)=>void}} options */
  constructor(options) { this.options=options; this.entries=new Map(); }
  entry(id) {
    if(!this.entries.has(id))this.entries.set(id,{text:'',revision:0,dirty:false,hydrated:false,sending:false,timer:null,tail:Promise.resolve(),listeners:new Set()});
    return this.entries.get(id);
  }
  hydrate(id,text='') {const entry=this.entry(id);if(!entry.hydrated){entry.hydrated=true;entry.text=text;this.emit(entry);}}
  get(id) {return this.entry(id).text;}
  emit(entry) {for(const listener of entry.listeners)listener(entry.text);}
  subscribe(id,listener) {const entry=this.entry(id);entry.listeners.add(listener);listener(entry.text);return()=>entry.listeners.delete(listener);}
  stopTimer(entry) {if(entry.timer){clearTimeout(entry.timer);entry.timer=null;}}
  schedule(id) {const entry=this.entry(id);this.stopTimer(entry);if(entry.sending||!entry.dirty)return;entry.timer=setTimeout(()=>{entry.timer=null;this.flush(id).catch(error=>this.options.onError?.(error));},this.options.delay??550);}
  set(id,text) {const entry=this.entry(id);entry.hydrated=true;entry.text=text;entry.revision++;entry.dirty=true;this.emit(entry);this.schedule(id);}
  queue(entry,operation) {const pending=entry.tail.then(operation);entry.tail=pending.catch(()=>{});return pending;}
  async flush(id) {
    const entry=this.entry(id);this.stopTimer(entry);
    if(entry.sending){await entry.tail;return this.flush(id);}
    if(!entry.dirty){await entry.tail;return;}
    const revision=entry.revision,text=entry.text;
    return this.queue(entry,async()=>{if(!entry.dirty||revision!==entry.revision)return;await this.options.patch(id,text);if(revision===entry.revision)entry.dirty=false;});
  }
  async flushAll() {await Promise.all([...this.entries.keys()].map(id=>this.flush(id)));}
  /** @param {string} id @param {string} text @param {((id:string,text:string)=>Promise<unknown>)|undefined} [sendOverride] */
  async send(id,text,sendOverride) {
    const entry=this.entry(id);if(entry.sending)throw new Error('消息正在发送，请稍候');
    const original=entry.text,clearsComposer=original.trim()===text.trim();this.stopTimer(entry);entry.sending=true;
    if(clearsComposer){entry.text='';entry.revision++;entry.dirty=false;this.emit(entry);}
    const clearedRevision=entry.revision;
    return this.queue(entry,async()=>{
      try {const result=await (sendOverride||this.options.send)(id,text);entry.dirty=Boolean(entry.text)||Boolean(sendOverride);return result;}
      catch(error){if(clearsComposer&&entry.revision===clearedRevision){entry.text=original;entry.revision++;entry.dirty=true;this.emit(entry);}throw error;}
      finally {entry.sending=false;this.schedule(id);}
    });
  }
  clear() {for(const entry of this.entries.values())this.stopTimer(entry);this.entries.clear();}
}
