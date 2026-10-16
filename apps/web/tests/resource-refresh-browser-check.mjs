/** Verify real hook cache semantics across an HTTP permission/error boundary. */
import assert from 'node:assert/strict';
import {createRequire} from 'node:module';
import {resolve} from 'node:path';
import {build} from 'esbuild';
const require=createRequire(import.meta.url),{chromium}=require(process.env.HS_PLAYWRIGHT_MODULE||'playwright');
const source=`import React from 'react';import {createRoot} from 'react-dom/client';import {AppContext,useResource} from './hooks';function Surface(){const value=useResource('/api/private');return <><output id="result">{JSON.stringify({data:value.data,error:value.error,backgroundError:value.backgroundError,loading:value.loading})}</output><button onClick={value.retry}>Refresh</button></>}createRoot(document.getElementById('root')).render(<AppContext.Provider value={{refresh:0}}><Surface/></AppContext.Provider>);`;
const bundle=await build({stdin:{contents:source,loader:'tsx',resolveDir:resolve('src')},write:false,bundle:true,format:'iife',platform:'browser',jsx:'automatic'});
const browser=await chromium.launch({headless:true,channel:process.env.HS_BROWSER_CHANNEL||'chrome'}),checks=[];
try{
 for(const status of [403,404,500]){
  const context=await browser.newContext(),page=await context.newPage();let reads=0;
  await context.route('**/*',route=>{const path=new URL(route.request().url()).pathname;if(path==='/')return route.fulfill({contentType:'text/html',body:'<div id="root"></div><script src="/app.js"></script>'});if(path==='/app.js')return route.fulfill({contentType:'text/javascript',body:bundle.outputFiles[0].text});if(path==='/api/private'){reads++;return route.fulfill({status:reads===1?200:status,contentType:'application/json',body:JSON.stringify(reads===1?{approval_valid:true}:{detail:'resource unavailable'})});}return route.abort();});
  await page.goto('http://resource-check.invalid/');await page.waitForFunction(()=>JSON.parse(document.querySelector('#result').textContent).data?.approval_valid===true);
  await page.getByRole('button',{name:'Refresh',exact:true}).click();await page.waitForFunction(()=>{const x=JSON.parse(document.querySelector('#result').textContent);return Boolean(x.error||x.backgroundError);});
  const result=JSON.parse(await page.locator('#result').innerText());
  if(status===403||status===404){assert.equal(result.data,null,`${status} must invalidate inaccessible cached data`);assert.equal(result.error,'resource unavailable');assert.equal(result.backgroundError,'');}
  else{assert.deepEqual(result.data,{approval_valid:true});assert.equal(result.error,'');assert.equal(result.backgroundError,'resource unavailable');}
  checks.push({status,passed:true});await context.close();
 }
 console.log(JSON.stringify({passed:true,checks}));
}finally{await browser.close();}
