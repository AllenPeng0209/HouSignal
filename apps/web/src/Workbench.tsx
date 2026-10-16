import {type KeyboardEvent} from 'react';
import {Button,Heading} from './components';
import {useApp} from './hooks';
import {Dashboard} from './ProjectPages';
import {Reports} from './Reports';
import {ProjectTaskQueue} from './ProjectTaskQueue';
import {readPageTarget} from './workspace-navigation';

export function Workbench({view='overview'}:{view?:'overview'|'analysis'}){
 const {project,session,navigate}=useApp();
 const resumeTarget=readPageTarget({userId:session.user.id,organizationId:session.organization.id,projectId:project.id},'studio');
 const tabs=[{id:'overview',label:'项目总览',page:'dashboard'},{id:'analysis',label:'效果分析',page:'reports'}] as const;
 function selectWithKeyboard(event:KeyboardEvent<HTMLButtonElement>,index:number){
  const target=event.key==='Home'?0:event.key==='End'?1:['ArrowLeft','ArrowRight'].includes(event.key)?1-index:null;
  if(target===null)return;
  event.preventDefault();navigate(tabs[target].page);document.getElementById(`workbench-tab-${tabs[target].id}`)?.focus();
 }
 return <><Heading eyebrow="PROJECT WORKBENCH" title="工作台" subtitle={`${project.name} · 进展、效果与下一步，在这里看清。`} actions={<>{resumeTarget.contentId&&<Button primary icon="edit" onClick={()=>navigate('studio',resumeTarget)}>继续上次编辑</Button>}<Button icon="sparkle" onClick={()=>navigate('assistant')}>问问楼管家</Button></>}/><div className="workbench-tabs" role="tablist" aria-label="工作台视图">{tabs.map((tab,index)=><button key={tab.id} id={`workbench-tab-${tab.id}`} role="tab" aria-selected={view===tab.id} aria-controls={`workbench-panel-${tab.id}`} tabIndex={view===tab.id?0:-1} onClick={()=>navigate(tab.page)} onKeyDown={event=>selectWithKeyboard(event,index)}>{tab.label}</button>)}</div><section key={`${project.id}:${view}`} id={`workbench-panel-${view}`} role="tabpanel" aria-labelledby={`workbench-tab-${view}`} tabIndex={0}>{view==='overview'?<><ProjectTaskQueue/><Dashboard/></>:<Reports/>}</section></>;
}
