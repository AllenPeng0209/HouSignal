import type {Job} from '../api/types';
export class RequestScope {private version=0;private alive=true;begin(){return ++this.version;} current(value:number){return this.alive&&value===this.version;} close(){this.alive=false;this.version++;}}
function ensureActive(signal?:AbortSignal){if(signal?.aborted)throw new Error('已停止等待任务');}
function pause(ms:number,signal?:AbortSignal){return new Promise<void>((resolve,reject)=>{ensureActive(signal);const stop=()=>{clearTimeout(timer);signal?.removeEventListener('abort',stop);reject(new Error('已停止等待任务'));};const timer=setTimeout(()=>{signal?.removeEventListener('abort',stop);resolve();},ms);signal?.addEventListener('abort',stop,{once:true});});}
export async function pollChat(read:(path:string,options?:any)=>Promise<Job>,id:string,project:string,options:{signal?:AbortSignal;onProgress?:(job:Job)=>void;interval?:number}={}):Promise<Job>{
 const start=Date.now();let errors=0;
 for(;;){
  ensureActive(options.signal);if(Date.now()-start>600000)throw new Error('等待任务超时，请从任务中心查看结果');
  let job:Job;
  try{job=await read(`/api/jobs/${encodeURIComponent(id)}`,{signal:options.signal});errors=0;}
  catch(error){ensureActive(options.signal);if(++errors>=3)throw error;await pause(1500,options.signal);continue;}
  ensureActive(options.signal);if(job.id!==id||job.property_id!==project)throw new Error('回答任务与当前项目不一致');
  if(!['queued','running','succeeded','failed','cancelled'].includes(job.status))throw new Error('任务状态无效');
  options.onProgress?.(job);if(['succeeded','failed','cancelled'].includes(job.status))return job;
  await pause(options.interval??1000,options.signal);
 }
}
