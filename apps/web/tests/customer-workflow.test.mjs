import {after,before,test} from 'node:test';
import assert from 'node:assert/strict';
import {existsSync} from 'node:fs';
import {mkdtemp,rm,writeFile} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import {join,resolve} from 'node:path';
import {pathToFileURL} from 'node:url';
import {build} from 'esbuild';
import React from 'react';
import {renderToStaticMarkup} from 'react-dom/server';
let ui,directory;
before(async()=>{
 directory=await mkdtemp(join(tmpdir(),'louxiang-customer-workflow-'));
 const result=await build({stdin:{contents:`export * from './ProjectTaskQueue';export * from './crm-ux.js';export {LeadsPage,LeadDialog,InquiryDialog} from './Leads';`,resolveDir:resolve('src'),loader:'tsx'},write:false,bundle:true,format:'esm',platform:'node',jsx:'automatic',external:['react','react-dom','react/jsx-runtime'],loader:{'.css':'empty'},plugins:[{name:'isolated-project-data',setup(builder){
  if(!existsSync(resolve('src/ProjectTaskQueue.tsx')))builder.onResolve({filter:/^\.\/ProjectTaskQueue$/},()=>({path:'missing-task',namespace:'fixture'}));
  if(!existsSync(resolve('src/crm-ux.js')))builder.onResolve({filter:/^\.\/crm-ux\.js$/},()=>({path:'missing-helper',namespace:'fixture'}));
  builder.onResolve({filter:/^\.\/hooks$/},()=>({path:'hooks',namespace:'fixture'}));
  builder.onLoad({filter:/.*/,namespace:'fixture'},({path})=>({loader:'js',contents:path==='missing-task'?'export const ProjectTaskQueue=()=>null;':path==='missing-helper'?'export const toShanghaiTimestamp=()=>undefined;export const followupPayload=()=>({});export const inquiryPayload=()=>({});export const formatShanghaiTime=()=>undefined;export const followupDueLabel=()=>undefined;export const taskDestination=()=>undefined;':`export const useApp=()=>globalThis.crmApp;export const useResource=path=>({data:globalThis.crmData[path]??null,loading:false,error:'',retry(){},...globalThis.crmStates[path]});export const useMutation=()=>({busy:false,error:'',run:async()=>{}});`}));
 }}]});
 const bundled=result.outputFiles[0].text.replaceAll('from "react"',`from ${JSON.stringify(pathToFileURL(resolve('node_modules/react/index.js')).href)}`).replaceAll('from "react/jsx-runtime"',`from ${JSON.stringify(pathToFileURL(resolve('node_modules/react/jsx-runtime.js')).href)}`);
 const target=join(directory,'workflow.mjs');await writeFile(target,bundled);ui=await import(pathToFileURL(target).href);
});
after(async()=>{delete globalThis.crmApp;delete globalThis.crmData;delete globalThis.crmStates;if(directory)await rm(directory,{recursive:true,force:true});});
const inquiry={id:'inq-1',name:'张女士',need:'三房',channel:'抖音',source_confidence:'self_reported',source_text:'客户自述',contact:'已脱敏',contactable:true,consent:true,qualified:false,created_at:'2026-10-16T23:30:00Z',publication_id:'pub-1',publication_title:'公园旁的三房'};
const lead={id:'lead-1',inquiry_id:'inq-1',name:'张女士',need:'三房',stage:'qualified',next_action:'确认到访',next_action_at:'2026-10-16T23:30:00Z',source_confidence:'self_reported',created_at:'2026-10-16T23:30:00Z',updated_at:'2026-10-16T23:30:00Z',publication_id:'pub-1',publication_title:'公园旁的三房'};
function setup({data={},states={},target={}}={}){globalThis.crmApp={project:{id:'p-1',name:'云境',channel:'抖音'},session:{role:'sales',user:{id:'sales-1'}},canSales:true,canManage:false,navigationTarget:target,navigate(){},refresh:0};globalThis.crmData=data;globalThis.crmStates=states;}
const render=(component,props={})=>renderToStaticMarkup(React.createElement(component,props));
// Losing timezone conversion would move a morning follow-up to the device timezone.
test('follow-up deadlines preserve the explicit Shanghai time and never invent missing dates',()=>{
 assert.equal(ui.toShanghaiTimestamp('2026-10-17T09:30'),'2026-10-17T09:30:00+08:00');assert.equal(ui.toShanghaiTimestamp(''),null);assert.throws(()=>ui.toShanghaiTimestamp('2026-02-30T09:30'),/时间/);assert.equal(ui.formatShanghaiTime('2026-10-16T23:30:00Z'),'2026/10/17 07:30');assert.equal(ui.followupDueLabel(null),'未安排具体时间');assert.equal(ui.followupDueLabel('2026-10-17T01:30:00Z','2026-10-17T02:00:00Z'),'已到期 · 2026/10/17 09:30');
});
// Dropping publication id or upgrading self-reported evidence creates false attribution.
test('inquiry payload preserves attribution confidence while allowing an unknown work',()=>{
 const draft={name:'张女士',need:'三房',source_confidence:'self_reported',source_text:'客户自述',publication_id:'pub-1'};assert.deepEqual(ui.inquiryPayload(draft,'request-1'),{...draft,request_key:'request-1'});assert.deepEqual(ui.inquiryPayload({...draft,publication_id:''},'request-1'),{...draft,publication_id:null,request_key:'request-1'});assert.deepEqual(ui.followupPayload('客户希望周末到访','电话确认','2026-10-18T10:00'),{note:'客户希望周末到访',next_action:'电话确认',next_action_at:'2026-10-18T10:00:00+08:00'});assert.deepEqual(ui.followupPayload('待客户确认','',''),{note:'待客户确认',next_action:'',next_action_at:null});
});
// Forwarding only the page silently loses the specific customer/content task destination.
test('task destinations retain actionable record ids and reject unsupported pages',()=>{assert.deepEqual(ui.taskDestination({page:'leads',leadId:'lead-1'}),{page:'leads',target:{leadId:'lead-1'}});assert.deepEqual(ui.taskDestination({page:'studio',contentId:'cv-1',studioStage:'review'}),{page:'studio',target:{contentId:'cv-1',studioStage:'review'}});assert.equal(ui.taskDestination({page:'not-a-page'}),null);});
// Emptying an errored resource must not claim the team has no outstanding work.
test('task queue surfaces unavailable data with retry and no success count',()=>{setup({states:{'/api/projects/p-1/tasks':{data:null,error:'待办暂不可用'}}});const html=render(ui.ProjectTaskQueue);assert.match(html,/待办暂不可用/);assert.match(html,/重试/);assert.doesNotMatch(html,/暂无待办|0 项|已全部完成/);});
test('task queue shows role-specific ownership, deadlines, blocker and precise action',()=>{setup({data:{'/api/projects/p-1/tasks':{timezone:'Asia/Shanghai',as_of:'2026-10-17T02:00:00Z',items:[{id:'follow-1',kind:'followup',title:'联系张女士',detail:'确认周末到访',due_at:'2026-10-17T01:30:00Z',owner_name:'王顾问',blocked:false,target:{page:'leads',leadId:'lead-1'}},{id:'review-1',kind:'content_review',title:'审核三房视频',detail:'核对数字依据',due_at:null,owner_name:null,blocked:true,blocker:'缺少授权依据',target:{page:'studio',contentId:'cv-1',studioStage:'review'}}]}}});const html=render(ui.ProjectTaskQueue);assert.match(html,/我的待办/);assert.match(html,/王顾问/);assert.match(html,/已到期/);assert.match(html,/2026\/10\/17 09:30/);assert.match(html,/缺少授权依据/);assert.match(html,/查看客户/);assert.match(html,/审核内容/);assert.doesNotMatch(html,/1970/);});
test('customer detail keeps the original work and deadline visible without upgrading evidence',()=>{setup({data:{'/api/projects/p-1/leads/lead-1':{lead,inquiry,followups:[],viewings:[]}}});const html=render(ui.LeadDialog,{id:'lead-1',onClose(){}});assert.match(html,/公园旁的三房/);assert.match(html,/客户自报/);assert.match(html,/2026\/10\/17 07:30/);assert.match(html,/下次跟进时间/);assert.doesNotMatch(html,/来源已核实/);});
test('inquiry detail identifies the work while qualification offers an optional deadline',()=>{setup({data:{'/api/projects/p-1/leads':[]}});const html=render(ui.InquiryDialog,{inquiry,onClose(){}});assert.match(html,/公园旁的三房/);assert.match(html,/下次跟进时间/);assert.match(html,/北京时间/);});

// An explicit accessible name keeps the source selector discoverable without option text.
test('work-origin inquiry keeps source controls accessible and evidence unverified',()=>{setup({target:{publicationId:'pub-1'},data:{'/api/projects/p-1/publications':[{id:'pub-1',title:'公园旁的三房',channel:'抖音'}],'/api/projects/p-1/inquiries':[],'/api/projects/p-1/leads':[]}});const html=render(ui.LeadsPage);assert.match(html,/<select aria-label="来源可信度"/);assert.match(html,/<select aria-label="来源作品（可不选）"/);assert.match(html,/<option value="unknown" selected="">/);assert.match(html,/<option value="pub-1" selected="">/);});
// Malformed success responses must not crash the workbench or imply completed work.
test('task response missing items is shown as unavailable rather than an empty queue',()=>{setup({data:{'/api/projects/p-1/tasks':{timezone:'Asia/Shanghai'}}});const html=render(ui.ProjectTaskQueue);assert.match(html,/待办记录缺失/);assert.match(html,/重试/);assert.doesNotMatch(html,/0 项|暂无待处理/);});
test('missing customer detail offers a retry instead of an empty dialog',()=>{setup();const html=render(ui.LeadDialog,{id:'missing-lead',onClose(){}});assert.match(html,/客户详情缺失/);assert.match(html,/重试/);});
