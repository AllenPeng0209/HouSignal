/** Actual content-studio interaction against isolated HTTP fixtures; no platform, model, or persistent writes. */
import assert from 'node:assert/strict';
import {createRequire} from 'node:module';
import {mkdir, writeFile} from 'node:fs/promises';
import {resolve} from 'node:path';

const require=createRequire(import.meta.url);
const {chromium}=require(process.env.HS_PLAYWRIGHT_MODULE||'/Users/pengyanlun/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright');
const appUrl=process.env.HS_DIRECTION_QA_URL||'http://127.0.0.1:8017/';
const output=resolve(process.env.HS_DIRECTION_QA_OUTPUT||'../../reports/content-directions/browser');
const browser=await chromium.launch({headless:true,channel:process.env.HS_BROWSER_CHANNEL||'chrome'});
const page=await browser.newPage({viewport:{width:1440,height:1100}});
page.setDefaultTimeout(15000);

const at='2026-10-14T07:00:00Z',pid='content-direction-fixture',base=`/api/projects/${pid}`,research=`${base}/operations/research`;
const project={id:pid,name:'内容方向验收项目',city:'杭州',property_type:'住宅',channel:'抖音',owner_name:'合成负责人',audience:'改善家庭',sales_owner_name:'合成负责人',marketing_confirmed:true,marketing_evidence:'fixture',is_test:true,completeness:{percent:100,completed:7,total:7,items:[]}};
const session={user:{id:'qa',name:'合成验收',email:'qa@example.invalid'},organization:{id:'qa',name:'合成测试组织'},role:'owner'};
const oldFilters={schema_version:1,scope:{geography:'nationwide',cities:[],segments:['residential'],topics:['layout']},query:'老方向动线',platforms:['bilibili'],days:7,min_likes:10,min_views:100,sort:'latest',account_scope:'operations'};
const oldProfile={id:'legacy-1',name:'既有户型研究',filters:oldFilters,revision:1,queries:['住宅 户型动线 老方向动线'],created_at:at};
let profiles=[oldProfile],runs=[],plans=[];
const mutations=[],requests=[],errors=[],unhandled=[],checks=[];
const item={platform:'bilibili',content_id:'fixture-1',title:'合成政策参考内容',url:'https://www.bilibili.com/video/BV1testfixture/',author:'合成账号',published_at:at,metrics:{views:1000,likes:20,comments:2,saves:null,shares:null},metrics_observed_at:at,metrics_source:'isolated HTTP fixture',match_keywords:['购房政策'],qualification:'candidate_unverified',historical:false,qualification_reason:'fixture; no external source claim'};
const topics=['tour','layout','light','space','kitchen'].map(id=>({id,title:{tour:'看房导览',layout:'户型动线',light:'采光景观',space:'空间尺度',kitchen:'厨房细节'}[id],eligible:true,missing:[],angle:'核对项目资料',fact_refs:[]}));
function completedRun(profile){return {id:'search-1',kind:'discovery_search',status:'succeeded',progress:1,created_at:at,result:{discovery_search:{query:profile.filters.query,queries:['上海 住宅 购房政策'],window_days:30,searched_at:at,sources:[{platform:'bilibili',adapter:'bilibili_public',status:'ok',reason:'fixture'}],items:[item]}}};}
page.on('pageerror',error=>errors.push(error.message));
await page.route('**/*',async route=>{
 const request=route.request(),url=new URL(request.url()),path=url.pathname,method=request.method();
 if(url.origin!==new URL(appUrl).origin)return route.abort();
 if(!path.startsWith('/api/'))return route.continue();
 let body;try{body=request.postDataJSON();}catch{body=null;}
 requests.push({method,path,body});if(method!=='GET')mutations.push({method,path,body});
 const respond=(value,status=200)=>route.fulfill({status,contentType:'application/json',body:JSON.stringify(value)});
 if(path==='/api/auth/me')return respond(session);
 if(path==='/api/projects')return respond([project]);
 if(path==='/api/model/status')return respond({configured:true,provider:'fixture',model:'fixture'});
 if(path===`${base}/overview`)return respond({project,metrics:{published:0,inquiries:0,qualified:0,appointments:0,visits:0,views:null,unknown_rate:null},pending_inquiries:[],content_versions:[],publications:[],proposals:[]});
 if(path===`${base}/reports`)return respond({window:'D7',metrics:{views:null,likes:null,comments:null,saves:null,inquiries:null,qualified:null,window_complete:false,observed_publications:0,total_publications:0},publications:[],proposals:[]});
 if(path===`${base}/topics`)return respond(topics);
 if(path===`${base}/jobs`||path===`${research}/runs`)return respond(runs);
 if(path===`${research}/saved-searches`&&method==='GET')return respond(profiles);
 if(path===`${research}/saved-searches`&&method==='POST'){
  const value={id:`profile-${profiles.length}`,name:body.name,filters:body.filters,revision:1,queries:['上海 住宅 购房政策'],created_at:at};profiles.push(value);return respond(value,201);
 }
 if(path.startsWith(`${research}/saved-searches/`)&&method==='PATCH'){
  const index=profiles.findIndex(profile=>profile.id===path.split('/').at(-1));assert.notEqual(index,-1,'patch identifies a saved direction');
  assert.equal(body.expected_revision,profiles[index].revision,'saved direction updates use its current revision');
  profiles[index]={...profiles[index],name:body.name,filters:body.filters,revision:profiles[index].revision+1};return respond(profiles[index]);
 }
 if(path.startsWith(`${research}/saved-searches/`)&&path.endsWith('/run')&&method==='POST'){
  const profile=profiles.find(value=>value.id===path.split('/').at(-2));assert.ok(profile,'run identifies an actual saved direction');
  runs=[completedRun(profile)];return respond({job_id:'search-1'});
 }
 if(path==='/api/jobs/search-1/events')return route.fulfill({status:200,contentType:'text/event-stream',body:`event: status\ndata: ${JSON.stringify(runs[0])}\n\n`});
 if(path==='/api/jobs/search-1')return respond(runs[0]);
 if(path===`${base}/operations/plans`&&method==='POST'){
  const value={id:`plan-${plans.length+1}`,status:'draft',content_version_id:null,source:null,created_at:at,...body};plans.push(value);return respond(value,201);
 }
 if(path===`${base}/operations/plans`)return respond(plans);
 if(path===`${base}/operations/discovery/douyin-account`)return respond({state:'unknown',scope:'research',message:'fixture'});
 if(path===`${base}/operations/platform-accounts/douyin/status`)return respond({state:'success',loggedIn:true,verified:true,name:'合成项目运营账号'});
 if(path===`${base}/operations/platform-status`)return respond({scope:'fixture',account_authorization:false,platforms:[]});
 if([`${base}/assets`,`${base}/contents`,`${base}/publications`,`${base}/operations/accounts`,`${base}/operations/opportunities`,`${research}/references`].includes(path))return respond([]);
 if(path===`${base}/conversations`)return respond([]);
 unhandled.push(`${method} ${path}`);return respond({detail:'unhandled fixture'},404);
});

const selectedTypes=()=>page.locator('button[aria-pressed="true"]').filter({hasText:/专业内容|流量内容|信任内容|获客内容|转化内容/});
async function studio(){await page.getByRole('navigation',{name:'主导航'}).getByRole('button',{name:'内容工作台',exact:true}).click();}
async function mutationsSettled(){await page.evaluate(()=>new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve))));}
function noWritesSince(count,why){assert.equal(mutations.length,count,why);}
async function viewportFits(label){assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=window.innerWidth),`${label}: page fits viewport`);}

try{
 await mkdir(output,{recursive:true});await page.goto(appUrl,{waitUntil:'domcontentloaded',timeout:30000});await studio();
 await page.getByRole('heading',{name:'按内容类型找选题',exact:true}).waitFor();
 for(const label of ['专业内容','流量内容','信任内容','获客内容','转化内容'])assert.equal(await page.getByRole('button',{name:new RegExp(`^${label}`)}).getAttribute('aria-pressed'),'false');
 const initialWrites=mutations.length;assert.equal(initialWrites,0,'opening direction selection does not start remote work');
 await page.getByRole('button',{name:/^专业内容/}).click();
 assert.equal(await page.getByRole('button',{name:/^专业内容/}).getAttribute('aria-pressed'),'true');
 for(const label of ['政策解读','板块分析','户型点评','买房知识'])await page.getByRole('button',{name:label,exact:true}).waitFor();
 assert.notEqual(await page.getByLabel('补充关键词').inputValue(),'');
 await mutationsSettled();noWritesSince(initialWrites,'choosing a type only changes the editable direction');
 await page.getByRole('button',{name:'政策解读',exact:true}).click();
 assert.equal(await page.getByLabel('补充关键词').inputValue(),'购房政策');
 assert.equal(await page.getByRole('checkbox',{name:'实拍看房',exact:true}).isChecked(),false);
 await mutationsSettled();noWritesSince(initialWrites,'choosing a topic does not save or search automatically');
 checks.push('five content types guide topic choice; type and topic interactions make no requests');

 await page.getByLabel('区域').selectOption('cities');
 await page.getByLabel('城市（最多 5 个）').fill('上海、杭州');
 await page.getByRole('checkbox',{name:'抖音',exact:true}).uncheck();
 await page.getByRole('checkbox',{name:'买房避坑',exact:true}).check();
 await page.getByRole('button',{name:'政策解读',exact:true}).click();
 await page.getByLabel('方向名称').fill('专业政策参考');
 const runResponse=page.waitForResponse(response=>response.url().endsWith('/run')&&response.request().method()==='POST');
 await page.getByRole('button',{name:'搜索相关内容',exact:true}).click();await runResponse;
 await page.locator('.discovery-result-card').waitFor();
 const saved=profiles.find(profile=>profile.name==='专业政策参考');assert.ok(saved);
 assert.deepEqual(saved.filters.scope,{geography:'cities',cities:['上海','杭州'],segments:['residential','luxury','improvement'],topics:[],content_type:'professional'});
 assert.equal(saved.filters.query,'购房政策');assert.deepEqual(saved.filters.platforms,['bilibili']);assert.equal(saved.filters.account_scope,'operations');
 assert.equal(mutations.filter(request=>request.path.endsWith('/run')).length,1);
 checks.push('explicit search persists professional policy query, clears unrelated topics, preserves cities, segments and platforms');
 await viewportFits('desktop');await page.evaluate(()=>window.scrollTo(0,0));await page.screenshot({path:resolve(output,'directions-desktop.png'),fullPage:true,animations:'disabled'});

 await page.reload({waitUntil:'domcontentloaded'});await studio();await page.getByLabel('已保存研究方向').selectOption(saved.id);
 assert.equal(await page.getByRole('button',{name:/^专业内容/}).getAttribute('aria-pressed'),'true');
 assert.equal(await page.getByLabel('补充关键词').inputValue(),'购房政策');assert.equal(await page.getByLabel('城市（最多 5 个）').inputValue(),'上海、杭州');
 const writesBeforeLoad=mutations.length;await page.getByLabel('已保存研究方向').selectOption('legacy-1');
 assert.equal(await selectedTypes().count(),0);assert.equal(await page.getByLabel('补充关键词').inputValue(),'老方向动线');assert.equal(await page.getByRole('checkbox',{name:'户型动线',exact:true}).isChecked(),true);
 await mutationsSettled();noWritesSince(writesBeforeLoad,'loading old or new saved directions does not start another search');
 checks.push('saved type, query and geography restore after refresh; legacy scopes still load without a content type');

 await page.getByLabel('已保存研究方向').selectOption(saved.id);
 await page.getByRole('button',{name:'清除内容类型',exact:true}).click();assert.equal(await selectedTypes().count(),0);
 await page.getByRole('button',{name:'保存方向',exact:true}).click();await page.getByText('研究方向已保存').waitFor();
 assert.equal(profiles.find(profile=>profile.id===saved.id).filters.scope.content_type,null);
 assert.equal(mutations.filter(request=>request.path.endsWith('/run')).length,1,'save direction does not search');
 checks.push('clearing a content type can be saved without running a search');

 await page.getByRole('button',{name:/^专业内容/}).click();await page.getByRole('button',{name:'政策解读',exact:true}).click();
 await page.getByRole('button',{name:'用此选题策划',exact:true}).click();
 const dialog=page.getByRole('dialog',{name:'新建内容计划',exact:true});await dialog.waitFor();
 assert.equal(await dialog.getByLabel('选题标题').inputValue(),'政策解读');
 const brief=await dialog.getByLabel('创作简报').inputValue();assert.match(brief,/专业内容/);assert.match(brief,/信任|认知|有能力/);
 assert.equal(await dialog.getByLabel('项目选题方向').inputValue(),'tour','content type does not become an unsupported project topic ID');
 assert.equal(plans.length,0,'planning selection waits for explicit save');
 await dialog.getByLabel('项目选题方向').selectOption('layout');
 await dialog.getByRole('button',{name:'保存计划',exact:true}).click();await dialog.waitFor({state:'hidden'});
 assert.equal(plans.length,1);assert.equal(plans[0].title,'政策解读');assert.equal(plans[0].topic_id,'layout');assert.match(plans[0].brief,/专业内容/);
 assert.equal(mutations.filter(request=>request.path===`${base}/operations/plans`&&request.method==='POST').length,1);
 checks.push('chosen topic opens editable plan seed; explicit save uses existing plan endpoint and topic IDs');

 await page.locator('.studio-steps').getByRole('button',{name:/发现/}).click();
 await page.setViewportSize({width:390,height:844});
 await page.getByRole('button',{name:/^专业内容/}).click();await page.getByRole('button',{name:'政策解读',exact:true}).click();
 await page.getByText('内容计划已创建',{exact:true}).waitFor({state:'hidden',timeout:8000});
 await viewportFits('mobile direction selection');await page.evaluate(()=>window.scrollTo(0,0));await page.screenshot({path:resolve(output,'directions-mobile.png'),fullPage:true,animations:'disabled'});
 await page.getByRole('button',{name:'用此选题策划',exact:true}).click();await dialog.waitFor();
 assert.ok(await dialog.evaluate(element=>element.scrollWidth<=element.clientWidth),'mobile plan dialog fits viewport');await viewportFits('mobile plan dialog');
 await page.screenshot({path:resolve(output,'plan-mobile.png'),animations:'disabled'});
 checks.push('direction picker and seeded plan fit the 390px mobile viewport');
 assert.deepEqual(errors,[]);assert.deepEqual(unhandled,[]);
 await writeFile(resolve(output,'receipt.json'),JSON.stringify({mode:'isolated HTTP fixtures; no model, platform or persistent database writes',appUrl,checks,errors,unhandled,mutations},null,2));
 process.stdout.write(`Content direction browser acceptance passed (${checks.length} groups); receipt: ${resolve(output,'receipt.json')}\n`);
}catch(error){
 await mkdir(output,{recursive:true});await page.screenshot({path:resolve(output,'failure.png'),fullPage:true}).catch(()=>{});
 await writeFile(resolve(output,'failure.json'),JSON.stringify({error:error.message,errors,unhandled,mutations},null,2));throw error;
}finally{await browser.close();}
