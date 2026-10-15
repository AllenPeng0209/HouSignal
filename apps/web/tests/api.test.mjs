import {test} from 'node:test';
import assert from 'node:assert/strict';
import {api, ApiError, isTerminalJob, safeUrl} from '../src/api.js';
test('an expired session is distinguishable from a recoverable failure', async () => {
  const server = await import('node:http');
  const app = server.createServer((_req,res)=>{res.writeHead(401,{'content-type':'application/json'});res.end(JSON.stringify({detail:'请重新登录'}));});
  await new Promise(r=>app.listen(0,'127.0.0.1',r));
  try { await assert.rejects(api(`http://127.0.0.1:${app.address().port}/api/auth/me`),error=>error instanceof ApiError&&error.status===401&&error.message==='请重新登录'); }
  finally { app.close(); }
});
test('terminal job states exclude queued and running and include failure',()=>{
  assert.equal(isTerminalJob('running'),false);assert.equal(isTerminalJob('queued'),false);
  assert.equal(isTerminalJob('succeeded'),true);assert.equal(isTerminalJob('failed'),true);assert.equal(isTerminalJob('cancelled'),true);
});
test('untrusted publication links cannot execute scripts or use files',()=>{
  assert.equal(safeUrl('javascript:alert(1)'),null);assert.equal(safeUrl('file:///tmp/private'),null);assert.equal(safeUrl('https://www.douyin.com/video/123'),'https://www.douyin.com/video/123');
});
test('a 401 notifies the application to clear its expired session', async()=>{
 const server=await import('node:http');const app=server.createServer((_req,res)=>{res.writeHead(401,{'content-type':'application/json'});res.end('{"detail":"会话已过期"}');});await new Promise(r=>app.listen(0,'127.0.0.1',r));const target=new EventTarget();let expired=false;target.addEventListener('housignal-session-expired',()=>{expired=true;});globalThis.window=target;
 try{await assert.rejects(api(`http://127.0.0.1:${app.address().port}/api/auth/me`));assert.equal(expired,true);}finally{delete globalThis.window;app.close();}
});
