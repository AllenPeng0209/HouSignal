/** Smooth bursty transport updates without retaining text the server withdrew. */
export function nextStreamText(current,target,elapsedMs){
 if(!target.startsWith(current))return target;
 const remaining=target.length-current.length;
 if(!remaining)return target;
 const rate=Math.max(60,Math.min(480,remaining*6));
 const count=Math.max(1,Math.floor(Math.max(0,Math.min(32,elapsedMs))*rate/1000));
 let end=Math.min(target.length,current.length+count);
 if(end<target.length&&/[\uD800-\uDBFF]/.test(target[end-1]))end++;
 return target.slice(0,end);
}

/** Terminal jobs omit previews; checked final messages take over after the fetch. */
export function previewForProgress(previous,job){
 if(job.status==='succeeded')return previous;
 if(job.status!=='running'&&job.status!=='queued')return '';
 return typeof job.result?.preview_text==='string'?job.result.preview_text:'';
}
