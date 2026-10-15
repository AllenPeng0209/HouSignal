import {after,before,test} from 'node:test';
import assert from 'node:assert/strict';
import {existsSync} from 'node:fs';
import {mkdtemp,rm,writeFile} from 'node:fs/promises';
import {createServer} from 'node:http';
import {tmpdir} from 'node:os';
import {join,resolve} from 'node:path';
import {pathToFileURL} from 'node:url';
import {build} from 'esbuild';
import React from 'react';
import {renderToStaticMarkup} from 'react-dom/server';

let ui,directory,server,origin;
const requests=[];
const breakdown={source:{url:'https://www.douyin.com/video/123456789',title:'户型参考',watched:false,evidence_basis:'user_supplied_notes'},timeline:[{start_seconds:0,end_seconds:3,transcript:'先看厨房动线',visual:'',evidence_status:'user_supplied'},{start_seconds:null,end_seconds:null,transcript:'',visual:'从玄关走到客厅',evidence_status:'user_supplied'}],hook:{evidence:'先看厨房动线',status:'user_supplied',questions:['开场是否在前三秒出现？']},adaptation:{topic_id:'layout',eligible:false,missing_facts:['已核验户型图'],fact_refs:[],shots:[{start_seconds:0,end_seconds:3,purpose:'提出问题',voiceover:'先核对项目的户型资料',visual:'拍摄真实入户动线',evidence_status:'pending'}]},report:'原片口播与画面拆解\n用户提供片段，时间待核对',plan_seed:{title:'入户动线怎么看',angle:'用真实户型解释动线',brief:'依据已提供片段改编；待补齐户型图。',topic_id:'layout',channel:'抖音'}};
before(async()=>{
 directory=await mkdtemp(join(tmpdir(),'housignal-breakdown-'));
 const source=resolve('src/VideoBreakdownDialog.tsx');
 const result=await build({stdin:{contents:`export * from ${JSON.stringify(source)};export {AppContext} from ${JSON.stringify(resolve('src/hooks.tsx'))};`,resolveDir:process.cwd(),loader:'tsx'},write:false,bundle:true,format:'esm',platform:'node',jsx:'automatic',external:['react','react-dom','react/jsx-runtime'],loader:{'.css':'empty'},plugins:[{name:'missing-breakdown',setup(builder){if(!existsSync(source)){builder.onResolve({filter:/VideoBreakdownDialog\.tsx$/},()=>({path:'missing-breakdown',namespace:'fixture'}));builder.onLoad({filter:/.*/,namespace:'fixture'},()=>({loader:'js',contents:'export const VideoBreakdownDialog=()=>null,VideoBreakdownPreview=()=>null;export const requestVideoBreakdown=async()=>null,requestBreakdownPlan=async()=>null;'}));}}}]});
 const bundled=result.outputFiles[0].text.replaceAll('from "react"',`from ${JSON.stringify(pathToFileURL(resolve('node_modules/react/index.js')).href)}`).replaceAll('from "react/jsx-runtime"',`from ${JSON.stringify(pathToFileURL(resolve('node_modules/react/jsx-runtime.js')).href)}`);
 const target=join(directory,'breakdown.mjs');await writeFile(target,bundled);ui=await import(pathToFileURL(target).href);
 server=createServer(async(req,res)=>{const chunks=[];for await(const chunk of req)chunks.push(chunk);requests.push({path:req.url,method:req.method,body:JSON.parse(Buffer.concat(chunks).toString()||'{}')});res.writeHead(200,{'content-type':'application/json'});res.end(JSON.stringify(req.url.endsWith('/breakdown/plan')?{id:'plan-from-breakdown'}:breakdown));});
 await new Promise(resolve=>server.listen(0,'127.0.0.1',resolve));origin=`http://127.0.0.1:${server.address().port}`;
});
after(async()=>{server?.close();if(directory)await rm(directory,{recursive:true,force:true});});
const context={project:{id:'p-1',channel:'抖音'},session:{role:'content'},notify(){}};
function markup(element,role='content'){return renderToStaticMarkup(React.createElement(ui.AppContext.Provider,{value:{...context,session:{role}}},element));}
function button(html,label){return Array.from(html.matchAll(/<button\b[^>]*>[\s\S]*?<\/button>/g),m=>m[0]).find(value=>value.includes(`<span>${label}</span>`))||'';}

// Catches pretending a link alone supplies the video content, or hiding the timestamp input convention.
test('breakdown asks for actual source notes and offers explicit duration and topic controls',()=>{
 const html=markup(React.createElement(ui.VideoBreakdownDialog,{source:{source_url:breakdown.source.url,title:'户型参考'},onClose(){},onSaved(){}}));
 assert.match(html,/原片口播或字幕/);assert.match(html,/画面与节奏笔记/);assert.match(html,/00:00-00:03/);assert.match(html,/未自动观看原片/);assert.match(html,/min="15"/);assert.match(html,/max="600"/);assert.match(html,/value="layout"/);assert.match(button(html,'生成拆解与长片章纲'),/disabled=""/);
});
// Catches accidental write access for a read-only project role.
test('read-only roles cannot request breakdowns',()=>{
 const html=markup(React.createElement(ui.VideoBreakdownDialog,{source:{source_url:breakdown.source.url,title:'户型参考'},onClose(){},onSaved(){}}),'sales');
 assert.match(button(html,'生成拆解与长片章纲'),/disabled=""/);assert.match(html,/当前角色/);
});
// Catches missing project scope, fabricated timestamps, or leaking saved discovery summaries as transcript.
test('breakdown posts only the supplied notes and chosen adaptation options to its project',async()=>{
 const nativeFetch=globalThis.fetch;globalThis.fetch=(path,options)=>nativeFetch(origin+path,options);requests.length=0;
 try{const actual=await ui.requestVideoBreakdown('p-1',{source_url:` ${breakdown.source.url} `,title:' 户型参考 ',transcript:' 00:00-00:03 先看厨房动线 ',observations:' 从玄关走到客厅 ',target_seconds:45,topic_id:'layout'});assert.deepEqual(actual,breakdown);assert.deepEqual(requests,[{path:'/api/projects/p-1/operations/discovery/breakdown',method:'POST',body:{source_url:breakdown.source.url,title:'户型参考',transcript:'00:00-00:03 先看厨房动线',observations:'从玄关走到客厅',target_seconds:45,topic_id:'layout'}}]);}finally{globalThis.fetch=nativeFetch;}
});
// Catches a URL-only submit and invalid target duration crossing the HTTP boundary.
test('empty evidence and invalid duration are rejected before submitting',async()=>{
 const nativeFetch=globalThis.fetch;globalThis.fetch=(path,options)=>nativeFetch(origin+path,options);requests.length=0;
 try{
  await assert.rejects(()=>ui.requestVideoBreakdown('p-1',{source_url:breakdown.source.url,title:'户型参考',transcript:' ',observations:'',target_seconds:30,topic_id:'tour'}),/口播|笔记/);
  await assert.rejects(()=>ui.requestVideoBreakdown('p-1',{source_url:breakdown.source.url,title:'户型参考',transcript:'有口播',observations:'',target_seconds:14,topic_id:'tour'}),/15.*600/);
  await assert.rejects(()=>ui.requestVideoBreakdown('p-1',{source_url:breakdown.source.url,title:'户型参考',transcript:'有口播',observations:'',target_seconds:120,topic_id:'tour'}),/短视频.*长视频|15.*60.*300.*600/);
  assert.equal(requests.length,0);
 }finally{globalThis.fetch=nativeFetch;}
});
// Catches unmeasured timing becoming a precise timeline, or missing project facts being hidden.
test('preview keeps unknown timing and missing project evidence visible alongside the script',()=>{
 const html=renderToStaticMarkup(React.createElement(ui.VideoBreakdownPreview,{result:breakdown}));
 assert.match(html,/时间待核对/);assert.match(html,/从玄关走到客厅/);assert.match(html,/先看厨房动线/);assert.match(html,/已核验户型图/);assert.match(html,/拍摄真实入户动线/);assert.match(html,/未自动观看原片/);assert.match(html,/原片口播与画面拆解/);
});
// Catches saving only the edited script and silently dropping the source notes and full report.
test('a reviewed script saves its original evidence and edited plan through the durable breakdown endpoint',async()=>{
 const nativeFetch=globalThis.fetch;globalThis.fetch=(path,options)=>nativeFetch(origin+path,options);requests.length=0;
 const reference={source_url:` ${breakdown.source.url} `,title:' 户型参考 ',transcript:' 00:00-00:03 先看厨房动线 ',observations:' 从玄关走到客厅 ',target_seconds:45,topic_id:'layout'};
 const idempotencyKey='1b6a7e00-b75e-4b71-972b-9a47e68012ac';
 try{
  const actual=await ui.requestBreakdownPlan('p-1',reference,{...breakdown.plan_seed,title:' 修改后的标题 ',brief:' 手动复核后的分镜 '},idempotencyKey,'o-7');
  assert.deepEqual(actual,{id:'plan-from-breakdown'});
  assert.deepEqual(requests,[{path:'/api/projects/p-1/operations/discovery/breakdown/plan',method:'POST',body:{reference:{source_url:breakdown.source.url,title:'户型参考',transcript:'00:00-00:03 先看厨房动线',observations:'从玄关走到客厅',target_seconds:45,topic_id:'layout'},plan:{title:'修改后的标题',angle:'用真实户型解释动线',brief:'手动复核后的分镜',topic_id:'layout',channel:'抖音'},opportunity_id:'o-7',idempotency_key:idempotencyKey}}]);
 }finally{globalThis.fetch=nativeFetch;}
});
// Catches retrying an uncertain write with a newly generated key and creating duplicate plans.
test('durable save retries preserve the supplied idempotency key and source body',async()=>{
 const nativeFetch=globalThis.fetch;globalThis.fetch=(path,options)=>nativeFetch(origin+path,options);requests.length=0;
 const reference={source_url:breakdown.source.url,title:'户型参考',transcript:'提供的口播',observations:'',target_seconds:30,topic_id:'tour'};
 const idempotencyKey='08ccddae-c1c0-4c14-bd2f-c98acfd52e5d';
 try{
  await ui.requestBreakdownPlan('p-1',reference,breakdown.plan_seed,idempotencyKey);
  await ui.requestBreakdownPlan('p-1',reference,breakdown.plan_seed,idempotencyKey);
  assert.equal(requests.length,2);assert.deepEqual(requests[0],requests[1]);assert.equal(requests[0].body.idempotency_key,idempotencyKey);assert.equal(requests[0].body.opportunity_id,null);
 }finally{globalThis.fetch=nativeFetch;}
});
// Catches dropping AbortSignal in a request after a dialog or project closes.
test('an aborted breakdown request cannot issue a network write',async()=>{
 const nativeFetch=globalThis.fetch;globalThis.fetch=(path,options)=>nativeFetch(origin+path,options);requests.length=0;
 try{const controller=new AbortController();controller.abort();await assert.rejects(()=>ui.requestVideoBreakdown('p-1',{source_url:breakdown.source.url,title:'户型参考',transcript:'提供的口播',observations:'',target_seconds:30,topic_id:'tour'},controller.signal),{name:'AbortError'});assert.equal(requests.length,0);}finally{globalThis.fetch=nativeFetch;}
});
// Catches a linked opportunity being saved against a different, edited video source.
test('a saved discovery keeps its original source fixed during breakdown',()=>{
 const html=markup(React.createElement(ui.VideoBreakdownDialog,{source:{source_url:breakdown.source.url,title:'户型参考',opportunity_id:'o-7'},onClose(){},onSaved(){}}));
 const link=Array.from(html.matchAll(/<input\b[^>]*>/g),m=>m[0]).find(value=>value.includes('type="url"'))||'';
 assert.match(link,/readOnly=""/);assert.match(html,/保留已保存发现的原始来源/);
});
// Catches accepting note/title sizes that the server cannot process and sending an avoidable write.
test('breakdown rejects oversized evidence before crossing the HTTP boundary',async()=>{
 const nativeFetch=globalThis.fetch;globalThis.fetch=(path,options)=>nativeFetch(origin+path,options);requests.length=0;
 try{
  const input={source_url:breakdown.source.url,title:'户型参考',transcript:'提供的口播',observations:'',target_seconds:30,topic_id:'tour'};
  await assert.rejects(()=>ui.requestVideoBreakdown('p-1',{...input,transcript:'字'.repeat(10001)}),/10000/);
  await assert.rejects(()=>ui.requestVideoBreakdown('p-1',{...input,observations:'字'.repeat(10001)}),/10000/);
  await assert.rejects(()=>ui.requestVideoBreakdown('p-1',{...input,title:'字'.repeat(301)}),/300/);
  assert.equal(requests.length,0);
 }finally{globalThis.fetch=nativeFetch;}
});
// Catches reopening a saved research reference with an empty, nonrecoverable note form.
test('saved reference opens with its persisted research draft and fixed source',()=>{
 const html=markup(React.createElement(ui.VideoBreakdownDialog,{source:{source_url:breakdown.source.url,title:'户型参考',reference_id:'r-1',analysis_job_id:'j-actual',initial_notes:{transcript:'00:00–00:02 已核对的动线',observations:'00:04 实际玄关',target_seconds:45,topic_id:'layout'}},onClose(){},onSaved(){}}));
 assert.match(html,/已核对的动线/);assert.match(html,/实际玄关/);assert.match(html,/value="45"/);assert.doesNotMatch(button(html,'生成拆解与脚本'),/disabled=""/);
});
// Catches real ASR/frame evidence being mislabeled as wholly user-supplied or complete-video observation.
test('preview labels analyzed source evidence as transcription and sampled frames',()=>{
 const html=renderToStaticMarkup(React.createElement(ui.VideoBreakdownPreview,{result:{...breakdown,source:{...breakdown.source,evidence_basis:'asset_asr_and_sampled_frames'}}}));assert.match(html,/语音转写/);assert.match(html,/采样画面/);assert.match(html,/不能代表逐帧观看/);assert.doesNotMatch(html,/仅根据你提供/);
});
// Catches sampled frame instants being represented as continuous observed video intervals.
test('a sampled visual instant is shown as a point rather than a watched interval',()=>{const html=renderToStaticMarkup(React.createElement(ui.VideoBreakdownPreview,{result:{...breakdown,timeline:[{start_seconds:4,end_seconds:4,transcript:'',visual:'玄关采样画面',evidence_status:'sampled_frame'}]}}));assert.match(html,/4 秒（采样时刻）/);assert.doesNotMatch(html,/4–4 秒/);});

// Catches starting new reference adaptations at 30 seconds or overwriting a persisted user target.
test('new video breakdowns default to five minutes and saved adaptation durations are preserved',()=>{
 const render=initial_notes=>markup(React.createElement(ui.VideoBreakdownDialog,{source:{source_url:breakdown.source.url,title:'户型参考',initial_notes},onClose(){},onSaved(){}}));
 assert.match(render(),/<input type="number"[^>]*max="600"[^>]*value="300"/);
 for(const seconds of [45,480,600])assert.match(render({target_seconds:seconds}),new RegExp(`<input type="number"[^>]*value="${seconds}"`));
});

// Catches an obsolete client limit blocking valid long-form research before it reaches the project endpoint.
test('breakdown accepts five to ten minute targets without changing evidence and rejects longer or nonintegral values',async()=>{
 const nativeFetch=globalThis.fetch;globalThis.fetch=(path,options)=>nativeFetch(origin+path,options);requests.length=0;
 const input={source_url:breakdown.source.url,title:'户型参考',transcript:'00:00–00:03 实际记录的开场',observations:'',target_seconds:300,topic_id:'layout'};
 try{
  for(const target_seconds of [300,480,600])await ui.requestVideoBreakdown('p-1',{...input,target_seconds});
  assert.deepEqual(requests.map(r=>r.body),[300,480,600].map(target_seconds=>({...input,target_seconds})));
  for(const target_seconds of [601,300.5])await assert.rejects(()=>ui.requestVideoBreakdown('p-1',{...input,target_seconds}),/15.*600/);
  assert.equal(requests.length,3);
 }finally{globalThis.fetch=nativeFetch;}
});
// Catches a long reference response with no finished shots being mistaken for an empty or completed script.
test('long-form breakdown previews the proposed chapter outline and identifies the needed shot expansion',()=>{
 const chapters=Array.from({length:6},(_,i)=>({id:`chapter-${i+1}`,title:`第${i+1}章具体问题`,target_seconds:50,purpose:`解释第${i+1}项取舍`,evidence_status:'proposed'}));
 const html=renderToStaticMarkup(React.createElement(ui.VideoBreakdownPreview,{result:{...breakdown,adaptation:{...breakdown.adaptation,chapters,needs_chapter_expansion:true,shots:[]}}}));
 assert.match(html,/项目长片章纲/);assert.match(html,/第1章具体问题/);assert.match(html,/第6章具体问题/);assert.match(html,/解释第1项取舍/);assert.match(html,/逐章生成/);assert.match(html,/30.*镜头/);assert.match(html,/待展开|章纲建议/);assert.doesNotMatch(html,/<h3>项目改编分镜<\/h3>/);
});
test('long and short breakdown actions name the actual output being requested',()=>{
 const render=seconds=>markup(React.createElement(ui.VideoBreakdownDialog,{source:{source_url:breakdown.source.url,title:'户型参考',initial_notes:{target_seconds:seconds,transcript:'已提供的原片笔记'}},onClose(){},onSaved(){}}));
 assert.ok(button(render(300),'生成拆解与长片章纲'));assert.ok(button(render(45),'生成拆解与脚本'));
});
// Catches losing the long-form target, hook and outline on the durable adaptation-plan request.
test('saving a long-form chapter adaptation keeps its structured planning requirements',async()=>{
 const nativeFetch=globalThis.fetch;globalThis.fetch=(path,options)=>nativeFetch(origin+path,options);requests.length=0;
 const reference={source_url:breakdown.source.url,title:'户型参考',transcript:'原片笔记',observations:'',target_seconds:300,topic_id:'layout'};
 const planning={content_type:'professional',subject:'户型分析',target_seconds:300,skill_ids:['topic-matrix'],hook:'先核对家庭需求',outline:['明确需求','核对动线','比较取舍'],cta:'用清单核对下一套房'};
 try{await ui.requestBreakdownPlan('p-1',reference,{...breakdown.plan_seed,planning},'long-plan-key');assert.deepEqual(requests[0].body.plan.planning,planning);assert.equal(requests[0].body.reference.target_seconds,300);}finally{globalThis.fetch=nativeFetch;}
});
