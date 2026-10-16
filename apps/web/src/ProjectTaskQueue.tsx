import {Badge,Button,Empty,ErrorNotice,Loading} from './components';
import {useApp,useResource} from './hooks';
import {followupDueLabel,taskDestination} from './crm-ux.js';
import type {NavigationTarget} from './workspace-navigation';
import type {Page} from './types';
interface ProjectTask {id:string;kind:string;title:string;detail:string;due_at:string|null;owner_id?:string|null;owner_name?:string|null;blocked?:boolean;blocker?:string|null;target:NavigationTarget&{page:Page}}
interface ProjectTasks {timezone:string;as_of:string;items:ProjectTask[]}
const kinds:Record<string,string>={fact_gap:'补齐资料',pending_inquiry:'承接咨询',content_review:'内容审核',failed_job:'制作异常',followup:'客户跟进',viewing:'会面带看',metrics:'指标回填'};
const actions:Record<string,string>={fact_gap:'补齐资料',pending_inquiry:'确认咨询',content_review:'审核内容',failed_job:'查看任务',followup:'查看客户',viewing:'查看安排',metrics:'回填指标'};
export function ProjectTaskQueue(){
 const {project,session,navigate}=useApp();
 const resource=useResource<ProjectTasks>(`/api/projects/${project.id}/tasks`);
 const items=resource.data&&Array.isArray(resource.data.items)?resource.data.items:null;
 const manager=['owner','admin'].includes(session.role),title=manager?'团队待办':'我的待办';
 return <section className="card project-task-queue" aria-label={title}><div className="card-header"><div><h3>{title}</h3><p className="muted">{manager?'查看项目责任人与下一步，点击直接处理。':'按当前角色和承接范围显示，点击直接处理。'}</p></div>{items&&!resource.error&&<Badge>{items.length} 项待处理</Badge>}</div>{resource.loading?<Loading/>:resource.error?<ErrorNotice message={resource.error} retry={resource.retry}/>:resource.data&&items?<>{resource.backgroundError&&<ErrorNotice message={`待办刷新失败，以下为上次读取的记录：${resource.backgroundError}`} retry={resource.retry}/ >}{items.length?<div className="project-task-list">{items.map(task=>{const destination=taskDestination(task.target);return <article className="project-task-row" key={task.id}><div className="project-task-copy"><div className="record-meta"><Badge tone={task.blocked?'amber':'neutral'}>{kinds[task.kind]||'项目任务'}</Badge><span>负责人：{task.owner_name||(task.owner_id?'已指派，姓名待读取':'待分配')}</span></div><h4>{task.title}</h4><p>{task.detail}</p><div className="project-task-deadline">{task.due_at?followupDueLabel(task.due_at,resource.data!.as_of):'未设期限'}</div>{task.blocker&&<p role="note" className="project-task-blocker">{task.blocked?'待解决：':'说明：'}{task.blocker}</p>}</div><Button disabled={!destination} onClick={()=>{if(destination)navigate(destination.page,destination.target);}}>{actions[task.kind]||'查看详情'}</Button></article>;})}</div>:<Empty icon="check" title="暂无待处理事项">项目记录更新后，会在这里显示对应责任人的下一步。</Empty>}</>:<ErrorNotice message="待办记录缺失，请重新读取。" retry={resource.retry}/>}</section>;
}
