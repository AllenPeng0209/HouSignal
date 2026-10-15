"""Private reference-video ingestion with an explicit research-only purpose."""
import hashlib
from pathlib import Path
import re
from uuid import uuid4

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from .business import EDIT_ROLES
from .core import MAX_UPLOAD_BYTES, _file_type, _has_value, asset_dict, storage_path
from .core_models import Asset
from .db import get_db
from .operations_models import ContentOpportunity
from .security import Context, get_context, require_project


router = APIRouter(prefix='/api/projects/{pid}/operations/research', tags=['参考视频'])


@router.post('/references/{rid}/media')
async def upload_reference_media(pid: str, rid: str, file: UploadFile = File(...),
        expected_revision: int = Form(..., ge=1), evidence: str = Form(..., max_length=5000),
        model_processing_authorized: bool = Form(False),
        db: Session = Depends(get_db), ctx: Context = Depends(get_context)):
    require_project(db, ctx, pid, EDIT_ROLES)
    query = select(ContentOpportunity).where(ContentOpportunity.id == rid,
        ContentOpportunity.tenant_id == ctx.tenant_id, ContentOpportunity.property_id == pid)
    reference = db.scalar(query)
    if reference is None:
        raise HTTPException(404, '参考作品不存在或无权访问')
    if reference.revision != expected_revision:
        raise HTTPException(409, '参考作品已更新，请刷新后重新上传')
    if not _has_value(evidence):
        raise HTTPException(422, '请记录原片的研究用途与授权依据')
    from .discovery import _canonical_work_url
    if _canonical_work_url(reference.source_url) is None:
        raise HTTPException(422, '请先登记可核对的原片完整链接')
    content = bytearray()
    while chunk := await file.read(1024 * 1024):
        content.extend(chunk)
        if len(content) > MAX_UPLOAD_BYTES:
            raise HTTPException(413, '参考视频不能超过100MB')
    if not content:
        raise HTTPException(422, '不能上传空文件')
    name = Path((file.filename or '参考视频').replace('\\', '/')).name
    name = re.sub(r'[\x00-\x1f\x7f]', '', name)[:255]
    kind, mime, metadata = _file_type(name, bytes(content))
    if kind != 'video':
        raise HTTPException(422, '请上传MP4、MOV或WebM原视频')
    # Recheck access after reading the upload; no long-lived row lock spans I/O.
    db.expire_all()
    require_project(db, ctx, pid, EDIT_ROLES)
    key = f'{ctx.tenant_id}/{pid}/references/{uuid4().hex}{Path(name).suffix.lower()}'
    path = storage_path(key)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    path.write_bytes(content); path.chmod(0o600)
    metadata.update(usage_scope='research_only', model_processing_authorized=model_processing_authorized,
                    reference_id=rid)
    asset = Asset(tenant_id=ctx.tenant_id, property_id=pid, name=name, kind=kind,
        storage_key=key, mime_type=mime, sha256=hashlib.sha256(content).hexdigest(), byte_size=len(content),
        rights='authorized', rights_evidence=evidence.strip(), origin_type='reference', metadata_json=metadata)
    try:
        db.add(asset); db.flush()
        changed = db.execute(update(ContentOpportunity).where(ContentOpportunity.id == rid,
            ContentOpportunity.tenant_id == ctx.tenant_id, ContentOpportunity.property_id == pid,
            ContentOpportunity.revision == expected_revision).values(media_asset_id=asset.id,
                analysis_snapshot={}, revision=expected_revision + 1)
            .execution_options(synchronize_session=False)).rowcount
        if changed != 1:
            raise HTTPException(409, '上传期间参考作品已更新，请刷新后重新关联原片')
        db.commit()
    except Exception:
        db.rollback(); path.unlink(missing_ok=True)
        raise
    db.expire_all()
    reference = db.scalar(query)
    from .research_workspace import reference_dict
    return {'reference': reference_dict(reference), 'asset': asset_dict(db.get(Asset, asset.id))}
