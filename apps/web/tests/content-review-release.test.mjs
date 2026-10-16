import {after,before,test} from 'node:test';
import assert from 'node:assert/strict';
import {existsSync} from 'node:fs';
import {mkdtemp,rm,writeFile} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import {join,resolve} from 'node:path';
import {pathToFileURL} from 'node:url';
import {build} from 'esbuild';
let ui,dir;
before(async()=>{
 dir=await mkdtemp(join(tmpdir(),'content-review-release-'));
 const source=resolve('src/content-review-release.ts');
 if(!existsSync(source)){ui={reviewEvidenceMatches:()=>false,reviewFeedbackBody:()=>null,matchingReleaseJobs:()=>[],releaseResultState:()=>({label:'',tone:'neutral'}),releasePlatformAccounts:()=>[]};return;}
 const result=await build({entryPoints:[source],bundle:true,write:false,platform:'node',format:'esm'});
 const target=join(dir,'model.mjs');await writeFile(target,result.outputFiles[0].text);ui=await import(pathToFileURL(target).href);
});
after(async()=>{await rm(dir,{recursive:true,force:true});});
const content={id:'content-1',version:3,script:'南向客厅。请看实际采光。',render_asset_id:'render-current',channel:'抖音',status:'rendered'};
const review={content,shots:[{id:'shot-a',voiceover:'南向客厅。',caption:'客厅',asset_id:'asset-a'}],facts:[{id:'fact-a',expected_revision:2,revision:2,valid:true}],assets:[{id:'asset-a',valid:true}],feedback:[],release_frozen:false};
// A stale review must never authorize a replacement video or a different content selection.
test('review evidence only authorizes the exact loaded content and render with usable facts and rights',()=>{
 assert.equal(ui.reviewEvidenceMatches(review,content),true);
 for(const value of [{...content,id:'content-other'},{...content,render_asset_id:'render-replaced'}])assert.equal(ui.reviewEvidenceMatches(review,value),false);
 assert.equal(ui.reviewEvidenceMatches({...review,facts:[{...review.facts[0],valid:false}]},content),false);
 assert.equal(ui.reviewEvidenceMatches({...review,assets:[{...review.assets[0],valid:false}]},content),false);
});
// Feedback cannot silently move from one shot or original sentence to another.
test('review feedback preserves an exact shot and sentence and rejects unknown targets',()=>{
 assert.deepEqual(ui.reviewFeedbackBody(review,'  请确认朝向来源  ','shot-a','南向客厅。'),{note:'请确认朝向来源',shot_id:'shot-a',sentence:'南向客厅。',expected_render_asset_id:'render-current'});
 assert.deepEqual(ui.reviewFeedbackBody(review,'补充全片营销范围','','请看实际采光。'),{note:'补充全片营销范围',sentence:'请看实际采光。',expected_render_asset_id:'render-current'});
 assert.throws(()=>ui.reviewFeedbackBody(review,'意见','another-shot',''),/镜头/);
 assert.throws(()=>ui.reviewFeedbackBody(review,'意见','shot-a','请看实际采光。'),/原文/);
 assert.throws(()=>ui.reviewFeedbackBody(review,'   ','',''),/意见/);
});
// Old release or another content's platform task must not appear as the current submission.
test('release task history is scoped to exact content release and selected platform',()=>{
 const job=(id,cid='content-1',release='release-3',platform='douyin')=>({id,kind:'platform_publish',status:'succeeded',result:{content_version_id:cid,release_id:release,platform,status:'submitted'}});
 assert.deepEqual(ui.matchingReleaseJobs([job('old','content-1','release-2'),job('other','other-content'),job('platform','content-1','release-3','xiaohongshu'),job('current')],content,'release-3','douyin').map(j=>j.id),['current']);
});
// Submission success and the live receipt are distinct, so a finished worker never means published.
test('release state tells apart draft submission pending review and human reconciliation',()=>{
 assert.equal(ui.releaseResultState({status:'succeeded',result:{status:'submitted'}}).label,'已提交平台，待核对回执');
 assert.equal(ui.releaseResultState({status:'succeeded',result:{status:'draft_saved'}}).label,'已保存到平台草稿箱');
 assert.equal(ui.releaseResultState({status:'succeeded',result:{status:'review_pending'}}).label,'等待平台审核');
 assert.equal(ui.releaseResultState({status:'succeeded',result:{status:'reconciliation_required'}}).label,'需要人工核对结果');
 assert.equal(ui.releaseResultState({status:'running'}).label,'平台正在处理');
});
// Channel aliases must select only an eligible platform; an unsupported account cannot be submitted.
test('release account selection respects channel aliases and unavailable platforms',()=>{
 const accounts=[{platform:'douyin',name:'抖音',supported:true},{platform:'bilibili',name:'B站',supported:true},{platform:'weixin-channels',name:'微信视频号',supported:true},{platform:'douyin-disabled',name:'抖音',supported:false}];
 assert.deepEqual(ui.releasePlatformAccounts(accounts,content).map(a=>a.platform),['douyin']);
 assert.deepEqual(ui.releasePlatformAccounts(accounts,{...content,channel:'哔哩哔哩'}).map(a=>a.platform),['bilibili']);
 assert.deepEqual(ui.releasePlatformAccounts(accounts,{...content,channel:'视频号'}).map(a=>a.platform),['weixin-channels']);
});
// Reloading during an already authorized submit recovers that exact task instead of submitting again.
test('pending release tasks resume only through their explicit safe request binding',()=>{
 const active={id:'active-current',kind:'platform_publish',status:'running',result:{},publication_request:{platform:'douyin',content_version_id:'content-1',release_id:'release-3'}};
 assert.deepEqual(ui.matchingReleaseJobs([active,{...active,id:'active-other',publication_request:{...active.publication_request,content_version_id:'other'}}],content,'release-3','douyin').map(j=>j.id),['active-current']);
});
