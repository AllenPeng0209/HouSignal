import assert from 'node:assert/strict';
import test from 'node:test';
import { AssistantDrafts } from '../src/domain/assistant-drafts';
const deferred=()=>{let resolve!:(v?:unknown)=>void,reject!:(e:unknown)=>void;const promise=new Promise((yes,no)=>{resolve=yes;reject=no;});return {promise,resolve,reject};};
test('typing during successful send survives server clearing the previous draft',async()=>{
 const request=deferred(),saved:string[]=[];const store=new AssistantDrafts({patch:async(_id,text)=>{saved.push(text);},send:async()=>request.promise,delay:60000});
 store.hydrate('a','旧问题');const sending=store.send('a','旧问题');store.set('a','下一条问题');request.resolve({job_id:'j'});await sending;await store.flush('a');
 assert.equal(store.get('a'),'下一条问题');assert.deepEqual(saved,['下一条问题']);store.close();
});
test('failed send restores old draft unless user has typed a replacement',async()=>{
 const request=deferred();const store=new AssistantDrafts({patch:async()=>{},send:async()=>request.promise,delay:60000});store.hydrate('a','保留我');
 const sending=store.send('a','保留我');request.reject(new Error('网络中断'));await assert.rejects(sending);assert.equal(store.get('a'),'保留我');store.close();
});
test('switching conversations never overwrites local unsaved text with stale server draft',()=>{
 const store=new AssistantDrafts({patch:async()=>{},send:async()=>({}),delay:60000});store.hydrate('a','服务端旧值');store.set('a','新值');store.hydrate('b','另一个对话');store.hydrate('a','服务端旧值');
 assert.equal(store.get('a'),'新值');assert.equal(store.get('b'),'另一个对话');store.close();
});
test('failed autosave remains dirty and can retry without losing input',async()=>{
 let failed=true;const saved:string[]=[];const store=new AssistantDrafts({patch:async(_id,text)=>{if(failed)throw new Error('offline');saved.push(text);},send:async()=>({}),delay:60000});store.set('a','未保存草稿');
 await assert.rejects(store.flush('a'));failed=false;await store.flush('a');assert.deepEqual(saved,['未保存草稿']);store.close();
});
test('flush drains edits typed while a previous autosave is in flight',async context=>{
 const request=deferred();let saves=0;const values:string[]=[];const store=new AssistantDrafts({patch:async(_id,text)=>{values.push(text);if(++saves===1)await request.promise;},send:async()=>({}),delay:60000});
 context.after(()=>store.close());store.set('a','先输入');const flushing=store.flush('a');await Promise.resolve();store.set('a','随后输入');request.resolve();await flushing;assert.deepEqual(values,['先输入','随后输入']);
});
test('failed send never replaces new typing with the original question',async()=>{
 const request=deferred();const store=new AssistantDrafts({patch:async()=>{},send:async()=>request.promise,delay:60000});store.hydrate('a','旧问题');const sending=store.send('a','旧问题');store.set('a','新的问题');request.reject(new Error('offline'));await assert.rejects(sending);assert.equal(store.get('a'),'新的问题');store.close();
});
