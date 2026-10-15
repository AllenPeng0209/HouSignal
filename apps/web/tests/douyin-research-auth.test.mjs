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

let ui,directory,server,origin,statuses,requests;
before(async()=>{
 directory=await mkdtemp(join(tmpdir(),'housignal-douyin-research-'));
 const source=resolve('src/DouyinResearchAuth.tsx');
 const result=await build({entryPoints:[source],write:false,bundle:true,format:'esm',platform:'node',jsx:'automatic',external:['react','react-dom','react/jsx-runtime'],loader:{'.css':'empty'},plugins:[{name:'missing-feature',setup(builder){if(!existsSync(source)){builder.onResolve({filter:/DouyinResearchAuth\.tsx$/},()=>({path:'missing-feature',namespace:'fixture'}));builder.onLoad({filter:/.*/,namespace:'fixture'},()=>({loader:'js',contents:'export const DouyinResearchAuthView=()=>null;export const requestDouyinResearchAccount=async()=>null,requestDouyinResearchQr=async()=>null,watchDouyinResearchLogin=async()=>null;'}));}}}]});
 const bundled=result.outputFiles[0].text.replaceAll('from "react"',`from ${JSON.stringify(pathToFileURL(resolve('node_modules/react/index.js')).href)}`).replaceAll('from "react/jsx-runtime"',`from ${JSON.stringify(pathToFileURL(resolve('node_modules/react/jsx-runtime.js')).href)}`);
 const target=join(directory,'research.mjs');await writeFile(target,bundled);ui=await import(pathToFileURL(target).href);
 server=createServer(async(req,res)=>{const chunks=[];for await(const chunk of req)chunks.push(chunk);requests.push({path:req.url,method:req.method,body:Buffer.concat(chunks).toString()});
  if(req.url.includes('/qr?')){res.writeHead(200,{'content-type':'image/png'});res.end(Buffer.from([137,80,78,71,13,10,26,10]));return;}
  const value=statuses.shift()||{state:'unknown',message:'尚未连接',qrReady:false,loggedIn:false,verified:false,ready:true,scope:'research'};
  res.writeHead(value.httpStatus||200,{'content-type':'application/json'});res.end(JSON.stringify(value.body||value));
 });
 await new Promise(resolve=>server.listen(0,'127.0.0.1',resolve));origin=`http://127.0.0.1:${server.address().port}`;
});
after(async()=>{server?.close();if(directory)await rm(directory,{recursive:true,force:true});});
const noop=()=>{};
const status={state:'qr_ready',message:'请用抖音扫码',qrReady:true,qrTs:42,loggedIn:false,verified:false,ready:true,scope:'research'};
const props={status,qrSource:'blob:private-official-qr',error:'',qrError:'',busy:false,editable:true,onLogin:noop,onVerify:noop,onDisconnect:noop,onRefresh:noop};
const render=value=>renderToStaticMarkup(React.createElement(ui.DouyinResearchAuthView,value));
async function withFetch(run){const nativeFetch=globalThis.fetch;globalThis.fetch=(path,options)=>nativeFetch(origin+path,options);try{return await run();}finally{globalThis.fetch=nativeFetch;}}

// Catches login requests being sent to a shared publisher session or a stale project.
test('research account actions target only the current project research endpoints',async()=>{
 requests=[];statuses=[];
 await withFetch(async()=>{await ui.requestDouyinResearchAccount('project/b','status');await ui.requestDouyinResearchAccount('project/b','login');await ui.requestDouyinResearchAccount('project/b','verify');await ui.requestDouyinResearchAccount('project/b','disconnect');});
 assert.deepEqual(requests,[{path:'/api/projects/project%2Fb/operations/discovery/douyin-account',method:'GET',body:''},{path:'/api/projects/project%2Fb/operations/discovery/douyin-account/login',method:'POST',body:''},{path:'/api/projects/project%2Fb/operations/discovery/douyin-account/verify',method:'POST',body:''},{path:'/api/projects/project%2Fb/operations/discovery/douyin-account/disconnect',method:'POST',body:''}]);
});
// Catches a widened helper issuing SMS or publishing operations under research authorization.
test('unsupported actions and publisher-scoped responses fail without accepting shared auth',async()=>{
 requests=[];statuses=[{...status,scope:'publisher'}];
 await withFetch(async()=>{await assert.rejects(ui.requestDouyinResearchAccount('p-1','publish'),/研究/);assert.equal(requests.length,0);await assert.rejects(ui.requestDouyinResearchAccount('p-1','status'),/研究/);});
});
// Catches stale QR display after challenge/expiry and accepting unverified account states as ready.
test('official QR renders only during QR login and readiness requires verified login',()=>{
 let html=render(props);assert.match(html,/src="blob:private-official-qr"/);assert.match(html,/alt="抖音研究账号登录二维码"/);assert.doesNotMatch(html,/已连接并核对/);
 html=render({...props,status:{...status,state:'success',loggedIn:true,verified:false}});assert.match(html,/待核对/);assert.doesNotMatch(html,/已连接并核对|private-official-qr/);
 html=render({...props,status:{...status,state:'success',loggedIn:true,verified:true}});assert.match(html,/已连接并核对/);assert.doesNotMatch(html,/private-official-qr/);
 html=render({...props,status:{...status,state:'expired'}});assert.match(html,/重新连接/);assert.doesNotMatch(html,/private-official-qr/);
});
// Catches cached login metadata falsely overriding a current verification challenge.
test('an active challenge or missing runtime never claims a usable research connection',()=>{
 let html=render({...props,status:{...status,state:'challenge',loggedIn:true,verified:true}});assert.match(html,/需要人工验证/);assert.doesNotMatch(html,/已连接并核对/);
 html=render({...props,status:{...status,state:'success',loggedIn:true,verified:true,ready:false}});assert.doesNotMatch(html,/已连接并核对/);
});
// Catches collecting verification codes in the research UI instead of human official-page completion.
test('challenges and official browser login direct the human to the platform window',()=>{
 for(const state of ['challenge','window_login']){
  const html=render({...props,status:{...status,state,message:'请在官方页面完成'}});assert.match(html,/官方.*浏览器.*窗口/);assert.doesNotMatch(html,/<input|private-official-qr|提交验证码|发布内容/);assert.match(html,/核对登录状态/);
 }
});
// Catches unauthorized roles receiving enabled research auth controls.
test('read-only roles cannot connect verify or disconnect research sessions',()=>{
 const html=render({...props,editable:false,status:{...status,state:'success',loggedIn:true,verified:true}});
 const actions=Array.from(html.matchAll(/<button\b[^>]*>[\s\S]*?<\/button>/g),match=>match[0]).filter(value=>/连接抖音|核对登录状态|断开研究账号/.test(value));assert.ok(actions.length>=2);assert.ok(actions.every(value=>value.includes('disabled=""')));
});
// Catches QR requests losing authentication or bypassing error handling through an image URL.
test('QR requests use the private project endpoint and return an image blob',async()=>{
 requests=[];const calls=[],nativeFetch=globalThis.fetch;
 globalThis.fetch=(path,options)=>{calls.push(options);return nativeFetch(origin+path,options);};
 try{const blob=await ui.requestDouyinResearchQr('p-current',42,new AbortController().signal);assert.equal(blob?.type,'image/png');assert.equal(calls[0]?.credentials,'same-origin');assert.equal(calls[0]?.cache,'no-store');assert.deepEqual(requests,[{path:'/api/projects/p-current/operations/discovery/douyin-account/qr?v=42',method:'GET',body:''}]);}finally{globalThis.fetch=nativeFetch;}
});
// Catches sending stale polling results after project-switch/unmount abort.
test('aborting a research login watcher stops status delivery and later requests',async()=>{
 requests=[];statuses=[status];const controller=new AbortController(),observed=[];
 await withFetch(async()=>{await assert.rejects(ui.watchDouyinResearchLogin('p-old',{signal:controller.signal,pollMs:20,onStatus:value=>{observed.push(value.state);controller.abort();}}),error=>error.name==='AbortError');await new Promise(resolve=>setTimeout(resolve,30));});
 assert.deepEqual(observed,['qr_ready']);assert.equal(requests.length,1);
});
// Catches background polling continuing after verified success.
test('research watchers stop on verified success and preserve the status',async()=>{
 requests=[];statuses=[{...status,state:'starting'},{...status,state:'success',loggedIn:true,verified:true}];const observed=[];
 const outcome=await withFetch(()=>ui.watchDouyinResearchLogin('p-1',{signal:new AbortController().signal,pollMs:1,onStatus:value=>observed.push(value.state)}));
 assert.equal(outcome?.state,'success');assert.deepEqual(observed,['starting','success']);assert.equal(requests.length,2);
});
// Catches a completed human verification never being reflected while the login runner still owns its lock.
test('challenge polling reads status until the human completes login without sending verification actions',async()=>{
 requests=[];statuses=[{...status,state:'challenge',message:'请在官方窗口验证'},{...status,state:'success',loggedIn:true,verified:true}];const observed=[];
 const outcome=await withFetch(()=>ui.watchDouyinResearchLogin('p-human',{signal:new AbortController().signal,pollMs:1,onStatus:value=>observed.push(value.state)}));
 assert.equal(outcome?.state,'success');assert.deepEqual(observed,['challenge','success']);assert.deepEqual(requests,[{path:'/api/projects/p-human/operations/discovery/douyin-account',method:'GET',body:''},{path:'/api/projects/p-human/operations/discovery/douyin-account',method:'GET',body:''}]);
});
// Catches users being trapped in a pending login until the owned browser process times out.
test('every pending login state exposes an authorized end-connection action',()=>{
 for(const state of ['starting','qr_ready','window_login','challenge']){
  let html=render({...props,status:{...status,state}});assert.match(html,/<button\b[^>]*>[\s\S]*结束本次连接/);
  html=render({...props,editable:false,status:{...status,state}});const end=Array.from(html.matchAll(/<button\b[^>]*>[\s\S]*?<\/button>/g),match=>match[0]).find(value=>value.includes('结束本次连接'));assert.ok(end?.includes('disabled=""'));
 }
 const html=render({...props,status:{...status,state:'success',loggedIn:true,verified:true}});assert.match(html,/断开研究账号/);assert.doesNotMatch(html,/结束本次连接/);
});
// Catches a failed refresh/verify erasing the official state and hiding the cancellation action.
test('failed non-login actions preserve the previously observed connection status',async()=>{
 assert.equal(typeof ui.updateDouyinResearchAccount,'function');
 for(const action of ['status','verify','disconnect']){
  requests=[];statuses=[{httpStatus:503,body:{detail:'暂时无法核对'}}];let observed={...status,state:'challenge'};
  await withFetch(()=>assert.rejects(ui.updateDouyinResearchAccount('p-1',action,{signal:new AbortController().signal,onStatus:value=>{observed=value;}}),/暂时无法核对/));
  assert.equal(observed.state,'challenge');assert.equal(observed.qrTs,42);
 }
});
