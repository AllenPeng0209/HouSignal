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

let ui,directory,server,origin;
const requests=[];
before(async()=>{
 directory=await mkdtemp(join(tmpdir(),'housignal-discovery-'));
 const source=resolve('src/DiscoverySearch.tsx');
 const result=await build({stdin:{contents:`export * from ${JSON.stringify(source)};export {AppContext} from ${JSON.stringify(resolve('src/hooks.tsx'))};`,resolveDir:process.cwd(),loader:'tsx'},write:false,bundle:true,format:'esm',platform:'node',jsx:'automatic',external:['react','react-dom','react/jsx-runtime'],loader:{'.css':'empty'},plugins:[{name:'missing-feature',setup(builder){if(!existsSync(source)){builder.onResolve({filter:/DiscoverySearch\.tsx$/},()=>({path:'missing-feature',namespace:'fixture'}));builder.onLoad({filter:/.*/,namespace:'fixture'},()=>({loader:'js',contents:'export const DiscoverySearch=()=>null,DiscoverySearchResults=()=>null;export const requestDiscoverySearch=async()=>null,requestDiscoveryInspect=async()=>null,discoveryOpportunitySeed=()=>null;'}));}}}]});
 const bundled=result.outputFiles[0].text.replaceAll('from "react"',`from ${JSON.stringify(pathToFileURL(resolve('node_modules/react/index.js')).href)}`).replaceAll('from "react/jsx-runtime"',`from ${JSON.stringify(pathToFileURL(resolve('node_modules/react/jsx-runtime.js')).href)}`);
 const target=join(directory,'discovery.mjs');await writeFile(target,bundled);ui=await import(pathToFileURL(target).href);
 server=createServer(async(req,res)=>{const chunks=[];for await(const chunk of req)chunks.push(chunk);requests.push({path:req.url,method:req.method,body:JSON.parse(Buffer.concat(chunks).toString()||'{}')});res.writeHead(200,{'content-type':'application/json'});res.end(JSON.stringify(req.url.endsWith('/inspect')?{item:reference}:{job_id:'search-1'}));});
 await new Promise(resolve=>server.listen(0,'127.0.0.1',resolve));origin=`http://127.0.0.1:${server.address().port}`;
});
after(async()=>{server?.close();if(directory)await rm(directory,{recursive:true,force:true});});
const reference={platform:'bilibili',content_id:'BVfixture',title:'真实大平层户型讲解',url:'https://www.bilibili.com/video/BVfixture',author:'住宅观察',published_at:'2025-11-01T00:00:00Z',metrics:{views:240000,likes:3100,comments:80,saves:null,shares:0},metrics_observed_at:'2026-10-13T03:00:00Z',metrics_source:'bilibili_public_api',match_keywords:['大平层','户型'],qualification:'verified_high_engagement',historical:true,qualification_reason:'点赞及播放量达到门槛',snippet:'厨房与客餐厅的动线'};
const result={query:'住宅 大平层',searched_at:'2026-10-13T04:00:00Z',window_days:365,total:2,sources:[{platform:'douyin',adapter:'platform_search',status:'login_required',reason:'需要登录后核对搜索结果'},{platform:'bilibili',adapter:'bilibili_public_api',status:'ok',reason:'已取得公开作品数据'}],items:[reference,{...reference,content_id:'BVunknown',title:'发布时间未知的住宅参考',url:'javascript:alert(1)',published_at:null,metrics:{views:null,likes:null,comments:null,saves:null,shares:null},qualification:'candidate_unverified',historical:false,qualification_reason:'发布时间及互动数据待核验',date_unknown:true}]};
const context={project:{id:'residential-project',name:'项目内部名称',city:'不应自动发送的城市'},notify(){}};
function markup(element){return renderToStaticMarkup(React.createElement(ui.AppContext.Provider,{value:context},element));}

// Catches a regression to a local text filter or automatic broad hot-list fetch.
test('the discovery form starts with residential keywords and an explicit searchable time range',()=>{
 const html=markup(React.createElement(ui.DiscoverySearch,{editable:true,onSave(){}}));
 assert.match(html,/value="房地产"/);assert.match(html,/搜索相关内容/);assert.match(html,/value="30" selected=""/);assert.match(html,/<input type="checkbox" checked=""\/>抖音/);assert.match(html,/<input type="checkbox"\/>小红书/);assert.match(html,/<input type="checkbox" checked=""\/>B站/);assert.match(html,/用作品链接核对数据/);assert.match(html,/默认近30天/);assert.match(html,/累计互动量不代表近期增速/);assert.match(html,/默认同时检索B站公开内容/);assert.match(html,/<option value="operations" selected=""/);
 assert.doesNotMatch(html,/value="项目内部名称"|不应自动发送的城市/);
});

// Catches missing project scoping, dropped platform/range/sort filters or privacy leakage.
test('a residential search posts the selected filters to the current project endpoint',async()=>{
 const nativeFetch=globalThis.fetch;globalThis.fetch=(path,options)=>nativeFetch(origin+path,options);requests.length=0;
 try{const submitted=await ui.requestDiscoverySearch('residential-project',{query:' 上海 大平层 ',platforms:['douyin','bilibili'],days:30,min_likes:1000,min_views:100000,sort:'latest'});assert.deepEqual(submitted,{job_id:'search-1'});assert.deepEqual(requests,[{path:'/api/projects/residential-project/operations/discovery/search',method:'POST',body:{query:'上海 大平层',platforms:['douyin','bilibili'],days:30,min_likes:1000,min_views:100000,sort:'latest'}}]);}finally{globalThis.fetch=nativeFetch;}
});

// Catches the use of search time as publication time, null metrics as 0 or unsupported source success.
test('results keep publication time, data collection time, missing metrics and blocked sources distinct',()=>{
 const html=renderToStaticMarkup(React.createElement(ui.DiscoverySearchResults,{result,editable:true,onSave(){},verifiedOnly:false}));
 assert.match(html,/真实大平层户型讲解/);assert.match(html,/2025\/11\/01 08:00/);assert.match(html,/2026\/10\/13 11:00/);assert.match(html,/2026\/10\/13 12:00/);assert.match(html,/240,000/);assert.match(html,/3,100/);assert.match(html,/未获取/);assert.match(html,/需登录/);assert.match(html,/需要登录后核对搜索结果/);assert.match(html,/发布时间未知的住宅参考/);assert.match(html,/发布时间未获取/);assert.match(html,/待核验/);assert.match(html,/历史参考/);assert.doesNotMatch(html,/href="javascript:/);assert.doesNotMatch(html,/当前爆款|模型评分/);
});

// Catches web-index candidates being presented as a successful native platform search.
test('a web-index fallback is named as a public web index rather than platform-native data',()=>{
 const fallback={...result,sources:[{platform:'xiaohongshu',adapter:'web_index',status:'partial',reason:'找到相关作品，原生搜索尚未接通'}]};
 const html=renderToStaticMarkup(React.createElement(ui.DiscoverySearchResults,{result:fallback,editable:true,onSave(){}}));assert.match(html,/公开网页索引/);assert.match(html,/部分结果/);assert.match(html,/原生搜索尚未接通/);
});

// Catches B站 public metrics enrichment erasing the web-index provenance of search discovery.
test('B站 data enrichment preserves both the search index and public work-data provenance',()=>{
 const enriched={...result,sources:[{platform:'bilibili',adapter:'web_index+bilibili_public_api',status:'ok',reason:'已取得相关作品的数据'}]};
 const html=renderToStaticMarkup(React.createElement(ui.DiscoverySearchResults,{result:enriched,editable:true,onSave(){}}));assert.match(html,/公开网页索引 \+ 作品数据核对/);
});

// Catches first-party search data being mislabeled as a web-index result.
test('B站 public search and work verification are identified as the actual adapters',()=>{
 for(const [adapter,label] of [['bilibili_public_search','平台公开搜索'],['bilibili_public_search+bilibili_public_api','平台公开搜索 + 作品数据核对']]){
  const direct={...result,sources:[{platform:'bilibili',adapter,status:'ok',reason:'本次返回作品来源'}]};
  const html=renderToStaticMarkup(React.createElement(ui.DiscoverySearchResults,{result:direct,editable:true,onSave(){}}));assert.ok(html.includes(`<small>${label}</small>`));
 }
});
// Catches authenticated Douyin research results being mislabeled as generic index data.
test('Douyin research adapter identifies connection and human verification failures accurately',()=>{
 for(const [status,label] of [['not_connected','尚未连接'],['busy','账号正在使用'],['challenge','平台校验未确认']]){
  const direct={...result,sources:[{platform:'douyin',adapter:'douyin_public_browser',status,reason:'本项目研究会话状态'}]};
  const html=renderToStaticMarkup(React.createElement(ui.DiscoverySearchResults,{result:direct,editable:true,onSave(){}}));assert.match(html,/抖音研究账号公开搜索/);assert.ok(html.includes(label));
 }
});
// Catches the research account UI becoming unreachable from residential discovery.
test('residential search uses the current project operational session by default',()=>{
 const html=markup(React.createElement(ui.DiscoverySearch,{editable:true,onSave(){}}));assert.match(html,/本项目运营账号/);assert.match(html,/核对已有登录/);assert.doesNotMatch(html,/抖音研究账号登录二维码|请用抖音 App 扫码/);
});

// Catches the verified-only filter still showing candidates, or dropping hidden evidence from the retained result.
test('verified filtering strictly hides candidates and reports hidden evidence that can be restored',()=>{
 const mixed={...result,items:[...result.items,{...reference,content_id:'unverified',title:'未核实互动的住宅作品',qualification:'candidate_unverified',metrics:{views:null,likes:null,comments:null,saves:null,shares:null}}]};
 const html=renderToStaticMarkup(React.createElement(ui.DiscoverySearchResults,{result:mixed,editable:true,onSave(){},verifiedOnly:true}));assert.match(html,/真实大平层户型讲解/);assert.doesNotMatch(html,/未核实互动的住宅作品|发布时间未知的住宅参考/);assert.match(html,/另有 2 条/);assert.match(html,/关闭筛选查看/);
 const restored=renderToStaticMarkup(React.createElement(ui.DiscoverySearchResults,{result:mixed,editable:true,onSave(){},verifiedOnly:false}));assert.match(restored,/未核实互动的住宅作品/);assert.match(restored,/发布时间未知的住宅参考/);
});

// Catches replacing explicit network search with local-only filtering when users change search parameters.
test('the search form explains when to re-query and offers a distinct local verified-result filter',()=>{
 const html=markup(React.createElement(ui.DiscoverySearch,{editable:true,onSave(){}}));assert.match(html,/只看互动已核对/);assert.match(html,/不重新联网/);assert.match(html,/点击「搜索相关内容」重新查询/);
});

// Catches saving an invented/source-free opportunity instead of the real piece and observations.
test('saving a result preserves its actual URL, platform and metric evidence',()=>{
 const seed=ui.discoveryOpportunitySeed(reference);assert.equal(seed?.title,'真实大平层户型讲解');assert.equal(seed?.source_url,'https://www.bilibili.com/video/BVfixture');assert.equal(seed?.platform,'B站');assert.match(seed?.summary||'',/播放 240,000/);assert.match(seed?.summary||'',/点赞 3,100/);assert.match(seed?.summary||'',/2025\/11\/01 08:00/);assert.match(seed?.summary||'',/2026\/10\/13 11:00/);assert.match(seed?.summary||'',/收藏 未获取/);
});

// Catches a local URL-only placeholder that never reads the platform's actual work data.
test('checking a known residential work posts its link and returns the observed item',async()=>{
 const nativeFetch=globalThis.fetch;globalThis.fetch=(path,options)=>nativeFetch(origin+path,options);requests.length=0;
 try{const inspected=await ui.requestDiscoveryInspect('residential-project','https://www.bilibili.com/video/BVfixture');assert.deepEqual(inspected,{item:reference});assert.deepEqual(requests,[{path:'/api/projects/residential-project/operations/discovery/inspect',method:'POST',body:{url:'https://www.bilibili.com/video/BVfixture'}}]);}finally{globalThis.fetch=nativeFetch;}
});

// Catches removing the adaptation action from actual result cards or permitting it for read-only roles.
test('result cards expose breakdown beside the existing save action with role controls',()=>{
 for(const editable of [true,false]){
  const html=renderToStaticMarkup(React.createElement(ui.DiscoverySearchResults,{result:{...result,items:[reference]},editable,onSave(){},onBreakdown(){}}));
  const actions=Array.from(html.matchAll(/<button\b[^>]*>[\s\S]*?<\/button>/g),m=>m[0]);
  const breakdown=actions.find(value=>value.includes('<span>拆解并写脚本</span>'))||'';
  assert.ok(breakdown);assert.match(html,/保存为发现/);assert.equal(breakdown.includes('disabled=""'),!editable);
 }
});
// Catches narrowing search to housing types without actionable property-content presets.
test('real estate presets offer audience problems and filming formats without launching requests',()=>{
 const html=markup(React.createElement(ui.DiscoverySearch,{editable:true,onSave(){}}));
 for(const label of ['房地产','买房避坑','户型动线','实拍看房'])assert.match(html,new RegExp(`<button[^>]*type="button"[^>]*>${label}</button>`));
 assert.match(html,/选择方向后点击搜索/);
});

// Catches invented daily growth and dropping the actual sampling interval from the result card.
test('observed growth shows measured likes and rate with both sample times',()=>{
 const growth={status:'observed',previous_observed_at:'2026-10-13T01:00:00Z',current_observed_at:'2026-10-13T03:00:00Z',elapsed_hours:2,metric_deltas:{views:1000,likes:240,comments:2,saves:null,shares:0},likes_per_hour:120,reasons:[]};
 const item={...reference,growth};
 const html=renderToStaticMarkup(React.createElement(ui.DiscoverySearchResults,{result:{...result,items:[item]},editable:true,onSave(){}}));
 assert.match(html,/已观测增量/);assert.match(html,/点赞增量/);assert.match(html,/\+240/);assert.match(html,/120.*赞\/小时/);assert.match(html,/2026\/10\/13 09:00/);assert.match(html,/2026\/10\/13 11:00/);assert.match(html,/间隔 2 小时/);assert.doesNotMatch(html,/24h|24 小时|今日增长/);
 const seed=ui.discoveryOpportunitySeed(item);assert.match(seed.summary,/已观测增量/);assert.match(seed.summary,/\+240/);assert.match(seed.summary,/09:00/);assert.match(seed.summary,/120.*赞\/小时/);
});
// Catches missing or incomparable baselines being rendered as a measured zero.
test('missing and incomparable baselines remain pending instead of zero growth',()=>{
 for(const [status,label] of [['no_baseline','待复测'],['incomparable','暂不可比']]){
  const growth={status,previous_observed_at:null,current_observed_at:reference.metrics_observed_at,elapsed_hours:null,metric_deltas:{views:null,likes:null,comments:null,saves:null,shares:null},likes_per_hour:null,reasons:['还没有可比较的历史采样']};
  const html=renderToStaticMarkup(React.createElement(ui.DiscoverySearchResults,{result:{...result,items:[{...reference,growth}]},editable:true,onSave(){}}));
  assert.ok(html.includes(label));assert.match(html,/还没有可比较的历史采样/);assert.doesNotMatch(html,/\+0|0 赞\/小时/);
 }
});
// Catches an old saved search without growth data being presented as measured growth.
test('historical results without growth evidence show a pending remeasurement',()=>{
 const html=renderToStaticMarkup(React.createElement(ui.DiscoverySearchResults,{result:{...result,items:[reference]},editable:true,onSave(){}}));
 assert.match(html,/待复测/);assert.doesNotMatch(html,/\+0|0 赞\/小时/);
});
// Catches dropping the sampled-growth sort at the HTTP boundary.
test('search offers the observed growth sort and sends its selected value',async()=>{
 const html=markup(React.createElement(ui.DiscoverySearch,{editable:true,onSave(){}}));assert.match(html,/<option value="growth">已观测增量<\/option>/);
 const nativeFetch=globalThis.fetch;globalThis.fetch=(path,options)=>nativeFetch(origin+path,options);requests.length=0;
 try{await ui.requestDiscoverySearch('residential-project',{query:'改善住宅',platforms:['douyin'],days:30,min_likes:1000,min_views:100000,sort:'growth'});assert.equal(requests[0].body.sort,'growth');}finally{globalThis.fetch=nativeFetch;}
});

// Catches treating a completed worker as proof that unavailable platform searches found no works.
test('unavailable sources show visible reasons and recovery actions instead of a no-match empty state',()=>{
 const unavailable={...result,total:0,items:[],sources:[
  {platform:'douyin',adapter:'douyin_public_browser',status:'login_required',reason:'研究登录已失效'},
  {platform:'bilibili',adapter:'bilibili_public_search',status:'blocked',reason:'公开搜索被限流'},
  {platform:'xiaohongshu',adapter:'web_index',status:'not_configured',reason:'联网检索尚未配置'},
  {platform:'douyin',adapter:'douyin_public_browser',status:'challenge',reason:'官方页面要求安全验证'}]};
 const html=renderToStaticMarkup(React.createElement(ui.DiscoverySearchResults,{result:unavailable,editable:true,onSave(){}}));
 assert.match(html,/role="alert"/);assert.match(html,/discovery-source-details" open=""/);
 for(const reason of ['研究登录已失效','公开搜索被限流','联网检索尚未配置','官方页面要求安全验证'])assert.ok(html.includes(reason));
 assert.match(html,/搜索账号与连接状态/);assert.match(html,/管理员/);assert.match(html,/未确认可见验证/);assert.match(html,/稍后/);
 assert.doesNotMatch(html,/所选时间范围内暂无可核对作品|扩大发布时间范围/);
});

// Catches silently hiding the per-query exceptions returned by a fan-out search.
test('failed query branches expose their errors even when the job itself succeeded',()=>{
 const failed={...result,total:0,items:[],sources:[],queries:['房地产','豪宅'],query_runs:[
  {query:'房地产',status:'failed',total:0,error:'查询达到模型用量限制'},
  {query:'豪宅',status:'failed',total:0,error:'真实检索未返回有效来源'}]};
 const html=renderToStaticMarkup(React.createElement(ui.DiscoverySearchResults,{result:failed,editable:true,onSave(){}}));
 assert.match(html,/role="alert"/);assert.match(html,/查询达到模型用量限制/);assert.match(html,/真实检索未返回有效来源/);
 assert.match(html,/房地产/);assert.match(html,/豪宅/);assert.doesNotMatch(html,/所选时间范围内暂无可核对作品/);
});

// Catches discarding useful works or erasing a failed direction when another direction succeeds.
test('partial query failures retain usable works while making missing coverage visible',()=>{
 const mixed={...result,total:1,items:[reference],sources:[{platform:'bilibili',adapter:'bilibili_public_search',status:'ok',reason:'已查询'}],query_runs:[
  {query:'住宅 大平层',status:'succeeded',total:1,error:null},
  {query:'豪宅',status:'failed',total:0,error:'豪宅方向检索超时'}]};
 const html=renderToStaticMarkup(React.createElement(ui.DiscoverySearchResults,{result:mixed,editable:true,onSave(){},onCurate(){}}));
 assert.match(html,/真实大平层户型讲解/);assert.match(html,/保存到参考库/);assert.match(html,/豪宅方向检索超时/);assert.match(html,/部分/);
});

// Catches ambiguous partial/empty sources being promoted to successful zero-result queries.
test('an empty partial source remains inconclusive while an explicit successful source can show no matches',()=>{
 const partial={...result,total:0,items:[],sources:[{platform:'bilibili',adapter:'bilibili_public_search',status:'partial',reason:'平台公开搜索暂不可用'}]};
 const uncertain=renderToStaticMarkup(React.createElement(ui.DiscoverySearchResults,{result:partial,editable:true,onSave(){}}));
 assert.match(uncertain,/role="alert"/);assert.doesNotMatch(uncertain,/所选时间范围内暂无可核对作品/);
 const queried=renderToStaticMarkup(React.createElement(ui.DiscoverySearchResults,{result:{...partial,sources:[{...partial.sources[0],status:'ok',reason:'已成功查询，符合条件的作品为0'}]},editable:true,onSave(){}}));
 assert.match(queried,/所选时间范围内暂无可核对作品/);assert.doesNotMatch(queried,/role="alert"/);
});

// Catches no matches from one available platform being presented as no matches across blocked platforms.
test('zero matches from partial available coverage are limited to the sources that actually ran',()=>{
 const html=renderToStaticMarkup(React.createElement(ui.DiscoverySearchResults,{result:{...result,total:0,items:[]},editable:true,onSave(){}}));
 assert.match(html,/可用来源中暂无符合条件的作品/);assert.doesNotMatch(html,/所选时间范围内暂无可核对作品|保留已取得的作品/);
});

// Catches valid zero growth being confused with absent sampling evidence.
test('a measured zero remains visible while unavailable growth metrics stay unknown',()=>{
 const base={status:'observed',previous_observed_at:'2026-10-13T01:00:00Z',current_observed_at:'2026-10-13T03:00:00Z',elapsed_hours:2,metric_deltas:{views:1000,likes:0,comments:null,saves:null,shares:null},likes_per_hour:0,reasons:[]};
 const zero=renderToStaticMarkup(React.createElement(ui.DiscoverySearchResults,{result:{...result,items:[{...reference,growth:base}]},editable:true,onSave(){}}));
 assert.match(zero,/点赞增量 \+0/);assert.match(zero,/0 赞\/小时/);
 const partial={...base,metric_deltas:{...base.metric_deltas,likes:null},likes_per_hour:null,reasons:['本次点赞数回退，点赞增量不可比']};
 const unknown=renderToStaticMarkup(React.createElement(ui.DiscoverySearchResults,{result:{...result,items:[{...reference,growth:partial}]},editable:true,onSave(){}}));
 assert.match(unknown,/点赞增量未获取/);assert.match(unknown,/点赞增速未获取/);assert.match(unknown,/本次点赞数回退，点赞增量不可比/);assert.doesNotMatch(unknown,/点赞增量 \+0|0 赞\/小时/);
});
// Catches bounded query expansion being presented as exhaustive geographical/topic coverage.
test('limited search coverage exposes actual queries and the unsearched scope warning',()=>{
 const html=renderToStaticMarkup(React.createElement(ui.DiscoverySearchResults,{result:{...result,items:[],queries:['上海 豪宅','杭州 豪宅'],total_query_count:30,queries_truncated:true,coverage_note:'本次执行 2 / 30 组查询，未覆盖全部城市与方向'},editable:true,onSave(){}}));assert.match(html,/上海 豪宅/);assert.match(html,/杭州 豪宅/);assert.match(html,/未覆盖全部城市与方向/);
});

// Catches access-denied or rate-limited responses being falsely presented as a user-verification task.
test('HTTP restrictions report actual evidence without telling the user to scan or solve a challenge',()=>{
 for(const [status,http_status,restriction_reason,label] of [['blocked',403,'http_denied','公开访问受阻'],['rate_limited',429,'rate_limited','请求受限']]){
  const source={platform:'douyin',adapter:'douyin_operating_public_browser',status,reason:'本次搜索未取得作品',http_status,restriction_reason,challenge_visible:false};
  const restricted={...result,total:0,items:[],sources:[source]};
  const html=renderToStaticMarkup(React.createElement(ui.DiscoverySearchResults,{result:restricted,editable:true,onSave(){}}));
  assert.match(html,/本项目运营账号公开搜索/);assert.ok(html.includes(label));assert.ok(html.includes(`HTTP ${http_status}`));
  assert.match(html,/可见验证：未确认/);assert.match(html,/role="alert"/);assert.equal(ui.discoverySearchState(restricted).incomplete,true);
  assert.doesNotMatch(html,/完成安全验证|扫码|滑块|需人工验证|class="badge green"|http_denied|rate_limited/);
 }
});
// Catches historic or service-only challenge markers being promoted to a visible verification window.
test('challenge recovery asks for human verification only with explicit visible evidence',()=>{
 for(const challenge_visible of [undefined,false,true]){
  const source={platform:'douyin',adapter:'douyin_operating_public_browser',status:'challenge',reason:'平台校验尚待确认',...(challenge_visible===undefined?{}:{challenge_visible})};
  const html=renderToStaticMarkup(React.createElement(ui.DiscoverySearchResults,{result:{...result,total:0,items:[],sources:[source]},editable:true,onSave(){}}));
  if(challenge_visible===true){assert.match(html,/可见验证：已确认/);assert.match(html,/需人工验证/);assert.match(html,/由本人.*完成.*可见验证/);}
  else{assert.match(html,/平台校验未确认/);assert.match(html,/未确认可见验证/);assert.doesNotMatch(html,/由本人.*完成|完成安全验证|扫码|滑块|需人工验证/);}
  assert.doesNotMatch(html,/HTTP (?:null|undefined|0)/);
 }
});
// Catches invented transport evidence on saved results that predate the diagnostics fields.
test('old successful results do not invent an HTTP status or visible challenge evidence',()=>{
 const html=renderToStaticMarkup(React.createElement(ui.DiscoverySearchResults,{result:{...result,sources:[{platform:'bilibili',adapter:'bilibili_public_search',status:'ok',reason:'已查询'}]},editable:true,onSave(){}}));
 assert.doesNotMatch(html,/HTTP|可见验证：/);
});
