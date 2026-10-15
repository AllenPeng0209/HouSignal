import {api,ApiError,isTerminalJob} from './api.js';

const aborted=()=>new DOMException('已停止等待任务','AbortError');
function ensureActive(signal) {if(signal.aborted)throw signal.reason||aborted();}
function sleep(milliseconds,signal) {
  return new Promise((resolve,reject)=>{
    ensureActive(signal);
    const stop=()=>{clearTimeout(timer);signal.removeEventListener('abort',stop);reject(signal.reason||aborted());};
    const timer=setTimeout(()=>{signal.removeEventListener('abort',stop);resolve();},milliseconds);
    signal.addEventListener('abort',stop,{once:true});
  });
}

/**
 * Follow durable server state. SSE uses authenticated fetch so its socket can be
 * closed immediately when the project/page changes. Broken streams recover by
 * polling the same scoped API, with bounded network retries. Chat has no default
 * total deadline; an explicit deadline still applies to every kind of job.
 * @param {string} id
 * @param {{signal?:AbortSignal,onProgress?:(job:any)=>void,baseUrl?:string,streaming?:boolean,timeoutMs?:number,pollIntervalMs?:number}} options
 */
export async function watchJob(id,options={}) {
  const controller=new AbortController(),signal=controller.signal;
  const external=options.signal,stop=()=>controller.abort(external?.reason||aborted());
  if(external?.aborted)stop();else external?.addEventListener('abort',stop,{once:true});
  const expire=()=>controller.abort(new ApiError('等待任务超时，请稍后从项目任务查看结果',408));
  // Identify the server job before choosing a default deadline. A delayed first
  // chat event must not inherit the production watcher’s ten-minute cutoff.
  let deadline=options.timeoutMs===undefined?undefined:setTimeout(expire,options.timeoutMs);
  let defaultDeadline=false,longformDeadline=false,chatObserved=false;
  const path=`${options.baseUrl||''}/api/jobs/${encodeURIComponent(id)}`;
  let previous='';
  const emit=job=>{
    ensureActive(signal);
    if(!job||job.id!==id||typeof job.status!=='string')throw new Error('任务事件格式无效');
    const target=job.result?.longform_progress?.target_seconds;
    const extended=job.kind==='reference_analyze'||(['generate_content','render_content'].includes(job.kind)&&
       Number.isInteger(target)&&target>=300&&target<=600);
    if(options.timeoutMs===undefined){
      if(job.kind==='chat'){
        clearTimeout(deadline);deadline=undefined;chatObserved=true;
      }else if(!chatObserved&&extended&&!longformDeadline){
        clearTimeout(deadline);deadline=setTimeout(expire,2700000);longformDeadline=true;defaultDeadline=true;
      }else if(!chatObserved&&!defaultDeadline&&typeof job.kind==='string'&&job.kind.length>0){
        deadline=setTimeout(expire,600000);defaultDeadline=true;
      }
    }
    const encoded=JSON.stringify(job);
    if(encoded!==previous){previous=encoded;options.onProgress?.(job);}
    ensureActive(signal);
    return isTerminalJob(job.status)?job:null;
  };
  try{
    ensureActive(signal);
    if(options.streaming!==false&&typeof ReadableStream!=='undefined'){
      try{
        const response=await fetch(`${path}/events`,{credentials:'same-origin',headers:{Accept:'text/event-stream'},signal});
        if(!response.ok){
          if(response.status>=400&&response.status<500){
            const detail=await response.json().catch(()=>null);
            if(response.status===401&&typeof window!=='undefined')window.dispatchEvent(new Event('housignal-session-expired'));
            throw new ApiError(typeof detail?.detail==='string'?detail.detail:'无权访问此任务',response.status);
          }
          throw new Error('实时连接暂不可用');
        }
        if(!response.body||!response.headers.get('content-type')?.includes('text/event-stream'))throw new Error('实时连接暂不可用');
        const reader=response.body.getReader(),decoder=new TextDecoder();let buffer='';
        const consume=block=>{
          let event='message';const data=[];
          for(const line of block.replace(/^\uFEFF/,'').split(/\r?\n/)){
            if(line.startsWith('event:'))event=line.slice(6).trim();
            else if(line.startsWith('data:'))data.push(line.slice(5).replace(/^ /,''));
          }
          if(!data.length)return null;
          const value=JSON.parse(data.join('\n'));
          if(event==='error')throw new ApiError(value.detail||'访问权限已失效',403);
          return event==='status'?emit(value):null;
        };
        try{
          for(;;){
            const {value,done}=await reader.read();ensureActive(signal);
            buffer+=decoder.decode(value,{stream:!done});
            let boundary;
            while((boundary=/\r?\n\r?\n/.exec(buffer))){
              const block=buffer.slice(0,boundary.index);buffer=buffer.slice(boundary.index+boundary[0].length);
              const terminal=consume(block);if(terminal)return terminal;
            }
            if(done){if(buffer.trim()){const terminal=consume(buffer);if(terminal)return terminal;}break;}
          }
        }finally{await reader.cancel().catch(()=>{});}
      }catch(error){
        ensureActive(signal);
        if(error instanceof ApiError)throw error;
        // A disconnected proxy/browser does not cancel the durable server job.
      }
    }
    let errors=0;
    for(;;){
      ensureActive(signal);
      try{
        const terminal=emit(await api(path,{signal}));errors=0;if(terminal)return terminal;
      }catch(error){
        ensureActive(signal);
        if(error instanceof ApiError||++errors>=3)throw error;
      }
      await sleep(Math.min(1500,(options.pollIntervalMs??500)*Math.max(1,errors)),signal);
    }
  }finally{
    clearTimeout(deadline);external?.removeEventListener('abort',stop);controller.abort();
  }
}
