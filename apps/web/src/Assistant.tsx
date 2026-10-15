import {useCallback,useEffect,useLayoutEffect,useRef,useState} from 'react';
import {api} from './api.js';
import {watchJob} from './job-watch.js';
import {jobStatusText} from './job-status.js';
import {useApp,useResource} from './hooks';
import {conversationDrafts} from './conversation-drafts';
import {consumeAgentPrompt,stageAgentPrompt} from './agent-prompt.js';
import {operationActions,presentAssistantAnswer,assistantInlineParts} from './secretary.js';
import {previewForProgress} from './stream-text.js';
import {useStreamText} from './useStreamText';
import './secretary.css';
import {Button,ErrorNotice,Icon,Loading} from './components';
import type {AgentControlKind,AgentControlResult,ChatMessage,Citation,Conversation,Job,ModelStatus} from './types';
const summaryQuestion='请完整汇总当前项目情况，包括资料、内容、客户与下一步。';
const prompts=[
 {icon:'clock',title:'安排会面带看',note:'记住时间、地点与待补充事项',question:'请为【客户称呼】记录下周三下午的会面带看，地点是【会面地点】。'},
 {icon:'users',title:'更新客户档案',note:'背景、财力、预算和生活偏好',question:'请更新【客户称呼】的 CRM 档案：客户背景【实际信息】，预算【实际信息】，居住偏好【实际信息】。'},
 {icon:'building',title:'更新楼盘资料',note:'把新信息写回楼盘资料',question:'请更新本项目的【资料字段】为【实际信息】，来源是【实际来源】。'},
 {icon:'studio',title:'策划制作视频',note:'找素材、写脚本，再安排制作',question:'请为当前楼盘策划一条【时长】的【内容主题】视频，使用素材库中的【素材名称】，先整理已有素材和制作步骤。'},
 {icon:'play',title:'接着处理任务',note:'查看进度、补充要求与继续制作',question:'请查看当前项目尚未完成的视频制作任务，并按以下要求继续处理：【实际要求】。'},
 {icon:'image',title:'整理项目素材',note:'改名、补充描述与标签',question:'请整理素材【素材名称】：改名为【新名称】，补充描述【实际描述】和标签【标签】。'},
];
export function Assistant(){
 const {project,openReadiness,citation,reload}=useApp();const base=`/api/projects/${project.id}`;
 const conversations=useResource<Conversation[]>(`${base}/conversations`),model=useResource<ModelStatus>('/api/model/status');
 const [conversation,setConversation]=useState(''),[messages,setMessages]=useState<ChatMessage[]>([]),[draft,setDraft]=useState(''),[busy,setBusy]=useState(false),[switching,setSwitching]=useState(true),[error,setError]=useState(''),[status,setStatus]=useState(''),[preview,setPreview]=useState(''),[jobView,setJobView]=useState<Job|null>(null),[now,setNow]=useState(Date.now),[controlKind,setControlKind]=useState<AgentControlKind>('steer'),[sending,setSending]=useState(false),[controlNotice,setControlNotice]=useState('');
 const current=useRef(true),thread=useRef<HTMLDivElement>(null),composer=useRef<HTMLTextAreaElement>(null),pending=useRef(false),watched=useRef<string|null>(null),watchController=useRef<AbortController|null>(null),activeConversation=useRef(''),transitioning=useRef(false),initialized=useRef(false),creating=useRef<Promise<Conversation>|null>(null),submitting=useRef(false),settledJobs=useRef(new Set<string>());
 const [streamJobId,setStreamJobId]=useState('');
 const followBottom=useRef(true);
 const waitingForProduction=Boolean(jobView?.waiting_for_job_id);
 const streamedMessage=messages.find(message=>message.role==='assistant'&&message.job_id===streamJobId);
 const streamText=streamedMessage&&!['queued','running','pending'].includes(streamedMessage.status)?streamedMessage.content:preview;
 const displayedStream=useStreamText(streamText,`${conversation}:${streamJobId}`);
 const displayedMessages=messages.filter(message=>message.role==='user'||message.content||!['queued','running','pending'].includes(message.status)||(busy&&message.job_id===streamJobId));
 if(busy&&!displayedMessages.some(message=>message.role==='assistant'&&message.job_id===streamJobId))displayedMessages.push({id:`pending-${streamJobId}`,role:'assistant',content:'',status:'running',job_id:streamJobId,citations:[],created_at:''});
 useEffect(()=>{current.current=true;const report=(event:Event)=>{if(current.current)setError((event as CustomEvent<string>).detail);};window.addEventListener('housignal-draft-error',report);return()=>{current.current=false;watchController.current?.abort();window.removeEventListener('housignal-draft-error',report);if(activeConversation.current)void conversationDrafts.flush(activeConversation.current).catch(()=>{});};},[]);
 useEffect(()=>{if(!busy)return;const timer=setInterval(()=>setNow(Date.now()),1000);return()=>clearInterval(timer);},[busy]);
 useEffect(()=>{if(!conversation)return;return conversationDrafts.subscribe(conversation,(text:string)=>{if(current.current&&activeConversation.current===conversation)setDraft(text);});},[conversation]);
 useEffect(()=>{if(!conversation||switching)return;const previous=conversationDrafts.get(conversation);const prompt=consumeAgentPrompt(sessionStorage,project.id,previous);if(prompt!==previous){conversationDrafts.set(conversation,prompt);composer.current?.focus();}},[conversation,switching,project.id]);
 const readMessages=useCallback(async(id:string)=>{const rows=await api(`/api/conversations/${id}/messages`) as ChatMessage[];if(current.current&&activeConversation.current===id)setMessages(rows);return rows;},[]);
 const watch=useCallback(async(jobId:string,cid:string)=>{
  if(watched.current===jobId)return;watchController.current?.abort();const controller=new AbortController();watchController.current=controller;
  settledJobs.current.delete(jobId);watched.current=jobId;pending.current=true;setStreamJobId(jobId);setBusy(true);setError('');setPreview('');setJobView(null);setControlNotice('');
  try{
   const job=await watchJob(jobId,{signal:controller.signal,onProgress:(value:Job)=>{
    if(!current.current||activeConversation.current!==cid||watched.current!==jobId||controller.signal.aborted)return;
    setJobView(value);setNow(Date.now());setPreview(previous=>previewForProgress(previous,value));
   }}) as Job;
   if(!current.current||activeConversation.current!==cid||watched.current!==jobId)return;
   settledJobs.current.add(jobId);
   const rows=await readMessages(cid); // Final messages and citations undergo fresh source/permission checks.
   if(rows.some(message=>message.job_id===jobId&&operationActions(message).length)){
    try{await reload();}catch{if(current.current)setError('操作已保存，项目数据刷新失败，请刷新页面查看最新记录。');}
   }
   if(job.status!=='succeeded')throw new Error(job.error||'本次回答未完成，可以重新提问。');
  }catch(e){if(current.current&&activeConversation.current===cid&&!controller.signal.aborted){settledJobs.current.add(jobId);setError((e as Error).message);}}
  finally{if(activeConversation.current===cid&&watched.current===jobId){pending.current=false;watched.current=null;if(current.current){setBusy(false);setStatus('');setPreview('');setJobView(null);conversations.retry();}}}
 },[readMessages,reload]);
 async function createConversation(){if(!creating.current)creating.current=api(`${base}/conversations`,{method:'POST',body:{title:'新的项目对话'}}).finally(()=>{creating.current=null;}) as Promise<Conversation>;return creating.current;}
 async function activate(row:Conversation){
  transitioning.current=true;followBottom.current=true;setStreamJobId('');setSwitching(true);setError('');
  try{if(activeConversation.current)await conversationDrafts.flush(activeConversation.current);conversationDrafts.hydrate(row.id,row.draft||'');const rows=await api(`/api/conversations/${row.id}/messages`) as ChatMessage[];if(!current.current)return;activeConversation.current=row.id;setConversation(row.id);setMessages(rows);setDraft(conversationDrafts.get(row.id));const unfinished=[...rows].reverse().find(m=>m.job_id&&['queued','running','pending'].includes(m.status));if(unfinished?.job_id)void watch(unfinished.job_id,row.id);}
  catch(e){if(current.current)setError((e as Error).message);throw e;}
  finally{transitioning.current=false;if(current.current){setSwitching(false);requestAnimationFrame(()=>composer.current?.focus());}}
 }
 useEffect(()=>{if(initialized.current||!conversations.data)return;initialized.current=true;transitioning.current=true;setSwitching(true);const initialize=async()=>{try{const row=conversations.data![0]||await createConversation();await activate(row);if(!conversations.data!.length)conversations.retry();}catch(e){if(current.current){initialized.current=false;setError((e as Error).message);setSwitching(false);transitioning.current=false;}}};void initialize();},[conversations.data]);
 useEffect(()=>{
  if(busy||switching||sending||pending.current||submitting.current||transitioning.current||!conversation||activeConversation.current!==conversation)return;
  const unfinished=[...messages].reverse().find(message=>message.job_id&&!settledJobs.current.has(message.job_id)&&['queued','running','pending'].includes(message.status));
  if(unfinished?.job_id)void watch(unfinished.job_id,conversation);
 },[busy,switching,sending,messages,conversation,watch]);
 // Keep following before paint, including final receipts and resource refreshes.
 // Reading older messages pauses following until the user returns or sends.
 useLayoutEffect(()=>{const element=thread.current;if(element&&followBottom.current)element.scrollTop=element.scrollHeight;});
 function trackScroll(){const element=thread.current;if(element)followBottom.current=element.scrollHeight-element.clientHeight-element.scrollTop<=32;}
 async function send(text=draft){
  const question=text.trim(),cid=activeConversation.current;if(!question||submitting.current||transitioning.current||!cid)return;
  followBottom.current=true;
  if(pending.current&&!waitingForProduction){
   if(!watched.current)return;submitting.current=true;setSending(true);setError('');
   const kind=controlKind;
   try{
    const result=await conversationDrafts.send(cid,question,(id:string,controlText:string)=>api(`/api/conversations/${id}/control`,{method:'POST',body:{kind,text:controlText}})) as AgentControlResult;
    if(!current.current||activeConversation.current!==cid)return;
    await readMessages(cid);setControlNotice(kind==='steer'?'已收到补充要求，会在当前工具处理结束后调整。':'已加入后续事项，当前任务结束后接着处理。');
    if(watched.current!==result.job_id&&!watched.current)void watch(result.job_id,cid);
   }catch(e){if(current.current&&activeConversation.current===cid)setError((e as Error).message);}
   finally{submitting.current=false;if(current.current)setSending(false);}
   return;
  }
  submitting.current=true;pending.current=true;setSending(true);setBusy(true);setError('');setControlNotice('');
  try{
   const result=await conversationDrafts.send(cid,question) as {job_id:string};if(!current.current)return;
   await readMessages(cid);pending.current=false;submitting.current=false;setSending(false);void watch(result.job_id,cid);
  }catch(e){if(current.current)setError((e as Error).message);if(!watched.current){pending.current=false;if(current.current)setBusy(false);}}
  finally{submitting.current=false;if(current.current)setSending(false);}
 }
 function prefill(text:string){
  const cid=activeConversation.current;if(!cid||switching)return;
  if(!stageAgentPrompt(sessionStorage,project.id,text)){setError('草稿暂时无法保存，请直接输入。');return;}
  conversationDrafts.set(cid,consumeAgentPrompt(sessionStorage,project.id,conversationDrafts.get(cid)));
  composer.current?.focus();
 }
 async function cancel(){if(!watched.current)return;try{await api(`/api/jobs/${watched.current}/cancel`,{method:'POST'});setStatus('正在停止后续操作…');}catch(e){setError((e as Error).message);}}
 async function newConversation(){if(pending.current||transitioning.current)return;transitioning.current=true;setSwitching(true);setError('');try{if(activeConversation.current)await conversationDrafts.flush(activeConversation.current);const created=await createConversation();await activate(created);conversations.retry();}catch(e){if(current.current)setError((e as Error).message);}finally{transitioning.current=false;if(current.current)setSwitching(false);}}
 async function selectConversation(id:string){if(pending.current||transitioning.current)return;const row=conversations.data?.find(c=>c.id===id);if(row)await activate(row);}
 return <section className={`agent-page ${messages.length?'has-conversation':'is-welcome'}`}><header className="agent-page-header"><div><div className="eyebrow">YOUR PRIVATE SECRETARY</div><div className="agent-title-row"><h1>楼管家</h1><span className="agent-demo-label"><span/>{model.data?.agent_engine?.available?'智能执行已连接':model.data?.configured?'百炼 · 实时项目数据':'项目数据库已连接'}</span></div><p>你的豪宅经纪秘书：记客户、排带看、改资料、制作视频。</p></div><div className="agent-page-actions"><button className="agent-header-button" disabled={busy||switching} onClick={()=>void send(summaryQuestion)} aria-label="刷新当前项目汇总"><Icon name="refresh" size={16}/><span>刷新汇总</span></button><button className="agent-header-button" disabled={busy||switching} onClick={()=>void newConversation()} aria-label="开始新对话"><Icon name="plus" size={16}/><span>新对话</span></button></div></header>{!model.loading&&model.data&&!model.data.configured&&<ErrorNotice message="模型尚未配置，请联系组织管理员。项目资料仍可正常管理。"/>}{conversations.error&&<ErrorNotice message={conversations.error} retry={conversations.retry}/>}<div className="agent-chat"><div className="agent-project-bar"><div className="agent-project-binding"><Icon name="building" size={15}/><span>当前项目</span><strong>{project.name}</strong></div><button className="agent-project-status" onClick={openReadiness}><Icon name="shield" size={14}/><span>{project.completeness.completed} / {project.completeness.total} 项准备完成</span><Icon name="arrow" size={13}/></button></div>{conversations.data&&conversations.data.length>1&&<div className="conversation-switch"><label htmlFor="conversation-select">项目对话</label><select id="conversation-select" disabled={busy||switching} value={conversation} onChange={e=>void selectConversation(e.target.value)}>{conversations.data.map(c=><option key={c.id} value={c.id}>{c.title||'项目对话'}</option>)}</select></div>}<div className="agent-thread" ref={thread} onScroll={trackScroll} role="log" aria-label="楼管家对话" aria-live="polite" aria-relevant="additions text">{(!conversation&&conversations.loading)||switching?<Loading text="正在读取你的项目对话…"/>:displayedMessages.length?<div className="agent-message-list">{displayedMessages.map(message=>message.role==='user'?<article key={message.id} className="agent-message agent-user-message"><div className="agent-user-bubble"><p>{message.content}</p></div></article>:<AssistantMessage key={message.id} message={message} content={message.job_id===streamJobId?displayedStream:message.content} pending={busy&&message.job_id===streamJobId&&['queued','running','pending'].includes(message.status)} pendingText={status||(waitingForProduction?'等待制作任务完成，可继续交代其他事项':jobView?jobStatusText(jobView,now):'正在确认项目上下文…')} onSource={citation}/>)}</div>:<div className="agent-welcome"><div className="agent-welcome-mark"><Icon name="sparkle" size={31}/><span className="agent-mark-corner"/></div><div className="agent-welcome-eyebrow">A SECRETARY WHO GETS THINGS DONE</div><h2>交代一句话，<br className="agent-mobile-break"/>把事情安排好。</h2><p className="agent-welcome-copy">记客户、排带看、整理素材，也能策划与制作视频。<br/>交代目标后持续推进，处理中可以随时补充要求。</p><div className="agent-context-line"><span>已连接项目数据库</span><i/><span>资料完整度 {project.completeness.percent}%</span></div><div className="agent-prompt-grid">{prompts.map(p=><button key={p.title} className="agent-prompt-card" disabled={busy||switching||model.data?.configured===false} onClick={()=>prefill(p.question)}><span className="agent-prompt-icon"><Icon name={p.icon} size={19}/></span><span className="agent-prompt-copy"><strong>{p.title}</strong><small>{p.note}</small></span><Icon name="arrow" size={16}/></button>)}</div></div>}</div><div className="agent-composer-wrap">{error&&<ErrorNotice message={error} retry={()=>{setError('');if(draft.trim())void send();else if(messages.length){const question=[...messages].reverse().find(m=>m.role==='user');if(question)void send(question.content);}else{initialized.current=false;conversations.retry();}}}/>}{controlNotice&&<p className="agent-control-notice" role="status">{controlNotice}</p>}<div className="agent-composer"><label className="agent-sr-only" htmlFor="agent-input">向楼管家交代事项</label><textarea ref={composer} id="agent-input" aria-label="向楼管家交代事项" maxLength={2000} rows={2} disabled={switching||!conversation} placeholder={switching?'正在载入项目对话…':waitingForProduction?'正在等待制作完成，你可以继续交代其他事项…':busy?'任务处理中，可以补充要求、纠正方向，或安排接下来要做的事…':'例如：安排彭先生看房、更新 CRM，或用项目素材制作一条讲解视频…'} value={draft} onChange={e=>{if(activeConversation.current)conversationDrafts.set(activeConversation.current,e.target.value);}} onBlur={()=>{if(activeConversation.current)void conversationDrafts.flush(activeConversation.current).catch((e:Error)=>{if(current.current)setError((e as Error).message);});}} onKeyDown={e=>{if(e.key==='Enter'&&!e.shiftKey&&!e.nativeEvent.isComposing&&e.keyCode!==229){e.preventDefault();void send();}}}/><div className="agent-composer-actions">{busy&&!waitingForProduction?<label className="agent-control-kind"><span className="agent-sr-only">处理中消息的处理方式</span><select aria-label="处理中消息的处理方式" value={controlKind} disabled={sending} onChange={event=>setControlKind(event.target.value as AgentControlKind)}><option value="steer">补充当前任务</option><option value="follow_up">接着处理</option></select></label>:<span className="agent-composer-scope"><Icon name="lock" size={12}/><span>可读写本项目授权资料与 CRM</span></span>}{busy&&watched.current&&<Button onClick={()=>void cancel()}>停止</Button>}<button className="agent-send" disabled={sending||switching||!draft.trim()||model.data?.configured===false} onClick={()=>void send()} aria-label={busy&&!waitingForProduction?controlKind==='steer'?'补充当前任务':'安排接着处理':'发送消息'}><Icon name="arrow" size={17}/><span>{sending?'发送中':busy&&!waitingForProduction?controlKind==='steer'?'补充':'接着做':'发送'}</span></button></div></div><div className="agent-composer-footnote"><span>{waitingForProduction?'制作任务正在后台推进，可继续交代客户、资料或其他事项。':busy?'补充要求会在当前工具处理结束后生效；可以停止后续执行。':'保存与制作任务分别显示回执；任务创建后可继续查看制作进度。'}</span><span className="agent-keyboard-hint"><kbd>Enter</kbd> 发送 · <kbd>Shift + Enter</kbd> 换行</span></div></div></div></section>;
}
function AssistantMessage({message,content,pending,pendingText,onSource}:{message:ChatMessage;content:string;pending:boolean;pendingText:string;onSource:(source:Citation)=>void}){
 return <article className="agent-message agent-assistant-message"><span className="agent-message-avatar"><Icon name="sparkle" size={17}/></span><div className="agent-assistant-body"><div className="agent-message-role">楼管家</div><div className={`agent-text${pending?' agent-stream-preview':''}`}><AnswerText content={content}/>{pending&&content&&<span className="agent-stream-cursor" aria-hidden="true"/>}</div>{pending&&<div className="agent-pending-body" role="status"><span className="agent-loading-dots"><i/><i/><i/></span>{pendingText}</div>}<ActionReceipts message={message} onSource={onSource}/>{message.status==='failed'&&<ErrorNotice message="这条回答未完成，请重试。"/>}{message.citations?.length>0&&<details className="agent-sources"><summary><Icon name="file" size={14}/><span>依据与记录 · {message.citations.length} 项</span><Icon name="chevron" size={15}/></summary><div className="agent-source-list">{message.citations.map((source,index)=><button key={`${source.kind}-${source.id}-${index}`} className="agent-source-button" onClick={()=>onSource(source)}><Icon name={source.kind==='lead'?'users':source.kind==='asset'?'image':'file'} size={14}/><span>{source.label}{source.revision?` · v${source.revision}`:''}</span></button>)}</div></details>}</div></article>;
}
export function AnswerText({content}:{content:string}){return <>{presentAssistantAnswer(content).split('\n').map((line,i)=>{const value=line.replace(/^#{1,6}\s+/,'');const parts=value.split(/(\*\*[^*]+\*\*)/g).map((text,j)=>text.startsWith('**')?<strong key={j}><AnswerInline text={text.slice(2,-2)}/></strong>:<AnswerInline key={j} text={text}/>);return /^#{1,6}\s+/.test(line)?<h3 key={i}>{parts}</h3>:line?<p key={i}>{parts}</p>:<div className="answer-space" key={i}/>;})}</>;}
function AnswerInline({text}:{text:string}){return <>{assistantInlineParts(text).map((part,index)=>part.kind==='asset'?<a key={index} href={part.url} target="_blank" rel="noreferrer">{part.label}</a>:part.text)}</>;}

export function ActionReceipts({message,onSource}:{message:Pick<ChatMessage,'actions'>;onSource:(source:Citation)=>void}){
 const actions=operationActions(message);if(!actions.length)return null;
 return <section className="secretary-receipts" aria-label="已保存的操作"><div className="secretary-receipts-heading"><Icon name="check" size={14}/><strong>操作回执</strong></div>{actions.map((action,index)=><article className="secretary-receipt" key={action.id||`${action.name}-${index}`}><strong>{action.label}</strong>{action.status==='queued'&&<small className="secretary-queued-label">已创建任务 · 等待制作</small>}<p>{action.summary}</p>{action.sources?.length?<div className="secretary-record-links">{action.sources.map((source,i)=><button type="button" key={`${source.kind}-${source.id}-${i}`} onClick={()=>onSource(source)}><Icon name={source.kind==='lead'?'users':source.kind==='asset'?'image':'file'} size={13}/>{source.label}<Icon name="arrow" size={12}/></button>)}</div>:null}</article>)}</section>;
}
