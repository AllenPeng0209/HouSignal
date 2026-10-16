/** Reference-entry planning edits survive the same discovery/remount path as ordinary planning. */
import assert from 'node:assert/strict';
import {createServer} from 'node:http';
import {createRequire} from 'node:module';
import {build} from 'esbuild';

const require=createRequire(import.meta.url);
const {chromium}=require(process.env.HS_PLAYWRIGHT_MODULE||'playwright');
const source=`import React,{useState} from 'react';
import {createRoot} from 'react-dom/client';
import {AppContext} from './src/hooks';
import {PlanningPanel} from './src/PlanningPanel';
function Fixture(){
 const [show,setShow]=useState(false),[draft,setDraft]=useState(null);
 const reference=()=>{setDraft({title:'参考 A',angle:'参考开场角度',brief:'参考笔记',topic_id:'tour',reference_id:'ref-a',start_mode:'reference',target_seconds:30});setShow(true);};
 return <AppContext.Provider value={{project:{id:'fixture',channel:'抖音',audience:'改善家庭'},session:{user:{id:'user-fixture'},organization:{id:'org-fixture'},role:'owner'},refresh:0,notify:()=>{},reload:async()=>{}}}>
  <button onClick={reference}>从参考 A 开始</button>
  {show?<PlanningPanel initialDraft={draft} onDraftConsumed={()=>setDraft(null)} onGenerate={()=>{}} onDiscover={()=>setShow(false)}/>:<button onClick={()=>setShow(true)}>返回策划工作区</button>}
 </AppContext.Provider>;
}
createRoot(document.getElementById('app')).render(<Fixture/>);`;
const bundle=await build({stdin:{contents:source,resolveDir:process.cwd(),loader:'tsx'},bundle:true,write:false,format:'esm',platform:'browser',jsx:'automatic',loader:{'.css':'empty'}});
const reference={id:'ref-a',title:'参考 A',platform:'抖音',source_url:'https://example.test/reference-a',curation_state:'inbox',research_notes:{},analysis_snapshot:{},revision:1,observed_at:'2026-10-17T00:00:00Z',tags:[]};
const errors=[];
const server=createServer((request,response)=>{
 const path=new URL(request.url,'http://fixture').pathname;
 if(path==='/favicon.ico'){response.writeHead(204);response.end();return;}
 if(path==='/'){response.writeHead(200,{'content-type':'text/html'});response.end('<!doctype html><html lang="zh"><body><div id="app"></div><script type="module" src="/app.js"></script></body></html>');return;}
 if(path==='/app.js'){response.writeHead(200,{'content-type':'text/javascript'});response.end(bundle.outputFiles[0].text);return;}
 let data=[];
 if(path.endsWith('/research/references'))data=[reference];
 else if(path.endsWith('/topics'))data=[{id:'tour',title:'实拍看房',eligible:true}];
 else if(path.endsWith('/skills'))data=[{id:'content-strategy',status:'ready'},{id:'viral-topic-matrix',status:'ready'},{id:'housing-video-plan',status:'ready'}];
 else if(!['/operations/plans','/operations/accounts','/operations/opportunities','/jobs'].some(suffix=>path.endsWith(suffix)))errors.push(`${request.method} ${path}`);
 response.writeHead(200,{'content-type':'application/json'});response.end(JSON.stringify(data));
});
await new Promise(resolve=>server.listen(0,'127.0.0.1',resolve));
const browser=await chromium.launch({headless:true,channel:process.env.HS_BROWSER_CHANNEL||'chrome'});
const page=await browser.newPage();page.setDefaultTimeout(10000);page.on('pageerror',error=>errors.push(error.message));
try{
 await page.goto(`http://127.0.0.1:${server.address().port}/`);
 // This entry is used by DiscoveryPanel's "用这条策划", and consumes its incoming draft once.
 await page.getByRole('button',{name:'从参考 A 开始',exact:true}).click();
 const idea=page.getByRole('textbox',{name:'策划想法与补充要求',exact:true});
 await idea.fill('保留参考 A 的开场，我补写的内容必须能接着编辑。');
 await page.getByRole('button',{name:'去发现',exact:true}).click();
 await page.getByRole('button',{name:'返回策划工作区',exact:true}).click();
 assert.equal(await idea.inputValue(),'保留参考 A 的开场，我补写的内容必须能接着编辑。','Reference-entry draft disappeared when the planner remounted without initialDraft');
 assert.equal(await page.getByText('项目配置已更新，请选择保留本地策划，或使用当前项目配置。',{exact:true}).count(),0,'The same draft must not become a false server conflict after remount');
 await page.reload();
 await page.getByRole('button',{name:'返回策划工作区',exact:true}).click();
 assert.equal(await idea.inputValue(),'保留参考 A 的开场，我补写的内容必须能接着编辑。','Reference-entry draft disappeared after refresh');
 // A new incoming reference must retain the existing human edits rather than replace them.
 await page.getByRole('button',{name:'从参考 A 开始',exact:true}).click();
 assert.equal(await idea.inputValue(),'保留参考 A 的开场，我补写的内容必须能接着编辑。');
 assert.deepEqual(errors,[]);
 console.log(JSON.stringify({passed:true,checks:['reference-entry-edit','discovery-return-restoration','no-false-config-conflict','refresh-restoration','incoming-reference-preserves-human-intent']}));
}finally{await browser.close();await new Promise(resolve=>server.close(resolve));}
