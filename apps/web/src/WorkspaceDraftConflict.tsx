import {Button,formatDate} from './components';
import type {DraftAlternative} from './workspace-draft-store.js';
interface ConflictState<T>{conflict:boolean;conflictKind:'server'|'concurrent'|null;alternatives:DraftAlternative<T>[];restoreLocal:()=>void;restoreAlternative:(id:string)=>void;useServer:()=>void}
function DraftPreview({value}:{value:unknown}){
 const record=value&&typeof value==='object'?value as Record<string,unknown>:{},request=record.request&&typeof record.request==='object'?record.request as Record<string,unknown>:{};
 const fields=[['封面标题',record.cover],['脚本',record.script],['策划想法',request.intent],['补充要求',record.entryBrief]].filter((field):field is [string,string]=>typeof field[1]==='string'&&Boolean(field[1]));
 return <>{fields.map(([label,text])=><div key={label}><strong>{label}</strong><pre className="record-text" style={{whiteSpace:'pre-wrap',overflowWrap:'anywhere',maxHeight:220,overflowY:'auto'}}>{text}</pre></div>)}{!fields.length&&<p>这份草稿保留了已填写的内容与设置，可以选择恢复后查看。</p>}</>;
}
export function WorkspaceDraftConflict<T>({draft,localLabel='恢复本地草稿',serverLabel='使用服务器版本',serverMessage='本地草稿与最新服务器内容不同。恢复本地草稿会作为新的编辑保留，不会直接覆盖服务器版本。'}:{draft:ConflictState<T>;localLabel?:string;serverLabel?:string;serverMessage?:string}){
 if(!draft.conflict)return null;const concurrent=draft.conflictKind==='concurrent';
 return <section className="card draft-conflict" role="alert"><p>{concurrent?'另一个窗口也编辑了这份内容。各窗口草稿已分别保留，请选择继续使用的版本。':serverMessage}</p><div className="form-actions"><Button onClick={draft.useServer}>{serverLabel}</Button><Button primary onClick={draft.restoreLocal}>{concurrent?'保留本窗口草稿':localLabel}</Button></div>{draft.alternatives.map((value,index)=><details key={value.id} open><summary>其他窗口草稿 {index+1} · {formatDate(new Date(value.savedAt).toISOString())}</summary><DraftPreview value={value.value}/><Button onClick={()=>draft.restoreAlternative(value.id)}>使用这份草稿</Button></details>)}</section>;
}
