import {before,after,test} from 'node:test';
import assert from 'node:assert/strict';
import {existsSync} from 'node:fs';
import {mkdtemp,rm,writeFile} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import {join,resolve} from 'node:path';
import {pathToFileURL} from 'node:url';
import {build} from 'esbuild';
import React from 'react';
import {renderToStaticMarkup} from 'react-dom/server';
let ui,dir;
before(async()=>{
 dir=await mkdtemp(join(tmpdir(),'studio-prepare-'));
 const path=resolve('src/StudioPublishPreparation.tsx');
 const result=await build({entryPoints:[path],bundle:true,write:false,format:'esm',platform:'node',jsx:'automatic',external:['react','react-dom','react/jsx-runtime'],loader:{'.css':'empty'},plugins:[{name:'loaded-project-records',setup(b){
  if(!existsSync(path))b.onResolve({filter:/StudioPublishPreparation\.tsx$/},()=>({path:'missing',namespace:'fixture'}));
  b.onResolve({filter:/^\.\/hooks$/},()=>({path:'hooks',namespace:'fixture'}));
  b.onLoad({filter:/.*/,namespace:'fixture'},({path})=>({loader:'js',contents:path==='missing'?`export const StudioPublishPreparation=()=>null,matchingStudioPreparation=()=>null;`:`export const useApp=()=>globalThis.preparationApp;export const useResource=()=>({data:globalThis.preparationJobs,loading:false,error:'',retry(){}});export const useMutation=()=>({error:'',busy:false,setError(){},run:async()=>{}});export const useJob=()=>({busy:false,error:'',job:null,percent:0,statusText:'',wait:async()=>{},setError(){},setJob(){},cancel(){}});`}));
 }}]});
 const bundle=result.outputFiles[0].text.replaceAll('from "react"',`from ${JSON.stringify(pathToFileURL(resolve('node_modules/react/index.js')).href)}`).replaceAll('from "react/jsx-runtime"',`from ${JSON.stringify(pathToFileURL(resolve('node_modules/react/jsx-runtime.js')).href)}`);
 const target=join(dir,'ui.mjs');await writeFile(target,bundle);ui=await import(pathToFileURL(target).href);
});
after(async()=>{delete globalThis.preparationApp;delete globalThis.preparationJobs;await rm(dir,{recursive:true,force:true});});
const content={id:'content-1',channel:'抖音',status:'approved',approval_valid:true,release_id:'release-1',render_asset_id:'render-1',version:1,topic:'空间示意导览'};
const job={id:'prepare-1',kind:'platform_prepare',status:'succeeded',result:{platform:'douyin',content_version_id:'content-1',release_id:'release-1',dry_run:true,publication_state:'not_published',publication_preview:{title:'329㎡空间示意导览',body:'仅作空间示意，预约需确认。',draft_only:false,content_format:'video'},package:{name:'publish-package.zip',download_url:'/api/projects/p/operations/platform-accounts/tasks/prepare-1/package'},plan:{steps:['核对同一审核版本','准备后等待实际回执']}}};
function setup(role='owner',jobs=[]){globalThis.preparationApp={project:{id:'p',is_test:true},session:{role},navigate(){}};globalThis.preparationJobs=jobs;}
function markup(value=content,releaseId='release-1'){return renderToStaticMarkup(React.createElement(ui.StudioPublishPreparation,{content:value,releaseId,disabled:false}));}
function button(html){return [...html.matchAll(/<button\b[^>]*>[\s\S]*?<\/button>/g)].map(m=>m[0]).find(m=>m.includes('<span>准备平台发布包</span>'))||'';}
// Catches forcing an account login before reversible local preparation or exposing actual publication actions in the example flow.
test('the same reviewed content can prepare an offline platform package directly in Studio',()=>{
 setup();const html=markup();assert.ok(button(html));assert.doesNotMatch(button(html),/disabled=""/);assert.match(html,/抖音/);assert.doesNotMatch(html,/确认并提交平台|请先登录/);
});
test('unreviewed and read-only content cannot prepare a platform package',()=>{
 setup();assert.match(button(markup({...content,status:'rendered',approval_valid:false},'')),/disabled=""/);
 setup('sales');assert.match(button(markup()),/disabled=""/);
});
// Catches restoring a ZIP from another release/content/channel or labeling preparation as published.
test('restoring preparation requires the exact reviewed version and preserves the actual unpublished state',()=>{
 setup('owner',[job]);const html=markup();assert.match(html,/329㎡空间示意导览/);assert.match(html,/仅作空间示意/);assert.match(html,/下载 publish-package.zip/);assert.match(html,/尚未发布/);
 assert.equal(ui.matchingStudioPreparation({...job,result:{...job.result,release_id:'old-release'}},content,'release-1','douyin'),false);
 assert.equal(ui.matchingStudioPreparation({...job,result:{...job.result,content_version_id:'other-content'}},content,'release-1','douyin'),false);
 assert.equal(ui.matchingStudioPreparation({...job,result:{...job.result,platform:'xiaohongshu'}},content,'release-1','douyin'),false);
});
