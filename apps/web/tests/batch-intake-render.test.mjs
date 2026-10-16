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
let ui,directory;
before(async()=>{
 directory=await mkdtemp(join(tmpdir(),'louxiang-intake-'));
 const result=await build({stdin:{contents:"export {BatchUploadDialog} from './BatchUploadDialog';export {FactReviewPanel} from './FactReviewPanel';",resolveDir:resolve('src'),loader:'tsx'},bundle:true,write:false,format:'esm',platform:'node',jsx:'automatic',external:['react','react-dom','react/jsx-runtime'],plugins:[{name:'http-boundary',setup(builder){
  builder.onResolve({filter:/^\.\/(BatchUploadDialog|FactReviewPanel)$/},args=>existsSync(resolve('src',args.path+'.tsx'))?undefined:{path:args.path,namespace:'missing'});
  builder.onLoad({filter:/.*/,namespace:'missing'},()=>({loader:'js',contents:'export const BatchUploadDialog=()=>null,FactReviewPanel=()=>null;'}));
  builder.onResolve({filter:/^\.\/hooks$/},()=>({path:'hooks',namespace:'http'}));builder.onLoad({filter:/.*/,namespace:'http'},()=>({loader:'js',contents:'export const useApp=()=>globalThis.intakeApp;'}));
 }}]});
 const bundled=result.outputFiles[0].text.replaceAll('from "react"',`from ${JSON.stringify(pathToFileURL(resolve('node_modules/react/index.js')).href)}`).replaceAll('from "react/jsx-runtime"',`from ${JSON.stringify(pathToFileURL(resolve('node_modules/react/jsx-runtime.js')).href)}`);const target=join(directory,'ui.mjs');await writeFile(target,bundled);ui=await import(pathToFileURL(target).href);
});
after(async()=>{delete globalThis.intakeApp;await rm(directory,{recursive:true,force:true});});
const setup=(canReview=true)=>{globalThis.intakeApp={project:{id:'p'},canEdit:true,canReview,notify(){},reload:async()=>{}};};
const render=(component,props)=>renderToStaticMarkup(React.createElement(component,props));
// Catches a single-file uploader or AI approval being silently combined with upload.
test('batch upload exposes multiple files, explicit origin and a separate rights stage',()=>{
 setup();const html=render(ui.BatchUploadDialog,{onClose(){}});assert.match(html,/<input[^>]*type="file"[^>]*multiple=""/);assert.match(html,/来源/);assert.match(html,/拖入/);assert.match(html,/AI 识别/);assert.match(html,/同批用途确认/);assert.doesNotMatch(html,/<input[^>]*type="checkbox"[^>]*checked=""/);
});
// Catches hiding conflicting evidence or presenting candidates as already verified.
test('central review displays current versions, missing fields and conflicting candidate evidence',()=>{
 setup();const facts=[{id:'area',semantic_key:'area',label:'建筑面积',value:'98',unit:'㎡',source:'当前楼书第2页',revision:2,status:'unverified'},{id:'layout',semantic_key:'layout',label:'户型布局',value:'',unit:'',source:'',revision:1,status:'unverified'}];const assets=[{id:'brochure',name:'新版楼书.pdf',origin_type:'actual',metadata_json:{fact_candidates:[{semantic_key:'area',label:'建筑面积',value:'102',unit:'㎡',source:'新版楼书第3页',source_excerpt:'建筑面积102㎡'}]}}];
 const html=render(ui.FactReviewPanel,{facts,assets,onEdit(){},onRefresh(){}});assert.match(html,/集中核验/);assert.match(html,/来源冲突/);assert.match(html,/当前楼书第2页/);assert.match(html,/新版楼书第3页/);assert.match(html,/建筑面积102㎡/);assert.match(html,/缺少事实内容或证据来源/);assert.match(html,/v2/);assert.match(html,/待人工核验/);assert.match(html,/expected|当前保存版本|保存版本/);
});
test('read-only reviewers cannot select or submit a batch verification',()=>{
 setup(false);const facts=[{id:'area',semantic_key:'area',label:'建筑面积',value:'98',unit:'㎡',source:'楼书第2页',revision:2,status:'unverified'}];const html=render(ui.FactReviewPanel,{facts,assets:[],onEdit(){},onRefresh(){}});const checkboxes=[...html.matchAll(/<input[^>]*type="checkbox"[^>]*>/g)].map(match=>match[0]);assert.ok(checkboxes.length);assert.ok(checkboxes.every(input=>input.includes('disabled=""')));assert.match(html,/审核负责人/);
});
