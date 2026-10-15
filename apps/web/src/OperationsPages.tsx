import {useEffect, useRef, useState, type FormEvent} from 'react';
import {api, safeUrl} from './api.js';
import {useApp, useMutation, useResource} from './hooks';
import {Badge, Button, Empty, ErrorNotice, Field, Heading, Icon, Loading, Modal} from './components';
import './operations.css';
import {ResearchWorkspace} from './ResearchWorkspace';
import type {ContentPlanDraft} from './ContentDirectionGuide';
import {PlatformAccounts} from './PlatformAccounts';

const platforms=['抖音','小红书','快手','知乎','B站','微信视频号','微信公众号'];
interface Account {id:string;platform:string;name:string;homepage:string;positioning:string;audience:string;voice:string;red_lines:string;status:'managed'|'paused'}
const canOperate=(role:string)=>['owner','admin','content'].includes(role);
const defaultPlatform=(value:string)=>platforms.includes(value)?value:'抖音';
function useDialogLifetime(){const mounted=useRef(true);useEffect(()=>{mounted.current=true;return()=>{mounted.current=false;};},[]);return mounted;}
function SourceLink({url,label='查看原始来源'}:{url:string;label?:string}){const href=safeUrl(url);return href?<a className="operations-source" href={href} target="_blank" rel="noreferrer"><Icon name="external" size={12}/>{label}<span>{new URL(href).hostname}</span></a>:<span className="operations-source unavailable">来源链接不可用</span>;}
function PermissionNote({editable}:{editable:boolean}){return editable?null:<p className="operations-permission"><Icon name="lock" size={13}/>当前角色可查看；修改需要项目管理员或内容负责人。</p>;}
function Search({value,onChange,placeholder}:{value:string;onChange:(value:string)=>void;placeholder:string}){return <label className="operations-search"><Icon name="search" size={16}/><input type="search" value={value} aria-label={placeholder} placeholder={placeholder} onChange={e=>onChange(e.target.value)}/></label>;}

export function AccountsPage(){
 const {project,session}=useApp(),editable=canOperate(session.role),base=`/api/projects/${project.id}/operations`;
 const accounts=useResource<Account[]>(`${base}/accounts`);
 const [filter,setFilter]=useState('all'),[search,setSearch]=useState(''),[editing,setEditing]=useState<Account|null>(null),[creating,setCreating]=useState(false);
 const rows=(accounts.data||[]).filter(a=>(filter==='all'||a.platform===filter)&&`${a.name} ${a.positioning} ${a.audience}`.toLowerCase().includes(search.trim().toLowerCase()));
 return <div className="operations-page"><Heading eyebrow="ACCOUNTS & OPERATIONS" title="账号登录 Accounts" subtitle="连接项目的平台账号，让楼管家为你准备内容与运营操作。"/>
  <PlatformAccounts/>
  <div className="platform-profile-heading"><div><h2>账号画像</h2><p>管理运营账号的受众、定位与表达边界，作为楼管家策划内容的上下文。</p></div><Button primary icon="plus" disabled={!editable} onClick={()=>setCreating(true)}>新增账号</Button></div>
  <div className="operations-account-intro"><span className="operations-intro-icon"><Icon name="users" size={23}/></span><div><h2>每个账号，都有清晰的表达方向。</h2><p>账号资料会成为选题与内容计划的上下文。先连接上方平台会话，再为账号制定内容计划。</p></div><span className="operations-total">{accounts.data?.length??'—'}<small>项目账号</small></span></div>
  <PermissionNote editable={editable}/><div className="operations-toolbar"><div className="operations-pills"><button className={filter==='all'?'active':''} onClick={()=>setFilter('all')}>全部平台</button>{platforms.map(p=><button key={p} className={filter===p?'active':''} onClick={()=>setFilter(p)}>{p}</button>)}</div><Search value={search} onChange={setSearch} placeholder="搜索账号或定位"/></div>
  {accounts.loading?<Loading text="正在读取项目账号…"/>:accounts.error?<ErrorNotice message={accounts.error} retry={accounts.retry}/>:!rows.length?<div className="card operations-empty"><Empty icon="users" title={accounts.data?.length?'没有符合条件的账号':'还没有运营账号'} action={editable&&!accounts.data?.length?<Button primary onClick={()=>setCreating(true)}>新增第一个账号</Button>:undefined}>{accounts.data?.length?'调整平台筛选或搜索关键词。':'先登记账号与定位，再为它规划内容。'}</Empty></div>:<div className="operations-account-grid">{rows.map(account=><article className="card operations-account-card" key={account.id}><div className="operations-card-top"><span className="operations-platform-icon">{account.platform.slice(0,1)}</span><div><span className="operations-kicker">{account.platform}</span><h3>{account.name}</h3></div><Badge tone={account.status==='managed'?'blue':'neutral'}>{account.status==='managed'?'运营中':'已暂停'}</Badge></div>{account.homepage?<SourceLink url={account.homepage} label="打开账号主页"/>:<span className="operations-no-homepage">账号主页待补充</span>}<dl className="operations-profile">{[['账号定位',account.positioning],['目标受众',account.audience],['人设与声音',account.voice],['表达边界',account.red_lines]].map(([label,value])=><div key={label}><dt>{label}</dt><dd>{value||'待完善'}</dd></div>)}</dl><div className="operations-card-footer"><span>{Object.values({positioning:account.positioning,audience:account.audience,voice:account.voice,red_lines:account.red_lines}).filter(Boolean).length}/4 项画像已填写</span><Button icon="edit" disabled={!editable} onClick={()=>setEditing(account)}>编辑画像</Button></div></article>)}</div>}
  {(creating||editing)&&<AccountDialog account={editing} base={base} onClose={()=>{setCreating(false);setEditing(null);}} onSaved={accounts.retry}/>}</div>;
}
function AccountDialog({account,base,onClose,onSaved}:{account:Account|null;base:string;onClose:()=>void;onSaved:()=>void}){
 const {project,session}=useApp(),editable=canOperate(session.role),mutation=useMutation(),mounted=useDialogLifetime();
 const [form,setForm]=useState({platform:account?.platform||defaultPlatform(project.channel),name:account?.name||'',homepage:account?.homepage||'',positioning:account?.positioning||'',audience:account?.audience||'',voice:account?.voice||'',red_lines:account?.red_lines||'',status:account?.status||'managed'});
 const change=(key:keyof typeof form,value:string)=>setForm(s=>({...s,[key]:value}));
 async function submit(e:FormEvent){e.preventDefault();if(!editable)return;if(form.homepage.trim()&&!safeUrl(form.homepage.trim())){mutation.setError('请填写有效的 http 或 https 账号主页链接。');return;}const row=await mutation.run<Account>(()=>api(`${base}/accounts${account?`/${account.id}`:''}`,{method:account?'PATCH':'POST',body:Object.fromEntries(Object.entries(form).map(([k,v])=>[k,v.trim()]))}),account?'账号画像已保存':'项目账号已登记',false);if(row&&mounted.current){onSaved();onClose();}}
 return <Modal title={account?'编辑账号与画像':'新增运营账号'} onClose={onClose} wide><form onSubmit={submit}><div className="form-grid"><Field label="账号名称"><input required maxLength={160} value={form.name} disabled={!editable} onChange={e=>change('name',e.target.value)} placeholder="例如：杭州改善选房"/></Field><Field label="运营平台"><select value={form.platform} disabled={!editable} onChange={e=>change('platform',e.target.value)}>{platforms.map(p=><option key={p}>{p}</option>)}</select></Field></div><Field label="账号主页" hint="填写公开主页地址，便于团队核对账号。"><input type="url" value={form.homepage} disabled={!editable} onChange={e=>change('homepage',e.target.value)} placeholder="https://…"/></Field><div className="form-grid"><Field label="账号定位"><textarea value={form.positioning} disabled={!editable} onChange={e=>change('positioning',e.target.value)} placeholder="核心垂类、差异点与内容价值" rows={3}/></Field><Field label="目标受众"><textarea value={form.audience} disabled={!editable} onChange={e=>change('audience',e.target.value)} placeholder="谁会看、他们希望解决什么问题" rows={3}/></Field><Field label="人设与声音"><textarea value={form.voice} disabled={!editable} onChange={e=>change('voice',e.target.value)} placeholder="专业程度、用词习惯与叙事语气" rows={3}/></Field><Field label="表达边界"><textarea value={form.red_lines} disabled={!editable} onChange={e=>change('red_lines',e.target.value)} placeholder="避免的表述、未经核验的承诺与禁用话题" rows={3}/></Field></div><Field label="运营状态"><select value={form.status} disabled={!editable} onChange={e=>change('status',e.target.value)}><option value="managed">运营中</option><option value="paused">已暂停</option></select></Field>{mutation.error&&<ErrorNotice message={mutation.error}/>}<div className="form-actions"><Button onClick={onClose}>取消</Button><Button primary type="submit" busy={mutation.busy} disabled={!editable}>保存账号</Button></div></form></Modal>;
}

export function DiscoveryPanel({onPlan,onOpenPlan,onOpenContent}:{onPlan:(draft?:ContentPlanDraft)=>void;onOpenPlan?:(id:string)=>void;onOpenContent?:(id:string)=>void}) {
 const {project}=useApp();
 return <ResearchWorkspace key={project.id} onPlan={onPlan} onOpenPlan={onOpenPlan} onOpenContent={onOpenContent}/>;
}
export {PlanningPanel} from './PlanningPanel';
