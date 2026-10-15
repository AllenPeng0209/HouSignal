/** Exercises real React discovery handoffs against isolated HTTP fixtures, without a live project. */
import assert from 'node:assert/strict';
import {createServer} from 'node:http';
import {createRequire} from 'node:module';
import {build} from 'esbuild';
import {resolve} from 'node:path';

const require=createRequire(import.meta.url);
const {chromium}=require(process.env.HS_PLAYWRIGHT_MODULE||'/Users/pengyanlun/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright');
const source=resolve('src/ResearchWorkspace.tsx'),hooks=resolve('src/hooks.tsx');
const bundle=await build({stdin:{contents:`
 import React from 'react';import {createRoot} from 'react-dom/client';
 import {ResearchWorkspace} from ${JSON.stringify(source)};import {AppContext} from ${JSON.stringify(hooks)};
 const root=createRoot(document.getElementById('app'));window.planningDrafts=[];
 window.renderRole=(role)=>root.render(<AppContext.Provider value={{project:{id:'bridge',name:'隔离策划验收',channel:'抖音'},session:{role},refresh:0,notify(){},navigate(){}}}><ResearchWorkspace onPlan={draft=>window.planningDrafts.push(draft)}/></AppContext.Provider>);
 window.renderRole('owner');`,resolveDir:process.cwd(),loader:'tsx'},bundle:true,write:false,format:'esm',platform:'browser',jsx:'automatic',loader:{'.css':'empty'}});
const at='2026-10-14T08:00:00Z',base='/api/projects/bridge/operations/research';
const makeItem=(id,title,extra={})=>({platform:'bilibili',content_id:id,title,url:`https://www.bilibili.com/video/${id}`,subject:'户型分析',job_id:`job-${id}`,metrics:{likes:10,views:null,comments:null,saves:null,shares:null},...extra});
const fresh=makeItem('BVFresh','未收藏的户型参考'),saved=makeItem('BVSaved','已经收藏的户型参考',{reference_id:'reference-saved'}),failing=makeItem('BVFail','保存失败的户型参考');
const makeReference=(id,item)=>({id,title:item.title,source_url:item.url,platform:'B站',source_snapshot:item,status:'saved',curation_state:'inbox',tags:[],note:'',research_notes:{topic_id:'layout'},analysis_snapshot:{},revision:1,observed_at:at});
const references=[makeReference('reference-saved',saved)];
const catalog={categories:[{id:'professional',label:'专业内容',purpose:'解释户型与生活场景',subjects:[{label:'户型点评',query:'户型分析'}],items:[fresh,saved,failing],collection_state:'ready',updated_at:at}],total:3,updated_at:at};
const mutations=[],errors=[];let releaseCapture,captureStarted;
const capturePending=new Promise(resolve=>captureStarted=resolve);
const server=createServer(async(request,response)=>{
 const path=new URL(request.url,'http://fixture').pathname;
 const send=(value,status=200)=>{response.writeHead(status,{'content-type':'application/json'});response.end(JSON.stringify(value));};
 if(path==='/'){response.writeHead(200,{'content-type':'text/html'});response.end('<!doctype html><html lang="zh"><body><div id="app"></div><script type="module" src="/app.js"></script></body></html>');return;}
 if(path==='/app.js'){response.writeHead(200,{'content-type':'text/javascript'});response.end(bundle.outputFiles[0].text);return;}
 if(!path.startsWith('/api/'))return send({detail:'Unknown fixture path'},404);
 let body={};if(request.method!=='GET'){const chunks=[];for await(const chunk of request)chunks.push(chunk);body=JSON.parse(Buffer.concat(chunks).toString()||'{}');mutations.push({path,method:request.method,body});}
 if(path===`${base}/catalog`)return send(catalog);
 if(path===`${base}/references`&&request.method==='GET')return send(references);
 if(path===`${base}/references`&&request.method==='POST'){
  if(body.job_id===failing.job_id)return send({detail:'隔离验收：收藏失败'},409);
  assert.deepEqual(body,{job_id:fresh.job_id,platform:'bilibili',content_id:'BVFresh'});
  releaseCapture=()=>{const reference=makeReference('reference-fresh',fresh);references.push(reference);send(reference,201);};captureStarted();return;
 }
 if(path===`${base}/references/reference-saved`&&request.method==='PATCH'){
  const index=references.findIndex(reference=>reference.id==='reference-saved');references[index]={...references[index],...body,revision:2};return send(references[index]);
 }
 if(path.endsWith('/outcomes'))return send({reference_id:'reference-saved',plans:[],publications:[],verified_live_count:0,coverage_note:''});
 if(path.endsWith('/capabilities'))return send({video_analysis:{ready:false,local_asr_ready:false,vision_ready:false,ffmpeg_ready:false},evidence_note:''});
 if(['/api/projects/bridge/assets','/api/projects/bridge/jobs'].includes(path))return send([]);
 errors.push(`${request.method} ${path}`);return send({detail:'Unknown API fixture'},404);
});
await new Promise(resolve=>server.listen(0,'127.0.0.1',resolve));
const browser=await chromium.launch({headless:true,channel:process.env.HS_BROWSER_CHANNEL||'chrome'});
const page=await browser.newPage({viewport:{width:1360,height:1000}});page.setDefaultTimeout(10000);
page.on('pageerror',error=>errors.push(error.message));
const card=item=>page.locator(`article[data-content-key="bilibili:${item.content_id}"]`);
try{
 await page.goto(`http://127.0.0.1:${server.address().port}/`);await card(fresh).getByRole('button',{name:'用这条策划',exact:true}).waitFor();
 await page.getByRole('button',{name:'户型点评',exact:true}).click();
 await card(fresh).getByRole('button',{name:'用这条策划',exact:true}).evaluate(button=>{button.click();button.click();});
 await capturePending;assert.equal(mutations.length,1);assert.equal(await page.evaluate(()=>window.planningDrafts.length),0);
 assert.equal(await card(saved).getByRole('button',{name:'用这条策划',exact:true}).isDisabled(),true);
 releaseCapture();await page.waitForFunction(()=>window.planningDrafts.length===1);
 const first=await page.evaluate(()=>window.planningDrafts[0]);assert.equal(first.reference_id,'reference-fresh');assert.equal(first.content_type,'professional');assert.equal(first.subject,'户型分析');assert.equal(first.title,fresh.title);
 await card(fresh).getByRole('button',{name:'用这条策划',exact:true}).click();await page.waitForFunction(()=>window.planningDrafts.length===2);assert.equal(mutations.filter(value=>value.method==='POST').length,1,'an immediate retry must reuse the captured reference');
 await card(saved).getByRole('button',{name:'用这条策划',exact:true}).click();await page.waitForFunction(()=>window.planningDrafts.length===3);assert.equal(mutations.filter(value=>value.method==='POST').length,1,'existing saved sources must not be captured again');
 await card(failing).getByRole('button',{name:'用这条策划',exact:true}).click();await page.getByRole('alert').filter({hasText:'隔离验收：收藏失败'}).waitFor();assert.equal(await page.evaluate(()=>window.planningDrafts.length),3);assert.equal(await card(failing).getByRole('button',{name:'用这条策划',exact:true}).isDisabled(),false);
 await page.getByRole('tab',{name:/我的参考/,exact:false}).click();
 const referenceCard=page.locator('.research-reference-card').filter({has:page.getByRole('heading',{name:saved.title,exact:true})});
 await referenceCard.getByRole('button',{name:'用这条策划',exact:true}).click();await page.waitForFunction(()=>window.planningDrafts.length===4);
 await referenceCard.getByRole('button',{name:'研究与拆解',exact:true}).click();
 const dialog=page.getByRole('dialog',{name:'参考研究与原片拆解',exact:true});
 await dialog.getByLabel('研究重点与复盘结论',{exact:true}).fill('先问家庭家务需求，再展示真实动线。');
 await dialog.getByRole('button',{name:'用这条策划',exact:true}).click();await page.waitForFunction(()=>window.planningDrafts.length===5);
 const last=await page.evaluate(()=>window.planningDrafts.at(-1));assert.equal(last.reference_id,'reference-saved');assert.match(last.brief,/先问家庭家务需求/);assert.equal(await dialog.count(),0);
 const patch=mutations.find(value=>value.method==='PATCH');assert.equal(patch.body.expected_revision,1);assert.equal(patch.body.note,'先问家庭家务需求，再展示真实动线。');
 await page.evaluate(()=>window.renderRole('sales'));await page.waitForFunction(()=>[...document.querySelectorAll('button')].filter(button=>button.textContent.includes('用这条策划')).every(button=>button.disabled));
 assert.deepEqual(errors,[]);console.log(JSON.stringify({passed:true,checks:['capture-before-handoff','double-click-guard','immediate-reference-reuse','saved-reference-reuse','capture-error-stays-in-discovery','library-handoff','saved-dialog-notes','read-only-controls'],mutationCount:mutations.length}));
}finally{await browser.close();await new Promise(resolve=>server.close(resolve));}
