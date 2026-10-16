import {test} from 'node:test';
import assert from 'node:assert/strict';
import {existsSync} from 'node:fs';
const path=new URL('../src/batch-intake.js',import.meta.url);
// Missing-feature fallback ensures RED fails on observable behavior rather than module resolution.
const feature=existsSync(path)?await import(path):{UploadQueue:class{addFiles(){}setOrigin(){}cancel(){return false;}retry(){}snapshot(){return [];}async run(){}},applyBatchRights:async()=>[],buildFactReviewRows:()=>[],verifySelectedFacts:async()=>[]};
const {UploadQueue,applyBatchRights,buildFactReviewRows,verifySelectedFacts}=feature;
const file=(name,size=10)=>({name,size,lastModified:1,type:'image/jpeg'});
const asset=(id,extra={})=>({id,name:id,mime_type:'image/jpeg',kind:'image',rights:'pending',rights_evidence:'',rights_revision:1,origin_type:'actual',metadata_json:{},...extra});
const fact=(id,extra={})=>({id,label:'建筑面积',semantic_key:'area',value:'98',unit:'㎡',source:'楼书第2页',status:'unverified',revision:2,required:true,...extra});
// Catches abort-on-first-failure and reuploading completed files during retry.
test('a batch keeps partial successes and retries only the failed file',async()=>{
 const queue=new UploadQueue();queue.addFiles([file('first.jpg'),file('second.jpg'),file('third.jpg')]);for(const row of queue.snapshot())queue.setOrigin(row.id,'actual');
 let secondFails=true;const uploaded=[];const upload=async(value)=>{uploaded.push(value.name);if(value.name==='second.jpg'&&secondFails)throw new Error('连接中断');return asset(value.name);};
 await queue.run({upload});assert.deepEqual(queue.snapshot().map(row=>[row.file.name,row.status]),[['first.jpg','succeeded'],['second.jpg','failed'],['third.jpg','succeeded']]);
 secondFails=false;queue.retry(queue.snapshot()[1].id);await queue.run({upload});assert.deepEqual(uploaded,['first.jpg','second.jpg','third.jpg','second.jpg']);assert.deepEqual(queue.snapshot().map(row=>row.status),['succeeded','succeeded','succeeded']);
});
// Catches sending cancelled queued files while another upload runs.
test('queued files can be cancelled while the active upload completes',async()=>{
 const queue=new UploadQueue();queue.addFiles([file('active.jpg'),file('cancel.jpg')]);for(const row of queue.snapshot())queue.setOrigin(row.id,'actual');let finish;const pending=new Promise(resolve=>{finish=resolve;});
 const task=queue.run({upload:async()=>{await pending;return asset('saved');}});const rows=queue.snapshot();assert.equal(queue.cancel(rows[0]?.id),false);assert.equal(queue.cancel(rows[1]?.id),true);finish();await task;assert.deepEqual(queue.snapshot().map(row=>row.status),['succeeded','cancelled']);
});
// Catches implicit origin selection, duplicate files or success without a saved record.
test('origin is explicit and selecting the same file again never queues it twice',async()=>{
 const queue=new UploadQueue();queue.addFiles([file('same.jpg'),file('same.jpg')]);let sent=false;await queue.run({upload:async()=>{sent=true;return asset('bad');}});
 assert.equal(queue.snapshot().length,1);assert.equal(sent,false);assert.equal(queue.snapshot()[0]?.status,'failed');assert.match(queue.snapshot()[0]?.error||'',/来源/);
 queue.retry(queue.snapshot()[0].id);queue.setOrigin(queue.snapshot()[0].id,'ai_generated');await queue.run({upload:async(_file,origin,onProgress)=>{assert.equal(origin,'ai_generated');onProgress(60);return asset('saved');}});queue.addFiles([file('same.jpg')]);assert.equal(queue.snapshot().length,1);assert.equal(queue.snapshot()[0]?.asset?.id,'saved');
});
test('an upload without a saved asset id remains failed and retryable',async()=>{
 const queue=new UploadQueue();queue.addFiles([file('not-saved.jpg')]);for(const row of queue.snapshot())queue.setOrigin(row.id,'actual');await queue.run({upload:async()=>({})});assert.equal(queue.snapshot()[0]?.status,'failed');assert.match(queue.snapshot()[0]?.error||'',/保存/);
});
// Catches authority expansion from selected files and implicit opt-in to AI.
test('shared evidence applies only to selected files and AI stays off unless selected',async()=>{
 const saved=[];const result=await applyBatchRights([asset('one'),asset('two'),asset('three')],['one','three'],{canReview:true,confirmed:true,evidence:'摄制方书面许可',usage:'抖音本项目介绍',allowAI:false},{authorize:async(id,body)=>{saved.push([id,body]);return asset(id,{rights:'authorized'});}});
 assert.deepEqual(saved,[['one',{rights:'authorized',evidence:'摄制方书面许可\n用途：抖音本项目介绍',model_processing_authorized:false,expected_rights_revision:1}],['three',{rights:'authorized',evidence:'摄制方书面许可\n用途：抖音本项目介绍',model_processing_authorized:false,expected_rights_revision:1}]]);assert.deepEqual(result.map(row=>row.id),['one','three']);
});
// Catches bypassing reviewer permission or source/use confirmation.
test('no rights writes occur without review permission and explicit source/use confirmation',async()=>{
 let wrote=false;const api={authorize:async()=>{wrote=true;return asset('one');}};
 for(const options of [{canReview:false,confirmed:true,evidence:'许可',usage:'项目展示'},{canReview:true,confirmed:false,evidence:'许可',usage:'项目展示'},{canReview:true,confirmed:true,evidence:'',usage:'项目展示'},{canReview:true,confirmed:true,evidence:'许可',usage:''}])await assert.rejects(()=>applyBatchRights([asset('one')],['one'],options,api));assert.equal(wrote,false);
});
test('rights failures remain retryable while other authorized files are reported',async()=>{
 const result=await applyBatchRights([asset('one'),asset('two')],['one','two'],{canReview:true,confirmed:true,evidence:'许可',usage:'项目展示',allowAI:true},{authorize:async(id,body)=>{assert.equal(body.model_processing_authorized,true);if(id==='two')throw new Error('权限已变更');return asset(id,{rights:'authorized'});}});assert.equal(result[0]?.asset?.rights,'authorized');assert.equal(result[1]?.error,'权限已变更');assert.equal(result[1]?.asset,undefined);
});
// Catches hidden conflicting sources or research material becoming project fact evidence.
test('fact review shows missing fields and conflicting sources without verifying or replacing facts',()=>{
 const facts=[fact('area'),fact('layout',{semantic_key:'layout',label:'户型布局',value:'',unit:'',source:''})];const records=[asset('brochure',{name:'楼书.pdf',metadata_json:{fact_candidates:[{semantic_key:'area',label:'建筑面积',value:'102',unit:'㎡',source:'楼书第3页',source_excerpt:'建筑面积102㎡'},{semantic_key:'layout',label:'户型布局',value:'3室2厅',unit:'',source:'楼书第4页'}]}}),asset('reference',{origin_type:'reference',metadata_json:{fact_candidates:[{semantic_key:'area',value:'200',unit:'㎡',source:'竞品'}]}})];
 const rows=buildFactReviewRows(facts,records);assert.equal(rows[0]?.conflict,true);assert.equal(rows[0]?.candidates.length,1);assert.equal(rows[0]?.candidates[0]?.source,'楼书第3页');assert.equal(rows[1]?.missing,true);assert.equal(rows[1]?.canVerify,false);assert.equal(facts[0].status,'unverified');assert.equal(facts[0].value,'98');
});
// Catches stale selected revisions and writes to unselected facts.
test('bulk verification requires human confirmation and verifies only current selected saved revisions',async()=>{
 const facts=[fact('one'),fact('two')],writes=[];const selected=[{id:'one',revision:2}];const api={loadFacts:async()=>facts,verify:async(id,body)=>{writes.push([id,body]);return fact(id,{status:'verified',revision:3});}};
 await assert.rejects(()=>verifySelectedFacts(selected,{canReview:false,confirmed:true},api));await assert.rejects(()=>verifySelectedFacts(selected,{canReview:true,confirmed:false},api));assert.equal(writes.length,0);
 const stale=await verifySelectedFacts([{id:'one',revision:1}],{canReview:true,confirmed:true},api);assert.match(stale[0]?.error||'',/版本/);assert.equal(writes.length,0);
 const result=await verifySelectedFacts(selected,{canReview:true,confirmed:true},api);assert.deepEqual(writes,[['one',{confirmed:true,expected_revision:2}]]);assert.equal(result[0]?.fact?.status,'verified');
});
// Catches placeholder sources becoming eligible and malformed metadata crashing the review UI.
test('placeholder evidence and malformed extraction candidates cannot become verifiable facts',()=>{
 const rows=buildFactReviewRows([fact('none',{source:'—'}),fact('null',{source:'n/a'})],[asset('broken',{metadata_json:{fact_candidates:[{semantic_key:'area',value:{number:102},unit:'㎡',source:'坏元数据'},{semantic_key:'area',value:'102',unit:'㎡',source:'真实来源'}]}})]);
 assert.equal(rows[0]?.canVerify,false);assert.equal(rows[1]?.canVerify,false);assert.equal(rows[0]?.candidates.length,1);assert.equal(rows[0]?.candidates[0]?.value,'102');
});
// Catches success responses for the wrong asset leaking into selected-file authorization.
test('a rights response for another asset is reported as failure',async()=>{
 const result=await applyBatchRights([asset('selected')],['selected'],{canReview:true,confirmed:true,evidence:'许可',usage:'项目展示'},{authorize:async()=>asset('different',{rights:'authorized'})});assert.equal(result[0]?.asset,undefined);assert.match(result[0]?.error||'',/确认/);
});

// Catches using one common revision for the entire selection or resurrecting externally revoked rights.
test('each selected asset carries its own loaded rights revision and stale revocation requires refresh',async()=>{
 const current={one:{rights:'pending',revision:2},two:{rights:'revoked',revision:5}},bodies=[];
 const result=await applyBatchRights([asset('one',{rights_revision:2}),asset('two',{rights_revision:4})],['one','two'],{canReview:true,confirmed:true,evidence:'许可',usage:'项目展示',allowAI:true},{authorize:async(id,body)=>{bodies.push(body);if(body.expected_rights_revision!==current[id].revision){const conflict=new Error('素材授权版本已变化');conflict.status=409;throw conflict;}current[id].rights=body.rights;current[id].revision++;return asset(id,{rights:'authorized',rights_revision:current[id].revision});}});
 assert.equal(current.one.rights,'authorized');assert.equal(current.two.rights,'revoked');assert.deepEqual(bodies.map(body=>body.expected_rights_revision),[2,4]);assert.equal(result[1]?.requiresRefresh,true);assert.match(result[1]?.error||'',/版本/);
});
// Catches guessing a version when an old response is missing the concurrency token.
test('missing rights revisions block that asset before any authorization write',async()=>{
 let wrote=false;const record=asset('legacy');delete record.rights_revision;const result=await applyBatchRights([record],['legacy'],{canReview:true,confirmed:true,evidence:'许可',usage:'项目展示'},{authorize:async()=>{wrote=true;return asset('legacy',{rights:'authorized'});}});assert.equal(wrote,false);assert.equal(result[0]?.requiresRefresh,true);assert.match(result[0]?.error||'',/刷新/);
});
