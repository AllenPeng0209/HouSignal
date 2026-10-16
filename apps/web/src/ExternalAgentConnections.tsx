import {useState} from 'react';
import {api} from './api.js';
import {useApp,useMutation,useResource} from './hooks';
import {Badge,Button,ErrorNotice,Loading} from './components';

type AgentConnection={id:string;client_name:string;scopes:string[];project_ids?:string[]|null;project_names?:string[];expires_at:string|null;revoked_at?:string|null};
type ProjectName={id:string;name:string};
const scopeNames:Record<string,string>={'louxiang:read':'查询项目资料','louxiang:write':'编辑资料与策划','louxiang:production':'提交制作任务','offline_access':'保持连接'};
const connectionDate=(value:string)=>new Date(/(?:Z|[+-]\d{2}:?\d{2})$/i.test(value)?value:`${value}Z`);
const dateLabel=(value:string)=>{const date=connectionDate(value);return Number.isNaN(date.valueOf())?'日期待确认':date.toLocaleString('zh-CN',{year:'numeric',month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit'});};
export function ExternalAgentConnectionList({connections,projects,busy,onRevoke}:{connections:AgentConnection[];projects:ProjectName[];busy:boolean;onRevoke:(id:string)=>void}) {
 if(!connections.length)return <p className="form-note">尚未连接外部 Agent。连接后可在这里查看授权范围和撤销访问。</p>;
 return <div className="member-list">{connections.map(connection=>{
  const expired=!!connection.expires_at&&connectionDate(connection.expires_at).valueOf()<=Date.now();
  const projectNames=connection.project_names?.length?connection.project_names:connection.project_ids?.map(id=>projects.find(project=>project.id===id)?.name||'受限项目')||[];
  return <div className="member-row" key={connection.id}><div><strong>{connection.client_name||'外部 Agent'}</strong><small>项目：{projectNames.length?projectNames.join('、'):'授权项目'}</small><small>权限：{connection.scopes.map(scope=>scopeNames[scope]||scope).join('、')||'无业务权限'}</small><small>{connection.expires_at?`有效期至 ${dateLabel(connection.expires_at)}`:'有效期由连接授权管理'}</small></div><Badge tone={connection.revoked_at||expired?'neutral':'green'}>{connection.revoked_at?'已撤销':expired?'已到期':'已连接'}</Badge>{!connection.revoked_at&&!expired&&<Button disabled={busy} onClick={()=>onRevoke(connection.id)}>撤销连接</Button>}</div>;
 })}</div>;
}

function CopyValue({label,value}:{label:string;value:string}) {
 const [copied,setCopied]=useState(false),[error,setError]=useState('');
 async function copy(){try{await navigator.clipboard.writeText(value);setCopied(true);setError('');}catch{setCopied(false);setError('复制未完成，请选中后手动复制');}}
 return <div style={{margin:'12px 0'}}><div className="setting-line"><span>{label}</span><button type="button" className="btn" aria-label={`复制${label}`} onClick={()=>void copy()}>{copied?'已复制':'复制'}</button></div><code style={{display:'block',whiteSpace:'pre-wrap',overflowWrap:'anywhere',fontSize:12,lineHeight:1.7}}>{value}</code>{error&&<p role="status" className="form-note">{error}</p>}</div>;
}

export function ExternalAgentConnections(){
 const {projects}=useApp();
 const connections=useResource<{connections:AgentConnection[]}|AgentConnection[]>('/api/agent/connections');
 const mutation=useMutation();
 const mcpUrl=`${window.location.origin}/mcp`;
 const rows=Array.isArray(connections.data)?connections.data:connections.data?.connections||[];
 async function revoke(id:string){await mutation.run(()=>api(`/api/agent/connections/${encodeURIComponent(id)}`,{method:'DELETE'}),'外部 Agent 连接已撤销');}
 return <article className="card settings-card" aria-labelledby="external-agent-title"><div className="card-header"><h3 id="external-agent-title">外部 Agent</h3><Badge tone="blue">与 Web 同步</Badge></div><p className="form-note">在 ChatGPT 或 Codex 中连接楼响，按授权项目查询资料、编排内容和跟进任务。保存的更改会同步到项目工作区。</p><CopyValue label="远程 MCP 地址" value={mcpUrl}/><details><summary>连接 Codex CLI</summary><CopyValue label="添加楼响" value={`codex mcp add louxiang --url ${mcpUrl}`}/><CopyValue label="登录授权" value="codex mcp login louxiang"/></details><details style={{marginTop:16}}><summary>连接 ChatGPT</summary><p className="form-note">在 ChatGPT 的设置中打开应用连接与开发者模式，创建个人应用，将远程 MCP 地址填入连接地址，选择 OAuth 后登录楼响并确认项目权限。菜单名称以当前 ChatGPT 页面为准。</p></details><div className="form-divider">我的连接</div>{connections.loading?<Loading text="正在读取已授权连接…"/>:connections.error?<ErrorNotice message={connections.error} retry={connections.retry}/>:<ExternalAgentConnectionList connections={rows} projects={projects} busy={mutation.busy} onRevoke={id=>void revoke(id)}/>} {mutation.error&&<ErrorNotice message={mutation.error}/>}</article>;
}
