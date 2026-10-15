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
before(async()=>{dir=await mkdtemp(join(tmpdir(),'louxiang-search-account-'));const src=resolve('src/SearchAccountConnection.tsx');const built=await build({stdin:{contents:`export * from ${JSON.stringify(src)};export {AppContext} from ${JSON.stringify(resolve('src/hooks.tsx'))};`,resolveDir:process.cwd(),loader:'tsx'},write:false,bundle:true,format:'esm',platform:'node',jsx:'automatic',external:['react','react-dom','react/jsx-runtime'],loader:{'.css':'empty'},plugins:[{name:'missing-feature',setup(b){if(!existsSync(src)){b.onResolve({filter:/SearchAccountConnection\.tsx$/},()=>({path:'missing',namespace:'fixture'}));b.onLoad({filter:/.*/,namespace:'fixture'},()=>({loader:'js',contents:'export const SearchAccountConnection=()=>null,OperationalSearchAccountView=()=>null;export const requestOperationalSearchAccount=async()=>null,updateOperationalSearchAccount=async()=>null;'}));}}}]});const bundle=built.outputFiles[0].text.replaceAll('from "react"',`from ${JSON.stringify(pathToFileURL(resolve('node_modules/react/index.js')).href)}`).replaceAll('from "react/jsx-runtime"',`from ${JSON.stringify(pathToFileURL(resolve('node_modules/react/jsx-runtime.js')).href)}`);const dest=join(dir,'account.mjs');await writeFile(dest,bundle);ui=await import(pathToFileURL(dest));server=createServer(async(req,res)=>{const chunks=[];for await(const c of req)chunks.push(c);requests.push({path:req.url,method:req.method,body:Buffer.concat(chunks).toString()});res.writeHead(200,{'content-type':'application/json'});res.end(JSON.stringify({state:'success',loggedIn:true,verified:true,name:'已登录的住宅账号'}));});await new Promise(r=>server.listen(0,'127.0.0.1',r));origin=`http://127.0.0.1:${server.address().port}`;});
after(async()=>{server?.close();if(dir)await rm(dir,{recursive:true,force:true});});
const context={project:{id:'project-current',name:'当前住宅项目'},session:{role:'owner'},navigate(){}};
function pane(scope){return renderToStaticMarkup(React.createElement(ui.AppContext.Provider,{value:context},React.createElement(ui.SearchAccountConnection,{scope,editable:true})));}
// Catches showing the independently logged-out research profile as the search's operational account.
test('account pane displays only the session selected by the search scope',()=>{
 const operational=pane('operations');assert.match(operational,/本项目运营账号/);assert.match(operational,/核对已有登录/);assert.match(operational,/当前住宅项目/);assert.doesNotMatch(operational,/请用抖音 App 扫码|douyin-research-auth/);
 const research=pane('research');assert.match(research,/独立研究账号/);assert.match(research,/douyin-research-auth/);assert.doesNotMatch(research,/核对已有登录/);
});
// Catches a cached login being rejected or a logged-out project being forced into an unrelated QR flow.
test('existing operational login is named and can be verified without starting login',()=>{
 const props={status:{state:'success',loggedIn:true,verified:true,name:'已有运营账号'},projectName:'云境项目',loading:false,busy:false,error:'',editable:true,onVerify(){},onRefresh(){},onOpenAccounts(){}};
 const html=renderToStaticMarkup(React.createElement(ui.OperationalSearchAccountView,props));assert.match(html,/已有运营账号/);assert.match(html,/已登录并核对/);assert.match(html,/核对已有登录/);assert.doesNotMatch(html,/扫码|连接抖音/);
 const loggedOut=renderToStaticMarkup(React.createElement(ui.OperationalSearchAccountView,{...props,status:{state:'idle',loggedIn:false,verified:false}}));assert.match(loggedOut,/前往账号页/);assert.match(loggedOut,/当前项目/);
 const readOnly=renderToStaticMarkup(React.createElement(ui.OperationalSearchAccountView,{...props,editable:false}));const verify=Array.from(readOnly.matchAll(/<button\b[^>]*>[\s\S]*?<\/button>/g),m=>m[0]).find(b=>b.includes('核对已有登录'));assert.ok(verify?.includes('disabled=""'));assert.match(readOnly,/已有运营账号/);
});
// Catches verification being sent to the separate research login or another project's account.
test('status and verification use the existing current project operational endpoints only',async()=>{
 const native=globalThis.fetch;globalThis.fetch=(path,options)=>native(origin+path,options);requests.length=0;
 try{const status=await ui.requestOperationalSearchAccount('p 1','status');assert.equal(status?.name,'已登录的住宅账号');await ui.requestOperationalSearchAccount('p 1','verify');assert.deepEqual(requests,[{path:'/api/projects/p%201/operations/platform-accounts/douyin/status',method:'GET',body:''},{path:'/api/projects/p%201/operations/platform-accounts/douyin/verify',method:'POST',body:''}]);}finally{globalThis.fetch=native;}
});
// Catches a late response surviving a project/scope switch even when the transport cannot abort it.
test('an aborted account lookup cannot deliver a late project status',async()=>{
 const native=globalThis.fetch,controller=new AbortController(),observed=[];globalThis.fetch=async()=>{controller.abort();return new Response(JSON.stringify({state:'success',loggedIn:true,verified:true,name:'旧项目账号'}),{headers:{'content-type':'application/json'}});};
 try{await assert.rejects(ui.updateOperationalSearchAccount('project-old','status',{signal:controller.signal,onStatus:value=>observed.push(value)}),error=>error.name==='AbortError');assert.deepEqual(observed,[]);}finally{globalThis.fetch=native;}
});
