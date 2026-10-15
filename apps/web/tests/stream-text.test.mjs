import {test} from 'node:test';
import assert from 'node:assert/strict';
import {nextStreamText,previewForProgress} from '../src/stream-text.js';

test('a batched server delta is revealed over multiple frames without losing text',()=>{
 const target='客户需求与房源资料逐项核对。'.repeat(20);
 let text=nextStreamText('',target,16);
 assert.ok(text.length>0&&text.length<20);
 for(let frame=0;frame<1000&&text!==target;frame++){
  const next=nextStreamText(text,target,16);
  assert.ok(next.startsWith(text));assert.ok(next.length-text.length<=20);text=next;
 }
 assert.equal(text,target);
});

test('a delayed frame does not dump the accumulated answer at once',()=>{
 const next=nextStreamText('已确认','已确认'+'新的资料'.repeat(100),5000);
 assert.ok(next.length>3&&next.length<25);
});

test('cleared or replaced server text immediately discards the previous preview',()=>{
 assert.equal(nextStreamText('已失效的资料','',16),'');
 assert.equal(nextStreamText('旧来源内容','重新核验后的结果',16),'重新核验后的结果');
});

test('stream frames never end with a split emoji surrogate',()=>{
 const target='🏡客户偏好🌿江景';let text='';
 while(text!==target){text=nextStreamText(text,target,16);assert.doesNotMatch(text,/[\uD800-\uDBFF]$/);}
});

test('successful terminal state retains preview only until checked messages arrive',()=>{
 assert.equal(previewForProgress('正在显示的最后一段',{status:'succeeded',result:{message_id:'m1'}}),'正在显示的最后一段');
});

test('running removal, failures and cancellation clear stale preview',()=>{
 for(const job of [{status:'running',result:{}},{status:'running',result:{preview_text:''}},{status:'failed'},{status:'cancelled'}])assert.equal(previewForProgress('旧资料',job),'');
 assert.equal(previewForProgress('旧资料',{status:'running',result:{preview_text:'最新资料'}}),'最新资料');
});
