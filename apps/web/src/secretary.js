const profileFields=['background','financial_situation','budget','preferences','family','requirements','notes'];
export function profileChanges(previous,draft){
 const changes={};
 for(const key of profileFields){
  if(typeof draft?.[key]!=='string')continue;
  const value=draft[key].trim();
  if(value!==(typeof previous?.[key]==='string'?previous[key].trim():''))changes[key]=value;
 }
 return changes;
}
export function appliedActions(message){
 return Array.isArray(message?.actions)?message.actions.filter(action=>action&&action.status==='applied'):[];
}
export function operationActions(message){
 return Array.isArray(message?.actions)?message.actions.filter(action=>action&&['applied','queued'].includes(action.status)):[];
}

const nativeAssetPath='/api/projects/[A-Za-z0-9_-]+/assets/[A-Za-z0-9_-]+/file';
const answerTokenPattern=()=>new RegExp(`\\[[^\\]\\n]*\\]\\([^\\s)]+\\)|(?:[A-Za-z][A-Za-z0-9+.-]*:)?\\/\\/[^\\s<>]+|${nativeAssetPath}(?![A-Za-z0-9_/?#%+-])`,'g');
function nativeAssetToken(token,previous=''){
 const markdown=token.match(/^\[([^\]\n]*)\]\(([^)\s]+)\)$/);
 const url=markdown?markdown[2]:token;
 if(!new RegExp(`^${nativeAssetPath}$`).test(url)||!markdown&&/[A-Za-z0-9_/:.%@-]/.test(previous))return null;
 return {url,label:markdown&&/视频|播放/.test(markdown[1])?'播放视频':'查看归档素材'};
}
function placeholderUrl(token){
 const markdown=token.match(/^\[[^\]\n]*\]\(([^)\s]+)\)$/);
 try{const host=new URL(markdown?markdown[1]:token).hostname.toLowerCase();return ['example.com','example.org','example.net','example'].some(domain=>host===domain||host.endsWith(`.${domain}`));}catch{return false;}
}
export function presentAssistantAnswer(content){
 const protectedPaths=[];
 const protectedContent=content.replace(answerTokenPattern(),(token,offset,full)=>{
  if(placeholderUrl(token))return '';
  if(!nativeAssetToken(token,full[offset-1]||''))return token;
  return `\uE000${protectedPaths.push(token)-1}\uE001`;
 });
 const identity='[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}';
 const field=new RegExp(`(?:客户|预约|素材|任务|项目|线索|内容|记录)?\\s*(?:ID|编号)\\s*[:：]\\s*\u0060?${identity}\u0060?`,'gi');
 const literal=new RegExp(`\u0060?${identity}\u0060?`,'gi');
 return protectedContent.split('\n').flatMap(line=>{
  const visible=line.replace(field,'').replace(literal,'相关记录').replace(/[（(]\s*[）)]/g,'');
  return /^[\s\-*]*(?:\[S\d+\]\s*)*$/.test(visible)?[]:[visible];
 }).join('\n').replace(/\uE000(\d+)\uE001/g,(_match,index)=>protectedPaths[Number(index)]||'');
}
export function assistantInlineParts(text){
 const parts=[];let start=0;
 for(const match of text.matchAll(answerTokenPattern())){
  if(match.index>start)parts.push({kind:'text',text:text.slice(start,match.index)});
  const asset=nativeAssetToken(match[0],text[match.index-1]||'');
  parts.push(asset?{kind:'asset',...asset}:{kind:'text',text:match[0]});start=match.index+match[0].length;
 }
 if(start<text.length)parts.push({kind:'text',text:text.slice(start)});
 return parts;
}
export function viewingWhen(viewing){
 const timezone=viewing.timezone||'Asia/Shanghai';
 const exact=viewing.starts_at?new Date(viewing.starts_at):null;
 const date=exact&&!Number.isNaN(exact.getTime())?exact:viewing.date?new Date(`${viewing.date}T12:00:00+08:00`):null;
 if(!date||Number.isNaN(date.getTime()))return '时间待补充';
 const formatter=new Intl.DateTimeFormat('zh-CN',{timeZone:timezone,month:'numeric',day:'numeric',weekday:'short'});
 const parts=formatter.formatToParts(date),get=type=>parts.find(part=>part.type===type)?.value||'';
 const day=`${get('month')}月${get('day')}日（${get('weekday')}）`;
 if(exact&&!Number.isNaN(exact.getTime()))return day+new Intl.DateTimeFormat('zh-CN',{timeZone:timezone,hour:'2-digit',minute:'2-digit',hour12:false}).format(exact);
 return day+(viewing.time_window||'时间待补充');
}
