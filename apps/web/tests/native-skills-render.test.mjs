import {after,before,test} from 'node:test';
import assert from 'node:assert/strict';
import {existsSync} from 'node:fs';
import {mkdtemp,rm,writeFile,readFile} from 'node:fs/promises';
import {createServer} from 'node:http';
import {tmpdir} from 'node:os';
import {join,resolve} from 'node:path';
import {pathToFileURL} from 'node:url';
import {build} from 'esbuild';
import React from 'react';
import {renderToStaticMarkup} from 'react-dom/server';

let ui,directory,server,origin;
const requests=[],base='/api/projects/p-1/operations';
before(async()=>{
 directory=await mkdtemp(join(tmpdir(),'housignal-native-skills-'));
 const source=resolve('src/NativeSkillInputs.tsx');
 const result=await build({stdin:{contents:`export * from ${JSON.stringify(resolve('src/SkillsPage.tsx'))};export * from ${JSON.stringify(source)};`,resolveDir:process.cwd(),loader:'tsx'},write:false,bundle:true,format:'esm',platform:'node',jsx:'automatic',external:['react','react-dom','react/jsx-runtime'],loader:{'.css':'empty'},plugins:[{name:'loaded-native-resources',setup(builder){
  if(!existsSync(source))builder.onResolve({filter:/NativeSkillInputs\.tsx$/},()=>({path:'missing-native',namespace:'fixture'}));
  builder.onResolve({filter:/^\.\/hooks$/},()=>({path:'loaded-hooks',namespace:'fixture'}));
  builder.onLoad({filter:/.*/,namespace:'fixture'},({path})=>({loader:'js',contents:path==='missing-native'?'export const NativeSkillInputs=()=>null,nativeSkillDefaults=()=>({}),requestNativeSkill=async()=>null;':`export const useApp=()=>globalThis.nativeSkillApp;export const useResource=path=>({data:globalThis.nativeSkillData[path]??null,loading:false,error:'',retry(){}});export const useJob=()=>({job:null,busy:false,error:'',percent:0,statusText:'',wait:async()=>undefined,cancel(){},setJob(){},setError(){}});export const useMutation=()=>({busy:false,error:'',setError(){},run:async()=>undefined});`}));
 }}]});
 const bundled=result.outputFiles[0].text.replaceAll('from "react"',`from ${JSON.stringify(pathToFileURL(resolve('node_modules/react/index.js')).href)}`).replaceAll('from "react/jsx-runtime"',`from ${JSON.stringify(pathToFileURL(resolve('node_modules/react/jsx-runtime.js')).href)}`);
 const target=join(directory,'native.mjs');await writeFile(target,bundled);ui=await import(pathToFileURL(target).href);
 server=createServer(async(req,res)=>{const chunks=[];for await(const chunk of req)chunks.push(chunk);requests.push({path:req.url,method:req.method,body:JSON.parse(Buffer.concat(chunks).toString()||'{}')});res.writeHead(202,{'content-type':'application/json'});res.end(JSON.stringify({job_id:'native-1'}));});
 await new Promise(resolve=>server.listen(0,'127.0.0.1',resolve));origin=`http://127.0.0.1:${server.address().port}`;
});
after(async()=>{server?.close();delete globalThis.nativeSkillApp;delete globalThis.nativeSkillData;if(directory)await rm(directory,{recursive:true,force:true});});
function setup(records={},role='content'){globalThis.nativeSkillApp={project:{id:'p-1',name:'测试项目'},session:{role},refresh:0};globalThis.nativeSkillData=records;}
const schema={accepts_assets:true,accepted_kinds:['video'],requires_model_authorization:true,parameters:[{key:'format',label:'输出格式',type:'select',options:[{value:'mp4',label:'MP4'},{value:'webm',label:'WebM'}],default:'mp4'},{key:'duration',label:'截取秒数',type:'number',min:1,max:300,default:12},{key:'caption',label:'字幕内容',type:'text',default:'项目实拍'}]};
const assets=[{id:'allowed',name:'已授权客厅视频',kind:'video',rights:'authorized',metadata_json:{model_processing_authorized:true}},{id:'local-only',name:'仅本地处理视频',kind:'video',rights:'authorized',metadata_json:{model_processing_authorized:false}},{id:'revoked',name:'已撤销视频',kind:'video',rights:'revoked',metadata_json:{model_processing_authorized:true}},{id:'wrong-kind',name:'授权图片',kind:'image',rights:'authorized',metadata_json:{model_processing_authorized:true}}];
function markupInputs(overrides={}){return renderToStaticMarkup(React.createElement(ui.NativeSkillInputs,{schema,assets,assetIds:['allowed'],parameters:{format:'webm',duration:20,caption:'真实字幕'},onAssetsChange(){},onParametersChange(){},...overrides}));}

// Catches the old copied-source UI or external execution route persisting in the product.
test('the native skill page shows project services and real readiness without copied source or external service entrypoints',()=>{
 const skills=[{id:'native-video',title:'视频转换',layer:'audio_video',description:'转换项目素材',mode:'native',execution_engine:'housignal_native',input_schema:schema,target:'studio',status:'ready',reason:'本项目媒体服务可用',easel_skill:'video-converter',easel_url:'http://127.0.0.1:8002'},{id:'unconfigured',title:'AI 视频',layer:'audio_video',description:'生成视频',mode:'native',execution_engine:'housignal_native',input_schema:{accepts_assets:false,parameters:[]},target:'studio',status:'setup',reason:'尚未配置楼响视频模型',easel_skill:'ai-video-gen',easel_url:'http://127.0.0.1:8002'}];
 setup({[`${base}/skills`]:skills,[`${base}/accounts`]:[],'/api/projects/p-1/jobs':[]});const html=renderToStaticMarkup(React.createElement(ui.SkillsPage));
 assert.doesNotMatch(html,/Easel|查看源码|skill-library|127\.0\.0\.1:8002/);assert.match(html,/楼响项目服务/);assert.match(html,/尚未配置楼响视频模型/);assert.match(html,/使用技能/);assert.match(html,/查看准备/);assert.match(html,/最近内容技能任务/);
});

// Catches enabling revoked/wrong-kind files or assuming missing AI authorization is permission.
test('model-processing inputs restrict assets to authorized accepted kinds with explicit AI permission',()=>{
 const html=markupInputs();assert.match(html,/已授权客厅视频/);assert.match(html,/<input\b(?=[^>]*value="allowed")(?=[^>]*checked="")[^>]*>/);assert.doesNotMatch(html,/已撤销视频|授权图片|仅本地处理视频/);assert.match(html,/输出格式/);assert.match(html,/value="webm" selected=""/);assert.match(html,/type="number"[^>]*min="1"[^>]*max="300"[^>]*value="20"/);assert.match(html,/value="真实字幕"/);
});

// Catches over-restricting deterministic local processing or silently inventing model permission.
test('deterministic inputs accept authorized files without claiming AI authorization',()=>{
 const html=markupInputs({schema:{...schema,requires_model_authorization:false}});assert.match(html,/仅本地处理视频/);assert.match(html,/AI 处理未授权/);assert.doesNotMatch(html,/已撤销视频|授权图片/);
});

// Catches allowing read-only users or a busy task to edit selected materials and parameters.
test('disabled native inputs keep both asset and parameter controls read-only',()=>{
 const html=markupInputs({disabled:true});const controls=Array.from(html.matchAll(/<(?:input|select)\b[^>]*>/g),match=>match[0]);assert.ok(controls.length>=4);for(const control of controls)assert.match(control,/disabled=""/);
});

// Catches numeric material-index guessing and selections beyond the API's bounded 12-file contract.
test('material controls name the selected files and stop adding a thirteenth file without trapping existing selections',()=>{
 const many=Array.from({length:13},(_,index)=>({id:`asset-${index}`,name:`素材 ${index+1}`,kind:'video',rights:'authorized',metadata_json:{model_processing_authorized:false}}));
 const html=markupInputs({schema:{accepts_assets:true,parameters:[{key:'voice_index',label:'voice_index',type:'number',integer:true,min:0}]},assets:many,assetIds:many.slice(0,12).map(asset=>asset.id),parameters:{voice_index:0}});
 assert.match(html,/<option value="0" selected="">[^<]*素材 1/);assert.match(html,/<option value="11">[^<]*素材 12/);assert.match(html,/清空选择/);assert.match(html,/<input\b(?=[^>]*value="asset-12")(?=[^>]*disabled="")[^>]*>/);assert.doesNotMatch(Array.from(html.matchAll(/<input\b[^>]*>/g),match=>match[0]).find(value=>value.includes('value="asset-0"'))||'',/disabled=/);
});

// Catches dropping server-owned parameter defaults before submission.
test('parameter defaults preserve scalar types and do not invent absent defaults',()=>{
 assert.deepEqual(ui.nativeSkillDefaults({...schema,parameters:[...schema.parameters,{key:'optional',label:'可选备注',type:'text'}]}),{format:'mp4',duration:12,caption:'项目实拍'});
});

// Catches stale generate-only defaults being rejected after changing to a narrower operation contract.
test('submission parameters include only the current operation fields and its defaults',()=>{
 const operationSchema={accepts_assets:false,input_required:true,parameters:[{key:'op',label:'操作',type:'select',default:'plan'},{key:'plan',label:'剧集计划',type:'text',format:'json'},{key:'confirmed',label:'确认',type:'boolean',default:false}]};
 assert.deepEqual(ui.nativeSkillSubmissionParameters?.(operationSchema,{op:'plan',negative_prompt:'旧生成设置',seed:42,plan:'{"title":"本项目"}'}),{op:'plan',plan:'{"title":"本项目"}',confirmed:false});
});

// Catches exposing internal control names or losing the owning schema's date/integer constraints.
test('native controls explain generation settings and render dates and integer ranges accurately',()=>{
 const readable={accepts_assets:false,input_required:true,parameters:[{key:'negative_prompt',label:'negative_prompt',type:'text'},{key:'seed',label:'seed',type:'number',integer:true,min:0,max:2147483647},{key:'start_date',label:'开始日期',type:'text',format:'date',placeholder:'YYYY-MM-DD'},{key:'op',label:'op',type:'select',options:[{value:'brief',label:'brief'},{value:'generate',label:'generate'}],default:'brief'}]};
 const html=markupInputs({schema:readable,parameters:{seed:42,start_date:'2026-10-14',op:'brief'}});
 assert.match(html,/避免出现的内容/);assert.match(html,/随机种子/);assert.match(html,/留空/);assert.match(html,/type="number"[^>]*step="1"/);assert.match(html,/type="date"/);assert.match(html,/placeholder="YYYY-MM-DD"/);assert.match(html,/整理制作需求/);assert.match(html,/生成内容/);assert.doesNotMatch(html,/>negative_prompt<|>seed<|>brief<|>generate</);
});

// Catches another registered media control silently reverting to raw internal vocabulary.
test('registered media controls have readable product labels and keep the backend option values intact',async()=>{
 const definitions=JSON.parse(await readFile(resolve('../api/app/content_skills/media_definitions.json'),'utf8')),keys=new Set();
 for(const skill of Object.values(definitions)){
  for(const key of skill.accepted_parameters||[])if(key!=='operation')keys.add(key);
  for(const parameter of skill.parameters||[])keys.add(parameter.key);
  const html=markupInputs({schema:{...skill,parameters:skill.parameters||[]},parameters:ui.nativeSkillDefaults(skill)});
  for(const parameter of skill.parameters||[])for(const option of parameter.options||[])assert.ok(html.includes(`value="${option.value}"`),`missing ${parameter.key} option ${option.value}`);
 }
 for(const key of keys)assert.match(ui.nativeSkillParameterLabel({key,label:key,type:'text'}),/[^\x00-\x7f]/,`unreadable field ${key}`);
});

// Catches boolean consent/options being sent as text instead of literal booleans.
test('native boolean parameters render editable checkboxes and retain literal false and true defaults',()=>{
 const booleanSchema={accepts_assets:false,input_required:true,parameters:[{key:'references_reviewed',label:'已审阅参考材料',type:'boolean',default:false},{key:'instrumental',label:'纯音乐',type:'boolean',default:true}]};
 assert.deepEqual(ui.nativeSkillDefaults(booleanSchema),{references_reviewed:false,instrumental:true});
 const html=markupInputs({schema:booleanSchema,parameters:{references_reviewed:false,instrumental:true}}),controls=Array.from(html.matchAll(/<input\b[^>]*>/g),match=>match[0]);
 assert.equal(controls.length,2);for(const control of controls)assert.match(control,/type="checkbox"/);assert.doesNotMatch(controls[0],/checked=""/);assert.match(controls[1],/checked=""/);assert.doesNotMatch(html,/value="(?:false|true)"/);
 const disabled=markupInputs({schema:booleanSchema,parameters:{references_reviewed:false,instrumental:true},disabled:true});for(const control of disabled.matchAll(/<input\b[^>]*>/g))assert.match(control[0],/disabled=""/);
});

// Catches bypassing required text for a model task or preventing a pure file conversion.
test('native input validation distinguishes required briefs from asset-only conversion',()=>{
 assert.equal(ui.nativeSkillHasInput?.({...schema,input_required:false},'', ['allowed']),true);
 assert.equal(ui.nativeSkillHasInput?.({...schema,input_required:true},'', ['allowed']),false);
 assert.equal(ui.nativeSkillHasInput?.({...schema,input_required:false,requires_assets:true},'', []),false);
 assert.equal(ui.nativeSkillHasInput?.({accepts_assets:false,input_required:true,parameters:[]},'  写一条文案  ',[]),true);
});

// Catches inventing a prompt requirement for a project read/list operation.
test('project reads accept no brief or assets while required-asset operations keep that gate',()=>{
 assert.equal(ui.nativeSkillHasInput?.({accepts_assets:false,input_required:false,parameters:[]},'',[]),true);
 assert.equal(ui.nativeSkillHasInput?.({accepts_assets:true,input_required:false,requires_assets:true,parameters:[]},'已有描述',[]),false);
 assert.equal(ui.nativeSkillHasInput?.(undefined,'',[]),false);
});

// Catches the top-level generate state preventing local plan/assemble, or an operation override losing asset/model flags.
test('selected native operations derive their own readiness and merge partial input flags',()=>{
 const skill={status:'setup',reason:'生成模型待配置',input_schema:{...schema,input_required:true,requires_assets:false,parameters:[{key:'op',label:'操作',type:'select',default:'generate'}]},operation_states:{generate:{status:'setup',reason:'生成模型待配置'},plan:{status:'ready',reason:'本项目规划可用',input_schema:{accepts_assets:false,requires_model_authorization:false,input_required:true}},assemble:{status:'ready',reason:'本地素材可拼接',input_schema:{input_required:false,requires_assets:true,requires_model_authorization:false}}}};
 assert.equal(ui.nativeSkillOperation?.(skill,{}).status,'setup');
 const plan=ui.nativeSkillOperation?.(skill,{op:'plan'});assert.equal(plan?.status,'ready');assert.equal(plan?.reason,'本项目规划可用');assert.equal(plan?.input_schema.accepts_assets,false);assert.equal(plan?.input_schema.requires_model_authorization,false);assert.equal(plan?.input_schema.input_required,true);
 const assemble=ui.nativeSkillOperation?.(skill,{op:'assemble'});assert.equal(assemble?.status,'ready');assert.equal(assemble?.input_schema.requires_assets,true);assert.equal(assemble?.input_schema.input_required,false);assert.equal(assemble?.input_schema.requires_model_authorization,false);assert.deepEqual(assemble?.input_schema.accepted_kinds,['video']);
 assert.notEqual(ui.nativeSkillOperation?.(skill,{op:'unknown'}).status,'ready');
});

// Catches speech recognition being blocked by subtitle-only model requirements, or ignoring a non-op selector.
test('format-selected speech recognition renders TXT as ready while SRT keeps its own setup state',()=>{
 const skill={status:'ready',reason:'云全文识别可用',operation_key:'format',input_schema:{accepts_assets:true,requires_assets:true,input_required:false,parameters:[{key:'format',label:'输出格式',type:'select',default:'txt',options:[{value:'txt',label:'TXT 全文'},{value:'srt',label:'SRT 字幕'}]}]},operation_states:{txt:{status:'ready',reason:'云全文识别可用'},srt:{status:'setup',reason:'时间轴模型待配置'}}};
 const txt=ui.nativeSkillOperation(skill,{});assert.equal(txt.status,'ready');assert.equal(txt.reason,'云全文识别可用');
 const html=markupInputs({schema:txt.input_schema,parameters:{format:'txt'}});assert.match(html,/value="txt" selected=""/);assert.match(html,/SRT 字幕/);
 const srt=ui.nativeSkillOperation(skill,{format:'srt'});assert.equal(srt.status,'setup');assert.equal(srt.reason,'时间轴模型待配置');
 const actionSkill={...skill,operation_key:'action',input_schema:{...skill.input_schema,parameters:[{key:'action',label:'操作',type:'select',default:'txt'}]}};assert.equal(ui.nativeSkillOperation(actionSkill,{}).status,'ready');assert.equal(ui.nativeSkillOperation(actionSkill,{action:'srt'}).status,'setup');
});

// Catches a JSON parameter looking like ordinary prose or a required-asset operation hiding material selection.
test('JSON parameters explain their format and asset-required operations expose project material selection',()=>{
 const html=markupInputs({schema:{accepts_assets:false,input_required:false,requires_assets:true,parameters:[{key:'segments',label:'字幕片段',type:'text',format:'json'}]},parameters:{segments:'[{"start":0,"text":"项目实拍"}]'}});
 assert.match(html,/输入素材/);assert.match(html,/字幕片段/);assert.match(html,/JSON/);assert.match(html,/&quot;start&quot;:0/);
});

// Catches project leakage, omitted asset/parameter inputs and requiring natural language for a conversion.
test('native requests send selected assets and structured parameters to the current project with an optional brief',async()=>{
 const nativeFetch=globalThis.fetch;globalThis.fetch=(path,options)=>nativeFetch(origin+path,options);requests.length=0;
 try{const result=await ui.requestNativeSkill('p-1','video-convert',{input:'  ',account_id:'account-1',asset_ids:['allowed'],parameters:{format:'webm',duration:20}});assert.deepEqual(result,{job_id:'native-1'});assert.deepEqual(requests,[{path:'/api/projects/p-1/operations/skills/video-convert/run',method:'POST',body:{input:'',account_id:'account-1',asset_ids:['allowed'],parameters:{format:'webm',duration:20}}}]);}finally{globalThis.fetch=nativeFetch;}
});

// Catches a supposedly free readiness check accidentally executing/enqueuing the skill.
test('native preflight posts the draft only to the scoped preflight endpoint',async()=>{
 const nativeFetch=globalThis.fetch;globalThis.fetch=(path,options)=>nativeFetch(origin+path,options);requests.length=0;
 try{await ui.requestNativeSkillPreflight?.('p-1','auto-subtitle',{input:'',asset_ids:[],parameters:{format:'srt',segments:'[{"start":0,"end":2,"text":"真实片段"}]'}});assert.deepEqual(requests,[{path:'/api/projects/p-1/operations/skills/auto-subtitle/preflight',method:'POST',body:{input:'',asset_ids:[],parameters:{format:'srt',segments:'[{"start":0,"end":2,"text":"真实片段"}]'}}}]);}finally{globalThis.fetch=nativeFetch;}
});

// Catches forcing users to download every image/video to review it or automatically loading an external/private-other-project URL.
test('task artifacts preview only current private raster/media files and keep every output downloadable',()=>{
 assert.equal(typeof ui.SkillArtifactGallery,'function');const prefix='/api/projects/p-1/operations/skills/artifacts/job-1/';
 const artifacts=[{name:'封面',url:prefix+'cover.png',mime_type:'image/png',byte_size:1024},{name:'成片',url:prefix+'final.mp4',mime_type:'video/mp4'},{name:'配音',url:prefix+'voice.mp3',mime_type:'audio/mpeg'},{name:'SVG原文件',url:prefix+'layout.svg',mime_type:'image/svg+xml'},{name:'外部参考',url:'https://example.invalid/reference.png',mime_type:'image/png'},{name:'其他项目',url:'/api/projects/other/operations/skills/artifacts/job-1/other.png',mime_type:'image/png'}];
 const html=renderToStaticMarkup(React.createElement(ui.SkillArtifactGallery,{artifacts,privatePrefix:prefix}));assert.match(html,/img[^>]*src="\/api\/projects\/p-1\/operations\/skills\/artifacts\/job-1\/cover.png"/);assert.match(html,/<video[^>]*controls/);assert.match(html,/<audio[^>]*controls/);assert.doesNotMatch(html,/<(?:img|video|audio)[^>]*src="(?:https:|[^\"]*other|[^\"]*layout.svg)/);assert.equal(Array.from(html.matchAll(/download=/g)).length,6);assert.doesNotMatch(html,/autoPlay|autoplay/);
});
