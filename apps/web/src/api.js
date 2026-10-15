export class ApiError extends Error {
  constructor(message,status) { super(message); this.name='ApiError'; this.status=status; }
}
export async function api(path, options={}) {
  const {body,...rest}=options;
  const headers=new Headers(rest.headers||{});
  let encoded=body;
  if(body!==undefined&&!(body instanceof FormData)) {headers.set('Content-Type','application/json');encoded=JSON.stringify(body);}
  const response=await fetch(path,{credentials:'same-origin',...rest,headers,body:encoded});
  const json=response.status===204?null:await response.json().catch(()=>null);
  if(!response.ok) {
    if(response.status===401&&typeof window!=='undefined')window.dispatchEvent(new Event('housignal-session-expired'));
    const detail=json?.detail;
    const message=typeof detail==='string'?detail:Array.isArray(detail)?detail.map(v=>v.msg).join('；'):'请求未完成，请稍后重试';
    throw new ApiError(message,response.status);
  }
  return json;
}
export function isTerminalJob(status) {return ['succeeded','failed','cancelled'].includes(status);}
export function safeUrl(value) {try{const url=new URL(value);return ['https:','http:'].includes(url.protocol)?url.href:null;}catch{return null;}}
