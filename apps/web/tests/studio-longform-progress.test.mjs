import {before,after,test} from 'node:test';
import assert from 'node:assert/strict';
import {mkdtemp,writeFile,rm} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import {join,resolve} from 'node:path';
import {pathToFileURL} from 'node:url';
import {build} from 'esbuild';
import React from 'react';
import {renderToStaticMarkup} from 'react-dom/server';
let ui,directory;
before(async()=>{directory=await mkdtemp(join(tmpdir(),'studio-longform-progress-'));const result=await build({entryPoints:[resolve('src/Studio.tsx')],write:false,bundle:true,format:'esm',platform:'node',jsx:'automatic',external:['react','react-dom','react/jsx-runtime'],loader:{'.css':'empty'},plugins:[{name:'loaded-job-fixture',setup(b){b.onResolve({filter:/^\.\/hooks$/},()=>({path:'hooks',namespace:'fixture'}));b.onLoad({filter:/.*/,namespace:'fixture'},()=>({loader:'js',contents:`export const useApp=()=>({project:{id:'p',is_test:true,name:'进度验收',channel:'抖音'},session:{role:'owner'},notify(){},navigate(){},reload(){}});export const useResource=()=>({data:[],loading:false,error:'',retry(){}});export const useMutation=()=>({busy:false,error:'',setError(){},run(){}});export const useJob=()=>({job:globalThis.progressJob,busy:true,error:'',statusText:'逐章展开脚本',percent:10,wait(){},setJob(){},setError(){}});`}));}}]});const code=result.outputFiles[0].text.replaceAll('from "react"',`from ${JSON.stringify(pathToFileURL(resolve('node_modules/react/index.js')).href)}`).replaceAll('from "react/jsx-runtime"',`from ${JSON.stringify(pathToFileURL(resolve('node_modules/react/jsx-runtime.js')).href)}`);const path=join(directory,'ui.mjs');await writeFile(path,code);ui=await import(pathToFileURL(path));});
after(async()=>{delete globalThis.progressJob;await rm(directory,{recursive:true,force:true});});
// Catches reading a nonexistent top-level property and hiding the durable per-chapter progress counters.
test('Studio displays the actual chapter and shot counters from the persisted job result',()=>{
 for(const [completed_chapters,completed_shots] of [[0,0],[2,12]]){globalThis.progressJob={id:'job-long',kind:'generate_content',status:'running',progress:.1,result:{longform_progress:{completed_chapters,total_chapters:6,completed_shots,total_shots:36,target_seconds:300}}};const html=renderToStaticMarkup(React.createElement(ui.Studio));assert.match(html,new RegExp(`章节 ${completed_chapters}/6 · 镜头 ${completed_shots}/36`));assert.match(html,/目标 5 分钟/);}
});
