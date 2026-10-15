/** Busy control delivery preserves the active task and newer composer typing. */
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
 createRoot(document.getElementById('app')).render(<AppContext.Provider value={{project:{id:'p1',name:'隔离执行控制',completeness:{completed:1,total:7,percent:14}},session:{role:'owner'},refresh:0,reload:async()=>{},notify(){},navigate(){},citation(){},openReadiness(){}}}><Assistant/></AppContext.Provider>);`,resolveDir:process.cwd(),loader:'tsx'},bundle:true,write:false,format:'esm',platform:'browser',jsx:'automatic',loader:{'.css':'empty'}});
const messages=[{id:'u1',role:'user',content:'请制作项目视频',status:'completed',citations:[]},{id:'a1',role:'assistant',content:'',status:'running',job_id:'job1',citations:[]}];
const job={id:'job1',kind:'chat',status:'running',progress:0.2,result:{preview_text:'正在整理项目素材'},created_at:'2026-10-15T01:00:00Z'};
let signalControl;const firstControlReceived=new Promise(resolve=>signalControl=resolve);
const mutations=[],errors=[];let storedDraft='',heldControl=null,stream=null,streamCount=0;
const server=createServer(async(request,response)=>{
 const path=new URL(request.url,'http://fixture').pathname;
 const send=(value,status=200)=>{response.writeHead(status,{'content-type':'application/json'});response.end(JSON.stringify(value));};
 if(path==='/'){response.writeHead(200,{'content-type':'text/html'});response.end('<!doctype html><html lang="zh"><body><div id="app"></div><script type="module" src="/app.js"></script></body></html>');return;}
 if(path==='/app.js'){response.writeHead(200,{'content-type':'text/javascript'});response.end(bundle.outputFiles[0].text);return;}
 if(path==='/favicon.ico'){response.writeHead(204);response.end();return;}
 if(path==='/api/jobs/job1/events'){streamCount++;stream=response;response.writeHead(200,{'content-type':'text/event-stream'});response.write(`event: status\ndata: ${JSON.stringify(job)}\n\n`);return;}
 let body={};if(request.method!=='GET'){const chunks=[];for await(const chunk of request)chunks.push(chunk);body=JSON.parse(Buffer.concat(chunks).toString()||'{}');mutations.push({path,method:request.method,body});}
 if(path==='/api/model/status')return send({configured:true,provider:'fixture',model:'fixture',agent_engine:{name:'pi-agent-core',version:'1.2.0',available:true}});
 if(path==='/api/projects/p1/conversations')return send([{id:'c1',title:'运行中对话',draft:storedDraft,created_at:job.created_at}]);
 if(path==='/api/conversations/c1/messages'&&request.method==='GET')return send(messages);
 if(path==='/api/conversations/c1/draft'){storedDraft=body.draft;return send({draft:storedDraft});}
 if(path==='/api/conversations/c1/control'){
  const result={control_id:`ctrl-${mutations.length}`,job_id:'job1',user_message_id:`u-${messages.length}`};messages.push({id:result.user_message_id,role:'user',content:body.text,status:'completed',citations:[]});
  if(body.text==='请先处理客户档案'){heldControl=()=>send(result,202);signalControl();return;}
  if(body.text==='这个指令应失败')return send({detail:'隔离验收：暂时无法接收补充'},409);
  return send(result,202);
 }
 if(path==='/api/jobs/job1/cancel'){job.status='cancelled';stream?.write(`event: status\ndata: ${JSON.stringify(job)}\n\n`);stream?.end();return send(job);}
 errors.push(`${request.method} ${path}`);return send({detail:'Unknown isolated API'},404);
});
await new Promise(resolve=>server.listen(0,'127.0.0.1',resolve));
const browser=await chromium.launch({headless:true,channel:process.env.HS_BROWSER_CHANNEL||'chrome'});
const page=await browser.newPage({viewport:{width:1360,height:1000}});page.setDefaultTimeout(10000);page.on('pageerror',error=>errors.push(error.message));
try{
 await page.goto(`http://127.0.0.1:${server.address().port}/`);
 const composer=page.getByRole('textbox',{name:'向楼管家交代事项'});
 await page.getByRole('combobox',{name:'处理中消息的处理方式'}).waitFor();
 await composer.fill('请先处理客户档案');await page.getByRole('button',{name:'补充当前任务',exact:true}).click();
 await firstControlReceived;
 await composer.fill('保留这条尚未提交的新输入');
 assert.equal(streamCount,1);assert.equal(await page.getByRole('button',{name:'补充当前任务',exact:true}).isDisabled(),true);
 assert.ok(heldControl);const release=heldControl;heldControl=null;release();
 await page.getByText('已收到补充要求，会在当前工具处理结束后调整。',{exact:true}).waitFor();
 assert.equal(await composer.inputValue(),'保留这条尚未提交的新输入');
 await page.getByRole('combobox',{name:'处理中消息的处理方式'}).selectOption('follow_up');
 await page.getByRole('button',{name:'安排接着处理',exact:true}).click();
 await page.getByText('已加入后续事项，当前任务结束后接着处理。',{exact:true}).waitFor();
 assert.equal(await composer.inputValue(),'');
 const controls=mutations.filter(row=>row.path.endsWith('/control'));
 assert.deepEqual(controls.map(row=>row.body),[{kind:'steer',text:'请先处理客户档案'},{kind:'follow_up',text:'保留这条尚未提交的新输入'}]);
 assert.equal(mutations.filter(row=>row.path.endsWith('/messages')).length,0);assert.equal(streamCount,1);
 await composer.fill('这个指令应失败');await page.getByRole('button',{name:'安排接着处理',exact:true}).click();
 await page.getByRole('alert').filter({hasText:'隔离验收：暂时无法接收补充'}).waitFor();assert.equal(await composer.inputValue(),'这个指令应失败');
 await page.getByRole('button',{name:'停止',exact:true}).click();
 await page.getByRole('button',{name:'发送消息',exact:true}).waitFor();
 assert.equal(mutations.filter(row=>row.path.endsWith('/cancel')).length,1);assert.equal(streamCount,1);assert.deepEqual(errors,[]);
 console.log(JSON.stringify({passed:true,checks:['steer-mid-run','follow-up-mid-run','active-watcher-retained','newer-draft-preserved','failed-control-restores-draft','stop-remains-available'],controlCount:controls.length}));
}finally{heldControl?.();stream?.end();await browser.close();await new Promise(resolve=>server.close(resolve));}
