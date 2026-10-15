"""Shared content and CRM services used by both the workbench and 楼管家."""
from datetime import datetime, timezone
import hashlib
import json
import math
import os
import re
from urllib.parse import urlparse
from uuid import uuid4
from fastapi import APIRouter, Body, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from .db import get_db
from .security import Context, get_context, require_project, context_for_user, encrypt_pii, decrypt_pii, pii_fingerprint
from .core_models import Asset, Fact, Membership, Property, User
from .business_models import ContentVersion, Release, Publication, Inquiry, Lead, FollowUp, Proposal
from .operations_models import ContentAccount, ContentPlan
from .core import valid_fact, project_dict

router=APIRouter(tags=['content-and-sales'])
EDIT_ROLES=('owner','admin','content')
REVIEW_ROLES=('owner','admin','reviewer')
SALES_ROLES=('owner','admin','sales')
STAGES=('qualified','appointment','visit_completed','opportunity','won')
PLATFORM_STATES=('pending','live','removed','rejected')


def now(): return datetime.now(timezone.utc)
def iso(value): return value.isoformat() if value is not None else None

def _text(body,key,default='',max_length=12000):
    value=body.get(key,default)
    if not isinstance(value,str) or len(value)>max_length:
        raise HTTPException(422,f'{key}格式不正确')
    return value.strip()


def _required_text(body,key,max_length=12000):
    value=_text(body,key,max_length=max_length)
    if not value: raise HTTPException(422,f'请填写{key}')
    return value


def _confirmed(body):
    if body.get('confirmed') is not True: raise HTTPException(422,'请由负责人明确确认')


def _scope(db,model,ctx,pid):
    return select(model).where(model.tenant_id==ctx.tenant_id,model.property_id==pid)


def _record(db,model,ctx,pid,rid):
    result=db.scalar(_scope(db,model,ctx,pid).where(model.id==rid))
    if result is None: raise HTTPException(404,'记录不存在或无权访问')
    return result


def _content(db,ctx,pid,cid,roles=None):
    require_project(db,ctx,pid,roles)
    return _record(db,ContentVersion,ctx,pid,cid)


def _references(refs):
    if not isinstance(refs,list): raise HTTPException(422,'事实引用格式不正确')
    out=[]
    for ref in refs:
        if isinstance(ref,str): out.append({'id':ref,'revision':None})
        elif isinstance(ref,dict) and isinstance(ref.get('id',ref.get('fact_id')),str):
            out.append({'id':ref.get('id',ref.get('fact_id')),'revision':ref.get('revision')})
        else: raise HTTPException(422,'事实引用必须包含记录编号和版本')
    return out


def _shots(storyboard):
    shots=storyboard.get('shots',[]) if isinstance(storyboard,dict) else storyboard
    if not isinstance(shots,list): raise HTTPException(422,'分镜格式不正确')
    return shots


def content_dict(c,db=None,ctx=None):
    result={key:getattr(c,key) for key in ('id','property_id','series_id','version','topic','script','fact_refs','storyboard','channel','cover_title','aspect_ratio','subtitle_style','render_asset_id','status','created_by')} | {'created_at':iso(c.created_at),'approval_valid':False,'release_id':None,'approved_release_id':None}
    if db is not None and ctx is not None and c.render_asset_id:
        try:
            release=_valid_release(db,ctx,c.property_id,c)
            result.update(approval_valid=True,release_id=release.id,approved_release_id=release.id)
        except HTTPException:
            if result['status']=='approved': result['status']='needs_review'
    return result


def release_dict(r):
    return {key:getattr(r,key) for key in ('id','property_id','content_version_id','status','snapshot_sha256','reviewer_id','review_note')} | {'created_at':iso(r.created_at)}


def normalize_metrics(metrics):
    if metrics is None: return {'D7':None,'D30':None}
    if not isinstance(metrics,dict): raise HTTPException(422,'指标必须按D7、D30窗口填写')
    extra=set(metrics)-{'D7','D30'}
    if extra: raise HTTPException(422,'指标仅支持D7、D30窗口')
    result={'D7':None,'D30':None}
    allowed={'views','likes','comments','saves','inquiries','qualified'}
    for window in result:
        data=metrics.get(window)
        if data is None: continue
        if not isinstance(data,dict) or set(data)-allowed: raise HTTPException(422,'指标窗口字段不正确')
        clean={}
        for key,value in data.items():
            if value is not None and (type(value) is not int or value<0): raise HTTPException(422,'指标必须是非负整数，未观察请留空')
            clean[key]=value
        result[window]=clean or None
    return result


def publication_dict(p):
    return {key:getattr(p,key) for key in ('id','property_id','release_id','title','channel','url','platform_state','receipt_status')} | {'metrics':normalize_metrics(p.metrics),'created_at':iso(p.created_at)}


def _fingerprint(db,ctx,pid,contact):
    return pii_fingerprint(contact,ctx.tenant_id) if contact else None


def normalize_source_confidence(value):
    value={'verified':'confirmed','unverified':'inferred'}.get(value,value)
    if value not in ('confirmed','self_reported','inferred','unknown'): raise HTTPException(422,'来源可信度不支持')
    return value


def inquiry_dict(i):
    # Phone is deliberately not decrypted for ordinary listings or agent tools.
    return {key:getattr(i,key) for key in ('id','property_id','name','need','channel','source_confidence','source_text','contactable','consent','qualified','owner_id','qualification_checks','withdrawn','linked_lead_id','request_key')} | {'contact':'已加密保存' if i.contact_cipher else '未提供','source_confidence':normalize_source_confidence(i.source_confidence),'created_at':iso(i.created_at)}


def lead_dict(l):
    return {key:getattr(l,key) for key in ('id','property_id','inquiry_id','name','need','stage','owner_id','next_action','source_confidence','stage_history')} | {'created_at':iso(l.created_at),'updated_at':iso(l.updated_at),'source_confidence':normalize_source_confidence(l.source_confidence)}


def proposal_dict(p):
    return {key:getattr(p,key) for key in ('id','property_id','title','reason','status','source_refs')} | {'created_at':iso(p.created_at)}


def topics_data(db,ctx,pid):
    require_project(db,ctx,pid)
    facts=db.scalars(_scope(db,Fact,ctx,pid)).all()
    by_key={f.semantic_key:f for f in facts}
    definitions=[
        ('space','从面积走进真实生活','结合面积与空间尺度，回答居住体验问题',('area','size','floor_area')),
        ('light','客厅的尺度与采光','用真实开间与采光资料讲清空间关系',('width','living_width','frontage')),
        ('layout','户型如何容纳一家人的日常','围绕房间布局与动线组织内容',('layout','rooms','floor_plan')),
        ('kitchen','厨房与客餐厅的生活连接','从厨房关系出发拆解真实生活场景',('kitchen','kitchen_relation','island')),
        ('tour','一条有证据的项目导览','串联已核验项目事实与授权实拍素材',())]
    result=[]
    for tid,title,angle,keys in definitions:
        chosen=[by_key[k] for k in keys if k in by_key]
        if not keys: chosen=[f for f in facts if f.required]
        missing=[f.label for f in chosen if not valid_fact(f)]
        if not chosen: missing=['对应项目事实']
        result.append({'id':tid,'title':title,'angle':angle,'fact_refs':[{'id':f.id,'revision':f.revision} for f in chosen if valid_fact(f)],'eligible':bool(chosen) and not missing,'missing':missing})
    return result


def content_snapshot(db,ctx_or_content,content=None):
    """Canonical render input snapshot; excludes render pointer and mutable status."""
    ctx=ctx_or_content if content is not None else None
    c=content if content is not None else ctx_or_content
    p=db.scalar(select(Property).where(Property.id==c.property_id,Property.tenant_id==c.tenant_id))
    if p is None: raise HTTPException(404,'项目不存在')
    if ctx: require_project(db,ctx,c.property_id)
    facts=[]
    for ref in _references(c.fact_refs):
        f=db.scalar(select(Fact).where(Fact.id==ref['id'],Fact.tenant_id==c.tenant_id,Fact.property_id==c.property_id))
        if f is None: raise HTTPException(409,'引用事实已不存在或不属于当前项目')
        facts.append({'id':f.id,'expected_revision':ref['revision'],'revision':f.revision,'label':f.label,'value':f.value,'unit':f.unit,'source':f.source,'status':f.status,'valid_until':iso(f.valid_until)})
    assets=[]
    ids=sorted({s.get('asset_id') for s in _shots(c.storyboard) if isinstance(s,dict) and s.get('asset_id')})
    for aid in ids:
        a=db.scalar(select(Asset).where(Asset.id==aid,Asset.tenant_id==c.tenant_id,Asset.property_id==c.property_id))
        if a is None: raise HTTPException(409,'引用素材已不存在或不属于当前项目')
        assets.append({'id':a.id,'sha256':a.sha256,'rights':a.rights,'rights_evidence':a.rights_evidence,'rights_revision':a.rights_revision,'origin_type':a.origin_type,'kind':a.kind,'mime_type':a.mime_type})
    return {'content_version_id':c.id,'property_id':c.property_id,'tenant_id':c.tenant_id,'version':c.version,'project_name':p.name,'marketing_confirmed':p.marketing_confirmed,'marketing_evidence':p.marketing_evidence,'is_test':p.is_test,'topic':c.topic,'script':c.script,'fact_refs':facts,'storyboard':c.storyboard,'assets':assets,'channel':c.channel,'cover_title':c.cover_title,'aspect_ratio':c.aspect_ratio,'subtitle_style':c.subtitle_style}


def snapshot_sha256(snapshot):
    return hashlib.sha256(json.dumps(snapshot,sort_keys=True,ensure_ascii=False,separators=(',',':')).encode()).hexdigest()


def content_snapshot_hash(db,version): return snapshot_sha256(content_snapshot(db,version))


def _check_quantities(script,facts):
    """Numeric claims must match both the value and the fact's unit."""
    groups={'㎡':'area','平方米':'area','平米':'area','m²':'area',
            '米':'length','m':'length','万元':'money_10k','万':'money_10k',
            '亿元':'money_100m','亿':'money_100m','元':'money','房':'rooms','室':'rooms','卫':'bathrooms'}
    pattern=r'(\d+(?:\.\d+)?)\s*(平方米|万元|亿元|平米|m²|㎡|米|元|万|亿|房|室|卫|m)'
    supported=set()
    for f in facts:
        for match in re.finditer(pattern,f.value+' '+f.unit):
            supported.add((float(match.group(1)),groups[match.group(2)]))
    for match in re.finditer(pattern,script):
        if (float(match.group(1)),groups[match.group(2)]) not in supported:
            raise HTTPException(409,'文案存在未获事实支持的数字或单位，请补充事实或修订文案')


def _check_visible_quantities(script,cover_title,storyboard,facts):
    """Apply the same evidence rule to every string drawn into the output."""
    texts=[script,cover_title or '']
    for shot in _shots(storyboard):
        if not isinstance(shot,dict): raise HTTPException(422,'分镜格式不正确')
        texts.append(shot.get('caption') or '')
    for text in texts:
        if not isinstance(text,str): raise HTTPException(422,'可见文案必须是文本')
        _check_quantities(text,facts)


def validate_content_ready(db,ctx,c,require_render=False):
    p=require_project(db,ctx,c.property_id)
    if not p.marketing_confirmed or not p.marketing_evidence.strip(): raise HTTPException(409,'请先确认项目营销授权及来源')
    required=db.scalars(_scope(db,Fact,ctx,p.id).where(Fact.required.is_(True))).all()
    if not required or any(not valid_fact(f) for f in required): raise HTTPException(409,'项目核心事实尚未全部核验，或已有事实过期')
    facts=[]
    for ref in _references(c.fact_refs):
        f=_record(db,Fact,ctx,p.id,ref['id'])
        if not valid_fact(f) or ref['revision']!=f.revision: raise HTTPException(409,'内容引用事实已改变或失效，请生成新版本')
        facts.append(f)
    if not facts: raise HTTPException(409,'内容缺少经过核验的事实引用')
    _check_visible_quantities(c.script,c.cover_title,c.storyboard,facts)
    shots=_shots(c.storyboard)
    if not shots: raise HTTPException(409,'请先设置实际素材分镜')
    if isinstance(c.storyboard,dict) and c.storyboard.get('schema_version')==2:
        try:
            validate_generated_content(db,ctx,c.property_id,{'topic':c.topic,'script':c.script,'cover_title':c.cover_title,
                'storyboard':c.storyboard,'fact_refs':c.fact_refs})
        except ValueError as error:
            raise HTTPException(409,str(error)) from None
        if any(shot.get('production_status')!='ready' or not shot.get('asset_id') for shot in shots):
            raise HTTPException(409,'长视频仍有镜头素材未就绪，请补齐授权素材后制作成片')
    selected_assets=[]
    for shot in shots:
        if not isinstance(shot,dict) or not shot.get('asset_id'): raise HTTPException(409,'分镜缺少素材')
        a=_record(db,Asset,ctx,p.id,shot['asset_id'])
        if a.rights!='authorized' or not a.rights_evidence.strip(): raise HTTPException(409,'素材授权尚未完成或已撤销')
        if a.kind not in ('image','video') or (a.metadata_json or {}).get('usage_scope') == 'research_only' or (a.origin_type != 'actual' and not (p.is_test and a.origin_type in ('test','template'))): raise HTTPException(409,'请使用当前项目授权实拍素材，参考研究视频不能用于营销成片')
        selected_assets.append(a)
    try:
        _check_generated_material_copy({'topic':c.topic,'script':c.script,'cover_title':c.cover_title,'storyboard':shots},selected_assets)
    except ValueError as error:
        raise HTTPException(409,str(error)) from None
    snapshot=content_snapshot(db,ctx,c)
    if require_render: validate_render_snapshot(db,c,p)
    return snapshot


def validate_render_snapshot(db,content,project):
    a=db.scalar(select(Asset).where(Asset.id==content.render_asset_id,Asset.tenant_id==content.tenant_id,Asset.property_id==content.property_id)) if content.render_asset_id else None
    metadata=a.metadata_json or {} if a else {}
    if (not a or a.mime_type!='video/mp4' or a.kind!='video' or a.byte_size<=0 or a.rights!='authorized' or metadata.get('renderer')!='ffmpeg' or metadata.get('content_version_id')!=content.id or metadata.get('content_snapshot_sha256')!=content_snapshot_hash(db,content)):
        raise HTTPException(409,'尚无对应当前内容与资料版本的实际成片，请重新渲染')
    from .media import _verified_path
    try:
        _verified_path(a)
    except (OSError,ValueError):
        raise HTTPException(409,'成片文件缺失或完整性校验失败，请重新渲染') from None
    if isinstance(content.storyboard,dict) and content.storyboard.get('schema_version')==2:
        from .longform_generation import narration_text
        audio=db.scalar(select(Asset).where(Asset.id==metadata.get('voiceover_audio_asset_id'),
            Asset.tenant_id==content.tenant_id,Asset.property_id==content.property_id))
        expected=hashlib.sha256(narration_text(_shots(content.storyboard)).encode()).hexdigest()
        if (audio is None or audio.kind!='audio' or audio.rights!='authorized' or not audio.rights_evidence.strip()
                or audio.sha256!=metadata.get('voiceover_audio_sha256')
                or audio.rights_revision!=metadata.get('voiceover_audio_rights_revision')
                or metadata.get('narration_text_sha256')!=expected):
            raise HTTPException(409,'长视频配音音频或授权已改变，请重新制作配音与成片')
        try:_verified_path(audio)
        except (OSError,ValueError):
            raise HTTPException(409,'长视频旁白音频完整性校验失败，请重新制作') from None
    return a


def _release_snapshot(db,ctx,c):
    snapshot=validate_content_ready(db,ctx,c,require_render=True)
    render=_record(db,Asset,ctx,c.property_id,c.render_asset_id)
    return {'input':snapshot,'render':{'id':render.id,'sha256':render.sha256,'rights':render.rights,'rights_revision':render.rights_revision,'rights_evidence':render.rights_evidence,'metadata':render.metadata_json}}


def approve_content(db,ctx,pid,cid,body):
    c=_content(db,ctx,pid,cid,REVIEW_ROLES); _confirmed(body)
    # Hold the content row until commit so a worker cannot swap the previewed render.
    db.refresh(c,with_for_update=True)
    if 'expected_render_asset_id' in body:
        expected=_required_text(body,'expected_render_asset_id',max_length=100)
        if expected!=c.render_asset_id:
            raise HTTPException(409,'成片已更新，请重新预览并确认当前成片')
    snapshot=_release_snapshot(db,ctx,c); digest=snapshot_sha256(snapshot)
    existing=db.scalar(_scope(db,Release,ctx,pid).where(Release.content_version_id==cid,Release.status=='approved',Release.snapshot_sha256==digest))
    if existing: return release_dict(existing)
    r=Release(tenant_id=ctx.tenant_id,property_id=pid,content_version_id=cid,status='approved',snapshot=snapshot,snapshot_sha256=digest,reviewer_id=ctx.user_id,review_note=_text(body,'note',max_length=4000))
    db.add(r); c.status='approved'; db.flush()
    return release_dict(r)


def _valid_release(db,ctx,pid,c):
    snapshot=_release_snapshot(db,ctx,c)
    r=db.scalar(_scope(db,Release,ctx,pid).where(Release.content_version_id==c.id,Release.status=='approved',Release.snapshot_sha256==snapshot_sha256(snapshot)).order_by(Release.created_at.desc()))
    if r is None: raise HTTPException(409,'当前版本没有有效审批，资料改变后需重新渲染与审批')
    return r


def export_content(db,ctx,pid,cid):
    c=_content(db,ctx,pid,cid); r=_valid_release(db,ctx,pid,c)
    return {'schema_version':1,'project_id':pid,'content':content_dict(c,db,ctx),'release':release_dict(r),'snapshot':r.snapshot,'render_url':f'/api/projects/{pid}/assets/{c.render_asset_id}/file','exported_at':iso(now())}


def revise_content(db,ctx,pid,cid,body,*,roles=EDIT_ROLES):
    c=_content(db,ctx,pid,cid,roles)
    script=_required_text(body,'script')
    ratio=_text(body,'aspect_ratio',c.aspect_ratio,20)
    if ratio not in ('9:16','16:9','1:1','4:5'): raise HTTPException(422,'画面比例不支持')
    storyboard=body.get('storyboard',c.storyboard)
    _shots(storyboard)
    refs=body.get('fact_refs',c.fact_refs); _references(refs)
    if (isinstance(c.storyboard,dict) and c.storyboard.get('schema_version')==2) or (isinstance(storyboard,dict) and storyboard.get('schema_version')==2):
        if not isinstance(storyboard,dict) or storyboard.get('schema_version')!=2:
            raise HTTPException(422,'长视频编辑须保留章节与分镜结构')
        try:
            validate_generated_content(db,ctx,pid,{'topic':c.topic,'script':script,'cover_title':body.get('cover_title',c.cover_title),
                'storyboard':storyboard,'fact_refs':refs})
        except ValueError as error:
            raise HTTPException(422,str(error)) from None
    # Lock the series rows: the unique DB constraint is a final concurrent safety net.
    versions=db.scalars(_scope(db,ContentVersion,ctx,pid).where(ContentVersion.series_id==c.series_id).with_for_update()).all()
    new=ContentVersion(tenant_id=ctx.tenant_id,property_id=pid,series_id=c.series_id,version=max(v.version for v in versions)+1,topic=c.topic,script=script,fact_refs=refs,storyboard=storyboard,channel=_text(body,'channel',c.channel,40),cover_title=_text(body,'cover_title',c.cover_title,120),aspect_ratio=ratio,subtitle_style=_text(body,'subtitle_style',c.subtitle_style,40),status='draft',created_by=ctx.user_id)
    db.add(new); db.flush()
    for plan in db.scalars(_scope(db,ContentPlan,ctx,pid).where(ContentPlan.content_version_id==c.id).with_for_update()).all():
        plan.content_version_id = new.id
    return content_dict(new)


def _check_generated_material_copy(data, assets):
    captions=[str(shot.get('caption','')) for shot in _shots(data.get('storyboard',[]))]
    if any(re.search(r'(?:镜头|画面|字幕)(?:缓慢|逐渐)?(?:收束|推近|拉远|切换|对准|转向|打出|引导)', caption)
           for caption in captions):
        raise ValueError('分镜caption应为观众字幕，不能把拍摄或剪辑指令烧入成片')
    if assets and all(asset.origin_type in ('test','template') for asset in assets):
        from .provider import CAPTURE_CLAIMS, SyntheticOriginError, source_claim_negated
        # Negation covers only a closed source-word list, never a later claim.
        fields=[('topic',str(data.get('topic',''))),('script',data['script']),('cover_title',str(data.get('cover_title','')))]
        fields += [(f'storyboard[{index}].caption',caption) for index,caption in enumerate(captions)]
        for field,text in fields:
            for sentence in re.split(r'[。！？；\n]',text):
                for match in CAPTURE_CLAIMS.finditer(sentence):
                    if not source_claim_negated(sentence,match.start()):
                        excerpt=sentence[max(0,match.start()-60):min(len(sentence),match.end()+100)]
                        raise SyntheticOriginError('合成或示意素材不能声称实拍、现场记录或实测，请保留示意说明',
                            field=field,matched_claim=match.group(0),excerpt=excerpt)


def validate_generated_content(db,ctx,pid,data):
    if not isinstance(data,dict) or not isinstance(data.get('script'),str) or not data['script'].strip(): raise ValueError('模型未返回有效文案')
    try:
        refs=_references(data.get('fact_refs',[]))
        if not refs: raise ValueError('模型未返回事实引用')
        facts=[]
        for ref in refs:
            f=_record(db,Fact,ctx,pid,ref['id'])
            if not valid_fact(f) or ref['revision']!=f.revision: raise ValueError('模型引用了未经核验或过期的事实')
            facts.append(f)
        _check_visible_quantities(data['script'],data.get('cover_title',''),data.get('storyboard',[]),facts)
        board=data.get('storyboard',[])
        longform=isinstance(board,dict) and board.get('schema_version')==2
        if longform:
            from .longform_generation import validate_storyboard
            shots=validate_storyboard(board,data['script'])
            _check_quantities(str(data.get('topic','')),facts)
            for chapter in board['chapters']:
                _check_quantities(chapter['title'],facts);_check_quantities(chapter['purpose'],facts)
        else:
            shots=_shots(board)
        p=require_project(db,ctx,pid)
        total=0
        selected_assets=[]
        for shot in shots:
            if not isinstance(shot,dict): raise ValueError('模型分镜格式不正确')
            if longform:
                available={fact.id:fact for fact in facts}
                per_shot=[]
                for ref in shot['fact_refs']:
                    fact=available.get(ref['id'])
                    if fact is None or ref['revision']!=fact.revision:
                        raise ValueError('镜头引用事实未包含在当前内容的有效引用里')
                    per_shot.append(fact)
                for text in (shot['voiceover'],shot['caption'],shot['visual_direction']):
                    _check_quantities(text,per_shot)
                if shot['production_status']=='planned' and shot['asset_id'] is None:
                    total+=shot['duration']
                    continue
            a=_record(db,Asset,ctx,pid,shot.get('asset_id',''))
            if a.rights!='authorized' or not a.rights_evidence: raise ValueError('模型引用了未授权素材')
            if (a.metadata_json or {}).get('usage_scope')=='research_only': raise ValueError('研究素材不能用于营销成片')
            if a.origin_type!='actual' and not (p.is_test and a.origin_type in ('test','template')): raise ValueError('模型引用了非当前项目实拍素材')
            selected_assets.append(a)
            if longform:
                _check_generated_material_copy({'topic':'','script':shot['voiceover']+'\n'+shot['visual_direction'],
                    'cover_title':'','storyboard':[{'caption':shot['caption']}]},[a])
            duration=shot.get('duration')
            start=shot.get('start',0)
            if type(duration) not in (int,float) or not math.isfinite(duration) or not 0.1<=duration<=60 or type(start) not in (int,float) or not math.isfinite(start) or start<0: raise ValueError('模型分镜时长不正确')
            total+=duration
        if shots and not longform and not 15<=total<=60: raise ValueError('模型分镜总时长需为15至60秒')
        origin_assets=selected_assets
        if longform and not origin_assets and material_basis(p,source_assets(db,ctx,p))['kind'] in ('schematic','unavailable'):
            from types import SimpleNamespace
            origin_assets=[SimpleNamespace(origin_type='template')]
        if longform:
            _check_generated_material_copy({'topic':'','script':'\n'.join(chapter['title']+'\n'+chapter['purpose'] for chapter in board['chapters']),
                'cover_title':'','storyboard':[]},origin_assets)
        _check_generated_material_copy(data,origin_assets)
        return data | {'fact_refs':refs,'storyboard':board if longform else shots}
    except HTTPException as exc: raise ValueError(exc.detail) from exc


def _generation_plan(db, ctx, pid, plan_id, topic_id, lock=False):
    if plan_id is None or plan_id == '':
        return None, None
    if not isinstance(plan_id, str) or len(plan_id) > 36:
        raise HTTPException(422, '计划编号格式不正确')
    query = _scope(db, ContentPlan, ctx, pid).where(ContentPlan.id==plan_id)
    plan = db.scalar(query.with_for_update() if lock else query)
    if plan is None:
        raise HTTPException(404, '计划不存在或无权访问')
    if plan.topic_id != topic_id:
        raise HTTPException(409, '选题与策划计划不一致，请刷新后重试')
    account = None
    if plan.account_id:
        query = _scope(db, ContentAccount, ctx, pid).where(ContentAccount.id==plan.account_id)
        account = db.scalar(query.with_for_update() if lock else query)
        if account is None:
            raise HTTPException(404, '账号不存在或无权访问')
    if account and (account.status != 'managed' or account.platform != plan.channel):
        raise HTTPException(409, '账号已暂停或平台已变更，请先调整策划计划')
    return plan, account


def _plan_generation_snapshot(plan, account):
    if not plan:
        return None
    return tuple(getattr(plan, k) for k in ('title','angle','brief','channel','account_id','topic_id')) + (
        tuple(getattr(account, k) for k in ('name','positioning','audience','voice','red_lines')) if account else None,
        json.dumps(plan.source_snapshot, sort_keys=True, ensure_ascii=False, separators=(',', ':')))


def _plan_target_duration(brief):
    # Only the structured plan marker is authoritative; quoted reference lengths
    # and other numbers in planning background must not become generation rules.
    targets=re.findall(r'^目标：([0-9]+)秒；',brief or '',re.MULTILINE)
    if not targets:
        return None
    if any(len(value)>2 or not 15<=int(value)<=60 for value in targets):
        raise ValueError('策划目标时长需为15至60秒')
    durations={int(value) for value in targets}
    if len(durations)!=1:
        raise ValueError('策划目标时长存在冲突，请保留一个目标')
    return durations.pop()


def source_assets(db,ctx,project):
    """Only project sources enter new production, never prior generated outputs."""
    rows=db.scalars(_scope(db,Asset,ctx,project.id).where(Asset.rights=='authorized',
        Asset.kind.in_(('image','video'))).order_by(Asset.created_at,Asset.id)).all()
    return [asset for asset in rows if asset.rights_evidence
        and (asset.metadata_json or {}).get('usage_scope')!='research_only'
        and (asset.metadata_json or {}).get('presentation')!='story_card'
        and not (asset.metadata_json or {}).get('content_version_id')
        and not (asset.metadata_json or {}).get('renderer')
        and (asset.origin_type=='actual' or (project.is_test and asset.origin_type in ('test','template')))]


def material_basis(project,assets):
    from .provider import SCHEMATIC_DISCLOSURE
    actual=any(asset.origin_type=='actual' for asset in assets)
    schematic=any(asset.origin_type in ('test','template') for asset in assets)
    kind='mixed' if actual and schematic else 'actual' if actual else 'schematic' if schematic else 'unavailable'
    if kind=='schematic':
        requirements=[f'标题与字幕用空间示意或资料讲解表达；口播自然包含“{SCHEMATIC_DISCLOSURE}”，不罗列来源术语作为免责声明。',
            '策划中的实拍、实测、现场或真实样板间措辞是拟创作方向，不能改变当前素材来源；改为资料讲解或示意导览。',
            '保留已核验数字与单位，不声称示意画面证明现场拍摄、真实居住体验或测量过程。']
    elif kind=='unavailable':
        requirements=['当前没有可用于制作的授权源素材；策划须写清准备条件，不声称已实拍或成片。']
    else:
        requirements=['每个镜头按对应素材来源表达；示意素材须标示意，不把素材名称当作人物、动作或现场测量证据。']
    counts={}
    for asset in assets:
        counts[asset.origin_type]=counts.get(asset.origin_type,0)+1
    return {'kind':kind,'is_test':project.is_test,'real_capture_available':actual,
        'disclosure':SCHEMATIC_DISCLOSURE if kind=='schematic' else None,
        'origin_counts':counts,'source_asset_ids':[asset.id for asset in assets],
        'expression_requirements':requirements}


def _record_source_diagnostic(job,error):
    """Keep a redacted source-failure receipt outside public artifact routes."""
    from .config import settings
    if not isinstance(job.id,str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,100}',job.id):
        return
    try:
        root=settings.storage_root.resolve()
        for part in ('.private','generation-diagnostics',job.id):
            root=root/part
            if root.is_symlink():
                return
            root.mkdir(mode=0o700,exist_ok=True)
        row={'job_id':job.id,'plan_id':(job.payload or {}).get('plan_id'),'topic_id':(job.payload or {}).get('topic_id'),
             'attempt':job.attempt,'code':error.diagnostic_code,'field':error.field,
             'matched_claim':error.matched_claim,'excerpt':error.excerpt,'created_at':now().isoformat()}
        descriptor=os.open(root/'source-expression.jsonl',os.O_CREAT|os.O_APPEND|os.O_WRONLY|os.O_NOFOLLOW,0o600)
        os.fchmod(descriptor,0o600)
        with os.fdopen(descriptor,'a',encoding='utf-8') as output:
            output.write(json.dumps(row,ensure_ascii=False)+'\n')
    except (OSError,ValueError):
        # Diagnostics must never replace the original domain failure.
        return


def handle_generate(db,job):
    from .provider import generate_content
    from .model_budget import check_daily_token_budget
    from .agent_models import ModelUsage
    from .config import settings
    def checkpoint(progress, stage, **extra):
        callback=db.info.get('job_progress')
        if callback: callback(progress=progress,stage=stage,**extra)
    ctx=context_for_user(db,job.user_id,job.tenant_id)
    p=require_project(db,ctx,job.property_id,EDIT_ROLES)
    existing=db.scalar(_scope(db,ContentVersion,ctx,p.id).where(ContentVersion.series_id==job.id))
    if existing: return {'content_version_id':existing.id}
    checkpoint(0.05,'核对事实与素材授权')
    topic=next((t for t in topics_data(db,ctx,p.id) if t['id']==job.payload.get('topic_id')),None)
    if not topic or not topic['eligible']: raise ValueError('选题所需事实尚未核验')
    plan, account = _generation_plan(db, ctx, p.id, job.payload.get('plan_id'), topic['id'])
    plan_snapshot = _plan_generation_snapshot(plan, account)
    planning = (plan.source_snapshot or {}).get('planning') if plan else None
    target_duration = planning.get('target_seconds') if planning else _plan_target_duration(plan.brief) if plan else None
    longform=type(target_duration) is int and target_duration>60
    if longform:
        from .production_contract import validate_duration
        validate_duration(target_duration)
    if planning:
        # Structured suggestions can combine area, width and layout in one
        # idea. Keep their script fact scope aligned with planning suggestions.
        facts=[fact for fact in db.scalars(_scope(db,Fact,ctx,p.id)
            .order_by(Fact.semantic_key,Fact.id)).all() if valid_fact(fact)]
    else:
        facts=[_record(db,Fact,ctx,p.id,r['id']) for r in topic['fact_refs']]
    assets=source_assets(db,ctx,p)
    if not assets and not longform: raise ValueError('请先上传并授权当前项目实拍素材')
    payload={'project':{'id':p.id,'name':p.name,'city':p.city,'is_test':p.is_test},'facts':[{'id':f.id,'label':f.label,'value':f.value,'unit':f.unit,'source':f.source,'revision':f.revision} for f in facts],'topic':topic,'channel':job.payload.get('channel') or p.channel,'audience':p.audience,'assets':[{'id':a.id,'name':a.name,'kind':a.kind,'origin_type':a.origin_type,'description':(a.metadata_json or {}).get('description','')} for a in assets]}
    payload['material_basis']=material_basis(p,assets)
    if target_duration is not None:
        payload['target_duration_seconds'] = target_duration
    if plan:
        from .agent import sanitize
        from .video_breakdown import validate_reference_source
        from .content_planning import planning_generation_context, validate_planning_reference
        validate_reference_source(db, ctx, p.id, plan.source_snapshot)
        validate_planning_reference(db, ctx, p.id, plan.source_snapshot)
        structured_context = planning_generation_context(plan.source_snapshot)
        background = plan.source_snapshot
        if isinstance(background, dict) and 'reference_analysis' in background:
            from .video_breakdown import generation_reference_context
            background = generation_reference_context(background)
        elif isinstance(background, dict) and background.get('planning_reference'):
            background = {'reference': structured_context.get('reference', {})}
        elif isinstance(background, dict) and background.get('source_key'):
            from .discovery import _canonical_work_url
            identity = _canonical_work_url(background.get('source_url'))
            background = ({'source_url':identity[2], 'content_identity':identity[0]+':'+identity[1],
                'source_type':'public_video_reference', 'evidence_note':'仅保留参考来源，原片描述不是当前项目事实'}
                if identity else {})
        payload['channel'] = plan.channel
        payload['planning_context'] = sanitize({'plan_id':plan.id, 'title':plan.title, 'angle':plan.angle, 'brief':plan.brief,
            'background':background, 'source_status':'选题背景，未经房产事实核验，不能作为数值或承诺依据'})
        payload['planning_context'].update(sanitize(structured_context))
        if payload['material_basis']['kind']=='schematic':
            from .provider import schematic_planning_projection
            payload['planning_context']=schematic_planning_projection(payload['planning_context'])
            payload['planning_context']['source_basis_override']=True
        payload['planning_context']['expression_requirements']=payload['material_basis']['expression_requirements']
        if account:
            payload['account'] = sanitize({k:getattr(account,k) for k in ('name','positioning','audience','voice','red_lines')})
    def evidence_signature():
        return {'project':(p.id,p.tenant_id,p.is_test,p.marketing_confirmed),
            'facts':[(f.id,f.label,f.value,f.unit,f.source,f.revision,f.status,iso(f.valid_until)) for f in facts],
            'assets':[(a.id,a.sha256,a.rights,a.rights_evidence,a.rights_revision,a.origin_type,a.kind,
                       json.dumps(a.metadata_json,sort_keys=True,ensure_ascii=False)) for a in assets]}
    evidence_snapshot=evidence_signature() if longform else None
    ticket=(job.id,job.lease_token,job.attempt)
    if longform:
        payload['_authority']={'tenant_id':ctx.tenant_id,'property_id':p.id,'user_id':ctx.user_id}
        payload['_provenance']=evidence_snapshot
    def guard(*,lock=False):
        nonlocal ctx, plan, account
        cancelled=db.info.get('job_cancelled')
        if cancelled and cancelled(): raise ValueError('任务已取消')
        db.expire_all()
        ctx=context_for_user(db,job.user_id,job.tenant_id)
        require_project(db,ctx,p.id,EDIT_ROLES)
        plan, account = _generation_plan(db,ctx,p.id,job.payload.get('plan_id'),topic['id'],lock=lock)
        if plan_snapshot != _plan_generation_snapshot(plan,account):
            raise HTTPException(409,'策划或账号画像在生成期间已更新，请按新计划重新生成')
        if plan:
            validate_reference_source(db, ctx, p.id, plan.source_snapshot)
            validate_planning_reference(db, ctx, p.id, plan.source_snapshot)
            planning_generation_context(plan.source_snapshot)
        revisions={row['id']:row['revision'] for row in payload['facts']}
        for fact in facts:
            db.refresh(fact)
            if not valid_fact(fact) or fact.revision!=revisions[fact.id]:
                raise ValueError('生成所用楼盘事实或资料已改变，请按最新资料重新生成')
        available={asset.id for asset in source_assets(db,ctx,p)}
        if any(asset.id not in available for asset in assets):
            raise ValueError('生成所用素材包含未授权素材，或来源已改变，请核对后重新生成')
        if longform:
            db.refresh(p)
            for asset in assets:db.refresh(asset)
            if evidence_signature()!=evidence_snapshot:
                raise ValueError('长视频生成所用事实或素材资料已改变，请按最新资料重新生成')
            if ticket[1]:
                from .job_models import Job
                row=db.execute(select(Job.status,Job.lease_token,Job.attempt).where(Job.id==ticket[0],
                    Job.tenant_id==ctx.tenant_id,Job.property_id==p.id)).one_or_none()
                if row is None or tuple(row)!=('running',ticket[1],ticket[2]):
                    from .worker import LostLease
                    raise LostLease()

    def before_call():
        guard()
        local_generation=settings.native_text_backend in ('mlx', 'resident_ppu')
        check_daily_token_budget(db,ctx.tenant_id,6000 if longform else 0,
                                 local_generation=local_generation)

    def record_usage(response):
        usage=response.get('usage')
        if not isinstance(usage,dict): return
        # Record only actual supplier counts, never estimated usage or secrets.
        counts={key:value for key,value in usage.items() if key in ('prompt_tokens','completion_tokens')
                and type(value) is int and value>=0}
        if not counts: return
        # Billing survives a worker rollback when evidence validation rejects
        # the draft. This transaction does not commit pending business records.
        with Session(bind=db.get_bind()) as accounting:
            accounting.info.update(tenant_id=job.tenant_id,user_id=job.user_id)
            accounting.add(ModelUsage(tenant_id=job.tenant_id,property_id=job.property_id,job_id=job.id,
                model=str(response.get('model') or settings.dashscope_model)[:100],
                input_tokens=counts.get('prompt_tokens',0),output_tokens=counts.get('completion_tokens',0)))
            accounting.commit()

    def validate(generated):
        # Recheck evidence after every slow supplier call, including correction.
        guard(lock=True)
        from .provider import SyntheticOriginError
        try:
            return validate_generated_content(db,ctx,p.id,generated)
        except SyntheticOriginError as error:
            _record_source_diagnostic(job,error)
            raise

    checkpoint(0.15,'正在生成脚本与分镜')
    if longform:
        from .job_models import Job
        from .longform_generation import checkpoint_directory, generate_longform,latest_checkpoint_directory,source_bound_chapter_review
        base=checkpoint_directory(ctx.tenant_id,p.id,job.id)
        resume_dir=latest_checkpoint_directory(base)
        resume_id=job.payload.get('resume_from_job_id')
        if isinstance(resume_id,str) and resume_id!=job.id:
            previous=db.scalar(select(Job).where(Job.id==resume_id,Job.tenant_id==ctx.tenant_id,
                Job.property_id==p.id,Job.kind=='generate_content'))
            if (previous and (previous.payload or {}).get('plan_id')==job.payload.get('plan_id')
                    and (previous.payload or {}).get('topic_id')==topic['id']):
                resume_dir=latest_checkpoint_directory(checkpoint_directory(ctx.tenant_id,p.id,previous.id))
        def chapter_progress(counters):
            fraction=counters['completed_chapters']/counters['total_chapters']
            checkpoint(.15+.55*fraction,counters['stage'],longform_progress=counters)
        lease=hashlib.sha256(str(ticket[1] or 'direct').encode()).hexdigest()[:12]
        directory=base/f'attempt-{ticket[2] or 0}-{lease}'
        def review_chapter(draft,chapter,data):
            return source_bound_chapter_review(draft,chapter,data,before_call=before_call,guard=guard,on_usage=record_usage)
        generated=generate_longform(payload,checkpoint_dir=directory,
            request_id=job.id,validate=validate,before_call=before_call,on_usage=record_usage,
            on_progress=chapter_progress,resume_dir=resume_dir,checkpoint_guard=guard,review_chapter=review_chapter)
    else:
        generated=generate_content(payload,validate=validate,before_call=before_call,on_usage=record_usage)
    checkpoint(0.70,'检查生成依据与素材权限')
    data=validate(generated)
    checkpoint(0.95,'保存草稿，等待人工审核')
    c=ContentVersion(tenant_id=ctx.tenant_id,property_id=p.id,series_id=job.id,version=1,topic=str(data.get('topic') or topic['title'])[:500],script=data['script'],fact_refs=data['fact_refs'],storyboard=data['storyboard'],channel=payload['channel'],cover_title=str(data.get('cover_title') or p.name)[:120],aspect_ratio='9:16',subtitle_style='minimal',status='draft',created_by=ctx.user_id)
    db.add(c); db.flush()
    if plan:
        plan.content_version_id = c.id
    return {'content_version_id':c.id, **({'plan_id':plan.id} if plan else {})}

registered_handlers={'generate_content':handle_generate}


def _inquiry_query(db,ctx,pid):
    query=_scope(db,Inquiry,ctx,pid)
    return query.where(Inquiry.owner_id==ctx.user_id) if ctx.role=='sales' else query


def _inquiry(db,ctx,pid,iid,roles=None):
    require_project(db,ctx,pid,roles)
    i=db.scalar(_inquiry_query(db,ctx,pid).where(Inquiry.id==iid))
    if i is None: raise HTTPException(404,'咨询不存在或无权访问')
    return i


def _lead_query(db,ctx,pid):
    query=_scope(db,Lead,ctx,pid)
    return query.where(Lead.owner_id==ctx.user_id) if ctx.role=='sales' else query


def _lead(db,ctx,pid,lid,roles=None,require_active=False):
    require_project(db,ctx,pid,roles)
    l=db.scalar(_lead_query(db,ctx,pid).where(Lead.id==lid))
    if l is None: raise HTTPException(404,'客户不存在或无权访问')
    i=_record(db,Inquiry,ctx,pid,l.inquiry_id)
    if require_active and (i.withdrawn or l.stage=='withdrawn' or (l.stage!='captured' and (not i.consent or not i.contactable))): raise HTTPException(409,'客户联系授权已撤回，不可继续跟进')
    return l


def _valid_owner(db,ctx,pid,user_id):
    membership=db.scalar(select(Membership).where(Membership.user_id==user_id,Membership.tenant_id==ctx.tenant_id,Membership.active.is_(True)))
    user=db.get(User,user_id)
    if not membership or membership.role not in ('owner','admin','sales') or not user or not user.active or (membership.project_ids is not None and pid not in membership.project_ids): raise HTTPException(422,'承接人没有当前项目销售权限')
    return user_id


def _request_retry(db,ctx,pid,request_key,request_hash):
    if not request_key: return None
    existing=db.scalar(_scope(db,Inquiry,ctx,pid).where(Inquiry.request_key==request_key))
    if existing is None: return None
    if ctx.role=='sales' and existing.owner_id!=ctx.user_id: raise HTTPException(404,'咨询不存在或无权访问')
    if existing.request_hash!=request_hash: raise HTTPException(409,'该请求编号已用于不同咨询资料，请重新开始录入')
    return inquiry_dict(existing)


def create_inquiry(db,ctx,pid,body):
    p=require_project(db,ctx,pid,SALES_ROLES)
    contact=_text(body,'contact',max_length=200)
    contactable=body.get('contactable') is True
    consent=body.get('consent') is True
    if contact and not consent: raise HTTPException(422,'保存联系方式前需确认客户联系授权')
    if contactable and not contact: raise HTTPException(422,'可联系咨询需提供联系方式')
    owner=body.get('owner_id') or ctx.user_id
    if ctx.role=='sales' and owner!=ctx.user_id: raise HTTPException(403,'销售只能录入分配给自己的咨询')
    _valid_owner(db,ctx,pid,owner)
    confidence=normalize_source_confidence(_text(body,'source_confidence','unknown',30))
    source=_text(body,'source_text',max_length=4000)
    if confidence=='confirmed' and not source: raise HTTPException(422,'已核验来源必须填写来源证据')
    values={'name':_text(body,'name','新咨询',120) or '新咨询','need':_text(body,'need',max_length=4000),'channel':_text(body,'channel',p.channel,40),'source_confidence':confidence,'source_text':source,'contactable':contactable,'consent':consent,'owner_id':owner}
    request_key=_text(body,'request_key',max_length=120) or None
    # This HMAC describes a request retry, never an identity match.
    request_hash=pii_fingerprint(json.dumps(values|{'contact':contact},sort_keys=True,ensure_ascii=False,separators=(',',':')),ctx.tenant_id) if request_key else None
    retried=_request_retry(db,ctx,pid,request_key,request_hash)
    if retried is not None: return retried
    i=Inquiry(tenant_id=ctx.tenant_id,property_id=pid,**values,contact_cipher=encrypt_pii(contact) if contact else None,contact_fingerprint=_fingerprint(db,ctx,pid,contact),request_key=request_key,request_hash=request_hash,qualified=False,qualification_checks={},withdrawn=False)
    try:
        with db.begin_nested():
            db.add(i); db.flush()
    except IntegrityError:
        retried=_request_retry(db,ctx,pid,request_key,request_hash)
        if retried is None: raise HTTPException(409,'咨询记录同时发生变更，请刷新重试') from None
        return retried
    return inquiry_dict(i)


def qualify_inquiry(db,ctx,pid,iid,body):
    i=_inquiry(db,ctx,pid,iid,SALES_ROLES)
    db.refresh(i,with_for_update=True)
    checks={k:body.get(k) is True for k in ('contactable','consent','demand_confirmed','fit_confirmed')}
    if not all(checks.values()) or i.withdrawn or not i.contact_cipher or not i.consent: raise HTTPException(422,'合格线索须人工确认可联系、联系授权、真实需求及项目匹配四项')
    requested=_text(body,'existing_lead_id',max_length=36) or None
    existing=db.scalar(_scope(db,Lead,ctx,pid).where(Lead.inquiry_id==iid))
    resolved_id=i.linked_lead_id or (existing.id if existing else None)
    if resolved_id:
        if requested and requested!=resolved_id: raise HTTPException(409,'该咨询已关联客户，不能自动重新合并')
        resolved=_lead(db,ctx,pid,resolved_id,SALES_ROLES,True)
        if resolved.stage=='captured':
            i.contactable=True; i.consent=True; i.qualified=True
            i.qualification_checks=checks|{'confirmed_by':ctx.user_id,'confirmed_at':iso(now())}
            resolved.stage='qualified'; resolved.updated_at=now()
            resolved.stage_history=(resolved.stage_history or [])+[{'stage':'qualified','by':ctx.user_id,'at':iso(now())}]
            db.flush()
        return lead_dict(resolved)
    confirmed=checks|{'confirmed_by':ctx.user_id,'confirmed_at':iso(now())}
    if requested:
        if body.get('same_contact_confirmed') is not True: raise HTTPException(422,'关联已有客户须人工确认是同一位客户')
        target=_lead(db,ctx,pid,requested,SALES_ROLES,True)
        i.contactable=True; i.consent=True; i.qualified=True; i.owner_id=target.owner_id; i.linked_lead_id=target.id
        i.qualification_checks=confirmed|{'same_contact_confirmed':True,'existing_lead_id':target.id}
        db.flush(); return lead_dict(target)
    owner=body.get('owner_id') or i.owner_id or ctx.user_id
    if ctx.role=='sales' and owner!=ctx.user_id: raise HTTPException(403,'销售不能给其他人分配线索')
    _valid_owner(db,ctx,pid,owner)
    i.contactable=True; i.consent=True; i.qualified=True; i.qualification_checks=confirmed; i.owner_id=owner
    l=Lead(tenant_id=ctx.tenant_id,property_id=pid,inquiry_id=iid,name=i.name,need=_text(body,'need',i.need,4000),stage='qualified',owner_id=owner,next_action=_text(body,'next_action','确认沟通时间',4000),source_confidence=i.source_confidence,stage_history=[{'stage':'qualified','by':ctx.user_id,'at':iso(now())}])
    try:
        with db.begin_nested():
            db.add(l); db.flush()
    except IntegrityError:
        existing=db.scalar(_scope(db,Lead,ctx,pid).where(Lead.inquiry_id==iid))
        if existing is None: raise HTTPException(409,'线索同时发生变更，请刷新重试') from None
        return lead_dict(existing)
    return lead_dict(l)


def withdraw_inquiry(db,ctx,pid,iid):
    i=_inquiry(db,ctx,pid,iid,SALES_ROLES)
    l=_record(db,Lead,ctx,pid,i.linked_lead_id) if i.linked_lead_id else db.scalar(_scope(db,Lead,ctx,pid).where(Lead.inquiry_id==iid))
    affected=[i]
    if l:
        _lead(db,ctx,pid,l.id,SALES_ROLES)
        affected=db.scalars(_scope(db,Inquiry,ctx,pid).where((Inquiry.id==l.inquiry_id)|(Inquiry.linked_lead_id==l.id))).all()
        l.stage='withdrawn'; l.next_action='联系授权已撤回'; l.stage_history=(l.stage_history or [])+[{'stage':'withdrawn','by':ctx.user_id,'at':iso(now())}]
    for inquiry in affected:
        inquiry.withdrawn=True; inquiry.contact_cipher=None; inquiry.contact_fingerprint=None; inquiry.contactable=False; inquiry.consent=False
    db.flush(); return inquiry_dict(i)


def list_leads_data(db,ctx,pid):
    require_project(db,ctx,pid)
    records=db.scalars(_lead_query(db,ctx,pid).where(Lead.stage!='withdrawn').order_by(Lead.updated_at.desc())).all()
    result=[]
    for l in records:
        i=_record(db,Inquiry,ctx,pid,l.inquiry_id)
        if not i.withdrawn and (l.stage=='captured' or (i.consent and i.contactable)): result.append(lead_dict(l))
    return result


def lead_detail_data(db,ctx,pid,lid):
    l=_lead(db,ctx,pid,lid,require_active=True)
    i=_record(db,Inquiry,ctx,pid,l.inquiry_id)
    followups=db.scalars(_scope(db,FollowUp,ctx,pid).where(FollowUp.lead_id==lid).order_by(FollowUp.created_at.desc())).all()
    from .secretary import profile_data, list_viewings_data
    return {'lead':lead_dict(l),'inquiry':inquiry_dict(i),'profile':profile_data(db,ctx,pid,lid),'viewings':list_viewings_data(db,ctx,pid,lid),'followups':[{'id':f.id,'lead_id':f.lead_id,'note':f.note,'next_action':f.next_action,'created_by':f.created_by,'created_at':iso(f.created_at)} for f in followups]}


def add_followup(db,ctx,pid,lid,body):
    l=_lead(db,ctx,pid,lid,SALES_ROLES,True)
    note=_required_text(body,'note',4000); next_action=_text(body,'next_action',max_length=4000)
    f=FollowUp(tenant_id=ctx.tenant_id,property_id=pid,lead_id=lid,note=note,next_action=next_action,created_by=ctx.user_id)
    db.add(f); l.next_action=next_action; l.updated_at=now(); db.flush()
    return {'id':f.id,'lead_id':lid,'note':f.note,'next_action':f.next_action,'created_by':f.created_by,'created_at':iso(f.created_at)}


def change_stage(db,ctx,pid,lid,body):
    l=_lead(db,ctx,pid,lid,SALES_ROLES,True)
    stage=_required_text(body,'stage',30)
    if stage=='withdrawn': return withdraw_inquiry(db,ctx,pid,l.inquiry_id)
    if stage not in STAGES+('lost',): raise HTTPException(422,'客户阶段不支持')
    if l.stage=='captured': raise HTTPException(409,'待完善客户需先补充联系方式、联系授权并完成资格确认')
    if l.stage in ('lost','won'): raise HTTPException(409,'已结案客户不能自动回到销售阶段')
    if stage!=l.stage and stage!='lost' and STAGES.index(stage)!=STAGES.index(l.stage)+1: raise HTTPException(409,'请按资格确认、预约、到访、洽谈、成交顺序记录')
    if stage!=l.stage: l.stage_history=(l.stage_history or [])+[{'stage':stage,'by':ctx.user_id,'at':iso(now())}]
    l.stage=stage; l.updated_at=now(); db.flush(); return lead_dict(l)


def _visible_publications(db,ctx,pid):
    return db.scalars(_scope(db,Publication,ctx,pid).where(Publication.receipt_status=='verified',Publication.platform_state=='live').order_by(Publication.created_at.desc())).all()


def overview_data(db,ctx,pid):
    p=require_project(db,ctx,pid)
    inquiries=db.scalars(_inquiry_query(db,ctx,pid).order_by(Inquiry.created_at.desc())).all()
    leads=db.scalars(_lead_query(db,ctx,pid)).all()
    pubs=_visible_publications(db,ctx,pid)
    views=[(x.metrics or {}).get('D7',{}).get('views') if (x.metrics or {}).get('D7') else None for x in pubs]
    unknown=sum(i.source_confidence=='unknown' for i in inquiries)
    qualified_ids={i.id for i in inquiries if i.qualified}
    qualified_count=sum(l.inquiry_id in qualified_ids or any(h.get('stage')=='qualified' for h in (l.stage_history or [])) or (l.stage!='captured' and not any(h.get('stage')=='captured' for h in (l.stage_history or []))) for l in leads)
    metrics={'published':len(pubs),'inquiries':len(inquiries),'qualified':qualified_count,'appointments':sum(any(h.get('stage')=='appointment' for h in (l.stage_history or [])) for l in leads),'visits':sum(any(h.get('stage')=='visit_completed' for h in (l.stage_history or [])) for l in leads),'views':sum(views) if views and all(v is not None for v in views) else None,'unknown_rate':round(unknown/len(inquiries)*100,1) if inquiries else None}
    contents=db.scalars(_scope(db,ContentVersion,ctx,pid).order_by(ContentVersion.created_at.desc()).limit(12)).all()
    proposals=db.scalars(_scope(db,Proposal,ctx,pid).order_by(Proposal.created_at.desc())).all()
    return {'project':project_dict(db,ctx,p),'metrics':metrics,'pending_inquiries':[inquiry_dict(i) for i in inquiries if not i.qualified and not i.withdrawn],'content_versions':[content_dict(c,db,ctx) for c in contents],'publications':[publication_dict(x) for x in pubs],'proposals':[proposal_dict(x) for x in proposals]}


def reports_data(db,ctx,pid,window='D7'):
    require_project(db,ctx,pid)
    if window not in ('D7','D30'): raise HTTPException(422,'复盘窗口仅支持D7或D30')
    pubs=_visible_publications(db,ctx,pid)
    counters={k:[] for k in ('views','likes','comments','saves','inquiries','qualified')}
    for p in pubs:
        data=normalize_metrics(p.metrics).get(window)
        for k in counters: counters[k].append(data.get(k) if data else None)
    metrics={k:sum(vals) if vals and all(v is not None for v in vals) else None for k,vals in counters.items()}
    metrics['window_complete']=bool(pubs) and all(normalize_metrics(p.metrics).get(window) is not None for p in pubs)
    metrics['observed_publications']=sum(normalize_metrics(p.metrics).get(window) is not None for p in pubs)
    metrics['total_publications']=len(pubs)
    proposals=db.scalars(_scope(db,Proposal,ctx,pid).order_by(Proposal.created_at.desc())).all()
    return {'metrics':metrics,'publications':[publication_dict(p) for p in pubs],'proposals':[proposal_dict(p) for p in proposals],'window':window}


@router.get('/api/projects/{pid}/overview')
def overview(pid:str,db:Session=Depends(get_db),ctx:Context=Depends(get_context)): return overview_data(db,ctx,pid)

@router.get('/api/projects/{pid}/topics')
def topics(pid:str,db:Session=Depends(get_db),ctx:Context=Depends(get_context)): return topics_data(db,ctx,pid)

@router.get('/api/projects/{pid}/contents')
def contents(pid:str,db:Session=Depends(get_db),ctx:Context=Depends(get_context)):
    require_project(db,ctx,pid)
    return [content_dict(c,db,ctx) for c in db.scalars(_scope(db,ContentVersion,ctx,pid).order_by(ContentVersion.created_at.desc())).all()]

@router.post('/api/projects/{pid}/contents/generate')
def generate(pid:str,body:dict=Body(...),db:Session=Depends(get_db),ctx:Context=Depends(get_context)):
    from .jobs import enqueue_job
    p=require_project(db,ctx,pid,EDIT_ROLES)
    topic=next((t for t in topics_data(db,ctx,pid) if t['id']==body.get('topic_id')),None)
    if not topic: raise HTTPException(422,'请选择有效选题')
    if not topic['eligible']: raise HTTPException(409,'选题依赖的事实尚未核验')
    plan, _ = _generation_plan(db, ctx, pid, body.get('plan_id'), topic['id'],lock=True)
    payload = {'topic_id':topic['id'], 'channel':plan.channel if plan else _text(body,'channel',p.channel,40)}
    if plan:
        payload['plan_id'] = plan.id
        planning=(plan.source_snapshot or {}).get('planning') or {}
        if type(planning.get('target_seconds')) is int and planning['target_seconds']>60:
            from .job_models import Job
            from .longform_generation import checkpoint_directory,latest_checkpoint_directory
            requests=select(Job).where(Job.tenant_id==ctx.tenant_id,Job.property_id==pid,
                Job.kind=='generate_content',Job.payload['plan_id'].as_string()==plan.id,
                Job.payload['topic_id'].as_string()==topic['id']).order_by(Job.created_at.desc(),Job.id)
            active=db.scalar(requests.where(Job.status.in_(('queued','running'))).limit(1))
            if active:
                return {'job_id':active.id}
            for previous in db.scalars(requests.where(Job.status=='failed').limit(10)).all():
                directory=checkpoint_directory(ctx.tenant_id,pid,previous.id)
                if latest_checkpoint_directory(directory):
                    payload['resume_from_job_id']=previous.id
                    break
    j=enqueue_job(db,ctx,pid,'generate_content',payload)
    db.commit(); return {'job_id':j.id}

@router.post('/api/projects/{pid}/contents/{cid}/revise')
def revise(pid:str,cid:str,body:dict=Body(...),db:Session=Depends(get_db),ctx:Context=Depends(get_context)):
    result=revise_content(db,ctx,pid,cid,body); db.commit(); return result

@router.post('/api/projects/{pid}/contents/{cid}/render')
def render(pid:str,cid:str,db:Session=Depends(get_db),ctx:Context=Depends(get_context)):
    from .jobs import enqueue_job
    c=_content(db,ctx,pid,cid,EDIT_ROLES); validate_content_ready(db,ctx,c)
    j=enqueue_job(db,ctx,pid,'render_content',{'content_version_id':cid}); db.commit(); return {'job_id':j.id}

@router.post('/api/projects/{pid}/contents/{cid}/illustrate')
def illustrate(pid:str,cid:str,db:Session=Depends(get_db),ctx:Context=Depends(get_context)):
    from .jobs import enqueue_job
    from .job_models import Job
    from .story_card_production import illustration_snapshot
    _,digest=illustration_snapshot(db,ctx,pid,cid,lock=True)
    existing=db.scalar(select(Job).where(Job.tenant_id==ctx.tenant_id,Job.property_id==pid,
        Job.kind=='illustrate_content',Job.status.in_(('queued','running')),
        Job.payload['content_version_id'].as_string()==cid,
        Job.payload['source_snapshot_sha256'].as_string()==digest).order_by(Job.created_at.desc()).limit(1))
    if existing: return {'job_id':existing.id}
    j=enqueue_job(db,ctx,pid,'illustrate_content',{'content_version_id':cid,'source_snapshot_sha256':digest})
    db.commit(); return {'job_id':j.id}

@router.post('/api/projects/{pid}/contents/{cid}/approve')
def approve(pid:str,cid:str,body:dict=Body(...),db:Session=Depends(get_db),ctx:Context=Depends(get_context)):
    result=approve_content(db,ctx,pid,cid,body); db.commit(); return result

@router.get('/api/projects/{pid}/contents/{cid}/export')
def export(pid:str,cid:str,db:Session=Depends(get_db),ctx:Context=Depends(get_context)): return export_content(db,ctx,pid,cid)

@router.get('/api/projects/{pid}/publications')
def publications(pid:str,db:Session=Depends(get_db),ctx:Context=Depends(get_context)):
    require_project(db,ctx,pid)
    return [publication_dict(p) for p in db.scalars(_scope(db,Publication,ctx,pid).order_by(Publication.created_at.desc())).all()]

@router.post('/api/projects/{pid}/publications')
def create_publication(pid:str,body:dict=Body(...),db:Session=Depends(get_db),ctx:Context=Depends(get_context)):
    require_project(db,ctx,pid,EDIT_ROLES)
    r=_record(db,Release,ctx,pid,_required_text(body,'release_id',36))
    c=_record(db,ContentVersion,ctx,pid,r.content_version_id)
    valid=_valid_release(db,ctx,pid,c)
    if valid.id!=r.id: raise HTTPException(409,'发布回执必须关联当前有效审核版本')
    url=_required_text(body,'url',2000)
    parsed=urlparse(url)
    if parsed.scheme not in ('http','https') or not parsed.netloc or parsed.username or parsed.password: raise HTTPException(422,'请输入有效平台链接')
    state=_text(body,'platform_state','pending',30)
    if state not in PLATFORM_STATES: raise HTTPException(422,'平台状态不支持')
    existing=db.scalar(_scope(db,Publication,ctx,pid).where(Publication.url==url))
    if existing: return publication_dict(existing)
    p=Publication(tenant_id=ctx.tenant_id,property_id=pid,release_id=r.id,title=c.cover_title or c.topic,channel=c.channel,url=url,platform_state=state,receipt_status='unverified',metrics={'D7':None,'D30':None})
    db.add(p); db.commit(); return publication_dict(p)

@router.post('/api/projects/{pid}/publications/{pubid}/verify')
def verify_publication(pid:str,pubid:str,body:dict=Body(...),db:Session=Depends(get_db),ctx:Context=Depends(get_context)):
    require_project(db,ctx,pid,REVIEW_ROLES); _confirmed(body)
    p=_record(db,Publication,ctx,pid,pubid)
    state=_required_text(body,'platform_state',30)
    if state not in PLATFORM_STATES: raise HTTPException(422,'平台状态不支持')
    p.platform_state=state; p.receipt_status='verified'
    if 'metrics' in body: p.metrics=normalize_metrics(body['metrics'])
    db.commit(); return publication_dict(p)

@router.get('/api/projects/{pid}/inquiries')
def inquiries(pid:str,db:Session=Depends(get_db),ctx:Context=Depends(get_context)):
    require_project(db,ctx,pid)
    return [inquiry_dict(i) for i in db.scalars(_inquiry_query(db,ctx,pid).order_by(Inquiry.created_at.desc())).all()]

@router.post('/api/projects/{pid}/inquiries')
def new_inquiry(pid:str,body:dict=Body(...),db:Session=Depends(get_db),ctx:Context=Depends(get_context)):
    result=create_inquiry(db,ctx,pid,body); db.commit(); return result

@router.post('/api/projects/{pid}/inquiries/{iid}/qualify')
def qualify(pid:str,iid:str,body:dict=Body(...),db:Session=Depends(get_db),ctx:Context=Depends(get_context)):
    result=qualify_inquiry(db,ctx,pid,iid,body); db.commit(); return result

@router.post('/api/projects/{pid}/inquiries/{iid}/withdraw')
def withdraw(pid:str,iid:str,body:dict=Body(...),db:Session=Depends(get_db),ctx:Context=Depends(get_context)):
    _confirmed(body); result=withdraw_inquiry(db,ctx,pid,iid); db.commit(); return result

@router.post('/api/projects/{pid}/inquiries/{iid}/assign')
def assign_inquiry(pid:str,iid:str,body:dict=Body(...),db:Session=Depends(get_db),ctx:Context=Depends(get_context)):
    i=_inquiry(db,ctx,pid,iid,('owner','admin'))
    owner=_valid_owner(db,ctx,pid,_required_text(body,'owner_id',36)); i.owner_id=owner
    l=db.scalar(_scope(db,Lead,ctx,pid).where(Lead.inquiry_id==iid))
    if l: l.owner_id=owner; l.updated_at=now()
    db.commit(); return inquiry_dict(i)

def reveal_contact(db,ctx,pid,iid,body):
    i=_inquiry(db,ctx,pid,iid,SALES_ROLES)
    _required_text(body,'purpose',200)
    linked=_record(db,Lead,ctx,pid,i.linked_lead_id) if i.linked_lead_id else None
    if not i.consent or not i.contactable or i.withdrawn or not i.contact_cipher or (linked and linked.stage=='withdrawn'):
        raise HTTPException(409,'没有有效联系授权')
    try:
        value=decrypt_pii(i.contact_cipher)
    except Exception:
        raise HTTPException(409,'联系方式暂时不可用，请联系管理员') from None
    return {'contact':value}


@router.post('/api/projects/{pid}/inquiries/{iid}/contact-reveal')
def contact_reveal(pid:str,iid:str,body:dict=Body(...),db:Session=Depends(get_db),ctx:Context=Depends(get_context)):
    # This explicit audited action is never exposed to the agent tool registry.
    return reveal_contact(db,ctx,pid,iid,body)

@router.get('/api/projects/{pid}/leads')
def leads(pid:str,db:Session=Depends(get_db),ctx:Context=Depends(get_context)): return list_leads_data(db,ctx,pid)

@router.get('/api/projects/{pid}/leads/{lid}')
def lead_detail(pid:str,lid:str,db:Session=Depends(get_db),ctx:Context=Depends(get_context)): return lead_detail_data(db,ctx,pid,lid)

@router.post('/api/projects/{pid}/leads/{lid}/followups')
def followup(pid:str,lid:str,body:dict=Body(...),db:Session=Depends(get_db),ctx:Context=Depends(get_context)):
    result=add_followup(db,ctx,pid,lid,body); db.commit(); return result

@router.post('/api/projects/{pid}/leads/{lid}/stage')
def stage(pid:str,lid:str,body:dict=Body(...),db:Session=Depends(get_db),ctx:Context=Depends(get_context)):
    result=change_stage(db,ctx,pid,lid,body); db.commit(); return result

@router.get('/api/projects/{pid}/reports')
def reports(pid:str,window:str='D7',db:Session=Depends(get_db),ctx:Context=Depends(get_context)): return reports_data(db,ctx,pid,window)

@router.post('/api/projects/{pid}/proposals/{propid}/approve')
def approve_proposal(pid:str,propid:str,body:dict=Body(...),db:Session=Depends(get_db),ctx:Context=Depends(get_context)):
    require_project(db,ctx,pid,('owner','admin','content','reviewer')); _confirmed(body)
    p=_record(db,Proposal,ctx,pid,propid); p.status='approved'; db.commit(); return proposal_dict(p)
