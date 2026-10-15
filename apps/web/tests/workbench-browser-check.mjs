/** Exercise the actual app with isolated HTTP fixtures; no business database or AI calls. */
import assert from 'node:assert/strict';
import {createRequire} from 'node:module';
import {mkdir,writeFile} from 'node:fs/promises';
import {resolve} from 'node:path';
const require=createRequire(import.meta.url);
const {chromium}=require(process.env.HS_PLAYWRIGHT_MODULE||'playwright');
const browser=await chromium.launch({headless:true,channel:process.env.HS_BROWSER_CHANNEL||'chrome'});
const page=await browser.newPage({viewport:{width:1440,height:1100}});
page.setDefaultTimeout(12000);
const output=resolve(process.env.HS_WORKBENCH_QA_OUTPUT||'../../reports/workbench-integration-20261014');
const date='2026-10-14T02:00:00Z';
const session={user:{id:'qa',name:'看板测试',email:'qa@example.invalid'},organization:{id:'qa-org',name:'测试组织'},role:'owner'};
const makeProject=(id,name)=>({id,name,city:'上海',property_type:'住宅',channel:'抖音',owner_name:'测试内容负责人',audience:'改善居住需求',sales_owner_name:'测试销售',marketing_confirmed:true,marketing_evidence:'fixture',is_test:true,completeness:{percent:86,completed:6,total:7,items:[{key:'facts',label:'价格依据',complete:false,detail:'待核对当前报价',target:'facts'}]}});
const projects=[makeProject('workbench-a','看板测试甲项目'),makeProject('workbench-b','看板测试乙项目')];
const proposal={id:'suggestion-a',title:'回应客户的采光问题',reason:'两条咨询提到客厅日照，建议补充实拍依据。',status:'pending',source_refs:[],created_at:date};
const publication={id:'publication-a',release_id:'release-a',title:'甲项目采光实拍',channel:'抖音',url:'https://example.invalid/observed-video',platform_state:'live',receipt_status:'verified',metrics:{D7:{views:1200,likes:0,comments:null,saves:null,inquiries:null,qualified:null},D30:null},created_at:date};
const inquiry={id:'inquiry-a',name:'合成客户甲',need:'了解客厅日照',channel:'抖音',source_confidence:'unknown',source_text:'',contactable:false,consent:false,qualified:false,created_at:date};
const content={id:'content-a',series_id:'series-a',version:1,topic:'甲项目采光实拍',script:'已核实项目事实的合成脚本。',status:'approved',channel:'抖音',cover_title:'客厅采光',aspect_ratio:'9:16',subtitle_style:'minimal',storyboard:[],fact_refs:[],created_at:date};
const requests=[],errors=[],unhandled=[];
let reportError=false,holdA=false,releaseA;
page.on('pageerror',error=>errors.push(error.message));
await page.route('**/api/**',async route=>{
 const request=route.request(),url=new URL(request.url()),path=url.pathname,method=request.method(),body=request.postDataJSON();
 requests.push({path,query:url.search,method,body});
 const respond=(value,status=200)=>route.fulfill({status,contentType:'application/json',body:JSON.stringify(value)});
 if(path==='/api/auth/me')return respond(session);
 if(path==='/api/projects')return respond(projects);
 if(path==='/api/model/status')return respond({provider:'fixture',model:'fixture',configured:true});
 if(path==='/api/conversations/conversation-a/draft')return respond({draft:body.draft});
 if(path==='/api/conversations/conversation-a/messages')return respond([{id:'answer-a',role:'assistant',content:'已有项目效果记录，可继续查看。',status:'succeeded',citations:[{kind:'page',page:'reports',label:'历史效果复盘入口'}]}]);
 if(path==='/api/projects/workbench-a/conversations')return respond([{id:'conversation-a',title:'测试项目对话',draft:'',created_at:date}]);
 const match=path.match(/^\/api\/projects\/(workbench-[ab])\/(.+)$/);
 if(match){
  const [,id,resource]=match,first=id==='workbench-a',project=projects.find(p=>p.id===id);
  if(resource==='overview')return respond({project,metrics:{published:first?1:0,inquiries:first?4:0,qualified:first?2:0,appointments:first?1:0,visits:0,views:first?1200:null,unknown_rate:first?25:null},pending_inquiries:first?[inquiry]:[],content_versions:first?[content]:[],publications:first?[publication]:[],proposals:first?[proposal]:[]});
  if(resource==='reports'){
   if(reportError)return respond({detail:'合成效果接口暂不可用'},503);
   if(holdA&&first)await new Promise(done=>{releaseA=done;});
   const window=url.searchParams.get('window')||'D7',values=first?publication.metrics[window]:null;
   return respond({window,metrics:{views:values?.views??null,likes:values?.likes??null,comments:null,saves:null,inquiries:null,qualified:null,window_complete:Boolean(values),observed_publications:values?1:0,total_publications:first?1:0},publications:first?[publication]:[],proposals:first?[proposal]:[]});
  }
  if(resource==='publications/publication-a/verify'&&method==='POST'){Object.assign(publication,{metrics:body.metrics,platform_state:body.platform_state});return respond(publication);}
  if(resource==='proposals/suggestion-a/approve'&&method==='POST'){proposal.status='approved';return respond(proposal);}
 }
 unhandled.push(`${method} ${path}`);return respond({detail:'Unexpected fixture request'},404);
});
const analysis=()=>page.getByRole('tab',{name:'效果分析',exact:true});
const overview=()=>page.getByRole('tab',{name:'项目总览',exact:true});
async function selectProject(name){
 await page.getByRole('button',{name:'切换当前项目',exact:true}).click();
 await page.getByRole('dialog',{name:'选择当前项目'}).getByRole('button').filter({hasText:name}).click();
 await overview().waitFor();assert.equal(await overview().getAttribute('aria-selected'),'true');
}
try{
 await page.goto(process.env.HS_WORKBENCH_QA_URL||'http://127.0.0.1:8017/');
 // Catches keeping chat as the default or leaving performance as a separate primary destination.
 await page.getByRole('heading',{name:'工作台',exact:true}).waitFor();
 assert.equal(await overview().getAttribute('aria-selected'),'true');
 assert.equal(await page.getByRole('navigation',{name:'主导航'}).getByRole('button',{name:'效果复盘',exact:true}).count(),0);
 assert.equal(await page.getByRole('navigation',{name:'主导航'}).getByRole('button',{name:'工作台',exact:true}).getAttribute('aria-current'),'page');
 await page.getByText('回应客户的采光问题',{exact:true}).waitFor();
 await analysis().click();
 await page.getByRole('heading',{name:'已核实发布与指标回填',exact:true}).waitFor();
 assert.equal(await analysis().getAttribute('aria-selected'),'true');
 assert.equal(await page.getByRole('navigation',{name:'主导航'}).getByRole('button',{name:'工作台',exact:true}).getAttribute('aria-current'),'page');
 await Promise.all([page.waitForResponse(response=>response.url().includes('reports?window=D30')),page.getByRole('button',{name:'发布后 D30',exact:true}).click()]);
 await page.getByText('待观察 / 未完整回填',{exact:true}).first().waitFor();
 // Catches loss of manual receipt/metrics writes when the report is nested.
 await page.getByRole('button',{name:'核对 / 回填',exact:true}).click();
 let dialog=page.getByRole('dialog',{name:'核实发布回执'});
 assert.equal(await dialog.getByLabel('观察窗口').inputValue(),'D30');
 await dialog.getByLabel('播放量',{exact:true}).fill('2500');
 await dialog.getByLabel('已打开链接核对平台状态及填写的指标').check();
 await dialog.getByRole('button',{name:'确认回执',exact:true}).click();
 await dialog.waitFor({state:'hidden'});
 assert.equal(publication.metrics.D30.views,2500);
 assert.equal(publication.metrics.D30.likes,null);
 const saved=requests.find(r=>r.path.endsWith('/publication-a/verify'));
 assert.equal(saved.body.confirmed,true);assert.equal(saved.body.metrics.D7.likes,0);
 await page.getByRole('button',{name:'查看并采纳',exact:true}).click();
 dialog=page.getByRole('dialog',{name:'采纳复盘建议'});
 await dialog.getByRole('button',{name:'确认采纳',exact:true}).click();
 await dialog.waitFor({state:'hidden'});await page.getByText('已采纳',{exact:true}).waitFor();
 assert.equal(proposal.status,'approved');
 await mkdir(output,{recursive:true});
 await page.screenshot({path:resolve(output,'web-analysis-desktop.png'),fullPage:true});
 // Catches old Assistant page citations becoming orphaned when reports leaves the sidebar.
 await page.getByRole('navigation',{name:'楼管家'}).getByRole('button',{name:'楼管家',exact:true}).click();
 await page.getByText('依据与记录 · 1 项',{exact:true}).click();
 await page.getByRole('button',{name:'历史效果复盘入口',exact:true}).click();
 await page.getByRole('dialog',{name:'历史效果复盘入口'}).getByRole('button',{name:'查看对应页面',exact:true}).click();
 await analysis().waitFor();assert.equal(await analysis().getAttribute('aria-selected'),'true');
 // Catches project switches inheriting another project's report/window or late metrics.
 await selectProject('看板测试乙项目');
 assert.equal(await page.getByText('甲项目采光实拍',{exact:true}).count(),0);
 assert.equal(await page.getByText('回应客户的采光问题',{exact:true}).count(),0);
 await analysis().click();
 await page.getByRole('heading',{name:'已核实发布与指标回填',exact:true}).waitFor();
 assert.ok(requests.some(r=>r.path==='/api/projects/workbench-b/reports'&&r.query==='?window=D7'));
 await selectProject('看板测试甲项目');
 holdA=true;await Promise.all([page.waitForRequest(request=>request.url().includes('/workbench-a/reports')),analysis().click()]);
 await selectProject('看板测试乙项目');
 releaseA?.();holdA=false;
 await page.getByRole('heading',{name:'工作台',exact:true}).waitFor();
 assert.equal(await page.getByText('甲项目采光实拍',{exact:true}).count(),0);
 // Catches effects failures being replaced by successful-looking zeros.
 reportError=true;await analysis().click();
 await page.getByRole('alert').filter({hasText:'合成效果接口暂不可用'}).waitFor();
 reportError=false;await page.getByRole('alert').getByRole('button',{name:'重试',exact:true}).click();
 await page.getByRole('heading',{name:'已核实发布与指标回填',exact:true}).waitFor();
 await overview().click();
 await page.screenshot({path:resolve(output,'web-overview-empty.png'),fullPage:true});
 await selectProject('看板测试甲项目');
 await page.getByText('甲项目采光实拍',{exact:true}).waitFor();
 await page.screenshot({path:resolve(output,'web-overview-desktop.png'),fullPage:true});
 await page.setViewportSize({width:390,height:844});
 await page.screenshot({path:resolve(output,'web-overview-mobile.png'),fullPage:true});
 assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=window.innerWidth),'overview overflows the mobile viewport');
 await overview().focus();await page.keyboard.press('ArrowRight');
 assert.equal(await analysis().getAttribute('aria-selected'),'true');
 await page.getByRole('heading',{name:'已核实发布与指标回填',exact:true}).waitFor();
 await page.screenshot({path:resolve(output,'web-analysis-mobile.png'),fullPage:true});
 assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=window.innerWidth),'analysis overflows the mobile viewport');
 assert.deepEqual(errors,[]);assert.deepEqual(unhandled,[]);
 await writeFile(resolve(output,'web-browser-receipt.json'),JSON.stringify({mode:'isolated browser HTTP fixtures; no business writes or model calls',checks:['default project workbench','one primary dashboard destination','overview and analysis tabs','per-publication D7/D30','receipt metrics save with null and zero preservation','manual proposal adoption','legacy reports citations','project switch resets to overview and D7','late report response cannot cross projects','report failure and retry','mobile layout','keyboard tabs','zero page errors'],mutations:requests.filter(r=>r.method!=='GET')},null,2));
 process.stdout.write(`Workbench browser checks passed; receipt: ${output}/web-browser-receipt.json\n`);
}finally{releaseA?.();await browser.close();}
