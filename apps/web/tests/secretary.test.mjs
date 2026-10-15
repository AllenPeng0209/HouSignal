import {test} from 'node:test';
import assert from 'node:assert/strict';
import {existsSync} from 'node:fs';
import {pathToFileURL} from 'node:url';
import {resolve} from 'node:path';
const path=resolve('src/secretary.js');
const helpers=existsSync(path)?await import(pathToFileURL(path).href):{
 profileChanges:()=>({}), appliedActions:()=>[], viewingWhen:()=>'',
};

test('profile updates include only changed CRM fields and retain an intentional clear',()=>{
 const changes=helpers.profileChanges(
  {background:'企业经营者',budget:'待确认',preferences:'江景'},
  {background:' 企业经营者 ',budget:'3000万元',preferences:'',role:'admin',lead_id:'other'},
 );
 assert.deepEqual(changes,{budget:'3000万元',preferences:''});
});

test('an unchanged or absent profile does not send blank field deletions',()=>{
 assert.deepEqual(helpers.profileChanges(undefined,{background:'  '}),{});
 assert.deepEqual(helpers.profileChanges({budget:'3000万元'},{budget:'3000万元'}),{});
});

test('only persisted applied operations appear as completed receipts',()=>{
 const rows=[
  {id:'saved',status:'applied',label:'更新客户画像',summary:'已保存预算'},
  {id:'failed',status:'failed',summary:'未保存'},
  {id:'planned',status:'pending',summary:'准备更新'},
 ];
 assert.deepEqual(helpers.appliedActions({actions:rows}).map(row=>row.id),['saved']);
 assert.deepEqual(helpers.appliedActions({actions:null}),[]);
});

test('an afternoon appointment keeps its date and uncertainty without inventing an hour',()=>{
 assert.equal(helpers.viewingWhen({date:'2026-10-21',time_window:'下午',starts_at:null,timezone:'Asia/Shanghai'}),'10月21日（周三）下午');
 assert.equal(helpers.viewingWhen({date:null,time_window:'',starts_at:null,timezone:'Asia/Shanghai'}),'时间待补充');
});

test('exact viewing times are displayed in the appointment timezone',()=>{
 assert.equal(helpers.viewingWhen({date:'2026-10-21',starts_at:'2026-10-21T07:30:00Z',timezone:'Asia/Shanghai'}),'10月21日（周三）15:30');
});

test('persisted production queue receipts stay distinct from completed record writes',()=>{
 const message={actions:[{id:'q1',status:'queued',label:'视频制作任务',summary:'已创建视频任务'},{id:'a1',status:'applied',label:'客户档案',summary:'已保存客户档案'},{id:'p1',status:'pending'}]};
 assert.deepEqual(helpers.operationActions?.(message).map(row=>row.id),['q1','a1']);
 assert.deepEqual(helpers.appliedActions(message).map(row=>row.id),['a1']);
});

test('secretary answers keep meeting details while record buttons carry machine identifiers',()=>{
 const answer='已保存客户档案。\n- 客户ID：`fc19b2e4-1dd8-456c-aad3-51dfa2f36173` [S2]\n预约10月22日15:30，在南门大堂。\n预算3800万元。';
 const visible=helpers.presentAssistantAnswer(answer);
 assert.doesNotMatch(visible,/fc19b2e4|客户ID/);
 assert.match(visible,/10月22日15:30，在南门大堂/);
 assert.match(visible,/预算3800万元/);
});

test('authenticated asset file paths keep their UUIDs while meeting identifiers remain hidden',()=>{
 const path='/api/projects/6e3c19fa-7e41-4b26-bae1-699d437acc40/assets/fc19b2e4-1dd8-456c-aad3-51dfa2f36173/file';
 const visible=helpers.presentAssistantAnswer(`视频已归档：[播放视频](${path})\n客户ID：fc19b2e4-1dd8-456c-aad3-51dfa2f36173\n下周三15:30会面。`);
 assert.ok(visible.includes(path));assert.doesNotMatch(visible,/客户ID/);assert.match(visible,/15:30会面/);
});

test('placeholder service URLs are removed when no authenticated asset path is available',()=>{
 const visible=helpers.presentAssistantAnswer('成片地址：https://housignal.example.com/projects/p1/assets/a1/file\n[播放成片](https://placeholder.example/api/projects/p1/assets/a1/file)\n已记录客户需求。');
 assert.doesNotMatch(visible,/https?:|example/);assert.match(visible,/已记录客户需求/);
});

test('external URL parentheses and protocol-relative query values never activate embedded native asset paths',()=>{
 const path='/api/projects/p1/assets/a1/file';
 for(const content of [`https://evil.test/(${path})`,`//evil.test/?next=${path}`,`[外部文件](https://evil.test/(${path}))`,`[外部文件](//evil.test/?next=${path})`,`ftp://evil.test/(${path})`]){
  assert.deepEqual(helpers.assistantInlineParts(helpers.presentAssistantAnswer(content)).filter(part=>part.kind==='asset'),[],content);
 }
});

test('standalone native asset URLs and native Markdown URLs remain clickable',()=>{
 const path='/api/projects/p1/assets/a1/file';
 assert.deepEqual(helpers.assistantInlineParts(helpers.presentAssistantAnswer(path)),[{kind:'asset',url:path,label:'查看归档素材'}]);
 assert.deepEqual(helpers.assistantInlineParts(helpers.presentAssistantAnswer(`[播放成片视频](${path})`)),[{kind:'asset',url:path,label:'播放视频'}]);
});

test('embedded asset UUIDs in external URLs are not protected from identifier hiding',()=>{
 const path='/api/projects/6e3c19fa-7e41-4b26-bae1-699d437acc40/assets/fc19b2e4-1dd8-456c-aad3-51dfa2f36173/file';
 for(const prefix of ['https://evil.test/(', '//evil.test/?next=']){
  const visible=helpers.presentAssistantAnswer(prefix+path);
  assert.doesNotMatch(visible,/6e3c19fa|fc19b2e4/);
  assert.equal(helpers.assistantInlineParts(visible).some(part=>part.kind==='asset'),false);
 }
});
