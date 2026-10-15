/** A dependency-waiting task allows other work and later queued turns resume once. */
import assert from 'node:assert/strict';
import {createServer} from 'node:http';
import {createRequire} from 'node:module';
import {build} from 'esbuild';
import {resolve} from 'node:path';
const require=createRequire(import.meta.url);
const {chromium}=require(process.env.HS_PLAYWRIGHT_MODULE||'/Users/pengyanlun/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright');
const bundle=await build({stdin:{contents:`
 import React from 'react';import {createRoot} from 'react-dom/client';
 import {Assistant} from ${JSON.stringify(resolve('src/Assistant.tsx'))};import {AppContext} from ${JSON.stringify(resolve('src/hooks.tsx'))};
 createRoot(document.getElementById('app')).render(<AppContext.Provider value={{project:{id:'p1',name:'隔离等待与接续',completeness:{completed:1,total:7,percent:14}},session:{role:'owner'},refresh:0,reload:async()=>{},notify(){},navigate(){},citation(){},openReadiness(){}}}><Assistant/></AppContext.Provider>);`,resolveDir:process.cwd(),loader:'tsx'},bundle:true,write:false,format:'esm',platform:'browser',jsx:'automatic',loader:{'.css':'empty'}});
const at='2026-10-15T01:00:00Z';
const jobs={waiting:{id:'waiting',kind:'chat',status:'queued',progress:0,waiting_for_job_id:'production1',created_at:at,result:{}},direct:{id:'direct',kind:'chat',status:'running',progress:0.2,created_at:at,result:{preview_text:'正在处理新交代事项'}},late:{id:'late',kind:'chat',status:'running',progress:0.2,created_at:at,result:{preview_text:'接续收到的后续事项'}}};
const messages=[{id:'u1',role:'user',content:'制作后继续整理视频',status:'completed',citations:[]},{id:'waiting-message',role:'assistant',content:'',status:'queued',job_id:'waiting',citations:[]}];
const mutations=[],errors=[],streamCounts={},streams={};let storedDraft='';
const publish=id=>streams[id]?.write(`event: status\ndata: ${JSON.stringify(jobs[id])}\n\n`);
const server=createServer(async(request,response)=>{
 const path=new URL(request.url,'http://fixture').pathname;
 const send=(value,status=200)=>{response.writeHead(status,{'content-type':'application/json'});response.end(JSON.stringify(value));};
 if(path==='/'){response.writeHead(200,{'content-type':'text/html'});response.end('<!doctype html><html lang="zh"><body><div id="app"></div><script type="module" src="/app.js"></script></body></html>');return;}
 if(path==='/app.js'){response.writeHead(200,{'content-type':'text/javascript'});response.end(bundle.outputFiles[0].text);return;}
 if(path==='/favicon.ico'){response.writeHead(204);response.end();return;}
 const event=path.match(/^\/api\/jobs\/(waiting|direct|late)\/events$/);if(event){const id=event[1];streams[id]=response;streamCounts[id]=(streamCounts[id]||0)+1;response.writeHead(200,{'content-type':'text/event-stream'});publish(id);return;}
 let body={};if(request.method!=='GET'){const chunks=[];for await(const chunk of request)chunks.push(chunk);body=JSON.parse(Buffer.concat(chunks).toString()||'{}');mutations.push({path,method:request.method,body});}
 if(path==='/api/model/status')return send({configured:true,provider:'fixture',model:'fixture'});
 if(path==='/api/projects/p1/conversations')return send([{id:'c1',title:'等待中的项目对话',draft:storedDraft,created_at:at}]);
 if(path==='/api/conversations/c1/messages'&&request.method==='GET')return send(messages);
 if(path==='/api/conversations/c1/messages'&&request.method==='POST'){messages.push({id:'u2',role:'user',content:body.text,status:'completed',citations:[]},{id:'direct-message',role:'assistant',content:'',status:'running',job_id:'direct',citations:[]});storedDraft='';return send({job_id:'direct'},202);}
 if(path==='/api/conversations/c1/draft'){storedDraft=body.draft;return send({draft:storedDraft});}
 errors.push(`${request.method} ${path}`);return send({detail:'Unknown isolated API'},404);
});
await new Promise(resolve=>server.listen(0,'127.0.0.1',resolve));
const browser=await chromium.launch({headless:true,channel:process.env.HS_BROWSER_CHANNEL||'chrome'});
const page=await browser.newPage({viewport:{width:1360,height:1000}});page.setDefaultTimeout(6000);page.on('pageerror',error=>errors.push(error.message));
try{
 await page.goto(`http://127.0.0.1:${server.address().port}/`);
 await page.getByText('等待制作任务完成，可继续交代其他事项',{exact:true}).waitFor();
 const composer=page.getByRole('textbox',{name:'向楼管家交代事项'});await composer.fill('先更新客户预算');await page.getByRole('button',{name:'发送消息',exact:true}).click();
 await page.getByText('正在处理新交代事项',{exact:true}).waitFor();
 assert.equal(mutations.filter(row=>row.path.endsWith('/messages')).length,1);assert.equal(mutations.filter(row=>row.path.endsWith('/control')).length,0);
 messages.find(row=>row.id==='direct-message').status='completed';messages.find(row=>row.id==='direct-message').content='已处理新交代事项';
 messages.push({id:'late-message',role:'assistant',content:'',status:'queued',job_id:'late',citations:[]});jobs.direct.status='succeeded';publish('direct');streams.direct.end();
 await page.getByText('接续收到的后续事项',{exact:true}).waitFor();assert.equal(streamCounts.late,1);
 messages.find(row=>row.id==='waiting-message').status='completed';messages.find(row=>row.id==='late-message').status='completed';messages.find(row=>row.id==='late-message').content='接续事项已处理';jobs.late.status='succeeded';publish('late');streams.late.end();
 await page.getByText('接续事项已处理',{exact:true}).waitFor();await page.getByRole('button',{name:'发送消息',exact:true}).waitFor();
 assert.deepEqual(streamCounts,{waiting:1,direct:1,late:1});assert.deepEqual(errors,[]);
 console.log(JSON.stringify({passed:true,checks:['waiting-status-is-explicit','waiting-allows-normal-new-message','new-task-replaces-watcher','late-queued-turn-auto-resumes','each-job-watched-once'],streamCounts}));
}finally{for(const response of Object.values(streams))response.end();await browser.close();await new Promise(resolve=>server.close(resolve));}
