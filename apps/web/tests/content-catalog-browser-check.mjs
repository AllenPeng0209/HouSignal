/** Actual React catalogue acceptance with isolated HTTP fixtures; never searches a platform or writes persistent data. */
import assert from 'node:assert/strict';
import {createRequire} from 'node:module';
import {mkdir, writeFile} from 'node:fs/promises';
import {resolve} from 'node:path';

const require = createRequire(import.meta.url);
const {chromium} = require(process.env.HS_PLAYWRIGHT_MODULE || '/Users/pengyanlun/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright');
const appUrl = process.env.HS_CATALOG_QA_URL || 'http://127.0.0.1:8017/';
const phase = process.env.HS_CATALOG_QA_PHASE || 'green';
const output = resolve(process.env.HS_CATALOG_QA_OUTPUT || `../../reports/content-catalog/browser/${phase}`);
const browser = await chromium.launch({headless:true, channel:process.env.HS_BROWSER_CHANNEL || 'chrome'});
const page = await browser.newPage({viewport:{width:1440,height:1100}});
page.setDefaultTimeout(15000);

const at = '2026-10-14T07:00:00Z', pid = 'content-catalog-fixture';
const base = `/api/projects/${pid}`, research = `${base}/operations/research`;
const project = {id:pid,name:'内容灵感验收项目',city:'杭州',property_type:'住宅',channel:'抖音',owner_name:'合成负责人',audience:'改善家庭',sales_owner_name:'合成负责人',marketing_confirmed:true,marketing_evidence:'isolated HTTP fixture',is_test:true,completeness:{percent:100,completed:7,total:7,items:[]}};
const session = {user:{id:'qa',name:'合成验收',email:'qa@example.invalid'},organization:{id:'qa',name:'合成测试组织'},role:'owner'};
const subjects = {
 professional:[{label:'政策解读',query:'购房政策'},{label:'板块分析',query:'板块分析'},{label:'户型点评',query:'户型分析'},{label:'买房知识',query:'买房知识'}],
 traffic:[{label:'楼市热点',query:'楼市热点'},{label:'特色房源',query:'特色房源'},{label:'居住反差',query:'居住体验'},{label:'热门话题',query:'买房话题'}],
 trust:[{label:'真实服务过程',query:'看房服务'},{label:'客户反馈',query:'买房经历'},{label:'交易案例复盘',query:'成交案例'},{label:'个人从业经历',query:'房产经纪人日常'}],
 acquisition:[{label:'房源展示',query:'房源推荐'},{label:'不同预算的需求匹配',query:'预算选房'},{label:'买卖租服务介绍',query:'房产服务'}],
 conversion:[{label:'具体房源对比',query:'房源对比'},{label:'购房疑虑解答',query:'买房顾虑'},{label:'看房后分析',query:'看房复盘'},{label:'成交过程拆解',query:'成交过程'}],
};
function item(id,title,subject,jobId,extra={}) {
 return {platform:'bilibili',content_id:id,title,url:`https://www.bilibili.com/video/${id}/`,author:'合成验收账号',published_at:at,metrics:{views:1200,likes:30,comments:2,saves:null,shares:null},metrics_observed_at:at,metrics_source:'isolated HTTP fixture',match_keywords:[subject],qualification:'candidate_unverified',historical:false,qualification_reason:'仅为浏览器验收样本，未提出外部真实来源主张',subject,job_id:jobId,...extra};
}
const policy = item('BVpolicyfixture','【验收样本】购房政策参考','购房政策','job-professional-policy',{published_at:null,metrics:{views:null,likes:0,comments:1,saves:null,shares:0}});
const layout = item('BVlayoutfixture','【验收样本】户型动线参考','户型分析','job-professional-layout',{metrics:{views:null,likes:null,comments:null,saves:null,shares:null}});
const traffic = item('BVtrafficfixture','【验收样本】楼市热点参考','楼市热点','job-traffic');
const acquisition = item('BVacquisitionfixture','【验收样本】预算选房参考','预算选房','job-acquisition',{reference_id:'reference-existing'});
const hidden = item('BVhiddenfixture','【验收样本】后台整理房源参考','房源推荐',undefined,{reference_id:'reference-hidden',catalog_only:true,metrics:{views:null,likes:null,comments:null,saves:null,shares:null}});
const categories = [
 {id:'professional',label:'专业内容',purpose:'帮助用户理解房产，建立专业认知。',subjects:subjects.professional,items:[policy,layout],updated_at:at,collection_state:'ready'},
 {id:'traffic',label:'流量内容',purpose:'通过热点与特色吸引目标客群。',subjects:subjects.traffic,items:[traffic],updated_at:at,collection_state:'ready'},
 {id:'trust',label:'信任内容',purpose:'呈现真实服务与反馈，建立信任。',subjects:subjects.trust,items:[],updated_at:null,collection_state:'pending'},
 {id:'acquisition',label:'获客内容',purpose:'让有需求的客户找到对应服务。',subjects:subjects.acquisition,items:[acquisition,hidden],updated_at:at,collection_state:'ready'},
 {id:'conversion',label:'转化内容',purpose:'帮助意向客户推进具体选择。',subjects:subjects.conversion,items:[],updated_at:at,collection_state:'unavailable'},
];
const catalog = {categories,updated_at:at,total:5};
function reference(id,source,extra={}) {
 return {id,title:source.title,platform:'B站',source_url:source.url,source_key:`${source.platform}:${source.content_id}`,source_snapshot:source,summary:'浏览器验收参考',status:'saved',curation_state:'inbox',tags:[],note:'',research_notes:{transcript:'已保存的研究笔记',observations:'核对真实内容',topic_id:'layout',target_seconds:30},analysis_snapshot:{},revision:1,observed_at:at,...extra};
}
const hiddenNote = '后台整理保留的选题观察：先核对房源条件与需求。';
let references = [reference('reference-existing',acquisition,{plan_id:'plan-existing',status:'planned'}),reference('reference-hidden',hidden,{catalog_only:true,note:hiddenNote})];
const plans = [{id:'plan-existing',title:'【验收样本】已有预算选房计划',angle:'对应家庭预算',brief:'使用现有计划入口',topic_id:'layout',channel:'抖音',status:'draft',opportunity_id:'reference-existing',content_version_id:null,source:{title:acquisition.title,source_url:acquisition.url},created_at:at}];
const requests = [], mutations = [], forbidden = [], errors = [], unhandled = [], checks = [], externalBlocked = [];
page.on('pageerror', error => errors.push(error.message));
await page.route('**/*', async route => {
 const request = route.request(), url = new URL(request.url()), path = url.pathname, method = request.method();
 if(url.origin !== new URL(appUrl).origin){externalBlocked.push(url.origin);return route.abort();}
 if(!path.startsWith('/api/'))return route.continue();
 let body;try{body=request.postDataJSON();}catch{body=null;}
 requests.push({method,path,body});
 if(method !== 'GET')mutations.push({method,path,body});
 const respond = (value,status=200) => route.fulfill({status,contentType:'application/json',body:JSON.stringify(value)});
 if(/\/saved-searches(?:\/|$)|\/research\/runs(?:\/|$)|\/operations\/(?:discovery|platform-accounts|platform-status)(?:\/|$)/.test(path)){
  forbidden.push({method,path,body});return respond({detail:'Online search/connection endpoints are forbidden in catalogue browsing.'},409);
 }
 if(path === '/api/auth/me')return respond(session);
 if(path === '/api/projects')return respond([project]);
 if(path === '/api/model/status')return respond({configured:true,provider:'fixture',model:'fixture'});
 if(path === `${base}/overview`)return respond({project,metrics:{published:0,inquiries:0,qualified:0,appointments:0,visits:0,views:null,unknown_rate:null},pending_inquiries:[],content_versions:[],publications:[],proposals:[]});
 if(path === `${base}/reports`)return respond({window:'D7',metrics:{views:null,likes:null,comments:null,saves:null,inquiries:null,qualified:null,window_complete:false,observed_publications:0,total_publications:0},publications:[],proposals:[]});
 if(path === `${base}/topics`)return respond(['tour','layout','light','space','kitchen'].map(id=>({id,title:id,eligible:true,missing:[],angle:'fixture',fact_refs:[]})));
 if(path === `${base}/operations/plans` && method === 'GET')return respond(plans);
 if(path === `${research}/catalog` && method === 'GET')return respond(catalog);
 if(path === `${research}/references` && method === 'GET')return respond(references.filter(value=>!value.catalog_only));
 if(path === `${research}/references` && method === 'POST'){
  if(body?.job_id){
   assert.deepEqual(body,{job_id:policy.job_id,platform:policy.platform,content_id:policy.content_id},'collection sends only the genuine source job identity');
   const saved = reference('reference-collected',policy);references=[...references,saved];return respond(saved,201);
  }
  assert.deepEqual(body,{source_url:hidden.url,title:hidden.title},'hidden indexed references are collected with only their source URL and title');
  const index=references.findIndex(value=>value.id==='reference-hidden');assert.notEqual(index,-1);
  references[index]={...references[index],catalog_only:false};return respond(references[index],201);
 }
 if(path.startsWith(`${research}/references/`) && path.endsWith('/outcomes')){
  const id=path.split('/').at(-2);return respond({reference_id:id,plans:id==='reference-existing'?[{id:'plan-existing',title:plans[0].title,content_id:null,stage:'planned',template_id:null}]:[],publications:[],verified_live_count:0,coverage_note:'仅展示隔离验收资料'});
 }
 if(path.startsWith(`${research}/references/`) && method === 'GET'){
  const found=references.find(value=>value.id===path.split('/').at(-1));return found?respond(found):respond({detail:'Missing reference fixture'},404);
 }
 if(path === `${research}/capabilities`)return respond({video_analysis:{ready:false,local_asr_ready:false,vision_ready:false,ffmpeg_ready:false},evidence_note:'No actual analysis may run in browser acceptance.'});
 if([`${base}/assets`,`${base}/contents`,`${base}/publications`,`${base}/operations/accounts`,`${base}/operations/opportunities`,`${base}/conversations`,`${base}/jobs`].includes(path) && method === 'GET')return respond([]);
 unhandled.push(`${method} ${path}`);return respond({detail:'Unhandled isolated browser fixture'},404);
});

const card = source => page.locator(`article[data-content-key="${source.platform}:${source.content_id}"]`);
const types = () => page.getByRole('tablist',{name:'内容类型',exact:true});
const browsing = () => page.getByRole('tablist',{name:'内容浏览',exact:true});
const detail = () => page.getByRole('dialog',{name:'参考研究与原片拆解',exact:true});
async function studio(){await page.getByRole('navigation',{name:'主导航',exact:true}).getByRole('button',{name:'内容工作台',exact:true}).click();}
async function settled(){await page.evaluate(()=>new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve))));}
async function fits(label){assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=window.innerWidth),`${label}: no horizontal page overflow`);}
function noSearch(){assert.deepEqual(forbidden,[],'catalogue browsing never calls saved searches, search runs, platform search or platform connections');}
async function selectType(label){await types().getByRole('tab',{name:label,exact:true}).click();await settled();}
async function screenshot(name){await page.evaluate(()=>window.scrollTo(0,0));await settled();await page.screenshot({path:resolve(output,name),fullPage:page.viewportSize().width>500,animations:'disabled'});}

try {
 await mkdir(output,{recursive:true});
 await page.goto(appUrl,{waitUntil:'domcontentloaded',timeout:30000});await studio();
 await page.locator('.research-workspace').waitFor();
 assert.equal(await page.getByRole('button',{name:'搜索相关内容',exact:true}).count(),0,'online search action has been removed from the content workbench');
 assert.equal(await page.getByRole('tab',{name:'搜索发现',exact:true}).count(),0,'legacy online-search tab has been removed');
 assert.equal(await page.getByRole('tab',{name:'搜索记录',exact:true}).count(),0,'legacy search-history tab has been removed');
 assert.equal(await page.locator('.research-search-form').count(),0,'online search form has been removed');
 assert.equal(await page.locator('.research-workspace input[type="search"]').count(),0,'catalogue has no editable online query');
 await browsing().getByRole('tab',{name:'内容灵感',exact:true}).waitFor();
 await card(policy).waitFor();noSearch();
 assert.equal(mutations.length,0,'opening catalogue performs no writes');
 assert.equal(requests.filter(value=>value.path===`${research}/catalog`).length,1,'catalogue is read once on opening');
 checks.push('old search UI removed; opening reads a prepared catalogue without online-search or connection requests');

 assert.equal(await types().getByRole('tab').count(),5,'all five content categories are shown');
 for(const category of categories)assert.equal(await types().getByRole('tab',{name:category.label,exact:true}).getAttribute('aria-selected'),category.id==='professional'?'true':'false');
 assert.equal(await page.getByRole('button',{name:'全部选题',exact:true}).getAttribute('aria-pressed'),'true');
 assert.equal(await card(layout).count(),1);assert.equal(await card(traffic).count(),0);
 assert.equal(await card(policy).getByRole('link',{name:'查看原作',exact:true}).getAttribute('href'),policy.url);
 const measures=await card(policy).innerText();
 assert.match(measures,/点赞\s*0(?:\s|$)/,'an observed zero is preserved');
 assert.match(await card(layout).innerText(),/点赞\s*待观察/,'missing likes are not imputed to zero');
 assert.doesNotMatch(await card(layout).innerText(),/点赞\s*0(?:\s|$)/,'an unknown observation is never displayed as a zero');
 checks.push('professional is the default; genuine source link and unknown/zero metrics retain distinct states');

 const beforeFilters=requests.length;
 await page.getByRole('button',{name:'政策解读',exact:true}).click();await settled();
 assert.equal(await card(policy).count(),1);assert.equal(await card(layout).count(),0);
 assert.equal(await page.getByRole('button',{name:'政策解读',exact:true}).getAttribute('aria-pressed'),'true');
 await page.getByRole('button',{name:'户型点评',exact:true}).click();await settled();
 assert.equal(await card(policy).count(),0);assert.equal(await card(layout).count(),1);
 await page.getByRole('button',{name:'全部选题',exact:true}).click();await settled();
 assert.equal(await card(policy).count(),1);assert.equal(await card(layout).count(),1);
 await selectType('流量内容');assert.equal(await card(traffic).count(),1);assert.equal(await card(policy).count(),0);
 assert.equal(await page.getByRole('button',{name:'全部选题',exact:true}).getAttribute('aria-pressed'),'true','changing category resets local subject filter');
 await selectType('信任内容');assert.equal(await page.locator('article[data-content-key]').count(),0);
 assert.match(await page.locator('.research-workspace').innerText(),/待整理|整理中|正在整理/,'a category without material communicates pending collection');
 await screenshot('pending-desktop.png');
 await selectType('转化内容');assert.equal(await page.locator('article[data-content-key]').count(),0);
 assert.match(await page.locator('.research-workspace').innerText(),/暂未收录作品|暂不可用|受阻|未完成/,'unavailable collection is not presented as completed search with zero hits');
 await screenshot('blocked-desktop.png');
 await selectType('获客内容');assert.equal(await card(acquisition).count(),1);assert.equal(await card(hidden).count(),1);
 assert.equal(requests.length,beforeFilters,'type and subject switches filter locally without even another GET');
 assert.equal(mutations.length,0);noSearch();
 checks.push('all five categories browse prepared results; subject chips filter locally; pending and blocked categories remain explicit');

 await card(acquisition).getByRole('button',{name:'查看研究',exact:true}).click();await detail().waitFor();
 assert.equal(await detail().getByLabel('原片口播或字幕').inputValue(),'已保存的研究笔记');
 assert.equal(mutations.length,0,'opening an existing reference causes no collection POST');
 await fits('desktop existing-reference dialog');
 await page.screenshot({path:resolve(output,'reference-desktop.png'),animations:'disabled'});
 await detail().getByRole('button',{name:'查看内容计划',exact:true}).click();
 await page.getByText('已定位此计划',{exact:true}).waitFor();
 await page.getByText(plans[0].title,{exact:true}).waitFor();
 assert.equal(mutations.length,0,'opening a reference plan does not duplicate that plan');
 checks.push('an already collected card opens its actual reference notes and existing content plan without another POST');

 await page.locator('.studio-steps').getByRole('button',{name:/发现/}).click();
 await browsing().getByRole('tab',{name:'内容灵感',exact:true}).click();
 await selectType('获客内容');await card(hidden).waitFor();
 assert.equal(await card(hidden).getByRole('button',{name:'查看研究',exact:true}).count(),0,'a catalogue-only reference is not presented as already personally collected');
 assert.match(await card(hidden).innerText(),/点赞\s*待观察/,'hidden indexed references retain unknown engagement');
 await browsing().getByRole('tab',{name:/^我的参考/}).click();
 assert.equal(await page.getByText(hidden.title,{exact:true}).count(),0,'hidden catalogue rows stay out of My References');
 await browsing().getByRole('tab',{name:'内容灵感',exact:true}).click();
 await card(hidden).getByRole('button',{name:'收藏并研究',exact:true}).click();await detail().waitFor();
 assert.equal(mutations.length,1,'collecting a hidden indexed reference writes once');
 assert.deepEqual(mutations[0],{method:'POST',path:`${research}/references`,body:{source_url:hidden.url,title:hidden.title}});
 assert.equal(await detail().getByLabel('研究重点与复盘结论').inputValue(),hiddenNote,'collecting a catalogue row preserves its previous research note');
 assert.equal(references.find(value=>value.id==='reference-hidden').source_snapshot.metrics.likes,null,'collection never invents metrics for a hidden reference');
 assert.equal(references.filter(value=>value.id==='reference-hidden').length,1,'collection reuses the hidden reference identity');
 await page.screenshot({path:resolve(output,'hidden-reference-desktop.png'),animations:'disabled'});
 await detail().getByRole('button',{name:'关闭',exact:true}).click();
 await browsing().getByRole('tab',{name:/^我的参考/}).click();
 const hiddenReference=page.locator('.research-reference-card').filter({hasText:hidden.title});
 await hiddenReference.waitFor();assert.match(await hiddenReference.innerText(),/点赞\s*未获取/);assert.match(await hiddenReference.innerText(),/后台整理保留的选题观察/);
 await browsing().getByRole('tab',{name:'内容灵感',exact:true}).click();
 await card(hidden).getByRole('button',{name:'查看研究',exact:true}).click();await detail().waitFor();
 assert.equal(mutations.length,1,'reopening the collected hidden reference never repeats collection');
 await detail().getByRole('button',{name:'关闭',exact:true}).click();
 checks.push('catalogue-only references remain hidden from My References until one explicit collection; source-only POST reuses identity and preserves unknown metrics and saved notes');

 await selectType('专业内容');await card(policy).waitFor();
 const beforeCapture=mutations.length;
 await card(policy).getByRole('button',{name:'收藏并研究',exact:true}).click();await detail().waitFor();
 assert.equal(mutations.length,beforeCapture+1,'only the explicit collection action writes');
 assert.deepEqual(mutations.at(-1),{method:'POST',path:`${research}/references`,body:{job_id:policy.job_id,platform:policy.platform,content_id:policy.content_id}});
 await detail().getByRole('button',{name:'关闭',exact:true}).click();
 await browsing().getByRole('tab',{name:/^我的参考/}).click();
 await page.getByText(policy.title,{exact:true}).waitFor();
 assert.equal(await page.locator('.research-reference-card').count(),3);
 checks.push('explicit collection sends job/platform/content identity only and appears in My References');

 await page.locator('.toast').waitFor({state:'hidden',timeout:8000});
 await browsing().getByRole('tab',{name:'内容灵感',exact:true}).click();await selectType('专业内容');
 await page.getByRole('button',{name:'全部选题',exact:true}).click();await fits('1440px catalogue');await screenshot('catalog-desktop.png');
 await page.setViewportSize({width:390,height:844});await settled();await fits('390px catalogue');await screenshot('catalog-mobile.png');
 await page.locator('.content-catalog').screenshot({path:resolve(output,'catalog-mobile-content.png'),animations:'disabled'});
 await selectType('流量内容');await card(traffic).waitFor();await fits('390px category switch');
 await selectType('信任内容');await fits('390px pending category');await screenshot('pending-mobile.png');
 await selectType('获客内容');await card(acquisition).getByRole('button',{name:'查看研究',exact:true}).click();await detail().waitFor();
 assert.ok(await detail().evaluate(element=>element.scrollWidth<=element.clientWidth),'390px reference dialog has no inner horizontal overflow');
 await fits('390px existing-reference dialog');await page.screenshot({path:resolve(output,'reference-mobile.png'),animations:'disabled'});
 await detail().getByRole('button',{name:'关闭',exact:true}).click();
 await browsing().getByRole('tab',{name:/^我的参考/}).click();await fits('390px My References');await screenshot('references-mobile.png');
 checks.push('catalogue, pending category, My References and reference details fit 1440px/390px viewports');
 noSearch();assert.deepEqual(errors,[],'no React/browser runtime errors');assert.deepEqual(unhandled,[],'every API call has an explicit isolated fixture');
 const receipt={status:'passed',phase,mode:'isolated HTTP fixtures; no external platform, model or persistent database writes',appUrl,checks,errors,unhandled,forbidden,requests,mutations,externalBlocked:[...new Set(externalBlocked)]};
 await writeFile(resolve(output,'receipt.json'),JSON.stringify(receipt,null,2));
 process.stdout.write(`Content catalogue browser acceptance passed (${checks.length} groups); receipt: ${resolve(output,'receipt.json')}\n`);
} catch(error) {
 await mkdir(output,{recursive:true});await screenshot('failure.png').catch(()=>{});
 await writeFile(resolve(output,'receipt.json'),JSON.stringify({status:'failed',phase,error:error.message,mode:'isolated HTTP fixtures; no persistent writes',appUrl,checks,errors,unhandled,forbidden,requests,mutations},null,2));
 throw error;
} finally {
 await browser.close();
}
