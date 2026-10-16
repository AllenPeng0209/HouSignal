import {useCallback,useEffect,useRef,useState,type FormEvent} from 'react';
import {api,safeUrl} from './api.js';
import {useApp,useJob,useResource} from './hooks';
import {Badge,Button,ErrorNotice,Field,Icon,Loading,Modal} from './components';
import {PlatformLoginPanel,PlatformPublicationSms,PlatformPublishConfirmation,preparedSessionMatches,requestConfirmedPlatformPublish,requestPlatformAction,watchPlatformLogin,watchPlatformPublication,type LoginStatus,type PlatformAccount} from './PlatformAccounts';
import {PlatformCredentials} from './PlatformCredentials';
import {StudioPublishPreparation,matchingStudioPreparation} from './StudioPublishPreparation';
import {matchingReleaseJobs,releasePlatformAccounts,releaseResultState,type ReleaseJob,type ReviewedContent} from './content-review-release';
import type {Content,Job,Publication} from './types';

export function ContentRelease({content,releaseId,disabled}:{content:Content;releaseId:string;disabled:boolean}) {
 const {project,session,navigate,notify,reload}=useApp(),reviewed=content as ReviewedContent,editable=['owner','admin','content'].includes(session.role),base=`/api/projects/${project.id}`,accountsBase=`${base}/operations/platform-accounts`,key=`${project.id}:${content.id}:${releaseId}:${content.render_asset_id||''}`;
 const accounts=useResource<PlatformAccount[]>(accountsBase),history=useResource<ReleaseJob[]>(`${base}/jobs`),records=useResource<Publication[]>(`${base}/publications`),task=useJob();
 const available=releasePlatformAccounts(accounts.data||[],content),account=available[0],platform=account?.platform||'';
 const approved=Boolean(reviewed.status==='approved'&&reviewed.approval_valid===true&&content.render_asset_id&&releaseId&&(reviewed.approved_release_id||reviewed.release_id)===releaseId);
 const [localPrepared,setLocalPrepared]=useState<{key:string;job:Job|null}>({key,job:null}),[localPublication,setLocalPublication]=useState<{key:string;job:Job|null}>({key,job:null});
 const [confirmation,setConfirmation]=useState({key:'',value:false}),[error,setError]=useState(''),[notice,setNotice]=useState(''),[busy,setBusy]=useState(''),[login,setLogin]=useState<LoginStatus|null>(null),[qrSource,setQrSource]=useState(''),[qrError,setQrError]=useState(''),[loginCode,setLoginCode]=useState(''),[loginCodeError,setLoginCodeError]=useState('');
 const [publishChallenge,setPublishChallenge]=useState<LoginStatus|null>(null),[publishCode,setPublishCode]=useState(''),[publishCodeError,setPublishCodeError]=useState('');
 const [receipt,setReceipt]=useState({key,url:'',state:'live',publishedAt:''});
 const alive=useRef(true),scope=useRef(key),actions=useRef(new Set<AbortController>()),loginRequest=useRef<AbortController|null>(null),requestKey=useRef({scope:'',id:''}),pending=useRef(false),restored=useRef(new Set<string>());scope.current=key;
 const selectedPrepared=localPrepared.key===key?localPrepared.job:null;
 const saved=platform?history.data?.find(job=>matchingStudioPreparation(job,reviewed,releaseId,platform)):null;
 const prepared=selectedPrepared&&matchingStudioPreparation(selectedPrepared,reviewed,releaseId,String(selectedPrepared.result?.platform||''))?selectedPrepared:saved||null;
 const preview=prepared?.result?.publication_preview as {title?:string;body?:string}|undefined;
 const related=matchingReleaseJobs(history.data||[],content,releaseId,platform),publication=localPublication.key===key&&localPublication.job?localPublication.job:related[0]||null;
 const activePublication=publication&&['queued','running'].includes(publication.status)?publication:null,existingSubmission=Boolean(related.some(job=>['queued','running','succeeded'].includes(job.status))||publication&&['queued','running','succeeded'].includes(publication.status));
 const confirmationKey=`${key}:${platform}:${account?.session_id||''}:${prepared?.id||''}`,confirmed=confirmation.key===confirmationKey&&confirmation.value;
 const liveReady=Boolean(approved&&!disabled&&editable&&account&&prepared&&matchingStudioPreparation(prepared,reviewed,releaseId,platform)&&preparedSessionMatches(account,prepared.result?.session_id)),working=Boolean(busy)||task.busy;
 const acceptPrepared=useCallback((job:Job|null)=>setLocalPrepared({key,job}),[key]);
 const current=()=>alive.current&&scope.current===key;
 useEffect(()=>{alive.current=true;return()=>{alive.current=false;actions.current.forEach(controller=>controller.abort());loginRequest.current?.abort();};},[]);
 useEffect(()=>{actions.current.forEach(controller=>controller.abort());loginRequest.current?.abort();task.cancel();setLocalPrepared({key,job:null});setLocalPublication({key,job:null});setConfirmation({key:'',value:false});setError('');setNotice('');setBusy('');setLogin(null);setLoginCode('');setPublishCode('');setPublishCodeError('');setReceipt({key,url:'',state:'live',publishedAt:''});pending.current=false;requestKey.current={scope:'',id:''};},[key]);
 useEffect(()=>{setConfirmation({key:'',value:false});requestKey.current={scope:'',id:''};},[confirmationKey,account?.verified,account?.loggedIn]);
 async function finishPublication(id:string){const captured=key;restored.current.add(id);const completed=await task.wait(id);if(!alive.current||scope.current!==captured)return;if(completed){setLocalPublication({key:captured,job:completed});accounts.retry();records.retry();await reload();}history.retry();}
 useEffect(()=>{if(!editable||task.busy||pending.current||!activePublication||restored.current.has(activePublication.id))return;void finishPublication(activePublication.id);},[activePublication?.id,editable,key]);
 useEffect(()=>{
  setPublishChallenge(null);setPublishCode('');setPublishCodeError('');
  const job=task.job||activePublication;if(platform!=='douyin'||job?.kind!=='platform_publish'||!['queued','running'].includes(job.status))return;
  const controller=new AbortController(),captured=key;actions.current.add(controller);
  void watchPlatformPublication(accountsBase,platform,{signal:controller.signal,onStatus:value=>{if(alive.current&&scope.current===captured&&!controller.signal.aborted)setPublishChallenge(value);}}).catch(value=>{if(alive.current&&scope.current===captured&&!controller.signal.aborted)setPublishCodeError((value as Error).message);});
  return()=>{controller.abort();actions.current.delete(controller);};
 },[task.job?.id,task.job?.status,activePublication?.id,activePublication?.status,platform,key]);
 useEffect(()=>{
  setQrSource('');setQrError('');if(!account||login?.state!=='qr_ready'||!(login.qrReady||login.qr))return;
  const controller=new AbortController();let objectUrl='',active=true;
  fetch(`${accountsBase}/${encodeURIComponent(platform)}/qr?v=${encodeURIComponent(String(login.qrTs||''))}`,{credentials:'same-origin',signal:controller.signal,cache:'no-store'}).then(async response=>{if(!response.ok)throw new Error('二维码暂时不可用，请重新连接。');const blob=await response.blob();if(!blob.type.startsWith('image/'))throw new Error('平台未返回有效二维码。');if(active){objectUrl=URL.createObjectURL(blob);setQrSource(objectUrl);}}).catch(value=>{if(active&&!controller.signal.aborted)setQrError((value as Error).message);});
  return()=>{active=false;controller.abort();if(objectUrl)URL.revokeObjectURL(objectUrl);};
 },[key,platform,login?.state,login?.qrTs]);
 function closeLogin(){loginRequest.current?.abort();loginRequest.current=null;setLogin(null);setLoginCode('');setLoginCodeError('');if(busy==='login')setBusy('');}
 async function startLogin(){
  if(!editable||disabled||working||!account)return;const controller=new AbortController(),captured=key;loginRequest.current?.abort();loginRequest.current=controller;setLogin({state:'starting'});setLoginCode('');setLoginCodeError('');setError('');setBusy('login');
  const active=()=>alive.current&&scope.current===captured&&!controller.signal.aborted;
  try{const status=await requestPlatformAction(accountsBase,platform,'login',undefined,controller.signal);if(!active())return;setLogin(status);setBusy('');if(!['success','expired','error','cancelled','unknown','not_configured','idle'].includes(status.state))await watchPlatformLogin(accountsBase,platform,{signal:controller.signal,onStatus:value=>{if(active())setLogin(value);}});if(active())accounts.retry();}
  catch(value){if(active()){setLogin({state:'error',message:(value as Error).message});setError((value as Error).message);}}
  finally{if(active())setBusy('');}
 }
 async function endLogin(){
  if(!editable||!account||Boolean(busy)&&busy!=='login')return;loginRequest.current?.abort();loginRequest.current=null;
  const controller=new AbortController(),captured=key;actions.current.add(controller);setBusy('logout');setLoginCode('');setLoginCodeError('');
  try{await requestPlatformAction(accountsBase,platform,'logout',undefined,controller.signal);if(alive.current&&scope.current===captured&&!controller.signal.aborted){setLogin({state:'cancelled',message:'本次登录流程已结束。'});accounts.retry();}}
  catch(value){if(alive.current&&scope.current===captured&&!controller.signal.aborted)setError((value as Error).message);}
  finally{actions.current.delete(controller);if(alive.current&&scope.current===captured&&!controller.signal.aborted)setBusy('');}
 }
 async function accountAction(){
  if(!editable||working||disabled||!account)return;const controller=new AbortController(),captured=key;actions.current.add(controller);setBusy('verify');setError('');
  try{const status=await requestPlatformAction(accountsBase,platform,'verify',undefined,controller.signal);if(!alive.current||scope.current!==captured||controller.signal.aborted)return;setNotice(status.loggedIn&&status.verified?'账号已校验；如发布包绑定的是旧会话，请重新准备。':status.message||'账号需要重新登录。');accounts.retry();}
  catch(value){if(alive.current&&scope.current===captured&&!controller.signal.aborted)setError((value as Error).message);}
  finally{actions.current.delete(controller);if(alive.current&&scope.current===captured&&!controller.signal.aborted)setBusy('');}
 }
 async function submitCode(kind:'login'|'publication'){
  const code=(kind==='login'?loginCode:publishCode).replace(/\D/g,'');if(!editable||Boolean(busy)||kind==='login'&&task.busy||!account||!/^\d{4,8}$/.test(code))return;
  if(kind==='login'&&!loginRequest.current||kind==='publication'&&publishChallenge?.state!=='sms_required')return;
  const controller=new AbortController(),captured=key;actions.current.add(controller);setBusy(`${kind}-sms`);
  try{await requestPlatformAction(accountsBase,platform,kind==='login'?'sms':'publish-sms',{code},controller.signal);if(!alive.current||scope.current!==captured||controller.signal.aborted)return;if(kind==='login'){setLoginCode('');setLoginCodeError('');setLogin({state:'verifying'});}else{setPublishCode('');setPublishCodeError('');setPublishChallenge({state:'verifying'});}}
  catch(value){if(alive.current&&scope.current===captured&&!controller.signal.aborted)(kind==='login'?setLoginCodeError:setPublishCodeError)((value as Error).message);}
  finally{actions.current.delete(controller);if(alive.current&&scope.current===captured&&!controller.signal.aborted)setBusy('');}
 }
 async function publish(){
  if(!liveReady||!confirmed||working||pending.current||existingSubmission||!account||!prepared)return;
  const controller=new AbortController(),captured=key;actions.current.add(controller);pending.current=true;setBusy('publish');setError('');
  if(requestKey.current.scope!==confirmationKey)requestKey.current={scope:confirmationKey,id:crypto.randomUUID()};
  try{const queued=await requestConfirmedPlatformPublish(accountsBase,account,reviewed,prepared,requestKey.current.id,controller.signal);if(!alive.current||scope.current!==captured||controller.signal.aborted)return;if(!queued.job_id)throw new Error('平台未返回任务编号，请先刷新任务记录。');setConfirmation({key:'',value:false});setBusy('');history.retry();await finishPublication(queued.job_id);}
  catch(value){if(alive.current&&scope.current===captured&&!controller.signal.aborted)setError((value as Error).message);}
  finally{actions.current.delete(controller);if(alive.current&&scope.current===captured&&!controller.signal.aborted){pending.current=false;setBusy('');}}
 }
 async function copy(value:string,label:string){try{await navigator.clipboard.writeText(value);if(current())notify(`${label}已复制`);}catch{if(current())setError('无法访问剪贴板，请在下方文本中选中复制。');}}
 async function downloadRecord(){
  if(!approved||disabled||working)return;const controller=new AbortController(),captured=key;actions.current.add(controller);setBusy('export');setError('');
  try{const value=await api(`${base}/contents/${content.id}/export`,{signal:controller.signal});if(!alive.current||scope.current!==captured||controller.signal.aborted)return;if(value?.content?.id!==content.id||value?.release?.id!==releaseId)throw new Error('导出的审核版本已改变，请刷新当前内容。');const blob=new Blob([JSON.stringify(value,null,2)],{type:'application/json'}),url=URL.createObjectURL(blob),link=document.createElement('a');link.href=url;link.download=`${project.name}_内容v${content.version}_记录.json`;link.click();URL.revokeObjectURL(url);notify('已下载当前审核版本的内容记录');}
  catch(value){if(alive.current&&scope.current===captured&&!controller.signal.aborted)setError((value as Error).message);}
  finally{actions.current.delete(controller);if(alive.current&&scope.current===captured&&!controller.signal.aborted)setBusy('');}
 }
 async function register(event:FormEvent){
  event.preventDefault();if(!editable||!approved||disabled||working||receipt.key!==key)return;const url=safeUrl(receipt.url);if(!url){setError('请填写有效的 https 或 http 发布链接。');return;}
  if(records.data?.some(row=>row.release_id===releaseId&&safeUrl(row.url)===url)){setNotice('该链接已经登记，请在下方发布记录核对回执。');return;}
  const controller=new AbortController(),captured=key;actions.current.add(controller);setBusy('receipt');setError('');setNotice('');
  try{const record=await api(`${base}/publications`,{method:'POST',body:{release_id:releaseId,url,platform_state:receipt.state,...(receipt.publishedAt?{published_at:`${receipt.publishedAt}:00+08:00`}:{})},signal:controller.signal});if(!alive.current||scope.current!==captured||controller.signal.aborted)return;if(record?.release_id!==releaseId||safeUrl(record?.url)!==url)throw new Error('发布记录返回的审核版本或链接与当前内容不一致，请核对已有记录后重试。');setNotice('真实链接已登记，等待审核负责人核对回执。');setReceipt({key,url:'',state:'live',publishedAt:''});records.retry();await reload();}
  catch(value){if(alive.current&&scope.current===captured&&!controller.signal.aborted)setError((value as Error).message);}
  finally{actions.current.delete(controller);if(alive.current&&scope.current===captured&&!controller.signal.aborted)setBusy('');}
 }
 const resultHref=safeUrl(publication?.result?.url),resultState=publication?releaseResultState(publication):null;
 return <section className="content-release" aria-label="发布当前内容">
  <div className="card content-release-summary"><div className="card-header"><div><h2>从当前成片到真实发布回执</h2><p>内容 v{content.version} · {content.channel} · 审核版本 {releaseId||'尚未审核'}</p></div><Badge tone={approved?'green':'amber'}>{approved?'审核版本有效':'等待当前版本审核'}</Badge></div><ol className="content-release-steps"><li>核对文件与文案</li><li>准备并确认平台账号</li><li>提交后等待平台处理</li><li>登记链接并核实回执</li></ol>{disabled&&<p className="form-note">请先完成当前编辑或制作任务，再继续发布。</p>}</div>
  <div className="card content-release-files"><h3>1. 核对文件与文案</h3><div className="inline-actions">{approved&&!disabled&&content.render_asset_id&&<a className="btn primary" href={`${base}/assets/${content.render_asset_id}/file`} download><Icon name="download" size={15}/>下载 MP4</a>}<Button icon="download" disabled={!approved||disabled||working} onClick={()=>void downloadRecord()}>下载内容记录</Button><Button icon="copy" disabled={!approved||disabled} onClick={()=>void copy(preview?.title||content.cover_title||content.topic,'标题')}>复制标题</Button><Button icon="copy" disabled={!approved||disabled} onClick={()=>void copy(preview?.body||content.script,'正文')}>复制正文</Button></div><details><summary>{preview?'平台将使用的标题与正文':'当前审核版本标题与正文'}</summary><h4>{preview?.title||content.cover_title||content.topic}</h4><pre className="content-release-copy">{preview?.body||content.script}</pre></details><small className="form-note">内容记录是 JSON 核验记录；可直接使用的文件请下载 MP4 或下方 ZIP 发布包。</small></div>
  <section className="card content-release-platform"><h3>2. 准备发布包与平台账号</h3>{accounts.loading?<Loading text="正在读取对应平台账号…"/>:accounts.error?<ErrorNotice message={accounts.error} retry={accounts.retry}/>:account?<div className="content-release-account"><div><strong>{account.name} · {account.identity_name||'账号身份待确认'}</strong><Badge tone={account.loggedIn&&account.verified?'green':'amber'}>{account.loggedIn&&account.verified?'当前账号已校验':account.loggedIn?'需要校验':'尚未登录'}</Badge></div><p>{account.note||'登录和发布操作只使用本项目的平台会话。'}</p><div className="inline-actions"><Button disabled={!editable||disabled||working} onClick={()=>void startLogin()}>{account.loggedIn?'重新登录':'登录此平台'}</Button><Button icon="shield" disabled={!editable||disabled||working||!account.loggedIn} busy={busy==='verify'} onClick={()=>void accountAction()}>校验当前账号</Button><Button icon="refresh" disabled={working} onClick={()=>{accounts.retry();history.retry();records.retry();}}>刷新状态</Button></div></div>:<p className="form-note">当前渠道暂无可用平台账号。可以先下载文件并登记真实发布链接。</p>}{platform==='wechat-oa'&&<PlatformCredentials key={`${key}:${account?.session_id||''}`} base={accountsBase} editable={editable&&!disabled} onSaved={accounts.retry}/>}
   <StudioPublishPreparation key={key} content={reviewed} releaseId={releaseId} disabled={disabled} busy={working} onPrepared={acceptPrepared}/>
   {prepared&&account&&!preparedSessionMatches(account,prepared.result?.session_id)&&<p className="form-note">这个发布包尚未绑定当前已校验账号。完成登录与校验后，点击「准备平台发布包」重新准备，再确认提交。</p>}
  </section>
  <section className="card content-release-submit"><h3>3. 确认提交与平台处理</h3>{prepared&&account?<PlatformPublishConfirmation content={reviewed} preparedContentId={content.id} preparedSessionId={prepared.result?.session_id} preview={prepared.result?.publication_preview} account={account} confirmed={confirmed} onConfirmed={value=>setConfirmation({key:confirmationKey,value})} onPublish={()=>void publish()} editable={liveReady&&!existingSubmission} busy={working}/>:<p className="form-note">先准备当前审核版本的发布包，并核对真实平台账号、标题和正文。</p>}{task.busy&&<div className="platform-task-progress" role="status"><span className="spinner"/>{task.statusText}<progress max={100} value={task.percent} aria-label="平台提交进度"/></div>}{publishChallenge&&<PlatformPublicationSms status={publishChallenge} code={publishCode} error={publishCodeError} busy={busy==='publication-sms'} onCode={setPublishCode} onSubmit={()=>void submitCode('publication')}/>} {publishCodeError&&publishChallenge?.state!=='sms_required'&&<ErrorNotice message={publishCodeError}/>}{task.error&&<ErrorNotice message={task.error} retry={history.retry}/>}{publication&&resultState&&<div className="content-release-result" role="status"><Badge tone={resultState.tone}>{resultState.label}</Badge>{typeof publication.result?.message==='string'&&<p>{publication.result.message}</p>}{publication.error&&<p>{publication.error}</p>}{resultHref&&<a className="small-link" href={resultHref} target="_blank" rel="noreferrer">查看平台结果<Icon name="external" size={13}/></a>}<small>任务 {publication.id} · 平台提交完成仍需核对真实回执。</small></div>}</section>
  <section className="card content-release-receipt"><h3>4. 登记真实发布链接</h3>{records.data?.some(row=>row.release_id===releaseId)&&<p className="form-note">这条审核版本已有发布记录，可在下方核对回执与回填 D7／D30。</p>}<form onSubmit={event=>void register(event)}><Field label="真实发布链接"><input type="url" required value={receipt.key===key?receipt.url:''} disabled={!editable||!approved||disabled||working} placeholder="粘贴平台实际作品链接" onChange={event=>setReceipt(previous=>({...previous,key,url:event.target.value}))}/></Field><div className="form-grid"><Field label="平台当前状态"><select value={receipt.key===key?receipt.state:'live'} disabled={!editable||!approved||disabled||working} onChange={event=>setReceipt(previous=>({...previous,key,state:event.target.value}))}><option value="live">已发布可见</option><option value="pending">平台审核中</option><option value="removed">已下线</option></select></Field><Field label="实际发布时间（上海，可选）" hint="未知则留空；按真实发布时间安排 D7／D30 观察。"><input type="datetime-local" aria-label="实际发布时间（上海，可选）" value={receipt.key===key?receipt.publishedAt:''} disabled={!editable||!approved||disabled||working} onChange={event=>setReceipt(previous=>({...previous,key,publishedAt:event.target.value}))}/></Field></div><p className="form-note">登记链接后仍为待核实；审核负责人核对平台状态后，才能计入已核实发布。</p><div className="form-actions"><Button primary type="submit" disabled={!editable||!approved||disabled||working||receipt.key!==key||!receipt.url.trim()}>保存发布记录</Button><Button icon="chart" onClick={()=>navigate('reports')}>查看效果分析</Button></div></form></section>
  {error&&<ErrorNotice message={error}/>}<p className="content-release-notice" role="status">{notice}</p>{!editable&&<p className="form-note">平台操作和发布登记需要项目管理员或内容负责人。</p>}
  {login&&account&&<Modal title={`登录 ${account.name}`} onClose={closeLogin} footer={<Button onClick={closeLogin}>{login.state==='success'?'完成':'关闭面板'}</Button>}><PlatformLoginPanel account={account} status={login} qrSource={qrSource} qrError={qrError} smsCode={loginCode} smsError={loginCodeError} smsBusy={busy==='login-sms'} onSmsCode={setLoginCode} onSms={()=>void submitCode('login')} onRetry={()=>void startLogin()} onCancel={()=>void endLogin()} cancelBusy={busy==='logout'}/><p className="form-note">请在平台官方页面完成登录与手机确认，然后返回这里核对当前账号。</p></Modal>}
 </section>;
}
