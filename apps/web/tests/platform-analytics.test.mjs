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
let ui,directory,server,origin,requests=[];
before(async()=>{
 directory=await mkdtemp(join(tmpdir(),'housignal-platform-analytics-'));
 const source=resolve('src/PlatformAnalytics.tsx');
 const result=await build({entryPoints:[source],write:false,bundle:true,format:'esm',platform:'node',jsx:'automatic',external:['react','react-dom','react/jsx-runtime'],loader:{'.css':'empty'},plugins:[{name:'feature-under-test',setup(builder){if(!existsSync(source)){builder.onResolve({filter:/PlatformAnalytics\.tsx$/},()=>({path:'missing-feature',namespace:'fixture'}));builder.onLoad({filter:/.*/,namespace:'fixture'},()=>({loader:'js',contents:'export const PlatformAnalyticsSnapshot=()=>null,currentPlatformAnalytics=()=>null,requestPlatformAnalyticsRefresh=async()=>null,platformAnalyticsResult=()=>null,platformAnalyticsPrompt=()=>"";'}));}}}]});
 const bundled=result.outputFiles[0].text.replaceAll('from "react"',`from ${JSON.stringify(pathToFileURL(resolve('node_modules/react/index.js')).href)}`).replaceAll('from "react/jsx-runtime"',`from ${JSON.stringify(pathToFileURL(resolve('node_modules/react/jsx-runtime.js')).href)}`);
 const target=join(directory,'analytics.mjs');await writeFile(target,bundled);ui=await import(pathToFileURL(target).href);
 server=createServer(async(req,res)=>{const chunks=[];for await(const chunk of req)chunks.push(chunk);requests.push({path:req.url,method:req.method,body:JSON.parse(Buffer.concat(chunks).toString()||'{}')});res.writeHead(200,{'content-type':'application/json'});res.end(JSON.stringify({job_id:'analytics-1'}));});
 await new Promise(resolve=>server.listen(0,'127.0.0.1',resolve));origin=`http://127.0.0.1:${server.address().port}`;
});
after(async()=>{server?.close();if(directory)await rm(directory,{recursive:true,force:true});});
const account={platform:'douyin',name:'抖音',loggedIn:true,verified:true,session_id:'session-current',identity_name:'住宅观察'};
const snapshot={platform:'douyin',session_id:'session-current',status:'partial',reason:'部分指标当前不可读取',observed_at:'2026-10-14T01:00:00+08:00',source:'平台创作者中心',metrics:{followers:0,works:12,views:null,likes:2735},items:[{id:'w1',title:'住宅采光如何判断',url:'https://www.douyin.com/video/12345',published_at:'2026-10-13T20:00:00+08:00',metrics:{views:120000,likes:1000,comments:null}}]};
const render=element=>renderToStaticMarkup(element);
// Catches missing metrics being replaced with zero and cached read time being implied as live.
test('ops snapshot separates real zero, unavailable metrics, source and observation time',()=>{
 const html=render(React.createElement(ui.PlatformAnalyticsSnapshot,{snapshot,account}));
 assert.match(html,/住宅观察/);assert.match(html,/粉丝/);assert.match(html,/>0</);assert.match(html,/12/);assert.match(html,/未获取/);assert.match(html,/2026/);assert.match(html,/平台创作者中心/);assert.match(html,/部分指标当前不可读取/);assert.match(html,/住宅采光如何判断/);assert.doesNotMatch(html,/已同步全部|实时数据/);
});
// Catches private historical data from a prior platform identity appearing after a relogin.
test('only the current platform session can display cached metrics and works',()=>{
 const current=ui.currentPlatformAnalytics([{...snapshot,platform:'xiaohongshu'},{...snapshot,session_id:'old-session',metrics:{followers:88888},items:[{title:'old-private-work'}]}],account);
 assert.equal(current?.platform,'douyin');assert.deepEqual(current?.metrics,{});assert.deepEqual(current?.items,[]);
 const html=render(React.createElement(ui.PlatformAnalyticsSnapshot,{snapshot:current,account}));assert.doesNotMatch(html,/88888|old-private-work/);assert.match(html,/重新同步|账号会话/);
 const valid=ui.currentPlatformAnalytics([snapshot],account);assert.equal(valid?.metrics.followers,0);
});
// Catches a user's requested sync being sent to a different project or performed for an unverified identity.
test('sync is an explicit durable POST for the selected project verified platform',async()=>{
 requests=[];const base=`${origin}/api/projects/p-selected/operations/platform-analytics`;
 const result=await ui.requestPlatformAnalyticsRefresh(base,account,new AbortController().signal);assert.equal(result?.job_id,'analytics-1');
 assert.deepEqual(requests,[{path:'/api/projects/p-selected/operations/platform-analytics/douyin/refresh',method:'POST',body:{}}]);
 for(const row of [{...account,verified:false},{...account,loggedIn:false},{...account,session_id:null}])await assert.rejects(ui.requestPlatformAnalyticsRefresh(base,row),/登录|校验|会话/);
 assert.equal(requests.length,1);
});
// Catches a previous platform's completing task being delivered to the new selected platform.
test('analytics task results must match the selected platform and current session',()=>{
 const job={kind:'platform_analytics',status:'succeeded',result:{platform_analytics:snapshot}};
 assert.equal(ui.platformAnalyticsResult(job,account)?.metrics.works,12);
 assert.equal(ui.platformAnalyticsResult(job,{...account,platform:'bilibili'}),null);
 assert.equal(ui.platformAnalyticsResult(job,{...account,session_id:'other'}),null);
 assert.equal(ui.platformAnalyticsResult({...job,status:'running'},account),null);
});
// Catches executable source links or treating an unsupported platform as already synchronized.
test('platform evidence links are safe and unavailable data remains explicit',()=>{
 const html=render(React.createElement(ui.PlatformAnalyticsSnapshot,{account,snapshot:{...snapshot,status:'not_supported',metrics:{},items:[{id:'malicious',title:'作品',url:'javascript:alert(1)'}],reason:'平台尚无可读数据入口'}}));
 assert.doesNotMatch(html,/href="javascript/);assert.match(html,/平台尚无可读数据入口/);assert.match(html,/未获取/);
});
// Catches passing invented data or irrelevant account context to the operations agent.
test('agent handoff identifies the project-selected platform account and verified observation',()=>{
 const text=ui.platformAnalyticsPrompt(account,snapshot);
 assert.match(text,/抖音/);assert.match(text,/住宅观察/);assert.match(text,/2026-10-14/);assert.match(text,/0/);assert.match(text,/缺失|未获取/);assert.match(text,/住宅|选题|复盘/);assert.doesNotMatch(text,/cookie|token|session-current/);
});
// Catches authentic period/source text being turned into fabricated precise totals.
test('period indicators retain platform labels and scope while absent counts stay absent',()=>{
 const normalized={...snapshot,metrics:{followers:null,following:56,works:12,reads:2300,likes:null},period_metrics:[{label:'近7日播放',value:'1.2万',comparison:'+3.2%',window:'近7日'}],items:[{title:'住宅复盘',metrics:{views:null,likes:null},metrics_text:'播放 1.2万 · 评论 --'}],metric_scope:'mixed',limitations:['平台缩写不可计算精确增长']};
 const html=render(React.createElement(ui.PlatformAnalyticsSnapshot,{snapshot:normalized,account}));assert.match(html,/关注/);assert.match(html,/56/);assert.match(html,/2,300/);assert.match(html,/近7日播放/);assert.match(html,/1.2万/);assert.match(html,/\+3.2%/);assert.match(html,/平台缩写不可计算精确增长/);assert.match(html,/播放 1.2万 · 评论 --/);assert.doesNotMatch(html,/12,000/);
});
// Catches a never-synced empty normalized backend row being misreported as an account switch.
test('a never-synced row with null metrics retains its reason and not-refreshed status',()=>{
 const row={platform:'douyin',session_id:null,status:'not_refreshed',reason:'尚未刷新账号数据',metrics:{followers:null,following:null,likes:null,works:null,reads:null},items:[]};
 assert.equal(ui.currentPlatformAnalytics([row],account)?.status,'not_refreshed');
 const html=render(React.createElement(ui.PlatformAnalyticsSnapshot,{account,snapshot:row}));assert.match(html,/尚未刷新账号数据/);assert.doesNotMatch(html,/账号已改变/);
});
