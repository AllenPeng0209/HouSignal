import {after,before,test} from 'node:test';
import assert from 'node:assert/strict';
import {existsSync} from 'node:fs';
import {mkdtemp,rm,writeFile} from 'node:fs/promises';
import {createServer} from 'node:http';
import {tmpdir} from 'node:os';
import {join,resolve} from 'node:path';
import {pathToFileURL} from 'node:url';
import {build} from 'esbuild';
import React from 'react';
import {renderToStaticMarkup} from 'react-dom/server';
let ui,directory,server,origin,statuses,requests;
before(async()=>{
 directory=await mkdtemp(join(tmpdir(),'housignal-platform-accounts-'));
 const source=resolve('src/PlatformAccounts.tsx');
 const result=await build({entryPoints:[source],write:false,bundle:true,format:'esm',platform:'node',jsx:'automatic',external:['react','react-dom','react/jsx-runtime'],loader:{'.css':'empty'},plugins:[{name:'missing-feature',setup(builder){if(!existsSync(source)){builder.onResolve({filter:/PlatformAccounts\.tsx$/},()=>({path:'missing-feature',namespace:'fixture'}));builder.onLoad({filter:/.*/,namespace:'fixture'},()=>({loader:'js',contents:'export const PlatformAccountsGrid=()=>null,PlatformLoginPanel=()=>null,PlatformPreparePanel=()=>null;export const requestPlatformAction=async()=>null,watchPlatformLogin=async()=>null;'}));}}}]});
 const bundled=result.outputFiles[0].text.replaceAll('from "react"',`from ${JSON.stringify(pathToFileURL(resolve('node_modules/react/index.js')).href)}`).replaceAll('from "react/jsx-runtime"',`from ${JSON.stringify(pathToFileURL(resolve('node_modules/react/jsx-runtime.js')).href)}`);
 const target=join(directory,'accounts.mjs');await writeFile(target,bundled);ui=await import(pathToFileURL(target).href);
 server=createServer(async(req,res)=>{const chunks=[];for await(const chunk of req)chunks.push(chunk);requests.push({path:req.url,method:req.method,body:JSON.parse(Buffer.concat(chunks).toString()||'{}')});res.writeHead(200,{'content-type':'application/json'});res.end(JSON.stringify((req.url.endsWith('/status')||req.url.endsWith('/publish-status'))?(statuses.shift()||{state:'starting',message:'准备中',qrReady:false}):{job_id:'prepare-1',job:{id:'prepare-1',kind:'platform_prepare',status:'queued'}}));});
 await new Promise(resolve=>server.listen(0,'127.0.0.1',resolve));origin=`http://127.0.0.1:${server.address().port}`;
});
after(async()=>{server?.close();if(directory)await rm(directory,{recursive:true,force:true});});
const accounts=[['xiaohongshu','小红书'],['kuaishou','快手'],['channels','微信视频号'],['zhihu','知乎'],['bilibili','B站'],['douyin','抖音'],['wechat','微信公众号']].map(([platform,name])=>({platform,name,backend:platform,supported:true,loggedIn:false,verified:false,ready:true,note:''}));
const boundAccount={...accounts[5],loggedIn:true,verified:true,identity_name:'住宅观察',session_id:'session-current'};
const noop=()=>{};
const gridProps={accounts,editable:true,busy:'',onLogin:noop,onVerify:noop,onLogout:noop};
const render=element=>renderToStaticMarkup(element);
// Catches missing login controls, or treating unknown/cached sessions as verified identities.
test('all seven platforms expose login while cached sessions require identity revalidation',()=>{
 const html=render(React.createElement(ui.PlatformAccountsGrid,{...gridProps,accounts:accounts.map(a=>a.platform==='douyin'?{...a,loggedIn:true,verified:false}:a)}));
 for(const name of ['小红书','快手','微信视频号','知乎','B站','抖音','微信公众号'])assert.match(html,new RegExp(name));
 assert.equal((html.match(/<span>登录<\/span>/g)||[]).length,6);assert.match(html,/待校验/);assert.match(html,/校验账号/);assert.match(html,/退出登录/);
});
// Catches frontend role bypass through apparently available buttons.
test('read-only roles can see session identities but cannot start login, verify, or logout',()=>{
 const html=render(React.createElement(ui.PlatformAccountsGrid,{...gridProps,editable:false,accounts:[accounts[0],{...accounts[5],loggedIn:true,verified:true,identity_name:'住宅观察'}]}));
 assert.match(html,/住宅观察/);const buttons=Array.from(html.matchAll(/<button\b[^>]*>[\s\S]*?<\/button>/g),m=>m[0]);assert.equal(buttons.length,3);assert.ok(buttons.every(button=>button.includes('disabled=""')));
});
// Catches the QR state field mismatch (qrReady vs qr) and missing retry / phone challenge flow.
test('QR ready, SMS challenge, and expired login states show the appropriate next action',()=>{
 const props={account:accounts[0],status:{state:'qr_ready',qrReady:true,message:'手机扫码'},qrSource:'blob:authenticated-image',smsCode:'',smsError:'',smsBusy:false,onSmsCode:noop,onSms:noop,onRetry:noop};
 const qr=render(React.createElement(ui.PlatformLoginPanel,props));assert.match(qr,/src="blob:authenticated-image"/);assert.match(qr,/alt="小红书登录二维码"/);
 const sms=render(React.createElement(ui.PlatformLoginPanel,{...props,status:{state:'sms_required',message:'请输入验证码'}}));assert.match(sms,/短信验证码/);assert.match(sms,/inputMode="numeric"/);assert.match(sms,/提交验证码/);assert.doesNotMatch(sms,/authenticated-image/);
 const expired=render(React.createElement(ui.PlatformLoginPanel,{...props,status:{state:'expired',message:'二维码已过期'}}));assert.match(expired,/重新获取二维码/);assert.doesNotMatch(expired,/authenticated-image/);
});
// Catches a false publication claim after successful preparation.
test('prepared task result provides the authenticated package and accurately identifies the unpublished state',()=>{
 const html=render(React.createElement(ui.PlatformPreparePanel,{job:{id:'j-1',status:'succeeded',kind:'platform_prepare',result:{dry_run:true,publication_state:'not_published',package:{name:'抖音发布包.zip',download_url:'/api/projects/p-1/jobs/j-1/package',byte_size:2048,sha256:'abc'},plan:{draft_only:true,steps:['核对标题','人工确认发布']}}}}));
 assert.match(html,/抖音发布包.zip/);assert.match(html,/href="\/api\/projects\/p-1\/jobs\/j-1\/package"/);assert.match(html,/尚未发布/);assert.match(html,/核对标题/);
});
// Catches project/target confusion and leaking the content identifier into a GET.
test('prepare and SMS requests target the current project platform with the required JSON body',async()=>{
 requests=[];statuses=[];const base=`${origin}/api/projects/project-b/operations/platform-accounts`;
 await ui.requestPlatformAction(base,'douyin','prepare',{content_version_id:'approved-3'});await ui.requestPlatformAction(base,'xiaohongshu','sms',{code:'123456'});
 assert.deepEqual(requests,[{path:'/api/projects/project-b/operations/platform-accounts/douyin/prepare',method:'POST',body:{content_version_id:'approved-3'}},{path:'/api/projects/project-b/operations/platform-accounts/xiaohongshu/sms',method:'POST',body:{code:'123456'}}]);
});
// Catches continued polling after a terminal outcome.
test('login polling stops on success and preserves authoritative account status',async()=>{
 requests=[];statuses=[{state:'qr_ready',message:'扫码',qrReady:true,qrTs:10},{state:'success',message:'登录成功',loggedIn:true,verified:true}];const observed=[];
 const result=await ui.watchPlatformLogin(`${origin}/api/projects/p-1/operations/platform-accounts`,'douyin',{signal:new AbortController().signal,onStatus:value=>observed.push(value),pollMs:1});
 assert.equal(result?.state,'success');assert.equal(result?.verified,true);assert.deepEqual(observed.map(s=>s.state),['qr_ready','success']);assert.equal(requests.length,2);
});
// Catches delivery of stale QR/status results when a modal closes or the project changes.
test('an aborted login poll stops status delivery and schedules no later requests',async()=>{
 requests=[];statuses=[{state:'qr_ready',message:'扫码',qrReady:true}];const controller=new AbortController(),observed=[];
 await assert.rejects(ui.watchPlatformLogin(`${origin}/api/projects/p-old/operations/platform-accounts`,'douyin',{signal:controller.signal,pollMs:50,onStatus:value=>{observed.push(value.state);controller.abort();}}),error=>error.name==='AbortError');
 await new Promise(resolve=>setTimeout(resolve,60));assert.deepEqual(observed,['qr_ready']);assert.equal(requests.length,1);
});
// Catches stale approval being offered to the agent or a different platform publication.
test('content choices require a valid approved render on the selected platform',()=>{
 assert.equal(typeof ui.eligiblePlatformContents,'function');
 const selected={id:'c-good',status:'approved',approval_valid:true,approved_release_id:'r-1',render_asset_id:'video-1',channel:'抖音'};
 const rows=[selected,{...selected,id:'c-stale',approval_valid:false},{...selected,id:'c-no-render',render_asset_id:null},{...selected,id:'c-other',channel:'小红书'},{...selected,id:'c-draft',status:'draft'}];
 assert.deepEqual(ui.eligiblePlatformContents(rows,accounts[5]).map(row=>row.id),['c-good']);
});
// Catches automatic publishing, or publishing a different selection after its predecessor was prepared.
test('publication requires human confirmation of the concrete successfully prepared content',()=>{
 assert.equal(typeof ui.PlatformPublishConfirmation,'function');
 const content={id:'c-1',version:2,cover_title:'真实住宅导览',script:'实际已审核脚本',approved_release_id:'release-1',channel:'抖音'};
 const props={content,preparedContentId:'c-1',account:boundAccount,preparedSessionId:'session-current',preview:{title:'真实住宅导览',body:'实际已审核脚本',draft_only:false,content_format:'video'},confirmed:false,onConfirmed:noop,onPublish:noop,editable:true,busy:false};
 let html=render(React.createElement(ui.PlatformPublishConfirmation,props));assert.match(html,/真实住宅导览/);assert.match(html,/实际已审核脚本/);assert.match(html,/release-1/);assert.match(html,/<button[^>]*disabled=""[^>]*>[\s\S]*确认并提交平台/);
 html=render(React.createElement(ui.PlatformPublishConfirmation,{...props,confirmed:true}));assert.doesNotMatch(html,/<button[^>]*disabled=""/);
 html=render(React.createElement(ui.PlatformPublishConfirmation,{...props,confirmed:true,preparedContentId:'other-content'}));assert.match(html,/<button[^>]*disabled=""/);
});
// Catches losing an agent-prepared task on navigation, or reusing a stale approval for publication.
test('only a completed preparation for the selected platform content and current release can resume',()=>{
 assert.equal(typeof ui.matchingPreparedJob,'function');const content={id:'c-1',approved_release_id:'r-current',channel:'抖音'};
 const job=(id,status,release,platform='douyin')=>({id,kind:'platform_prepare',status,result:{platform,content_version_id:'c-1',release_id:release,session_id:'session-current'}});
 assert.equal(ui.matchingPreparedJob([job('pending','running','r-current'),job('stale','succeeded','r-old'),job('other','succeeded','r-current','xiaohongshu'),job('valid','succeeded','r-current')],boundAccount,content)?.id,'valid');
 assert.equal(ui.matchingPreparedJob([job('stale','succeeded','r-old')],boundAccount,content),null);
});
// Catches showing original content when the publisher submits a platform-specific HTML body.
test('confirmation displays the exact server-prepared payload and prevents submission without that preview',()=>{
 const content={id:'c-1',version:2,cover_title:'原始标题',script:'原始正文',approved_release_id:'release-1',channel:'微信公众号'};
 const props={content,preparedContentId:'c-1',account:boundAccount,preparedSessionId:'session-current',confirmed:true,onConfirmed:noop,onPublish:noop,editable:true,busy:false};
 let html=render(React.createElement(ui.PlatformPublishConfirmation,{...props,preview:{title:'平台确切标题',body:'<p>已经审核 &amp; 转义的正文</p>',draft_only:true,content_format:'html'}}));
 assert.match(html,/平台确切标题/);assert.match(html,/&lt;p&gt;已经审核 &amp;amp; 转义的正文&lt;\/p&gt;/);assert.doesNotMatch(html,/原始标题|原始正文/);assert.match(html,/草稿箱/);
 html=render(React.createElement(ui.PlatformPublishConfirmation,props));assert.match(html,/<button[^>]*disabled=""/);assert.match(html,/重新准备发布包/);
});
// Catches publishing a newly approved release after the user reviewed an older preparation.
test('confirmed publication binds the request to the exact prepared release and rejects a stale preparation',async()=>{
 assert.equal(typeof ui.requestConfirmedPlatformPublish,'function');requests=[];statuses=[];
 const content={id:'c-1',approved_release_id:'r-review',approval_valid:true,status:'approved',render_asset_id:'video-1',channel:'抖音'};
 const job={id:'prepare-1',status:'succeeded',kind:'platform_prepare',result:{platform:'douyin',content_version_id:'c-1',release_id:'r-review',session_id:'session-current',publication_preview:{title:'已核对标题',body:'已核对正文',draft_only:false,content_format:'video'}}};
 await ui.requestConfirmedPlatformPublish(`${origin}/api/projects/p-1/operations/platform-accounts`,boundAccount,content,job,'one-confirmation');
 assert.deepEqual(requests,[{path:'/api/projects/p-1/operations/platform-accounts/douyin/publish',method:'POST',body:{content_version_id:'c-1',expected_release_id:'r-review',expected_session_id:'session-current',confirmed:true,request_key:'one-confirmation'}}]);
 await assert.rejects(ui.requestConfirmedPlatformPublish(`${origin}/api/projects/p-1/operations/platform-accounts`,boundAccount,{...content,approved_release_id:'r-new'},job,'new-key'),/重新准备/);assert.equal(requests.length,1);
});
// Catches a valid historical channel alias disappearing from the publication selector.
test('canonical platform selectors retain valid B站 and 视频号 channel aliases',()=>{
 const row={id:'c-alias',status:'approved',approval_valid:true,render_asset_id:'render-1',channel:'哔哩哔哩'};
 assert.equal(ui.eligiblePlatformContents([row],accounts[4]).length,1);
 assert.equal(ui.eligiblePlatformContents([{...row,channel:'视频号'}],{...accounts[2],platform:'weixin-channels'}).length,1);
 assert.equal(ui.eligiblePlatformContents([{...row,channel:'抖音'}],accounts[4]).length,0);
});
// Catches unsafe external avatars while preserving an authenticated nickname.
test('verified account identities show public avatars and reject executable schemes',()=>{
 const props={...gridProps,accounts:[{...accounts[5],loggedIn:true,verified:true,identity_name:'住宅观察',avatar:'https://example.com/public-avatar.png'}]};
 let html=render(React.createElement(ui.PlatformAccountsGrid,props));assert.match(html,/src="https:\/\/example.com\/public-avatar.png"/);assert.match(html,/住宅观察/);
 html=render(React.createElement(ui.PlatformAccountsGrid,{...props,accounts:[{...props.accounts[0],avatar:'javascript:alert(1)'}]}));assert.doesNotMatch(html,/src="javascript:/);assert.match(html,/住宅观察/);
});
// Catches a live publication SMS challenge being hidden while a durable task remains running.
test('publication verification asks for a bounded one-time SMS code and hides it after verification',()=>{
 assert.equal(typeof ui.PlatformPublicationSms,'function');
 const props={status:{state:'sms_required',sms_required:true,message:'平台需要手机验证'},code:'123456',error:'',busy:false,onCode:noop,onSubmit:noop};
 let html=render(React.createElement(ui.PlatformPublicationSms,props));assert.match(html,/发布短信验证码/);assert.match(html,/autoComplete="one-time-code"/);assert.match(html,/maxLength="8"/);assert.match(html,/提交发布验证码/);
 html=render(React.createElement(ui.PlatformPublicationSms,{...props,code:'123'}));assert.match(html,/<button[^>]*disabled=""/);
 html=render(React.createElement(ui.PlatformPublicationSms,{...props,status:{state:'verifying',sms_required:false,message:'正在核对'}}));assert.doesNotMatch(html,/发布短信验证码/);
});
// Catches accidentally submitting a publication code to the login flow or wrong project.
test('publication SMS requests target only the active project publish challenge endpoints',async()=>{
 requests=[];statuses=[];const base=`${origin}/api/projects/p-current/operations/platform-accounts`;
 await ui.requestPlatformAction(base,'douyin','publish-status');await ui.requestPlatformAction(base,'douyin','publish-sms',{code:'654321'});
 assert.deepEqual(requests,[{path:'/api/projects/p-current/operations/platform-accounts/douyin/publish-status',method:'GET',body:{}},{path:'/api/projects/p-current/operations/platform-accounts/douyin/publish-sms',method:'POST',body:{code:'654321'}}]);
});
// Catches publication verification polling surviving task completion or a project switch.
test('ending a publication watcher aborts challenge delivery and all later requests',async()=>{
 assert.equal(typeof ui.watchPlatformPublication,'function');requests=[];statuses=[{state:'sms_required',message:'手机验证'}];
 const controller=new AbortController(),observed=[];
 await assert.rejects(ui.watchPlatformPublication(`${origin}/api/projects/p-old/operations/platform-accounts`,'douyin',{signal:controller.signal,pollMs:50,onStatus:value=>{observed.push(value.state);controller.abort();}}),error=>error.name==='AbortError');
 await new Promise(resolve=>setTimeout(resolve,60));assert.deepEqual(observed,['sms_required']);assert.deepEqual(requests,[{path:'/api/projects/p-old/operations/platform-accounts/douyin/publish-status',method:'GET',body:{}}]);
});
// Catches losing a running publication and its SMS monitor after Accounts remount.
test('a remounted account page recovers only the current platform publication that this actor can verify',async()=>{
 assert.equal(typeof ui.matchingActivePublication,'function');
 const row=(id,platform,can_verify=true,status='running')=>({id,kind:'platform_publish',status,platform,can_verify,result:{}});
 const history=[row('other-platform','xiaohongshu'),row('other-actor','douyin',false),row('completed','douyin',true,'succeeded'),row('recover-me','douyin')];
 const recovered=ui.matchingActivePublication(history,'douyin',null,'content');assert.equal(recovered?.id,'recover-me');
 assert.equal(ui.matchingActivePublication(history,'douyin',{...recovered,status:'succeeded'},'content'),null);
 assert.equal(ui.matchingActivePublication([row('admin-assist','douyin',false)],'douyin',null,'admin')?.id,'admin-assist');
 assert.equal(ui.matchingActivePublication(history,'douyin',null,'viewer'),null);
 requests=[];statuses=[{state:'sms_required',message:'恢复发布验证'}];const controller=new AbortController(),observed=[];
 await assert.rejects(ui.watchPlatformPublication(`${origin}/api/projects/remounted/operations/platform-accounts`,recovered.platform,{signal:controller.signal,onStatus:value=>{observed.push(value.state);controller.abort();}}),error=>error.name==='AbortError');
 assert.deepEqual(observed,['sms_required']);assert.equal(requests[0].path,'/api/projects/remounted/operations/platform-accounts/douyin/publish-status');
});

// Catches authorizing a replaced browser identity against the previously reviewed payload.
test('a prepared package and confirmation are invalid after the platform session changes',async()=>{
 const content={id:'c-1',approved_release_id:'release-1',version:1,channel:'抖音'};
 const prepared={id:'prepare-bound',kind:'platform_prepare',status:'succeeded',result:{platform:'douyin',content_version_id:'c-1',release_id:'release-1',session_id:'session-before',publication_preview:{title:'住宅',body:'内容',draft_only:false,content_format:'video'}}};
 assert.equal(ui.matchingPreparedJob([prepared],boundAccount,content),null);
 requests=[];statuses=[];
 await assert.rejects(ui.requestConfirmedPlatformPublish(`${origin}/api/projects/p-1/operations/platform-accounts`,boundAccount,content,prepared,'request-1'),/账号|会话|重新准备/);
 assert.equal(requests.length,0);
 const html=render(React.createElement(ui.PlatformPublishConfirmation,{content,account:boundAccount,preparedContentId:content.id,preparedSessionId:'session-before',preview:prepared.result.publication_preview,confirmed:true,onConfirmed:noop,onPublish:noop,editable:true,busy:false}));
 assert.match(html,/<button[^>]*disabled=""/);assert.match(html,/账号|会话/);assert.match(html,/住宅观察/);
});
// Catches legacy or unverified account state being treated as a usable publication authorization.
test('missing session bindings and unverified identities cannot be submitted even with confirmation',async()=>{
 const content={id:'c-1',approved_release_id:'release-1',version:1,channel:'抖音'};
 const prepared={id:'p1',kind:'platform_prepare',status:'succeeded',result:{platform:'douyin',content_version_id:'c-1',release_id:'release-1',session_id:'session-current',publication_preview:{title:'住宅',body:'内容',draft_only:false,content_format:'video'}}};
 requests=[];statuses=[];
 for(const account of [{...boundAccount,verified:false},{...boundAccount,session_id:undefined},{...boundAccount,loggedIn:false}]){
  await assert.rejects(ui.requestConfirmedPlatformPublish(`${origin}/api/projects/p-1/operations/platform-accounts`,account,content,prepared,'request-1'),/账号|会话|重新准备/);
 }
 assert.equal(requests.length,0);
});
// Catches a platform that needs its official browser window being trapped behind an endless QR loader.
test('official-window, challenge, and unknown login states provide concrete human actions',()=>{
 const props={account:accounts[5],qrSource:'',smsCode:'',smsError:'',smsBusy:false,onSmsCode:noop,onSms:noop,onRetry:noop,onCancel:noop,cancelBusy:false};
 for(const state of ['window_login','challenge','unknown']){
  const html=render(React.createElement(ui.PlatformLoginPanel,{...props,status:{state,message:'请在官方窗口完成验证'}}));
  assert.doesNotMatch(html,/正在准备二维码/);assert.match(html,/官方|浏览器/);assert.match(html,/结束登录/);
 }
 const waiting=render(React.createElement(ui.PlatformLoginPanel,{...props,status:{state:'qr_ready',qrReady:true},qrSource:'blob:qr'}));assert.match(waiting,/结束登录/);
});
// Catches polling a terminal unknown/not-configured state until an invented timeout.
test('unknown and unavailable login status ends observation without fabricating a QR',async()=>{
 for(const state of ['unknown','not_configured']){
  requests=[];statuses=[{state,message:'无法确认当前登录状态',verified:false,loggedIn:false}];
  const result=await ui.watchPlatformLogin(`${origin}/api/projects/p-1/operations/platform-accounts`,'douyin',{signal:new AbortController().signal,onStatus:noop,pollMs:1,maxMs:20});
  assert.equal(result.state,state);assert.equal(requests.length,1);
 }
});
// Catches a custom goal dropping the selected real account/content before agent handoff.
test('a custom operations goal remains bound to the selected platform and reviewed content',()=>{
 assert.equal(typeof ui.platformOperationsPrompt,'function');
 const text=ui.platformOperationsPrompt(boundAccount,{id:'content-reviewed',version:3,cover_title:'住宅采光',approved_release_id:'review-v3'},'安排住宅系列的下轮内容');
 assert.match(text,/抖音/);assert.match(text,/住宅观察/);assert.match(text,/content-reviewed/);assert.match(text,/v3/);assert.match(text,/安排住宅系列的下轮内容/);assert.match(text,/校验|核对/);assert.match(text,/确认/);assert.doesNotMatch(text,/session-current/);
 assert.ok(ui.platformOperationsPrompt(boundAccount,null,'目标'.repeat(1000)).length<=2000);
});
