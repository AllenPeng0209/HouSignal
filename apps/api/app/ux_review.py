"""Review the current evidence and append feedback without mutating a release."""
from fastapi import HTTPException
from .business import (REVIEW_ROLES, _content, _scope, _references, _shots, _text,
    _required_text, content_dict, aware_iso)
from .business_models import ContentReviewFeedback, Release
from .core_models import Fact, Asset
from .core import valid_fact
from .audit_models import AuditEvent
from .security import require_project


def _review_shots(content):
    return [dict(shot,id=shot.get('id') or f'shot-{index+1}') for index,shot in enumerate(_shots(content.storyboard)) if isinstance(shot,dict)]


def _frozen(db,ctx,pid,cid):
    return db.scalar(_scope(db,Release,ctx,pid).where(Release.content_version_id==cid).limit(1)) is not None


def feedback_dict(record):
    return {key:getattr(record,key) for key in ('id','content_version_id','version','render_asset_id','reviewer_id','reviewer_name','note','shot_id','sentence')} | {'created_at':aware_iso(record.created_at)}


def review_content_data(db,ctx,pid,cid):
    content=_content(db,ctx,pid,cid)
    project=require_project(db,ctx,pid)
    facts=[]
    for ref in _references(content.fact_refs):
        fact=db.scalar(_scope(db,Fact,ctx,pid).where(Fact.id==ref['id']))
        if fact is None:
            facts.append({'id':ref['id'],'expected_revision':ref['revision'],'missing':True,'valid':False}); continue
        facts.append({key:getattr(fact,key) for key in ('id','revision','label','value','unit','source','status')} | {
            'expected_revision':ref['revision'],'valid_until':aware_iso(fact.valid_until),
            'valid':valid_fact(fact) and fact.revision==ref['revision'],'missing':False})
    shots=_review_shots(content); assets=[]
    for aid in dict.fromkeys(shot.get('asset_id') for shot in shots if shot.get('asset_id')):
        asset=db.scalar(_scope(db,Asset,ctx,pid).where(Asset.id==aid))
        if asset is None:
            assets.append({'id':aid,'missing':True,'valid':False}); continue
        assets.append({key:getattr(asset,key) for key in ('id','name','kind','rights','rights_evidence','rights_revision','origin_type')} | {
            'missing':False,'valid':asset.rights=='authorized' and bool(asset.rights_evidence.strip()) and asset.kind in ('image','video') and (asset.metadata_json or {}).get('usage_scope')!='research_only' and (asset.origin_type=='actual' or (project.is_test and asset.origin_type in ('test','template')))})
    feedback=db.scalars(_scope(db,ContentReviewFeedback,ctx,pid).where(ContentReviewFeedback.content_version_id==cid).order_by(ContentReviewFeedback.created_at.desc())).all()
    return {'content':content_dict(content,db,ctx),'shots':shots,'facts':facts,'assets':assets,
        'feedback':[feedback_dict(row) for row in feedback],'release_frozen':_frozen(db,ctx,pid,cid)}


def add_review_feedback(db,ctx,pid,cid,body):
    content=_content(db,ctx,pid,cid,REVIEW_ROLES)
    db.refresh(content,with_for_update=True)
    note=_required_text(body,'note',4000)
    shot_id=_text(body,'shot_id',max_length=100) if body.get('shot_id') is not None else ''
    sentence=_text(body,'sentence',max_length=4000) if body.get('sentence') is not None else ''
    if 'expected_render_asset_id' in body and body['expected_render_asset_id']!=content.render_asset_id:
        raise HTTPException(409,'成片已更新，请重新预览当前成片')
    shots=_review_shots(content)
    shot=next((value for value in shots if value['id']==shot_id),None) if shot_id else None
    if shot_id and shot is None: raise HTTPException(422,'审核意见引用的镜头不属于当前版本')
    text='\n'.join(str(shot.get(key) or '') for key in ('caption','voiceover','narration')) if shot else content.script
    if sentence and sentence not in text: raise HTTPException(422,'审核意见引用的句子不在当前内容中')
    frozen=_frozen(db,ctx,pid,cid)
    record=ContentReviewFeedback(tenant_id=ctx.tenant_id,property_id=pid,content_version_id=cid,
        version=content.version,render_asset_id=content.render_asset_id,reviewer_id=ctx.user_id,
        reviewer_name=ctx.name,note=note,shot_id=shot_id or None,sentence=sentence or None)
    db.add(record)
    if not frozen: content.status='needs_revision'
    db.flush()
    db.add(AuditEvent(tenant_id=ctx.tenant_id,property_id=pid,actor_id=ctx.user_id,action='content.review_feedback',
        details={'feedback_id':record.id,'content_version_id':cid,'version':content.version,
            'render_asset_id':content.render_asset_id,'release_frozen':frozen,'shot_id':shot_id or None}))
    db.flush()
    return {'feedback':feedback_dict(record),'content_status':content.status,'release_frozen':frozen}
