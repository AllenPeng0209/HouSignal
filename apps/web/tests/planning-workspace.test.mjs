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
let ui,directory;
before(async()=>{
 directory=await mkdtemp(join(tmpdir(),'planning-workspace-'));
 const helper=resolve('src/planning-model.ts');
 const result=await build({stdin:{contents:`export {PlanningPanel} from ${JSON.stringify(resolve('src/OperationsPages.tsx'))};export * from ${JSON.stringify(helper)};`,resolveDir:process.cwd(),loader:'tsx'},write:false,bundle:true,format:'esm',platform:'node',jsx:'automatic',external:['react','react-dom','react/jsx-runtime'],loader:{'.css':'empty'},plugins:[{name:'loaded-resources',setup(builder){
  builder.onResolve({filter:/^\.\/hooks$/},()=>({path:'hooks',namespace:'fixture'}));
  if(!existsSync(helper))builder.onResolve({filter:/planning-model\.ts$/},()=>({path:'missing',namespace:'fixture'}));
  builder.onLoad({filter:/.*/,namespace:'fixture'},({path})=>({loader:'js',contents:path==='missing'?`export const candidatePlan=()=>null,readPlanningSuggestions=()=>null,planningDraftState=()=>null;`:`export const useApp=()=>globalThis.planningApp;export const useResource=(path)=>({data:globalThis.planningData[path]??[],loading:false,error:'',retry(){}});export const useMutation=()=>({busy:false,error:'',setError(){},run:async()=>{}});export const useJob=()=>({job:null,busy:false,error:'',percent:0,statusText:'',wait:async()=>{},cancel(){},setJob(){},setError(){}});`}));
 }}]});
 const bundled=result.outputFiles[0].text.replaceAll('from "react"',`from ${JSON.stringify(pathToFileURL(resolve('node_modules/react/index.js')).href)}`).replaceAll('from "react/jsx-runtime"',`from ${JSON.stringify(pathToFileURL(resolve('node_modules/react/jsx-runtime.js')).href)}`);
 const target=join(directory,'ui.mjs');await writeFile(target,bundled);ui=await import(pathToFileURL(target).href);
});
after(async()=>{delete globalThis.planningApp;delete globalThis.planningData;await rm(directory,{recursive:true,force:true});});
const base='/api/projects/p/operations';
const reference={id:'ref-1',title:'从入户看家务动线',platform:'抖音',source_url:'https://example.com/reference',observed_at:'2026-10-14T01:00:00Z',curation_state:'shortlisted',tags:[],note:'看回家动线',research_notes:{},analysis_snapshot:{job_id:'analysis-1',summary:'从问题到实拍验证',techniques:[{id:'question',label:'问题引入',reason:'先问走路是否绕远',evidence_refs:['s1']}]},revision:2};
const skills=['topic-matrix','titles-and-hooks','topic-evaluation'].map(id=>({id,title:{'topic-matrix':'选题矩阵','titles-and-hooks':'标题与 Hook','topic-evaluation':'选题评分'}[id],status:'ready',layer:'plan',reason:'可使用'}));
function setup(role='content'){globalThis.planningApp={project:{id:'p',name:'江边住宅',channel:'抖音',audience:'改善家庭'},session:{role},refresh:0,notify(){}};globalThis.planningData={[`${base}/research/references`]:[reference],[`${base}/skills`]:skills,'/api/projects/p/topics':[{id:'layout',title:'户型动线',eligible:false,missing:['户型图']} ]};}
const render=(props={})=>renderToStaticMarkup(React.createElement(ui.PlanningPanel,{onGenerate(){},onDiscover(){},...props}));
function button(html,label){return [...html.matchAll(/<button\b[^>]*>[\s\S]*?<\/button>/g)].map(m=>m[0]).find(m=>m.includes(`<span>${label}</span>`))||'';}
// Catches making users reopen unrelated pages or complete a plan form before using reference evidence.
test('the default planner combines saved reference research, content goals and executable methods inline',()=>{
 setup();const html=render();assert.match(html,/role="tab"[^>]*aria-selected="true"[^>]*>策划台/);assert.match(html,/从入户看家务动线/);assert.match(html,/已拆解/);assert.match(html,/专业内容/);assert.match(html,/获客内容/);assert.match(html,/选题矩阵/);assert.match(html,/生成 3 个策划方向/);assert.doesNotMatch(html,/role="dialog"/);
});
// Catches losing the reference selected in discovery and pretending its metrics prove success.
test('a reference draft opens the same planner with the actual selected evidence and no fabricated performance score',()=>{
 setup();const html=render({initialDraft:{title:reference.title,angle:'借鉴开场',brief:'用户笔记',topic_id:'layout',reference_id:'ref-1',content_type:'trust',subject:'看房服务',start_mode:'reference'}});assert.match(html,/问题引入/);assert.match(html,/从问题到实拍验证/);assert.match(html,/aria-pressed="true"[^>]*>[\s\S]*?信任内容/);assert.doesNotMatch(html,/爆款概率|成功率|92%|role="dialog"/);
});
test('read-only project users can inspect inspiration but cannot run methods or save plans',()=>{
 setup('sales');const html=render();assert.match(button(html,'生成 3 个策划方向'),/disabled=""/);assert.match(html,/当前角色可查看/);
});
// Catches dropping structured content goals or applying an unrelated reference when adopting a candidate.
test('adopting a candidate preserves the exact request context and structured hook, outline and method provenance',()=>{
 const request={content_type:'acquisition',subject:'房源推荐',topic_id:'tour',channel:'小红书',account_id:'a',reference_id:'ref-1',skill_ids:['topic-matrix','titles-and-hooks'],target_seconds:45,intent:'帮助改善家庭选房'};
 const candidate={id:'candidate-2',title:'回家后的三分钟',angle:'从入户看生活需求',hook:'回家第一步，会不会绕路？',outline:['从玄关出发','核对户型动线'],cta:'留下你家的需求',topic_id:'layout',why:'对应改善需求',missing:['补入户实拍'],reference_note:'借鉴问题引入'};
 const result=ui.candidatePlan(request,candidate,'job-1');assert.equal(result.reference_id,'ref-1');assert.equal(result.account_id,'a');assert.equal(result.topic_id,'layout');assert.equal(result.channel,'小红书');assert.deepEqual(result.planning,{content_type:'acquisition',subject:'房源推荐',target_seconds:45,skill_ids:['topic-matrix','titles-and-hooks'],hook:candidate.hook,outline:['从玄关出发','核对户型动线'],cta:candidate.cta,suggestion_job_id:'job-1',candidate_id:'candidate-2'});assert.match(result.brief,/补入户实拍/);
});
// Catches accepting failed/malformed model replies as real directions or restoring an incomplete job context.
test('only successful structured suggestions with their submitted context can be restored',()=>{
 assert.equal(ui.readPlanningSuggestions({status:'failed',result:{planning_suggestions:{candidates:[]}}}),null);
 assert.equal(ui.readPlanningSuggestions({status:'succeeded',result:{planning_suggestions:{candidates:[{title:'假结果'}]}}}),null);
});
// Catches losing the server-owned reference snapshot after a plan reload.
test('saved planning references retain their source and evidence basis in the plan queue',()=>{
 setup();globalThis.planningData[`${base}/plans`]=[{id:'p-1',title:'回家第一步',angle:'看动线',channel:'抖音',topic_id:'layout',status:'draft',account_id:null,scheduled_for:null,brief:'',reference_id:'ref-1',planning:{},source:{planning_reference:{reference_id:'ref-1',revision:2,title:'从入户看家务动线',source_url:'https://example.com/reference',evidence_basis:'title_only',research_notes:{},analysis:null}}}];
 const html=render({focusPlanId:'p-1'});assert.match(html,/从入户看家务动线/);assert.match(html,/href="https:\/\/example.com\/reference"/);assert.match(html,/尚未分析原片/);
});

// Catches silently inheriting a short-video default when a fresh content plan needs a useful long-form explanation.
test('a fresh planner starts at five minutes while explicit short and long draft durations survive',()=>{
 assert.equal(ui.planningDraftState().target_seconds,300);
 for(const seconds of [30,45,60,300,480,600])assert.equal(ui.planningDraftState({title:'一个真实问题',angle:'解释取舍',brief:'',topic_id:'tour',target_seconds:seconds}).target_seconds,seconds);
 setup();const html=render();assert.match(html,/<option value="300" selected="">5 分钟/);
 for(const seconds of [30,45,60,480,600])assert.match(html,new RegExp(`<option value="${seconds}"`));
 assert.match(html,/6 章/);assert.match(html,/30–45 镜/);assert.match(html,/预算/);
});

// Catches long-form jobs being discarded on restore even though their complete structured reply is valid.
test('structured suggestions restore targets up to ten minutes and reject targets outside the supported interval',()=>{
 const context={content_type:'professional',subject:'户型分析',topic_id:'layout',channel:'抖音',account_id:null,reference_id:null,skill_ids:['topic-matrix'],target_seconds:300,intent:'解释真实取舍'};
 const candidates=[1,2,3].map(n=>({id:`c-${n}`,title:`第 ${n} 个方向`,angle:'解释动线',hook:'从一个问题开始',outline:['提出问题','核对证据','总结取舍'],cta:'核对自己的需求',topic_id:'layout',why:'对应受众问题',missing:[],reference_note:''}));
 const job=seconds=>({id:'j-long',status:'succeeded',result:{planning_suggestions:{context:{...context,target_seconds:seconds},candidates}}});
 for(const seconds of [15,30,45,60,300,480,600])assert.equal(ui.readPlanningSuggestions(job(seconds))?.context.target_seconds,seconds);
 for(const seconds of [14,61,120,299,601,300.5])assert.equal(ui.readPlanningSuggestions(job(seconds)),null);
});

// Catches confusing execution budgets with fixed rules or displaying a long target as an opaque number of seconds.
test('duration helpers give readable labels and advisory chapter and shot budgets',()=>{
 assert.equal(typeof ui.planningDurationLabel,'function');assert.equal(typeof ui.planningDurationBudget,'function');
 for(const [seconds,label,budget] of [[30,'30 秒',{chapters:3,shotsMin:3,shotsMax:6}],[300,'5 分钟',{chapters:6,shotsMin:30,shotsMax:45}],[480,'8 分钟',{chapters:6,shotsMin:48,shotsMax:64}],[600,'10 分钟',{chapters:8,shotsMin:60,shotsMax:80}]]){
  assert.equal(ui.planningDurationLabel(seconds),label);assert.deepEqual(ui.planningDurationBudget(seconds),budget);
 }
 assert.equal(ui.planningDurationLabel(330),'5 分 30 秒');
});

// Catches presenting an unprocessed reference as observed proof of its hook, pacing or success.
test('an unanalysed reference is explicitly a structure hypothesis awaiting validation',()=>{
 setup();globalThis.planningData[`${base}/research/references`]=[{...reference,analysis_snapshot:{},note:'先提问再验证'}];
 const html=render({initialDraft:{title:reference.title,angle:'借鉴结构',brief:'',topic_id:'layout',reference_id:'ref-1',start_mode:'reference'}});
 const evidence=html.split('class="planning-reference-evidence"')[1].split('</aside>')[0];
 assert.match(evidence,/结构假设/);assert.match(evidence,/待验证/);assert.match(evidence,/尚不能判断原片开场与节奏/);
 assert.doesNotMatch(evidence,/已拆解|实测爆款|爆款概率|成功率|已观看/);
});
test('unsupported medium durations do not enter a new planning request',()=>{
 for(const target_seconds of [61,120,299])assert.equal(ui.planningDraftState({title:'待调整时长',angle:'',brief:'',topic_id:'tour',target_seconds}).target_seconds,300);
});
