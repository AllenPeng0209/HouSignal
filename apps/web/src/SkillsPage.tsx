import {useCallback,useEffect,useRef,useState,type FormEvent} from 'react';
import {api,safeUrl} from './api.js';
import {useApp,useJob,useMutation,useResource} from './hooks';
import {Badge,Button,Empty,ErrorNotice,Field,Heading,Icon,Loading,Modal,formatDate,statusLabels} from './components';
import type {Asset,Job,Page} from './types';
import {NativeSkillInputs,nativeSkillDefaults,nativeSkillHasInput,nativeSkillOperation,nativeSkillSubmissionParameters,requestNativeSkill,requestNativeSkillPreflight,type NativeSkillInputSchema,type NativeSkillParameters,type NativeSkillOperationState,type NativeSkillPreflight} from './NativeSkillInputs';
import './skills.css';

type Layer='discover'|'plan'|'text_visual'|'audio_video'|'publish_attribute';
type SkillStatus='ready'|'setup'|'unavailable'|'manual';
interface Capability {id:string;title:string;layer:Layer;description:string;mode:'native'|'manual';target:Page;status:SkillStatus;reason:string;execution_engine?:string;execution_mode?:'task'|'navigate';input_schema?:NativeSkillInputSchema;operation_states?:Record<string,NativeSkillOperationState>;operation_key?:string}
interface Account {id:string;name:string;platform:string;status:'managed'|'paused'}
interface SkillResult {response?:unknown;artifacts?:unknown;review_required?:boolean;publication_state?:string;execution_engine?:string}
interface SkillJob extends Job {skill_id?:string}
interface SkillArtifact {url:string;name:string;mime_type?:string;byte_size?:number}
export function SkillArtifactGallery({artifacts,privatePrefix}:{artifacts:SkillArtifact[];privatePrefix:string}){
 return <div className="skill-artifact-gallery">{artifacts.map((artifact,index)=>{const local=artifact.url.startsWith(privatePrefix),mime=artifact.mime_type||'';return <article key={`${artifact.url}-${index}`}>
  {local&&['image/png','image/jpeg','image/webp','image/gif'].includes(mime)?<img src={artifact.url} alt={artifact.name} loading="lazy"/>:local&&['video/mp4','video/webm','video/quicktime'].includes(mime)?<video controls preload="metadata" src={artifact.url} aria-label={artifact.name}/>:local&&['audio/mpeg','audio/mp3','audio/wav','audio/x-wav','audio/mp4','audio/ogg','audio/flac'].includes(mime)?<audio controls preload="metadata" src={artifact.url} aria-label={artifact.name}/>:null}
  <div><a href={artifact.url} download target="_blank" rel="noopener noreferrer"><Icon name="download" size={15}/>{artifact.name}</a>{typeof artifact.byte_size==='number'&&artifact.byte_size>0&&<small>{new Intl.NumberFormat('zh-CN').format(artifact.byte_size)} 字节</small>}</div>
 </article>;})}</div>;
}
const layers:{id:Layer;label:string;icon:string;description:string}[]=[
 {id:'discover',label:'发现',icon:'search',description:'发现趋势、信息和内容机会'},
 {id:'plan',label:'策划',icon:'target',description:'定位、选题和内容节奏'},
 {id:'text_visual',label:'文字与视觉',icon:'image',description:'从文案到图文表达'},
 {id:'audio_video',label:'音频与视频',icon:'play',description:'配音、剪辑与视频制作'},
 {id:'publish_attribute',label:'发布与归因',icon:'chart',description:'核对发布，记录实际结果'},
];
const statuses:Record<SkillStatus,{label:string;tone:string}>={ready:{label:'可提交',tone:'green'},setup:{label:'待配置',tone:'amber'},unavailable:{label:'暂不可用',tone:'neutral'},manual:{label:'人工确认',tone:'blue'}};
const isTaskSkill=(skill:Capability)=>skill.execution_mode==='task'||(skill.execution_mode!=='navigate'&&skill.execution_engine==='housignal_native');
const executionLabel=(skill:Capability)=>isTaskSkill(skill)?'楼响项目服务':skill.mode==='manual'?'平台确认流程':'项目内操作';
const targetLabels:Partial<Record<Page,string>>={studio:'前往内容工作台',assets:'前往素材库',reports:'前往效果复盘',accounts:'前往账号管理'};

export function SkillsPage(){
 const {project,navigate,session}=useApp();
 const canRun=['owner','admin','content'].includes(session.role);
 const base=`/api/projects/${project.id}/operations`;
 const skills=useResource<Capability[]>(`${base}/skills`),accounts=useResource<Account[]>(`${base}/accounts`);
 const history=useResource<SkillJob[]>(`/api/projects/${project.id}/jobs`);
 const [layer,setLayer]=useState<Layer|'all'>('all'),[query,setQuery]=useState(''),[selection,setSelected]=useState<Capability|null>(null);
 const closeModal=useCallback(()=>setSelected(null),[]);
 const [input,setInput]=useState(''),[account,setAccount]=useState(''),[activeSkill,setActiveSkill]=useState<Capability|null>(null),[result,setResult]=useState<SkillResult|null>(null),[stopRequested,setStopRequested]=useState(false);
 const [restored,setRestored]=useState(false),[assetIds,setAssetIds]=useState<string[]>([]),[parameters,setParameters]=useState<NativeSkillParameters>({});
 const [preflight,setPreflight]=useState<{key:string;value:NativeSkillPreflight}|null>(null),[preflightError,setPreflightError]=useState<{key:string;message:string}|null>(null),[preflightBusy,setPreflightBusy]=useState(''),[preflightEpoch,setPreflightEpoch]=useState(0);
 const rows=skills.data||[],selected=selection?(rows.find(skill=>skill.id===selection.id)||selection):null;
 const catalogOperation=selected?nativeSkillOperation(selected,parameters):null;
 const draftBody={input:input.trim(),asset_ids:catalogOperation?.input_schema?.accepts_assets||catalogOperation?.input_schema?.requires_assets?assetIds:[],parameters:nativeSkillSubmissionParameters(catalogOperation?.input_schema,parameters),...(account?{account_id:account}:{})};
 const preflightKey=selected&&isTaskSkill(selected)&&canRun&&!restored?JSON.stringify([project.id,selected.id,draftBody,preflightEpoch]):'';
 const selectedOperation=preflight?.key===preflightKey&&preflightKey?preflight.value:catalogOperation;
 const inputSchema=selectedOperation?.input_schema;
 const assets=useResource<Asset[]>(inputSchema?.accepts_assets||inputSchema?.requires_assets?`/api/projects/${project.id}/assets`:null);
 const mutation=useMutation(),task=useJob(),submitting=useRef(false),mounted=useRef(true);
 useEffect(()=>{mounted.current=true;return()=>{mounted.current=false;};},[]);
 useEffect(()=>{
  if(!preflightKey||!selected){setPreflightBusy('');return;}
  let current=true;const controller=new AbortController();setPreflightBusy(preflightKey);setPreflightError(null);
  const timer=setTimeout(()=>{void requestNativeSkillPreflight(project.id,selected.id,draftBody,controller.signal).then(value=>{
   if(!value||!['ready','setup','manual','unavailable'].includes(value.status)||!value.input_schema)throw new Error('服务未返回技能准备状态，请刷新后重试。');
   if(current)setPreflight({key:preflightKey,value});
  }).catch((error:Error)=>{if(current&&error.name!=='AbortError')setPreflightError({key:preflightKey,message:error.message});}).finally(()=>{if(current)setPreflightBusy('');});},250);
  return()=>{current=false;clearTimeout(timer);controller.abort();};
 },[preflightKey,skills.data]);
 const currentPreflightError=preflightError?.key===preflightKey?preflightError.message:'',checkingPreflight=Boolean(preflightKey&&(preflightBusy===preflightKey||preflight?.key!==preflightKey&&!currentPreflightError));
 const needle=query.trim().toLocaleLowerCase();
 const filtered=rows.filter(skill=>(layer==='all'||skill.layer===layer)&&(!needle||[skill.title,skill.description,skill.id,layers.find(item=>item.id===skill.layer)?.label||''].join(' ').toLocaleLowerCase().includes(needle)));
 const busy=mutation.busy||task.busy;
 function refreshSkills(){setPreflightEpoch(value=>value+1);skills.retry();}
 function changeParameters(values:NativeSkillParameters){setParameters(previous=>{const next={...previous};for(const parameter of inputSchema?.parameters||[])delete next[parameter.key];return {...next,...values};});}
 function open(skill:Capability){setRestored(false);setSelected(skill);if(skill.id!==activeSkill?.id){setInput('');setAccount('');setAssetIds([]);setParameters(nativeSkillDefaults(skill.input_schema));if(!task.busy){task.setError('');mutation.setError('');}}}
 async function run(event:FormEvent){
  event.preventDefault();if(!selected||!isTaskSkill(selected)||selectedOperation?.status!=='ready'||!nativeSkillHasInput(inputSchema,input,assetIds)||!canRun||submitting.current||task.busy||skills.loading||checkingPreflight||currentPreflightError)return;
  submitting.current=true;setResult(null);setStopRequested(false);setActiveSkill(selected);task.setError('');
  try{
   const queued=await mutation.run<{job_id?:string;job?:Job}>(()=>requestNativeSkill(project.id,selected.id,{input,asset_ids:inputSchema?.accepts_assets||inputSchema?.requires_assets?assetIds:[],parameters:nativeSkillSubmissionParameters(inputSchema,parameters),...(account?{account_id:account}:{})}),undefined,false);
   if(!mounted.current)return;
   const jobId=queued?.job_id||queued?.job?.id;
   if(queued&&!jobId){mutation.setError('服务未返回任务标识，请刷新技能状态后重试。');return;}
   if(jobId){history.retry();const finished=await task.wait(jobId);if(!mounted.current)return;if(finished)setResult(finished.result as SkillResult||{});history.retry();}
  }finally{submitting.current=false;}
 }
 async function cancel(){if(task.job){const stopped=await mutation.run(()=>api(`/api/jobs/${task.job!.id}/cancel`,{method:'POST'}),undefined,false);if(!mounted.current)return;if(stopped)setStopRequested(true);history.retry();}}
 function historySkillId(row:SkillJob){return row.skill_id||(typeof row.result?.skill_id==='string'?row.result.skill_id:'');}
 async function restore(row:SkillJob){
  if(task.busy){if(task.job?.id===row.id&&activeSkill)setSelected(activeSkill);return;}
  const skillId=historySkillId(row),known=rows.find(item=>item.id===skillId);
  const skill:Capability=known||{id:skillId||`job-${row.id}`,title:'内容技能',layer:'text_visual',description:'当前项目保存的内容技能任务。',mode:'native',execution_engine:'housignal_native',target:'studio',status:'unavailable',reason:'查看服务端保存的任务与结果。'};
  setRestored(true);setSelected(skill);setActiveSkill(skill);setInput('');setAccount('');setAssetIds([]);setParameters({});mutation.setError('');task.setError('');task.setJob(row);setStopRequested(row.status==='cancelled');setResult(row.status==='succeeded'?(row.result as SkillResult||{}):null);
  if(['queued','running'].includes(row.status)){const finished=await task.wait(row.id);if(!mounted.current)return;if(finished)setResult(finished.result as SkillResult||{});history.retry();}
  else if(row.status==='failed'||row.status==='cancelled')task.setError(row.error||statusLabels[row.status]||'此任务未完成');
 }
 const historyRows=(history.data||[]).filter(row=>row.kind==='content_skill').slice(0,12);
 const currentTask=selected?.id===activeSkill?.id;
 const stopExplanation='停止接收结果会丢弃此任务后续回复。楼响服务会在下一个处理检查点停止任务；已发出的模型请求可能继续执行。';
 const artifactRows=Array.isArray(result?.artifacts)?result.artifacts.flatMap((value:unknown)=>{
  if(!value||typeof value!=='object')return [];
  const artifact=value as Record<string,unknown>,raw=artifact.url;
  if(typeof raw!=='string'||raw.startsWith('//')||/[\u0000-\u001f\u007f]/.test(raw))return [];
  let url:string|null=null;
  try{
   const parsed=new URL(raw,window.location.origin);
   if(!['http:','https:'].includes(parsed.protocol)||parsed.username||parsed.password)return [];
   if(parsed.origin===window.location.origin){
    const prefix=`${base}/skills/artifacts/${encodeURIComponent(task.job?.id||'')}/`;
    if(!task.job?.id||!parsed.pathname.startsWith(prefix)||parsed.search||parsed.hash)return [];
    const filename=decodeURIComponent(parsed.pathname.slice(prefix.length));
    if(!filename||filename==='.'||filename==='..'||/[\\/\u0000-\u001f\u007f]/.test(filename))return [];
    url=parsed.pathname;
   }else if(!raw.startsWith('/'))url=safeUrl(raw);
  }catch{return [];}
  return url?[{url,name:typeof artifact.name==='string'?artifact.name:'查看产物',mime_type:typeof artifact.mime_type==='string'?artifact.mime_type:undefined,byte_size:typeof artifact.byte_size==='number'?artifact.byte_size:undefined}]:[];
 }):[];
 return <section className="skills-page">
  <Heading eyebrow="CONTENT CAPABILITIES" title="技能库" subtitle="账号、发现、策划与创作能力目录。每项展示调用通道及准备状态，生成结果需核验。" actions={<Button icon="refresh" onClick={refreshSkills}>刷新状态</Button>}/>
  <div className="skills-summary"><div><span className="skills-summary-icon"><Icon name="sparkle" size={21}/></span><span><strong>{rows.length} 项能力目录</strong><small>以当前项目与账号画像为上下文</small></span></div><div className="skills-status-counts"><span><i className="ready"/>{rows.filter(skill=>skill.status==='ready').length} 项可提交任务</span><span><i/>{rows.filter(skill=>['setup','unavailable'].includes(skill.status)).length} 项待准备</span><span><i className="manual"/>{rows.filter(skill=>skill.status==='manual').length} 项需人工确认</span></div></div>
  <div className="skills-toolbar"><div className="skills-filters" role="group" aria-label="能力层筛选"><button className={layer==='all'?'active':''} aria-pressed={layer==='all'} onClick={()=>setLayer('all')}>全部能力 <span>{rows.length}</span></button>{layers.map(item=><button key={item.id} className={layer===item.id?'active':''} aria-pressed={layer===item.id} onClick={()=>setLayer(item.id)}><Icon name={item.icon} size={15}/>{item.label}<span>{rows.filter(skill=>skill.layer===item.id).length}</span></button>)}</div><label className="skills-search"><Icon name="search" size={17}/><input type="search" aria-label="搜索技能" placeholder="搜索能力、用途或技能名称" value={query} onChange={event=>setQuery(event.target.value)}/></label></div>
  {activeSkill&&task.busy&&<div className="skills-task-strip" role="status"><span className="spinner"/><span><strong>{activeSkill.title}</strong> · {task.statusText} · {task.percent}%</span><Button onClick={()=>setSelected(activeSkill)}>查看任务</Button><Button busy={mutation.busy} onClick={()=>void cancel()}>停止接收结果</Button></div>}
  {skills.loading?<Loading text="正在读取技能与服务状态…"/>:skills.error?<ErrorNotice message={skills.error} retry={skills.retry}/>:filtered.length?<>
   <div className="skills-list-heading"><p>{layer==='all'?'从一个具体目标开始，选择合适的能力。':layers.find(item=>item.id===layer)?.description}</p><span>{filtered.length} 项{needle?'匹配结果':''}</span></div>
   <div className="skills-grid">{filtered.map(skill=>{const status=statuses[skill.status]||statuses.unavailable;return <article className={`skill-card status-${skill.status}`} key={skill.id}><div className="skill-card-top"><span className={`skill-layer-icon layer-${skill.layer}`}><Icon name={layers.find(item=>item.id===skill.layer)?.icon||'sparkle'} size={20}/></span><Badge tone={status.tone}>{status.label}</Badge></div><h2>{skill.title}</h2><p>{skill.description}</p><div className="skill-card-meta"><span>{layers.find(item=>item.id===skill.layer)?.label}</span><span>{executionLabel(skill)}</span></div><div className="skill-card-footer"><span title={skill.reason}>{skill.reason||'状态以当前楼响服务配置为准'}</span><Button onClick={()=>open(skill)} icon="arrow">{isTaskSkill(skill)?skill.status==='ready'?'使用技能':'查看准备':skill.mode==='manual'?'查看流程':'打开操作'}</Button></div></article>;})}</div>
  </>:<div className="card"><Empty icon="search" title="没有找到匹配能力" action={<Button onClick={()=>{setQuery('');setLayer('all');}}>查看全部能力</Button>}>换一个关键词，或清除能力层筛选。</Empty></div>}
  <section className="card" role="region" aria-label="最近内容技能任务" style={{marginTop:24}}><div className="card-header"><h3>最近内容技能任务</h3><Button icon="refresh" onClick={history.retry}>刷新任务</Button></div>{history.error?<p className="form-note" role="status">任务历史暂未读取：{history.error}</p>:history.loading?<p className="form-note">正在读取任务历史…</p>:!historyRows.length?<p className="form-note">尚无内容技能任务。任务提交后保存在当前项目，可刷新页面后继续查看。</p>:<div>{historyRows.map(row=><article className="skill-history-row" data-job-id={row.id} key={row.id} style={{display:'flex',gap:14,alignItems:'center',padding:'13px 0',borderTop:'1px solid #edf2f8'}}><div style={{flex:1,minWidth:0}}><strong style={{fontSize:12,color:'#8198b8'}}>{rows.find(skill=>skill.id===historySkillId(row))?.title||'内容技能'}</strong><p className="form-note" style={{margin:'5px 0 0'}}>{formatDate(row.created_at)} · {row.phase||statusLabels[row.status]||row.status}</p></div><Badge tone={row.status==='succeeded'?'green':row.status==='failed'?'amber':'neutral'}>{statusLabels[row.status]||row.status}</Badge><Button disabled={busy&&task.job?.id!==row.id} onClick={()=>void restore(row)}>{row.status==='succeeded'?'查看结果':'查看任务'}</Button></article>)}</div>}</section>
  {selected&&<Modal title={selected.title} onClose={closeModal} wide>
   <div className="skill-detail-header"><Badge tone={(statuses[selectedOperation?.status||selected.status]||statuses.unavailable).tone}>{(statuses[selectedOperation?.status||selected.status]||statuses.unavailable).label}</Badge><span>{executionLabel(selected)} · {layers.find(item=>item.id===selected.layer)?.label}</span></div><p className="skill-detail-description">{selected.description}</p><div className="skill-readiness"><Icon name={selectedOperation?.status==='ready'?'check':'clock'} size={17}/><p>{selectedOperation?.reason||'请确认楼响项目服务配置与资料准备情况。'}</p></div>
   {restored?<div className="skill-native-action"><p>已恢复当前项目保存的任务，{task.busy?'可以查看执行阶段或停止接收结果。':'结果与状态以服务端记录为准。'}</p>{task.busy&&task.job&&<Button busy={mutation.busy} onClick={()=>void cancel()}>停止接收结果</Button>}</div>:!isTaskSkill(selected)&&selected.mode==='native'?<div className="skill-native-action"><p>此能力使用当前项目已有的操作流程，结果保存在对应的项目记录中。</p><Button primary icon="arrow" onClick={()=>navigate(selected.target)}>{targetLabels[selected.target]||'前往操作'}</Button></div>:!isTaskSkill(selected)&&selected.mode==='manual'?<div className="skill-native-action">{selected.id==='account-data'?<p>自动同步尚未接入当前项目。请在真实平台核对账号和表现数据，再将已观察的内容指标回填到发布回执。</p>:<ol><li>在账号页核对目标平台、账号和运营画像。</li><li>在内容工作台完成最终内容审核并导出发布包。</li><li>人工登录平台确认提交，发布后登记真实链接并核实回执。</li></ol>}<div className="inline-actions"><Button primary icon="arrow" onClick={()=>navigate('accounts')}>前往账号管理</Button><Button onClick={()=>navigate('studio')}>前往内容工作台</Button></div></div>:<form className="skill-run-form" onSubmit={event=>void run(event)}>
    <Field label="你的需求" hint={inputSchema&&!inputSchema.input_required?'可选。留空时按所选操作、素材与参数处理当前项目。':'写清目标、平台、受众与输入材料；生成媒体时写明画幅和时长。'}><textarea rows={5} required={!inputSchema||inputSchema.input_required===true} maxLength={6000} value={input} disabled={!canRun||busy} onChange={event=>setInput(event.target.value)} placeholder="例如：结合项目的已核验事实，为改善型购房人群写 3 条抖音选题，说明每条的切入角度与依据。"/></Field>
    {inputSchema&&<NativeSkillInputs schema={inputSchema} assets={assets.data||[]} assetIds={assetIds} parameters={nativeSkillSubmissionParameters(inputSchema,parameters)} onAssetsChange={setAssetIds} onParametersChange={changeParameters} disabled={!canRun||busy} loading={assets.loading} error={assets.error} retry={assets.retry} onOpenAssets={()=>navigate('assets')}/>}
    <Field label="账号画像" hint="可选。仅列出在运营的账号；选择后带入定位、受众与表达风格。"><select value={account} disabled={!canRun||busy||accounts.loading} onChange={event=>setAccount(event.target.value)}><option value="">使用当前项目上下文</option>{accounts.data?.filter(row=>row.status==='managed').map(row=><option value={row.id} key={row.id}>{row.name} · {row.platform}</option>)}</select></Field>
    {accounts.error&&<ErrorNotice message={`账号画像暂未读取：${accounts.error}`} retry={accounts.retry}/>}
    {!canRun&&<p className="form-note">需要组织负责人、管理员或内容负责人权限才能运行技能。</p>}
    <div className="skill-run-notice"><Icon name="shield" size={15}/><span>结果作为待核对草稿。发布仍需最终审核、平台确认和真实回执。</span></div>
    {checkingPreflight&&<p className="form-note" role="status">正在核对当前参数、素材权限与服务状态…</p>}{currentPreflightError&&<ErrorNotice message={currentPreflightError} retry={refreshSkills}/>}
    <div className="form-actions"><Button type="submit" primary icon="sparkle" busy={busy} disabled={!canRun||skills.loading||checkingPreflight||!!currentPreflightError||selectedOperation?.status!=='ready'||!nativeSkillHasInput(inputSchema,input,assetIds)}>运行技能</Button><Button icon="refresh" disabled={busy||skills.loading} onClick={refreshSkills}>刷新技能状态</Button>{task.busy&&task.job&&currentTask&&<Button onClick={()=>void cancel()}>停止接收结果</Button>}</div>
   </form>}
   {currentTask&&(mutation.error||task.error)&&<ErrorNotice message={mutation.error||task.error}/>}
   {currentTask&&task.busy&&<div className="skill-job-progress" role="status"><div><span className="spinner"/><strong>{task.statusText}</strong><span>{task.percent}%</span></div><progress max={100} value={task.percent} aria-label="技能任务进度"/><small>任务阶段由服务端返回，关闭弹窗后仍可从本页查看。</small></div>}
   {currentTask&&(task.busy||stopRequested||task.job?.status==='cancelled')&&<div className="skill-stop-boundary"><p>{stopExplanation}</p></div>}
   {currentTask&&result&&<div className="skill-result" aria-live="polite"><div className="skill-result-heading"><h3>任务结果</h3><Badge tone="amber">待人工核对</Badge></div>{typeof result.response==='string'&&result.response?<pre>{result.response}</pre>:<p>{artifactRows.length?'任务已完成，请查看下方文件并核对内容。':'任务已完成，服务未返回文字或文件。请查看任务记录或联系管理员。'}</p>}{artifactRows.length>0&&<SkillArtifactGallery artifacts={artifactRows} privatePrefix={`${base}/skills/artifacts/${encodeURIComponent(task.job?.id||'')}/`}/>}<div className="form-actions"><Button onClick={()=>navigate('studio')}>前往内容工作台</Button></div></div>}
  </Modal>}
 </section>;
}
