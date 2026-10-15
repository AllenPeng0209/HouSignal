/** Real UI acceptance with isolated HTTP fixtures. No database, model or platform requests. */
import assert from 'node:assert/strict';
import {createHash} from 'node:crypto';
import {createRequire} from 'node:module';
import {mkdir, rm, writeFile} from 'node:fs/promises';
import {resolve} from 'node:path';

const require = createRequire(import.meta.url);
const {chromium} = require(process.env.HS_PLAYWRIGHT_MODULE || '/Users/pengyanlun/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright');
const appUrl = process.env.HS_VIDEO_BREAKDOWN_QA_URL || 'http://127.0.0.1:8017/';
const output = resolve(process.env.HS_VIDEO_BREAKDOWN_QA_OUTPUT || '../../reports/real-estate-content-improvement/browser');
const browser = await chromium.launch({headless:true, channel:process.env.HS_BROWSER_CHANNEL || 'chrome'});
const page = await browser.newPage({viewport:{width:1440,height:1100}});
page.setDefaultTimeout(12000);
const date = '2026-10-14T02:00:00Z';
const session = {user:{id:'qa',name:'视频拆解验收',email:'qa@example.invalid'},organization:{id:'qa-org',name:'合成测试组织'},role:'owner'};
const makeProject = (id,name) => ({id,name,city:'上海',property_type:'住宅',channel:'抖音',owner_name:'合成内容负责人',audience:'改善家庭',sales_owner_name:'合成销售',marketing_confirmed:true,marketing_evidence:'HTTP fixture only',is_test:true,completeness:{percent:100,completed:7,total:7,items:[]}});
const projects = [makeProject('breakdown-a','拆解验收甲项目'),makeProject('breakdown-b','拆解验收乙项目')];
const topics = ['space','light','layout','kitchen','tour'].map(id=>({id,title:({space:'空间尺度',light:'采光景观',layout:'户型动线',kitchen:'厨房细节',tour:'看房导览'})[id],eligible:true,missing:[],angle:'通过核验户型图解释真实动线',fact_refs:[{kind:'fact',id:'own-layout'}]}));
const sourceA = 'https://www.douyin.com/video/7390000000000000001';
const sourceB = 'https://www.douyin.com/video/7390000000000000002';
const makeItem = (content_id,title,url) => ({platform:'douyin',content_id,title,url,author:'合成住宅观察',published_at:'2026-10-12T02:00:00Z',metrics:{views:120000,likes:3200,comments:38,saves:null,shares:0},metrics_observed_at:date,metrics_source:'isolated fixture',match_keywords:['房地产','户型'],qualification:'verified_high_engagement',historical:false,qualification_reason:'浏览器合成数据，仅用于交互验收',snippet:'用玄关到客厅的实拍解释改善住宅动线。'});
const items = [makeItem('source-a','参考视频甲：看户型先看动线',sourceA),makeItem('source-b','参考视频乙：玄关如何进入客厅',sourceB)];
items[0].growth={status:'observed',previous_observed_at:'2026-10-14T00:00:00Z',current_observed_at:date,elapsed_hours:2,metric_deltas:{views:null,likes:240,comments:4,saves:null,shares:0},likes_per_hour:120,reasons:['收藏指标未获取，保留为空']};
items[1].growth={status:'no_baseline',previous_observed_at:null,current_observed_at:date,elapsed_hours:null,metric_deltas:{views:null,likes:null,comments:null,saves:null,shares:null},likes_per_hour:null,reasons:['首次采样，尚无历史基线']};
const savedOpportunity = {id:'saved-source',title:'已保存的真实来源参考',kind:'competitor',platform:'抖音',source_url:sourceB,summary:'已有团队记录：00:00 提出看房问题。',observed_at:date,status:'saved'};
const opportunities = {'breakdown-a':[savedOpportunity],'breakdown-b':[]};
const plans = {'breakdown-a':[],'breakdown-b':[]};
const discoveryResults = {'breakdown-a':null,'breakdown-b':null};
const requests = [], errors = [], unhandled = [], blockedExternal = [], checks = [];
let holdNextBreakdown = false;
let failNextSave = true;
const savedByKey = new Map();
const holds = [];
let lastPreview;
function canonicalJson(value){return JSON.stringify(value&&typeof value==='object'?Array.isArray(value)?value.map(item=>JSON.parse(canonicalJson(item))):Object.fromEntries(Object.keys(value).sort().map(key=>[key,JSON.parse(canonicalJson(value[key]))])):value);}
const digest=value=>createHash('sha256').update(canonicalJson(value)).digest('hex');

function preview(body,project) {
  const row = {start_seconds:0,end_seconds:3,transcript:'先看玄关到客厅的动线。',visual:'从玄关推镜到客厅',evidence_status:'user_supplied'};
  const shots = [
    {start_seconds:0,end_seconds:3,purpose:'开场问题',voiceover:'看房先核对户型与动线。',visual:'授权实拍展示玄关位置',evidence_status:'creative_draft'},
    {start_seconds:3,end_seconds:20,purpose:'事实与依据',voiceover:'按核验户型图说明玄关至客厅的连接。',visual:'实拍与已核验图纸对照',evidence_status:'verified_project_fact'},
    {start_seconds:20,end_seconds:36,purpose:'生活场景与核对',voiceover:'走一遍日常回家路径。',visual:'连续实拍核对行走动线',evidence_status:'creative_draft'},
    {start_seconds:36,end_seconds:body.target_seconds,purpose:'回答与行动',voiceover:'你还想核对哪一点？',visual:'字幕邀请具体提问',evidence_status:'creative_draft'},
  ];
  const brief = `参考来源：${body.source_url}\n仅依据用户提供的口播与观察，系统未观看原片。\n目标：${body.target_seconds}秒\n0–3秒：看房先核对户型与动线。\n3–20秒：按核验户型图说明玄关至客厅连接。\n20–36秒：日常回家路径。\n36–${body.target_seconds}秒：邀请具体提问。`;
  return {source:{url:body.source_url,title:body.title,watched:false,evidence_basis:'user_supplied_notes'},timeline:[row,{start_seconds:3,end_seconds:10,transcript:'再核对日常回家路径。',visual:'',evidence_status:'user_supplied'}],hook:{evidence:`${row.transcript}；${row.visual}`,status:'user_supplied',questions:['前 3 秒是否提出明确的看房问题？']},adaptation:{topic_id:body.topic_id,eligible:true,missing_facts:[],fact_refs:[{id:'own-layout',revision:2,label:'玄关至客厅',value:'图纸已核验',unit:'',source:'本项目核验户型图'}],shots},report:`参考作品：${body.title}\n${body.source_url}\n证据范围：用户提供，系统未观看原片。\n原片分秒记录：00:00–00:03 先看玄关到客厅的动线。\n画面：从玄关推镜到客厅\n互动表现和爆款成因尚未核验。`,plan_seed:{title:`${project.name}：户型动线`,angle:'用当前项目资料解释回家动线',brief,topic_id:body.topic_id,channel:'抖音'}};
}

page.on('pageerror',error=>errors.push(error.message));
await page.route('**/*',async route=>{
  const request=route.request(),url=new URL(request.url()),path=url.pathname,method=request.method();
  if(url.origin!==new URL(appUrl).origin){blockedExternal.push(request.url());return route.abort('blockedbyclient');}
  if(!path.startsWith('/api/'))return route.continue();
  const body=request.postDataJSON();requests.push({path,query:url.search,method,body});
  const respond=(value,status=200)=>route.fulfill({status,contentType:'application/json',body:JSON.stringify(value)});
  if(path==='/api/auth/me')return respond(session);
  if(path==='/api/projects')return respond(projects);
  if(path==='/api/model/status')return respond({provider:'fixture',model:'fixture',configured:true});
  if(path==='/api/jobs/search-fixture/events')return route.fulfill({status:200,contentType:'text/event-stream',body:`event: status\ndata: ${JSON.stringify({id:'search-fixture',kind:'discovery_search',status:'succeeded',progress:{percent:100},created_at:date,result:{discovery_search:{query:requests.findLast(r=>r.path.endsWith('/discovery/search')).body.query,searched_at:date,window_days:30,total:items.length,sources:[{platform:'douyin',adapter:'douyin_public_browser',status:'ok',reason:'HTTP fixture only'}],items}}})}\n\n`});
  const match=path.match(/^\/api\/projects\/(breakdown-[ab])\/(.+)$/);
  if(match){
    const [,id,resource]=match,project=projects.find(p=>p.id===id);
    if(resource==='overview')return respond({project,metrics:{published:0,inquiries:0,qualified:0,appointments:0,visits:0,views:null,unknown_rate:null},pending_inquiries:[],content_versions:[],publications:[],proposals:[]});
    if(['contents','assets','publications'].includes(resource))return respond([]);
    if(resource==='jobs')return respond(discoveryResults[id]?[{id:'search-fixture',kind:'discovery_search',status:'succeeded',created_at:date,result:{discovery_search:discoveryResults[id]}}]:[]);
    if(resource==='reports')return respond({window:'D7',metrics:{views:null,likes:null,comments:null,saves:null,inquiries:null,qualified:null,window_complete:false,observed_publications:0,total_publications:0},publications:[],proposals:[]});
    if(resource==='topics')return respond(topics);
    if(resource==='operations/discovery/douyin-account')return respond({state:'unknown',message:'浏览器 fixture，不访问平台',scope:'research'});
    if(resource==='operations/discovery/search'&&method==='POST'){discoveryResults[id]={query:body.query,searched_at:date,window_days:30,total:items.length,sources:[{platform:'douyin',adapter:'douyin_public_browser',status:'ok',reason:'HTTP fixture only'}],items};return respond({job_id:'search-fixture'},202);}
    if(resource==='operations/discovery/breakdown'&&method==='POST'){
      const value=preview(body,project);lastPreview=value;
      if(holdNextBreakdown){holdNextBreakdown=false;await new Promise(done=>holds.push(done));}
      // Aborted fetches can close before the fixture is released; this is expected.
      return respond(value).catch(error=>{if(!/closed|cancelled|canceled|intercepted|Invalid InterceptionId/i.test(error.message))throw error;});
    }
    if(resource==='operations/opportunities')return respond(opportunities[id]);
    if(resource==='operations/accounts')return respond([]);
    if(resource==='operations/discovery/breakdown/plan'&&method==='POST'){
      let value=savedByKey.get(body.idempotency_key);
      if(!value){
        const opportunity=opportunities[id].find(o=>o.id===body.opportunity_id);
        const evidence=preview(body.reference,project),reference={...body.reference,source_url:new URL(body.reference.source_url).origin+new URL(body.reference.source_url).pathname};
        const source={...(opportunity||{}),reference_analysis:{schema_version:1,reference,source:{...evidence.source,url:reference.source_url},timeline:evidence.timeline,structure:{known_clip_count:2,untimed_clip_count:0,last_known_seconds:10,gaps:[]},hook:evidence.hook,reuse_template:{id:'question',label:'问题引入',description:'用具体看房问题组织资料与实拍。',techniques:['问题引入','实拍动线']},observed_at:date,content_sha256:digest(reference),idempotency_key:body.idempotency_key,request_hash:digest({reference:body.reference,plan:body.plan,opportunity_id:body.opportunity_id})}};
        value={id:`plan-${plans[id].length+1}`,property_id:id,...body.plan,status:'draft',account_id:null,scheduled_for:null,opportunity_id:body.opportunity_id,source,content_version_id:null};
        plans[id].push(value);savedByKey.set(body.idempotency_key,value);
        if(opportunity)opportunity.status='planned';
      }
      // Simulate commit succeeded but its response failed. Retry must use the same UUID.
      if(failNextSave){failNextSave=false;return respond({detail:'合成保存响应暂不可用，请重试'},503);}
      return respond(value);
    }
    if(resource==='operations/plans')return respond(plans[id]);
  }
  unhandled.push(`${method} ${path}`);return respond({detail:'Unexpected acceptance fixture request'},404);
});

const dialog=()=>page.getByRole('dialog',{name:'视频拆解与脚本改编',exact:true});
const card=title=>page.locator('article.discovery-result-card').filter({hasText:title});
async function openSearchCard(item=items[0]) {await card(item.title).getByRole('button',{name:'拆解并写脚本',exact:true}).click();await dialog().waitFor();}
async function fillNotes(){
  await dialog().getByLabel('原片口播或字幕').fill('[00:00-00:03] 先看玄关到客厅的动线。\n[00:03-00:10] 再核对日常回家路径。');
  await dialog().getByLabel('画面与节奏笔记').fill('[00:00-00:03] 从玄关推镜到客厅');
  await dialog().getByLabel('目标时长（秒）',{exact:true}).fill('45');
  await dialog().getByLabel('项目选题方向').selectOption('layout');
}
async function generatePreview(){await fillNotes();await dialog().getByRole('button',{name:'生成拆解与脚本',exact:true}).click();}
async function selectProject(name){
  await page.getByRole('button',{name:'切换当前项目',exact:true}).click();
  await page.getByRole('dialog',{name:'选择当前项目',exact:true}).getByRole('button').filter({hasText:name}).click();
}

try {
  await mkdir(output,{recursive:true});
  await page.goto(appUrl);
  await page.getByRole('navigation',{name:'主导航'}).getByRole('button',{name:'内容工作台',exact:true}).click();
  await page.getByRole('button',{name:'房地产',exact:true}).click();
  const query=await page.getByRole('searchbox',{name:'房地产内容关键词',exact:true}).inputValue();
  assert.match(query,/房地产|住宅|买房/);
  assert.equal(requests.filter(r=>r.path.endsWith('/discovery/search')).length,0,'preset must not silently launch a platform search');
  await page.getByRole('button',{name:'搜索相关内容',exact:true}).click();
  await card(items[0].title).waitFor();
  const search=requests.find(r=>r.path.endsWith('/discovery/search'));
  assert.equal(search.body.query,query);assert.deepEqual(search.body.platforms,['douyin']);assert.equal(search.body.days,30);
  checks.push('real-estate preset sets keyword; explicit search sends project-scoped filters');
  process.stdout.write('Search preset and project-scoped search passed.\n');
  assert.ok(await page.getByText('未获取',{exact:true}).count()>0,'unknown metric must stay unknown');
  const observed=card(items[0].title).locator('[aria-label="采样增量"]');
  assert.match(await observed.innerText(),/已观测增量/);assert.match(await observed.innerText(),/点赞增量 \+240/);
  assert.match(await observed.innerText(),/120 赞\/小时/);assert.match(await observed.innerText(),/间隔 2 小时/);
  assert.match(await observed.innerText(),/2026\/10\/14 08:00.*2026\/10\/14 10:00/);
  assert.match(await card(items[1].title).locator('[aria-label="采样增量"]').innerText(),/待复测[\s\S]*首次采样/);
  assert.doesNotMatch(await page.locator('.discovery-search-results').innerText(),/24\s*小时|24h/);
  await page.getByLabel('排序').selectOption('growth');
  assert.equal(requests.filter(r=>r.path.endsWith('/discovery/search')).length,1,'changing growth order must wait for explicit search');
  await Promise.all([page.waitForRequest(r=>r.url().endsWith('/discovery/search')&&r.postDataJSON().sort==='growth'),page.getByRole('button',{name:'搜索相关内容',exact:true}).click()]);
  await card(items[0].title).waitFor();
  assert.equal(requests.filter(r=>r.path.endsWith('/discovery/search')).at(-1).body.sort,'growth');
  checks.push('actual two-hour samples show measured delta/rate; no-baseline remains pending; growth sort requires explicit search');
  await page.screenshot({path:resolve(output,'discovery-desktop.png'),fullPage:true});

  await openSearchCard();
  assert.equal(await dialog().getByLabel('原片标题',{exact:true}).inputValue(),items[0].title);
  assert.equal(await dialog().getByLabel('原片链接').inputValue(),sourceA);
  assert.equal(await dialog().getByRole('button',{name:'生成拆解与脚本',exact:true}).isDisabled(),true,'a URL alone must not pretend to be an observed video');
  await generatePreview();
  await dialog().getByRole('heading',{name:'项目改编分镜',exact:true}).waitFor();
  const request=requests.find(r=>r.path.endsWith('/discovery/breakdown'));
  assert.deepEqual(request.body,{source_url:sourceA,title:items[0].title,transcript:'[00:00-00:03] 先看玄关到客厅的动线。\n[00:03-00:10] 再核对日常回家路径。',observations:'[00:00-00:03] 从玄关推镜到客厅',target_seconds:45,topic_id:'layout'});
  await dialog().getByText('从玄关推镜到客厅',{exact:false}).first().waitFor();
  assert.match(await dialog().innerText(),/未自动观看原片|未观看原片/);
  await dialog().getByText('完整拆解报告',{exact:true}).click();
  await dialog().locator('pre').waitFor();
  assert.match(await dialog().locator('pre').innerText(),/互动表现和爆款成因尚未核验/);
  checks.push('search result prefill; timestamped transcript and observations; explicit evidence limits; adapted shots');
  process.stdout.write('Timestamped preview and full report passed.\n');
  await page.screenshot({path:resolve(output,'breakdown-desktop.png')});
  await dialog().getByRole('button',{name:'保存到内容计划',exact:true}).click();
  await dialog().getByRole('alert').filter({hasText:'合成保存响应暂不可用，请重试'}).waitFor();
  assert.equal(plans['breakdown-a'].length,1);
  const firstSave=requests.find(r=>r.path.endsWith('/discovery/breakdown/plan'));
  assert.match(firstSave.body.idempotency_key,/^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i);
  await dialog().getByRole('button',{name:'保存到内容计划',exact:true}).click();
  await dialog().waitFor({state:'hidden'});
  const generate=page.getByRole('button',{name:'生成选题脚本',exact:true});
  await generate.waitFor();assert.equal(await generate.isEnabled(),true);
  const saves=requests.filter(r=>r.path==='/api/projects/breakdown-a/operations/discovery/breakdown/plan'&&r.method==='POST');
  assert.equal(saves.length,2);assert.deepEqual(saves[0].body,saves[1].body,'failed-response retry must preserve full body and UUID');
  const saved=saves[1];
  assert.deepEqual(saved.body,{reference:request.body,plan:lastPreview.plan_seed,opportunity_id:null,idempotency_key:firstSave.body.idempotency_key});
  assert.ok(saved.body.plan.brief.includes(sourceA));assert.equal(plans['breakdown-a'].length,1,'retry must not create a duplicate plan');
  const persisted=plans['breakdown-a'][0].source.reference_analysis;
  assert.deepEqual(persisted.reference,request.body);assert.equal(persisted.source.url,sourceA);assert.equal(persisted.source.watched,false);
  assert.equal(persisted.content_sha256,digest(request.body));assert.equal(persisted.idempotency_key,saved.body.idempotency_key);
  const planCard=page.locator('article.operations-plan-card').filter({hasText:plans['breakdown-a'][0].title});
  assert.equal(await planCard.getByRole('link',{name:/查看原始来源/}).getAttribute('href'),sourceA);
  assert.equal(plans['breakdown-b'].length,0);
  checks.push('durable save retries HTTP503 with identical UUID/body and one plan; canonical reference source remains visible; eligible script generation control');
  process.stdout.write('Exact plan seed and script generation control passed.\n');
  await page.reload();
  await page.getByRole('navigation',{name:'主导航'}).getByRole('button',{name:'内容工作台',exact:true}).click();
  await page.locator('.studio-steps').getByRole('button',{name:/策划/}).click();
  await planCard.getByText('查看原片记录',{exact:true}).click();
  const sourceRecord=planCard.locator('details.operations-reference-record');
  assert.equal(await sourceRecord.getByRole('link',{name:/打开原片/}).getAttribute('href'),sourceA);
  assert.deepEqual(await sourceRecord.locator('pre').allTextContents(),[request.body.transcript,request.body.observations]);
  assert.match(await sourceRecord.innerText(),/未自动观看原片/);assert.match(await sourceRecord.innerText(),/问题引入/);
  checks.push('reloaded plan recovers canonical original source, supplied transcript/observations, technique and evidence limits');
  await page.screenshot({path:resolve(output,'plan-desktop.png'),fullPage:true});

  await page.locator('.studio-steps').getByRole('button',{name:/发现/}).click();
  await openSearchCard();
  holdNextBreakdown=true;await Promise.all([page.waitForRequest(r=>r.url().endsWith('/discovery/breakdown')),generatePreview()]);
  await dialog().getByRole('button',{name:'关闭弹窗',exact:true}).click();
  await openSearchCard(items[1]);
  await dialog().getByLabel('原片口播或字幕').fill('第二条尚未提交的内容');
  holds.shift()?.();
  await page.waitForTimeout(100);
  assert.equal(await dialog().getByLabel('原片标题',{exact:true}).inputValue(),items[1].title);
  assert.equal(await dialog().getByLabel('原片口播或字幕').inputValue(),'第二条尚未提交的内容');
  assert.equal(await dialog().getByRole('heading',{name:'项目改编分镜',exact:true}).count(),0);
  checks.push('closed-dialog late preview cannot overwrite the next source or close the next dialog');
  process.stdout.write('Late preview isolation passed.\n');
  await dialog().getByRole('button',{name:'关闭弹窗',exact:true}).click();

  const savedCard=page.locator('article.operations-opportunity-card').filter({hasText:savedOpportunity.title});
  await savedCard.getByRole('button',{name:'拆解并写脚本',exact:true}).click();
  assert.equal(await dialog().getByLabel('原片链接').inputValue(),sourceB);
  await generatePreview();
  await dialog().getByRole('heading',{name:'项目改编分镜',exact:true}).waitFor();
  await page.setViewportSize({width:390,height:844});
  await dialog().getByRole('heading',{name:'项目改编分镜',exact:true}).scrollIntoViewIfNeeded();
  await page.screenshot({path:resolve(output,'breakdown-mobile.png')});
  assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=window.innerWidth),'mobile page has horizontal overflow');
  assert.ok(await dialog().evaluate(element=>element.scrollWidth<=element.clientWidth),'mobile dialog has horizontal overflow');
  await dialog().getByRole('button',{name:'保存到内容计划',exact:true}).click();
  await dialog().waitFor({state:'hidden'});
  const savedSource=requests.filter(r=>r.path.endsWith('/discovery/breakdown/plan')&&r.method==='POST').at(-1);
  assert.equal(savedSource.body.opportunity_id,savedOpportunity.id);assert.ok(savedSource.body.plan.brief.includes(sourceB));
  assert.notEqual(savedSource.body.idempotency_key,saved.body.idempotency_key);
  assert.equal(plans['breakdown-a'][1].source.id,savedOpportunity.id);
  assert.equal(plans['breakdown-a'][1].source.reference_analysis.source.url,sourceB);
  checks.push('saved opportunity opens breakdown with stable source; linked opportunity survives save; mobile dialog fits');

  await page.locator('.studio-steps').getByRole('button',{name:/发现/}).click();
  await page.locator('article.operations-opportunity-card').filter({hasText:savedOpportunity.title}).waitFor();
  assert.equal(await page.locator('article.operations-opportunity-card').filter({hasText:savedOpportunity.title}).getByRole('button',{name:'拆解并写脚本',exact:true}).isDisabled(),true);
  await page.screenshot({path:resolve(output,'discovery-mobile.png'),fullPage:true});
  assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=window.innerWidth),'mobile discovery page has horizontal overflow');

  await page.setViewportSize({width:1440,height:1100});
  await openSearchCard();holdNextBreakdown=true;
  await Promise.all([page.waitForRequest(r=>r.url().endsWith('/discovery/breakdown')),generatePreview()]);
  await dialog().getByRole('button',{name:'关闭弹窗',exact:true}).click();
  await selectProject(projects[1].name);
  holds.shift()?.();await page.waitForTimeout(100);
  assert.equal(await page.getByRole('dialog',{name:'视频拆解与脚本改编',exact:true}).count(),0);
  assert.equal(await page.getByText(plans['breakdown-a'][0].title,{exact:true}).count(),0);
  assert.equal(await page.getByText(items[0].title,{exact:true}).count(),0);
  checks.push('project change discards pending preview and another project source/plan');
  assert.deepEqual(errors,[]);assert.deepEqual(unhandled,[]);assert.deepEqual(blockedExternal,[]);
  await writeFile(resolve(output,'browser-receipt.json'),JSON.stringify({mode:'isolated browser HTTP fixtures; no database writes, model calls, or third-party platform requests',app_url:appUrl,checks,page_errors:errors,unhandled_api_requests:unhandled,blocked_external_requests:blockedExternal,mutations:requests.filter(r=>r.method!=='GET')},null,2));
  await Promise.all([rm(resolve(output,'failure.json'),{force:true}),rm(resolve(output,'failure.png'),{force:true})]);
  process.stdout.write(`Video breakdown browser acceptance passed (${checks.length} check groups); receipt: ${resolve(output,'browser-receipt.json')}\n`);
} catch(error) {
  await page.screenshot({path:resolve(output,'failure.png'),fullPage:true}).catch(()=>{});
  await writeFile(resolve(output,'failure.json'),JSON.stringify({error:error.message,page_errors:errors,unhandled_api_requests:unhandled,blocked_external_requests:blockedExternal,requests},null,2));
  throw error;
} finally {
  holds.forEach(done=>done());await browser.close();
}
