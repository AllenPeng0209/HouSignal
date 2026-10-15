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
before(async()=>{directory=await mkdtemp(join(tmpdir(),'louxiang-longform-'));const source=resolve('src/LongformScriptEditor.tsx');const result=await build({entryPoints:[source],write:false,bundle:true,format:'esm',platform:'node',jsx:'automatic',external:['react','react-dom','react/jsx-runtime'],loader:{'.css':'empty'},plugins:[{name:'missing-editor',setup(b){if(!existsSync(source)){b.onResolve({filter:/LongformScriptEditor\.tsx$/},()=>({path:'missing',namespace:'fixture'}));b.onLoad({filter:/.*/,namespace:'fixture'},()=>({loader:'js',contents:'export const LongformScriptEditor=()=>null,updateLongformChapter=()=>null,updateLongformScript=()=>null,longformSummary=()=>null,continuityContext=()=>null;'}));}}}]});const code=result.outputFiles[0].text.replaceAll('from "react"',`from ${JSON.stringify(pathToFileURL(resolve('node_modules/react/index.js')).href)}`).replaceAll('from "react/jsx-runtime"',`from ${JSON.stringify(pathToFileURL(resolve('node_modules/react/jsx-runtime.js')).href)}`);const file=join(directory,'ui.mjs');await writeFile(file,code);ui=await import(pathToFileURL(file));});
after(async()=>{if(directory)await rm(directory,{recursive:true,force:true});});
const chapters=[{id:'c1',title:'生活需求',plan_section:'先明确家庭需求',reference_beat_ids:['question'],custom:{retained:true}},{id:'c2',title:'逐项核对',plan_section:'用一致标准比较',reference_beat_ids:['evidence']}];
const value={schema_version:2,target_seconds:300,plan_id:'plan-original',source:{reference_id:'ref-1',revision:4},chapters,shots:Array.from({length:30},(_,i)=>({id:`s${i+1}`,chapter_id:i<15?'c1':'c2',plan_section:i<15?'先明确家庭需求':'用一致标准比较',reference_beat_ids:[i<15?'question':'evidence'],voiceover:'一句话',visual_direction:'拍摄对应的生活场景',caption:'场景示例',fact_refs:[{id:'fact-1',revision:2}],asset_id:'footage',start:0,duration:10,custom:{retained:true}})),reference_beats:[{id:'question',label:'问题引入',description:'先提出与家庭生活有关的问题',evidence_basis:'asset_asr_and_sampled_frames'}]};
const plan={id:'plan-original',title:'家庭如何比较户型',planning:{hook:'看户型之前，先想一家人的一天。',outline:['先明确家庭需求','用一致标准比较'],cta:'用这份清单核对下一套房。'},source:{planning_reference:{title:'原片家庭动线讲解',evidence_basis:'asset_asr_and_sampled_frames',analysis:{summary:'提问后以生活场景给出依据'}}}};
// Catches replacing chapter objects and stripping frozen source or future metadata while changing a title.
test('chapter edits preserve its original identity and untouched storyboard provenance',()=>{
 const result=ui.updateLongformChapter(value,'c1',{title:'从家庭的一天开始'});assert.ok(result);assert.deepEqual(result.chapters[0],{...chapters[0],title:'从家庭的一天开始'});assert.deepEqual(result.source,{reference_id:'ref-1',revision:4});assert.equal(result.shots[0].id,'s1');assert.deepEqual(result.shots[0].fact_refs,[{id:'fact-1',revision:2}]);
});
// Catches edited narration being visible in one control but the saved script continuing to contain stale prose.
test('a shot narration edit updates the saved script while retaining source, chapter and fact mappings',()=>{
 const result=ui.updateLongformScript(value,0,{voiceover:'改后的口播',caption:'改后的字幕'});assert.ok(result);assert.equal(result.storyboard.shots[0].id,'s1');assert.equal(result.storyboard.shots[0].chapter_id,'c1');assert.deepEqual(result.storyboard.shots[0].reference_beat_ids,['question']);assert.deepEqual(result.storyboard.shots[0].custom,{retained:true});assert.equal(result.script,'改后的口播'+'\n一句话'.repeat(29),'must match Python narration_text: one LF between trimmed shot narrations');assert.deepEqual(value.shots[0].voiceover,'一句话');
});
// Catches omitting target, script length or material gaps when judging whether a long-form plan is shootable.
test('long-form summary counts real chapter, shot, narration, duration and asset coverage',()=>{
 assert.deepEqual(ui.longformSummary(value,[{id:'footage',kind:'video',rights:'authorized',rights_evidence:'许可',origin_type:'actual'}],false),{chapterCount:2,shotCount:30,totalSeconds:300,targetSeconds:300,wordCount:90,missingAssets:0,missingVoiceovers:0});
 const withGaps={...value,shots:[{...value.shots[0],asset_id:'',voiceover:''}]};const summary=ui.longformSummary(withGaps,[],false);assert.equal(summary.missingAssets,1);assert.equal(summary.missingVoiceovers,1);
});
test('the displayed narration budget counts Chinese characters exactly as the server does',()=>{
 const summary=ui.longformSummary({...value,shots:[{...value.shots[0],voiceover:'中文 abc123，标点。'}]},[],false);assert.equal(summary.wordCount,4,'letters, digits, whitespace and punctuation do not count toward the Chinese narration budget');
});
// Catches unanalysed references being labeled as observed viral structure rather than an editable hypothesis.
test('continuity exposes frozen plan requirements and distinguishes observed reference evidence from assumptions',()=>{
 const actual=ui.continuityContext(value,plan);assert.ok(actual);assert.equal(actual.hook,'看户型之前，先想一家人的一天。');assert.equal(actual.cta,'用这份清单核对下一套房。');assert.equal(actual.referenceTitle,'原片家庭动线讲解');assert.equal(actual.evidenceBasis,'asset_asr_and_sampled_frames');assert.equal(actual.beats[0].label,'问题引入');
 const assumed=ui.continuityContext({...value,reference_beats:[]},{...plan,source:{planning_reference:{title:'未拆解原片',evidence_basis:'title_only'}}});assert.equal(assumed.evidenceBasis,'structure_hypothesis');assert.equal(assumed.beats.length,0);
});
test('the chapter editor places narration, planned structure and reference mapping in one readable workspace',()=>{
 const html=renderToStaticMarkup(React.createElement(ui.LongformScriptEditor,{value,assets:[],isTest:false,plan,onChange(){}}));assert.match(html,/章节与镜头协同编辑/);assert.match(html,/生活需求/);assert.match(html,/逐项核对/);assert.match(html,/先明确家庭需求/);assert.match(html,/问题引入/);assert.match(html,/用这份清单核对下一套房/);assert.match(html,/素材缺口/);assert.match(html,/aria-label="镜头 1 口播"/);assert.doesNotMatch(html,/<code>|复制.*JSON|爆款概率/);
});
