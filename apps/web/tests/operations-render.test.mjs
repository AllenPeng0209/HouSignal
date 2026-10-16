import {test, before, after} from 'node:test';
import assert from 'node:assert/strict';
import {existsSync} from 'node:fs';
import {mkdtemp, rm, writeFile} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import {join, resolve} from 'node:path';
import {pathToFileURL} from 'node:url';
import {build} from 'esbuild';
import React from 'react';
import {renderToStaticMarkup} from 'react-dom/server';
let panels, directory;
before(async()=>{
 directory=await mkdtemp(join(tmpdir(),'housignal-operations-'));
 // Resource hooks load in a browser effect; supply their loaded HTTP results for server-render assertions.
 const source=resolve('src/OperationsPages.tsx');
 const result=await build({entryPoints:[source],write:false,bundle:true,format:'esm',platform:'node',jsx:'automatic',external:['react','react-dom','react/jsx-runtime'],loader:{'.css':'empty'},plugins:[{name:'loaded-resources',setup(builder){
  if(!existsSync(source))builder.onResolve({filter:/OperationsPages\.tsx$/},()=>({path:'missing-panels',namespace:'fixture'}));
  builder.onResolve({filter:/^\.\/hooks$/},()=>({path:'loaded-hooks',namespace:'fixture'}));
  builder.onLoad({filter:/.*/,namespace:'fixture'},({path})=>({loader:'js',contents:path==='missing-panels'?'export const AccountsPage=()=>null,DiscoveryPanel=()=>null,PlanningPanel=()=>null;':`export const useJob=()=>({job:null,busy:false,error:'',percent:0,statusText:'',wait:async()=>undefined,cancel(){},setJob(){},setError(){}});export const useApp=()=>globalThis.operationApp;export const useResource=(path)=>({data:globalThis.operationData[path]??[],loading:false,error:'',retry:()=>{}});export const useMutation=()=>({busy:false,error:'',setError:()=>{},run:async()=>{}});`}));
 }}]});
 const bundled=result.outputFiles[0].text.replaceAll('from "react"',`from ${JSON.stringify(pathToFileURL(resolve('node_modules/react/index.js')).href)}`).replaceAll('from "react/jsx-runtime"',`from ${JSON.stringify(pathToFileURL(resolve('node_modules/react/jsx-runtime.js')).href)}`);
 const target=join(directory,'panels.mjs');await writeFile(target,bundled);panels=await import(pathToFileURL(target).href);
});
after(async()=>{delete globalThis.operationApp;delete globalThis.operationData;if(directory)await rm(directory,{recursive:true,force:true});});
function setup(role='content',records={}){globalThis.operationApp={project:{id:'p-1',name:'测试项目',channel:'抖音'},session:{role,user:{id:'test-user',name:'测试运营',email:'test@example.invalid'},organization:{id:'test-org',name:'隔离组织'}},navigationTarget:{},rememberNavigation(){},notify(){},navigate(){},refresh:0};globalThis.operationData=records;}
function buttonMarkup(html,label){return Array.from(html.matchAll(/<button\b[^>]*>[\s\S]*?<\/button>/g),m=>m[0]).find(button=>button.includes(`<span>${label}</span>`))||'';}
const base='/api/projects/p-1/operations';
const account={id:'a-1',platform:'小红书',name:'江边选房',homepage:'https://www.xiaohongshu.com/user/profile/example',positioning:'真实楼盘科普',audience:'改善型买家',voice:'克制专业',red_lines:'不承诺收益',status:'managed'};
const opportunity={id:'o-1',title:'改善户型讨论',kind:'gap',platform:'小红书',source_url:'https://example.com/observed',summary:'用户关注动线问题',observed_at:'2026-10-13T03:00:00Z',status:'saved'};
const plan={id:'plan-1',title:'厨房动线选题',angle:'围绕备餐效率',channel:'小红书',scheduled_for:'2026-10-15',account_id:'a-1',opportunity_id:'o-1',topic_id:'kitchen',status:'scheduled',brief:'核对项目资料再制作'};
test('account profiles show positioning and the real external homepage without suggesting an authenticated connection',()=>{
 setup('content',{[`${base}/accounts`]:[account]});const html=renderToStaticMarkup(React.createElement(panels.AccountsPage));assert.match(html,/真实楼盘科普/);assert.match(html,/改善型买家/);assert.match(html,/克制专业/);assert.match(html,/不承诺收益/);assert.match(html,/href="https:\/\/www\.xiaohongshu\.com/);assert.doesNotMatch(html,/已登录|登录成功|已连接/);
});
test('read-only sales roles cannot add or edit account records',()=>{
 setup('sales',{[`${base}/accounts`]:[account]});const html=renderToStaticMarkup(React.createElement(panels.AccountsPage));assert.match(buttonMarkup(html,'新增账号'),/disabled=""/);assert.match(buttonMarkup(html,'编辑画像'),/disabled=""/);
});
// Catches accidental reintroduction of the long combined discovery/library page.
test('discovery opens prepared content categories and a lightweight reference entry point',()=>{
 setup('content');const html=renderToStaticMarkup(React.createElement(panels.DiscoveryPanel,{onPlan(){}}));
 assert.match(html,/role="tablist"/);assert.match(html,/<h2>内容灵感<\/h2>/);assert.match(html,/我的参考/);for(const label of ['专业内容','流量内容','信任内容','获客内容','转化内容'])assert.match(html,new RegExp(label));assert.doesNotMatch(html,/<form|type="search"|搜索发现|搜索记录|保存方向|登记作品链接|搜索相关内容/);
});
test('planning joins account and source and disables script generation until project facts are eligible',()=>{
 setup('content',{[`${base}/accounts`]:[account],[`${base}/opportunities`]:[opportunity],[`${base}/plans`]:[plan],'/api/projects/p-1/topics':[{id:'kitchen',title:'厨房动线',eligible:false,missing:['户型图'],angle:'动线'}]});const html=renderToStaticMarkup(React.createElement(panels.PlanningPanel,{onGenerate(){},focusPlanId:'plan-1'}));assert.match(html,/江边选房/);assert.match(html,/改善户型讨论/);assert.match(html,/2026-10-15/);assert.match(html,/户型图/);assert.match(buttonMarkup(html,'生成选题脚本'),/disabled=""/);
});
// Catches dropping the chosen discovery subject when entering planning.
test('planning opens an editable draft with the chosen content purpose and subject',()=>{
 setup('content');const html=renderToStaticMarkup(React.createElement(panels.PlanningPanel,{onGenerate(){},initialDraft:{title:'政策解读',angle:'从购房者问题出发',brief:'内容类型：专业内容\n核心作用：建立专业认知',topic_id:'tour'}}));
 assert.doesNotMatch(html,/role="dialog"/);
 assert.match(html,/政策解读/);assert.match(html,/从购房者问题出发/);assert.match(html,/专业内容/);
});
test('empty discovery does not manufacture reference works or an unrelated hot list',()=>{
 setup('content',{[`${base}/trends`]:{trends:[{title:'无关娱乐热搜'}]}});const html=renderToStaticMarkup(React.createElement(panels.DiscoveryPanel,{onPlan(){}}));
 assert.match(html,/这类内容正在整理/);assert.doesNotMatch(html,/无关娱乐热搜|href="https:/);
});
test('project platform sessions expose authenticated login controls while keeping profile records',()=>{
 setup('content',{[`${base}/accounts`]:[account],[`${base}/platform-accounts`]:[{platform:'douyin',name:'抖音',backend:'douyin',supported:true,loggedIn:true,verified:true,ready:true,note:''},{platform:'xiaohongshu',name:'小红书',backend:'xiaohongshu',supported:true,loggedIn:false,verified:false,ready:true,note:''}]});const html=renderToStaticMarkup(React.createElement(panels.AccountsPage));assert.match(html,/本项目独立保存/);assert.match(html,/校验账号/);assert.match(html,/退出登录/);assert.match(html,/<span>登录<\/span>/);assert.match(html,/真实楼盘科普/);assert.doesNotMatch(html,/本机登录参考|当前设备的 Easel/);
});
test('a plan with an existing generated content version exposes that saved script',()=>{
 setup('content',{[`${base}/plans`]:[{...plan,content_version_id:'c-1'}],'/api/projects/p-1/topics':[{id:'kitchen',title:'厨房动线',eligible:true,missing:[],angle:'动线'}]});const html=renderToStaticMarkup(React.createElement(panels.PlanningPanel,{onGenerate(){},onOpenContent(){},focusPlanId:'plan-1'}));assert.match(html,/打开已生成脚本/);
});

// Catches a read-only operator being allowed to create a source from the prepared catalog.
test('read-only discovery browses cached works without online collection controls',()=>{
 setup('sales');const html=renderToStaticMarkup(React.createElement(panels.DiscoveryPanel,{onPlan(){}}));assert.match(html,/专业内容/);assert.match(html,/我的参考/);assert.doesNotMatch(html,/搜索相关内容|保存方向|登记作品链接|<form/);
});
// Catches deep links opening an unfiltered list where the requested existing plan cannot be identified.
test('an opened plan is identified and focused even when other plans exist',()=>{
 setup('content',{[`${base}/plans`]:[plan,{...plan,id:'plan-2',title:'其他计划'}]});const html=renderToStaticMarkup(React.createElement(panels.PlanningPanel,{onGenerate(){},focusPlanId:'plan-1'}));assert.match(html,/已定位此计划/);assert.match(html,/data-plan-id="plan-1"/);
});
// Catches durable reference-only plans losing their original source and saved evidence after reload.
test('a saved breakdown source without opportunity fields exposes its original record safely',()=>{
 const reference={source_url:'https://www.bilibili.com/video/BVOriginal',title:'原片厨房动线',transcript:'00:00-00:03 <script>原片片段</script>',observations:'00:00-00:03 镜头从玄关转向厨房',target_seconds:30,topic_id:'kitchen'};
 const source={reference_analysis:{schema_version:1,reference,source:{url:reference.source_url,title:reference.title,watched:false,evidence_basis:'user_supplied_notes'},timeline:[{start_seconds:0,end_seconds:3,transcript:reference.transcript,visual:reference.observations,evidence_status:'user_supplied'}],structure:{gaps:[]},hook:{evidence:reference.transcript,status:'user_supplied',questions:[]},reuse_template:{id:'question',label:'问题引入',description:'先提出问题再实拍回答',techniques:['实拍动线'],status:'observed',mapping_note:'核对后复用结构'},observed_at:'2026-10-14T03:00:00Z',content_sha256:'a'.repeat(64),idempotency_key:'08ccddae-c1c0-4c14-bd2f-c98acfd52e5d',request_hash:'b'.repeat(64)}};
 setup('sales',{[`${base}/plans`]:[{...plan,opportunity_id:null,source}]});
 const html=renderToStaticMarkup(React.createElement(panels.PlanningPanel,{onGenerate(){},focusPlanId:'plan-1'}));
 assert.match(html,/原片厨房动线/);assert.match(html,/href="https:\/\/www\.bilibili\.com\/video\/BVOriginal"/);assert.match(html,/2026\/10\/14 11:00/);assert.match(html,/<summary>查看原片记录<\/summary>/);assert.match(html,/00:00-00:03 &lt;script&gt;原片片段&lt;\/script&gt;/);assert.match(html,/镜头从玄关转向厨房/);assert.match(html,/问题引入/);assert.match(html,/用户提供/);assert.match(html,/未自动观看原片/);assert.doesNotMatch(html,/<script>|来源链接不可用|内容计划已创建/);assert.match(buttonMarkup(html,'生成选题脚本'),/disabled=""/);
});
// Catches linking an unsafe URI inside a saved original record or replacing an existing opportunity source.
test('saved original records reject unsafe links and keep existing opportunity sources',()=>{
 const analysis={reference:{title:'用户原片标题',source_url:'javascript:alert(1)',transcript:'用户笔记',observations:''},source:{url:'javascript:alert(1)',title:'用户原片标题'},observed_at:'2026-10-14T03:00:00Z',reuse_template:{label:'问题引入'}};
 setup('content',{[`${base}/plans`]:[{...plan,source:{...opportunity,reference_analysis:analysis}}]});
 const html=renderToStaticMarkup(React.createElement(panels.PlanningPanel,{onGenerate(){},focusPlanId:'plan-1'}));
 assert.match(html,/改善户型讨论/);assert.match(html,/href="https:\/\/example\.com\/observed"/);assert.match(html,/用户笔记/);assert.doesNotMatch(html,/href="javascript:/);assert.match(html,/来源链接不可用/);
});
// Catches saved ASR and sampled frame evidence being mislabeled as manually supplied after a plan reload.
test('saved analyzed reference identifies the actual transcription and sampling basis',()=>{
 setup('content',{[`${base}/plans`]:[{...plan,source:{reference_analysis:{source:{url:opportunity.source_url,title:'原片分析',evidence_basis:'asset_asr_and_sampled_frames'},reference:{transcript:'00:00–00:02.5 实际转写',observations:'00:04 抽帧观察'}}}}]});
 const html=renderToStaticMarkup(React.createElement(panels.PlanningPanel,{onGenerate(){},focusPlanId:'plan-1'}));assert.match(html,/语音转写与采样画面/);assert.match(html,/不能代表逐帧观看/);assert.doesNotMatch(html,/以下口播与画面笔记由用户提供/);
});
