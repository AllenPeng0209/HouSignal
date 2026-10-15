/** The real App resolves a receipt's job source, follows progress, and downloads its output. */
import assert from 'node:assert/strict';
import {createServer} from 'node:http';
import {createRequire} from 'node:module';
import {build} from 'esbuild';
import {resolve} from 'node:path';
const require=createRequire(import.meta.url);
const {chromium}=require(process.env.HS_PLAYWRIGHT_MODULE||'/Users/pengyanlun/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright');
const bundle=await build({stdin:{contents:`import React from 'react';import {createRoot} from 'react-dom/client';import App from ${JSON.stringify(resolve('src/App.tsx'))};createRoot(document.getElementById('app')).render(<App/>);`,resolveDir:process.cwd(),loader:'tsx'},bundle:true,write:false,format:'esm',platform:'browser',jsx:'automatic',loader:{'.css':'empty'},define:{'import.meta.env.DEV':'false'}});
const at='2026-10-15T01:00:00Z',project={id:'p1',name:'任务详情隔离验收',city:'上海',channel:'抖音',property_type:'住宅',is_test:true,completeness:{percent:100,completed:7,total:7,items:[]}};
const artifact='/api/projects/p1/operations/skills/artifacts/j1/script.txt';
const job={id:'j1',kind:'content_skill',status:'queued',progress:0,phase:'等待制作',created_at:at,result:{}};
const messages=[{id:'u1',role:'user',content:'制作项目视频',status:'completed',citations:[]},{id:'a1',role:'assistant',content:'已创建制作任务，请查看进度。',status:'completed',citations:[],actions:[{id:'a1',name:'production_start',label:'视频制作任务',status:'queued',summary:'已创建视频任务',sources:[{kind:'job',id:'j1',label:'查看制作任务'}]}]}];
const errors=[],requests=[];let stream=null,streamCount=0;
const server=createServer(async(request,response)=>{
 const path=new URL(request.url,'http://fixture').pathname;requests.push(path);
 const send=(value,status=200)=>{response.writeHead(status,{'content-type':'application/json'});response.end(JSON.stringify(value));};
 if(path==='/'){response.writeHead(200,{'content-type':'text/html'});response.end('<!doctype html><html lang="zh"><body><div id="app"></div><script type="module" src="/app.js"></script></body></html>');return;}
 if(path==='/app.js'){response.writeHead(200,{'content-type':'text/javascript'});response.end(bundle.outputFiles[0].text);return;}
 if(path==='/favicon.ico'){response.writeHead(204);response.end();return;}
 if(path==='/api/auth/me')return send({user:{id:'u1',name:'验收经纪人',email:'fixture@example.test'},organization:{id:'o1',name:'隔离组织'},role:'owner'});
 if(path==='/api/projects')return send([project]);
 if(path==='/api/projects/p1/overview')return send({project,metrics:{published:0,inquiries:0,qualified:0,appointments:0,visits:0,views:null,unknown_rate:null},pending_inquiries:[],content_versions:[],publications:[],proposals:[]});
 if(path==='/api/projects/p1/reports')return send({metrics:{views:null,likes:null,comments:null,saves:null,inquiries:null,qualified:null,window_complete:false,observed_publications:0,total_publications:0},publications:[],proposals:[],window:'D7'});
 if(path==='/api/model/status')return send({configured:true,provider:'fixture',model:'fixture'});
 if(path==='/api/projects/p1/conversations')return send([{id:'c1',title:'制作任务对话',draft:'',created_at:at}]);
 if(path==='/api/conversations/c1/messages')return send(messages);
 if(path==='/api/jobs/j1')return send(job);
 if(path==='/api/jobs/j1/events'){streamCount++;stream=response;response.writeHead(200,{'content-type':'text/event-stream'});response.write(`event: status\ndata: ${JSON.stringify(job)}\n\n`);return;}
 if(path===artifact){response.writeHead(200,{'content-type':'text/plain','content-disposition':'attachment; filename="script.txt"'});response.end('Isolated production output');return;}
 if(['/api/projects/p1/topics','/api/projects/p1/contents','/api/projects/p1/assets','/api/projects/p1/publications','/api/projects/p1/jobs','/api/projects/p1/operations/references','/api/projects/p1/operations/plans','/api/projects/p1/operations/research/references'].includes(path))return send([]);
 if(path.endsWith('/operations/research/catalog'))return send({categories:[],total:0,updated_at:at});
 if(path.endsWith('/capabilities'))return send({});
 errors.push(`${request.method} ${path}`);return send({detail:'Unknown isolated app API'},404);
});
await new Promise(resolve=>server.listen(0,'127.0.0.1',resolve));
const browser=await chromium.launch({headless:true,channel:process.env.HS_BROWSER_CHANNEL||'chrome'});
const page=await browser.newPage({viewport:{width:1360,height:1000}});page.setDefaultTimeout(8000);page.on('pageerror',error=>errors.push(error.message));
try{
 await page.goto(`http://127.0.0.1:${server.address().port}/`);
 await page.getByRole('navigation',{name:'楼管家',exact:true}).getByRole('button',{name:'楼管家',exact:true}).click();
 await page.getByRole('button',{name:'查看制作任务',exact:true}).click();
 const dialog=page.getByRole('dialog',{name:'查看制作任务',exact:true});await dialog.getByText('任务已创建，等待制作。完成后会在这里显示实际结果。',{exact:true}).waitFor();
 assert.equal(await dialog.locator('a[download]').count(),0);assert.equal(streamCount,1);
 job.status='succeeded';job.progress=1;job.phase='制作已完成';job.result={response:'项目脚本已保存',content_version_id:'content1',artifacts:[{name:'下载项目脚本',url:artifact,mime_type:'text/plain'},{name:'越界文件',url:'/api/projects/other/operations/skills/artifacts/j1/secret.txt'}]};
 stream.write(`event: status\ndata: ${JSON.stringify(job)}\n\n`);stream.end();
 const link=dialog.getByRole('link',{name:'下载项目脚本',exact:true});await link.waitFor();assert.equal(await link.getAttribute('href'),artifact);assert.equal(await dialog.locator('a[download]').count(),1);
 const downloadPromise=page.waitForEvent('download');await link.click();const download=await downloadPromise;assert.equal(download.suggestedFilename(),'script.txt');assert.equal(await download.failure(),null);
 await dialog.getByRole('button',{name:'在内容工作台查看',exact:true}).click();
 await page.getByRole('heading',{name:'内容工作台',exact:true}).waitFor();assert.equal(await dialog.count(),0);
 assert.equal(streamCount,1);assert.ok(requests.includes('/api/jobs/j1'));assert.deepEqual(errors,[]);
 console.log(JSON.stringify({passed:true,checks:['real-app-job-receipt-navigation','queued-does-not-offer-file','sse-completion-updates-detail','private-artifact-download','cross-project-artifact-rejected','real-content-opens-studio'],streamCount}));
}finally{stream?.end();await browser.close();await new Promise(resolve=>server.close(resolve));}
