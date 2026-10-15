/** Built full React app, isolated HTTP fixtures. No platform search, live data, or real model calls. */
import assert from 'node:assert/strict';
import {createRequire} from 'node:module';
import {mkdir,writeFile} from 'node:fs/promises';
import {resolve} from 'node:path';

const require=createRequire(import.meta.url);
const {chromium}=require(process.env.HS_PLAYWRIGHT_MODULE||'/Users/pengyanlun/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright');
const appUrl=process.env.HS_PLANNING_QA_URL||'http://127.0.0.1:8017/';
const output=resolve(process.env.HS_PLANNING_QA_OUTPUT||'../../reports/content-planning-redesign-20261014');
const browser=await chromium.launch({headless:true,channel:process.env.HS_BROWSER_CHANNEL||'chrome'});
const at='2026-10-14T14:00:00Z',pid='planning-a',otherPid='planning-b';
const methodIds=['topic-matrix','titles-and-hooks','topic-evaluation','series-planning','cross-platform-reuse'];
const methodLabels=['选题矩阵','标题与开场','选题评估','系列规划','平台适配'];
const topicNames={tour:'从面积走进真实生活',space:'空间尺度',light:'采光景观',layout:'户型动线',kitchen:'厨房细节'};
const project=id=>({id,name:id===pid?'江岸生活 · 策划验收':'独立项目 · 空白策划',city:'杭州',property_type:'住宅',channel:'抖音',owner_name:'验收负责人',audience:'有孩子的改善家庭',sales_owner_name:'验收负责人',marketing_confirmed:true,marketing_evidence:'isolated HTTP fixture',is_test:true,completeness:{percent:100,completed:7,total:7,items:[]}});
const video={platform:'bilibili',content_id:'BVplanningfixture',title:'一套三房，为什么家务动线比面积更重要？',url:'https://www.bilibili.com/video/BVplanningfixture',author:'验收参考账号',subject:'户型分析',job_id:'source-job',snippet:'从回家、备餐、洗衣三个生活场景解释动线。',published_at:at,metrics:{views:1500,likes:120,comments:8,saves:null,shares:null},metrics_observed_at:at,metrics_source:'isolated HTTP fixture',match_keywords:['户型分析'],qualification:'candidate_unverified',historical:false};
const reference=(id,item=video)=>({id,title:item.title,platform:'B站',source_url:item.url,source_snapshot:item,summary:'用问题引入，再以三个真实生活场景给出依据。',observed_at:at,status:'saved',curation_state:'shortlisted',tags:['户型','改善家庭'],note:'借鉴提问和生活动线，不套用原片项目数据。',research_notes:{transcript:'00:00–00:03 家务动线，为什么比面积更重要？',observations:'00:04 玄关转向厨房',topic_id:'layout',target_seconds:30},analysis_snapshot:{job_id:'analysis-fixture',summary:'问题开场 → 生活场景 → 实拍验证 → 用户自查。',techniques:[{id:'question',label:'问题引入',reason:'先回应改善家庭关心的问题',evidence_refs:['transcript-1']}],coverage:{note:'隔离验收转写与采样画面'}},revision:1});
const candidates=()=>[
 {id:'candidate-1',title:'三房不够用？先看这三条家务动线',angle:'用家庭的一天串起户型动线',hook:'同样是三房，为什么有的住起来总在绕路？',outline:['先看回家放东西的路线','再看备餐与餐厅的距离','最后核对洗衣晾晒的路径'],cta:'下次看房，把你的日常路线走一遍。',topic_id:'layout',why:'把抽象面积转成改善家庭可感知的使用体验。',missing:['项目户型图','三处真实动线镜头'],reference_note:'借鉴原片提问和生活场景递进结构'},
 {id:'candidate-2',title:'看房时，把一家人的早晨走一遍',angle:'以早晨高频场景核对空间使用',hook:'一家人同时出门，这套房会不会堵在一起？',outline:['卧室到洗漱区','厨房到餐厅','玄关到出门'],cta:'保存这份动线自查清单。',topic_id:'layout',why:'把户型点评变成可操作的看房方法。',missing:['早晨场景实拍'],reference_note:'保留场景推演的方法，重新设计项目表达'},
 {id:'candidate-3',title:'面积之外，改善家庭真正该比较什么',angle:'用相同标准比较生活便利性',hook:'多十平方米，就一定更好住吗？',outline:['列出家庭最常走的路线','比较路线交叉和绕行','核对收纳与家务分工'],cta:'先写下家庭需求，再去比较户型。',topic_id:'layout',why:'建立专业认知，并帮助用户形成比较标准。',missing:[],reference_note:'只借鉴对比结构'}
];
function fixture({role='owner',empty=false,failFirst=false}={}){
 const state={role,empty,failFirst,holdJobs:false,jobs:[],plans:[],references:empty?[]:[reference('reference-existing',{...video,content_id:'BVexistingfixture',url:'https://www.bilibili.com/video/BVexistingfixture',title:'已经收藏的生活场景参考'})],contents:[],requests:[],mutations:[],unexpected:[],pageErrors:[],jobPolls:new Map(),nextJob:1,nextPlan:1};
 if(!empty)state.plans.push({id:'legacy-plan',title:'旧版本保留的选题计划',angle:'保留原有创作角度',brief:'旧计划与新版并存',topic_id:'tour',channel:'抖音',account_id:null,reference_id:null,opportunity_id:null,status:'draft',scheduled_for:null,planning:{},source:{},created_at:at});
 state.accounts=[{id:'account-xhs',name:'江岸生活笔记',platform:'小红书',status:'managed',audience:'改善家庭',positioning:'真实生活场景'},{id:'account-douyin',name:'江岸实拍看房',platform:'抖音',status:'managed',audience:'本地改善客群'}];
 state.addJob=(context,{failed=false,kind='content_planning',id}={})=>{
  const job={id:id||`planning-job-${state.nextJob++}`,kind,status:'queued',progress:0,phase:'等待策划',created_at:at,updated_at:at,context,willFail:failed,result:kind==='content_planning'?{planning_suggestions:{context,candidates:candidates()}}:{}};
  state.jobs.unshift(job);return job;
 };
 return state;
}
async function install(page,state){
 page.on('pageerror',error=>state.pageErrors.push(error.message));
 await page.route('**/*',async route=>{
  const request=route.request(),url=new URL(request.url()),path=url.pathname,method=request.method();
  if(url.origin!==new URL(appUrl).origin)return route.abort();
  if(!path.startsWith('/api/'))return route.continue();
  let body=null;try{body=request.postDataJSON();}catch{}
  const log={path,method,body};state.requests.push(log);if(method!=='GET')state.mutations.push(log);
  const respond=(value,status=200)=>route.fulfill({status,contentType:'application/json',body:JSON.stringify(value)});
  if(path==='/api/auth/me')return respond({user:{id:'qa',name:'策划验收',email:'qa@example.invalid'},organization:{id:'qa-org',name:'隔离验收工作区'},role:state.role});
  if(path==='/api/projects')return respond([project(pid),project(otherPid)]);
  if(path==='/api/model/status')return respond({configured:true,provider:'fixture',model:'fixture'});
  const jobMatch=path.match(/^\/api\/jobs\/([^/]+)(?:\/(events|cancel))?$/);
  if(jobMatch){
   const job=state.jobs.find(value=>value.id===jobMatch[1]);if(!job)return respond({detail:'Job fixture absent'},404);
   if(jobMatch[2]==='events')return respond({detail:'SSE disabled to exercise polling'},503);
   if(jobMatch[2]==='cancel'){job.status='cancelled';return respond(job);}
   const polls=(state.jobPolls.get(job.id)||0)+1;state.jobPolls.set(job.id,polls);
   if(!state.holdJobs&&polls>1){job.status=job.willFail?'failed':'succeeded';job.progress=1;job.error=job.willFail?'隔离验收：策划服务暂时失败':'';job.phase=job.willFail?'生成失败':'策划已完成';}
   else{job.status='running';job.progress=.4;job.phase='结合参考与所选 Skills 展开方向';}
   return respond(job);
  }
  const match=path.match(/^\/api\/projects\/([^/]+)\/(.+)$/);
  if(match){
   const [,id,resource]=match,first=id===pid,base=`/api/projects/${id}`;
   if(resource==='overview')return respond({project:project(id),metrics:{published:0,inquiries:0,qualified:0,appointments:0,visits:0,views:null,unknown_rate:null},pending_inquiries:[],content_versions:[],publications:[],proposals:[]});
   if(resource==='reports')return respond({window:'D7',metrics:{views:null,likes:null,comments:null,saves:null,inquiries:null,qualified:null,window_complete:false,observed_publications:0,total_publications:0},publications:[],proposals:[]});
   if(resource==='topics')return respond(Object.entries(topicNames).map(([id,title])=>({id,title,angle:'依据当前项目事实',eligible:true,missing:[],fact_refs:[]})));
   if(resource==='jobs')return respond(first?state.jobs:[]);
   if(resource==='contents')return respond(first?state.contents:[]);
   if(['assets','publications','conversations','operations/opportunities'].includes(resource))return respond([]);
   if(resource==='operations/accounts')return respond(first?state.accounts:[]);
   if(resource==='operations/skills')return respond(methodIds.map((id,index)=>({id,title:methodLabels[index],status:'ready',reason:''})));
   if(resource==='operations/research/catalog')return respond({categories:[{id:'professional',label:'专业内容',purpose:'用生活场景和真实依据，帮助用户理解户型。',subjects:[{label:'户型点评',query:'户型分析'}],items:first&&!state.empty?[video]:[],updated_at:at,collection_state:'ready'}],total:first&&!state.empty?1:0,updated_at:at});
   if(resource==='operations/research/references'&&method==='GET')return respond(first?state.references:[]);
   if(resource==='operations/research/references'&&method==='POST'){
    assert.equal(first,true);assert.deepEqual(body,{job_id:'source-job',platform:'bilibili',content_id:'BVplanningfixture'});
    const saved=reference('reference-captured');state.references.push(saved);return respond(saved,201);
   }
   if(resource.startsWith('operations/research/references/')&&method==='GET')return respond(state.references.find(value=>value.id===resource.split('/').at(-1))||{});
   if(resource==='operations/planning/suggest'&&method==='POST'){
    assert.equal(first,true);const failed=state.failFirst;state.failFirst=false;return respond({job_id:state.addJob(body,{failed}).id},202);
   }
   if(resource==='operations/plans'&&method==='GET')return respond(first?state.plans:[]);
   if(resource==='operations/plans'&&method==='POST'){
    const value={...body,id:`plan-${state.nextPlan++}`,content_version_id:null,created_at:at,source:{planning:body.planning}};state.plans.unshift(value);return respond(value,201);
   }
   if(resource.startsWith('operations/plans/')&&method==='PATCH'){
    const value=state.plans.find(value=>value.id===resource.split('/').at(-1));assert.ok(value);Object.assign(value,body,{source:{planning:body.planning}});return respond(value);
   }
   if(resource==='contents/generate'&&method==='POST'){
    const plan=state.plans.find(value=>value.id===body.plan_id);assert.ok(plan,'script generation must refer to a saved plan');
    const account=state.accounts.find(value=>value.id===plan.account_id),channel=account?.platform||plan.channel;
    const content={id:'content-generated',series_id:'series-fixture',version:1,topic:plan.title,script:`${plan.planning?.hook||plan.angle}\n基于已保存的策划大纲与项目事实。`,status:'draft',channel,cover_title:plan.title,aspect_ratio:'9:16',subtitle_style:'minimal',storyboard:[],fact_refs:[],created_at:at};state.contents=[content];plan.content_version_id=content.id;
    const job=state.addJob(null,{kind:'generate_content',id:'script-job'});job.result={content_version_id:content.id};return respond({job_id:job.id},202);
   }
   state.unexpected.push(`${method} ${base}/${resource}`);
  }else state.unexpected.push(`${method} ${path}`);
  return respond({detail:'Unexpected isolated fixture request'},404);
 });
}
const records=[],selectedScenarios=new Set((process.env.HS_PLANNING_QA_SCENARIOS||'').split(',').filter(Boolean));
await mkdir(output,{recursive:true});
async function shot(page,name){await page.evaluate(()=>window.scrollTo(0,0));await page.screenshot({path:resolve(output,`${name}.png`),fullPage:page.viewportSize().width>500,animations:'disabled'});}
async function fits(page,label){assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=window.innerWidth),`${label}: horizontal overflow`);}
async function studio(page){
 const nav=page.getByRole('navigation',{name:'主导航',exact:true});
 await nav.waitFor({state:'attached'});
 if(!await nav.isVisible())await page.getByRole('button',{name:'打开导航',exact:true}).click();
 await nav.getByRole('button',{name:'内容工作台',exact:true}).click();await page.locator('.research-workspace').waitFor();
}
async function planning(page){await page.locator('.studio-steps').getByRole('button',{name:'2 策划',exact:true}).click();await page.locator('.planning-workspace').waitFor();}
async function waitCandidates(page){await page.locator('.planning-candidate').nth(2).waitFor();assert.equal(await page.locator('.planning-candidate').count(),3);}
const planEditor=page=>page.getByRole('dialog',{name:/采用并完善策划|编辑内容计划/});
async function run(name,options,action){
 if(selectedScenarios.size&&!selectedScenarios.has(name))return;
 const state=fixture(options),context=await browser.newContext({viewport:{width:1440,height:1120}}),page=await context.newPage();page.setDefaultTimeout(12000);await install(page,state);
 const result={scenario:name,passed:false,checks:[]};records.push(result);
 try{await page.goto(appUrl,{waitUntil:'domcontentloaded'});await studio(page);await action(page,state,result.checks);assert.deepEqual(state.unexpected,[]);assert.deepEqual(state.pageErrors,[]);result.passed=true;}
 catch(error){result.error=error.message;result.pageErrors=state.pageErrors;result.unexpected=state.unexpected;await shot(page,`${name}-failure`).catch(()=>{});console.error(`${name}: ${error.message}`);}
 finally{result.requestCount=state.requests.length;result.mutations=state.mutations;await context.close();}
}

try{
 await run('complete-flow',{},async(page,state,checks)=>{
  await page.getByRole('button',{name:'户型点评',exact:true}).click();await page.getByRole('button',{name:'按此选题策划',exact:true}).click();
  await page.locator('.planning-workspace').waitFor();assert.equal(await page.getByRole('button',{name:'内容类别',exact:true}).getAttribute('aria-pressed'),'true');assert.equal(await page.getByLabel('具体方向').inputValue(),'户型分析');assert.equal(await page.getByRole('dialog').count(),0);checks.push('category handoff opens inline planning with its selected purpose and subject');
  await page.locator('.studio-steps').getByRole('button',{name:'1 发现',exact:true}).click();
  await page.locator(`article[data-content-key="bilibili:${video.content_id}"]`).getByRole('button',{name:'用这条策划',exact:true}).click();await page.locator('.planning-workspace').waitFor();
  assert.equal(await page.getByRole('button',{name:'参考视频',exact:true}).getAttribute('aria-pressed'),'true');assert.equal(await page.getByLabel('具体方向').inputValue(),'户型分析');assert.equal(await page.locator('.planning-reference-card.selected').count(),1);assert.match(await page.locator('.planning-reference-evidence').innerText(),/问题开场|问题引入/);checks.push('video is captured once and its persisted source and analysis enter planning');
  await page.getByLabel('给哪个账号做').selectOption('account-xhs');await page.getByLabel('预计时长').selectOption('45');
  await page.locator('.planning-method-list').getByRole('checkbox',{name:/选题评估/}).uncheck();await page.locator('.planning-method-list').getByRole('checkbox',{name:/系列规划/}).check();await page.getByRole('textbox',{name:'策划想法与补充要求',exact:true}).fill('给有孩子的改善家庭看，以真实生活动线讲户型。');
  await fits(page,'desktop planning');await shot(page,'planning-desktop');
  await page.getByRole('button',{name:'生成 3 个策划方向',exact:true}).click();await waitCandidates(page);
  const suggestion=state.mutations.find(value=>value.path.endsWith('/planning/suggest'));
  assert.deepEqual(suggestion.body,{content_type:'professional',subject:'户型分析',topic_id:'layout',channel:'小红书',account_id:'account-xhs',reference_id:'reference-captured',skill_ids:['topic-matrix','titles-and-hooks','series-planning'],intent:'给有孩子的改善家庭看，以真实生活动线讲户型。',target_seconds:45});
  assert.ok(state.jobPolls.get('planning-job-1')>=2);checks.push('selected Skills, account platform, duration, category and durable reference form the exact suggest payload; polling yields three candidates');await shot(page,'candidates-desktop');
  await page.setViewportSize({width:375,height:920});await fits(page,'mobile candidates');await shot(page,'candidates-mobile');await page.locator('.planning-candidate').first().scrollIntoViewIfNeeded();await page.screenshot({path:resolve(output,'candidates-mobile-detail.png'),animations:'disabled'});await page.setViewportSize({width:1440,height:1120});
  await page.locator('.planning-candidate').first().getByRole('button',{name:'采用这个方向',exact:true}).click();
  const editor=planEditor(page);await editor.getByLabel('开场 Hook').fill('同样三房，为什么这套能少走很多弯路？');await shot(page,'adopt-editor-desktop');await editor.getByRole('button',{name:'保存计划',exact:true}).click();await page.locator('[data-plan-id="plan-1"]').waitFor();
  const saved=state.plans.find(value=>value.id==='plan-1');assert.equal(saved.reference_id,'reference-captured');assert.equal(saved.channel,'小红书');assert.equal(saved.planning.hook,'同样三房，为什么这套能少走很多弯路？');assert.equal(saved.planning.suggestion_job_id,'planning-job-1');assert.equal(saved.planning.candidate_id,'candidate-1');assert.deepEqual(saved.planning.skill_ids,['topic-matrix','titles-and-hooks','series-planning']);checks.push('adoption allows hook edits and saves source, structured planning, method IDs and suggestion provenance');
  await page.locator('[data-plan-id="plan-1"]').getByRole('button',{name:'编辑计划',exact:true}).click();
  assert.equal(await planEditor(page).getByRole('button',{name:'刷新参考资料',exact:true}).count(),1,'structured existing plans need a refresh-source recovery action');
  await planEditor(page).getByRole('button',{name:'刷新参考资料',exact:true}).click();await planEditor(page).getByRole('button',{name:'保存计划',exact:true}).click();await page.locator('[data-plan-id="plan-1"]').waitFor();
  const refresh=state.mutations.filter(value=>value.method==='PATCH'&&value.path.endsWith('/plans/plan-1')).at(-1);assert.equal(refresh.body.reference_id,'reference-captured');assert.equal(refresh.body.planning.suggestion_job_id,undefined);assert.equal(refresh.body.planning.candidate_id,undefined);checks.push('refreshing an existing structured source resends its reference identity and detaches outdated suggestion provenance');
  await page.locator('[data-plan-id="legacy-plan"]').getByRole('button',{name:'编辑计划',exact:true}).click();assert.equal(await planEditor(page).getByRole('button',{name:'刷新参考资料',exact:true}).count(),0);await planEditor(page).getByLabel('选题标题').fill('旧计划仍可继续编辑');await planEditor(page).getByRole('button',{name:'保存计划',exact:true}).click();await page.getByRole('heading',{name:'旧计划仍可继续编辑',exact:true}).waitFor();checks.push('legacy planning:{} records edit safely without a structured-source refresh control');
  await page.getByRole('tab',{name:'策划台',exact:true}).click();await page.locator('.planning-candidate').nth(1).getByRole('button',{name:'采用这个方向',exact:true}).click();await planEditor(page).getByLabel('关联账号').selectOption('account-douyin');await planEditor(page).getByLabel('目标时长').fill('30');await planEditor(page).getByRole('button',{name:'保存计划',exact:true}).click();await page.locator('[data-plan-id="plan-2"]').waitFor();
  const changed=state.mutations.filter(value=>value.method==='POST'&&value.path.endsWith('/plans')).at(-1);assert.equal(changed.body.account_id,'account-douyin');assert.equal(changed.body.channel,'抖音');assert.equal(changed.body.planning.target_seconds,30);assert.equal(changed.body.planning.suggestion_job_id,undefined);assert.equal(changed.body.planning.candidate_id,undefined);checks.push('changing account and duration on a fresh candidate detaches its own suggestion provenance and remains saveable');
  await page.locator('[data-plan-id="plan-2"]').getByRole('button',{name:'编辑计划',exact:true}).click();await planEditor(page).getByLabel('关联账号').selectOption('account-xhs');await planEditor(page).getByLabel('目标时长').fill('60');await planEditor(page).getByRole('button',{name:'保存并写脚本',exact:true}).click();await page.locator('.script-editor').waitFor();
  const generate=state.mutations.find(value=>value.path.endsWith('/contents/generate'));assert.equal(generate.body.plan_id,'plan-2');assert.equal(generate.body.topic_id,'layout');assert.equal(state.contents[0].channel,'小红书');assert.match(await page.locator('.brief-card').innerText(),/小红书/);checks.push('save-and-write sends the saved plan ID; the rendered content uses the account channel returned by the fixture server');
  await page.locator('.studio-steps').getByRole('button',{name:'2 策划',exact:true}).click();await page.getByRole('tab',{name:/内容计划/,exact:false}).click();await shot(page,'plans-desktop');await page.setViewportSize({width:375,height:920});await fits(page,'mobile plan board');await shot(page,'plans-mobile');
 });
 await run('failure-retry',{failFirst:true},async(page,state,checks)=>{
  await planning(page);await page.locator('.planning-reference-card').first().click();await page.getByRole('button',{name:'生成 3 个策划方向',exact:true}).click();await page.getByRole('alert').filter({hasText:'隔离验收：策划服务暂时失败'}).waitFor();assert.equal(await page.locator('.planning-candidate').count(),0);assert.equal(await page.getByRole('button',{name:'生成 3 个策划方向',exact:true}).isDisabled(),false);await shot(page,'planning-failure-desktop');
  await page.getByRole('button',{name:'生成 3 个策划方向',exact:true}).click();await waitCandidates(page);assert.equal(state.mutations.filter(value=>value.path.endsWith('/planning/suggest')).length,2);checks.push('failed durable jobs expose an error, retain context and allow a fresh successful retry');
 });
 await run('pending-reload-and-isolation',{},async(page,state,checks)=>{
  state.holdJobs=true;await planning(page);await page.locator('.planning-reference-card').first().click();await page.locator('.planning-purpose-grid').getByRole('button',{name:/流量内容/}).click();await page.getByLabel('给哪个账号做').selectOption('account-xhs');await page.getByLabel('预计时长').selectOption('60');await page.getByRole('textbox',{name:'策划想法与补充要求',exact:true}).fill('恢复时保留最初的受众和渠道。');await page.getByRole('button',{name:'生成 3 个策划方向',exact:true}).click();await page.locator('.planning-progress').waitFor();
  await page.reload({waitUntil:'domcontentloaded'});await studio(page);await planning(page);await page.locator('.planning-progress').waitFor();state.holdJobs=false;await waitCandidates(page);assert.equal(state.mutations.filter(value=>value.path.endsWith('/planning/suggest')).length,1);
  assert.equal(await page.locator('.planning-purpose-grid').getByRole('button',{name:/流量内容/}).getAttribute('aria-pressed'),'true','restoring a job must restore the selected content purpose');assert.equal(await page.getByLabel('给哪个账号做').inputValue(),'account-xhs');assert.equal(await page.getByLabel('预计时长').inputValue(),'60');assert.equal(await page.locator('.planning-reference-card.selected').count(),1);assert.equal(await page.getByLabel('具体方向').inputValue(),'楼市热点');
  await page.locator('.planning-result-provenance summary').click();assert.match(await page.locator('.planning-result-provenance').innerText(),/小红书 · 60 秒/);await page.locator('.planning-candidate').first().getByRole('button',{name:'采用这个方向',exact:true}).click();assert.equal(await planEditor(page).getByLabel('关联账号').inputValue(),'account-xhs');assert.equal(await planEditor(page).getByLabel('目标时长').inputValue(),'60');assert.match(await planEditor(page).getByLabel('创作简报与待准备资料').inputValue(),/恢复时保留最初的受众/);await planEditor(page).getByRole('button',{name:'取消',exact:true}).click();checks.push('page reload resumes the same pending job and restores purpose, subject, account, reference, duration and stored result context');
  await page.reload({waitUntil:'domcontentloaded'});await studio(page);await planning(page);await page.getByRole('button',{name:'恢复上次方案',exact:true}).click();await waitCandidates(page);assert.equal(await page.locator('.planning-purpose-grid').getByRole('button',{name:/流量内容/}).getAttribute('aria-pressed'),'true');assert.equal(await page.getByLabel('给哪个账号做').inputValue(),'account-xhs');assert.equal(await page.getByLabel('预计时长').inputValue(),'60');assert.equal(await page.locator('.planning-reference-card.selected').count(),1);checks.push('restoring completed historical suggestions also restores the controls that produced them');
  state.holdJobs=true;await page.getByRole('button',{name:'生成 3 个策划方向',exact:true}).click();await page.locator('.planning-progress').waitFor();
  await page.getByRole('button',{name:'切换当前项目',exact:true}).click();await page.getByRole('dialog',{name:'选择当前项目',exact:true}).getByRole('button').filter({hasText:project(otherPid).name}).click();state.holdJobs=false;await page.locator('.research-workspace').waitFor();await planning(page);await page.getByText('先收藏一条值得借鉴的视频',{exact:true}).waitFor();assert.equal(await page.locator('.planning-candidate').count(),0);assert.equal(await page.locator('.planning-reference-card').count(),0);assert.equal(await page.getByRole('button',{name:'恢复上次方案',exact:true}).count(),0);checks.push('switching projects during a pending job resets source, results and recovery history while its old response stays isolated');await shot(page,'other-project-empty');
 });
 await run('empty-mobile',{empty:true},async(page,state,checks)=>{
  await planning(page);await page.getByText('先收藏一条值得借鉴的视频',{exact:true}).waitFor();assert.equal(await page.getByRole('button',{name:'生成 3 个策划方向',exact:true}).isDisabled(),true);await page.getByRole('button',{name:'去发现选视频',exact:true}).click();await page.locator('.research-workspace').waitFor();await planning(page);await page.getByRole('button',{name:'内容类别',exact:true}).click();await page.getByRole('button',{name:'生成 3 个策划方向',exact:true}).waitFor();assert.equal(await page.getByRole('button',{name:'生成 3 个策划方向',exact:true}).isDisabled(),false);await page.setViewportSize({width:375,height:920});await fits(page,'mobile empty/category planning');await shot(page,'planning-mobile');checks.push('empty reference state returns to discovery or allows category planning; 375 px planning has no horizontal overflow');
 });
 await run('read-only',{role:'sales'},async(page,state,checks)=>{
  await page.locator(`article[data-content-key="bilibili:${video.content_id}"]`).waitFor();assert.equal(await page.getByRole('button',{name:'用这条策划',exact:true}).isDisabled(),true);await planning(page);await page.locator('.planning-reference-card').first().click();assert.equal(await page.getByRole('button',{name:'生成 3 个策划方向',exact:true}).isDisabled(),true);assert.equal(await page.getByRole('button',{name:'直接写计划',exact:true}).isDisabled(),true);assert.equal(await page.locator('.planning-method-list input:enabled').count(),0);await page.getByRole('tab',{name:/内容计划/,exact:false}).click();assert.equal(await page.getByRole('button',{name:'编辑计划',exact:true}).isDisabled(),true);assert.deepEqual(state.mutations,[]);checks.push('read-only users can inspect sources and plans while capture, suggestion, edit and script actions remain disabled');await shot(page,'planning-read-only');
 });
}finally{
 const report={passed:records.every(value=>value.passed),testedAt:new Date().toISOString(),appUrl,scope:'Built full React UI with isolated HTTP fixtures; backend business and actual model quality are separately verified.',scenarios:records};await writeFile(resolve(output,'browser-acceptance.json'),JSON.stringify(report,null,2));await browser.close();console.log(JSON.stringify({passed:report.passed,scenarios:records.map(({scenario,passed,error})=>({scenario,passed,...(error?{error}:{})})),output}));if(!report.passed)process.exitCode=1;
}
