from datetime import datetime, timedelta, timezone
import hashlib
import io
import math
from pathlib import Path
import re
import secrets
from uuid import uuid4
import zipfile

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, Response, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from PIL import Image, UnidentifiedImageError

from .config import settings
from .db import get_db, utcnow
from .core_models import Asset, Fact, FactRevision, LoginSession, Membership, Property, Tenant, User
from .security import Context, context_for_user, get_context, hash_password, require_project, verify_password

router = APIRouter(prefix='/api')
EDIT_ROLES = ('owner', 'admin', 'editor', 'content', 'reviewer')
REVIEW_ROLES = ('owner', 'admin', 'reviewer')
ADMIN_ROLES = ('owner', 'admin')
MAX_UPLOAD_BYTES = 100 * 1024 * 1024
PLACEHOLDERS = {'', '-', '--', '—', '暂无', '未知', '待确认', '待上传', '待补充', '待核验', '未提供', 'unknown', 'pending', 'tbd', 'null', 'none', 'n/a'}


def _has_value(value: str | None) -> bool:
    text = (value or '').strip()
    return text.lower() not in PLACEHOLDERS and not re.match(r'^(待确认|待上传|待补充|待核验|未知|暂无|未提供)', text) and not re.match(r'^(?:unknown|pending|tbd)(?:\b|[ :])',text,re.I)


def _aware(value: datetime) -> datetime:
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value


def valid_fact(fact: Fact) -> bool:
    if fact.status != 'verified' or not _has_value(fact.value) or not _has_value(fact.source):
        return False
    if fact.valid_until and _aware(fact.valid_until) <= utcnow():
        return False
    if fact.semantic_key in ('area', 'width', 'price', 'total_price', 'unit_price'):
        if not _has_value(fact.unit):
            return False
        try:
            number = float(fact.value.replace(',', '').strip())
            if not math.isfinite(number) or number <= 0:
                return False
        except ValueError:
            return False
        unit = fact.unit.strip().lower()
        allowed_units = {'area':{'㎡','m²','m2','平方米'},'width':{'m','米'},
                         'price':{'元','万元','亿元','元/㎡','万元/㎡','元/平方米','万元/平方米'},
                         'total_price':{'元','万元','亿元'},'unit_price':{'元/㎡','万元/㎡','元/平方米','万元/平方米'}}
        if unit not in allowed_units[fact.semantic_key]:
            return False
    return True


def fact_dict(fact: Fact) -> dict:
    return {'id':fact.id,'semantic_key':fact.semantic_key,'label':fact.label,'value':fact.value,'unit':fact.unit,
            'source':fact.source,'status':fact.status,'revision':fact.revision,'required':fact.required,
            'valid_until':fact.valid_until.isoformat() if fact.valid_until else None,
            'updated_at':fact.updated_at.isoformat() if fact.updated_at else None,'valid':valid_fact(fact)}


def asset_dict(asset: Asset) -> dict:
    return {'id':asset.id,'name':asset.name,'kind':asset.kind,'mime_type':asset.mime_type,'sha256':asset.sha256,
            'byte_size':asset.byte_size,'rights':asset.rights,'rights_evidence':asset.rights_evidence,
            'rights_revision':asset.rights_revision,'origin_type':asset.origin_type,'metadata_json':asset.metadata_json or {},
            'extracted_text':asset.extracted_text or '', 'created_at':asset.created_at.isoformat(),
            'file_url':f'/api/projects/{asset.property_id}/assets/{asset.id}/file'}


def project_readiness(db: Session, ctx: Context, property: Property) -> dict:
    require_project(db, ctx, property.id)
    facts = db.scalars(select(Fact).where(Fact.tenant_id == ctx.tenant_id, Fact.property_id == property.id, Fact.required.is_(True))).all()
    assets = db.scalars(select(Asset).where(Asset.tenant_id == ctx.tenant_id, Asset.property_id == property.id)).all()
    actual = [a for a in assets if a.origin_type == 'actual']
    checks = [
        ('basic','项目基础资料',all(_has_value(x) for x in (property.name, property.city, property.property_type, property.owner_name)),'项目名称、城市、类型与负责人','project'),
        ('marketing','项目营销范围',property.marketing_confirmed and _has_value(property.marketing_evidence),'确认营销范围并记录授权依据','project'),
        ('facts','核心事实核验',len(facts) >= 4 and all(valid_fact(f) for f in facts),'核验面积、面宽、户型与空间关系，保留来源','facts'),
        ('assets','当前项目素材',bool(actual),'上传当前项目的真实素材','assets'),
        ('rights','素材用途确认',bool(actual) and all(a.rights == 'authorized' and _has_value(a.rights_evidence) for a in actual),'确认全部真实素材的使用授权','assets'),
        ('goal','内容目标与渠道',_has_value(property.audience) and _has_value(property.channel),'填写目标客群与内容渠道','project'),
        ('handoff','销售承接设置',_has_value(property.sales_owner_name),'填写销售承接负责人','project'),
    ]
    items = [{'key':key,'label':label,'complete':bool(complete),'detail':detail,'target':target} for key,label,complete,detail,target in checks]
    completed = sum(item['complete'] for item in items)
    return {'percent':round(completed / len(items) * 100),'completed':completed,'total':len(items),'items':items}


def project_dict(db: Session, ctx: Context, property: Property) -> dict:
    return {key:getattr(property,key) for key in ('id','name','city','property_type','owner_name','audience','sales_owner_name','channel','marketing_confirmed','marketing_evidence','is_test')} | {'completeness':project_readiness(db,ctx,property)}


class LoginInput(BaseModel):
    email: str = Field(min_length=3, max_length=254)
    password: str = Field(min_length=1, max_length=512)


def _auth_dict(db, ctx):
    organization = db.get(Tenant,ctx.tenant_id)
    return {'user':{'id':ctx.user_id,'name':ctx.name,'email':ctx.email},'organization':{'id':ctx.tenant_id,'name':organization.name},'role':ctx.role,'project_ids':ctx.project_ids}


@router.post('/auth/login')
def login(data: LoginInput, response: Response, db: Session = Depends(get_db)):
    user = db.scalar(select(User).where(User.email == data.email.strip().lower(), User.active.is_(True)))
    if not user or not verify_password(data.password, user.password_hash):
        raise HTTPException(401,'账号或密码不正确')
    membership = db.scalar(select(Membership).where(Membership.user_id == user.id, Membership.active.is_(True)).order_by(Membership.id))
    if not membership:
        raise HTTPException(403,'账号没有可用组织')
    token = secrets.token_urlsafe(48)
    db.add(LoginSession(token_hash=hashlib.sha256(token.encode()).hexdigest(),user_id=user.id,tenant_id=membership.tenant_id,expires_at=utcnow()+timedelta(days=7)))
    db.commit()
    ctx = context_for_user(db,user.id,membership.tenant_id)
    response.set_cookie('hs_session',token,max_age=7*24*3600,httponly=True,secure=settings.app_env=='production',samesite='lax',path='/')
    response.headers['Cache-Control'] = 'no-store'
    return _auth_dict(db,ctx)


@router.get('/auth/me')
def me(ctx: Context = Depends(get_context), db: Session = Depends(get_db)):
    return _auth_dict(db,ctx)


@router.post('/auth/logout')
def logout(request: Request,response: Response, db: Session = Depends(get_db)):
    token = request.cookies.get('hs_session')
    if token:
        session = db.scalar(select(LoginSession).where(LoginSession.token_hash == hashlib.sha256(token.encode()).hexdigest()))
        if session:
            db.delete(session)
            db.commit()
    response.delete_cookie('hs_session',path='/')
    return {'ok':True}


class ProjectInput(BaseModel):
    name: str = Field(min_length=1,max_length=200)
    city: str = Field(min_length=1,max_length=100)
    property_type: str = Field(default='豪宅住宅',max_length=100)
    owner_name: str = Field(default='',max_length=100)
    audience: str = Field(default='',max_length=3000)
    sales_owner_name: str = Field(default='',max_length=100)
    channel: str = Field(default='抖音',max_length=100)
    marketing_confirmed: bool = False
    marketing_evidence: str = Field(default='',max_length=3000)

    @field_validator('name','city','property_type')
    @classmethod
    def nonempty(cls,value):
        if not value.strip():
            raise ValueError('请填写项目名称、城市与类型')
        return value.strip()


class ProjectPatch(BaseModel):
    name: str | None = Field(default=None,min_length=1,max_length=200)
    city: str | None = Field(default=None,min_length=1,max_length=100)
    property_type: str | None = Field(default=None,min_length=1,max_length=100)
    owner_name: str | None = Field(default=None,max_length=100)
    audience: str | None = Field(default=None,max_length=3000)
    sales_owner_name: str | None = Field(default=None,max_length=100)
    channel: str | None = Field(default=None,max_length=100)
    marketing_confirmed: bool | None = None
    marketing_evidence: str | None = Field(default=None,max_length=3000)


@router.get('/projects')
def projects(ctx: Context = Depends(get_context), db: Session = Depends(get_db)):
    query = select(Property).where(Property.tenant_id == ctx.tenant_id).order_by(Property.created_at,Property.id)
    if ctx.project_ids is not None:
        query = query.where(Property.id.in_(ctx.project_ids))
    return [project_dict(db,ctx,p) for p in db.scalars(query).all()]


@router.post('/projects')
def create_project(data: ProjectInput, ctx: Context = Depends(get_context), db: Session = Depends(get_db)):
    if ctx.role not in ADMIN_ROLES or ctx.project_ids is not None:
        raise HTTPException(403,'只有组织管理员可以新建项目')
    if data.marketing_confirmed and not _has_value(data.marketing_evidence):
        raise HTTPException(422,'请补充项目营销授权依据')
    property = Property(tenant_id=ctx.tenant_id,**data.model_dump())
    db.add(property)
    db.flush()
    for key,label,unit,required in [('area','建筑面积','㎡',True),('width','客厅面宽','m',True),('layout','户型','',True),('kitchen','厨房与空间关系','',True),('price','报价','万元',False),('view','景观与配套','',False)]:
        db.add(Fact(tenant_id=ctx.tenant_id,property_id=property.id,semantic_key=key,label=label,unit=unit,required=required))
    db.commit()
    return project_dict(db,ctx,property)


@router.get('/projects/{pid}')
def project(pid: str, ctx: Context = Depends(get_context), db: Session = Depends(get_db)):
    return project_dict(db,ctx,require_project(db,ctx,pid))


@router.patch('/projects/{pid}')
def update_project(pid: str, data: ProjectPatch, ctx: Context = Depends(get_context), db: Session = Depends(get_db)):
    property = require_project(db,ctx,pid,EDIT_ROLES)
    updates = data.model_dump(exclude_unset=True)
    if any(value is None for value in updates.values()):
        raise HTTPException(422,'项目字段不能设置为空值')
    for key in ('name','city','property_type'):
        if key in updates and not updates[key].strip():
            raise HTTPException(422,'项目名称、城市与类型不能为空')
    if updates.get('marketing_confirmed',property.marketing_confirmed) and not _has_value(updates.get('marketing_evidence',property.marketing_evidence)):
        raise HTTPException(422,'请补充项目营销授权依据')
    if any(key in updates for key in ('marketing_confirmed','marketing_evidence')) and ctx.role not in REVIEW_ROLES:
        raise HTTPException(403,'营销授权需要负责人或审核员确认')
    for key,value in updates.items():
        setattr(property,key,value.strip() if isinstance(value,str) else value)
    db.commit()
    return project_dict(db,ctx,property)


def _fact(db,ctx,pid,fid):
    fact = db.scalar(select(Fact).where(Fact.id == fid,Fact.property_id == pid,Fact.tenant_id == ctx.tenant_id).with_for_update())
    if not fact:
        raise HTTPException(404,'资料记录不存在')
    return fact


def _record_revision(db,fact):
    previous = db.scalar(select(FactRevision).where(FactRevision.fact_id == fact.id,FactRevision.revision == fact.revision))
    if not previous:
        db.add(FactRevision(tenant_id=fact.tenant_id,property_id=fact.property_id,fact_id=fact.id,revision=fact.revision,payload=fact_dict(fact)))


@router.get('/projects/{pid}/facts')
def facts(pid: str, ctx: Context = Depends(get_context), db: Session = Depends(get_db)):
    require_project(db,ctx,pid)
    return [fact_dict(f) for f in db.scalars(select(Fact).where(Fact.property_id == pid,Fact.tenant_id == ctx.tenant_id).order_by(Fact.required.desc(),Fact.semantic_key)).all()]


class FactInput(BaseModel):
    value: str = Field(max_length=5000)
    unit: str = Field(default='',max_length=50)
    source: str = Field(max_length=5000)
    valid_until: datetime | None = None


@router.patch('/projects/{pid}/facts/{fid}')
def update_fact(pid: str,fid: str,data: FactInput,ctx: Context = Depends(get_context),db: Session = Depends(get_db)):
    require_project(db,ctx,pid,EDIT_ROLES)
    fact = _fact(db,ctx,pid,fid)
    _record_revision(db,fact)
    for key,value in data.model_dump().items():
        setattr(fact,key,value.strip() if isinstance(value,str) else value)
    fact.status = 'unverified'
    fact.revision += 1
    fact.updated_at = utcnow()
    _record_revision(db,fact)
    db.commit()
    return fact_dict(fact)


class Confirmation(BaseModel):
    confirmed: bool


@router.post('/projects/{pid}/facts/{fid}/verify')
def verify_fact(pid: str,fid: str,data: Confirmation,ctx: Context = Depends(get_context),db: Session = Depends(get_db)):
    require_project(db,ctx,pid,REVIEW_ROLES)
    if not data.confirmed:
        raise HTTPException(422,'请人工确认资料与来源一致')
    fact = _fact(db,ctx,pid,fid)
    original = fact.status
    fact.status = 'verified'
    if not valid_fact(fact):
        fact.status = original
        raise HTTPException(422,'资料缺少有效值、单位或来源，不能核验')
    fact.status = original
    _record_revision(db,fact)
    fact.status = 'verified'
    fact.revision += 1
    fact.updated_at = utcnow()
    _record_revision(db,fact)
    db.commit()
    return fact_dict(fact)


@router.post('/projects/{pid}/facts/{fid}/expire')
def expire_fact(pid: str,fid: str,ctx: Context = Depends(get_context),db: Session = Depends(get_db)):
    require_project(db,ctx,pid,REVIEW_ROLES)
    fact = _fact(db,ctx,pid,fid)
    _record_revision(db,fact)
    fact.status = 'expired'
    fact.revision += 1
    fact.updated_at = utcnow()
    _record_revision(db,fact)
    db.commit()
    return fact_dict(fact)


def _asset(db,ctx,pid,aid):
    asset = db.scalar(select(Asset).where(Asset.id == aid,Asset.property_id == pid,Asset.tenant_id == ctx.tenant_id))
    if not asset:
        raise HTTPException(404,'素材不存在')
    return asset


@router.get('/projects/{pid}/assets')
def assets(pid: str,ctx: Context = Depends(get_context),db: Session = Depends(get_db)):
    require_project(db,ctx,pid)
    return [asset_dict(a) for a in db.scalars(select(Asset).where(Asset.property_id == pid,Asset.tenant_id == ctx.tenant_id).order_by(Asset.created_at.desc())).all()]


def _file_type(name: str, content: bytes) -> tuple[str,str,dict]:
    ext = Path(name).suffix.lower()
    if ext in ('.jpg','.jpeg','.png','.webp','.gif'):
        try:
            with Image.open(io.BytesIO(content)) as img:
                if img.format not in ('JPEG','PNG','WEBP','GIF') or img.width * img.height > 60_000_000:
                    raise HTTPException(422,'图片格式或尺寸不支持')
                meta = {'width':img.width,'height':img.height}
                mime = Image.MIME[img.format]
                img.verify()
                return 'image',mime,meta
        except (UnidentifiedImageError,OSError,ValueError,Image.DecompressionBombError):
            raise HTTPException(422,'图片内容无法解析')
    if ext == '.pdf' and content.startswith(b'%PDF-'):
        return 'document','application/pdf',{}
    if ext == '.txt':
        try:
            value = content.decode('utf-8-sig')
            if '\x00' in value or content.startswith((b'MZ',b'\x7fELF')):
                raise ValueError()
            return 'document','text/plain',{}
        except UnicodeError:
            raise HTTPException(422,'文本资料需要 UTF-8 编码')
        except ValueError:
            raise HTTPException(422,'文本内容格式不支持')
    if ext == '.docx':
        try:
            with zipfile.ZipFile(io.BytesIO(content)) as z:
                infos = z.infolist()
                if 'word/document.xml' not in z.namelist() or len(infos)>2000 or sum(i.file_size for i in infos)>100*1024*1024 or any(i.filename.startswith('/') or '..' in Path(i.filename).parts for i in infos):
                    raise ValueError()
            return 'document','application/vnd.openxmlformats-officedocument.wordprocessingml.document',{}
        except (zipfile.BadZipFile,ValueError):
            raise HTTPException(422,'Word 文档格式不支持')
    if ext in ('.mp4','.mov','.m4a') and len(content)>12 and (content[4:8] in (b'ftyp',b'moov',b'mdat',b'wide')):
        return ('audio','audio/mp4',{}) if ext == '.m4a' else ('video','video/mp4' if ext == '.mp4' else 'video/quicktime',{})
    if ext == '.webm' and content.startswith(b'\x1a\x45\xdf\xa3') and b'webm' in content[:4096]:
        return 'video','video/webm',{}
    if ext == '.wav' and content.startswith(b'RIFF') and content[8:12] == b'WAVE':
        return 'audio','audio/wav',{}
    if ext == '.mp3' and (content.startswith(b'ID3') or len(content)>1 and content[0]==255 and content[1]&224==224):
        return 'audio','audio/mpeg',{}
    raise HTTPException(422,'请选择有效的图片、PDF、TXT、DOCX、音频或视频')


def storage_path(key: str) -> Path:
    root = settings.storage_root.resolve()
    path = (root / key).resolve()
    if not path.is_relative_to(root) or path == root:
        raise HTTPException(404,'素材文件不存在')
    return path


@router.post('/projects/{pid}/assets')
async def upload_asset(pid: str,file: UploadFile = File(...),ctx: Context = Depends(get_context),db: Session = Depends(get_db)):
    require_project(db,ctx,pid,EDIT_ROLES)
    content = bytearray()
    while chunk := await file.read(1024*1024):
        content.extend(chunk)
        if len(content)>MAX_UPLOAD_BYTES:
            raise HTTPException(413,'单个素材不能超过 100MB')
    if not content:
        raise HTTPException(422,'不能上传空文件')
    name = Path((file.filename or '素材').replace('\\','/')).name
    name = re.sub(r'[\x00-\x1f\x7f]','',name)[:255]
    kind,mime,metadata = _file_type(name,bytes(content))
    key = f'{ctx.tenant_id}/{pid}/{uuid4().hex}{Path(name).suffix.lower()}'
    path = storage_path(key)
    path.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
    path.write_bytes(content)
    path.chmod(0o600)
    metadata['model_processing_authorized'] = False
    asset = Asset(tenant_id=ctx.tenant_id,property_id=pid,name=name,kind=kind,storage_key=key,mime_type=mime,
                  sha256=hashlib.sha256(content).hexdigest(),byte_size=len(content),rights='pending',rights_evidence='',origin_type='actual',metadata_json=metadata)
    try:
        db.add(asset)
        db.flush()
        from .jobs import enqueue_job
        enqueue_job(db,ctx,pid,'extract_asset',{'asset_id':asset.id})
        db.commit()
    except Exception:
        db.rollback()
        path.unlink(missing_ok=True)
        raise
    return asset_dict(asset)


class RightsInput(BaseModel):
    rights: str
    evidence: str = Field(default='',max_length=5000)
    model_processing_authorized: bool | None = None
    enqueue_extraction: bool = Field(default=True, strict=True)


@router.post('/projects/{pid}/assets/{aid}/rights')
def asset_rights(pid: str,aid: str,data: RightsInput,ctx: Context = Depends(get_context),db: Session = Depends(get_db)):
    require_project(db,ctx,pid,REVIEW_ROLES)
    asset = _asset(db,ctx,pid,aid)
    if data.rights not in ('pending','authorized','revoked'):
        raise HTTPException(422,'素材授权状态不支持')
    if data.rights == 'authorized' and not _has_value(data.evidence):
        raise HTTPException(422,'请记录素材授权依据')
    if data.model_processing_authorized and (data.rights != 'authorized' or not _has_value(data.evidence)):
        raise HTTPException(422,'模型处理需要素材授权与明确依据')
    metadata = dict(asset.metadata_json or {})
    before = bool(metadata.get('model_processing_authorized'))
    after = before if data.model_processing_authorized is None else data.model_processing_authorized
    if data.rights != 'authorized':
        after = False
    changed = asset.rights != data.rights or asset.rights_evidence != data.evidence.strip() or before != after
    asset.rights = data.rights
    asset.rights_evidence = data.evidence.strip()
    metadata['model_processing_authorized'] = after
    asset.metadata_json = metadata
    if changed:
        asset.rights_revision += 1
    if after and not before and data.enqueue_extraction:
        from .jobs import enqueue_job
        enqueue_job(db,ctx,pid,'extract_asset',{'asset_id':asset.id})
    db.commit()
    return asset_dict(asset)


@router.get('/projects/{pid}/assets/{aid}/file')
def asset_file(pid: str,aid: str,ctx: Context = Depends(get_context),db: Session = Depends(get_db)):
    require_project(db,ctx,pid)
    asset = _asset(db,ctx,pid,aid)
    path = storage_path(asset.storage_key)
    if not path.is_file():
        raise HTTPException(404,'素材文件不存在')
    return FileResponse(path,media_type=asset.mime_type,filename=asset.name,
                        content_disposition_type='inline' if asset.kind in ('image','audio','video') else 'attachment',
                        headers={'Cache-Control':'private, no-store','X-Content-Type-Options':'nosniff'})


def _require_admin(ctx: Context):
    if ctx.role not in ADMIN_ROLES or ctx.project_ids is not None:
        raise HTTPException(403,'需要组织管理员权限')


def _member_dict(db: Session, membership: Membership) -> dict:
    user = db.get(User,membership.user_id)
    return {'id':membership.id,'user_id':user.id,'name':user.name,'email':user.email,'role':membership.role,
            'project_ids':membership.project_ids,'active':membership.active and user.active}


def _validate_scope(db,ctx,project_ids):
    if project_ids is None:
        return None
    unique = list(dict.fromkeys(project_ids))
    available = set(db.scalars(select(Property.id).where(Property.tenant_id == ctx.tenant_id,Property.id.in_(unique))).all())
    if set(unique) != available:
        raise HTTPException(422,'成员项目范围必须属于当前组织')
    return unique


@router.get('/organization')
def organization(ctx: Context = Depends(get_context),db: Session = Depends(get_db)):
    tenant = db.get(Tenant,ctx.tenant_id)
    return {'id':tenant.id,'name':tenant.name,'role':ctx.role}


class OrganizationInput(BaseModel):
    name: str = Field(min_length=1,max_length=200)


@router.patch('/organization')
def update_organization(data: OrganizationInput,ctx: Context = Depends(get_context),db: Session = Depends(get_db)):
    _require_admin(ctx)
    if not data.name.strip():
        raise HTTPException(422,'请填写组织名称')
    tenant = db.get(Tenant,ctx.tenant_id)
    tenant.name = data.name.strip()
    db.commit()
    return {'id':tenant.id,'name':tenant.name,'role':ctx.role}


@router.get('/organization/members')
def members(ctx: Context = Depends(get_context),db: Session = Depends(get_db)):
    _require_admin(ctx)
    return [_member_dict(db,m) for m in db.scalars(select(Membership).where(Membership.tenant_id == ctx.tenant_id).order_by(Membership.id)).all()]


ROLES = {'owner','admin','editor','content','reviewer','sales','viewer'}


class MemberInput(BaseModel):
    email: str = Field(min_length=3,max_length=254)
    name: str = Field(min_length=1,max_length=100)
    password: str = Field(min_length=12,max_length=512)
    role: str = 'viewer'
    project_ids: list[str] | None = None


@router.post('/organization/members')
def create_member(data: MemberInput,ctx: Context = Depends(get_context),db: Session = Depends(get_db)):
    _require_admin(ctx)
    email = data.email.strip().lower()
    if not re.fullmatch(r'[^\s@]+@[^\s@]+\.[^\s@]+',email) or not data.name.strip():
        raise HTTPException(422,'请填写有效邮箱与姓名')
    if data.role not in ROLES:
        raise HTTPException(422,'成员角色不支持')
    project_ids = _validate_scope(db,ctx,data.project_ids)
    if db.scalar(select(User.id).where(User.email == email)):
        raise HTTPException(409,'该邮箱已存在，请联系管理员处理')
    user = User(email=email,name=data.name.strip(),password_hash=hash_password(data.password))
    db.add(user)
    db.flush()
    membership = Membership(user_id=user.id,tenant_id=ctx.tenant_id,role=data.role,project_ids=project_ids,active=True)
    db.add(membership)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(409,'该成员已存在')
    return _member_dict(db,membership)


class MemberPatch(BaseModel):
    role: str | None = None
    project_ids: list[str] | None = None
    active: bool | None = None


@router.patch('/organization/members/{mid}')
def update_member(mid: str,data: MemberPatch,ctx: Context = Depends(get_context),db: Session = Depends(get_db)):
    _require_admin(ctx)
    membership = db.scalar(select(Membership).where(Membership.id == mid,Membership.tenant_id == ctx.tenant_id).with_for_update())
    if not membership:
        raise HTTPException(404,'成员不存在')
    updates = data.model_dump(exclude_unset=True)
    if 'role' in updates and updates['role'] not in ROLES:
        raise HTTPException(422,'成员角色不支持')
    if 'active' in updates and updates['active'] is None:
        raise HTTPException(422,'成员状态不能为空')
    if 'project_ids' in updates:
        updates['project_ids'] = _validate_scope(db,ctx,updates['project_ids'])
    proposed_role = updates.get('role',membership.role)
    proposed_active = updates.get('active',membership.active)
    proposed_scope = updates.get('project_ids',membership.project_ids)
    if membership.active and membership.role in ADMIN_ROLES and membership.project_ids is None and (not proposed_active or proposed_role not in ADMIN_ROLES or proposed_scope is not None):
        others = db.scalars(select(Membership).where(Membership.tenant_id == ctx.tenant_id,Membership.id != mid,Membership.active.is_(True),Membership.role.in_(ADMIN_ROLES)).with_for_update()).all()
        if not any(m.project_ids is None and db.get(User,m.user_id).active for m in others):
            raise HTTPException(422,'组织至少需要一位可用管理员')
    for key,value in updates.items():
        setattr(membership,key,value)
    db.commit()
    return _member_dict(db,membership)
