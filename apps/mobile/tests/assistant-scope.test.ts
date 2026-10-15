import assert from 'node:assert/strict';
import test from 'node:test';
import { RequestScope, pollChat } from '../src/domain/assistant-scope';
test('late conversation responses cannot become current after a switch',()=>{
 const scope=new RequestScope();const first=scope.begin();const next=scope.begin();assert.equal(scope.current(first),false);assert.equal(scope.current(next),true);scope.close();assert.equal(scope.current(next),false);
});
test('chat polling rejects another project job before showing preview',async()=>{
 const seen:string[]=[];await assert.rejects(pollChat(async()=>({id:'j',property_id:'other',status:'running',result:{preview_text:'secret'}}) as any,'j','mine',{onProgress:j=>seen.push(j.result.preview_text),interval:0}),/项目/);assert.deepEqual(seen,[]);
});
test('terminal chat polling reloads final messages through caller and stops after completion',async()=>{
 let calls=0;const seen:string[]=[];const job=await pollChat(async()=>({id:'j',property_id:'mine',status:++calls===1?'running':'succeeded',result:{preview_text:'经过检查的草稿'}}) as any,'j','mine',{onProgress:j=>seen.push(j.status),interval:0});assert.equal(job.status,'succeeded');assert.equal(calls,2);assert.deepEqual(seen,['running','succeeded']);
});
test('leaving chat aborts polling with no further updates',async()=>{
 const controller=new AbortController();controller.abort();let calls=0;await assert.rejects(pollChat(async()=>{calls++;return {} as any;},'j','mine',{signal:controller.signal}),/停止/);assert.equal(calls,0);
});
