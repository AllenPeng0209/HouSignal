"""Native secretary services: all callers share scoped, auditable CRM writes.

Tools flush into their caller's transaction. Router handlers alone commit. Capturing
an internal customer or tentative appointment never asserts qualification or consent.
"""
from datetime import date, datetime, timedelta, timezone
import re
from zoneinfo import ZoneInfo
from fastapi import APIRouter, Body, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session
from .db import get_db
from .security import Context, get_context, require_project, encrypt_pii, pii_fingerprint
from .business_models import Inquiry, Lead, FollowUp
from .secretary_models import CustomerProfile, ViewingAppointment
from .audit_models import AuditEvent
from .business import SALES_ROLES, _scope, _lead, _lead_query, _record, _required_text, _text, _valid_owner, lead_dict, inquiry_dict, iso

router=APIRouter(tags=['secretary'])
LOCAL_ZONE=ZoneInfo('Asia/Shanghai')
PROFILE_FIELDS=('background','financial_situation','budget','preferences','family','requirements','notes','source_note')
PHONE=re.compile(r'(?<!\d)(?:\+?86[ -]?)?1[3-9]\d{9}(?!\d)')
LANDLINE=re.compile(r'(?<!\d)(?:0\d{2,3}[- ]?)\d{7,8}(?!\d)')
INTERNATIONAL_PHONE=re.compile(r'(?<![A-Za-z0-9])(?:\+|00)\d{1,3}[ -]?(?:\d[ -]?){6,13}\d(?!\d)')
EMAIL=re.compile(r'[A-Za-z0-9.!#$%&\x27*+/=?^_`{|}~-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)+')
WECHAT=re.compile(r'(?:微信(?:号)?|wechat)\s*[:：是为]?\s*([A-Za-z][A-Za-z0-9_-]{5,19})(?![A-Za-z0-9_-])',re.I)


def now(): return datetime.now(timezone.utc)


def _safe(value):
    if not isinstance(value,str): return value
    value=INTERNATIONAL_PHONE.sub('［联系方式已隐藏］',value)
    value=PHONE.sub('［联系方式已隐藏］',value)
    value=LANDLINE.sub('［联系方式已隐藏］',value)
    value=EMAIL.sub('［邮箱已隐藏］',value)
    return WECHAT.sub('微信［联系方式已隐藏］',value)


def _string(body,key,default='',limit=4000):
    return _safe(_text(body,key,default,max_length=limit))


def _customer(db,ctx,pid,lid,write=False):
    # Lock the stable parent before loading profile JSON or appointments, so
    # concurrent patches (including the first profile insert) serialize.
    if not write:return _lead(db,ctx,pid,lid,require_active=True)
    require_project(db,ctx,pid,SALES_ROLES)
    lead=db.scalar(_lead_query(db,ctx,pid).where(Lead.id==lid).with_for_update().execution_options(populate_existing=True))
    if lead is None:raise HTTPException(404,'客户不存在或无权访问')
    # PostgreSQL reevaluates the owner-scoped predicate after a lock wait. Reload
    # consent too: an earlier ORM identity must not authorize a later write.
    inquiry=db.scalar(_scope(db,Inquiry,ctx,pid).where(Inquiry.id==lead.inquiry_id).execution_options(populate_existing=True))
    if inquiry is None:raise HTTPException(404,'咨询不存在或无权访问')
    if inquiry.withdrawn or lead.stage=='withdrawn' or (lead.stage!='captured' and (not inquiry.consent or not inquiry.contactable)):
        raise HTTPException(409,'客户联系授权已撤回，不可继续跟进')
    return lead


def _audit(db,ctx,pid,action,rid,fields):
    db.add(AuditEvent(tenant_id=ctx.tenant_id,property_id=pid,actor_id=ctx.user_id,
        action='secretary.'+action,details={'record_id':rid,'fields':sorted(fields)}))


def profile_data(db,ctx,pid,lid):
    _customer(db,ctx,pid,lid)
    profile=db.scalar(_scope(db,CustomerProfile,ctx,pid).where(CustomerProfile.lead_id==lid))
    return {key:(profile.details or {}).get(key,'') if profile else '' for key in PROFILE_FIELDS} | {
        'lead_id':lid,'provenance':profile.provenance or {} if profile else {},
        'updated_at':iso(profile.updated_at) if profile else None}


def _contact(body,raw_text):
    if 'contact_from_message' in body and type(body['contact_from_message']) is not bool:
        raise HTTPException(422,'contact_from_message必须是布尔值')
    contact=_text(body,'contact',max_length=200)
    if body.get('contact_from_message') is True:
        matches=sorted((m for pattern in (INTERNATIONAL_PHONE,PHONE,LANDLINE,EMAIL) for m in pattern.finditer(raw_text)),key=lambda m:(m.start(),-len(m.group(0))))
        chosen=[]
        for match in matches:
            if not any(match.start()<old.end() and old.start()<match.end() for old in chosen):chosen.append(match)
        values=[m.group(0).strip() for m in chosen]
        values += [m.group(1) for m in WECHAT.finditer(raw_text)]
        values=list(dict.fromkeys(values))
        if len(values)!=1: raise HTTPException(422,'请明确本次要保存的一个客户联系方式')
        contact=values[0]
    if contact and body.get('consent_contact') is not True:
        raise HTTPException(422,'保存联系方式前请明确客户已同意后续联系；其余客户信息可先记录')
    return contact


def update_customer_data(db,ctx,pid,lid,body,raw_text=''):
    allowed=set(PROFILE_FIELDS)|{'lead_id','name','need','next_action','owner_id','contact','contact_from_message','consent_contact'}
    if set(body)-allowed:raise HTTPException(422,'客户资料含不支持的字段')
    lead=_customer(db,ctx,pid,lid,True)
    contact=_contact(body,raw_text)
    inquiry=_record(db,Inquiry,ctx,pid,lead.inquiry_id)
    profile=db.scalar(_scope(db,CustomerProfile,ctx,pid).where(CustomerProfile.lead_id==lid).execution_options(populate_existing=True))
    if not profile:
        profile=CustomerProfile(tenant_id=ctx.tenant_id,property_id=pid,lead_id=lid,details={},provenance={})
        db.add(profile)
    details=dict(profile.details or {});provenance=dict(profile.provenance or {})
    changes=[];stamp=iso(now());source=_string(body,'source_note') or '经纪人直接记录'
    for key in PROFILE_FIELDS:
        if key not in body: continue
        details[key]=_string(body,key)
        provenance[key]={'source_note':source,'recorded_by':ctx.user_id,'recorded_at':stamp,'confidence':'self_reported'}
        changes.append(key)
    for key in ('name','need','next_action'):
        if key not in body: continue
        value=_string(body,key,limit=120 if key=='name' else 4000)
        if key=='name' and not value: raise HTTPException(422,'客户称呼不能为空')
        setattr(lead,key,value)
        if key in ('name','need'):setattr(inquiry,key,value)
        changes.append(key)
    if contact:
        inquiry.contact_cipher=encrypt_pii(contact)
        inquiry.contact_fingerprint=pii_fingerprint(contact,ctx.tenant_id)
        inquiry.consent=True;inquiry.contactable=True
        changes.append('encrypted_contact')
    profile.details=details;profile.provenance=provenance;profile.updated_at=now();lead.updated_at=now()
    _audit(db,ctx,pid,'update_customer',lid,changes)
    db.flush()
    return {'lead':lead_dict(lead),'profile':profile_data(db,ctx,pid,lid),'inquiry':inquiry_dict(inquiry),'changed_fields':changes}


def create_customer_data(db,ctx,pid,body,raw_text=''):
    require_project(db,ctx,pid,SALES_ROLES)
    name=_string(body,'name',limit=120)
    if not name:raise HTTPException(422,'请提供客户称呼')
    # Validate contact before adding anything. The provider never receives it.
    _contact(body,raw_text)
    owner=body.get('owner_id') or ctx.user_id
    if ctx.role=='sales' and owner!=ctx.user_id: raise HTTPException(403,'销售只能记录自己的客户')
    _valid_owner(db,ctx,pid,owner)
    inquiry=Inquiry(tenant_id=ctx.tenant_id,property_id=pid,name=name,need=_string(body,'need'),channel='楼管家',source_confidence='self_reported',source_text=_string(body,'source_note') or '经纪人直接记录',owner_id=owner,consent=False,contactable=False,qualified=False,withdrawn=False,qualification_checks={})
    db.add(inquiry);db.flush()
    lead=Lead(tenant_id=ctx.tenant_id,property_id=pid,inquiry_id=inquiry.id,name=name,need=inquiry.need,stage='captured',owner_id=owner,next_action=_string(body,'next_action'),source_confidence='self_reported',stage_history=[{'stage':'captured','by':ctx.user_id,'at':iso(now())}])
    db.add(lead);db.flush()
    result=update_customer_data(db,ctx,pid,lead.id,body,raw_text)
    _audit(db,ctx,pid,'create_customer',lead.id,['name','need'])
    db.flush()
    return result


def search_customers_data(db,ctx,pid,body):
    require_project(db,ctx,pid,SALES_ROLES)
    query=_string(body,'query',limit=120)
    # Existing listing enforces tenant/project/owner and excludes withdrawn data.
    from .business import list_leads_data
    matches=[x for x in list_leads_data(db,ctx,pid) if not query or query.casefold() in x['name'].casefold()]
    matches.sort(key=lambda x:0 if x['name'].casefold()==query.casefold() else 1)
    return [x|{'profile':profile_data(db,ctx,pid,x['id'])} for x in matches[:30]]


def record_followup_data(db,ctx,pid,body):
    lead=_customer(db,ctx,pid,_required_text(body,'lead_id',36),True)
    note=_string(body,'note')
    if not note:raise HTTPException(422,'请填写跟进内容')
    action=_string(body,'next_action',lead.next_action)
    followup=FollowUp(tenant_id=ctx.tenant_id,property_id=pid,lead_id=lead.id,note=note,next_action=action,created_by=ctx.user_id)
    db.add(followup);lead.next_action=action;lead.updated_at=now()
    _audit(db,ctx,pid,'record_followup',lead.id,['note']+(['next_action'] if 'next_action' in body else []))
    db.flush()
    return {'id':followup.id,'lead_id':lead.id,'note':followup.note,'next_action':action,'created_at':iso(followup.created_at)}


def _resolve_date(text):
    text=text.strip()
    local_day=now().astimezone(LOCAL_ZONE).date()
    if not text:return ''
    iso_match=re.search(r'(?<!\d)(\d{4}-\d{2}-\d{2})(?!\d)',text)
    if iso_match:
        try:return date.fromisoformat(iso_match.group(1)).isoformat()
        except ValueError:raise HTTPException(422,'预约日期无效') from None
    match=re.search(r'(下下|下|本|这)?(?:周|星期|礼拜)([一二三四五六日天])',text)
    if match:
        weekday='一二三四五六日'.index('日' if match.group(2)=='天' else match.group(2))
        offset={'下下':14,'下':7,'本':0,'这':0,None:0}[match.group(1)]
        delta=weekday-local_day.weekday()+offset
        if match.group(1) is None and delta<0:delta+=7
        return (local_day+timedelta(days=delta)).isoformat()
    for keyword,days in [('大后天',3),('后天',2),('明天',1),('今天',0)]:
        if keyword in text:return (local_day+timedelta(days=days)).isoformat()
    raise HTTPException(422,'请补充明确的预约日期，例如2026-10-21或下周三')


def _date_value(value):
    if not isinstance(value,str):raise HTTPException(422,'预约日期格式无效')
    if not value:return ''
    try:return date.fromisoformat(value).isoformat()
    except ValueError:raise HTTPException(422,'预约日期格式无效') from None


def _datetime(value,key):
    if value is None or value=='':return None
    if not isinstance(value,str):raise HTTPException(422,f'{key}须为带时区时间')
    try:
        parsed=datetime.fromisoformat(value.replace('Z','+00:00'))
        if parsed.tzinfo is None:raise ValueError()
        return parsed.astimezone(LOCAL_ZONE)
    except ValueError:raise HTTPException(422,f'{key}须为带时区时间') from None


def _local_iso(value):
    if value is None:return None
    # SQLite strips tzinfo, while PostgreSQL preserves the instant.
    return (value.replace(tzinfo=LOCAL_ZONE) if value.tzinfo is None else value.astimezone(LOCAL_ZONE)).isoformat()


def viewing_dict(viewing,lead):
    missing=[]
    if not viewing.date:missing.append('date')
    if not viewing.starts_at:missing.append('time')
    if not viewing.location:missing.append('location')
    labels={'date':'日期','time':'具体时间','location':'见面地点'}
    return {key:getattr(viewing,key) for key in ('id','lead_id','date','time_window','timezone','location','notes','status','source_note')} | {
        'lead_name':lead.name,'starts_at':_local_iso(viewing.starts_at),'ends_at':_local_iso(viewing.ends_at),
        'missing_details':missing,'clarification':'请补充'+ '、'.join(labels[x] for x in missing) if missing else '',
        'created_at':iso(viewing.created_at),'updated_at':iso(viewing.updated_at),'delivery':'local_record_only'}


def _viewing_values(body,viewing=None):
    existing=lambda key,default:getattr(viewing,key,default) if viewing else default
    day=_date_value(body['date']) if 'date' in body else existing('date','')
    date_text=_text(body,'date_text',max_length=120)
    if date_text:day=_resolve_date(date_text)
    window=_string(body,'time_window',existing('time_window',''),80)
    window={'morning':'上午','afternoon':'下午','evening':'晚上','night':'晚上',
            'noon':'中午','dawn':'凌晨','early morning':'早上'}.get(window.casefold(),window)
    if date_text and 'time_window' not in body:
        window=next((x for x in ('凌晨','早上','上午','中午','下午','傍晚','晚上','晚间') if x in date_text),'')
    start=_datetime(body['starts_at'],'starts_at') if 'starts_at' in body else existing('starts_at',None)
    end=_datetime(body['ends_at'],'ends_at') if 'ends_at' in body else existing('ends_at',None)
    # Moving a dated window must not retain an obsolete exact timestamp.
    if viewing and (day!=viewing.date or window!=viewing.time_window) and 'starts_at' not in body:start=None;end=None
    if start:
        start=start.replace(tzinfo=LOCAL_ZONE) if start.tzinfo is None else start.astimezone(LOCAL_ZONE)
        if day and day!=start.date().isoformat():raise HTTPException(422,'具体时间与预约日期不一致')
        day=start.date().isoformat()
    if end:
        end=end.replace(tzinfo=LOCAL_ZONE) if end.tzinfo is None else end.astimezone(LOCAL_ZONE)
        if not start or end<=start:raise HTTPException(422,'结束时间须晚于开始时间')
    location=_string(body,'location',existing('location',''),2000)
    status=_text(body,'status',existing('status','pending_details'),30)
    if status not in ('pending_details','scheduled','completed','cancelled'):raise HTTPException(422,'预约状态不支持')
    if status not in ('completed','cancelled'):status='scheduled' if day and start and location else 'pending_details'
    return {'date':day,'time_window':window,'starts_at':start,'ends_at':end,'timezone':'Asia/Shanghai','location':location,
            'notes':_string(body,'notes',existing('notes','')),'source_note':_string(body,'source_note',existing('source_note','')),'status':status}


def schedule_viewing_data(db,ctx,pid,body):
    lead=_customer(db,ctx,pid,_required_text(body,'lead_id',36),True)
    values=_viewing_values(body)
    viewing=ViewingAppointment(tenant_id=ctx.tenant_id,property_id=pid,lead_id=lead.id,created_by=ctx.user_id,**values)
    db.add(viewing)
    _audit(db,ctx,pid,'schedule_viewing',lead.id,list(values))
    db.flush()
    return viewing_dict(viewing,lead)


def update_viewing_data(db,ctx,pid,vid,body):
    require_project(db,ctx,pid,SALES_ROLES)
    viewing=_record(db,ViewingAppointment,ctx,pid,vid)
    lead=_customer(db,ctx,pid,viewing.lead_id,True)
    viewing=db.scalar(_scope(db,ViewingAppointment,ctx,pid).where(ViewingAppointment.id==vid,ViewingAppointment.lead_id==lead.id).with_for_update().execution_options(populate_existing=True))
    if viewing is None:raise HTTPException(404,'预约不存在或无权访问')
    values=_viewing_values(body,viewing)
    for key,value in values.items():setattr(viewing,key,value)
    viewing.updated_at=now()
    _audit(db,ctx,pid,'update_viewing',vid,[key for key in body if key not in ('viewing_id','lead_id')])
    db.flush()
    return viewing_dict(viewing,lead)


def list_viewings_data(db,ctx,pid,lid=None):
    require_project(db,ctx,pid)
    if lid:_customer(db,ctx,pid,lid)
    from .business import list_leads_data
    leads={x['id']:x for x in list_leads_data(db,ctx,pid)}
    query=_scope(db,ViewingAppointment,ctx,pid)
    if lid:query=query.where(ViewingAppointment.lead_id==lid)
    items=db.scalars(query.order_by(ViewingAppointment.date,ViewingAppointment.created_at)).all()
    return [viewing_dict(v,db.get(Lead,v.lead_id)) for v in items if v.lead_id in leads]


def _tool(name,description,properties,required=()):
    return {'type':'function','function':{'name':name,'description':description,'parameters':{'type':'object','properties':properties,'required':list(required),'additionalProperties':False}}}


STR={'type':'string'}
PROFILE_SCHEMA={key:dict(STR) for key in PROFILE_FIELDS}
CONTACT_SCHEMA={'contact_from_message':{'type':'boolean','description':'仅在用户本条消息明确要求保存其中的联系方式时为true。原文由服务器提取，不要把手机号填入参数。'},'consent_contact':{'type':'boolean','description':'只有用户明确说明客户同意后续联系才可为true；不能从提供电话推定授权。'}}
VIEWING_SCHEMA={key:dict(STR) for key in ('date','date_text','time_window','location','notes','source_note')}
VIEWING_SCHEMA.update({key:{'type':['string','null']} for key in ('starts_at','ends_at')})
VIEWING_SCHEMA['status']={'type':'string','enum':['pending_details','scheduled','completed','cancelled']}
SECRETARY_TOOLS=[
    _tool('search_customers','在当前项目及负责人的权限内按姓名查找CRM客户。可能同名，修改前核对ID，禁止自动合并。',{'query':STR}),
    _tool('create_customer','按用户要求建立待完善CRM客户；只须称呼，不会推定有联系授权、财力或合格资格。',{'name':STR,'need':STR,'next_action':STR,**PROFILE_SCHEMA,**CONTACT_SCHEMA},('name',)),
    _tool('update_customer','按用户明确提供的事实局部更新客户背景、财力、预算、喜好等；保留未提及字段。联系方式从本条用户原文加密保存。',{'lead_id':STR,'name':STR,'need':STR,'next_action':STR,**PROFILE_SCHEMA,**CONTACT_SCHEMA},('lead_id',)),
    _tool('record_followup','保存客户内部跟进记录和可选下一步动作；不会发送信息。',{'lead_id':STR,'note':STR,'next_action':STR},('lead_id','note')),
    _tool('schedule_viewing','保存看楼或会面预约。下周三下午应使用date_text保留下午时间段，具体时间或地点缺失时记为待补充，不得编造。只记录本地日程，不发送提醒。',{'lead_id':STR,**VIEWING_SCHEMA},('lead_id',)),
    _tool('update_viewing','修改本项目看楼预约的时间、地点或状态；未提供的字段保留，改日期会清除过时的具体时间。',{'viewing_id':STR,**VIEWING_SCHEMA},('viewing_id',)),
    _tool('list_viewings','查看本项目或某位客户的本地预约及仍需补充的时间地点。',{'lead_id':STR}),
]
SECRETARY_NAMES={item['function']['name'] for item in SECRETARY_TOOLS}
MUTATION_NAMES=SECRETARY_NAMES-{'search_customers','list_viewings'}


def execute_secretary_tool(db,ctx,pid,name,args,*,raw_text=''):
    if not isinstance(args,dict):raise HTTPException(422,'工具参数必须是对象')
    # A model-generated boolean cannot supply the customer's consent. Direct UI
    # callers explicitly submit consent; agent tools must also cite this message.
    if name in ('create_customer','update_customer') and (args.get('contact_from_message') is True or args.get('contact')):
        positive=re.search(r'(?:已(?:经)?(?:明确)?同意.{0,12}(?:联系|回访|沟通)|(?:客户|对方|本人).{0,6}(?:同意|授权).{0,12}(?:联系|回访|沟通)|已(?:获得|取得|获|有).{0,6}(?:联系授权|联系同意|授权联系))',raw_text)
        negative=re.search(r'(?:未|没有|没|尚未|未曾|不|拒绝|撤回|取消).{0,8}(?:同意|授权|联系)',raw_text)
        if not positive or negative:raise HTTPException(422,'保存联系方式前请明确客户已同意后续联系；其余客户信息可先记录')
    if name=='search_customers':data=search_customers_data(db,ctx,pid,args)
    elif name=='create_customer':data=create_customer_data(db,ctx,pid,args,raw_text)
    elif name=='update_customer':data=update_customer_data(db,ctx,pid,_required_text(args,'lead_id',36),args,raw_text)
    elif name=='record_followup':data=record_followup_data(db,ctx,pid,args)
    elif name=='schedule_viewing':data=schedule_viewing_data(db,ctx,pid,args)
    elif name=='update_viewing':data=update_viewing_data(db,ctx,pid,_required_text(args,'viewing_id',36),args)
    elif name=='list_viewings':data=list_viewings_data(db,ctx,pid,_text(args,'lead_id',max_length=36) or None)
    else:raise HTTPException(422,'秘书工具不存在')
    rows=data if isinstance(data,list) else [data]
    ids=[]
    for row in rows:
        lid=(row.get('lead') or {}).get('id') or row.get('lead_id')
        if name=='search_customers':lid=row.get('id')
        if lid and lid not in ids:ids.append(lid)
    sources=[]
    for lid in ids:
        lead=_customer(db,ctx,pid,lid)
        sources.append({'kind':'lead','id':lead.id,'label':_safe(lead.name),'observed_at':iso(now())})
    if not sources:sources=[{'kind':'page','id':pid,'page':'leads','label':'咨询与客户','observed_at':iso(now())}]
    return {'data':data,'sources':sources,'observed_at':iso(now())}


@router.get('/api/projects/{pid}/leads/{lid}/profile')
def profile_get(pid:str,lid:str,db:Session=Depends(get_db),ctx:Context=Depends(get_context)):
    return profile_data(db,ctx,pid,lid)


@router.patch('/api/projects/{pid}/leads/{lid}/profile')
def profile_patch(pid:str,lid:str,body:dict=Body(...),db:Session=Depends(get_db),ctx:Context=Depends(get_context)):
    result=update_customer_data(db,ctx,pid,lid,body)
    db.commit();return result['profile']


@router.get('/api/projects/{pid}/viewings')
def viewings_list(pid:str,lead_id:str|None=None,db:Session=Depends(get_db),ctx:Context=Depends(get_context)):
    return list_viewings_data(db,ctx,pid,lead_id)


@router.get('/api/projects/{pid}/leads/{lid}/viewings')
def lead_viewings_list(pid:str,lid:str,db:Session=Depends(get_db),ctx:Context=Depends(get_context)):
    return list_viewings_data(db,ctx,pid,lid)


@router.post('/api/projects/{pid}/viewings')
def viewing_create(pid:str,body:dict=Body(...),db:Session=Depends(get_db),ctx:Context=Depends(get_context)):
    result=schedule_viewing_data(db,ctx,pid,body)
    db.commit();return result


@router.patch('/api/projects/{pid}/viewings/{vid}')
def viewing_patch(pid:str,vid:str,body:dict=Body(...),db:Session=Depends(get_db),ctx:Context=Depends(get_context)):
    result=update_viewing_data(db,ctx,pid,vid,body)
    db.commit();return result
