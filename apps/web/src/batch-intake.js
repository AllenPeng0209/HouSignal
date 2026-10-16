/** An upload's committed asset is kept independently from its retryable transfer state. */
export class UploadQueue {
  constructor(){this.items=[];this.running=false;this.sequence=0;}
  snapshot(){return this.items.map(item=>({...item}));}
  addFiles(files){
    for(const file of files){
      const key=[file.name,file.size,file.lastModified,file.type].join('\u0000');
      if(this.items.some(item=>item.key===key))continue;
      this.items.push({id:`upload-${++this.sequence}`,key,file,originType:'',status:'queued',progress:0,error:'',asset:null});
    }
  }
  setOrigin(id,origin){const item=this.items.find(item=>item.id===id);if(item&&['queued','failed'].includes(item.status))item.originType=['actual','ai_generated'].includes(origin)?origin:'';}
  cancel(id){const item=this.items.find(item=>item.id===id);if(!item||item.status!=='queued')return false;item.status='cancelled';return true;}
  retry(id){const item=this.items.find(item=>item.id===id);if(item?.status==='failed'&&!item.asset){item.status='queued';item.error='';item.progress=0;}}
  async run({upload,onChange=()=>{}}){
    if(this.running)return;this.running=true;
    try{
      for(const item of this.items){
        if(item.status!=='queued'||item.asset)continue;
        item.status='uploading';item.error='';onChange(this.snapshot());
        try{
          if(!['actual','ai_generated'].includes(item.originType))throw new Error('请先选择这份文件的来源');
          if(!item.file.size)throw new Error('不能上传空文件');
          if(item.file.size>100*1024*1024)throw new Error('单个素材不能超过 100MB');
          const asset=await upload(item.file,item.originType,progress=>{item.progress=Math.max(0,Math.min(100,Number(progress)||0));onChange(this.snapshot());});
          if(!asset?.id)throw new Error('服务器未确认素材保存，请重试');
          item.asset=asset;item.status='succeeded';item.progress=100;
        }catch(error){item.status='failed';item.error=error instanceof Error?error.message:'上传未完成，请重试';}
        onChange(this.snapshot());
      }
    }finally{this.running=false;onChange(this.snapshot());}
  }
}

export async function applyBatchRights(assets,selectedIds,options,{authorize}){
  if(!options.canReview)throw new Error('素材用途确认需要审核负责人权限');
  if(!options.confirmed)throw new Error('请逐份确认所选文件的来源与用途');
  if(!options.evidence?.trim()||!options.usage?.trim())throw new Error('请填写授权依据与适用用途');
  if(!selectedIds.length)throw new Error('请明确勾选本次适用的素材');
  const ids=[...new Set(selectedIds)],known=new Map(assets.map(asset=>[asset.id,asset]));
  if(ids.some(id=>!known.has(id)))throw new Error('所选素材已变化，请刷新后重新选择');
  const body={rights:'authorized',evidence:`${options.evidence.trim()}\n用途：${options.usage.trim()}`,model_processing_authorized:options.allowAI===true};
  const results=[];
  for(const id of ids){
    const revision=known.get(id).rights_revision;
    if(!Number.isInteger(revision)||revision<1){results.push({id,error:'缺少素材授权版本，请刷新授权状态后重新选择并确认',requiresRefresh:true});continue;}
    try{const asset=await authorize(id,{...body,expected_rights_revision:revision});if(asset?.id!==id||asset.rights!=='authorized')throw new Error('服务器未确认素材用途保存');results.push({id,asset});}
    catch(error){results.push({id,error:error instanceof Error?error.message:'用途确认未完成',requiresRefresh:error?.status===409});}
  }
  return results;
}

function meaningful(value){const text=String(value??'').trim();return !!text&&!['-','--','—','null','none','n/a'].includes(text.toLowerCase())&&!/^(待确认|待上传|待补充|待核验|未知|暂无|未提供|unknown\b|pending\b|tbd\b)/i.test(text);}
function factIssue(fact){
  if(!meaningful(fact.value)||!meaningful(fact.source))return '缺少事实内容或证据来源';
  if(fact.valid_until&&new Date(fact.valid_until).getTime()<=Date.now())return '资料有效期已过';
  const units={area:['㎡','m²','m2','平方米'],width:['m','米'],price:['元','万元','亿元','元/㎡','万元/㎡','元/平方米','万元/平方米'],total_price:['元','万元','亿元'],unit_price:['元/㎡','万元/㎡','元/平方米','万元/平方米']};
  if(units[fact.semantic_key]){
    const number=Number(String(fact.value).replaceAll(',',''));
    if(!Number.isFinite(number)||number<=0||!units[fact.semantic_key].includes(String(fact.unit||'').trim().toLowerCase()))return '数值或单位待补充/修正';
  }
  return '';
}
export function buildFactReviewRows(facts,assets){
  const candidates=assets.filter(asset=>asset.origin_type!=='reference'&&asset.metadata_json?.usage_scope!=='research_only').flatMap(asset=>Array.isArray(asset.metadata_json?.fact_candidates)?asset.metadata_json.fact_candidates.filter(value=>value&&typeof value==='object'&&typeof value.semantic_key==='string'&&typeof value.value==='string'&&typeof value.source==='string'&&(!value.unit||typeof value.unit==='string')&&(!value.source_excerpt||typeof value.source_excerpt==='string')).map((candidate,index)=>({...candidate,assetId:asset.id,assetName:asset.name,key:`${asset.id}-${index}`})):[]);
  return facts.map(fact=>{
    const matches=candidates.filter(candidate=>candidate.semantic_key===fact.semantic_key);
    const values=new Set([...matches.map(candidate=>`${String(candidate.value||'').trim()}\u0000${String(candidate.unit||'').trim()}`),...(meaningful(fact.value)?[`${String(fact.value).trim()}\u0000${String(fact.unit||'').trim()}`]:[])]);
    const issue=factIssue(fact);
    return {fact,candidates:matches,conflict:values.size>1,missing:!!issue,issue,canVerify:!issue&&fact.status!=='verified'};
  });
}

export async function verifySelectedFacts(selected,options,{loadFacts,verify}){
  if(!options.canReview)throw new Error('人工核验需要审核负责人权限');
  if(!options.confirmed)throw new Error('请先确认已逐项核对勾选版本的数值、来源与有效期');
  if(!selected.length)throw new Error('请逐项勾选已核对的记录');
  const current=await loadFacts(),results=[],seen=new Set();
  for(const item of selected){
    if(seen.has(item.id))continue;seen.add(item.id);
    const fact=current.find(fact=>fact.id===item.id);
    if(!fact||fact.revision!==item.revision){results.push({id:item.id,error:'资料版本已变化，请刷新后重新核对'});continue;}
    const issue=factIssue(fact);if(issue){results.push({id:item.id,error:issue});continue;}
    if(fact.status==='verified'){results.push({id:item.id,fact});continue;}
    try{const saved=await verify(item.id,{confirmed:true,expected_revision:item.revision});if(!saved?.id||saved.status!=='verified')throw new Error('服务器未确认核验结果');results.push({id:item.id,fact:saved});}
    catch(error){results.push({id:item.id,error:error instanceof Error?error.message:'核验未完成'});}
  }
  return results;
}
