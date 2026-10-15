import {test} from 'node:test';
import assert from 'node:assert/strict';
import {createServer} from 'node:http';
import {ConversationDraftStore} from '../src/draft-store.js';
function deferred(){let resolve;const promise=new Promise(r=>resolve=r);return{promise,resolve};}
async function fixture({holdPatch=false,holdSend=false}={}){
 let stored='',posted='';const events=[];const patchStarted=deferred(),releasePatch=deferred(),sendStarted=deferred(),releaseSend=deferred();
 const server=createServer(async(req,res)=>{const chunks=[];for await(const chunk of req)chunks.push(chunk);const body=JSON.parse(Buffer.concat(chunks).toString());
 if(req.url==='/draft'){patchStarted.resolve();if(holdPatch)await releasePatch.promise;stored=body.draft;events.push(['patch',stored]);}
 else {sendStarted.resolve();if(holdSend)await releaseSend.promise;posted=body.text;stored='';events.push(['send',posted]);}
 res.writeHead(200,{'Content-Type':'application/json'});res.end(JSON.stringify(req.url==='/draft'?{draft:stored}:{job_id:'job-1'}));});
 await new Promise(r=>server.listen(0,'127.0.0.1',r));const url=`http://127.0.0.1:${server.address().port}`;
 const request=async(path,body)=>{const response=await fetch(url+path,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});return response.json();};
 return{store:new ConversationDraftStore({patch:(_id,draft)=>request('/draft',{draft}),send:(_id,text)=>request('/send',{text}),delay:60000}),server,patchStarted,releasePatch,sendStarted,releaseSend,events,getStored:()=>stored,getPosted:()=>posted};
}
test('a late pre-send draft write finishes before message clear, then the next typed draft remains',async()=>{
 const f=await fixture({holdPatch:true,holdSend:true});try{f.store.hydrate('c1','');f.store.set('c1','原问题');assert.equal(f.store.get('c1'),'原问题');const save=f.store.flush('c1');await f.patchStarted.promise;const sending=f.store.send('c1','原问题');f.store.set('c1','下一条尚未发送');f.releasePatch.resolve();await save;await f.sendStarted.promise;f.releaseSend.resolve();await sending;await f.store.flush('c1');assert.equal(f.getPosted(),'原问题');assert.equal(f.store.get('c1'),'下一条尚未发送');assert.equal(f.getStored(),'下一条尚未发送');assert.deepEqual(f.events,[['patch','原问题'],['send','原问题'],['patch','下一条尚未发送']]);}finally{f.store.clear();f.releasePatch.resolve();f.releaseSend.resolve();f.server.close();}
});
test('flushAll saves distinct server-side conversation drafts before a project switch',async()=>{
 const f=await fixture();try{f.store.hydrate('a','');f.store.hydrate('b','原有草稿');f.store.set('a','项目A未发');f.store.set('b','项目B未发');await f.store.flushAll();assert.equal(f.store.get('a'),'项目A未发');assert.equal(f.store.get('b'),'项目B未发');assert.equal(f.events.filter(e=>e[0]==='patch').length,2);}finally{f.store.clear();f.server.close();}
});
test('sending a quick prompt preserves a separately prepared composer draft',async()=>{
 const f=await fixture();try{f.store.hydrate('c1','我的问题草稿');await f.store.send('c1','请汇总项目');await f.store.flush('c1');assert.equal(f.getPosted(),'请汇总项目');assert.equal(f.getStored(),'我的问题草稿');assert.equal(f.store.get('c1'),'我的问题草稿');}finally{f.store.clear();f.server.close();}
});

test('a running-task control uses its own endpoint and preserves text typed during delivery',async()=>{
 const controlStarted=deferred(),releaseControl=deferred(),requests=[];
 const f=await fixture({holdPatch:true});
 try{
  f.store.hydrate('c1','');f.store.set('c1','请改成下午');const patch=f.store.flush('c1');await f.patchStarted.promise;
  const sending=f.store.send('c1','请改成下午',async(id,text)=>{requests.push({id,text});controlStarted.resolve();await releaseControl.promise;return{control_id:'ctrl1',job_id:'job1'};});
  f.store.set('c1','接着整理客户喜好');f.releasePatch.resolve();await patch;releaseControl.resolve();
  assert.deepEqual(await sending,{control_id:'ctrl1',job_id:'job1'});await f.store.flush('c1');
  assert.deepEqual(requests,[{id:'c1',text:'请改成下午'}]);assert.equal(f.getPosted(),'');assert.equal(f.getStored(),'接着整理客户喜好');
 }finally{f.store.clear();f.releasePatch.resolve();releaseControl.resolve();f.server.close();}
});

test('a successful control also clears the persisted draft when no newer text was typed',async()=>{
 const f=await fixture();try{
  f.store.hydrate('c1','');f.store.set('c1','请改成下午');await f.store.flush('c1');
  await f.store.send('c1','请改成下午',async()=>({control_id:'ctrl1'}));await f.store.flush('c1');
  assert.equal(f.store.get('c1'),'');assert.equal(f.getStored(),'');assert.equal(f.getPosted(),'');
 }finally{f.store.clear();f.server.close();}
});

test('a rejected control restores only its unchanged draft and leaves newer typing intact',async()=>{
 const f=await fixture();try{
  f.store.hydrate('c1','纠正旧任务');
  await assert.rejects(f.store.send('c1','纠正旧任务',async()=>{throw new Error('任务已结束');}),/任务已结束/);
  assert.equal(f.store.get('c1'),'纠正旧任务');
  const started=deferred(),release=deferred();const pending=f.store.send('c1','纠正旧任务',async()=>{started.resolve();await release.promise;throw new Error('网络中断');});
  await started.promise;f.store.set('c1','保留新输入');release.resolve();await assert.rejects(pending,/网络中断/);await f.store.flush('c1');
  assert.equal(f.store.get('c1'),'保留新输入');assert.equal(f.getStored(),'保留新输入');
 }finally{f.store.clear();f.server.close();}
});
