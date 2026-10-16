import {after,before,test} from 'node:test';
import assert from 'node:assert/strict';
import {existsSync} from 'node:fs';
import {mkdtemp,rm,writeFile} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import {join,resolve} from 'node:path';
import {pathToFileURL} from 'node:url';
import {setImmediate as nextTurn} from 'node:timers/promises';
import {build} from 'esbuild';
import React from 'react';
import {renderToStaticMarkup} from 'react-dom/server';
let feature,directory;
before(async()=>{
 directory=await mkdtemp(join(tmpdir(),'louxiang-external-sync-'));
 const result=await build({stdin:{contents:`export * from './useExternalAgentSync'; export * from './ExternalAgentConnections';`,resolveDir:resolve('src'),loader:'tsx'},write:false,bundle:true,format:'esm',platform:'node',jsx:'automatic',external:['react','react/jsx-runtime'],plugins:[{name:'missing-feature',setup(builder){
  builder.onResolve({filter:/^\.\/(useExternalAgentSync|ExternalAgentConnections)$/},args=>existsSync(resolve('src',`${args.path.slice(2)}.${args.path.includes('Connections')?'tsx':'ts'}`))?null:({path:args.path,namespace:'missing'}));
  builder.onLoad({filter:/.*/,namespace:'missing'},({path})=>({loader:'js',contents:path.includes('Connections')?'export const ExternalAgentConnectionList=()=>null;':'export const selectAuthorizedProject=(rows,current)=>current; export const startExternalAgentSync=()=>()=>{};'}));
 }}]});
 const bundled=result.outputFiles[0].text.replaceAll('from "react"',`from ${JSON.stringify(pathToFileURL(resolve('node_modules/react/index.js')).href)}`).replaceAll('from "react/jsx-runtime"',`from ${JSON.stringify(pathToFileURL(resolve('node_modules/react/jsx-runtime.js')).href)}`);
 const target=join(directory,'feature.mjs');await writeFile(target,bundled);feature=await import(pathToFileURL(target).href);
});
after(async()=>{if(directory)await rm(directory,{recursive:true,force:true});});
function surface(t,visible=true){
 const window=new EventTarget(),document=new EventTarget();document.visibilityState=visible?'visible':'hidden';
 const beforeWindow=Object.getOwnPropertyDescriptor(globalThis,'window'),beforeDocument=Object.getOwnPropertyDescriptor(globalThis,'document');
 Object.defineProperty(globalThis,'window',{configurable:true,writable:true,value:window});Object.defineProperty(globalThis,'document',{configurable:true,writable:true,value:document});
 t.after(()=>{if(beforeWindow)Object.defineProperty(globalThis,'window',beforeWindow);else delete globalThis.window;if(beforeDocument)Object.defineProperty(globalThis,'document',beforeDocument);else delete globalThis.document;});
 return {window,document,visibility(value){document.visibilityState=value?'visible':'hidden';document.dispatchEvent(new Event('visibilitychange'));}};
}
const revision=value=>new Response(JSON.stringify({revision:value}),{headers:{'content-type':'application/json'}});

// Catches URL-based authorization bypass and accidental re-selection on every refresh.
test('a project link selects only an authorized project and otherwise preserves the current project',()=>{
 const rows=[{id:'public-a'},{id:'public-b'}];
 assert.equal(feature.selectAuthorizedProject(rows,'public-a','?project_id=public-b'),'public-b');
 assert.equal(feature.selectAuthorizedProject(rows,'public-b','?project_id=private'),'public-b');
 assert.equal(feature.selectAuthorizedProject(rows,'deleted','?project_id=private'),'public-a');
 assert.equal(feature.selectAuthorizedProject(rows,'public-b',''),'public-b');
 assert.equal(feature.selectAuthorizedProject([],'private','?project_id=private'),'');
});

// Catches reload loops caused by reads, baseline fetches, or an unchanged revision.
test('initial baseline and repeated reads do not reload while one external revision refreshes once',async t=>{
 surface(t);t.mock.timers.enable({apis:['setTimeout']});let reads=0,reloads=0;
 t.mock.method(globalThis,'fetch',async(path,options)=>{assert.equal(path,'/api/projects/project%2Fone/agent-changes');assert.equal(options.credentials,'same-origin');reads++;return revision(reads<3?'r1':'r2');});
 const stop=feature.startExternalAgentSync('project/one',async()=>{reloads++;});
 try{
  await nextTurn();assert.equal(reads,1);assert.equal(reloads,0);
  t.mock.timers.tick(3000);await nextTurn();assert.equal(reads,2);assert.equal(reloads,0);
  t.mock.timers.tick(3000);await nextTurn();assert.equal(reloads,1);
  t.mock.timers.tick(3000);await nextTurn();assert.equal(reloads,1);
 }finally{stop();}
 t.mock.timers.tick(30000);await nextTurn();assert.equal(reads,4);
});

// Catches background polling and missing immediate catch-up when returning to the page.
test('hidden pages pause and focus checks immediately without duplicate requests',async t=>{
 const page=surface(t,false);t.mock.timers.enable({apis:['setTimeout']});let reads=0,reloads=0;
 t.mock.method(globalThis,'fetch',async()=>revision(++reads===1?'r1':'r2'));
 const stop=feature.startExternalAgentSync('p1',async()=>{reloads++;});
 try{
  await nextTurn();t.mock.timers.tick(30000);await nextTurn();assert.equal(reads,0);
  page.visibility(true);page.window.dispatchEvent(new Event('focus'));await nextTurn();assert.equal(reads,1);assert.equal(reloads,0);
  page.visibility(false);t.mock.timers.tick(30000);await nextTurn();assert.equal(reads,1);
  page.visibility(true);await nextTurn();assert.equal(reads,2);assert.equal(reloads,1);
 }finally{stop();}
});

// Catches stale project responses mutating the new project's UI after cleanup.
test('cleanup aborts an in-flight request and ignores a late response',async t=>{
 surface(t);let release,signal,reloads=0;
 t.mock.method(globalThis,'fetch',async(_path,options)=>{signal=options.signal;return new Promise(resolve=>{release=resolve;});});
 const stop=feature.startExternalAgentSync('old-project',async()=>{reloads++;});await nextTurn();
 assert.ok(signal);stop();assert.equal(signal.aborted,true);release(revision('late'));await nextTurn();assert.equal(reloads,0);
});

// Catches tight error retry loops and a backoff that never recovers to the normal cadence.
test('network failures back off to thirty seconds and successful polling resets the cadence',async t=>{
 surface(t);t.mock.timers.enable({apis:['setTimeout']});let reads=0;
 t.mock.method(globalThis,'fetch',async()=>{reads++;if(reads<=5)throw new TypeError('offline');return revision('r1');});
 const stop=feature.startExternalAgentSync('p1',async()=>{});
 try{
  await nextTurn();assert.equal(reads,1);
  for(const [wait,count]of [[6000,2],[12000,3],[24000,4],[30000,5],[30000,6],[3000,7]]){
   t.mock.timers.tick(wait-1);await nextTurn();assert.equal(reads,count-1);
   t.mock.timers.tick(1);await nextTurn();assert.equal(reads,count);
  }
 }finally{stop();}
});

// Catches expired sessions silently polling forever instead of returning to login.
test('an expired session emits the normal auth event and stops polling even on focus',async t=>{
 const page=surface(t);t.mock.timers.enable({apis:['setTimeout']});let reads=0,expired=0;
 page.window.addEventListener('housignal-session-expired',()=>{expired++;});
 t.mock.method(globalThis,'fetch',async()=>{reads++;return new Response('{"detail":"登录已失效"}',{status:401,headers:{'content-type':'application/json'}});});
 const stop=feature.startExternalAgentSync('p1',async()=>{});
 try{await nextTurn();assert.equal(expired,1);t.mock.timers.tick(90000);page.window.dispatchEvent(new Event('focus'));await nextTurn();assert.equal(reads,1);}finally{stop();}
});

// Catches displaying secrets, losing scope labels, or allowing an already-revoked connection to act.
test('connection list shows project scopes and expiry with only active revoke controls',()=>{
 const html=renderToStaticMarkup(React.createElement(feature.ExternalAgentConnectionList,{connections:[
  {id:'active',client_name:'ChatGPT',scopes:['louxiang:read','louxiang:write'],project_ids:['p1'],expires_at:'2099-11-15T00:00:00Z',revoked_at:null,access_token:'never-render-this-secret'},
  {id:'revoked',client_name:'Codex CLI',scopes:['louxiang:read'],project_names:['研究项目'],expires_at:null,revoked_at:'2026-10-14T00:00:00Z'},
 ],projects:[{id:'p1',name:'陆家嘴大宅'}],busy:false,onRevoke(){}}));
 assert.match(html,/ChatGPT/);assert.match(html,/陆家嘴大宅/);assert.match(html,/研究项目/);assert.match(html,/策划/);assert.match(html,/2099/);assert.match(html,/已撤销/);assert.doesNotMatch(html,/never-render-this-secret/);
 const actions=[...html.matchAll(/<button\b[^>]*>[\s\S]*?<\/button>/g)].map(row=>row[0]);assert.equal(actions.length,1);assert.match(actions[0],/撤销/);
});

// Catches treating a server's timezone-less UTC expiry as browser-local wall time.
test('connection expiry without a timezone follows the backend UTC convention',t=>{
 const previous=process.env.TZ;process.env.TZ='Asia/Shanghai';t.after(()=>{if(previous===undefined)delete process.env.TZ;else process.env.TZ=previous;});
 t.mock.method(Date,'now',()=>Date.parse('2099-11-15T04:00:00Z'));
 const html=renderToStaticMarkup(React.createElement(feature.ExternalAgentConnectionList,{connections:[{id:'utc',client_name:'UTC client',scopes:['louxiang:read'],project_ids:['p1'],expires_at:'2099-11-15T08:00:00',revoked_at:null}],projects:[{id:'p1',name:'研究项目'}],busy:false,onRevoke(){}}));
 assert.match(html,/已连接/);assert.match(html,/16:00/);assert.doesNotMatch(html,/已到期/);
});
