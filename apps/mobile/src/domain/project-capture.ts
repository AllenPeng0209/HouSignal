export class CaptureFiles{
 private owned=new Set<string>();private pending=new Map<string,Promise<void>>();
 constructor(private remove:(uri:string)=>Promise<void>|void){}
 add(uri:string):void{if(uri)this.owned.add(uri);}
 async release(uri:string):Promise<void>{
  const running=this.pending.get(uri);if(running)return running;if(!this.owned.has(uri))return;
  const operation=Promise.resolve().then(()=>this.remove(uri)).then(()=>{this.owned.delete(uri);}).finally(()=>{this.pending.delete(uri);});this.pending.set(uri,operation);return operation;
 }
 async clear():Promise<void>{const results=await Promise.allSettled([...this.owned].map(uri=>this.release(uri)));if(results.some(result=>result.status==='rejected'))throw new Error('部分临时素材未能清理，请重试');}
}
