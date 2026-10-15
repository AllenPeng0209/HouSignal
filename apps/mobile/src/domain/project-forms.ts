import type {Project} from '../api/types';
import type {PickedFile} from '../lib/contracts';
export type ProjectDraft = Pick<Project,'name'|'city'|'property_type'|'owner_name'|'audience'|'sales_owner_name'|'channel'|'marketing_confirmed'|'marketing_evidence'>;
export function projectDraft(project?:Project|null):ProjectDraft{
 return {name:project?.name||'',city:project?.city||'',property_type:project?.property_type||'大平层',owner_name:project?.owner_name||'',audience:project?.audience||'',sales_owner_name:project?.sales_owner_name||'',channel:project?.channel||'抖音',marketing_confirmed:project?.marketing_confirmed||false,marketing_evidence:project?.marketing_evidence||''};
}
export function projectPayload(draft:ProjectDraft,canReview:boolean):Record<string,string|boolean>{
 const {marketing_confirmed,marketing_evidence,...editable}=draft;
 const body:Record<string,string|boolean>=Object.fromEntries(Object.entries(editable).map(([key,value])=>[key,value.trim()]));
 if(!body.name||!body.city||!body.property_type)throw new Error('请填写项目名称、城市与物业类型');
 if(String(body.name).length>200||String(body.city).length>100)throw new Error('项目名称或城市过长');
 if(canReview){if(marketing_confirmed&&!marketing_evidence.trim())throw new Error('请填写项目营销授权依据');body.marketing_confirmed=marketing_confirmed;body.marketing_evidence=marketing_evidence.trim();}
 return body;
}
export function rightsPayload(rights:string,evidence:string,ai:boolean,confirmed:boolean){
 if(rights==='authorized'&&!evidence.trim())throw new Error('请填写素材授权依据');
 if(rights==='authorized'&&!confirmed)throw new Error('请先确认文件来源与用途');
 return {rights,evidence:evidence.trim(),model_processing_authorized:rights==='authorized'&&ai};
}
export function factDateInput(value?:string|null):string{
 if(!value)return '';const date=new Date(value);if(!Number.isFinite(date.getTime()))return '';
 return new Date(date.getTime()+8*3600000).toISOString().slice(0,16).replace('T',' ');
}
export type FactDraft={value:string;unit:string;source:string;valid_until:string};
export function factPayload(draft:FactDraft){
 let valid_until:string|null=null;const value=draft.valid_until.trim();
 if(value){
  if(!/^\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}$/.test(value))throw new Error('有效期请填写 YYYY-MM-DD HH:mm（北京时间）');
  const date=new Date(`${value.replace(' ','T')}:00+08:00`);
  if(!Number.isFinite(date.getTime())||factDateInput(date.toISOString())!==value.replace('T',' '))throw new Error('有效期日期无效');
  valid_until=date.toISOString();
 }
 return {value:draft.value.trim(),unit:draft.unit.trim(),source:draft.source.trim(),valid_until};
}
export function validatePickedFile(file:PickedFile):void{
 if(!file.uri||!file.name)throw new Error('未取得可上传的文件，请重新选择');
 if(file.size===0)throw new Error('不能上传空文件');
 if(file.size!==undefined&&(!Number.isFinite(file.size)||file.size>100*1024*1024))throw new Error('单个素材不能超过 100MB');
}
