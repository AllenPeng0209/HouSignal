import {useEffect,useRef,useState} from 'react';
import {api} from './api.js';
import {useApp,useJob,useMutation,useResource} from './hooks';
import {Badge,Button,ErrorNotice,Icon} from './components';
import {PlatformPreparePanel,requestPlatformAction} from './PlatformAccounts';
import type {Content,Job} from './types';
import './studio-publish-preparation.css';

interface ReviewedContent extends Content {approval_valid?:boolean;approved_release_id?:string|null}
interface PreparationJob extends Job {preparation_request?:{platform:string;content_version_id:string}}
const channelPlatforms:Record<string,{id:string;name:string}>={
 '抖音':{id:'douyin',name:'抖音'},'小红书':{id:'xiaohongshu',name:'小红书'},'快手':{id:'kuaishou',name:'快手'},
 'B站':{id:'bilibili',name:'B站'},'B 站':{id:'bilibili',name:'B站'},'知乎':{id:'zhihu',name:'知乎'},
 '视频号':{id:'weixin-channels',name:'视频号'},'微信视频号':{id:'weixin-channels',name:'视频号'},
 '微信公众号':{id:'wechat-oa',name:'微信公众号'},
};
function preview(job:Job):{title:string;body:string}|null {
 const value=job.result?.publication_preview as {title?:unknown;body?:unknown}|undefined;
 return value&&typeof value.title==='string'&&typeof value.body==='string'?{title:value.title,body:value.body}:null;
}
export function matchingStudioPreparation(job:Job,content:ReviewedContent,releaseId:string,platform:string):boolean {
 return Boolean(releaseId&&content.approval_valid===true&&content.status==='approved'&&job.kind==='platform_prepare'&&
  job.status==='succeeded'&&job.result?.content_version_id===content.id&&job.result?.release_id===releaseId&&
  job.result?.platform===platform&&job.result?.dry_run===true&&job.result?.publication_state==='not_published'&&preview(job));
}

export function StudioPublishPreparation({content,releaseId,disabled=false}:{content:ReviewedContent;releaseId:string;disabled?:boolean}) {
 const {project,session,navigate}=useApp(),editable=['owner','admin','content'].includes(session.role);
 const target=channelPlatforms[content.channel],platform=target?.id||'';
 const history=useResource<PreparationJob[]>(`/api/projects/${project.id}/jobs`),task=useJob(),mutation=useMutation();
 const [prepared,setPrepared]=useState<Job|null>(null),[error,setError]=useState('');
 const alive=useRef(true),submitting=useRef(false),restored=useRef(new Set<string>());
 const approved=Boolean(content.status==='approved'&&content.approval_valid===true&&releaseId&&content.render_asset_id);
 const active=task.busy||mutation.busy,canPrepare=Boolean(editable&&approved&&platform&&!disabled&&!active);
 const saved=history.data?.find(j=>matchingStudioPreparation(j,content,releaseId,platform));
 const shown=!disabled?(prepared&&matchingStudioPreparation(prepared,content,releaseId,platform)?prepared:saved):null;
 const publication=shown?preview(shown):null;
 useEffect(()=>{alive.current=true;return()=>{alive.current=false;};},[]);
 useEffect(()=>{setPrepared(null);setError('');task.setError('');},[content.id,releaseId]);
 async function finish(id:string) {
  restored.current.add(id);const job=await task.wait(id);if(!alive.current)return;history.retry();
  if(job){if(!matchingStudioPreparation(job,content,releaseId,platform)){setError('发布准备对应的内容或审核版本已改变，请按当前版本重新准备。');return;}setPrepared(job);setError('');}
 }
 useEffect(()=>{
  if(!editable||submitting.current||task.busy)return;
  const pending=history.data?.find(j=>j.kind==='platform_prepare'&&['queued','running'].includes(j.status)&&
   j.preparation_request?.content_version_id===content.id&&j.preparation_request?.platform===platform&&!restored.current.has(j.id));
  if(pending){restored.current.add(pending.id);void finish(pending.id);}
 },[history.data,content.id,releaseId,platform]);
 async function prepare() {
  if(!canPrepare||submitting.current)return;submitting.current=true;setError('');task.setError('');setPrepared(null);
  try{const queued=await mutation.run(()=>requestPlatformAction<{job_id:string}>(`/api/projects/${project.id}/operations/platform-accounts`,platform,'prepare',{content_version_id:content.id}),undefined,false);if(!alive.current)return;if(queued?.job_id){restored.current.add(queued.job_id);history.retry();await finish(queued.job_id);}else if(queued)setError('服务未返回发布准备任务，请重新准备。');}finally{submitting.current=false;}
 }
 return <section className="studio-publish-preparation" aria-label="发布准备">
  <div className="studio-prepare-heading"><div><h3>准备这条内容的发布包</h3><p>把同一审核版本的成片、封面、标题与正文整理在一起。</p></div><Badge tone="neutral">{target?.name||content.channel}</Badge></div>
  {!approved&&<p className="studio-prepare-pending"><Icon name="shield" size={15}/>先在人工审核中确认当前成片，再准备平台发布包。</p>}
  {disabled&&approved&&<p className="studio-prepare-pending">当前编辑尚未保存或任务正在进行，请先处理当前版本。</p>}
  {(error||mutation.error||task.error)&&<ErrorNotice message={error||mutation.error||task.error} retry={()=>void prepare()}/>}
  {active&&<div className="studio-prepare-progress" role="status"><span className="spinner"/>{task.statusText} · {task.percent}%{task.job&&editable&&<Button disabled={mutation.busy} onClick={()=>void mutation.run(()=>api(`/api/jobs/${task.job!.id}/cancel`,{method:'POST'}),undefined,false)}>取消准备</Button>}</div>}
  <Button primary icon="download" busy={active} disabled={!canPrepare} onClick={()=>void prepare()}>准备平台发布包</Button>
  {shown&&<><PlatformPreparePanel job={shown}/>{publication&&<div className="studio-prepare-preview"><h4>平台标题</h4><p>{publication.title}</p><details><summary>查看发布正文</summary><pre>{publication.body}</pre></details></div>}</>}
  <div className="studio-prepare-footer"><small>发布准备只整理文件。实际发布后，再登记链接、核对回执并观察 D7／D30。</small>{shown&&<Button icon="chart" onClick={()=>navigate('reports')}>查看效果分析</Button>}</div>
 </section>;
}
