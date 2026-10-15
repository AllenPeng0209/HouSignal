import {useCallback,useEffect,useMemo,useRef,useState} from 'react';
import {ActivityIndicator,AppState,StyleSheet,Text,View} from 'react-native';
import {useApp} from '../state/AppContext';
import {Badge,Button,Card,Empty,Field,Heading,Notice,Page,Row,Sheet,colors,styles} from '../components/ui';
import type {ChatMessage,Conversation,Job} from '../api/types';
import type {Api,Params} from '../lib/contracts';
import {AssistantDrafts} from '../domain/assistant-drafts';
import {RequestScope,pollChat} from '../domain/assistant-scope';
import {useProjectResource} from '../domain/project-resource';

const stores=new Map<string,{api:Api;store:AssistantDrafts}>();
export function clearAssistantDrafts(){for(const entry of stores.values())entry.store.close();stores.clear();}
export async function flushAssistantDrafts(){await Promise.all([...stores.values()].map(entry=>entry.store.flushAll()));}
const prompts=[{title:'项目全貌',question:'请汇总当前项目的资料、内容、客户进展与下一步。'},{title:'资料缺口',question:'当前项目还缺什么资料？哪些问题会影响内容制作与发布？'},{title:'关键客户',question:'当前项目的关键客户是谁？请说明判断依据和下一步跟进动作。'},{title:'下一步',question:'根据当前项目记录，接下来最应该推进哪三件事？'}];

export function AssistantScreen({params}:{params?:Params}){
 const {api,project,session,openCitation,trackJob,navigate}=useApp(),projectId=project?.id,base=projectId?`/api/projects/${projectId}`:null;
 const conversations=useProjectResource<Conversation[]>(base?`${base}/conversations`:null),model=useProjectResource<{configured:boolean;provider:string}>('/api/model/status');
 const conversationData=conversations.data,conversationRetry=conversations.retry;
 const [conversation,setConversation]=useState(''),[messages,setMessages]=useState<ChatMessage[]>([]),[draft,setDraft]=useState(''),[busy,setBusy]=useState(false),[switching,setSwitching]=useState(false),[error,setError]=useState(''),[preview,setPreview]=useState(''),[job,setJob]=useState<Job|null>(null),[draftError,setDraftError]=useState(''),[picker,setPicker]=useState(false),[search,setSearch]=useState('');
 const alive=useRef(true),active=useRef(''),selection=useRef(new RequestScope()),watcher=useRef<AbortController|null>(null),watchVersion=useRef(0),operation=useRef(false),initializing=useRef(false),lastQuestion=useRef('');
 const drafts=useMemo(()=>{const key=`${session.organization.id}:${session.user.id}`;let entry=stores.get(key);if(!entry||entry.api!==api){entry?.store.close();entry={api,store:new AssistantDrafts({patch:(id,text)=>api(`/api/conversations/${id}/draft`,{method:'PATCH',body:{draft:text}}),send:(id,text)=>api(`/api/conversations/${id}/messages`,{method:'POST',body:{text}})})};stores.set(key,entry);}return entry.store;},[api,session.organization.id,session.user.id]);
 useEffect(()=>{alive.current=true;const scope=new RequestScope();selection.current=scope;drafts.setErrorHandler(error=>{if(alive.current)setDraftError(`草稿尚未保存：${error.message}。输入仍保留，请重新保存。`);});const subscription=AppState.addEventListener('change',state=>{if(state!=='active')void drafts.flushAll().catch(error=>{if(alive.current)setDraftError(`草稿尚未保存：${(error as Error).message}`);});});return()=>{alive.current=false;scope.close();watcher.current?.abort();subscription.remove();drafts.setErrorHandler(()=>{});void drafts.flushAll().catch(()=>{});};},[drafts,alive]);
 useEffect(()=>{if(!conversation)return;return drafts.subscribe(conversation,value=>{if(alive.current&&active.current===conversation)setDraft(value);});},[conversation,drafts,alive]);

 const readMessages=useCallback(async(cid:string,signal?:AbortSignal)=>{const rows=await api<ChatMessage[]>(`/api/conversations/${cid}/messages`,{signal});if(alive.current&&active.current===cid&&!signal?.aborted)setMessages(rows);return rows;},[api,alive]);
 const watch=useCallback(async(jobId:string,cid:string)=>{
  if(!projectId)return;watcher.current?.abort();const controller=new AbortController();watcher.current=controller;const token=++watchVersion.current;
  setBusy(true);setJob(null);setPreview('');setError('');trackJob(jobId);
  try{
   const final=await pollChat((path,options)=>api<Job>(path,options),jobId,projectId,{signal:controller.signal,onProgress:value=>{if(!alive.current||active.current!==cid||token!==watchVersion.current)return;setJob(value);setPreview(typeof value.result?.preview_text==='string'?value.result.preview_text:'');}});
   if(!alive.current||controller.signal.aborted||active.current!==cid||token!==watchVersion.current)return;
   await readMessages(cid,controller.signal);if(final.status==='failed')throw new Error(final.error||'回答未完成，请重新提问。');
  }catch(error){if(alive.current&&!controller.signal.aborted&&active.current===cid&&token===watchVersion.current)setError((error as Error).message);}
  finally{if(alive.current&&active.current===cid&&token===watchVersion.current){setBusy(false);setJob(null);setPreview('');watcher.current=null;conversationRetry();}}
 },[api,projectId,readMessages,trackJob,alive,conversationRetry]);
 const activate=useCallback(async(row:Conversation)=>{
  const scope=selection.current,token=scope.begin();setSwitching(true);setError('');watcher.current?.abort();watchVersion.current++;
  try{
   if(active.current)await drafts.flush(active.current);const rows=await api<ChatMessage[]>(`/api/conversations/${row.id}/messages`);if(!alive.current||!scope.current(token))return;
   drafts.hydrate(row.id,row.draft||'');active.current=row.id;setConversation(row.id);setDraft(drafts.get(row.id));setMessages(rows);setPreview('');setJob(null);setBusy(false);setDraftError('');
   const pending=rows.find(message=>message.job_id&&['queued','running','pending'].includes(message.status));if(pending?.job_id)void watch(pending.job_id,row.id);
  }catch(error){if(alive.current&&scope.current(token))setError((error as Error).message);}
  finally{if(alive.current&&scope.current(token))setSwitching(false);}
 },[drafts,api,alive,watch]);
 const create=useCallback(async()=>{if(!base)return;return api<Conversation>(`${base}/conversations`,{method:'POST',body:{title:'新对话'}});},[api,base]);
 useEffect(()=>{
  if(!conversationData||conversation||initializing.current||!base)return;initializing.current=true;
  const initialize=async()=>{try{const requested=params?.conversationId,existing=requested?conversationData.find(row=>row.id===requested):conversationData[0];if(requested&&!existing)throw new Error('这个对话不在当前项目中，请选择一个已有对话。');const row=existing||await create();if(!alive.current||!row)return;await activate(row);if(!existing)conversationRetry();}catch(error){if(alive.current){setError((error as Error).message);setSwitching(false);}}finally{initializing.current=false;}};void initialize();
 },[conversationData,conversation,base,create,activate,alive,conversationRetry,params?.conversationId]);
 async function newConversation(){if(operation.current||initializing.current||switching||busy)return;operation.current=true;setSwitching(true);setError('');try{if(active.current)await drafts.flush(active.current);const row=await create();if(alive.current&&row){await activate(row);conversations.retry();}}catch(error){if(alive.current)setError((error as Error).message);}finally{operation.current=false;if(alive.current)setSwitching(false);}}
 async function send(text=draft){const question=text.trim(),cid=active.current;if(!question||busy||switching||operation.current||!cid||model.data?.configured===false)return;operation.current=true;setBusy(true);setError('');setDraftError('');lastQuestion.current=question;
  try{const response=await drafts.send(cid,question) as {job_id:string};if(response.job_id)trackJob(response.job_id);if(!alive.current||active.current!==cid)return;if(!response.job_id)throw new Error('回答任务未建立，请重新加载对话');await readMessages(cid);operation.current=false;await watch(response.job_id,cid);}catch(error){if(alive.current&&active.current===cid){setError((error as Error).message);setBusy(false);}}finally{operation.current=false;}
 }
 async function cancel(){if(!job||!['queued','running'].includes(job.status))return;setError('');try{await api(`/api/jobs/${job.id}/cancel`,{method:'POST'});}catch(error){if(alive.current)setError((error as Error).message);}}
 async function saveDraft(){try{await drafts.flushAll();if(alive.current)setDraftError('');}catch(error){if(alive.current)setDraftError(`草稿尚未保存：${(error as Error).message}`);}}
 async function refresh(){conversations.retry();model.retry();if(active.current)try{const rows=await readMessages(active.current);const pending=rows.find(message=>message.job_id&&['queued','running','pending'].includes(message.status));if(pending?.job_id&&!busy)void watch(pending.job_id,active.current);}catch(error){if(alive.current)setError((error as Error).message);}}
 if(!project)return <Page><Empty title="先选择项目"/></Page>;
 return <Page onRefresh={()=>void refresh()} refreshing={conversations.loading} testID="assistant-page"><Heading title="楼管家" subtitle="读懂项目记录，理清下一步。" action={<Button title="新对话" tone="secondary" small disabled={busy||switching} onPress={()=>void newConversation()}/>}/><Card><Row><Text style={[styles.title,{flex:1}]}>{project.name}</Text><Badge label={project.is_test?'示例项目':'当前项目'} tone={project.is_test?'amber':'blue'}/></Row><Text style={styles.muted}>资料完整度 {project.completeness.percent}% · 仅查阅当前项目授权记录</Text><Button title="查看资料缺口" tone="ghost" small onPress={()=>navigate('project')}/></Card>
 {!!(model.error) && <Notice message={model.error} onRetry={model.retry}/>}{!!(model.data?.configured===false) && <Notice message="模型尚未配置，请联系组织管理员。项目资料仍可正常管理。"/>}{!!(conversations.error) && <Notice message={conversations.error} onRetry={conversations.retry}/>}
 {!!(!!conversations.data?.length) && <Row><Text numberOfLines={2} style={[styles.text,{flex:1}]}>{conversations.data.find(row=>row.id===conversation)?.title||'项目对话'}</Text><Button title="切换对话" tone="secondary" small disabled={busy||switching} onPress={()=>setPicker(true)}/></Row>}
 {!!((conversations.loading||switching)) && <View style={{alignItems:'center',gap:10}}><ActivityIndicator color={colors.primary}/><Text style={styles.muted}>正在读取项目对话…</Text></View>}
 {!!(!messages.length&&!switching&&!conversations.loading) && <Card><Text style={styles.title}>聊聊这个项目，下一步更清楚。</Text><Text style={styles.muted}>从楼盘事实到客户跟进，直接问。回答附上项目记录，方便核对。</Text><Row>{prompts.map(prompt=><Button key={prompt.title} title={prompt.title} tone="secondary" disabled={busy||!conversation||model.data?.configured===false} onPress={()=>void send(prompt.question)}/>)}</Row></Card>}
 {messages.filter(message=>message.role==='user'||message.content||!['queued','running','pending'].includes(message.status)).map(message=><View key={message.id} style={[local.bubble,message.role==='user'?local.user:local.assistant]}><Text style={[styles.label,message.role==='user'&&{color:'#FFF'}]}>{message.role==='user'?'你':'楼管家'}</Text><Text selectable style={[styles.text,message.role==='user'&&{color:'#FFF'}]}>{message.content||'这条回答没有生成内容。'}</Text>{!!(message.status==='failed') && <Text style={styles.muted}>这条回答未完成，请重新提问。</Text>}{!!(message.citations?.length>0) && <View style={{gap:8,marginTop:10}}><Text style={styles.label}>依据与记录 · {message.citations.length} 项</Text>{message.citations.map((citation,index)=><Button key={`${citation.kind}-${citation.id}-${index}`} title={`${citation.label}${citation.revision?` · v${citation.revision}`:''}`} tone="secondary" small onPress={()=>openCitation(citation)}/>)}</View>}</View>)}
 {!!(busy) && <Card>{!!(preview) && <><Badge label="回答生成中 · 待完成" tone="amber"/><Text selectable style={styles.text}>{preview}</Text></>}<Row><ActivityIndicator color={colors.primary}/><Text accessibilityLiveRegion="polite" style={styles.muted}>{job?.phase||(job?.status==='queued'?'等待调度':'正在确认项目上下文…')}{job?` · ${Math.round(Math.max(0,Math.min(1,job.progress))*100)}%`:''}</Text></Row>{!!(job) && <Button title="停止回答" tone="danger" onPress={()=>void cancel()}/>}</Card>}
 {!!(error) && <Notice message={error} onRetry={()=>{if(!conversation)conversations.retry();else if(draft.trim())void send();else if(lastQuestion.current)void send(lastQuestion.current);else void refresh();}}/>}{!!(draftError) && <Notice message={draftError} onRetry={()=>void saveDraft()}/>}<Card><Field label="向楼管家提问" value={draft} multiline editable={!switching&&!!conversation} onChangeText={value=>{if(active.current)drafts.set(active.current,value);}} placeholder="问问项目进展、资料缺口或客户情况…"/><Row><Text style={[styles.muted,{flex:1}]}>{draft.length} / 2000 · 草稿自动保存</Text><Button title="发送" loading={busy} disabled={switching||!conversation||!draft.trim()||model.data?.configured===false} onPress={()=>void send()}/></Row><Text style={styles.muted}>关键判断请核对原始资料。回答不会代替你核验事实、修改客户或批准发布。</Text></Card><Sheet visible={picker} title="项目对话" onClose={()=>setPicker(false)}><Field label="搜索对话" value={search} onChangeText={setSearch} placeholder="对话标题"/>{conversations.data?.filter(row=>row.title.includes(search.trim())).map(row=><Button key={row.id} title={row.title||'新对话'} tone={row.id===conversation?'primary':'secondary'} disabled={busy||switching} onPress={()=>{setPicker(false);void activate(row);}}/>)}</Sheet></Page>;
}
const local=StyleSheet.create({bubble:{borderRadius:18,padding:18,gap:4,maxWidth:'100%'},user:{backgroundColor:colors.primary,marginLeft:30},assistant:{backgroundColor:colors.card,borderWidth:1,borderColor:colors.border,marginRight:8}});
