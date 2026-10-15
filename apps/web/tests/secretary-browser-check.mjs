/** Exercises the real secretary/CRM UI against isolated HTTP fixtures. */
import assert from 'node:assert/strict';
import {createServer} from 'node:http';
import {createRequire} from 'node:module';
import {build} from 'esbuild';
import {resolve} from 'node:path';
const require=createRequire(import.meta.url);
const {chromium}=require(process.env.HS_PLAYWRIGHT_MODULE||'/Users/pengyanlun/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright');
const bundle=await build({stdin:{contents:`
 import React,{useState} from 'react';import {createRoot} from 'react-dom/client';
 import {Assistant} from ${JSON.stringify(resolve('src/Assistant.tsx'))};import {LeadDialog} from ${JSON.stringify(resolve('src/Leads.tsx'))};import {AppContext} from ${JSON.stringify(resolve('src/hooks.tsx'))};
 function Fixture(){const [page,setPage]=useState('lead'),[refresh,setRefresh]=useState(0);window.openFixturePage=setPage;return <AppContext.Provider value={{project:{id:'p1',name:'隔离秘书验收',completeness:{completed:1,total:7,percent:14}},session:{role:'owner'},canSales:true,canEdit:true,canReview:true,canManage:true,refresh,reload:async()=>{setRefresh(value=>value+1);},notify(){},navigate:setPage,citation(){},openReadiness(){}}}>{page==='lead'?<LeadDialog id="l1" onClose={()=>{}}/>:<Assistant/>}</AppContext.Provider>;}createRoot(document.getElementById('app')).render(<Fixture/>);`,resolveDir:process.cwd(),loader:'tsx'},bundle:true,write:false,format:'esm',platform:'browser',jsx:'automatic',loader:{'.css':'empty'}});
const profile={background:'企业经营者',financial_situation:'客户自报现金购房',budget:'3000万元',preferences:'安静、江景',family:'三口之家',requirements:'四房',notes:'下午方便',updated_at:'2026-10-15T01:00:00Z'};
const lead={id:'l1',name:'彭先生',stage:'captured',need:'改善自住',next_action:'确认带看地点',source_confidence:'self_reported'};
const viewings=[],mutations=[],errors=[];let draft='';
const server=createServer(async(request,response)=>{
 const path=new URL(request.url,'http://fixture').pathname;
 const send=(value,status=200)=>{response.writeHead(status,{'content-type':'application/json'});response.end(JSON.stringify(value));};
 if(path==='/'){response.writeHead(200,{'content-type':'text/html'});response.end('<!doctype html><html lang="zh"><body><div id="app"></div><script type="module" src="/app.js"></script></body></html>');return;}
 if(path==='/app.js'){response.writeHead(200,{'content-type':'text/javascript'});response.end(bundle.outputFiles[0].text);return;}
 if(path==='/favicon.ico'){response.writeHead(204);response.end();return;}
 let body={};if(request.method!=='GET'){const chunks=[];for await(const chunk of request)chunks.push(chunk);body=JSON.parse(Buffer.concat(chunks).toString()||'{}');mutations.push({method:request.method,path,body});}
 if(path==='/api/projects/p1/leads/l1')return send({lead,profile,inquiry:{id:'i1',name:'彭先生',contact:'未提供'},followups:[],viewings});
 if(path==='/api/projects/p1/leads/l1/profile'&&request.method==='PATCH'){Object.assign(profile,body,{updated_at:new Date().toISOString()});return send(profile);}
 if(path==='/api/projects/p1/viewings'&&request.method==='POST'){const viewing={...body,id:'v1',timezone:'Asia/Shanghai',missing_details:['time','location'],clarification:'请补充具体时间和会面地点'};viewings.push(viewing);return send(viewing);}
 if(path==='/api/projects/p1/viewings/v1'&&request.method==='PATCH'){Object.assign(viewings[0],body);return send(viewings[0]);}
 if(path==='/api/model/status')return send({configured:true,provider:'fixture',model:'fixture'});
 if(path==='/api/projects/p1/conversations')return send([{id:'c1',title:'隔离秘书对话',draft,created_at:'2026-10-15T01:00:00Z'}]);
 if(path==='/api/conversations/c1/messages'&&request.method==='GET')return send([]);
 if(path==='/api/conversations/c1/draft'&&request.method==='PATCH'){draft=body.draft;return send({id:'c1',draft});}
 errors.push(`${request.method} ${path}`);return send({detail:'Unknown isolated fixture API'},404);
});
await new Promise(resolve=>server.listen(0,'127.0.0.1',resolve));
const browser=await chromium.launch({headless:true,channel:process.env.HS_BROWSER_CHANNEL||'chrome'});
const page=await browser.newPage({viewport:{width:1360,height:1000}});page.setDefaultTimeout(10000);
page.on('pageerror',error=>errors.push(error.message));
try{
 await page.goto(`http://127.0.0.1:${server.address().port}/`);
 await page.getByRole('button',{name:'编辑档案',exact:true}).click();
 await page.getByLabel('购房预算',{exact:true}).fill('3500万元');
 await page.getByRole('button',{name:'保存客户档案',exact:true}).click();
 await page.getByRole('button',{name:'编辑档案',exact:true}).waitFor();
 const profileWrite=mutations.find(row=>row.path.endsWith('/profile'));
 assert.deepEqual(profileWrite.body,{budget:'3500万元',source_note:'经纪人人工录入'});
 assert.equal(profile.background,'企业经营者');
 await page.getByRole('button',{name:'安排带看',exact:true}).click();
 await page.getByLabel('日期 *',{exact:true}).fill('2026-10-21');
 await page.getByLabel('大致时段',{exact:true}).fill('下午');
 await page.getByRole('button',{name:'保存安排',exact:true}).click();
 await page.getByText('10月21日（周三）下午',{exact:true}).waitFor();
 const viewingWrite=mutations.find(row=>row.path.endsWith('/viewings'));
 assert.equal(viewingWrite.body.starts_at,null);assert.equal(viewingWrite.body.status,'pending_details');assert.equal(viewingWrite.body.lead_id,'l1');
 await page.getByText('地点待补充',{exact:true}).waitFor();
 await page.getByRole('button',{name:'补充 / 修改',exact:true}).click();
 await page.getByLabel('具体时间',{exact:false}).fill('15:30');
 await page.getByLabel('会面地点',{exact:true}).fill('项目会所接待厅');
 await page.getByRole('button',{name:'保存安排',exact:true}).click();
 await page.getByText('10月21日（周三）15:30',{exact:true}).waitFor();
 assert.equal(viewings[0].starts_at,'2026-10-21T15:30:00+08:00');assert.equal(viewings[0].status,'scheduled');
 await page.getByRole('button',{name:'让楼管家跟进',exact:true}).click();
 const composer=page.getByRole('textbox',{name:'向楼管家交代事项',exact:true});
 await composer.waitFor();await page.waitForFunction(()=>document.getElementById('agent-input')?.value.includes('客户 ID：l1'));
 assert.match(await composer.inputValue(),/彭先生/);
 assert.equal(mutations.filter(row=>row.path.endsWith('/messages')).length,0);
 await composer.fill('');await page.getByRole('button',{name:'安排会面带看',exact:false}).click();
 assert.match(await composer.inputValue(),/【客户称呼】/);
 assert.equal(mutations.filter(row=>row.path.endsWith('/messages')).length,0,'example cards must only prepare a draft');
 assert.deepEqual(errors,[]);
 console.log(JSON.stringify({passed:true,checks:['profile-patch-only-changed-fields','persisted-client-background-preserved','incomplete-viewing-has-no-guessed-hour','exact-time-and-location-update','lead-context-draft-handoff','example-does-not-send'],businessMutationCount:mutations.filter(row=>!row.path.endsWith('/draft')).length}));
}finally{await browser.close();await new Promise(resolve=>server.close(resolve));}
