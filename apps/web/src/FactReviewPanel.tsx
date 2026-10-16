import {useEffect,useState} from 'react';
import {api} from './api.js';
import {buildFactReviewRows,verifySelectedFacts} from './batch-intake.js';
import {useApp} from './hooks';
import {Badge,Button,ErrorNotice,formatDate} from './components';
import type {Asset,Fact} from './types';
type Candidate={key:string;assetId:string;assetName:string;label?:string;value:string;unit?:string;source:string;source_excerpt?:string};
type ReviewRow={fact:Fact;candidates:Candidate[];conflict:boolean;missing:boolean;issue:string;canVerify:boolean};
export function FactReviewPanel({facts,assets,onEdit,onRefresh}:{facts:Fact[];assets:Asset[];onEdit:(fact:Fact)=>void;onRefresh:()=>void}){
 const {project,canEdit,canReview,reload,notify}=useApp(),rows=buildFactReviewRows(facts,assets) as ReviewRow[];
 const [selected,setSelected]=useState<{id:string;revision:number}[]>([]),[confirmed,setConfirmed]=useState(false),[busy,setBusy]=useState(false),[error,setError]=useState(''),[rowErrors,setRowErrors]=useState<Record<string,string>>({});
 const revisionKey=facts.map(fact=>`${fact.id}:${fact.revision}`).join('|');
 useEffect(()=>{setSelected(value=>value.filter(item=>facts.some(fact=>fact.id===item.id&&fact.revision===item.revision&&fact.status!=='verified')));setConfirmed(false);},[revisionKey]);
 async function verify(){
  if(busy)return;setBusy(true);setError('');setRowErrors({});
  try{const results=await verifySelectedFacts(selected,{canReview,confirmed},{loadFacts:()=>api(`/api/projects/${project.id}/facts`),verify:(id:string,body:Record<string,unknown>)=>api(`/api/projects/${project.id}/facts/${id}/verify`,{method:'POST',body})});const failures:Record<string,string>={};for(const result of results)if(result.error)failures[result.id]=result.error;setRowErrors(failures);setSelected(value=>value.filter(item=>!!failures[item.id]));setConfirmed(false);notify(`已核验 ${results.filter(result=>result.fact).length} 项${Object.keys(failures).length?'，未通过项目已保留提示':''}`);await reload();onRefresh();}
  catch(cause){setError((cause as Error).message);}finally{setBusy(false);}
 }
 async function adopt(fact:Fact,candidate:Candidate){
  if(!canEdit||busy)return;setBusy(true);setError('');
  try{await api(`/api/projects/${project.id}/facts/${fact.id}`,{method:'PATCH',body:{value:candidate.value,unit:candidate.unit||'',source:candidate.source,valid_until:fact.valid_until||null,expected_revision:fact.revision}});setSelected(value=>value.filter(item=>item.id!==fact.id));setConfirmed(false);notify('候选已保存为新版本草稿，仍待人工核验');await reload();onRefresh();}
  catch(cause){setError((cause as Error).message);}finally{setBusy(false);}
 }
 const pendingRows=rows.filter(row=>row.fact.status!=='verified'||row.conflict||row.missing);
 return <section className="card fact-review-panel" aria-label="集中核验"><div className="card-header"><div><h3>集中核验</h3><p className="form-note">集中比对提取候选与当前保存版本；每项由人核对后勾选。</p></div><Button icon="refresh" disabled={busy} onClick={onRefresh}>刷新来源</Button></div><div className="fact-review-summary"><Badge tone="amber">{rows.filter(row=>row.fact.status!=='verified').length} 项待人工核验</Badge><Badge tone="red">{rows.filter(row=>row.conflict).length} 项来源冲突</Badge><Badge>{rows.filter(row=>row.missing).length} 项缺失或过期</Badge></div>
  <div className="fact-review-list">{pendingRows.map(row=><article className={`fact-review-row ${row.conflict?'has-conflict':''}`} key={row.fact.id}><div className="fact-review-current"><label className="checkbox-line"><input type="checkbox" disabled={!canReview||busy||!row.canVerify} checked={selected.some(item=>item.id===row.fact.id&&item.revision===row.fact.revision)} onChange={event=>{setSelected(value=>event.target.checked?[...value,{id:row.fact.id,revision:row.fact.revision}]:value.filter(item=>item.id!==row.fact.id));setConfirmed(false);}}/><strong>{row.fact.label}</strong><Badge>当前 v{row.fact.revision}</Badge></label><p><strong>{row.fact.value?`${row.fact.value} ${row.fact.unit||''}`:'待补充'}</strong></p><p className="source-cell">{row.fact.source||'尚未提供来源'}</p><small>有效期：{row.fact.valid_until?formatDate(row.fact.valid_until):'未设置'}</small>{row.conflict&&<p className="fact-conflict-note">来源冲突：发现不同数值，请逐一比对后确认当前保存版本。</p>}{row.issue&&<p className="fact-conflict-note">{row.issue}</p>}<Button disabled={busy} icon={canEdit?'edit':'file'} onClick={()=>onEdit(row.fact)}>{canEdit?'修改 / 单项核验':'查看资料'}</Button></div>
   {row.candidates.length>0&&<div className="fact-candidate-list"><strong>提取候选 · 待人工核验</strong>{row.candidates.map(candidate=><div className="fact-candidate" key={candidate.key}><p><strong>{candidate.value} {candidate.unit||''}</strong></p><p>{candidate.source}</p>{candidate.source_excerpt&&<blockquote>{candidate.source_excerpt}</blockquote>}<div className="inline-actions"><a className="text-btn" href={`/api/projects/${project.id}/assets/${candidate.assetId}/file`} target="_blank" rel="noreferrer">查看 {candidate.assetName}</a><Button disabled={!canEdit||busy||(row.fact.value===candidate.value&&(row.fact.unit||'')===(candidate.unit||'')&&row.fact.source===candidate.source)} onClick={()=>void adopt(row.fact,candidate)}>采纳为待核验草稿</Button></div></div>)}</div>}{rowErrors[row.fact.id]&&<div className="fact-review-error"><ErrorNotice message={rowErrors[row.fact.id]}/></div>}
  </article>)}</div>{!pendingRows.length&&<p className="form-note">当前保存资料已核验，暂无候选冲突或缺失。</p>}
  <div className="fact-review-actions"><label className="checkbox-line"><input type="checkbox" disabled={!canReview||busy||!selected.length} checked={confirmed} onChange={event=>setConfirmed(event.target.checked)}/>我已逐项核对勾选保存版本的数值、来源冲突和有效期，确认资料与依据一致</label><Button primary icon="check" busy={busy} disabled={!canReview||!confirmed||!selected.length} onClick={()=>void verify()}>人工确认核验 {selected.length} 项</Button>{!canReview&&<p className="form-note">人工核验需要审核负责人或组织管理员权限。</p>}{error&&<ErrorNotice message={error}/>}</div>
 </section>;
}
