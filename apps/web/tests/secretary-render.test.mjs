import {test,before,after} from 'node:test';
import assert from 'node:assert/strict';
import {mkdtemp,rm,writeFile} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import {join,resolve} from 'node:path';
import {pathToFileURL} from 'node:url';
import {build} from 'esbuild';
import React from 'react';
import {renderToStaticMarkup} from 'react-dom/server';
let panels,directory;
before(async()=>{
 directory=await mkdtemp(join(tmpdir(),'housignal-secretary-'));
 const result=await build({stdin:{contents:`export * from './Assistant';export {LeadDialog} from './Leads';`,resolveDir:resolve('src'),loader:'tsx'},write:false,bundle:true,format:'esm',platform:'node',jsx:'automatic',external:['react','react-dom','react/jsx-runtime'],loader:{'.css':'empty'},plugins:[{name:'loaded-crm-http',setup(builder){
 builder.onResolve({filter:/^\.\/hooks$/},()=>({path:'crm-hooks',namespace:'fixture'}));
 builder.onResolve({filter:/^\.\/conversation-drafts$/},()=>({path:'conversation-drafts',namespace:'fixture'}));
 builder.onLoad({filter:/.*/,namespace:'fixture'},({path})=>({loader:'js',contents:path==='conversation-drafts'?'export const conversationDrafts={};':`export const useApp=()=>globalThis.secretaryApp;export const useResource=(path)=>({data:globalThis.secretaryResources[path]??null,loading:false,error:'',retry(){}});export const useMutation=()=>({busy:false,error:'',setError(){},run:async()=>{}});`}));
 }}]});
 const bundled=result.outputFiles[0].text.replaceAll('from "react"',`from ${JSON.stringify(pathToFileURL(resolve('node_modules/react/index.js')).href)}`).replaceAll('from "react/jsx-runtime"',`from ${JSON.stringify(pathToFileURL(resolve('node_modules/react/jsx-runtime.js')).href)}`);
 const target=join(directory,'panels.mjs');await writeFile(target,bundled);panels=await import(pathToFileURL(target).href);
});
after(async()=>{delete globalThis.secretaryApp;delete globalThis.secretaryResources;if(directory)await rm(directory,{recursive:true,force:true});});
function setup(stage='captured'){
 globalThis.secretaryApp={project:{id:'p1'},canSales:true,navigate(){},notify(){}};
 globalThis.secretaryResources={'/api/projects/p1/leads/l1':{
 lead:{id:'l1',name:'彭先生',stage,need:'改善自住',next_action:'确认会面地点',source_confidence:'self_reported'},
 inquiry:{id:'i1',contact:'未提供'},followups:[],
 profile:{background:'企业经营者',financial_situation:'客户自报现金购房',budget:'3000万元',preferences:'安静、江景',family:'三口之家',requirements:'四房',notes:'周中下午可看房',provenance:{}},
 viewings:[{id:'v1',lead_id:'l1',date:'2026-10-21',time_window:'下午',starts_at:null,location:'',status:'pending_details',timezone:'Asia/Shanghai',missing_details:['time','location'],notes:'先见面沟通'}],
 }};
}
const render=(Component,props)=>renderToStaticMarkup(React.createElement(Component,props));

test('customer details show persisted client knowledge and incomplete viewing details',()=>{
 setup();const html=render(panels.LeadDialog,{id:'l1',onClose(){}});
 assert.match(html,/企业经营者/);assert.match(html,/3000万元/);assert.match(html,/安静、江景/);
 assert.match(html,/10月21日（周三）下午/);assert.match(html,/地点待补充/);assert.match(html,/待补充细节/);
 assert.match(html,/让楼管家跟进/);
});

test('captured clients do not advance through qualified sales stages',()=>{
 setup();const html=render(panels.LeadDialog,{id:'l1',onClose(){}});
 assert.match(html,/待完善客户/);assert.doesNotMatch(html,/<option value="qualified">/);
 assert.match(html,/确认资格/);
});

test('operation receipts describe only applied changes with links back to their records',()=>{
 const html=panels.ActionReceipts?render(panels.ActionReceipts,{message:{actions:[
 {id:'a1',label:'更新客户画像',status:'applied',summary:'预算已保存到客户档案',changed_fields:['budget'],sources:[{kind:'lead',id:'l1',label:'彭先生客户档案'}]},
 {id:'a2',label:'失败的操作',status:'failed',summary:'未保存'},
 ]},onSource(){}}):'';
 assert.match(html,/预算已保存到客户档案/);assert.match(html,/<button[^>]*>[\s\S]*?彭先生客户档案/);
 assert.doesNotMatch(html,/失败的操作/);
});

test('a queued video operation renders as waiting for production with its inspect link',()=>{
 const html=render(panels.ActionReceipts,{message:{actions:[{id:'q1',status:'queued',label:'视频制作任务',summary:'已创建视频任务',sources:[{kind:'job',id:'job-video',label:'查看制作任务'}]}]},onSource(){}});
 assert.match(html,/已创建视频任务/);assert.match(html,/等待制作/);assert.match(html,/查看制作任务/);assert.doesNotMatch(html,/已写入项目记录|成片已完成/);
});

test('answers link intact native archived videos and never create external playback links',()=>{
 const path='/api/projects/6e3c19fa-7e41-4b26-bae1-699d437acc40/assets/fc19b2e4-1dd8-456c-aad3-51dfa2f36173/file';
 const html=panels.AnswerText?render(panels.AnswerText,{content:`成片：[播放视频](${path})\n其他：https://untrusted.test${path}\n示例：https://housignal.example.com${path}`}):'';
 assert.ok(html.includes(`href="${path}"`));assert.match(html,/>播放视频<\/a>/);assert.equal([...html.matchAll(/<a /g)].length,1);assert.doesNotMatch(html,/href="https?:|housignal\.example/);
});
