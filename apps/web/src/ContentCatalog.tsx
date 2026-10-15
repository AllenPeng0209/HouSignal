import {safeUrl} from './api.js';
import {Button,Empty,ErrorNotice,Icon,Loading} from './components';
import {contentDirections,createContentPlanDraft,type ContentPlanDraft,type ContentTypeId} from './ContentDirectionGuide';
import {CatalogVideoCover,CatalogVideoPlayer,catalogSourceUrl} from './CatalogVideoPlayer';
export {CatalogVideoPlayer,catalogCoverUrl,catalogPlayerUrl} from './CatalogVideoPlayer';
import type {DiscoveryItem} from './DiscoverySearch';
import {useState,type KeyboardEvent} from 'react';

export interface CatalogItem extends DiscoveryItem {
 job_id?:string;reference_id?:string;catalog_only?:boolean;subject?:string;subjects?:string[];collected_at?:string|null;index_collected_at?:string|null;cover_url?:string|null;player_url?:string|null;
}
export interface CatalogCategory {
 id:ContentTypeId;label:string;purpose:string;subjects:{label:string;query:string}[];items:CatalogItem[];
 updated_at:string|null;collection_state:'ready'|'collecting'|'pending'|'unavailable';
}
export interface ResearchCatalog {categories:CatalogCategory[];updated_at:string|null;total:number}
const platformLabels:Record<string,string>={douyin:'抖音',xiaohongshu:'小红书',bilibili:'B站'};
const metric=(value:unknown)=>typeof value==='number'&&Number.isFinite(value)?new Intl.NumberFormat('zh-CN').format(value):'待观察';
function dateTime(value:string|null|undefined){
 if(!value)return '时间待补充';const date=new Date(value);
 return Number.isNaN(date.getTime())?'时间待补充':new Intl.DateTimeFormat('zh-CN',{month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit',hour12:false,timeZone:'Asia/Shanghai'}).format(date);
}

export function ContentCatalog({catalog,type,subject,loading=false,error='',retry,editable,busyItemKey='',onSelectType,onSelectSubject,onCapture,onOpenReference,onPlan,onPlanItem}:{
 catalog:ResearchCatalog|null;type:ContentTypeId;subject:string;loading?:boolean;error?:string;retry?:()=>void;editable:boolean;busyItemKey?:string;
 onSelectType:(type:ContentTypeId)=>void;onSelectSubject:(query:string)=>void;onCapture:(item:CatalogItem)=>void;onOpenReference:(id:string)=>void;onPlan?:(draft:ContentPlanDraft)=>void;onPlanItem?:(item:CatalogItem)=>void;
}) {
 const [playing,setPlaying]=useState<CatalogItem|null>(null);
 const category=catalog?.categories?.find(c=>c.id===type),direction=contentDirections.find(d=>d.id===type)!;
 const subjects=category?.subjects||direction.subjects,selected=subjects.find(s=>s.query===subject);
 const items=(category?.items||[]).filter(item=>!subject||[item.subject,...(item.subjects||[])].some(value=>value===subject||value===selected?.label));
 const draft=createContentPlanDraft(type,subject),state=category?.collection_state||'pending';
 function moveCategory(event:KeyboardEvent<HTMLButtonElement>){
  const index=contentDirections.findIndex(direction=>direction.id===type),last=contentDirections.length-1;
  const next=event.key==='ArrowRight'?(index+1)%(last+1):event.key==='ArrowLeft'?(index+last)%(last+1):event.key==='Home'?0:event.key==='End'?last:null;
  if(next===null)return;event.preventDefault();const id=contentDirections[next].id;onSelectType(id);
  event.currentTarget.parentElement?.querySelector<HTMLButtonElement>(`#content-type-${id}`)?.focus();
 }
 return <section className="content-catalog" aria-label="按内容类型浏览灵感">
  <div className="content-catalog-types" role="tablist" aria-label="内容类型">{contentDirections.map(d=><button key={d.id} id={`content-type-${d.id}`} type="button" role="tab" aria-label={d.label} aria-selected={type===d.id} aria-controls="content-catalog-panel" tabIndex={type===d.id?0:-1} className={type===d.id?'active':''} onClick={()=>onSelectType(d.id)} onKeyDown={moveCategory}>{d.label}</button>)}</div>
  <div role="tabpanel" id="content-catalog-panel" aria-labelledby={`content-type-${type}`}>
   <div className="content-catalog-purpose"><p>{category?.purpose||direction.purpose}</p>{category?.updated_at&&<small>更新于 {dateTime(category.updated_at)}</small>}</div>
   <div className="content-catalog-subjects" aria-label={`${direction.label}选题`}><button type="button" aria-pressed={!subject} className={!subject?'active':''} onClick={()=>onSelectSubject('')}>全部选题</button>{subjects.map(s=><button key={s.query} type="button" aria-pressed={subject===s.query} className={subject===s.query?'active':''} onClick={()=>onSelectSubject(s.query)}>{s.label}</button>)}</div>
   <div className="content-catalog-results-heading"><span>{selected?.label||'已整理作品'}<small>{items.length} 条</small></span>{draft&&onPlan&&<button className="content-catalog-plan" type="button" disabled={!editable||Boolean(busyItemKey)} onClick={()=>onPlan(draft)}>按此选题策划 <Icon name="arrow" size={13}/></button>}</div>
   {loading?<Loading text="正在读取内容灵感…"/>:error?<ErrorNotice message={error} retry={retry}/>:!items.length?<Empty icon="file" title={subject?'这个选题暂未收录作品':state==='unavailable'?'这类内容暂未收录作品':state==='ready'?'这类内容暂未收录作品':'这类内容正在整理'}>{subject?'可以切换其他选题，查看已整理的内容。':state==='collecting'?'作品整理完成后会显示在这里。':'后续整理的作品会显示在这里。'}</Empty>:<div className="content-catalog-grid">{items.map(item=>{
    const key=`${item.platform}:${item.content_id}`,href=catalogSourceUrl(item),busy=busyItemKey===key;
    const subjectLabel=subjects.find(s=>s.query===item.subject||s.label===item.subject)?.label;
    const hasMetrics=Object.values(item.metrics||{}).some(value=>typeof value==='number'&&Number.isFinite(value)&&value>=0);
    const metricTime=dateTime(item.metrics_observed_at),collectionTime=dateTime(item.collected_at);
    return <article className="content-catalog-card" key={key} data-content-key={key}>
     <CatalogVideoCover item={item} onOpen={()=>setPlaying(item)}/>
     <div className="content-catalog-card-meta"><span>{platformLabels[item.platform]||item.platform}</span>{subjectLabel&&<small>{subjectLabel}</small>}</div>
     <h3>{item.title}</h3>{item.author&&<p className="content-catalog-author">{item.author}</p>}
     {item.snippet&&<p className="content-catalog-summary">{item.snippet}</p>}
     <div className="content-catalog-observation"><span>点赞 <strong>{metric(item.metrics?.likes)}</strong></span><div className="content-catalog-observation-times"><small>{!hasMetrics?'互动指标待观察':metricTime==='时间待补充'?'互动采样时间待补充':`互动采样于 ${metricTime}`}</small><small>{collectionTime==='时间待补充'?'作品采集时间待补充':`作品采集于 ${collectionTime}`}</small></div></div>
     <div className="content-catalog-card-actions">{href?<a href={href} target="_blank" rel="noreferrer">查看原作 <Icon name="external" size={13}/></a>:<span className="content-catalog-source-missing">原作链接待补充</span>}{item.reference_id&&!item.catalog_only?<Button icon="studio" disabled={Boolean(busyItemKey)} onClick={()=>onOpenReference(item.reference_id!)}>查看研究</Button>:<Button icon="studio" busy={busy} disabled={!editable||!(item.job_id||item.catalog_only&&safeUrl(item.url))||Boolean(busyItemKey)} onClick={()=>onCapture(item)}>收藏并研究</Button>}{onPlanItem&&<Button primary icon="arrow" disabled={!editable||Boolean(busyItemKey)||!(item.reference_id&&!item.catalog_only||item.job_id||item.catalog_only&&safeUrl(item.url))} onClick={()=>onPlanItem(item)}>用这条策划</Button>}</div>
    </article>;
   })}</div>}
  </div>
  {playing&&<CatalogVideoPlayer item={playing} onClose={()=>setPlaying(null)}/>}
 </section>;
}
