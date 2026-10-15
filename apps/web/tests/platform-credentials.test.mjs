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
let ui,directory,server,origin,requests;
const metadata={configured:true,verified:true,mode:'official_api',appIdMasked:'wx0123***cdef',name:'住宅公众号',author:'团队',verified_at:'2026-10-14T02:00:00Z',verification_expires_at:'2026-10-14T04:00:00Z',code:0,message:'官方 API 凭证已核对',capabilities:'not_checked'};
before(async()=>{
 directory=await mkdtemp(join(tmpdir(),'housignal-credentials-'));
 const source=resolve('src/PlatformCredentials.tsx');
 const result=await build({entryPoints:[source],write:false,bundle:true,format:'esm',platform:'node',jsx:'automatic',external:['react','react-dom','react/jsx-runtime'],loader:{'.css':'empty'},plugins:[{name:'missing-feature',setup(builder){if(!existsSync(source)){builder.onResolve({filter:/PlatformCredentials\.tsx$/},()=>({path:'missing-feature',namespace:'fixture'}));builder.onLoad({filter:/.*/,namespace:'fixture'},()=>({loader:'js',contents:'export const PlatformCredentialsView=()=>null;export const requestPlatformCredentials=async()=>null;'}));}}}]});
 const bundled=result.outputFiles[0].text.replaceAll('from "react"',`from ${JSON.stringify(pathToFileURL(resolve('node_modules/react/index.js')).href)}`).replaceAll('from "react/jsx-runtime"',`from ${JSON.stringify(pathToFileURL(resolve('node_modules/react/jsx-runtime.js')).href)}`);
 const target=join(directory,'credentials.mjs');await writeFile(target,bundled);ui=await import(pathToFileURL(target).href);
 server=createServer(async(req,res)=>{const chunks=[];for await(const chunk of req)chunks.push(chunk);requests.push({path:req.url,method:req.method,body:Buffer.concat(chunks).toString()});res.writeHead(200,{'content-type':'application/json'});res.end(JSON.stringify(metadata));});
 await new Promise(resolve=>server.listen(0,'127.0.0.1',resolve));origin=`http://127.0.0.1:${server.address().port}`;
});
after(async()=>{server?.close();if(directory)await rm(directory,{recursive:true,force:true});});
const base='/api/projects/project%2Fb/operations/platform-accounts';
const props={metadata,error:'',busy:false,editable:true,formRef:React.createRef(),onSubmit(){},onClear(){},onRefresh(){}};
const render=value=>renderToStaticMarkup(React.createElement(ui.PlatformCredentialsView,value));
async function withFetch(run){const nativeFetch=globalThis.fetch;globalThis.fetch=(path,options)=>nativeFetch(origin+path,options);try{return await run();}finally{globalThis.fetch=nativeFetch;}}

test('credential requests target only the current project official API configuration',async()=>{
 requests=[];await withFetch(async()=>{await ui.requestPlatformCredentials(base,'read');await ui.requestPlatformCredentials(base,'save',{appId:'wx0123456789abcdef',appSecret:'private-input',name:'住宅'});await ui.requestPlatformCredentials(base,'clear');});
 assert.deepEqual(requests,[{path:base+'/wechat-oa/credentials',method:'GET',body:''},{path:base+'/wechat-oa/credentials',method:'POST',body:JSON.stringify({appId:'wx0123456789abcdef',appSecret:'private-input',name:'住宅'})},{path:base+'/wechat-oa/credentials',method:'DELETE',body:''}]);
});
test('an arbitrary endpoint or action cannot receive a secret',async()=>{
 requests=[];await withFetch(async()=>{await assert.rejects(ui.requestPlatformCredentials('https://external.example','save',{appSecret:'private-input'}));await assert.rejects(ui.requestPlatformCredentials(base,'publish',{appSecret:'private-input'}));});assert.equal(requests.length,0);
});
test('API validation is labeled independently of browser login and specific capability checks',()=>{
 const html=render(props);assert.match(html,/公众号 API 凭证/);assert.match(html,/wx0123\*\*\*cdef/);assert.match(html,/官方 API 已核对/);assert.match(html,/浏览器.*独立/);assert.match(html,/具体接口权限/);assert.doesNotMatch(html,/登录成功|已发布|private-issued-token/);
});
test('secret field is blank and password typed even with configured credentials',()=>{
 const html=render(props);const input=html.match(/<input\b[^>]*name="appSecret"[^>]*>/)?.[0];assert.ok(input);assert.match(input,/type="password"/);assert.doesNotMatch(input,/value=/);assert.match(html,/保存并验证/);assert.match(html,/清除 API 凭证/);
});
test('read only roles cannot save or clear official API credentials',()=>{
 const html=render({...props,editable:false});const buttons=Array.from(html.matchAll(/<button\b[^>]*>[\s\S]*?<\/button>/g),v=>v[0]).filter(v=>/保存并验证|清除 API 凭证/.test(v));assert.equal(buttons.length,2);assert.ok(buttons.every(v=>v.includes('disabled=""')));assert.match(html.match(/<input\b[^>]*name="appSecret"[^>]*>/)?.[0]||'',/disabled=""/);
});
test('stored but unverified credentials remain explicitly unverified',()=>{
 const html=render({...props,metadata:{...metadata,verified:false,code:40164,message:'请核对 IP 白名单'}});assert.match(html,/未通过核对/);assert.match(html,/40164/);assert.match(html,/IP 白名单/);assert.doesNotMatch(html,/官方 API 已核对/);
});
test('aborted credential requests cannot accept stale metadata',async()=>{
 requests=[];const controller=new AbortController();controller.abort();await withFetch(()=>assert.rejects(ui.requestPlatformCredentials(base,'read',undefined,controller.signal),e=>e.name==='AbortError'));assert.equal(requests.length,0);
});
