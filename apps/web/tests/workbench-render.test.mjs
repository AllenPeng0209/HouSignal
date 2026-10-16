import {test,before,after} from 'node:test';
import assert from 'node:assert/strict';
import {existsSync} from 'node:fs';
import {mkdtemp,rm,writeFile} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import {join,resolve} from 'node:path';
import {pathToFileURL} from 'node:url';
import {build} from 'esbuild';
import React from 'react';
import {renderToStaticMarkup} from 'react-dom/server';

let panels,directory;
before(async()=>{
 directory=await mkdtemp(join(tmpdir(),'housignal-workbench-'));
 const result=await build({stdin:{contents:`export {Dashboard,Stats} from './ProjectPages';export {Reports} from './Reports';export {Workbench} from './Workbench';export {saveWorkspaceLocation} from './workspace-navigation';`,resolveDir:resolve('src'),loader:'tsx'},write:false,bundle:true,format:'esm',platform:'node',jsx:'automatic',external:['react','react-dom','react/jsx-runtime'],loader:{'.css':'empty'},plugins:[{name:'loaded-http-resources',setup(builder){
  if(!existsSync(resolve('src/Workbench.tsx')))builder.onResolve({filter:/^\.\/Workbench$/},()=>({path:'missing-workbench',namespace:'fixture'}));
  builder.onResolve({filter:/^\.\/hooks$/},()=>({path:'loaded-hooks',namespace:'fixture'}));
  builder.onLoad({filter:/.*/,namespace:'fixture'},({path})=>({loader:'js',contents:path==='missing-workbench'?'export const Workbench=()=>null;':`export const useApp=()=>globalThis.workbenchApp;export const useResource=(path)=>({data:globalThis.workbenchData[path]??null,loading:false,error:'',retry:()=>{},...globalThis.workbenchStates[path]});export const useMutation=()=>({busy:false,error:'',setError(){},run:async()=>{}});export const useJob=()=>({job:null,busy:false,error:'',percent:0,statusText:'',wait:async()=>undefined,cancel(){},setJob(){},setError(){}});`}));
 }}]});
 const bundled=result.outputFiles[0].text.replaceAll('from "react"',`from ${JSON.stringify(pathToFileURL(resolve('node_modules/react/index.js')).href)}`).replaceAll('from "react/jsx-runtime"',`from ${JSON.stringify(pathToFileURL(resolve('node_modules/react/jsx-runtime.js')).href)}`);
 const target=join(directory,'panels.mjs');await writeFile(target,bundled);panels=await import(pathToFileURL(target).href);
});
after(async()=>{delete globalThis.workbenchApp;delete globalThis.workbenchData;delete globalThis.workbenchStates;if(directory)await rm(directory,{recursive:true,force:true});});
const project={id:'p-1',name:'云境·壹号',city:'上海',property_type:'住宅',completeness:{percent:86,completed:6,total:7,items:[{key:'assets',label:'项目素材',complete:false,detail:'待补充',target:'assets'}]}};
const metrics={published:4,inquiries:12,qualified:7,appointments:5,visits:2,views:null,unknown_rate:25};
const overview={project,metrics,pending_inquiries:[],content_versions:[],publications:[],proposals:[]};
const report={metrics:{views:1200,likes:0,comments:null,saves:null,inquiries:null,qualified:3,window_complete:true,observed_publications:4,total_publications:4},publications:[],proposals:[],window:'D7'};
function setup({summary=overview,analysis=report,states={}}={}){
 globalThis.workbenchApp={project,projects:[project],canEdit:true,canReview:true,canSales:true,canManage:true,navigationTarget:{},rememberNavigation(){},navigate(){},openReadiness(){},session:{role:'owner',user:{id:'user-workbench',name:'测试负责人',email:'owner@example.test'},organization:{id:'org-workbench',name:'测试组织'}},refresh:0,reload:async()=>{},notify(){},citation(){},openProject(){},logout:async()=>{}};
 globalThis.workbenchData={'/api/projects/p-1/overview':summary,'/api/projects/p-1/reports?window=D7':analysis};globalThis.workbenchStates=states;
}
const render=(Component,props)=>renderToStaticMarkup(React.createElement(Component,props));

// A broken tab branch would put the wrong body under the selected accessible tab.
test('workbench exposes one selected overview tab and an analysis drill-down within the same workspace',()=>{
 setup();const html=render(panels.Workbench,{view:'overview'});assert.match(html,/role="tab"[^>]*aria-selected="true"[^>]*>项目总览/);assert.match(html,/role="tab"[^>]*aria-selected="false"[^>]*>效果分析/);assert.match(html,/role="tabpanel"[^>]*aria-labelledby="workbench-tab-overview"/);assert.match(html,/项目进展/);assert.doesNotMatch(html,/已核实发布与指标回填/);
});
test('analysis alias displays the analysis body and selected analysis tab',()=>{
 setup();const html=render(panels.Workbench,{view:'analysis'});assert.match(html,/role="tab"[^>]*aria-selected="true"[^>]*>效果分析/);assert.match(html,/role="tabpanel"[^>]*aria-labelledby="workbench-tab-analysis"/);assert.match(html,/已核实发布与指标回填/);assert.doesNotMatch(html,/需要承接的咨询/);
});
// Substituting appointment count for completed visits is a plausible sales reporting regression.
test('overview core metrics distinguish completed visits from scheduled appointments',()=>{
 setup();const html=render(panels.Dashboard);assert.match(html,/<span class="stat-label">完成带看<\/span><strong class="stat-value">2<\/strong>/);assert.doesNotMatch(html,/<span class="stat-label">预约到访<\/span>/);assert.match(html,/累计预约[\s\S]*?5/);assert.match(html,/按实际销售阶段记录/);
});
test('overview preserves unobserved D7 values while rendering observed zero honestly',()=>{
 setup();const html=render(panels.Dashboard);assert.match(html,/发布后 D7/);assert.match(html,/作品发布后的累计表现/);assert.match(html,/<dt>点赞量<\/dt><dd>0<\/dd>/);assert.match(html,/<dt>内容咨询<\/dt><dd>待观察<\/dd>/);assert.match(html,/1,200/);assert.doesNotMatch(html,/最近7天|近 7 天/);
});
test('overview limits actionable suggestions and excludes already approved suggestions',()=>{
 const proposal=(id,title,status='pending')=>({id,title,status,reason:`${title}依据`,source_refs:[],created_at:'2026-10-14T01:00:00Z'});
 setup({analysis:{...report,proposals:[proposal('approved','已完成建议','approved'),proposal('1','第一个待办'),proposal('2','第二个待办'),proposal('3','第三个待办')]}});const html=render(panels.Dashboard);assert.match(html,/第一个待办/);assert.match(html,/第二个待办/);assert.doesNotMatch(html,/第三个待办|已完成建议/);assert.match(html,/进入效果分析/);assert.doesNotMatch(html,/确认采纳/);
});
test('analysis reports partial observation without claiming every indicator is complete',()=>{
 setup();const html=render(panels.Reports);assert.match(html,/发布后 D7/);assert.doesNotMatch(html,/观察窗口已完整回填/);assert.match(html,/仍可能有指标待观察/);assert.match(html,/累计预约/);assert.match(html,/完成带看/);
});
test('failed project-sales resource is visible in analysis instead of an endless loader',()=>{
 setup({summary:null,states:{'/api/projects/p-1/overview':{data:null,loading:false,error:'销售记录暂不可用'}}});const html=render(panels.Reports);assert.match(html,/role="alert"[^>]*>[\s\S]*?销售记录暂不可用/);assert.doesNotMatch(html,/正在读取项目记录/);
});
test('empty project sales stages render zero-width bars instead of invented progress',()=>{
 setup({summary:{...overview,metrics:{...metrics,inquiries:0,qualified:0,appointments:0,visits:0}}});const html=render(panels.Reports);const funnel=html.match(/<div class="funnel">([\s\S]*?)<\/div><p class="form-note">/)?.[1];assert.ok(funnel);assert.equal([...funnel.matchAll(/width:0%/g)].length,4);assert.doesNotMatch(funnel,/width:8%/);
});
test('overview surfaces a failed performance resource without hiding project progress',()=>{
 setup({analysis:null,states:{'/api/projects/p-1/reports?window=D7':{data:null,loading:false,error:'发布表现暂不可用'}}});const html=render(panels.Dashboard);assert.match(html,/role="alert"[^>]*>[\s\S]*?发布表现暂不可用/);assert.match(html,/项目进展/);assert.doesNotMatch(html,/第一个待办/);
});

// A stored record target is required before offering a continuation action.
test('overview offers continuation only when this user and project have a saved content target',()=>{
 setup();assert.doesNotMatch(render(panels.Workbench),/继续上次编辑/);
 panels.saveWorkspaceLocation({userId:'other-user',organizationId:'org-workbench',projectId:'p-1'},'studio',{contentId:'cv-private',studioStage:'script'});
 assert.doesNotMatch(render(panels.Workbench),/继续上次编辑/);
 panels.saveWorkspaceLocation({userId:'user-workbench',organizationId:'org-workbench',projectId:'p-1'},'studio',{contentId:'cv-resume',studioStage:'script'});
 assert.match(render(panels.Workbench),/继续上次编辑/);
});
