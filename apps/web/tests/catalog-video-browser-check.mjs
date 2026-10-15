/** Exercises the actual React app. HTTP fixtures isolate covers and the third-party iframe;
 * the receipt proves UI interaction and URL selection, never official video availability. */
import assert from 'node:assert/strict';
import {createRequire} from 'node:module';
import {mkdir,writeFile} from 'node:fs/promises';
import {resolve} from 'node:path';

const require=createRequire(import.meta.url);
const {chromium}=require(process.env.HS_PLAYWRIGHT_MODULE||'/Users/pengyanlun/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright');
const appUrl=process.env.HS_CATALOG_VIDEO_QA_URL||'http://127.0.0.1:8017/';
const origin=new URL(appUrl).origin;
const phase=process.env.HS_CATALOG_VIDEO_QA_PHASE||'green';
const output=resolve(process.env.HS_CATALOG_VIDEO_QA_OUTPUT||`../../reports/catalog-video/play-start/browser/${phase}`);
const browser=await chromium.launch({headless:true,channel:process.env.HS_BROWSER_CHANNEL||'chrome'});
const page=await browser.newPage({viewport:{width:1440,height:1100}});
page.setDefaultTimeout(12000);
const at='2026-10-14T09:00:00Z',pid='catalog-video-fixture',base=`/api/projects/${pid}`,research=`${base}/operations/research`;
const project={id:pid,name:'视频封面验收项目',city:'杭州',property_type:'住宅',channel:'抖音',owner_name:'合成负责人',audience:'改善家庭',sales_owner_name:'合成负责人',marketing_confirmed:true,marketing_evidence:'isolated HTTP fixture',is_test:true,completeness:{percent:100,completed:7,total:7,items:[]}};
const session={user:{id:'qa',name:'合成验收',email:'qa@example.invalid'},organization:{id:'qa',name:'合成测试组织'},role:'owner'};
function item(platform,content_id,title,extra={}) {
 return {platform,content_id,title,url:platform==='bilibili'?`https://www.bilibili.com/video/${content_id}/`:`https://www.douyin.com/video/${content_id}`,author:'合成验收账号',published_at:at,collected_at:at,metrics:{views:null,likes:null,comments:null,saves:null,shares:null},metrics_observed_at:at,metrics_source:'isolated HTTP fixture',match_keywords:['户型分析'],qualification:'candidate_unverified',historical:false,qualification_reason:'隔离浏览器样本；未主张外部真实作品',subject:'户型分析',job_id:`job-${content_id}`,...extra};
}
const normal=item('bilibili','BV1Ab411c7XY','【验收样本】户型动线与采光',{cover_url:'https://i0.hdslb.com/bfs/archive/normal-video-cover.jpg',player_url:'https://attacker.invalid/injected-player'});
const broken=item('bilibili','BV1Q541167Qg','【验收样本】封面加载失败仍可观看',{cover_url:'https://i0.hdslb.com/bfs/archive/broken-video-cover.jpg'});
const external=item('douyin','7350000000000000001','【验收样本】平台原作打开播放',{cover_url:null,player_url:'https://attacker.invalid/injected-player'});
const subjects=[{label:'户型点评',query:'户型分析'}];
const categories=[
 {id:'professional',label:'专业内容',purpose:'帮助用户理解房产，建立专业认知。',subjects,items:[normal,broken,external],updated_at:at,collection_state:'ready'},
 ...[['traffic','流量内容'],['trust','信任内容'],['acquisition','获客内容'],['conversion','转化内容']].map(([id,label])=>({id,label,purpose:'隔离验收方向',subjects:[],items:[],updated_at:at,collection_state:'ready'})),
];
const requests=[],mutations=[],forbidden=[],unhandled=[],errors=[],externalRequests=[],coverRequests=[],checks=[];
page.on('pageerror',error=>errors.push(error.message));
const fixtureCover='<svg xmlns="http://www.w3.org/2000/svg" width="640" height="360" viewBox="0 0 640 360"><rect width="640" height="360" fill="#eef2f8"/><path d="M0 286L110 198l105 52 151-175L640 240v120H0z" fill="#92a9c5"/><rect x="86" y="89" width="468" height="90" rx="14" fill="#ffffff"/><text x="320" y="129" text-anchor="middle" font-size="24" fill="#33475f">Synthetic HTTP cover fixture</text><text x="320" y="158" text-anchor="middle" font-size="16" fill="#75849b">Not a real platform thumbnail</text></svg>';
await page.route('**/*',async route=>{
 const request=route.request(),url=new URL(request.url()),path=url.pathname,method=request.method();
 if(url.origin==='https://player.bilibili.com'){
  externalRequests.push({origin:url.origin,path,query:url.search});
  return route.fulfill({status:200,contentType:'text/html',body:'<!doctype html><meta charset="utf-8"><style>body{margin:0;background:#172437;color:white;display:grid;place-items:center;height:100vh;font:16px sans-serif}</style><p>Isolated official-player HTTP fixture — UI verification only</p>'});
 }
 if(url.origin==='https://i0.hdslb.com'){
  coverRequests.push({origin:url.origin,path,query:url.search});
  if(path==='/bfs/archive/broken-video-cover.jpg')return route.fulfill({status:404,contentType:'text/plain',body:'Fixture cover deliberately unavailable.'});
  if(path==='/bfs/archive/normal-video-cover.jpg')return route.fulfill({status:200,contentType:'image/svg+xml',body:fixtureCover});
  unhandled.push(`CDN cover identity: ${method} ${path}`);return route.abort();
 }
 if(url.origin!==origin){externalRequests.push({origin:url.origin,path,blocked:true});return route.abort();}
 if(!path.startsWith('/api/'))return route.continue();
 let body;try{body=request.postDataJSON();}catch{body=null;}
 requests.push({method,path,query:url.search,body});
 if(method!=='GET')mutations.push({method,path,body});
 const respond=(value,status=200)=>route.fulfill({status,contentType:'application/json',body:JSON.stringify(value)});
 if(/\/saved-searches(?:\/|$)|\/research\/runs(?:\/|$)|\/operations\/(?:discovery|platform-accounts|platform-status)(?:\/|$)/.test(path)){
  forbidden.push({method,path});return respond({detail:'Catalogue browsing must not search or connect to a platform.'},409);
 }
 if(path==='/api/auth/me')return respond(session);
 if(path==='/api/projects')return respond([project]);
 if(path==='/api/model/status')return respond({configured:true,provider:'fixture',model:'fixture'});
 if(path===`${base}/overview`)return respond({project,metrics:{published:0,inquiries:0,qualified:0,appointments:0,visits:0,views:null,unknown_rate:null},pending_inquiries:[],content_versions:[],publications:[],proposals:[]});
 if(path===`${base}/reports`)return respond({window:'D7',metrics:{views:null,likes:null,comments:null,saves:null,inquiries:null,qualified:null,window_complete:false,observed_publications:0,total_publications:0},publications:[],proposals:[]});
 if(path===`${base}/topics`)return respond(['tour','layout','light','space','kitchen'].map(id=>({id,title:id,eligible:true,missing:[],angle:'fixture',fact_refs:[]})));
 if(path===`${research}/catalog`)return respond({categories,updated_at:at,total:3});
 if([`${base}/assets`,`${base}/contents`,`${base}/publications`,`${base}/operations/accounts`,`${base}/operations/plans`,`${base}/operations/opportunities`,`${base}/conversations`,`${base}/jobs`,`${research}/references`].includes(path))return respond([]);
 unhandled.push(`${method} ${path}`);return respond({detail:'Unhandled isolated browser fixture'},404);
});
const card=source=>page.locator(`article[data-content-key="${source.platform}:${source.content_id}"]`);
const play=source=>card(source).getByRole('button',{name:`播放：${source.title}`,exact:true});
const openOriginal=source=>card(source).getByRole('button',{name:`打开原作：${source.title}`,exact:true});
const dialog=()=>page.getByRole('dialog');
async function settled(){await page.evaluate(()=>new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve))));}
async function fits(label){assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=window.innerWidth),`${label}: no horizontal page overflow`);}
async function screenshot(name,fullPage=page.viewportSize().width>500){await settled();await page.screenshot({path:resolve(output,name),fullPage,animations:'disabled'});}
async function closePlayer(){await dialog().getByRole('button',{name:'关闭弹窗',exact:true}).click();await dialog().waitFor({state:'hidden'});assert.equal(await page.locator('iframe').count(),0,'closing player unmounts iframe and stops its session');}
function noSearch(){assert.deepEqual(forbidden,[],'prepared catalogue must never run online search/connection calls');assert.deepEqual(mutations,[],'browsing/playing must never write to persistent data');}
async function assertOfficialPlayer(iframe,expected){
 const url=new URL(await iframe.getAttribute('src'));assert.equal(url.origin,'https://player.bilibili.com','only the official player origin is allowed');assert.equal(url.pathname,'/player.html');assert.equal(url.searchParams.get('bvid'),expected);assert.equal(url.searchParams.get('p'),'1','player opens the first video page');assert.equal(url.searchParams.get('poster'),'1');assert.equal(url.searchParams.get('autoplay'),'1','clicking the play cover asks the official player to start playback');assert.equal(url.searchParams.get('muted'),'0','explicit play keeps sound enabled');
 const permissions=(await iframe.getAttribute('allow')||'').split(';').map(value=>value.trim());
 assert.ok(permissions.some(value=>/^autoplay(?:\s|$)/.test(value)),'the click-mounted iframe grants its official player autoplay permission');
}
try{
 await mkdir(output,{recursive:true});
 await page.goto(appUrl,{waitUntil:'domcontentloaded',timeout:30000});
 await page.getByRole('navigation',{name:'主导航',exact:true}).getByRole('button',{name:'内容工作台',exact:true}).click();
 await card(normal).waitFor();await settled();
 assert.equal(await play(normal).count(),1,'a prepared video card exposes its cover as a named play action');
 await page.waitForFunction(()=>{const card=document.querySelector('article[data-content-key="bilibili:BV1Ab411c7XY"]');return Array.from(card?.querySelectorAll('img')||[]).some(image=>image.complete&&image.naturalWidth>0);});
 const image=card(normal).locator('img');assert.equal(await image.count(),1,'normal card displays one fetched cover');
 assert.equal(await image.getAttribute('src'),normal.cover_url,'card displays the prepared official thumbnail URL');
 assert.equal(await image.evaluate(element=>element.naturalWidth),640,'visible thumbnail bytes have loaded');
 assert.equal(await page.locator('iframe,video').count(),0,'catalogue cards never eagerly mount players');
 assert.equal(externalRequests.length,0,'opening the catalogue loads no remote player or untrusted origin');
 noSearch();checks.push('prepared cards show successfully loaded official covers; opening uses no remote player, online search, or writes');

 await play(broken).waitFor();
 await page.waitForFunction(()=>{const card=document.querySelector('article[data-content-key="bilibili:BV1Q541167Qg"]');return card&&card.querySelectorAll('img').length===0;});
 assert.equal(await card(broken).locator('img').count(),0,'failed cover is replaced rather than leaving a broken image');
 assert.ok(await play(broken).isVisible(),'cover failure keeps the play action available');
 assert.equal(await card(broken).getByRole('heading',{name:broken.title,exact:true}).count(),1,'cover failure preserves the title');
 assert.equal(await card(broken).getByRole('link').getAttribute('href'),broken.url,'cover failure keeps the source link');
 checks.push('a 404 cover falls back without a broken image, lost title, source link, or play action');

 await fits('1440px catalogue');await page.evaluate(()=>window.scrollTo(0,0));await screenshot('catalog-video-desktop.png');
 await play(normal).click();await dialog().waitFor();
 assert.equal(await dialog().getAttribute('aria-label'),normal.title,'player dialog identifies the selected work');
 const iframe=dialog().locator('iframe');await iframe.waitFor();assert.equal(await iframe.count(),1);
 await assertOfficialPlayer(iframe,'BV1Ab411c7XY');
 assert.ok((await iframe.getAttribute('title'))?.trim(),'embedded player has an accessible title');
 assert.equal(await dialog().getByRole('link').getAttribute('href'),normal.url,'official source remains accessible when embedding is restricted');
 assert.ok(!externalRequests.some(value=>value.origin==='https://attacker.invalid'),'untrusted player_url can never become an iframe source');
 await fits('1440px player');await screenshot('catalog-video-player-desktop.png',false);await closePlayer();
 checks.push('explicit play click mounts one official iframe for the exact BVID with autoplay=1, muted=0, and autoplay permission; unsafe supplied player URL is ignored; source fallback exists; close removes player');

 await play(broken).click();await dialog().waitFor();await assertOfficialPlayer(dialog().locator('iframe'),'BV1Q541167Qg');await closePlayer();
 await openOriginal(external).click();await dialog().waitFor();
 assert.equal(await dialog().getAttribute('aria-label'),external.title);assert.equal(await dialog().locator('iframe,video').count(),0,'unsupported platform never embeds a guessed player');
 const externalLink=dialog().getByRole('link');assert.equal(await externalLink.getAttribute('href'),external.url);assert.equal(await externalLink.getAttribute('target'),'_blank','unsupported platform can be opened at its original playback page');
 await screenshot('catalog-video-external-desktop.png',false);await closePlayer();
 checks.push('a broken thumbnail still opens its BVID player; unsupported platform opens a source-link fallback without arbitrary iframe');

 await page.setViewportSize({width:390,height:844});await settled();await fits('390px catalogue');
 await page.evaluate(()=>window.scrollTo(0,0));await screenshot('catalog-video-mobile.png');
 await page.locator('.content-catalog').screenshot({path:resolve(output,'catalog-video-mobile-content.png'),animations:'disabled'});
 await play(normal).click();await dialog().waitFor();await assertOfficialPlayer(dialog().locator('iframe'),'BV1Ab411c7XY');
 assert.ok(await dialog().evaluate(element=>element.scrollWidth<=element.clientWidth),'390px player has no inner horizontal overflow');await fits('390px player');await screenshot('catalog-video-player-mobile.png',false);
 await page.keyboard.press('Escape');await dialog().waitFor({state:'hidden'});assert.equal(await page.locator('iframe').count(),0,'Escape closes and removes the player');
 await openOriginal(external).click();await dialog().waitFor();assert.equal(await dialog().locator('iframe,video').count(),0);await fits('390px source fallback');await screenshot('catalog-video-external-mobile.png',false);await closePlayer();
 checks.push('catalogue, video dialog and unsupported-platform fallback fit desktop/mobile; Escape unmounts playback iframe');
 noSearch();assert.deepEqual(errors,[],'React/browser raises no runtime errors');assert.deepEqual(unhandled,[],'API and cover requests use explicit fixtures');
 assert.ok(coverRequests.some(value=>value.path==='/bfs/archive/normal-video-cover.jpg'),'normal cover was requested');
 assert.ok(coverRequests.some(value=>value.path==='/bfs/archive/broken-video-cover.jpg'),'404 cover was requested');
 const receipt={status:'passed',phase,mode:'actual React UI; isolated HTTP covers and official-player iframe; proves no external real-video playback',appUrl,checks,errors,unhandled,forbidden,requests,mutations,coverRequests,externalRequests};
 await writeFile(resolve(output,'receipt.json'),JSON.stringify(receipt,null,2));
 process.stdout.write(`Catalogue video browser acceptance passed (${checks.length} groups); receipt: ${resolve(output,'receipt.json')}\n`);
}catch(error){
 await mkdir(output,{recursive:true});await screenshot('failure.png').catch(()=>{});
 await writeFile(resolve(output,'receipt.json'),JSON.stringify({status:'failed',phase,error:error.message,mode:'actual React UI; isolated HTTP fixtures; no external real-video playback',appUrl,checks,errors,unhandled,forbidden,requests,mutations,coverRequests,externalRequests},null,2));
 throw error;
}finally{await browser.close();}
