import {test,before,after} from 'node:test';
import assert from 'node:assert/strict';
import {existsSync} from 'node:fs';
import {mkdtemp,rm,writeFile} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import {join,resolve} from 'node:path';
import {pathToFileURL} from 'node:url';
import {build} from 'esbuild';
import React from 'react';
import {renderToStaticMarkup} from 'react-dom/server';
let ui,directory;
before(async()=>{
 directory=await mkdtemp(join(tmpdir(),'housignal-production-job-'));
 const result=await build({stdin:{contents:`export * from './ProductionJobDialog';`,resolveDir:resolve('src'),loader:'tsx'},write:false,bundle:true,format:'esm',platform:'node',jsx:'automatic',external:['react','react-dom','react/jsx-runtime'],loader:{'.css':'empty'},plugins:[{name:'job-resource-fixture',setup(builder){
 builder.onResolve({filter:/^\.\/ProductionJobDialog$/},()=>existsSync(resolve('src/ProductionJobDialog.tsx'))?null:{path:'missing',namespace:'fixture'});
 builder.onResolve({filter:/^\.\/hooks$/},()=>({path:'job-hooks',namespace:'fixture'}));
 builder.onLoad({filter:/.*/,namespace:'fixture'},({path})=>({loader:'js',contents:path==='missing'?'export const ProductionJobDialog=()=>null;':`export const useApp=()=>globalThis.productionJobApp;export const useResource=()=>({data:globalThis.productionJobData,loading:false,error:'',retry(){},setData(){}});export const useMutation=()=>({});export const useJob=()=>({});`}));
 }}]});
 const bundled=result.outputFiles[0].text.replaceAll('from "react"',`from ${JSON.stringify(pathToFileURL(resolve('node_modules/react/index.js')).href)}`).replaceAll('from "react/jsx-runtime"',`from ${JSON.stringify(pathToFileURL(resolve('node_modules/react/jsx-runtime.js')).href)}`);
 const target=join(directory,'ui.mjs');await writeFile(target,bundled);ui=await import(pathToFileURL(target).href);
});
after(async()=>{delete globalThis.productionJobApp;delete globalThis.productionJobData;if(directory)await rm(directory,{recursive:true,force:true});});
function renderJob(status,result={},error=''){
 globalThis.productionJobApp={project:{id:'p1'},navigate(){}};
 globalThis.productionJobData={id:'j1',kind:'content_skill',status,progress:status==='succeeded'?1:0.2,phase:status==='queued'?'等待制作':status==='failed'?'制作失败':'已完成',error,result,created_at:'2026-10-15T01:00:00Z'};
 return renderToStaticMarkup(React.createElement(ui.ProductionJobDialog,{id:'j1',onClose(){}}));
}
const artifact='/api/projects/p1/operations/skills/artifacts/j1/final.mp4';
test('a queued task remains queued and does not expose premature artifact downloads',()=>{
 const html=renderJob('queued',{artifacts:[{name:'未完成视频',url:artifact,mime_type:'video/mp4'}]});
 assert.match(html,/等待制作/);assert.doesNotMatch(html,/download=|<video|未完成视频/);
});
test('completed task downloads are limited to this project and job native artifact paths',()=>{
 const html=renderJob('succeeded',{artifacts:[
 {name:'真实成片',url:artifact,mime_type:'video/mp4'},
 {name:'其他项目',url:'/api/projects/other/operations/skills/artifacts/j1/secret.mp4'},
 {name:'其他任务',url:'/api/projects/p1/operations/skills/artifacts/j2/secret.mp4'},
 {name:'外部链接',url:'https://example.com/video.mp4'},
 {name:'脚本链接',url:'javascript:alert(1)'},
 {name:'路径越界',url:'/api/projects/p1/operations/skills/artifacts/j1/%2e%2e%2fsecret.mp4'},
 ]});
 assert.match(html,/href="\/api\/projects\/p1\/operations\/skills\/artifacts\/j1\/final.mp4"/);assert.match(html,/<video[^>]*controls/);
 assert.equal([...html.matchAll(/download=/g)].length,1);assert.doesNotMatch(html,/其他项目|其他任务|外部链接|脚本链接|路径越界/);
});
test('failed tasks show the actual error while successful content tasks can open Studio',()=>{
 const failed=renderJob('failed',{artifacts:[{name:'旧视频',url:artifact}]},'配音服务返回失败');
 assert.match(failed,/配音服务返回失败/);assert.doesNotMatch(failed,/download=|旧视频/);
 const completed=renderJob('succeeded',{content_version_id:'content-7',response:'脚本已生成'});
 assert.match(completed,/脚本已生成/);assert.match(completed,/在内容工作台查看/);
});
