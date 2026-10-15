import {test} from 'node:test';
import assert from 'node:assert/strict';
import {createServer} from 'node:http';
import {setTimeout as delay,setImmediate as nextTurn} from 'node:timers/promises';

async function fixture(handler) {
  const server=createServer(handler);
  await new Promise(r=>server.listen(0,'127.0.0.1',r));
  return {url:`http://127.0.0.1:${server.address().port}`,close:()=>server.close()};
}
const job=(status,extra={})=>({id:'j1',kind:'chat',status,progress:status==='succeeded'?1:0.05,result:{},created_at:'2026-10-13T01:00:00Z',updated_at:'2026-10-13T01:00:00Z',...extra});

// A delayed first status must not let the unknown job kind inherit a ten-minute chat cutoff.
for(const firstStatusDelayed of [false,true])test(`chat keeps waiting without a default deadline (first status delayed: ${firstStatusDelayed})`,async t=>{
 const {watchJob}=await import('../src/job-watch.js');
 t.mock.timers.enable({apis:['setTimeout']});
 let events;const stream=new ReadableStream({start(c){events=c;}});
 t.mock.method(globalThis,'fetch',async(_path,{signal})=>{
   signal.addEventListener('abort',()=>events.error(signal.reason),{once:true});
   return new Response(stream,{headers:{'content-type':'text/event-stream'}});
 });
 let outcome;const waiting=watchJob('j1');waiting.then(value=>{outcome={value};},error=>{outcome={error};});
 const send=value=>events.enqueue(new TextEncoder().encode(`event: status\ndata: ${JSON.stringify(value)}\n\n`));
 if(firstStatusDelayed){t.mock.timers.tick(600001);await nextTurn();assert.equal(outcome,undefined,'a job must be identified before choosing a default deadline');}
 send(job('running'));await nextTurn();
 t.mock.timers.tick(3600000);await nextTurn();assert.equal(outcome,undefined,'a chat has no automatic total wait cutoff');
 send(job('succeeded',{result:{preview_text:'完整回答'}}));
 assert.equal((await waiting).result.preview_text,'完整回答');
});

for(const kind of [undefined,''])test(`an unidentified job status cannot expire a later chat (kind: ${JSON.stringify(kind)})`,async t=>{
 const {watchJob}=await import('../src/job-watch.js');t.mock.timers.enable({apis:['setTimeout']});
 let events;const stream=new ReadableStream({start(c){events=c;}});
 t.mock.method(globalThis,'fetch',async(_path,{signal})=>{
   signal.addEventListener('abort',()=>events.error(signal.reason),{once:true});
   return new Response(stream,{headers:{'content-type':'text/event-stream'}});
 });
 let outcome;const waiting=watchJob('j1');waiting.then(value=>{outcome={value};},error=>{outcome={error};});
 const send=value=>events.enqueue(new TextEncoder().encode(`event: status\ndata: ${JSON.stringify(value)}\n\n`));
 send(job('running',{kind}));await nextTurn();
 t.mock.timers.tick(600001);await nextTurn();assert.equal(outcome,undefined);
 send(job('succeeded'));assert.equal((await waiting).status,'succeeded');
});

test('chat stream recovery keeps waiting beyond the former deadline',async t=>{
 const {watchJob}=await import('../src/job-watch.js');t.mock.timers.enable({apis:['setTimeout']});
 let polls=0,finish;
 t.mock.method(globalThis,'fetch',async(path,{signal})=>{
   if(path.endsWith('/events'))return new Response(`event: status\ndata: ${JSON.stringify(job('running'))}\n\n`,{headers:{'content-type':'text/event-stream'}});
   polls++;
   if(polls===1)return new Response(JSON.stringify(job('running')),{headers:{'content-type':'application/json'}});
   return new Promise((resolve,reject)=>{
     finish=()=>resolve(new Response(JSON.stringify(job('succeeded',{result:{preview_text:'断线恢复后的完整回答'}})),{headers:{'content-type':'application/json'}}));
     signal.addEventListener('abort',()=>reject(signal.reason),{once:true});
   });
 });
 let outcome;const waiting=watchJob('j1');waiting.then(value=>{outcome={value};},error=>{outcome={error};});
 await nextTurn();t.mock.timers.tick(500);await nextTurn();
 assert.equal(polls,2);t.mock.timers.tick(3600000);await nextTurn();assert.equal(outcome,undefined);
 finish();assert.equal((await waiting).result.preview_text,'断线恢复后的完整回答');
});

test('a normal production job retains its default deadline',async t=>{
 const {watchJob}=await import('../src/job-watch.js');t.mock.timers.enable({apis:['setTimeout']});
 let events;const stream=new ReadableStream({start(c){events=c;}});
 t.mock.method(globalThis,'fetch',async(_path,{signal})=>{
   signal.addEventListener('abort',()=>events.error(signal.reason),{once:true});
   return new Response(stream,{headers:{'content-type':'text/event-stream'}});
 });
 const waiting=watchJob('j1');const rejected=assert.rejects(waiting,e=>e.status===408);
 events.enqueue(new TextEncoder().encode(`event: status\ndata: ${JSON.stringify(job('running',{kind:'content_skill'}))}\n\n`));await nextTurn();
 t.mock.timers.tick(600001);await rejected;
});

test('a real longform production update extends only the default wait deadline',async t=>{
 const {watchJob}=await import('../src/job-watch.js');
 t.mock.timers.enable({apis:['setTimeout']});
 let events;const stream=new ReadableStream({start(c){events=c;}});
 t.mock.method(globalThis,'fetch',async()=>new Response(stream,{headers:{'content-type':'text/event-stream'}}));
 let outcome;const waiting=watchJob('j1');waiting.then(value=>{outcome={value};},error=>{outcome={error};});
 const send=value=>events.enqueue(new TextEncoder().encode(`event: status\ndata: ${JSON.stringify(value)}\n\n`));
 send(job('running',{kind:'generate_content',result:{longform_progress:{target_seconds:480,total_chapters:8,completed_chapters:2}}}));await nextTurn();
 t.mock.timers.tick(600001);await nextTurn();assert.equal(outcome,undefined,'longform chapter generation must keep waiting after ten minutes');
 send(job('succeeded',{kind:'generate_content',result:{content_version_id:'long-content'}}));await nextTurn();
 assert.ifError(outcome?.error);assert.equal((await waiting).result.content_version_id,'long-content');
});

test('reference analysis keeps waiting while later sampling batches run',async t=>{
 const {watchJob}=await import('../src/job-watch.js');t.mock.timers.enable({apis:['setTimeout']});
 let events;const stream=new ReadableStream({start(c){events=c;}});
 t.mock.method(globalThis,'fetch',async()=>new Response(stream,{headers:{'content-type':'text/event-stream'}}));
 let outcome;const waiting=watchJob('j1');waiting.then(value=>{outcome={value};},error=>{outcome={error};});
 const send=value=>events.enqueue(new TextEncoder().encode(`event: status\ndata: ${JSON.stringify(value)}\n\n`));
 send(job('running',{kind:'reference_analyze',phase:'核对后半段采样'}));await nextTurn();
 t.mock.timers.tick(600001);await nextTurn();assert.equal(outcome,undefined);
 send(job('succeeded',{kind:'reference_analyze'}));assert.equal((await waiting).status,'succeeded');
});

test('an explicit deadline still applies to a longform production job',async()=>{
 const {watchJob}=await import('../src/job-watch.js');
 const f=await fixture((_req,res)=>{res.writeHead(200,{'content-type':'application/json'});res.end(JSON.stringify(job('running',{
   kind:'render_content',result:{longform_progress:{target_seconds:480}}
 })));});
 try{await assert.rejects(watchJob('j1',{baseUrl:f.url,streaming:false,pollIntervalMs:10,timeoutMs:50}),e=>e.status===408);}
 finally{f.close();}
});

test('a terminal SSE answer completes before any polling timer advances',async t=>{
  const {watchJob}=await import('../src/job-watch.js');
  t.mock.timers.enable({apis:['setTimeout']});
  let events;const stream=new ReadableStream({start(controller){events=controller;}});
  t.mock.method(globalThis,'fetch',async path=>{
    if(path!=='/api/jobs/j1/events')throw new Error('A healthy SSE stream must not fall back to polling');
    return new Response(stream,{headers:{'content-type':'text/event-stream'}});
  });
  const controller=new AbortController(),statuses=[];let outcome;
  const waiting=watchJob('j1',{signal:controller.signal,onProgress:value=>statuses.push(value)});
  waiting.then(value=>{outcome={value};},error=>{outcome={error};});
  const send=value=>events.enqueue(new TextEncoder().encode(`event: status\ndata: ${JSON.stringify(value)}\n\n`));
  try{
    send(job('running'));await nextTurn();
    assert.deepEqual(statuses.map(value=>value.status),['running']);
    send(job('succeeded',{result:{preview_text:'完成回答'}}));await nextTurn();
    // An event-loop turn drains stream promises without advancing the frozen clock.
    assert.ok(outcome,'the ready answer must settle while every timeout callback is still frozen');
    assert.ifError(outcome.error);assert.equal(outcome.value.status,'succeeded');
    assert.equal(outcome.value.result.preview_text,'完成回答');
    assert.deepEqual(statuses.map(value=>value.status),['running','succeeded']);
  }finally{
    controller.abort();
    // Release a regressed fixed-delay implementation during failure cleanup only.
    t.mock.timers.runAll();await waiting.catch(()=>{});
  }
});

test('split UTF-8 SSE chunks deliver cumulative preview before final completion',async()=>{
  const {watchJob}=await import('../src/job-watch.js');const updates=[];
  const f=await fixture(async(_req,res)=>{res.writeHead(200,{'content-type':'text/event-stream'});const bytes=Buffer.from(`: keepalive\r\n\r\nevent: status\r\ndata: ${JSON.stringify(job('running',{phase:'generating',result:{preview_text:'云境的客户'}}))}\r\n\r\n`);for(let i=0;i<bytes.length;i+=7)res.write(bytes.subarray(i,i+7));await delay(30);res.end(`event: status\ndata: ${JSON.stringify(job('succeeded',{result:{preview_text:'云境的客户值得跟进'}}))}\n\n`);});
  try{await watchJob('j1',{baseUrl:f.url,onProgress:value=>updates.push(value)});assert.deepEqual(updates.map(value=>value.result.preview_text),['云境的客户','云境的客户值得跟进']);assert.equal(updates[0].status,'running');}
  finally{f.close();}
});

test('a broken stream recovers from durable job state instead of leaving the UI busy',async()=>{
  const {watchJob}=await import('../src/job-watch.js');let polls=0;
  const f=await fixture(async(req,res)=>{if(req.url.endsWith('/events')){res.writeHead(200,{'content-type':'text/event-stream'});res.end(`event: status\ndata: ${JSON.stringify(job('running'))}\n\n`);}else{polls++;res.writeHead(200,{'content-type':'application/json'});res.end(JSON.stringify(job(polls===1?'running':'succeeded')));}});
  try{const result=await watchJob('j1',{baseUrl:f.url,pollIntervalMs:10});assert.equal(result.status,'succeeded');assert.equal(polls,2);}
  finally{f.close();}
});

test('cancelling the watcher closes its connection and stops later updates',async()=>{
  const {watchJob}=await import('../src/job-watch.js');const controller=new AbortController();let connectionsClosed=false;const updates=[];
  const f=await fixture((_req,res)=>{res.writeHead(200,{'content-type':'text/event-stream'});res.write(`event: status\ndata: ${JSON.stringify(job('running'))}\n\n`);res.on('close',()=>{connectionsClosed=true;});});
  try{const waiting=watchJob('j1',{baseUrl:f.url,signal:controller.signal,onProgress:value=>{updates.push(value);controller.abort();}});await assert.rejects(waiting,e=>e.name==='AbortError');await delay(30);assert.equal(connectionsClosed,true);assert.equal(updates.length,1);}
  finally{controller.abort();f.close();}
});

test('permission revocation stops immediately and never retries with polling',async()=>{
  const {watchJob}=await import('../src/job-watch.js');let polls=0;
  const f=await fixture((req,res)=>{if(req.url.endsWith('/events')){res.writeHead(200,{'content-type':'text/event-stream'});res.end('event: error\ndata: {"detail":"访问权限已失效"}\n\n');}else{polls++;res.writeHead(200,{'content-type':'application/json'});res.end(JSON.stringify(job('succeeded')));}});
  try{await assert.rejects(watchJob('j1',{baseUrl:f.url}),e=>e.status===403&&e.message==='访问权限已失效');assert.equal(polls,0);}
  finally{f.close();}
});

test('a stalled fallback has a deadline instead of polling forever',async()=>{
  const {watchJob}=await import('../src/job-watch.js');
  const f=await fixture((req,res)=>{if(req.url.endsWith('/events')){res.writeHead(503);res.end();}else{res.writeHead(200,{'content-type':'application/json'});res.end(JSON.stringify(job('running')));}});
  try{await assert.rejects(watchJob('j1',{baseUrl:f.url,pollIntervalMs:10,timeoutMs:80}),e=>e.status===408);}
  finally{f.close();}
});

test('unavailable streaming falls back to polling and still resolves a failed terminal job',async()=>{
  const {watchJob}=await import('../src/job-watch.js');
  const f=await fixture((_req,res)=>{res.writeHead(200,{'content-type':'application/json'});res.end(JSON.stringify(job('failed',{error:'素材权限不足'})));});
  try{const result=await watchJob('j1',{baseUrl:f.url,streaming:false});assert.equal(result.status,'failed');assert.equal(result.error,'素材权限不足');}
  finally{f.close();}
});

test('fractional job progress and real elapsed time are shown accurately',async()=>{
  const {jobProgressPercent,jobStatusText}=await import('../src/job-status.js');
  assert.equal(jobProgressPercent(0.05),5);assert.equal(jobProgressPercent(0.675),68);assert.equal(jobProgressPercent(1),100);
  assert.equal(jobProgressPercent(NaN),0);assert.equal(jobProgressPercent(4),100);
  assert.match(jobStatusText(job('running',{phase:'正在生成回答',attempt_started_at:'2026-10-13T01:00:00Z'}),Date.parse('2026-10-13T01:00:07Z')),/正在生成回答.*已用7秒/);
  assert.match(jobStatusText(job('queued'),Date.parse('2026-10-13T01:00:03Z')),/等待调度.*3秒/);
});
