import {after,before,test} from 'node:test';
import assert from 'node:assert/strict';
import {existsSync} from 'node:fs';
import {mkdtemp,rm,writeFile} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import {join,resolve} from 'node:path';
import {pathToFileURL} from 'node:url';
import {build} from 'esbuild';
import React from 'react';
import {renderToStaticMarkup as render} from 'react-dom/server';
let ui,dir;
before(async()=>{
 dir=await mkdtemp(join(tmpdir(),'content-review-ui-'));
 const files=['ContentReview','ContentRelease'];
 const source=files.map(name=>existsSync(resolve(`src/${name}.tsx`))?`export {${name}} from ${JSON.stringify(resolve(`src/${name}.tsx`))};`:`export const ${name}=()=>null;`).join('\n');
 const result=await build({stdin:{contents:source,resolveDir:process.cwd(),loader:'tsx'},bundle:true,write:false,format:'esm',platform:'node',jsx:'automatic',external:['react','react-dom','react/jsx-runtime'],loader:{'.css':'empty'},plugins:[{name:'read-project-context',setup(b){
 b.onResolve({filter:/^\.\/hooks$/},()=>({path:'hooks',namespace:'fixture'}));
 b.onLoad({filter:/.*/,namespace:'fixture'},()=>({loader:'js',contents:`export const useApp=()=>globalThis.reviewApp;export const useResource=path=>({data:globalThis.reviewResources[path]??null,loading:false,error:'',backgroundError:'',retry(){}});export const useMutation=()=>({error:'',busy:false,setError(){},run:async()=>{}});export const useJob=()=>({busy:false,error:'',job:null,percent:0,statusText:'',wait:async()=>{},setError(){},setJob(){},cancel(){}});`}));
 }}]});
 const bundled=result.outputFiles[0].text.replaceAll('from "react"',`from ${JSON.stringify(pathToFileURL(resolve('node_modules/react/index.js')).href)}`).replaceAll('from "react/jsx-runtime"',`from ${JSON.stringify(pathToFileURL(resolve('node_modules/react/jsx-runtime.js')).href)}`);
 const target=join(dir,'ui.mjs');await writeFile(target,bundled);ui=await import(pathToFileURL(target).href);
});
after(async()=>{delete globalThis.reviewApp;delete globalThis.reviewResources;await rm(dir,{recursive:true,force:true});});
const noop=()=>{};
const content={id:'c1',version:3,topic:'客厅采光',cover_title:'实际采光',script:'南向客厅。请看实际采光。',render_asset_id:'render3',channel:'抖音',status:'rendered'};
const evidence={content,shots:[{id:'shot-a',voiceover:'南向客厅。',caption:'客厅',asset_id:'asset-a',fact_refs:[{id:'fact-a',revision:2}]}],facts:[{id:'fact-a',expected_revision:2,revision:2,label:'朝向',value:'南',unit:'',source:'楼书第3页',status:'verified',valid:true}],assets:[{id:'asset-a',name:'客厅实拍',rights:'authorized',rights_evidence:'2026年摄影授权书',origin_type:'actual',valid:true}],feedback:[{id:'fb1',shot_id:'shot-a',sentence:'南向客厅。',note:'请补充实际拍摄日期',reviewer_name:'审核员',created_at:'2026-10-17T02:00:00Z'}],release_frozen:false};
function setup(role='reviewer',value=evidence){globalThis.reviewApp={project:{id:'p1',name:'住宅项目'},session:{role},canReview:['reviewer','owner'].includes(role),navigate:noop,notify:noop,reload:async()=>{}};globalThis.reviewResources={'/api/projects/p1/contents/c1/review':value,'/api/projects/p1/operations/platform-accounts':[],'/api/projects/p1/jobs':[],'/api/projects/p1/publications':[]};}
// Reviewer must see original wording, source revision, rights proof, and precise existing feedback beside the actual render.
test('review shows exact evidence and shot-bound feedback beside the current video',()=>{
 setup();const html=render(React.createElement(ui.ContentReview,{content,dirty:false,busy:false,onBack:noop,onApproved:noop}));
 assert.match(html,/南向客厅。/);assert.match(html,/楼书第3页/);assert.match(html,/2026年摄影授权书/);assert.match(html,/fact-a|引用版本/);assert.match(html,/请补充实际拍摄日期/);assert.match(html,/\/api\/projects\/p1\/assets\/render3\/file/);assert.match(html,/镜头 1/);assert.match(html,/href="\/api\/projects\/p1\/assets\/asset-a\/file"/);
});
// A read-only or stale selection must not expose an enabled review mutation.
test('read-only reviewers and changed render evidence cannot approve',()=>{
 setup('sales');let html=render(React.createElement(ui.ContentReview,{content,dirty:false,busy:false,onBack:noop,onApproved:noop}));
 assert.match(html,/需要审核负责人/);assert.match(html,/<button[^>]*disabled=""[^>]*>[\s\S]*审核并冻结当前版本/);
 setup('reviewer',{...evidence,content:{...content,render_asset_id:'older-render'}});html=render(React.createElement(ui.ContentReview,{content,dirty:false,busy:false,onBack:noop,onApproved:noop}));assert.match(html,/成片.*改变|重新读取/);
});
// Exported JSON must not be mistaken for a ready-to-publish ZIP, and receipt recording remains on the same content screen.
test('release combines current version files preparation and receipt with truthful labels',()=>{
 setup('owner');globalThis.reviewResources['/api/projects/p1/operations/platform-accounts']=[{platform:'douyin',name:'抖音',supported:true,loggedIn:false}];globalThis.reviewResources['/api/projects/p1/jobs']=[{id:'submitted3',kind:'platform_publish',status:'succeeded',result:{platform:'douyin',content_version_id:'c1',release_id:'release3',status:'submitted'}}];const html=render(React.createElement(ui.ContentRelease,{content:{...content,status:'approved',approval_valid:true,release_id:'release3'},releaseId:'release3',disabled:false}));
 assert.match(html,/下载内容记录/);assert.match(html,/下载 MP4/);assert.match(html,/登记真实发布链接/);assert.match(html,/实际发布时间/);assert.match(html,/准备平台发布包/);assert.match(html,/尚未发布|待核对回执/);
});
