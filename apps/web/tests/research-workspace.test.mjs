import {after,before,test} from 'node:test';
import assert from 'node:assert/strict';
import {existsSync} from 'node:fs';
import {mkdtemp,rm,writeFile} from 'node:fs/promises';
import {createServer} from 'node:http';
import {tmpdir} from 'node:os';
import {join,resolve} from 'node:path';
import {pathToFileURL} from 'node:url';
import {build} from 'esbuild';
import React from 'react';
import {renderToStaticMarkup} from 'react-dom/server';
let ui,dir,server,origin;const requests=[];
before(async()=>{dir=await mkdtemp(join(tmpdir(),'louxiang-research-'));const src=resolve('src/ResearchWorkspace.tsx');const result=await build({stdin:{contents:`export * from ${JSON.stringify(src)};export * as catalogUI from ${JSON.stringify(resolve('src/ContentCatalog.tsx'))};export {AppContext} from ${JSON.stringify(resolve('src/hooks.tsx'))};`,resolveDir:process.cwd(),loader:'tsx'},write:false,bundle:true,format:'esm',platform:'node',jsx:'automatic',external:['react','react-dom','react/jsx-runtime'],loader:{'.css':'empty'},plugins:[{name:'missing',setup(b){if(!existsSync(src)){b.onResolve({filter:/ResearchWorkspace\.tsx$/},()=>({path:'missing',namespace:'fixture'}));b.onLoad({filter:/.*/,namespace:'fixture'},()=>({loader:'js',contents:'export const ReferenceLibrary=()=>null; export const referenceStage=()=>null;export const analysisToNotes=()=>null;export const requestReferenceCapture=async()=>null;export const requestReferenceUpdate=async()=>null;'}));}}}]});const bundle=result.outputFiles[0].text.replaceAll('from "react"',`from ${JSON.stringify(pathToFileURL(resolve('node_modules/react/index.js')).href)}`).replaceAll('from "react/jsx-runtime"',`from ${JSON.stringify(pathToFileURL(resolve('node_modules/react/jsx-runtime.js')).href)}`);const dest=join(dir,'research.mjs');await writeFile(dest,bundle);ui=await import(pathToFileURL(dest));server=createServer(async(req,res)=>{const chunks=[];for await(const c of req)chunks.push(c);requests.push({path:req.url,method:req.method,body:JSON.parse(Buffer.concat(chunks).toString()||'{}')});res.writeHead(200,{'content-type':'application/json'});res.end(JSON.stringify({id:'r-1',revision:3}));});await new Promise(r=>server.listen(0,'127.0.0.1',r));origin=`http://127.0.0.1:${server.address().port}`;});
after(async()=>{server?.close();if(dir)await rm(dir,{recursive:true,force:true});});
const reference={id:'r-1',title:'改善型住宅参考',source_url:'https://example.com/video',platform:'抖音',observed_at:'2026-10-14T01:00:00Z',status:'saved',curation_state:'shortlisted',note:'关注厨房动线',tags:['户型'],source_snapshot:{metrics:{likes:0,views:null}},research_notes:{},analysis_snapshot:{},revision:2};
// Catches online search controls being exposed instead of the prepared content catalog.
test('discovery opens five prepared categories without online search or manual collection controls',()=>{
 const html=renderToStaticMarkup(React.createElement(ui.AppContext.Provider,{value:{project:{id:'p-1'},session:{role:'owner'},notify(){}}},React.createElement(ui.ResearchWorkspace,{onPlan(){}})));
 assert.match(html,/<h2>内容灵感<\/h2>/);assert.match(html,/我的参考/);
 for(const label of ['专业内容','流量内容','信任内容','获客内容','转化内容'])assert.match(html,new RegExp(`role="tab"[^>]*aria-label="${label}"`));
 assert.match(html,/id="content-type-professional"[^>]*aria-selected="true"/);
 assert.doesNotMatch(html,/<form|type="search"|保存方向|搜索相关内容|搜索记录|登记作品链接|搜索账号与连接状态|搜索关键词/);
});
const catalogItem={platform:'bilibili',content_id:'BVPolicy',title:'首次买房需要核对的政策条件',url:'https://www.bilibili.com/video/BVPolicy',author:'真实作者',published_at:'2026-10-12T03:00:00Z',metrics:{views:null,likes:0,comments:null,saves:null,shares:null},metrics_observed_at:'2026-10-14T01:00:00Z',metrics_source:'bilibili_public_api',match_keywords:['购房政策'],qualification:'candidate_unverified',historical:false,qualification_reason:'真实平台返回',snippet:'先看政策的适用人群，再核对自己的条件。',job_id:'job-real',subject:'购房政策'};
const preparedCatalog={categories:[{id:'professional',label:'专业内容',purpose:'帮助用户理解房产',subjects:[{label:'政策解读',query:'购房政策'},{label:'户型点评',query:'户型分析'}],items:[catalogItem,{...catalogItem,content_id:'BVLayout',title:'三房户型的家务动线',subject:'户型分析',metrics:{...catalogItem.metrics,likes:null}}],updated_at:'2026-10-14T01:00:00Z',collection_state:'ready'},{id:'traffic',label:'流量内容',purpose:'让潜在客户愿意点开',subjects:[{label:'楼市热点',query:'楼市热点'}],items:[{...catalogItem,content_id:'BVHot',title:'本周楼市话题',subject:'楼市热点'}],updated_at:'2026-10-14T01:00:00Z',collection_state:'ready'}],updated_at:'2026-10-14T01:00:00Z',total:3};
// Catches a fresh index or category timestamp making an older measured count look newly observed.
test('catalog keeps the metric sample date separate from a newer work collection date',()=>{
 const item={...catalogItem,collected_at:'2026-10-15T08:00:00Z',metrics_observed_at:'2026-10-14T01:00:00Z'};
 const catalog={...preparedCatalog,categories:[{...preparedCatalog.categories[0],items:[item],updated_at:'2026-10-15T08:00:00Z'}]};
 const html=renderToStaticMarkup(React.createElement(ui.ContentCatalog,{catalog,type:'professional',subject:'',editable:false,onSelectType(){},onSelectSubject(){},onCapture(){},onOpenReference(){}}));
 const card=html.match(/<article\b[\s\S]*?<\/article>/)?.[0];assert.ok(card);
 assert.match(card,/点赞[\s\S]*?<strong>0<\/strong>/);
 assert.match(card,/互动采样于 10\/14 09:00/);assert.match(card,/作品采集于 10\/15 16:00/);
 assert.doesNotMatch(card,/互动采样于 10\/15/);
});
// Catches a work with no timestamps borrowing a date from another work in the category.
test('catalog never fills a missing work or metric date from its category update',()=>{
 const item={...catalogItem,collected_at:null,metrics_observed_at:null};
 const catalog={...preparedCatalog,categories:[{...preparedCatalog.categories[0],items:[item],updated_at:'2026-10-15T08:00:00Z'}]};
 const html=renderToStaticMarkup(React.createElement(ui.ContentCatalog,{catalog,type:'professional',subject:'',editable:false,onSelectType(){},onSelectSubject(){},onCapture(){},onOpenReference(){}}));
 const card=html.match(/<article\b[\s\S]*?<\/article>/)?.[0];assert.ok(card);
 assert.match(html,/更新于 10\/15 16:00/);assert.match(card,/互动采样时间待补充/);assert.match(card,/作品采集时间待补充/);
 assert.doesNotMatch(card,/10\/15|16:00/);
});
// Catches an indexed source with no measured values being presented as a completed metric sample.
test('catalog leaves unknown interaction values pending even when an index has an observation timestamp',()=>{
 const item={...catalogItem,metrics:{views:null,likes:null,comments:null,saves:null,shares:null},collected_at:'2026-10-15T08:00:00Z'};
 const catalog={...preparedCatalog,categories:[{...preparedCatalog.categories[0],items:[item]}]};
 const html=renderToStaticMarkup(React.createElement(ui.ContentCatalog,{catalog,type:'professional',subject:'',editable:false,onSelectType(){},onSelectSubject(){},onCapture(){},onOpenReference(){}}));
 const card=html.match(/<article\b[\s\S]*?<\/article>/)?.[0];assert.ok(card);
 assert.match(card,/点赞[\s\S]*?<strong>待观察<\/strong>/);assert.match(card,/互动指标待观察/);
 assert.match(card,/作品采集于 10\/15 16:00/);assert.doesNotMatch(card,/互动采样于/);
});
const catalogVideo={...catalogItem,content_id:'BV1Ab411c7XY',url:'https://www.bilibili.com/video/BV1Ab411c7XY',cover_url:'https://i0.hdslb.com/bfs/archive/real-cover.jpg',player_url:'https://attacker.invalid/player.html'};
// Catches a real prepared cover remaining invisible and a video requiring a detour through collection to watch it.
test('prepared video cards display the real cover and a direct playback action',()=>{
 const catalog={...preparedCatalog,categories:[{...preparedCatalog.categories[0],items:[catalogVideo]}]};
 const html=renderToStaticMarkup(React.createElement(ui.ContentCatalog,{catalog,type:'professional',subject:'',editable:true,onSelectType(){},onSelectSubject(){},onCapture(){},onOpenReference(){}}));
 assert.match(html,/<img[^>]*src="https:\/\/i0\.hdslb\.com\/bfs\/archive\/real-cover\.jpg"/);
 assert.match(html,/aria-label="播放：首次买房需要核对的政策条件"/);assert.match(html,/查看原作/);
 assert.doesNotMatch(html,/<iframe|attacker\.invalid/);
});
// Catches iframe injection, platform confusion, and playing a different BV from the indexed original.
test('catalog playback only constructs an official player for the exact canonical B站 source',()=>{
 assert.equal(typeof ui.catalogUI.catalogPlayerUrl,'function');
 assert.equal(ui.catalogUI.catalogPlayerUrl(catalogVideo),'https://player.bilibili.com/player.html?bvid=BV1Ab411c7XY&p=1&autoplay=1&muted=0&poster=1');
 for(const change of [{platform:'douyin'},{content_id:'BV1Xz411c7XY'},{url:'https://www.bilibili.com.attacker.invalid/video/BV1Ab411c7XY'},{url:'https://www.bilibili.com@attacker.invalid/video/BV1Ab411c7XY'},{url:'http://www.bilibili.com/video/BV1Ab411c7XY'},{url:'https://www.bilibili.com:8443/video/BV1Ab411c7XY'},{url:'javascript:alert(1)'},{url:'https://www.bilibili.com/video/BV1Ab411c7XY/other'}])assert.equal(ui.catalogUI.catalogPlayerUrl({...catalogVideo,...change}),null);
});
// Catches untrusted image hosts and empty covers creating broken image elements instead of a watchable neutral fallback.
test('catalog cover trust and missing cover fallback preserve the verified playback action',()=>{
 assert.equal(typeof ui.catalogUI.catalogCoverUrl,'function');
 assert.equal(ui.catalogUI.catalogCoverUrl(catalogVideo),'https://i0.hdslb.com/bfs/archive/real-cover.jpg');
 assert.equal(ui.catalogUI.catalogCoverUrl({...catalogVideo,cover_url:'https://archive.biliimg.com/cover.jpg'}),'https://archive.biliimg.com/cover.jpg');
 for(const cover_url of [null,'http://i0.hdslb.com/cover.jpg','https://i0.hdslb.com.attacker.invalid/cover.jpg','https://attacker.invalid/cover.jpg','data:image/svg+xml,<svg/>'])assert.equal(ui.catalogUI.catalogCoverUrl({...catalogVideo,cover_url}),null);
 const catalog={...preparedCatalog,categories:[{...preparedCatalog.categories[0],items:[{...catalogVideo,cover_url:null}]}]};
 const html=renderToStaticMarkup(React.createElement(ui.ContentCatalog,{catalog,type:'professional',subject:'',editable:false,onSelectType(){},onSelectSubject(){},onCapture(){},onOpenReference(){}}));
 assert.match(html,/aria-label="播放：首次买房需要核对的政策条件"/);assert.doesNotMatch(html,/<img|<iframe/);
 const play=Array.from(html.matchAll(/<button\b[^>]*>[\s\S]*?<\/button>/g),m=>m[0]).find(button=>button.includes('aria-label="播放：'));assert.ok(play);assert.doesNotMatch(play,/disabled=""/);
});
// Catches a cover click opening a paused player, missing autoplay permission, arbitrary iframe sources, and lost original-site access.
test('video dialog requests audible playback after a cover click and retains B站 original access',()=>{
 assert.equal(typeof ui.catalogUI.CatalogVideoPlayer,'function');
 const html=renderToStaticMarkup(React.createElement(ui.catalogUI.CatalogVideoPlayer,{item:catalogVideo,onClose(){}}));
 assert.match(html,/role="dialog"[^>]*aria-modal="true"[^>]*aria-label="首次买房需要核对的政策条件"/);
 assert.match(html,/<iframe[^>]*src="https:\/\/player\.bilibili\.com\/player\.html\?bvid=BV1Ab411c7XY&amp;p=1&amp;autoplay=1&amp;muted=0&amp;poster=1"/);
 assert.match(html,/<iframe[^>]*allow="autoplay; fullscreen; picture-in-picture"/);
 assert.match(html,/title="B站播放器：首次买房需要核对的政策条件"/);assert.match(html,/allowFullScreen=""/);assert.match(html,/在B站打开/);assert.match(html,/aria-label="关闭弹窗"/);
 assert.doesNotMatch(html,/attacker\.invalid|autoplay=0|muted=1|下载原片|已播放/);
});
// Catches unsupported platforms appearing to play in a false or arbitrary embedded player.
test('unsupported platform dialog offers its verified original link and never embeds a player',()=>{
 assert.equal(typeof ui.catalogUI.CatalogVideoPlayer,'function');
 const html=renderToStaticMarkup(React.createElement(ui.catalogUI.CatalogVideoPlayer,{item:{...catalogVideo,platform:'douyin',content_id:'123456789',url:'https://www.douyin.com/video/123456789'},onClose(){}}));
 assert.match(html,/href="https:\/\/www\.douyin\.com\/video\/123456789"/);assert.match(html,/在抖音打开/);assert.doesNotMatch(html,/<iframe|player\.bilibili|attacker\.invalid/);
 const unsafe=renderToStaticMarkup(React.createElement(ui.catalogUI.CatalogVideoPlayer,{item:{...catalogVideo,platform:'douyin',url:'https://attacker.invalid/video/123456789'},onClose(){}}));assert.doesNotMatch(unsafe,/<iframe|href="https:\/\/attacker/);
});
// Catches a category or subject selection mixing unrelated works or hiding measured zero.
test('catalog displays only the selected cached subject with source evidence and real measured values',()=>{
 const html=renderToStaticMarkup(React.createElement(ui.ContentCatalog||(()=>null),{catalog:preparedCatalog,type:'professional',subject:'购房政策',editable:true,onSelectType(){},onSelectSubject(){},onCapture(){},onOpenReference(){}}));
 assert.match(html,/首次买房需要核对的政策条件/);assert.match(html,/真实作者/);assert.match(html,/先看政策的适用人群/);assert.match(html,/点赞[\s\S]*?0/);assert.match(html,/互动采样于/);assert.match(html,/href="https:\/\/www\.bilibili\.com\/video\/BVPolicy"/);assert.match(html,/收藏并研究/);
 assert.doesNotMatch(html,/三房户型的家务动线|本周楼市话题|搜索相关内容|type="search"/);
});
// Catches a library work being captured again or an unmeasured metric being presented as zero.
test('saved catalog works link to their research and missing metrics remain pending',()=>{
 const catalog={...preparedCatalog,categories:[{...preparedCatalog.categories[0],items:[{...catalogItem,reference_id:'r-existing',url:'javascript:alert(1)',metrics:{...catalogItem.metrics,likes:null}}]}]};
 const html=renderToStaticMarkup(React.createElement(ui.ContentCatalog||(()=>null),{catalog,type:'professional',subject:'',editable:false,onSelectType(){},onSelectSubject(){},onCapture(){},onOpenReference(){}}));
 assert.match(html,/查看研究/);assert.match(html,/待观察/);assert.doesNotMatch(html,/收藏并研究|href="javascript:|点赞[\s\S]*?<strong>0/);
});
// Catches an imported catalog-only source being treated as an already saved personal reference.
test('catalog-only sources offer collection even though a source reference already exists',()=>{
 const catalog={...preparedCatalog,categories:[{...preparedCatalog.categories[0],items:[{...catalogItem,job_id:undefined,reference_id:'catalog-ref',catalog_only:true}]}]};
 const html=renderToStaticMarkup(React.createElement(ui.ContentCatalog||(()=>null),{catalog,type:'professional',subject:'',editable:true,onSelectType(){},onSelectSubject(){},onCapture(){},onOpenReference(){}}));
 assert.match(html,/收藏并研究/);assert.doesNotMatch(html,/查看研究/);
 const action=Array.from(html.matchAll(/<button\b[^>]*>[\s\S]*?<\/button>/g),m=>m[0]).find(button=>button.includes('收藏并研究'));assert.ok(action);assert.doesNotMatch(action,/disabled=""/);
});
// Catches losing imported provenance by creating another record instead of capturing its exact source URL.
test('capturing an imported catalog source uses its original URL and title while job results keep server identity',async()=>{
 const native=globalThis.fetch;globalThis.fetch=(p,o)=>native(origin+p,o);requests.length=0;
 try{
  assert.equal(typeof ui.requestCatalogReferenceCapture,'function');
  await ui.requestCatalogReferenceCapture('p-1',{...catalogItem,job_id:undefined,reference_id:'catalog-ref',catalog_only:true});
  await ui.requestCatalogReferenceCapture('p-1',catalogItem);
  assert.deepEqual(requests,[{path:'/api/projects/p-1/operations/research/references',method:'POST',body:{source_url:'https://www.bilibili.com/video/BVPolicy',title:'首次买房需要核对的政策条件'}},{path:'/api/projects/p-1/operations/research/references',method:'POST',body:{job_id:'job-real',platform:'bilibili',content_id:'BVPolicy'}}]);
 }finally{globalThis.fetch=native;}
});
// Catches discarding later verified platform metrics when an indexed catalog source acquires a real job identity.
test('an indexed catalog source with a later real job captures authoritative job evidence',async()=>{
 const native=globalThis.fetch;globalThis.fetch=(p,o)=>native(origin+p,o);requests.length=0;
 try{
  await ui.requestCatalogReferenceCapture('p-1',{...catalogItem,reference_id:'catalog-ref',catalog_only:true});
  assert.deepEqual(requests,[{path:'/api/projects/p-1/operations/research/references',method:'POST',body:{job_id:'job-real',platform:'bilibili',content_id:'BVPolicy'}}]);
 }finally{globalThis.fetch=native;}
});
// Catches manufacturing example works when backend collection is still incomplete.
test('pending catalog preserves its empty collection state without inventing examples',()=>{
 const catalog={categories:[{id:'professional',label:'专业内容',purpose:'帮助用户理解房产',subjects:[{label:'政策解读',query:'购房政策'}],items:[],updated_at:null,collection_state:'collecting'}],updated_at:null,total:0};
 const html=renderToStaticMarkup(React.createElement(ui.ContentCatalog||(()=>null),{catalog,type:'professional',subject:'',editable:true,onSelectType(){},onSelectSubject(){},onCapture(){},onOpenReference(){}}));
 assert.match(html,/正在整理/);assert.doesNotMatch(html,/<article|href="https:|搜索相关内容/);
});
// Catches turning a chosen subject into a generic plan that loses its purpose and writing direction.
test('a selected policy subject carries its content purpose and outline into an editable plan draft',()=>{
 const draft=ui.createContentPlanDraft?.('professional','购房政策');
 assert.ok(draft,'a selected subject must provide a plan draft');
 assert.equal(draft.title,'政策解读');assert.equal(draft.topic_id,'tour');
 assert.match(draft.brief,/专业内容/);assert.match(draft.brief,/政策原文/);assert.match(draft.angle,/购房/);
 assert.equal(ui.createContentPlanDraft('professional','用户自己输入的关键词'),null,'custom searches must not be presented as a selected template');
});

// Catches relying on prose parsing and losing the selected category when a draft reaches planning.
test('category planning retains the selected content type and subject as structured context',()=>{
 const draft=ui.createContentPlanDraft('professional','购房政策');
 assert.equal(draft.content_type,'professional');assert.equal(draft.subject,'购房政策');assert.equal(draft.start_mode,'category');
});

// Catches reference planning dropping its durable source or replacing saved project direction with a generic one.
test('reference planning carries the saved source, notes, and chosen category into its draft',()=>{
 const draft=ui.createReferencePlanDraft?.({...reference,title:'三房家务动线参考',research_notes:{topic_id:'kitchen'},note:'借鉴先提问再实拍'},'professional','户型分析');
 assert.ok(draft);assert.equal(draft.reference_id,'r-1');assert.equal(draft.title,'三房家务动线参考');assert.equal(draft.topic_id,'kitchen');
 assert.equal(draft.content_type,'professional');assert.equal(draft.subject,'户型分析');assert.equal(draft.start_mode,'reference');
 assert.match(draft.brief,/借鉴先提问再实拍/);assert.match(draft.brief,/户型/);
});

// Catches making unclassified saved references inherit the current discovery tab's unrelated content purpose.
test('reference planning infers only recorded category metadata and leaves unclassified references open',()=>{
 assert.equal(typeof ui.createReferencePlanDraft,'function');
 const recorded=ui.createReferencePlanDraft({...reference,source_snapshot:{subject:'购房政策'}});
 assert.equal(recorded.content_type,'professional');assert.equal(recorded.subject,'购房政策');
 const unclassified=ui.createReferencePlanDraft(reference);assert.equal(unclassified.content_type,undefined);assert.equal(unclassified.subject,undefined);
});

// Catches replacing a reference's explicit short or long duration with an unrelated default.
test('reference planning starts short and preserves explicit supported targets',()=>{
 for(const target_seconds of [undefined,61,120,601])assert.equal(ui.createReferencePlanDraft({...reference,research_notes:{target_seconds}}).target_seconds,30);
 for(const target_seconds of [30,45,60,300,480,600])assert.equal(ui.createReferencePlanDraft({...reference,research_notes:{target_seconds}}).target_seconds,target_seconds);
});

// Catches source summaries acquiring false observed-video authority once they are handed to the planner.
test('a source-only reference draft labels its structural inspiration as a hypothesis to validate',()=>{
 const draft=ui.createReferencePlanDraft({...reference,note:'',summary:'',source_snapshot:{snippet:'先提问，再比较两种生活动线'}});
 assert.match(draft.brief,/结构假设（待验证）：先提问/);assert.doesNotMatch(draft.brief,/参考观察：|实测|已观看|爆款原因/);
});

// Catches research editing rejecting a valid ten-minute adaptation or replacing an already saved short target.
test('research notes start short with an editable ten-minute limit and preserve saved durations',()=>{
 const render=research_notes=>renderToStaticMarkup(React.createElement(ui.AppContext.Provider,{value:{project:{id:'p-1'},notify(){}}},React.createElement(ui.ReferenceResearchDialog,{reference:{...reference,research_notes},editable:true,onClose(){},onChanged(){},onAdapt(){}})));
 assert.match(render({}),/<input type="number"[^>]*min="15"[^>]*max="600"[^>]*value="30"/);
 for(const target_seconds of [45,480,600])assert.match(render({target_seconds}),new RegExp(`<input type="number"[^>]*value="${target_seconds}"`));
});

// Catches navigating away before a newly found video has an actual persisted reference identity.
test('planning an uncaptured video persists its server source before returning the source draft',async()=>{
 assert.equal(typeof ui.requestCatalogPlanningDraft,'function');
 const native=globalThis.fetch;globalThis.fetch=(p,o)=>native(origin+p,o);requests.length=0;
 try{
  const draft=await ui.requestCatalogPlanningDraft('p-1',catalogItem,'professional','购房政策');
  assert.deepEqual(requests,[{path:'/api/projects/p-1/operations/research/references',method:'POST',body:{job_id:'job-real',platform:'bilibili',content_id:'BVPolicy'}}]);
  assert.equal(draft.reference_id,'r-1');assert.equal(draft.title,catalogItem.title);assert.equal(draft.content_type,'professional');assert.equal(draft.subject,'购房政策');
 }finally{globalThis.fetch=native;}
});

// Catches manufacturing a duplicate reference each time an already saved catalog video is planned.
test('planning an existing personal reference reuses its identity without another capture',async()=>{
 assert.equal(typeof ui.requestCatalogPlanningDraft,'function');requests.length=0;
 const draft=await ui.requestCatalogPlanningDraft('p-1',{...catalogItem,reference_id:'saved-source',catalog_only:false},'professional','');
 assert.equal(draft.reference_id,'saved-source');assert.equal(draft.subject,'购房政策');assert.equal(requests.length,0);
});

function actionMarkup(html,label){return Array.from(html.matchAll(/<button\b[^>]*>[\s\S]*?<\/button>/g),m=>m[0]).find(button=>button.includes(`<span>${label}</span>`))||'';}
// Catches making direct planning a dead end or bypassing edit permissions and in-flight capture controls.
test('video planning is available beside research and respects read-only, source, and capture state',()=>{
 const properties={catalog:preparedCatalog,type:'professional',subject:'购房政策',editable:true,onSelectType(){},onSelectSubject(){},onCapture(){},onOpenReference(){},onPlanItem(){}};
 const ready=renderToStaticMarkup(React.createElement(ui.ContentCatalog,properties));assert.ok(actionMarkup(ready,'用这条策划'));assert.doesNotMatch(actionMarkup(ready,'用这条策划'),/disabled=""/);assert.match(ready,/收藏并研究/);
 for(const changes of [{editable:false},{busyItemKey:'bilibili:BVPolicy'},{catalog:{...preparedCatalog,categories:[{...preparedCatalog.categories[0],items:[{...catalogItem,job_id:undefined,catalog_only:false}]}]}}]){
  const html=renderToStaticMarkup(React.createElement(ui.ContentCatalog,{...properties,...changes}));assert.match(actionMarkup(html,'用这条策划'),/disabled=""/);
 }
});

// Catches forcing users through the entire research dialog before they can plan from a saved reference.
test('reference library and research dialog offer direct planning while preserving their research path',()=>{
 for(const editable of [true,false]){
  const library=renderToStaticMarkup(React.createElement(ui.ReferenceLibrary,{references:[reference],editable,onOpen(){},onPlan(){}}));
  const dialog=renderToStaticMarkup(React.createElement(ui.AppContext.Provider,{value:{project:{id:'p-1'},notify(){}}},React.createElement(ui.ReferenceResearchDialog,{reference,editable,onClose(){},onChanged(){},onAdapt(){},onPlan(){}})));
  for(const html of [library,dialog]){assert.ok(actionMarkup(html,'用这条策划'));assert.equal(/disabled=""/.test(actionMarkup(html,'用这条策划')),!editable);}
  assert.match(library,editable?/研究与拆解/:/查看研究/);assert.match(dialog,/保存研究草稿/);
 }
});
// Catches losing measured zero and leaving planned references without actual next actions.
test('reference library retains measured zero and exposes actual next steps',()=>{const html=renderToStaticMarkup(React.createElement(ui.ReferenceLibrary,{references:[reference,{...reference,id:'r-2',title:'已改编参考',plan_id:'plan-2',content_id:'content-2'}],editable:true,onOpen(){},onOpenPlan(){},onOpenContent(){}}));assert.match(html,/改善型住宅参考/);assert.match(html,/点赞 0/);assert.match(html,/播放 未获取/);assert.match(html,/关注厨房动线/);assert.match(html,/继续编辑脚本/);assert.match(html,/查看内容计划/);});
// Catches claiming unprocessed assets are analyzed or discarding plan associations.
test('reference stage follows persisted evidence and plan associations',()=>{assert.equal(ui.referenceStage(reference),'待补原片');assert.equal(ui.referenceStage({...reference,media_asset_id:'a-1'}),'待分析');assert.equal(ui.referenceStage({...reference,research_notes:{transcript:'人工核对口播'}}),'笔记已保存');assert.equal(ui.referenceStage({...reference,analysis_snapshot:{job_id:'j-1',coverage:{note:'抽帧分析'}}}),'已分析');assert.equal(ui.referenceStage({...reference,plan_id:'p-1'}),'已改编');});
// Catches client-invented metrics replacing server-owned discovery evidence.
test('capturing a result sends only its server job and platform identity',async()=>{const native=globalThis.fetch;globalThis.fetch=(p,o)=>native(origin+p,o);requests.length=0;try{await ui.requestReferenceCapture('p 1',{job_id:'job-real',platform:'douyin',content_id:'789'});assert.deepEqual(requests,[{path:'/api/projects/p%201/operations/research/references',method:'POST',body:{job_id:'job-real',platform:'douyin',content_id:'789'}}]);}finally{globalThis.fetch=native;}});
// Catches blind overwrites from concurrent operators editing persistent research drafts.
test('reference draft save includes revision and independently edited notes',async()=>{const native=globalThis.fetch;globalThis.fetch=(p,o)=>native(origin+p,o);requests.length=0;try{await ui.requestReferenceUpdate('p-1','r-1',2,{research_notes:{transcript:'人工改写',observations:'00:03 镜头',target_seconds:45,topic_id:'layout'},note:'研究重点'});assert.deepEqual(requests[0],{path:'/api/projects/p-1/operations/research/references/r-1',method:'PATCH',body:{expected_revision:2,research_notes:{transcript:'人工改写',observations:'00:03 镜头',target_seconds:45,topic_id:'layout'},note:'研究重点'}});}finally{globalThis.fetch=native;}});
// Catches discarding actual source timings or describing entire intervals from sampled frames.
test('analysis import uses actual timed transcript and sampled observations',()=>{const notes=ui.analysisToNotes({transcript_segments:[{start_seconds:0,end_seconds:2.5,text:'先看动线'}],frames:[{time_seconds:4,observation:'玄关连接客厅'}],coverage:{note:'只抽样 1 帧'}});assert.equal(notes.transcript,'00:00–00:02.5 先看动线');assert.equal(notes.observations,'00:04 玄关连接客厅');});

// Catches making a reusable research reference a dead end after its first project adaptation.
test('a previously adapted reference can save another original adaptation while retaining prior plan access',()=>{
 const el=React.createElement(ui.ReferenceResearchDialog||(()=>null),{reference:{...reference,plan_id:'plan-existing',research_notes:{transcript:'00:00 真实来源笔记'}},editable:true,onClose(){},onChanged(){},onAdapt(){},onOpenPlan(){}});
 const html=renderToStaticMarkup(React.createElement(ui.AppContext.Provider,{value:{project:{id:'p-1'},notify(){}}},el));const action=Array.from(html.matchAll(/<button\b[^>]*>[\s\S]*?<\/button>/g),m=>m[0]).find(b=>b.includes('保存并再写一条改编'));assert.ok(action);assert.doesNotMatch(action,/disabled=""/);assert.match(html,/查看内容计划/);
});
// Catches replaying an old completed analysis forever, while retaining safe retries for an uncertain POST response.
test('analysis retry keeps its key only while outcome is uncertain and renews after a terminal job',()=>{
 const forRevision=ui.nextAnalysisAttempt||((current,revision)=>current||{revision,key:'unchanged'}),settle=ui.settleAnalysisAttempt||((a)=>a);
 const initial=forRevision(null,4);assert.ok(initial.key);assert.equal(forRevision(initial,4).key,initial.key,'uncertain network response must reuse idempotency key');
 for(const status of ['succeeded','failed','cancelled']){const settled=settle(initial,status);assert.equal(settled,null);const retry=forRevision(settled,4);assert.notEqual(retry.key,initial.key,'same-revision explicit reanalysis must create a new job');}
 assert.equal(settle(initial,'running'),initial);assert.notEqual(forRevision(initial,5).key,initial.key);
});
// Catches missing outcome measurements being converted to zero, or unverified/offline works being presented as live success.
test('reference outcomes distinguish observed zero, pending windows, and publication receipt states',()=>{
 const data={reference_id:'r-1',plans:[{id:'plan-1',title:'真实动线计划',content_id:'c-1',stage:'script',template_id:'question'}],publications:[{id:'pub-1',title:'真实发布作品',url:'https://example.com/published',channel:'抖音',platform_state:'live',receipt_status:'unverified',plan_id:'plan-1',content_version_id:'c-1',content_version:2,template_id:'question',metrics:{D7:{views:0,likes:null,inquiries:0,qualified:null},D30:null}},{id:'pub-2',title:'已下线作品',url:'javascript:alert(1)',channel:'抖音',platform_state:'offline',receipt_status:'verified',metrics:{D7:null,D30:null}}],verified_live_count:0,coverage_note:'仅展示实际关联发布与已回填观察'};
 const html=renderToStaticMarkup(React.createElement(ui.ReferenceOutcomes||(()=>null),{data,onAnalyze(){}}));assert.match(html,/改编效果与复盘/);assert.match(html,/真实动线计划/);assert.match(html,/真实发布作品/);assert.match(html,/回执待核实/);assert.match(html,/已下线/);assert.match(html,/播放<\/span><strong>0/);assert.match(html,/点赞<\/span><strong>待观察/);assert.match(html,/咨询<\/span><strong>0/);assert.match(html,/资格客户<\/span><strong>待观察/);assert.match(html,/D30/);assert.doesNotMatch(html,/href="javascript:|爆款率/);
});
// Catches an empty research reference displaying fabricated performance or omitting the existing report route.
test('reference outcomes remain empty with a concrete next step before any publication',()=>{
 const html=renderToStaticMarkup(React.createElement(ui.ReferenceOutcomes||(()=>null),{data:{reference_id:'r-1',plans:[],publications:[],verified_live_count:0,coverage_note:'尚无实际关联发布'},onAnalyze(){}}));assert.match(html,/暂无关联发布记录/);assert.match(html,/前往效果分析/);assert.doesNotMatch(html,/播放<\/span><strong>0/);
});

// Catches a succeeded worker displaying green success when every actual query or source was unavailable.
test('search history derives completion from actual query and source outcomes',()=>{
 const empty={query:'住宅',searched_at:'2026-10-14T00:00:00Z',window_days:30,total:0,items:[],sources:[]};
 const jobs=[
  {id:'failed-branches',kind:'discovery_search',status:'succeeded',created_at:'2026-10-14T00:00:00Z',result:{discovery_search:{...empty,query:'失败分支',query_runs:[{query:'住宅',status:'failed',total:0,error:'检索用量不足'}]}}},
  {id:'login-required',kind:'discovery_search',status:'succeeded',created_at:'2026-10-14T00:00:00Z',result:{discovery_search:{...empty,query:'登录受阻',sources:[{platform:'douyin',adapter:'douyin_public_browser',status:'login_required',reason:'请连接研究账号'}]}}},
  {id:'queried',kind:'discovery_search',status:'succeeded',created_at:'2026-10-14T00:00:00Z',result:{discovery_search:{...empty,query:'真实查询零匹配',sources:[{platform:'bilibili',adapter:'bilibili_public_search',status:'ok',reason:'真实查询无匹配'}]}}}
 ];
 const html=renderToStaticMarkup(React.createElement(ui.ResearchSearchHistory||(()=>null),{jobs,onOpen(){}}));
 const cards=Array.from(html.matchAll(/<article\b[\s\S]*?<\/article>/g),m=>m[0]);assert.equal(cards.length,3);
 for(const card of cards.slice(0,2)){assert.match(card,/badge amber/);assert.doesNotMatch(card,/badge green|已完成/);}
 assert.match(cards[0],/检索用量不足/);assert.match(cards[1],/请连接研究账号/);assert.match(cards[2],/badge green/);assert.match(cards[2],/查看这次结果/);
});
