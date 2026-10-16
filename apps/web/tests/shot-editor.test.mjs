import {after,before,test} from 'node:test';
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
 directory=await mkdtemp(join(tmpdir(),'louxiang-shot-editor-'));
 const source=resolve('src/ShotEditor.tsx');
 const result=await build({entryPoints:[source],write:false,bundle:true,format:'esm',platform:'node',jsx:'automatic',external:['react','react-dom','react/jsx-runtime'],loader:{'.css':'empty'},plugins:[{name:'missing-editor',setup(builder){if(!existsSync(source)){builder.onResolve({filter:/ShotEditor\.tsx$/},()=>({path:'missing',namespace:'fixture'}));builder.onLoad({filter:/.*/,namespace:'fixture'},()=>({loader:'js',contents:'export const ShotEditor=()=>null; export const storyboardShots=()=>[]; export const updateStoryboardShot=()=>[]; export const moveStoryboardShot=()=>[]; export const removeStoryboardShot=()=>[]; export const appendStoryboardShot=()=>[]; export const eligibleShotAssets=()=>[]; export const validateStoryboard=()=>({errors:[],shotErrors:[],totalSeconds:null});'}));}}}]});
 const code=result.outputFiles[0].text.replaceAll('from "react"',`from ${JSON.stringify(pathToFileURL(resolve('node_modules/react/index.js')).href)}`).replaceAll('from "react/jsx-runtime"',`from ${JSON.stringify(pathToFileURL(resolve('node_modules/react/jsx-runtime.js')).href)}`);
 const target=join(directory,'editor.mjs');await writeFile(target,code);ui=await import(pathToFileURL(target).href);
});
after(async()=>{if(directory)await rm(directory,{recursive:true,force:true});});
const asset=(id,name,extra={})=>({id,name,kind:'image',mime_type:'image/jpeg',rights:'authorized',rights_evidence:'摄制授权记录',origin_type:'actual',byte_size:10,created_at:'2026-10-14T01:00:00Z',...extra});
const assets=[asset('living','客厅实拍'),asset('kitchen','厨房实拍')];
const shots=[{asset_id:'living',duration:8,start:0,caption:'从客厅看动线'},{asset_id:'kitchen',duration:7,start:0,caption:'再看厨房'}];
function validLongStoryboard(planned=false){const voice='本镜头核对家庭真实需求与生活路线再解释空间选择的优点限制和现场核验的方法避免只看面积就下结论';return {schema_version:2,target_seconds:300,planning_context:{hook:'先明确问题',outline:['核对家庭需求'],cta:'用清单核对',reference:{beats:[]}},chapters:Array.from({length:6},(_,i)=>({id:'c'+i,title:'具体问题'+i,purpose:'解释一个具体的空间取舍',plan_sections:[i===0?'hook':i===5?'cta':'outline-1'],reference_beat_ids:[],target_seconds:50,shot_ids:Array.from({length:5},(_,n)=>'s'+(i*5+n))})),shots:Array.from({length:30},(_,i)=>({id:'s'+i,chapter_id:'c'+Math.floor(i/5),plan_section:i<5?'hook':i>=25?'cta':'outline-1',reference_beat_ids:[],asset_id:planned?null:'living',production_status:planned?'planned':'ready',voiceover:'镜头'+i+voice,visual_direction:'核对对应生活路线并展示取舍',visual_type:'scene',caption:'核对家庭需求',duration:10,start:0,fact_refs:[]}))};}

const html=(props={})=>renderToStaticMarkup(React.createElement(ui.ShotEditor,{value:shots,assets,isTest:false,onChange(){},...props}));
// Catches a visual editor that discards renderer or future metadata when updating one cell.
test('editing a row preserves the storyboard container and every unedited field',()=>{
 const input={schema_version:7,source:{reference_id:'r-1'},shots:[{...shots[0],purpose:'开场',voiceover:'已有资料',tracking:{id:'original'}}]};
 assert.deepEqual(ui.updateStoryboardShot(input,0,{caption:'修改后的字幕'}),{schema_version:7,source:{reference_id:'r-1'},shots:[{asset_id:'living',duration:8,start:0,caption:'修改后的字幕',purpose:'开场',voiceover:'已有资料',tracking:{id:'original'}}]});
 assert.equal(input.shots[0].caption,'从客厅看动线');
});
test('reordering keeps the existing array shape and moves the complete shot',()=>{
 assert.deepEqual(ui.moveStoryboardShot(shots,1,0),[{asset_id:'kitchen',duration:7,start:0,caption:'再看厨房'},{asset_id:'living',duration:8,start:0,caption:'从客厅看动线'}]);
 assert.equal(shots[0].asset_id,'living');
});
test('adding and removing rows retains object metadata and does not silently select an asset',()=>{
 const input={notes:['retain'],shots:[shots[0]]};
 assert.deepEqual(ui.appendStoryboardShot(input),{notes:['retain'],shots:[shots[0],{asset_id:'',caption:'',duration:5,start:0}]});
 assert.deepEqual(ui.removeStoryboardShot({notes:['retain'],shots},0),{notes:['retain'],shots:[shots[1]]});
});
// Catches reference footage, revoked footage, or templates entering marketing production.
test('only project footage with usable rights is offered, with test templates limited to test projects',()=>{
 const candidates=[...assets,asset('revoked','撤销素材',{rights:'revoked'}),asset('no-proof','无依据',{rights_evidence:' '}),asset('ref','参考原片',{origin_type:'reference'}),asset('research','仅研究',{metadata_json:{usage_scope:'research_only'}}),asset('ai','合成',{origin_type:'ai_generated'}),asset('template','模板',{origin_type:'template'}),asset('test','测试',{origin_type:'test'}),asset('doc','楼书',{kind:'document'})];
 assert.deepEqual(ui.eligibleShotAssets(candidates,false).map(a=>a.id),['living','kitchen']);
 assert.deepEqual(ui.eligibleShotAssets(candidates,true).map(a=>a.id),['living','kitchen','template','test']);
});
test('a valid 15 second storyboard accepts clip start zero and reports its actual duration',()=>{
 assert.deepEqual(ui.validateStoryboard(shots,assets,false),{errors:[],shotErrors:[[],[]],totalSeconds:15});
});
test('an observed zero duration remains zero while an unknown duration remains unknown and both require correction',()=>{
 const zero=ui.validateStoryboard([{...shots[0],duration:0}],assets,false);
 assert.equal(zero.totalSeconds,0);assert.match(zero.shotErrors[0].join(' '),/1.*60/);
 const unknown=ui.validateStoryboard([{asset_id:'living',caption:'待定'}],assets,false);
 assert.equal(unknown.totalSeconds,null);assert.match(unknown.shotErrors[0].join(' '),/时长/);
});
test('renderer boundaries and authorization failures are actionable before save',()=>{
 const result=ui.validateStoryboard([{...shots[0],start:-1,duration:61,asset_id:'gone'}],assets,false);
 assert.match(ui.validateStoryboard([{...shots[0],duration:601}],assets,false).errors.join(' '),/15.*60/);assert.match(result.shotErrors[0].join(' '),/素材.*不可用/);assert.match(result.shotErrors[0].join(' '),/0.*600/);assert.match(result.shotErrors[0].join(' '),/1.*60/);
 assert.match(ui.validateStoryboard(Array.from({length:21},()=>shots[0]),assets,false).errors.join(' '),/20/);
});
test('the editor presents ordered named assets, captions and timing controls without requiring asset IDs',()=>{
 const value=html();assert.match(value,/镜头 1/);assert.match(value,/镜头 2/);assert.match(value,/客厅实拍/);assert.match(value,/厨房实拍/);assert.match(value,/15 秒/);assert.match(value,/镜头字幕/);assert.match(value,/素材起点/);assert.match(value,/aria-label="镜头 1 时长/);assert.doesNotMatch(value,/<code>|JSON/);
});
test('a revoked selected asset stays visible and disabled for correction rather than being replaced',()=>{
 const value=html({assets:[asset('living','客厅实拍',{rights:'revoked'}),assets[1]]});
 assert.match(value,/<option value="living" disabled="" selected="">客厅实拍[^<]*不可用/);assert.match(value,/素材.*不可用/);assert.match(value,/<option value="kitchen">厨房实拍/);
});
test('read-only users can inspect shot rows but cannot change order, add or remove',()=>{
 const value=html({disabled:true});assert.match(value,/<select[^>]*disabled=""/);const buttons=[...value.matchAll(/<button\b[^>]*>/g)];assert.ok(buttons.length>=5);assert.ok(buttons.every(([tag])=>tag.includes('disabled=""')));
});
// Catches legal test/template assets being presented as real footage in a demo project.
test('a test storyboard describes synthetic materials as illustrations rather than actual project footage',()=>{
 const demoAssets=[asset('living','客厅示意',{origin_type:'template'}),asset('kitchen','厨房示意',{origin_type:'test'})];
 const value=html({assets:demoAssets,isTest:true});assert.match(value,/示意素材/);assert.match(value,/图文与示意素材不代表项目实景/);assert.doesNotMatch(value,/按镜头选择实拍素材|项目实拍素材/);
});
// Catches long-form chapter storyboards still being rejected by the former 20-shot / 60-second caps.
test('a five-minute chapter storyboard accepts thirty mapped ten-second shots',()=>{
 const storyboard=validLongStoryboard();
 const result=ui.validateStoryboard(storyboard,assets,false);assert.equal(result.totalSeconds,300);assert.deepEqual(result.errors,[]);
});
// Catches silently rendering a shorter chapter script than the target selected in planning.
test('a chapter storyboard requires its summed duration to match the planned duration',()=>{
 const storyboard={schema_version:2,target_seconds:300,chapters:[{id:'chapter-1',title:'需求',plan_section:'需求',reference_beat_ids:[]}],shots:[{...shots[0],id:'shot-1',chapter_id:'chapter-1',voiceover:'已提供口播',duration:60}]};
 assert.match(ui.validateStoryboard(storyboard,assets,false).errors.join(' '),/目标.*300|300.*目标/);
});
// Catches long-form edits losing all chapter linkage when a new shot is added.
test('adding a long-form shot preserves the source snapshot and inherits the selected chapter mapping',()=>{
 const value={schema_version:2,target_seconds:300,source:{plan_id:'plan-1'},chapters:[{id:'chapter-1',title:'需求',plan_section:'需求',reference_beat_ids:['question']}],shots:[{...shots[0],id:'shot-1',chapter_id:'chapter-1'}]};
 const updated=ui.appendStoryboardShot(value,'chapter-1'),added=updated.shots.at(-1);assert.deepEqual(updated.source,{plan_id:'plan-1'});assert.equal(added.chapter_id,'chapter-1');assert.equal(added.plan_section,'需求');assert.deepEqual(added.reference_beat_ids,['question']);assert.ok(added.id);assert.equal(added.asset_id,null);
});
test('short-form arrays retain their twenty-shot cap while chapter storyboards support up to 120',()=>{
 const twenty=Array.from({length:20},()=>shots[0]);assert.equal(ui.appendStoryboardShot(twenty),twenty);
 const full={schema_version:2,target_seconds:600,chapters:[{id:'c1'}],shots:Array.from({length:120},()=>({...shots[0],chapter_id:'c1'}))};assert.equal(ui.appendStoryboardShot(full),full);
});
// Catches requiring shoot-ready assets before a useful chapter script can be saved as a production draft.
test('planned long-form shots can save without assets, while rendering requires authorized ready shots',()=>{
 const value=validLongStoryboard(true);
 assert.deepEqual(ui.validateStoryboard(value,assets,false).errors,[]);assert.match(ui.validateStoryboard(value,assets,false,'render').errors.join(' '),/素材|准备/);
});
test('unsupported durations between short and long videos explain the available length choices',()=>{
 assert.match(ui.validateStoryboard([{...shots[0],duration:60},{...shots[1],duration:30}],assets,false).errors.join(' '),/15.*60.*300.*600|短视频.*长视频/);
});
test('chapter shot durations follow the renderer limit of thirty seconds',()=>{
 const value={schema_version:2,target_seconds:300,chapters:[{id:'c1',title:'章',target_seconds:31}],shots:[{...shots[0],id:'s1',chapter_id:'c1',duration:31,production_status:'ready',voiceover:'有效口播'}]};assert.match(ui.validateStoryboard(value,assets,false).shotErrors[0].join(' '),/1.*30/);
});
test('ten-minute scripts need at least sixty shots and each chapter needs its own dense shot coverage',()=>{
 const value={schema_version:2,target_seconds:600,chapters:Array.from({length:10},(_,i)=>({id:`c${i}`,title:'章',target_seconds:60,shot_ids:[`s${i*3}`,`s${i*3+1}`,`s${i*3+2}`]})),shots:Array.from({length:30},(_,i)=>({...shots[0],id:`s${i}`,chapter_id:`c${Math.floor(i/3)}`,duration:20,production_status:'ready',voiceover:'有口播'}))};const errors=ui.validateStoryboard(value,assets,false).errors.join(' ');assert.match(errors,/60.*镜头/);assert.match(errors,/章节|第 1 章/);
});
test('oversized long-form captions and missing narration produce fixable errors before save',()=>{
 const value={schema_version:2,target_seconds:300,chapters:[{id:'c1',title:'章',target_seconds:20}],shots:[{...shots[0],id:'s1',chapter_id:'c1',duration:20,production_status:'ready',voiceover:' ',caption:'字'.repeat(181),visual_direction:''}]};const errors=ui.validateStoryboard(value,assets,false).shotErrors[0].join(' ');assert.match(errors,/180/);assert.match(errors,/口播/);assert.match(errors,/画面/);
});
test('chapter lengths beyond the agreed eighty-second limit cannot be saved',()=>{
 for(const target_seconds of [81,180]){const value={schema_version:2,target_seconds:300,chapters:[{id:'c1',title:'章',target_seconds}],shots:[{...shots[0],id:'s1',chapter_id:'c1',duration:20,production_status:'ready',voiceover:'口播'}]};assert.match(ui.validateStoryboard(value,assets,false).errors.join(' '),/20–80/);}
});
test('adding to an earlier chapter inserts there instead of creating an invalid interleaved chapter order',()=>{
 const value={schema_version:2,target_seconds:300,chapters:[{id:'c1',title:'章1',plan_sections:['hook'],reference_beat_ids:[]},{id:'c2',title:'章2',plan_sections:['cta'],reference_beat_ids:[]}],shots:[{id:'s1',chapter_id:'c1',duration:10},{id:'s2',chapter_id:'c2',duration:10}]};const added=ui.appendStoryboardShot(value,'c1');assert.equal(added.shots[0].id,'s1');assert.equal(added.shots[1].chapter_id,'c1');assert.equal(added.shots[2].id,'s2');assert.deepEqual(added.chapters[0].shot_ids,[added.shots[0].id,added.shots[1].id]);
});
test('broken chapter mappings and insufficient chapter narration report their concrete repair requirements',()=>{
 const value=validLongStoryboard();value.chapters[0].plan_sections=['not-in-plan'];value.chapters[0].shot_ids=['incorrect'];value.shots.slice(0,5).forEach(shot=>shot.voiceover='太短');const errors=ui.validateStoryboard(value,assets,false).errors.join(' ');assert.match(errors,/策划环节/);assert.match(errors,/关联或顺序/);assert.match(errors,/190–260.*当前/);
});
test('copying the same narration into another shot is rejected without changing its source mappings',()=>{
 const value=validLongStoryboard();value.shots[1].voiceover=value.shots[0].voiceover;assert.match(ui.validateStoryboard(value,assets,false).errors.join(' '),/口播重复/);assert.equal(value.shots[1].chapter_id,'c0');
});
test('legacy arrays cannot claim long-video duration without the chapter storyboard format',()=>{
 assert.match(ui.validateStoryboard(Array.from({length:5},()=>({...shots[0],duration:60})),assets,false).errors.join(' '),/旧版短视频.*60.*章节脚本/);
});
