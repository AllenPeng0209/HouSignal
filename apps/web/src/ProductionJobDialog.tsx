import {useEffect,useState} from 'react';
import {useApp,useResource} from './hooks';
import {watchJob} from './job-watch.js';
import {jobProgressPercent,jobStatusText} from './job-status.js';
import {Badge,Button,ErrorNotice,Loading,Modal,formatDate,statusLabels} from './components';
import {SkillArtifactGallery} from './SkillsPage';
import type {Job} from './types';
interface ProductionArtifact {url:string;name:string;mime_type?:string;byte_size?:number}
function nativeArtifacts(result:Record<string,unknown>|undefined,projectId:string,jobId:string):ProductionArtifact[]{
 const prefix=`/api/projects/${encodeURIComponent(projectId)}/operations/skills/artifacts/${encodeURIComponent(jobId)}/`;
 return Array.isArray(result?.artifacts)?result.artifacts.flatMap((value:unknown)=>{
  if(!value||typeof value!=='object')return [];
  const artifact=value as Record<string,unknown>,raw=artifact.url;
  if(typeof raw!=='string'||!raw.startsWith('/')||raw.startsWith('//')||/[\u0000-\u001f\u007f]/.test(raw))return [];
  try{
   const parsed=new URL(raw,'https://housignal.local');
   if(parsed.origin!=='https://housignal.local'||!parsed.pathname.startsWith(prefix)||parsed.search||parsed.hash)return [];
   const filename=decodeURIComponent(parsed.pathname.slice(prefix.length));
   if(!filename||filename==='.'||filename==='..'||/[\\/\u0000-\u001f\u007f]/.test(filename))return [];
   return [{url:parsed.pathname,name:typeof artifact.name==='string'?artifact.name:'下载产物',mime_type:typeof artifact.mime_type==='string'?artifact.mime_type:undefined,byte_size:typeof artifact.byte_size==='number'?artifact.byte_size:undefined}];
  }catch{return [];}
 }):[];
}
export function ProductionJobDialog({id,title='制作任务详情',onClose}:{id:string;title?:string;onClose:()=>void}){
 const {project,navigate}=useApp();
 const resource=useResource<Job>(`/api/jobs/${encodeURIComponent(id)}`);
 const [watchError,setWatchError]=useState('');
 const job=resource.data?.id===id?resource.data:null;
 const active=Boolean(job&&['queued','running'].includes(job.status));
 useEffect(()=>{setWatchError('');},[id]);
 useEffect(()=>{
  if(!active)return;const controller=new AbortController();let mounted=true;setWatchError('');
  void watchJob(id,{signal:controller.signal,onProgress:(value:Job)=>{if(mounted)resource.setData(value);}}).catch((error:Error)=>{if(mounted&&!controller.signal.aborted)setWatchError(error.message);});
  return()=>{mounted=false;controller.abort();};
 },[id,active,resource.setData]);
 const result=job?.result;
 const artifacts=job?.status==='succeeded'?nativeArtifacts(result,project.id,id):[];
 const contentId=job?.status==='succeeded'&&typeof result?.content_version_id==='string'?result.content_version_id:'';
 function refresh(){setWatchError('');resource.retry();}
 return <Modal title={title} onClose={onClose} wide>{resource.loading?<Loading text="正在读取制作任务…"/>:resource.error?<ErrorNotice message={resource.error} retry={refresh}/>:job?<>
  <div className="record-meta"><Badge tone={job.status==='succeeded'?'green':job.status==='failed'?'red':'blue'}>{statusLabels[job.status]||job.status}</Badge><span>{formatDate(job.created_at)}</span></div>
  <div className="skill-job-progress" role="status"><div>{active&&<span className="spinner"/>}<strong>{job.waiting_for_job_id?'等待前置制作任务完成':jobStatusText(job)}</strong><span>{jobProgressPercent(job.progress)}%</span></div><progress max={100} value={jobProgressPercent(job.progress)} aria-label="制作任务进度"/></div>
  {job.status==='queued'&&<p className="form-note">任务已创建，等待制作。完成后会在这里显示实际结果。</p>}
  {job.error&&<ErrorNotice message={job.error}/>}{watchError&&<ErrorNotice message={watchError} retry={refresh}/>}
  {job.status==='succeeded'&&<div className="skill-result"><h3>实际任务结果</h3>{typeof result?.response==='string'&&result.response&&<p className="record-text">{result.response}</p>}{artifacts.length?<SkillArtifactGallery artifacts={artifacts} privatePrefix={`/api/projects/${encodeURIComponent(project.id)}/operations/skills/artifacts/${encodeURIComponent(id)}/`}/>:!contentId&&<p className="form-note">任务已完成，暂未返回可下载文件。</p>}{contentId&&<Button primary icon="studio" onClick={()=>{navigate('studio');onClose();}}>在内容工作台查看</Button>}</div>}
  <div className="form-actions"><Button icon="refresh" onClick={refresh}>刷新任务</Button><Button onClick={onClose}>关闭</Button></div>
 </>:<ErrorNotice message="任务记录不存在或暂无访问权限" retry={refresh}/>}</Modal>;
}
