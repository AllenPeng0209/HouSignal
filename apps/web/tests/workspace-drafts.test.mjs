import {test} from 'node:test';
import assert from 'node:assert/strict';
const feature=await import('../src/workspace-draft-store.js').catch(()=>({WorkspaceDraftStore:class{save(){return {ok:false};}load(){return null;}clearIdentity(){}}}));
const {WorkspaceDraftStore}=feature;
const scope={userId:'alice',organizationId:'org-a',projectId:'project-a'};
function storage(){const values=new Map();return {get length(){return values.size;},key:i=>[...values.keys()][i]??null,getItem:key=>values.get(key)??null,setItem:(key,value)=>values.set(key,value),removeItem:key=>values.delete(key)};}
// Catches dropped edits after the editor unmounts or the browser refreshes.
test('script, cover and storyboard survive a new store instance',()=>{
 const disk=storage(),first=new WorkspaceDraftStore(()=>disk);first.save(scope,'studio','v1',{script:'server'},{script:'edited',cover:'新标题',storyboard:[{caption:'新字幕'}]});
 const recovered=new WorkspaceDraftStore(()=>disk).load(scope,'studio','v1',{script:'server'});
 assert.deepEqual(recovered?.value,{script:'edited',cover:'新标题',storyboard:[{caption:'新字幕'}]});assert.equal(recovered?.conflict,false);
});
// Catches accidental cross-account, cross-project or cross-version restoration.
test('drafts are isolated by user, organization, project and content version',()=>{
 const disk=storage(),store=new WorkspaceDraftStore(()=>disk);store.save(scope,'studio','v1',{}, {script:'private'});
 for(const other of [{...scope,userId:'bob'},{...scope,organizationId:'org-b'},{...scope,projectId:'project-b'}])assert.equal(store.load(other,'studio','v1',{}),null);
 assert.equal(store.load(scope,'studio','v2',{}),null);assert.equal(store.load(scope,'studio','v1',{})?.value.script,'private');
});
// Catches silent overwriting when another editor changed the server source.
test('a newer server value reports conflict and retains the local copy for explicit recovery',()=>{
 const disk=storage(),store=new WorkspaceDraftStore(()=>disk);store.save(scope,'studio','v1',{script:'old'},{script:'mine'});
 const recovered=store.load(scope,'studio','v1',{script:'new'});assert.equal(recovered?.conflict,true);assert.deepEqual(recovered?.value,{script:'mine'});
});
// Catches quota and privacy-mode failures discarding an edit during page navigation.
test('failed storage writes report failure while retaining edits in memory',()=>{
 const store=new WorkspaceDraftStore(()=>({getItem(){throw Error('blocked');},setItem(){throw Error('quota');}}));
 const outcome=store.save(scope,'planning','composer',{}, {idea:'保留构思'});assert.equal(outcome.ok,false);assert.match(outcome.error,/草稿/);assert.deepEqual(store.load(scope,'planning','composer',{})?.value,{idea:'保留构思'});
});
// Catches stale in-memory data appearing after logout or session expiry.
test('clearing an identity removes only its drafts',()=>{
 const disk=storage(),store=new WorkspaceDraftStore(()=>disk),bob={...scope,userId:'bob'};store.save(scope,'studio','v1',{}, {script:'alice'});store.save(bob,'studio','v1',{}, {script:'bob'});
 store.clearIdentity(scope);assert.equal(store.load(scope,'studio','v1',{}),null);assert.equal(new WorkspaceDraftStore(()=>disk).load(scope,'studio','v1',{}),null);assert.equal(store.load(bob,'studio','v1',{})?.value.script,'bob');
});

// Catches last-writer-wins storage replacing another window's recoverable text.
test('concurrent windows keep independent drafts and each refresh recovers its own writer',()=>{
 const disk=storage(),tabA=storage(),tabB=storage(),a=new WorkspaceDraftStore(()=>disk,{writerId:'window-a',sessionStorage:()=>tabA}),b=new WorkspaceDraftStore(()=>disk,{writerId:'window-b',sessionStorage:()=>tabB});
 a.load(scope,'studio','v1',{script:'server'});b.load(scope,'studio','v1',{script:'server'});
 a.save(scope,'studio','v1',{script:'server'},{script:'A 的未提交稿'});b.save(scope,'studio','v1',{script:'server'},{script:'B 的未提交稿'});
 const refreshedA=new WorkspaceDraftStore(()=>disk,{writerId:'a-after-refresh',sessionStorage:()=>tabA}).load(scope,'studio','v1',{script:'server'}),refreshedB=new WorkspaceDraftStore(()=>disk,{writerId:'b-after-refresh',sessionStorage:()=>tabB}).load(scope,'studio','v1',{script:'server'});
 assert.equal(refreshedA?.value.script,'A 的未提交稿');assert.equal(refreshedB?.value.script,'B 的未提交稿');assert.equal(refreshedA?.conflictKind,'concurrent');assert.equal(refreshedA?.alternatives[0]?.value.script,'B 的未提交稿');
});
// Catches a duplicated tab sharing a session pointer then overwriting its opener's branch.
test('duplicated session pointers never make two document writers share a storage slot',()=>{
 const disk=storage(),tabA=storage();const a=new WorkspaceDraftStore(()=>disk,{writerId:'opener',sessionStorage:()=>tabA});a.save(scope,'planning','composer',{}, {idea:'原窗口构思'});
 const cloned=storage();for(let i=0;i<tabA.length;i++){const key=tabA.key(i);cloned.setItem(key,tabA.getItem(key));}
 const b=new WorkspaceDraftStore(()=>disk,{writerId:'cloned-tab',sessionStorage:()=>cloned});b.load(scope,'planning','composer',{});b.save(scope,'planning','composer',{}, {idea:'复制窗口构思'});
 assert.equal(new WorkspaceDraftStore(()=>disk,{writerId:'refresh-opener',sessionStorage:()=>tabA}).load(scope,'planning','composer',{})?.value.idea,'原窗口构思');
 assert.equal(new WorkspaceDraftStore(()=>disk,{writerId:'refresh-clone',sessionStorage:()=>cloned}).load(scope,'planning','composer',{})?.value.idea,'复制窗口构思');
});
// Catches resolving or saving one editor destroying the other editor's unsaved branch.
test('clearing this window does not remove another window draft',()=>{
 const disk=storage(),a=new WorkspaceDraftStore(()=>disk,{writerId:'a',sessionStorage:()=>storage()}),tabB=storage(),b=new WorkspaceDraftStore(()=>disk,{writerId:'b',sessionStorage:()=>tabB});
 a.save(scope,'studio','v1',{}, {script:'A'});b.save(scope,'studio','v1',{}, {script:'B'});a.remove(scope,'studio','v1');
 assert.equal(new WorkspaceDraftStore(()=>disk,{writerId:'b-refresh',sessionStorage:()=>tabB}).load(scope,'studio','v1',{})?.value.script,'B');
});

// Catches a malformed legacy entry hiding valid document branches during refresh.
test('corrupt legacy data does not hide a valid owned branch',()=>{
 const disk=storage(),tab=storage(),store=new WorkspaceDraftStore(()=>disk,{writerId:'owned',sessionStorage:()=>tab});store.save(scope,'studio','v1',{}, {script:'应恢复的窗口稿'});
 disk.setItem('housignal-workspace:v1:alice:org-a:project-a:studio:v1','{invalid json');
 assert.equal(new WorkspaceDraftStore(()=>disk,{writerId:'refreshed',sessionStorage:()=>tab}).load(scope,'studio','v1',{})?.value.script,'应恢复的窗口稿');
});
// Catches accepting malformed local envelopes that cannot be displayed or safely restored.
test('invalid branch timestamps cannot replace a valid draft candidate',()=>{
 const disk=storage();disk.setItem('housignal-workspace:v1:alice:org-a:project-a:studio:v1:writer:broken',JSON.stringify({schema:2,scope,kind:'studio',id:'v1',writerId:'broken',revision:'broken',base:'{}',value:{script:'invalid'},savedAt:'not-a-time'}));
 assert.equal(new WorkspaceDraftStore(()=>disk).load(scope,'studio','v1',{}),null);
});

// Catches a duplicate copy of the same recovered text falsely conflicting with its own next edit.
test('recovering identical copies treats their exact revisions as the same starting draft',()=>{
 const disk=storage(),a=new WorkspaceDraftStore(()=>disk,{writerId:'same-a'}),b=new WorkspaceDraftStore(()=>disk,{writerId:'same-b'});a.save(scope,'studio','v1',{}, {script:'共同稿'});b.save(scope,'studio','v1',{}, {script:'共同稿'});
 const resumed=new WorkspaceDraftStore(()=>disk,{writerId:'resumed'}),loaded=resumed.load(scope,'studio','v1',{});resumed.save(scope,'studio','v1',{}, {script:'在共同稿上继续编辑'},{acknowledged:[loaded.id,...loaded.acknowledged]});assert.equal(resumed.load(scope,'studio','v1',{}).conflict,false);
});

// Catches dropping existing v1 drafts during the branch-format migration.
test('a legacy v1 draft remains readable and survives adoption into a document branch',()=>{
 const disk=storage(),tab=storage(),legacyKey='housignal-workspace:v1:alice:org-a:project-a:studio:v1';disk.setItem(legacyKey,JSON.stringify({schema:1,scope,kind:'studio',id:'v1',base:'{}',value:{script:'升级前的未提交稿'},savedAt:100}));
 const store=new WorkspaceDraftStore(()=>disk,{writerId:'new-document',sessionStorage:()=>tab}),old=store.load(scope,'studio','v1',{});assert.equal(old.value.script,'升级前的未提交稿');store.save(scope,'studio','v1',{}, {script:'升级后继续修改'},{acknowledged:[old.id]});
 assert.equal(new WorkspaceDraftStore(()=>disk,{writerId:'reload',sessionStorage:()=>tab}).load(scope,'studio','v1',{})?.value.script,'升级后继续修改');assert.equal(JSON.parse(disk.getItem(legacyKey)).value.script,'升级前的未提交稿');
});
// Catches claiming durable tab recovery when sessionStorage is blocked even though localStorage accepts writes.
test('a failed tab recovery pointer retains the edit and warns until the pointer can be retried',()=>{
 const disk=storage(),tab=storage();let blocked=true;const store=new WorkspaceDraftStore(()=>disk,{writerId:'blocked-tab',sessionStorage:()=>{if(blocked)throw Error('denied');return tab;}});
 assert.equal(store.save(scope,'studio','v1',{}, {script:'指针失败也保留'}).ok,false);assert.equal(store.hasUnpersisted(),true);assert.equal(store.load(scope,'studio','v1',{}).value.script,'指针失败也保留');
 blocked=false;assert.equal(store.save(scope,'studio','v1',{}, {script:'指针失败也保留'}).ok,true);assert.equal(store.hasUnpersisted(),false);assert.equal(new WorkspaceDraftStore(()=>disk,{writerId:'refresh',sessionStorage:()=>tab}).load(scope,'studio','v1',{}).value.script,'指针失败也保留');
});
