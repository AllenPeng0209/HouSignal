/** Real draft hook in two Chromium documents sharing one localStorage origin. */
import assert from 'node:assert/strict';
import {createServer} from 'node:http';
import {createRequire} from 'node:module';
import {resolve} from 'node:path';
import {build} from 'esbuild';
const require=createRequire(import.meta.url),{chromium}=require(process.env.HS_PLAYWRIGHT_MODULE||'playwright');
const source=`import React from 'react';import {createRoot} from 'react-dom/client';import {useWorkspaceDraft} from './src/workspace-drafts';import {WorkspaceDraftConflict} from './src/WorkspaceDraftConflict';
function Harness(){const draft=useWorkspaceDraft({scope:{userId:'alice',organizationId:'org',projectId:'project'},kind:'studio',resourceId:'v1',serverValue:{script:'服务器原稿'}});return <><textarea aria-label="脚本" value={draft.value?.script||''} disabled={draft.conflict} onChange={e=>draft.setValue({script:e.target.value})}/><p role="status">{draft.conflictKind||draft.status}</p><WorkspaceDraftConflict draft={draft}/></>;};createRoot(document.getElementById('root')).render(<Harness/>);`;
const result=await build({stdin:{contents:source,resolveDir:process.cwd(),loader:'tsx'},bundle:true,write:false,format:'iife',platform:'browser',jsx:'automatic'}),bundle=result.outputFiles[0].text;
const server=createServer((request,response)=>{response.setHeader('Content-Type',request.url==='/fixture.js'?'application/javascript':'text/html');response.end(request.url==='/fixture.js'?bundle:'<div id="root"></div><script src="/fixture.js"></script>');});await new Promise(resolve=>server.listen(0,'127.0.0.1',resolve));
const url=`http://127.0.0.1:${server.address().port}`,browser=await chromium.launch({headless:true,channel:process.env.HS_BROWSER_CHANNEL||'chrome'}),context=await browser.newContext(),a=await context.newPage(),b=await context.newPage();a.setDefaultTimeout(3000);b.setDefaultTimeout(3000);
try{
 await Promise.all([a.goto(url),b.goto(url)]);await a.getByRole('textbox',{name:'脚本',exact:true}).fill('A 窗口的未提交稿');
 // A clean second window sees the recoverable branch without replacing its server text.
 await b.getByRole('status').filter({hasText:'concurrent'}).waitFor();assert.equal(await b.getByRole('textbox',{name:'脚本',exact:true}).inputValue(),'服务器原稿');await b.getByRole('button',{name:'保留本窗口草稿',exact:true}).click();await b.getByRole('textbox',{name:'脚本',exact:true}).fill('B 窗口的未提交稿');
 await a.getByRole('status').filter({hasText:'concurrent'}).waitFor();assert.equal(await a.getByRole('textbox',{name:'脚本',exact:true}).inputValue(),'A 窗口的未提交稿');await a.getByText('B 窗口的未提交稿',{exact:true}).waitFor();
 await a.reload();assert.equal(await a.getByRole('textbox',{name:'脚本',exact:true}).inputValue(),'A 窗口的未提交稿');await a.getByRole('status').filter({hasText:'concurrent'}).waitFor();await a.getByRole('button',{name:'保留本窗口草稿',exact:true}).click();await a.getByRole('textbox',{name:'脚本',exact:true}).fill('A 窗口继续修改');
 await b.getByRole('status').filter({hasText:'concurrent'}).waitFor();await b.reload();assert.equal(await b.getByRole('textbox',{name:'脚本',exact:true}).inputValue(),'B 窗口的未提交稿');await b.getByText('A 窗口继续修改',{exact:true}).waitFor();await b.getByRole('button',{name:'使用这份草稿',exact:true}).click();assert.equal(await b.getByRole('textbox',{name:'脚本',exact:true}).inputValue(),'A 窗口继续修改');
 // window.open copies sessionStorage; the new document must still get a unique writer branch.
 const popupPromise=a.waitForEvent('popup');await a.evaluate(url=>window.open(url,'_blank'),url);const cloned=await popupPromise;await cloned.waitForLoadState();if(await cloned.getByRole('textbox',{name:'脚本',exact:true}).isDisabled())await cloned.getByRole('button',{name:'保留本窗口草稿',exact:true}).click();await cloned.getByRole('textbox',{name:'脚本',exact:true}).fill('复制标签页的独立稿');await a.reload();assert.equal(await a.getByRole('textbox',{name:'脚本',exact:true}).inputValue(),'A 窗口继续修改');
 console.log(JSON.stringify({passed:true,checks:['two active windows retain both texts','storage events surface concurrent conflicts','refresh recovers own branch','explicit alternative selection','window.open cloned sessionStorage keeps independent branches']}));
}finally{await browser.close();await new Promise(resolve=>server.close(resolve));}
